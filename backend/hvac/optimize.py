"""
냉각 구조 최적화 - 일 냉방 전력량(칠러 + CRAH 팬 + 냉수·냉각수 펌프) 최소화, 랙 흡입온도 등 제약 만족.

탐색 (격자 전수 탐색, 결정적):
  컨테인먼트 x CRAH 수 x 풍량 배율 x 타일 밸런싱 -> 기류 해석 (피크 IT 시각 1회)
    급기온도: 기류 모델에서 (흡입 - 급기) 상승폭은 급기온도와 무관하므로
              허용 최대 급기 = 흡입 상한 - 최대 상승폭 (0.5℃ 내림, 상·하한 적용) 을 바로 계산
    x 냉수 ΔT x 배관 토폴로지 -> 에너지 (배관은 (CRAH 대수, 토폴로지, ΔT) 별로 1회만 해석해 재사용)
설계변수가 아닌 값(랙 배치, 냉각수 온도, 배관 최대 유속 등)은 입력 레이아웃 그대로.
결과는 모델(가정값) 기준의 상대 비교이며, 컨테인먼트·배관 변경 공사비는 목적함수에 없다.
"""
import copy
import math

from .airflow import analyze, building_dims, crah_positions, crah_unit_rated
from .engine import simulate
from .plant import energy, networks, params_of, peak_hour, summary, violations


def _frange(lo, hi, step):
    n = int(round((hi - lo) / step))
    return [round(lo + i * step, 4) for i in range(n + 1)]


DEFAULT_SPACE = {
    "containment": ["open", "cold_aisle", "hot_aisle"],
    "crah_per_side": None,                       # None = 현재 값만
    "airflow_ratio": _frange(0.3, 1.5, 0.05),       # VFD 최저 30% (가정)
    "tile_balancing": [0.0, 0.25, 0.5, 0.75, 1.0],
    "chw_delta_t": [5.0, 6.0, 7.0, 8.0, 10.0],
    "topology": ["radial", "dual", "ring"],
    "supply_temp_min_c": 16.0,
    "supply_temp_max_c": 25.0,
    "chw_supply_min_c": 5.0,
    "top_n": 5,
}


def optimize(sc, cfg, space=None):
    sp = {**DEFAULT_SPACE, **{k: v for k, v in (space or {}).items() if v is not None}}
    res = simulate(sc, 60)
    ph = peak_hour(sc)
    approach, ret_app = cfg["coil_approach_k"], cfg["coil_return_approach_k"]

    # 기준안 (현재 설정)
    base_af = analyze(sc, cfg, ph)["metrics"]
    cp = sc["cooling_plant"]
    base_nets = networks(sc, cfg, res, cp["chw_return_c"] - cp["chw_supply_c"])
    hours, daily = energy(sc, cfg, res, base_af["crah_fan_kw"], cp["chw_supply_c"], cp["chw_return_c"], base_nets)
    base_viol = violations(cfg, base_af, cfg["supply_temp_c"], cp["chw_supply_c"], cp["chw_return_c"])
    baseline = summary(params_of(sc, cfg), base_af, hours, daily, base_nets, base_viol, ph)

    # CRAH 대수를 바꾸는 후보는 장비 1대 크기를 현재안으로 고정 → 대수만큼 총풍량·용량이 변하고, 같은 풍량이면 대수가 많을수록 팬 전력 감소(풍량^3)
    unit = crah_unit_rated(sc, cfg, len(crah_positions(cfg, *building_dims(sc)[:2])))
    net_cache = {}

    def nets_for(c, topo, dt):
        """배관은 CRAH 배치(대수)·토폴로지·ΔT 에 따라 달라짐 → 그 조합별 1회 해석"""
        key = (c["crah"]["per_side"], topo, dt)
        if key not in net_cache:
            c2 = copy.deepcopy(c)
            c2["piping"]["topology"] = topo
            net_cache[key] = networks(sc, c2, res, dt)
        return net_cache[key]

    found, n_eval, n_air = [], 0, 0
    for cont in sp["containment"]:
        for per_side in (sp["crah_per_side"] or [cfg["crah"]["per_side"]]):
            for ratio in sp["airflow_ratio"]:
                for beta in sp["tile_balancing"]:
                    c = copy.deepcopy(cfg)
                    c["containment"] = cont
                    c["crah"].update(per_side=per_side, airflow_ratio=ratio, tile_balancing=beta)
                    if per_side != cfg["crah"]["per_side"]:
                        c["crah"].update(positions=None, unit_airflow_m3_s=unit)
                    m = analyze(sc, c, ph)["metrics"]
                    n_air += 1
                    rise = m["max_inlet_c"] - c["supply_temp_c"]
                    ret_rise = m["crah_return_c"] - c["supply_temp_c"]
                    t_sup = min(math.floor((cfg["inlet_limit_c"] - rise) * 2) / 2, sp["supply_temp_max_c"])
                    if t_sup < sp["supply_temp_min_c"]:
                        continue
                    shift = t_sup - c["supply_temp_c"]
                    m_s = {**m, "max_inlet_c": round(m["max_inlet_c"] + shift, 2),
                           "mean_inlet_c": round(m["mean_inlet_c"] + shift, 2),
                           "crah_return_c": round(t_sup + ret_rise, 2), "racks_over_limit": 0}
                    c["supply_temp_c"] = t_sup
                    chw_s = round(t_sup - approach, 2)
                    if chw_s < sp["chw_supply_min_c"]:
                        continue
                    for dt in sp["chw_delta_t"]:
                        chw_r = round(chw_s + dt, 2)
                        if chw_r > m_s["crah_return_c"] - ret_app + 1e-9:
                            continue
                        for topo in sp["topology"]:
                            c2 = copy.deepcopy(c)
                            c2["piping"]["topology"] = topo
                            nets = nets_for(c, topo, dt)
                            hrs, dly = energy(sc, c2, res, m["crah_fan_kw"], chw_s, chw_r, nets)
                            n_eval += 1
                            params = {"containment": cont, "crah_per_side": per_side, "airflow_ratio": ratio,
                                      "tile_balancing": beta, "supply_temp_c": t_sup, "chw_supply_c": chw_s,
                                      "chw_return_c": chw_r, "topology": topo}
                            found.append(summary(params, m_s, hrs, dly, nets, [], ph))

    found.sort(key=lambda s: s["daily_kwh"]["total"])
    top, seen = [], set()
    for s in found:   # 같은 에너지·같은 주요 변수 조합은 1개만
        key = (s["params"]["containment"], s["params"]["topology"], round(s["daily_kwh"]["total"], 0))
        if key in seen:
            continue
        seen.add(key)
        top.append(s)
        if len(top) >= sp["top_n"]:
            break
    return {"peak_hour": ph, "airflow_evaluations": n_air, "evaluated": n_eval,
            "baseline": baseline, "candidates": top}


def apply_candidate(sc, cfg, params):
    """후보 변수를 시나리오(냉수 온도)와 레이아웃에 반영한 새 (시나리오, 레이아웃)"""
    sc2, c = copy.deepcopy(sc), copy.deepcopy(cfg)
    sc2["cooling_plant"].update(chw_supply_c=params["chw_supply_c"], chw_return_c=params["chw_return_c"])
    c["containment"] = params["containment"]
    if params["crah_per_side"] != c["crah"]["per_side"]:
        unit = crah_unit_rated(sc, cfg, len(crah_positions(cfg, *building_dims(sc)[:2])))
        c["crah"].update(positions=None, unit_airflow_m3_s=unit)
    c["crah"].update(per_side=params["crah_per_side"], airflow_ratio=params["airflow_ratio"],
                     tile_balancing=params["tile_balancing"])
    c["supply_temp_c"] = params["supply_temp_c"]
    c["piping"]["topology"] = params["topology"]
    return sc2, c
