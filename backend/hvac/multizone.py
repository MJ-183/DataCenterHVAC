"""
다중 존 (적층 건물, Phase 18) - 존마다 단일 존 엔진(engine.simulate_zone)을 돌려 건물 합계를 만든다.

엔진 수식은 바꾸지 않는다. 존 하나 = 단일 존 시나리오 하나 (zone_scenario):
- 건물 공통: 사이트·기상·냉각 설비(cooling_plant: 냉수·냉각수 온도, COP, 프리쿨링, 보조동력 계수)·IT 시간 모양(it_load.utilization)
- 존별: 외기 접촉면(envelope), IT 설계부하, 실내 설정, 면적·조명·인원·외기량·기타 발열, 냉각 방식(선택), 랙 격자(전산실)
- 존 사이 전도열 (층간 슬래브·내벽): q = U·A·(T_이웃 - T_존) [kW], 존의 기타 발열(misc_kw)에 더함.
  모든 존이 같은 실내온도면 0. 건물 전체 합은 항상 0 (주고받는 열이 같음). 정상상태, 축열 무시 (엔진과 같은 가정)
- 1층 바닥(지반)은 엔진과 마찬가지로 단열로 본다

합산이 성립하는 이유: 엔진의 설비 식은 존 부하에 선형 (칠러 COP·프리쿨링 비율·R1 제어 상태는 기상·수온에만 의존하고
부하와 무관, 펌프·팬·냉각탑·보급수는 부하 x 계수). 따라서 공통 설비로 존별 계산을 더한 값 = 공통 설비로 건물을 계산한 값.
단, 각 존의 공기측 부하는 존 안에서만 상쇄된다 (한 존의 음(-)의 부하가 다른 존의 부하를 줄이지 않음 — 다중 존이 더 현실적).

연성 모델(기류·배관)은 전산실 존마다 해석하고 합친다 (combined_analyze / combined_networks):
- 랙 흡입 최대 = 전산실 중 최대, 초과 랙 수 = 합, CRAH 팬 = 합, CRAH 환기온도 = 최저(코일 환수 접근 제약이 가장 빡빡한 곳)
- 냉수 배관 = 건물 좌표 배관망 (칠러 N대 → 야드 헤더 → 입상관 → 층 분기 → 전산실 분기, hvac/building_view.py),
  펌프 양정 = 전산실 경로 중 최대 (입상관 마찰 포함, 정수두 0), 설계 유량 = 합.
  냉각수 양정은 기존 그대로 (첫 전산실 기준 단일 배관, 유량 = 건물 전체). 야드 헤더 기준 값은 building_view 참고값
- 운전점(급기·냉수 온도·풍량 배율·컨테인먼트·CRAH 대수)은 건물 공통 cooling_layout 하나. 랙 격자만 존별
- 비 전산 존(전기실·사무)은 공기측 냉방을 엔진 계수(CRAH 팬 발열 비율)로만 계산
"""
import copy

from .engine import loop_mode, plant_mode, daily_total, simulate_zone

SUM_LOAD = ("it", "ups_loss", "lighting", "people", "wall_conduction", "roof_conduction", "window_conduction",
            "window_solar", "ventilation", "crah_fan", "total_cooling")
POWER = ("chiller", "crah_fan", "chw_pump", "cw_pump", "liquid_pump", "tower_fan", "cooling_total", "facility")
REASON_RANK = {"min_on": 4, "min_off": 3, "staging": 2, "hysteresis_hold": 1, "threshold": 0}


def zones_of(sc):
    return sc.get("zones") or None


def _temp(sc, z):
    return (z.get("indoor") or sc["indoor"])["temp_c"]


def interzone_kw(sc, z):
    """다른 존에서 이 존으로 들어오는 전도열 [kW] (+ = 유입). partitions 는 쌍마다 한쪽에만 적혀 있어도 양쪽에 반영"""
    zs = {x["id"]: x for x in sc["zones"]}
    t = _temp(sc, z)
    q = 0.0
    for x in sc["zones"]:
        for p in x.get("partitions") or []:
            other = p["zone"] if x["id"] == z["id"] else x["id"] if p["zone"] == z["id"] else None
            if other is not None:
                q += p["u_value"] * p["area_m2"] * (_temp(sc, zs[other]) - t) / 1000.0
    return q


def zone_scenario(sc, z, cfg=None):
    """존 → 단일 존 시나리오 dict (엔진·기류 모델에 그대로 넘김). cfg 를 주면 그 운전점에 존 랙 격자를 끼운 cooling_layout"""
    cp = {**sc["cooling_plant"]}
    if z.get("architecture"):
        cp["architecture"] = z["architecture"]
    if z.get("liquid_fraction") is not None:
        cp["liquid_fraction"] = z["liquid_fraction"]
    intl = sc["internal"]
    g = z["geometry"]
    lay = copy.deepcopy(cfg if cfg is not None else sc.get("cooling_layout") or {})
    if z.get("racks"):
        lay["racks"] = z["racks"]
    if z.get("crah_positions"):
        lay["crah"] = {**(lay.get("crah") or {}), "positions": z["crah_positions"]}
    out = {
        "site": {**sc["site"], "name": f"{sc['site']['name']} · {z['name']}"},
        "weather": sc["weather"],
        "indoor": z.get("indoor") or sc["indoor"],
        "envelope": z["envelope"],
        "it_load": {**sc["it_load"], "design_kw": z.get("it_design_kw") or 0.0},
        "internal": {"floor_area_m2": z["floor_area_m2"],
                     "lighting_w_m2": intl["lighting_w_m2"] if z.get("lighting_w_m2") is None else z["lighting_w_m2"],
                     "people": z.get("people") or 0.0, "people_kw_each": intl["people_kw_each"],
                     "ups_loss_ratio": intl["ups_loss_ratio"],
                     "misc_kw": (z.get("misc_kw") or 0.0) + interzone_kw(sc, z)},
        "ventilation": {"outdoor_air_m3_h": z.get("outdoor_air_m3_h") or 0.0},
        "cooling_plant": cp,
        "cooling_layout": lay,
        "building": {"source": f"zone:{z['id']}", "ew_m": g["ew_m"], "ns_m": g["ns_m"], "height_m": g["height_m"]},
    }
    return out


def halls(sc, cfg=None):
    """[(zone, 존 시나리오)] 전산실만. 단일 존이면 [(None, sc)]"""
    if not zones_of(sc):
        return [(None, sc)]
    return [(z, zone_scenario(sc, z, cfg)) for z in sc["zones"] if z["kind"] == "data_hall"]


def others(sc):
    return [(z, zone_scenario(sc, z)) for z in (zones_of(sc) or []) if z["kind"] != "data_hall"]


# ---------- 엔진 합산 ----------
def _ctrl_fields(loop):
    return {k: loop[k] for k in ("chiller_on", "reason", "supply_overshoot_k") if k in loop}


def _merge_loop(loops):
    loops = [x for x in loops if x]
    if not loops:
        return None
    load = sum(x["load_kw"] for x in loops)
    ch = sum(x["chiller_load_kw"] for x in loops)
    x_fc = 1 - ch / load if load > 1e-9 else loops[0]["free_fraction"]
    out = {"load_kw": round(load, 1), "free_fraction": round(x_fc, 3), "chiller_load_kw": round(ch, 1),
           "mode": loop_mode(x_fc)}
    out.update(_ctrl_fields(loops[0]))
    return out


def _merge_hour(xs):
    h0 = xs[0]
    L = {k: round(sum(x["load_kw"][k] for x in xs), 2) for k in SUM_LOAD}
    P = {k: round(sum(x["plant"]["power_kw"][k] for x in xs), 2) for k in POWER}
    air = _merge_loop([x["plant"]["air_loop"] for x in xs])
    liq_loops = [x["plant"]["liquid_loop"] for x in xs if x["plant"]["liquid_loop"]]
    liq = None
    if liq_loops:
        liq = _merge_loop(liq_loops)
        f = liq_loops[0]
        liq.update(chiller_cop=f["chiller_cop"], tcs_supply_c=f["tcs_supply_c"], tcs_return_c=f["tcs_return_c"],
                   fws_supply_c=f["fws_supply_c"], fws_return_c=f["fws_return_c"],
                   fws_flow_m3_h=round(sum(x["fws_flow_m3_h"] for x in liq_loops), 1))
        for k in ("chiller_on", "reason", "supply_overshoot_k"):
            if k in f:
                liq[k] = f[k]
    mk = {k: round(sum(x["makeup_water_m3_h"][k] for x in xs), 4) for k in ("evaporation", "drift", "blowdown", "total")}
    it = L["it"]
    plant = {"mode": plant_mode(((air["load_kw"], air["free_fraction"]),
                                 ((liq or {}).get("load_kw", 0.0), (liq or {}).get("free_fraction", 0.0))))}
    reasons = [x["plant"]["mode_reason"] for x in xs if "mode_reason" in x["plant"]]
    if reasons:
        plant["mode_reason"] = max(reasons, key=REASON_RANK.get)
    plant.update(tower_water_c=h0["plant"]["tower_water_c"], free_cooling_water_c=h0["plant"]["free_cooling_water_c"],
                 air_loop=air, liquid_loop=liq, power_kw=P,
                 pue=round(P["facility"] / it, 3) if it > 0 else None,
                 wue_l_per_kwh=round(mk["total"] * 1000.0 / it, 3) if it > 0 else None)
    return {
        "hour": h0["hour"], "minute": h0["minute"], "t_h": h0["t_h"], "sun": h0["sun"],
        "surface_solar_w_m2": h0["surface_solar_w_m2"], "weather": h0["weather"], "load_kw": L,
        "chilled_water": {**h0["chilled_water"], "flow_m3_h": round(sum(x["chilled_water"]["flow_m3_h"] for x in xs), 1)},
        "chiller": {"cop": h0["chiller"]["cop"], "power_kw": round(sum(x["chiller"]["power_kw"] for x in xs), 1)},
        "condenser_water": {**h0["condenser_water"],
                            "heat_rejection_kw": round(sum(x["condenser_water"]["heat_rejection_kw"] for x in xs), 1),
                            "flow_m3_h": round(sum(x["condenser_water"]["flow_m3_h"] for x in xs), 1)},
        "makeup_water_m3_h": mk,
        "plant": plant,
    }


def _merge_control(rs):
    cs = [r["control"] for r in rs if "control" in r]
    if not cs:
        return None
    final, sw = {}, {}
    for c in cs:   # 루프 상태는 기상·수온에만 의존 → 같은 루프면 존마다 같음
        for k, v in c["final_state"].items():
            final.setdefault(k, v)
        for k, v in c["chiller_switches"].items():
            sw.setdefault(k, v)
    return {"chiller_switches": sw, "final_state": final, "notes": cs[0]["notes"]}


def simulate_building(sc, step_min=60, init_state=None):
    zs = sc["zones"]
    rs = [simulate_zone(zone_scenario(sc, z), step_min, init_state) for z in zs]
    hours = [_merge_hour(xs) for xs in zip(*(r["hourly"] for r in rs))]
    out = {"site": sc["site"]["name"], "date": sc["site"]["date"], "step_min": step_min,
           "architecture": sc["cooling_plant"].get("architecture", rs[0]["architecture"]),
           "hourly": hours, "daily_total": daily_total(hours, step_min / 60.0)}
    ctrl = _merge_control(rs)
    if ctrl:
        out["control"] = ctrl
    out["zones"] = [{
        "id": z["id"], "name": z["name"], "floor": z["floor"], "kind": z["kind"], "architecture": r["architecture"],
        "interzone_kw": round(interzone_kw(sc, z), 2), "daily_total": r["daily_total"],
        "it_kw": [x["load_kw"]["it"] for x in r["hourly"]],
        "cooling_kw": [x["load_kw"]["total_cooling"] for x in r["hourly"]],
        "cooling_power_kw": [x["plant"]["power_kw"]["cooling_total"] for x in r["hourly"]],
        "peak_cooling_kw": max(x["load_kw"]["total_cooling"] for x in r["hourly"]),
    } for z, r in zip(zs, rs)]
    return out


# ---------- 연성 모델 합산 (기류·배관) ----------
def combined_analyze(sc, cfg, hour, analyze_one):
    """전산실별 기류 해석을 합친 결과 (최적화·브리핑이 쓰는 metrics 와 같은 키). zones 키 = 존별 metrics"""
    hs = halls(sc, cfg)
    res = [(z, analyze_one(s, s["cooling_layout"], hour)) for z, s in hs]
    ms = [r["metrics"] for _, r in res]
    first = res[0][1]
    fan_other = _other_fan_kw(sc, hour)
    rack_n = sum(m["rack_count"] for m in ms)
    m = {
        "max_inlet_c": max(x["max_inlet_c"] for x in ms),
        "mean_inlet_c": round(sum(x["mean_inlet_c"] * x["rack_count"] for x in ms) / rack_n, 2),
        "racks_over_limit": sum(x["racks_over_limit"] for x in ms),
        "rack_count": rack_n,
        "supply_ratio": min(x["supply_ratio"] for x in ms),
        "bypass_fraction": round(sum(x["bypass_fraction"] for x in ms) / len(ms), 3),
        "shi": max(x["shi"] for x in ms), "rhi": min(x["rhi"] for x in ms),
        "crah_return_c": min(x["crah_return_c"] for x in ms),
        "crah_heat_kw": round(sum(x["crah_heat_kw"] for x in ms), 1),
        "crah_fan_kw": round(sum(x["crah_fan_kw"] for x in ms) + fan_other, 1),
    }
    racks = []
    for z, r in res:
        for x in r["racks"]:
            racks.append({**x, "id": x["id"] or f"{z['id']}:{x['row']}-{x['col']}"})
    return {**first, "it_kw": round(sum(r["it_kw"] for _, r in res), 1), "metrics": m, "racks": racks,
            "air_fraction": round(sum(r["air_fraction"] * r["it_kw"] for _, r in res) / max(sum(r["it_kw"] for _, r in res), 1e-9), 3),
            "zones": [{"id": z["id"], "name": z["name"], "floor": z["floor"], "metrics": r["metrics"]} for z, r in res]}


def _other_fan_kw(sc, hour):
    """비 전산 존의 CRAH(공조기) 팬 발열 [kW] — 기류 모델이 없어 엔진 비율(crah_fan_heat_ratio)값을 그 시각 그대로 씀.
    연성 모델은 건물 합계의 엔진 팬 발열을 이 값 + 전산실 기류 모델 팬 전력으로 바꾼다"""
    os_ = others(sc)
    if not os_:
        return 0.0
    h = hour if hour is not None else 0
    return sum(simulate_zone(s, 60)["hourly"][h]["load_kw"]["crah_fan"] for _, s in os_)


def combined_networks(sc, cfg, engine_res, chw_dt, networks_one):
    """냉수: 전산실별 분기 (양정 = 최대, 설계 유량 = 합). 설계 부하는 건물 엔진 결과를 전산실 IT 비율로 나눔"""
    hs = halls(sc, cfg)
    it_tot = sum(z["it_design_kw"] for z, _ in hs) or 1.0
    nets = []
    for z, s in hs:
        share = z["it_design_kw"] / it_tot
        scaled = {"hourly": [{**x, "load_kw": {**x["load_kw"], "total_cooling": x["load_kw"]["total_cooling"] * share}}
                             for x in engine_res["hourly"]]}
        nets.append(networks_one(s, s["cooling_layout"], scaled, chw_dt))
    plant = networks_one(hs[0][1], hs[0][1]["cooling_layout"], engine_res, chw_dt)   # 냉각수 = 건물 전체 유량
    # 양정 = 건물 좌표 배관망 (야드 헤더 + 입상관 + 층 분기 + 전산실 분기, hvac/building_view.py).
    # 전산실별 segments(존 좌표)는 '전산실 상세' 3D 표시용으로 그대로 둔다
    from .building_view import plant_network
    from .plant_units import size_units
    bn = plant_network(sc, cfg, hs, nets, plant, engine_res, size_units(cfg, engine_res))
    chw = {**nets[0]["chw"], "segments": [seg for n in nets for seg in n["chw"]["segments"]],
           "head_kpa": bn["chw_head_kpa"], "friction_kpa": bn["chw_friction_kpa"], "critical_m": bn["chw_critical_m"]}
    return {"chw": chw, "cw": plant["cw"], "chw_q_design": sum(n["chw_q_design"] for n in nets),
            "cw_q_design": plant["cw_q_design"], "halls": nets, "building": bn}


class SingleZoneOnly(ValueError):
    pass


def single_zone_only(sc, feature):
    """랙 편집·배치, CAD 가져오기, IT 랙 매칭, BMS 보정은 아직 전산실 하나(단일 존) 기준"""
    if zones_of(sc):
        raise SingleZoneOnly(f"{feature}은(는) 아직 단일 존 시나리오에서만 됩니다 — 다중 존은 존별 랙 격자(zones[].racks)를 "
                             f"시나리오 파일에서 고치거나, 단일 존 시나리오에서 작업하세요")
