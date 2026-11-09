"""
냉각 구조 연성 평가 - 기류(airflow) + 배관(piping) + 칠러 COP 를 묶어 24시간 전력/에너지를 계산.

부하 엔진(engine.simulate)의 결과는 바꾸지 않고, 그 위에서 공기측(CRAH -> 냉수) 경로만 교체/추가한다:
- CRAH 팬 발열: 엔진의 비율 가정(crah_fan_heat_ratio) 대신 기류 모델의 팬 전력(풍량^3)
- 칠러 COP: 엔진과 같은 식 (engine.chiller_cop: 습구온도 보정 x 공급수온 보정, 가정)
- 프리쿨링: 엔진과 같은 판정 (engine.free_fraction) 을 후보 냉수 온도로 다시 적용.
  현실성 R1 제어(realism.control)가 켜져 있으면 엔진과 같은 realism.run_series (단일일 = 하루 미리 돌려 초기화)
- 냉수·냉각수 펌프 전력 (배관 모델). 냉수 펌프 발열은 칠러 부하에 더함
- 액체 루프(hybrid_dlc / immersion)는 엔진 결과(보조 칠러 + TCS/FWS 펌프)를 그대로 더함 (liquid)
- 냉각탑 팬 전력은 포함하지 않음 (엔진 PUE 에는 포함)

제약 (피크 IT 시각 기준):
- 모든 랙 흡입온도 <= inlet_limit_c
- 급기온도 >= 냉수 공급온도 + coil_approach_k
- 냉수 환수온도 <= CRAH 환기온도 - coil_return_approach_k
"""
from .airflow import analyze, building_dims, crah_positions
from . import realism
from .engine import CP_WATER, RHO_WATER, chiller_cop, free_fraction, loop_mode, plant_param, simulate
from .piping import chw_network, cw_network, plant_positions, pump_kw
from .plant_units import size_units
from .building_view import view as building_view

POWER_KEYS = ("chiller", "crah_fan", "chw_pump", "cw_pump", "liquid", "total")


def peak_hour(sc):
    util = sc["it_load"]["utilization"]
    return max(range(24), key=lambda k: util[k])


def design_cooling_kw(engine_res):
    """배관 설계 기준 냉방부하 = 엔진 일 최대 냉방부하 (후보마다 관경이 흔들리지 않도록 고정)"""
    return max(x["load_kw"]["total_cooling"] for x in engine_res["hourly"])


def networks(sc, cfg, engine_res, chw_dt):
    """설계 유량으로 냉수·냉각수 배관 선정·해석 (다중 존: 전산실별 냉수 분기, hvac/multizone.py)"""
    if sc.get("zones"):
        from .multizone import combined_networks
        return combined_networks(sc, cfg, engine_res, chw_dt, networks)
    dims = building_dims(sc)
    crahs = crah_positions(cfg, dims[0], dims[1])
    q_d = design_cooling_kw(engine_res)
    cp = sc["cooling_plant"]
    chw_q = q_d / (CP_WATER * chw_dt) / RHO_WATER
    cop_d = cp["chiller_cop_rated"]
    cw_q = q_d * (1 + 1 / cop_d) / (CP_WATER * (cp["cw_return_c"] - cp["cw_supply_c"])) / RHO_WATER
    return {"chw": chw_network(cfg, dims, crahs, chw_q), "cw": cw_network(cfg, dims[0], cw_q),
            "chw_q_design": chw_q, "cw_q_design": cw_q}


def energy(sc, cfg, engine_res, fan_kw, chw_supply, chw_return, nets):
    """시간별 전력과 일 에너지 (팬 전력은 고정 풍량이므로 시간 무관)"""
    cp, pc = sc["cooling_plant"], cfg["piping"]
    dt_chw = chw_return - chw_supply
    dt_cw = cp["cw_return_c"] - cp["cw_supply_c"]
    chw_head, cw = nets["chw"]["head_kpa"], nets["cw"]
    fc = plant_param(cp, "free_cooling")
    rc = realism.control_on(cp)
    xs = None
    if rc and fc:
        xs = [c[0] for c in realism.run_series([x["plant"]["free_cooling_water_c"] for x in engine_res["hourly"]],
                                               chw_supply, chw_return, rc, 1.0)[0]]
    hours = []
    for k, x in enumerate(engine_res["hourly"]):
        L, P = x["load_kw"], x["plant"]
        # 공기측 냉방량: 엔진 공기 루프 부하에서 엔진 팬 발열을 기류 모델 팬 전력으로 교체
        q_air = max(P["air_loop"]["load_kw"] - L["crah_fan"] + fan_kw, 0.0)
        chw_q = q_air / (CP_WATER * dt_chw) / RHO_WATER
        p_chw = pump_kw(chw_q, chw_head * (chw_q / nets["chw_q_design"]) ** 2, pc["pump_efficiency"])
        load = q_air + p_chw
        x_fc = xs[k] if xs else free_fraction(P["free_cooling_water_c"], chw_supply, chw_return) if fc else 0.0
        cop = chiller_cop(cp, x["weather"]["wet_bulb_c"], chw_supply)
        p_ch = load * (1 - x_fc) / cop
        liq = P["liquid_loop"]
        liq_ch = liq["chiller_load_kw"] / liq["chiller_cop"] if liq else 0.0
        liq_kw = liq_ch + P["power_kw"]["liquid_pump"] if liq else 0.0
        liq_heat = liq["load_kw"] + liq_ch if liq else 0.0
        cw_q = (load + p_ch + liq_heat) / (CP_WATER * dt_cw) / RHO_WATER
        cw_head = cw["dynamic_kpa"] * (cw_q / nets["cw_q_design"]) ** 2 + cw["static_kpa"]
        p_cw = pump_kw(cw_q, cw_head, pc["pump_efficiency"])
        hours.append({"hour": x["hour"], "it_kw": L["it"], "cooling_kw": round(load, 1), "cop": round(cop, 2),
                      "free_fraction": round(x_fc, 3), "mode": loop_mode(x_fc),
                      "chiller_kw": round(p_ch, 1), "crah_fan_kw": round(fan_kw, 1),
                      "chw_pump_kw": round(p_chw, 2), "cw_pump_kw": round(p_cw, 2), "liquid_kw": round(liq_kw, 2),
                      "total_kw": round(p_ch + fan_kw + p_chw + p_cw + liq_kw, 1),
                      "chw_flow_m3_h": round(chw_q * 3600, 1), "cw_flow_m3_h": round(cw_q * 3600, 1)})
    daily = {k: round(sum(h[f"{k}_kw"] for h in hours), 1) for k in POWER_KEYS}
    return hours, daily


def violations(cfg, peak_metrics, supply_temp, chw_supply, chw_return):
    out = []
    if peak_metrics["racks_over_limit"]:
        out.append(f"랙 흡입온도 초과 {peak_metrics['racks_over_limit']}개 (최대 {peak_metrics['max_inlet_c']}℃ > "
                   f"{cfg['inlet_limit_c']}℃)")
    if supply_temp < chw_supply + cfg["coil_approach_k"] - 1e-9:
        out.append(f"급기온도 {supply_temp}℃ 가 냉수 공급 {chw_supply}℃ + 코일 접근온도 {cfg['coil_approach_k']}K 보다 낮음")
    if chw_return > peak_metrics["crah_return_c"] - cfg["coil_return_approach_k"] + 1e-9:
        out.append(f"냉수 환수 {chw_return}℃ 가 CRAH 환기 {peak_metrics['crah_return_c']}℃ - "
                   f"{cfg['coil_return_approach_k']}K 보다 높음")
    return out


def summary(params, peak, hours, daily, nets, viol, peak_h):
    p = hours[peak_h]
    return {"params": params,
            "max_inlet_c": peak["max_inlet_c"], "racks_over_limit": peak["racks_over_limit"],
            "crah_return_c": peak["crah_return_c"], "shi": peak["shi"],
            "peak_kw": {k: p[f"{k}_kw"] for k in POWER_KEYS},
            "cop_peak": p["cop"], "daily_kwh": daily,
            "chw_head_kpa": nets["chw"]["head_kpa"], "feasible": not viol, "violations": viol}


def params_of(sc, cfg):
    return {"containment": cfg["containment"], "crah_per_side": cfg["crah"]["per_side"],
            "airflow_ratio": cfg["crah"]["airflow_ratio"], "tile_balancing": cfg["crah"]["tile_balancing"],
            "supply_temp_c": cfg["supply_temp_c"], "chw_supply_c": sc["cooling_plant"]["chw_supply_c"],
            "chw_return_c": sc["cooling_plant"]["chw_return_c"], "topology": cfg["piping"]["topology"]}


def evaluate(sc, cfg, zone=None):
    """현재 시나리오+냉각 구조의 전체 평가 (3D 표시용 랙 24시간 흡입온도, 배관 구간 포함).
    다중 존: 요약·제약·전력은 전산실 합산, 3D·랙·배관 표시는 zone(없으면 첫 전산실) 하나.
    다중 존이면 building_view (건물 좌표계 층·존·랙·CRAH·설비·배관, hvac/building_view.py) 추가"""
    res = simulate(sc, 60)
    ph = peak_hour(sc)
    hourly_af = [analyze(sc, cfg, h) for h in range(24)]
    peak = hourly_af[ph]
    disp_sc, disp_af, zinfo = sc, hourly_af, None
    if sc.get("zones"):
        from .multizone import halls
        hs = halls(sc, cfg)
        idx = next((i for i, (z, _) in enumerate(hs) if z["id"] == zone), 0)
        z_sel, disp_sc = hs[idx]
        disp_af = [analyze(disp_sc, disp_sc["cooling_layout"], h) for h in range(24)]
        zinfo = (idx, z_sel["id"], [{"id": z["id"], "name": z["name"], "floor": z["floor"],
                                     "it_design_kw": z["it_design_kw"], **zm["metrics"]}
                                    for (z, _), zm in zip(hs, peak["zones"])])
    cp = sc["cooling_plant"]
    nets = networks(sc, cfg, res, cp["chw_return_c"] - cp["chw_supply_c"])
    hours, daily = energy(sc, cfg, res, peak["metrics"]["crah_fan_kw"], cp["chw_supply_c"], cp["chw_return_c"], nets)
    for hr, af in zip(hours, disp_af):
        m = af["metrics"]
        hr.update(max_inlet_c=m["max_inlet_c"], racks_over_limit=m["racks_over_limit"], crah_return_c=m["crah_return_c"])
    viol = violations(cfg, peak["metrics"], cfg["supply_temp_c"], cp["chw_supply_c"], cp["chw_return_c"])
    ew, ns, h = building_dims(disp_sc)
    from .advice import evaluate_issues
    from .mixed import warning
    w = warning(sc, cfg)
    return {
        "warnings": [w] if w else [],
        "issues": evaluate_issues(sc, cfg, ph, [w] if w else []),
        "peak_hour": ph,
        "airflow": disp_af[ph],
        "rack_inlet_c": [[r["inlet_c"] for r in af["racks"]] for af in disp_af],
        "hourly": hours,
        "summary": summary(params_of(sc, cfg), peak["metrics"], hours, daily, nets, viol, ph),
        "piping": {"topology": cfg["piping"]["topology"],
                   "chw_head_kpa": nets["chw"]["head_kpa"], "chw_friction_kpa": nets["chw"]["friction_kpa"],
                   "chw_critical_m": nets["chw"]["critical_m"],
                   "chw_design_flow_m3_h": round(nets["chw_q_design"] * 3600, 1),
                   "cw_design_flow_m3_h": round(nets["cw_q_design"] * 3600, 1),
                   "segments": (nets["halls"][zinfo[0]]["chw"]["segments"] if zinfo else nets["chw"]["segments"])
                   + nets["cw"]["segments"]},
        "geometry": {"building": {"ew_m": round(ew, 2), "ns_m": round(ns, 2), "h_m": round(h, 2)},
                     **plant_positions(cfg, ew)},
        "units": size_units(cfg, res),
        **({"zone": zinfo[1], "zones": zinfo[2]} if zinfo else {}),
        **({"building_view": building_view(sc, cfg, hs, hourly_af, ph, nets["building"], analyze)} if zinfo else {}),
    }
