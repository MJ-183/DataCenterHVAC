"""
전산실 기류(저차원) 모델 - 랙별 흡입온도 / CRAH 풍량 균형 / 팬 전력.

CFD 가 아니라 '대안 간 상대 비교'용 집중정수 모델이다. 아래는 모두 가정값이며 실측으로 보정되지 않았다.
- 컨테인먼트별 기본 재순환·바이패스 비율 (CONTAINMENT)
- 열 끝 랙의 재순환 가중 (END_FACTOR)
- CRAH 도달 거리 감쇠 exp(-d/throw), 타일 밸런싱 β 로 수요 비례 배분과 혼합
- CRAH 팬 전력은 풍량의 3승 (VFD 상사법칙)

식 (랙 i):
  Vreq_i = kW_i / (rho*cp*dT_srv)                         서버 요구 풍량
  Vsup_i = Vavail * ((1-b)*cov_i/sum(cov) + b*Vreq_i/sum(Vreq))   랙에 배분된 급기 (b = tile_balancing)
  r_i    = max(r_base*end_i, 1 - Vsup_i/Vreq_i)          재순환 비율 (급기 부족분은 고온 공기로 보충)
  Tin_i  = Tsup + r_i*dT/(1-r_i)                          흡입온도 (Tin = (1-r)Tsup + r(Tin+dT) 를 풀이)
  Tout_i = Tin_i + dT
에너지 보존: CRAH 가 제거하는 열 = rho*cp*Vc*(T_ret - Tsup) = 전체 IT 발열 (tests 에서 확인)
"""
import math

from .engine import liquid_capture

RHO_CP = 1.2 * 1.006          # kJ/m3.K  (공기 밀도 x 비열)
CONTAINMENT = {               # 컨테인먼트 방식: (재순환 기본비, 바이패스 비)  - 가정값
    "open": (0.12, 0.25),
    "cold_aisle": (0.04, 0.12),
    "hot_aisle": (0.02, 0.08),
}
END_FACTOR = (2.0, 1.5)       # 열 맨 끝 / 두 번째 랙의 재순환 가중 - 가정값
R_MAX = 0.95


def building_dims(sc, fallback=(60.0, 50.0, 10.0)):
    """CAD 치수(sc.building)가 있으면 그대로, 없으면 외피 면적에서 역산
    (frontend/src/components/Scene3D/layout.ts 와 동일 규칙). 다중 존이면 첫 전산실 존 평면"""
    if sc.get("zones"):
        g = next(z for z in sc["zones"] if z["kind"] == "data_hall")["geometry"]
        return g["ew_m"], g["ns_m"], g["height_m"]
    if sc.get("building"):
        b = sc["building"]
        return b["ew_m"], b["ns_m"], b["height_m"]
    walls = sc["envelope"]["walls"]
    area = lambda o: sum(w["area_m2"] for w in walls if w["orientation"] == o)
    ans, aew = (area("N") + area("S")) / 2, (area("E") + area("W")) / 2
    roof = sc["envelope"]["roof"]["area_m2"] or sc["internal"]["floor_area_m2"]
    if ans > 0 and aew > 0 and roof > 0:
        h = math.sqrt(ans * aew / roof)
        if 3 <= h <= 60:
            return ans / h, aew / h, h
    return fallback


def rack_items(cfg):
    """CAD 랙 목록을 (row, x) 순으로 정렬해 col 을 붙인 것 [(row, col, x, z, item)]. 없으면 None"""
    items = cfg["racks"].get("items")
    if not items:
        return None
    out, col, prev = [], 0, None
    for it in sorted(items, key=lambda r: (r["row"], r["x_m"])):
        col = col + 1 if it["row"] == prev else 0
        prev = it["row"]
        out.append((it["row"], col, it["x_m"], it["z_m"], it))
    return out


def rack_positions(cfg, ew, ns):
    """랙 배치 [(row, col, x, z)]. 좌표계는 3D 와 같음 (원점=건물 중심, x=동, z=남).
    CAD 랙(items)이 있으면 그 좌표 그대로 (건물 치수로 자르지 않음)"""
    items = rack_items(cfg)
    if items:
        return ([(j, i, x, z) for j, i, x, z, _ in items], max(j for j, *_ in items) + 1,
                max(i for _, i, *_ in items) + 1)
    g = cfg["racks"]
    pitch = g["depth_m"] + g["aisle_m"]
    rows = max(min(g["rows"], int((ns - 10) // pitch)), 0)
    per = max(min(g["per_row"], int((ew - 14) // g["width_m"])), 0)
    out = []
    for j in range(rows):
        z = -((rows - 1) * pitch) / 2 + j * pitch
        for i in range(per):
            out.append((j, i, -(per * g["width_m"]) / 2 + g["width_m"] / 2 + i * g["width_m"], z))
    return out, rows, per


def crah_positions(cfg, ew, ns):
    c = cfg["crah"]
    if c.get("positions"):
        return [tuple(p) for p in c["positions"]]
    n = c["per_side"]
    xe, zs = ew / 2, ns / 2 - 0.9
    pos = []
    for i in range(n):
        x = -xe + 6 + (i * (ew - 12)) / max(n - 1, 1)
        pos += [(x, -zs), (x, zs)]
    return pos


def rack_weights(cfg, racks, hour):
    """랙별 IT 발열 배분 가중치. 우선순위: 랙 시간별 프로파일 > 랙 설계 kW > 열별 밀도(row_density) > 균일"""
    items = rack_items(cfg)
    if items:
        prof = [it.get("kw_profile") for *_, it in items]
        if all(prof) and sum(p[hour] for p in prof) > 0:
            return [p[hour] for p in prof]
        dk = [it.get("design_kw") for *_, it in items]
        if all(d is not None for d in dk) and sum(dk) > 0:
            return dk
    dens = cfg["racks"].get("row_density")
    if dens and len(dens) > max(j for j, *_ in racks):
        return [dens[j] for j, _, _, _ in racks]
    return [1.0] * len(racks)


def rack_phis(sc, cfg, racks):
    """랙별 액체 회수 비율 φ (Phase 15). 우선순위: 랙 liquid_fraction > 열 row_liquid_fraction.
    랙·열 지정이 하나도 없으면 None (= 냉각 방식 전역값 사용). 지정이 있으면 미지정 랙은 공랭(0)"""
    items = rack_items(cfg)
    rows = cfg["racks"].get("row_liquid_fraction")
    row_v = lambda j: rows[j] if rows and j < len(rows) and rows[j] is not None else 0.0
    if items:
        vals = [it.get("liquid_fraction") for *_, it in items]
        if all(v is None for v in vals) and not (rows and any(v is not None for v in rows)):
            return None
        return [v if v is not None else row_v(j) for (j, *_), v in zip(items, vals)]
    if rows and any(v is not None for v in rows):
        return [row_v(j) for j, *_ in racks]
    return None


def air_fraction(sc, cfg, racks=None, weights=None):
    """IT 발열 중 공기로 나가는 비율 (랙별 φ 가 있으면 발열 가중 평균)"""
    if racks is None:
        ew, ns, _ = building_dims(sc)
        racks = rack_positions(cfg, ew, ns)[0]
    phis = rack_phis(sc, cfg, racks) if racks else None
    if phis is None:
        return 1.0 - liquid_capture(sc["cooling_plant"])
    util = sc["it_load"]["utilization"]
    w = weights or rack_weights(cfg, racks, max(range(24), key=lambda k: util[k]))
    tot = sum(w)
    return sum(x * (1 - p) for x, p in zip(w, phis)) / tot if tot > 0 else 1.0


def crah_unit_rated(sc, cfg, n):
    """CRAH 1대 정격 풍량 [m3/s]. crah.unit_airflow_m3_s 가 있으면 그 값(대수를 바꿔도 장비 크기 고정),
    없으면 공기측 설계 풍량(IT 설계용량 x 공기 배출 비율 x 여유율)을 n 대로 나눈 값"""
    c = cfg["crah"]
    if c.get("unit_airflow_m3_s"):
        return c["unit_airflow_m3_s"]
    if sc.get("zones"):   # 다중 존: 첫 전산실 기준 (전산실끼리 CRAH 크기가 같다고 가정)
        from .multizone import halls
        _, s0 = halls(sc, cfg)[0]
        return crah_unit_rated(s0, s0["cooling_layout"], n)
    air_frac = air_fraction(sc, cfg)
    return sc["it_load"]["design_kw"] * air_frac / (RHO_CP * cfg["server_delta_t_k"]) * c["design_margin"] / n


def analyze(sc, cfg, hour=None, rated_units=None):
    """sc: 시나리오 dict, cfg: CoolingLayout dict(기본값 채워진 것), hour: 0~23 또는 None(IT 최대 시각)
    rated_units: CRAH 정격 풍량을 나눌 대수. None 이면 현재 대수 (N+1 해석에서 고장 전 대수를 넘겨 정격 고정)"""
    if sc.get("zones"):   # 다중 존: 전산실별 해석을 합침
        from .multizone import combined_analyze
        return combined_analyze(sc, cfg, hour, lambda s, c, h: analyze(s, c, h, rated_units))
    util = sc["it_load"]["utilization"]
    if hour is None:
        hour = max(range(24), key=lambda k: util[k])
    it_kw = sc["it_load"]["design_kw"] * util[hour]
    ew, ns, _ = building_dims(sc)
    racks, rows, per = rack_positions(cfg, ew, ns)
    crahs = crah_positions(cfg, ew, ns)
    if not racks or not crahs:
        raise ValueError("배치된 랙 또는 CRAH 가 없습니다 (건물 치수 대비 랙 설정 확인)")

    dt = cfg["server_delta_t_k"]
    t_sup = cfg["supply_temp_c"]
    r_base, b_fixed = CONTAINMENT[cfg["containment"]]
    r_base *= cfg.get("recirculation_scale", 1.0)   # BMS 보정 배율 (기본 1)
    c = cfg["crah"]

    w = rack_weights(cfg, racks, hour)
    kw = [it_kw * x / sum(w) for x in w] if sum(w) > 0 else [it_kw / len(racks)] * len(racks)
    row_len = {}
    for j, *_ in racks:
        row_len[j] = row_len.get(j, 0) + 1
    items = rack_items(cfg)
    ids = [it["id"] for *_, it in items] if items else [None] * len(racks)
    # 냉각 방식: 액체(DLC 냉각판/액침)로 회수되는 발열은 공기로 나가지 않으므로 서버 요구 풍량에서 제외
    phis = rack_phis(sc, cfg, racks)
    if phis is None:
        air_frac = 1.0 - liquid_capture(sc["cooling_plant"])
        v_req = [k * air_frac / (RHO_CP * dt) for k in kw]
    else:   # 랙별 혼합 냉각 (Phase 15)
        v_req = [k * (1 - p) / (RHO_CP * dt) for k, p in zip(kw, phis)]
        air_frac = sum(k * (1 - p) for k, p in zip(kw, phis)) / sum(kw) if sum(kw) > 0 else 1.0
    v_req_tot = sum(v_req)

    # CRAH 풍량: 1대 정격 x airflow_ratio. 부하 시각이 달라져도 고정(VFD 로 ratio 만 변경)
    v_each_rated = crah_unit_rated(sc, cfg, rated_units or len(crahs))
    v_each = v_each_rated * c["airflow_ratio"]
    v_c = v_each * len(crahs)
    v_avail = v_c * (1 - b_fixed)

    cov = [sum(math.exp(-math.hypot(x - cx, z - cz) / c["throw_length_m"]) for cx, cz in crahs)
           for _, _, x, z in racks]
    beta = c["tile_balancing"]
    out, sum_excess, q_check = [], 0.0, 0.0
    t_out_acc = 0.0
    for idx, (j, i, x, z) in enumerate(racks):
        demand = v_req[idx] / v_req_tot if v_req_tot > 0 else 1 / len(racks)
        share = (1 - beta) * cov[idx] / sum(cov) + beta * demand
        v_sup = v_avail * share
        end = min(i, row_len[j] - 1 - i)
        r = r_base * (END_FACTOR[end] if end < len(END_FACTOR) else 1.0)
        r = min(max(r, 1 - v_sup / v_req[idx]) if v_req[idx] > 0 else r, R_MAX)
        excess = max(v_sup - v_req[idx] * (1 - r), 0.0)
        sum_excess += excess
        t_in = t_sup + r * dt / (1 - r)
        t_o = t_in + dt
        t_out_acc += v_req[idx] * (1 - r) * t_o
        out.append({"id": ids[idx], "row": j, "col": i, "x_m": round(x, 2), "z_m": round(z, 2), "kw": round(kw[idx], 2),
                    "required_m3_s": round(v_req[idx], 3), "supplied_m3_s": round(v_sup, 3),
                    "recirculation": round(r, 3), "inlet_c": round(t_in, 2), "outlet_c": round(t_o, 2)})

    v_bypass = (v_c - v_avail) + sum_excess
    # CRAH 환기 온도: 서버를 통과한 공기(고온) + 바이패스 공기(공급온도)의 혼합
    t_ret = (t_out_acc + v_bypass * t_sup) / v_c
    q_crah = RHO_CP * v_c * (t_ret - t_sup)

    inlets = [r_["inlet_c"] for r_ in out]
    sum_in = sum(r_["inlet_c"] - t_sup for r_ in out)
    sum_out = sum(r_["outlet_c"] - t_sup for r_ in out)
    shi = sum_in / sum_out if sum_out > 0 else 0.0
    fan_kw = len(crahs) * c["fan_kw_per_m3_s"] * v_each_rated * (v_each / v_each_rated) ** 3

    return {
        "hour": hour,
        "it_kw": round(it_kw, 1),
        "air_fraction": round(air_frac, 3),
        "room": {"ew_m": round(ew, 2), "ns_m": round(ns, 2), "rows": rows, "per_row": per},
        "racks": out,
        "crah": [{"x_m": round(x, 2), "z_m": round(z, 2), "airflow_m3_s": round(v_each, 2)} for x, z in crahs],
        "metrics": {
            "max_inlet_c": max(inlets), "mean_inlet_c": round(sum(inlets) / len(inlets), 2),
            "racks_over_limit": sum(1 for t in inlets if t > cfg["inlet_limit_c"]),
            "rack_count": len(inlets),
            "supply_ratio": round(v_c / v_req_tot, 3) if v_req_tot > 0 else 0.0,
            "bypass_fraction": round(v_bypass / v_c, 3),
            "shi": round(shi, 3), "rhi": round(1 - shi, 3),
            "crah_return_c": round(t_ret, 2),
            "crah_heat_kw": round(q_crah, 1),
            "crah_fan_kw": round(fan_kw, 1),
        },
    }
