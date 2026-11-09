"""
냉수·냉각수 배관 수력 모델 - 관경 선정, 구간 유량, 압력손실, 펌프 양정.

가정 (모두 가정값, 실측 미보정):
- 마찰: Darcy-Weisbach + Swamee-Jain (강관 조도 0.045 mm, 물 동점성 1.2e-6 m2/s)
- 이음쇠·밸브는 직관 길이 x fitting_factor 로 환산
- CRAH 는 모두 같은 유량(밸런싱 밸브로 정유량 조정)을 받는다
- 환수관은 공급관과 같은 경로·관경(직접 환수). 루프 양정 = 2 x 공급측 최대 손실 + 기기 손실
- 운전 유량에서의 양정은 설계 양정 x (유량비)^2 (VFD 펌프, 폐회로). 냉각탑 정수두만 유량 무관
관경은 '설계 유량에서 max_velocity 이하'가 되는 가장 작은 호칭경 (Sch40 내경 근사).

좌표계는 3D 와 동일 (원점=건물 중심, x=동, y=위, z=남).
"""
import math

DN = [(50, 52.5), (65, 62.7), (80, 77.9), (100, 102.3), (125, 128.2), (150, 154.1), (200, 202.7),
      (250, 254.5), (300, 304.8), (350, 336.6), (400, 387.4), (450, 438.2), (500, 489.0), (600, 590.6),
      # 대형(수십 MW) 플랜트용 — 외경 711~1524 mm, 두께 STD 9.53 mm 기준 내경 (ASME B36.10, 근사)
      (700, 692.1), (800, 793.8), (900, 895.4), (1000, 996.9), (1200, 1200.2), (1400, 1381.1), (1500, 1504.9)]
ROUGHNESS = 4.5e-5   # m
NU = 1.2e-6          # m2/s
RHO = 1000.0         # kg/m3


def friction(re, d):
    if re <= 0:
        return 0.0
    if re < 2300:
        return 64.0 / re
    return 0.25 / math.log10(ROUGHNESS / (3.7 * d) + 5.74 / re ** 0.9) ** 2


def dp_kpa(q, d, length):
    """유량 q [m3/s], 내경 d [m], 길이 [m] -> 마찰손실 [kPa]"""
    if q <= 0 or length <= 0:
        return 0.0
    v = q / (math.pi * d * d / 4)
    return friction(v * d / NU, d) * length / d * RHO * v * v / 2 / 1000.0


def pick_dn(q, vmax):
    for dn, d_mm in DN:
        if q / (math.pi * (d_mm / 1000) ** 2 / 4) <= vmax:
            return dn, d_mm / 1000
    return DN[-1][0], DN[-1][1] / 1000


def _len(a, b):
    return math.dist(a, b)


def plant_positions(cfg, ew):
    p = cfg["plant"]
    cw, ch, cd = p["chiller_size_m"]
    tw, th, td = p["tower_size_m"]
    px = ew / 2 + p["gap_east_m"] + cw / 2
    return {"chiller": {"pos": [px, ch / 2, p["chiller_z_m"]], "size": [cw, ch, cd]},
            "tower": {"pos": [px, th / 2, p["tower_z_m"]], "size": [tw, th, td]}}


def _runs(topology, ew, ns, crahs):
    """공급 헤더 경로. 반환: (runs, is_ring). run = [(x, z, 수요 CRAH 수)], 첫 점은 분기점 J"""
    xe = ew / 2
    zn, zs, xw, xj = -ns / 2 + 2.2, ns / 2 - 2.2, -xe + 2.2, xe - 2.2
    north = sorted((x for x, z in crahs if z < 0), reverse=True)    # 동 -> 서
    south = sorted((x for x, z in crahs if z >= 0), reverse=True)
    tap = lambda xs, z: [(x, z, 1) for x in xs]
    return xj, {
        "radial": ([[(xj, None, 0), (xj, zn, 0), *tap(north, zn), (xw, zn, 0), (xw, zs, 0), *tap(south[::-1], zs)]], False),
        "dual": ([[(xj, None, 0), (xj, zn, 0), *tap(north, zn)], [(xj, None, 0), (xj, zs, 0), *tap(south, zs)]], False),
        "ring": ([[(xj, None, 0), (xj, zn, 0), *tap(north, zn), (xw, zn, 0), (xw, zs, 0), *tap(south[::-1], zs),
                   (xj, zs, 0), (xj, None, 0)]], True),
    }[topology]


def _ring_flows(edges, demand_each, q_tot):
    """단일 루프: 분기점에서 루프 정방향으로 나가는 유량 q 를 루프 수두합=0 이 되도록 이분법으로 구함"""
    def flows(q):
        out, rem = [], q
        for e in edges:
            out.append(rem)
            rem -= e["demand_end"] * demand_each
        return out

    def head(q):
        return sum(math.copysign(dp_kpa(abs(f), e["d"], e["L"]), f) for f, e in zip(flows(q), edges))

    lo, hi = 0.0, q_tot
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if head(mid) < 0 else (lo, mid)
    return flows((lo + hi) / 2)


def chw_network(cfg, dims, crahs, q_design):
    """냉수 공급측 네트워크를 설계 유량 q_design [m3/s] 기준으로 관경 선정·해석.
    반환: segments(공급+환수, 3D 표시용), head_kpa(설계 양정), critical_m(최원단 경로 길이)"""
    ew, ns, h = dims
    pc = cfg["piping"]
    vmax, ff = pc["max_velocity_m_s"], pc["fitting_factor"]
    pl = plant_positions(cfg, ew)
    px, _, cz = pl["chiller"]["pos"]
    y1 = min(3.2, h - 1)
    xj, (runs, ring) = _runs(pc["topology"], ew, ns, crahs)
    n_taps = len(crahs)
    q_each = q_design / n_taps

    trunk_pts = [(px - pl["chiller"]["size"][0] / 2, 1.0, cz), (ew / 2 + 2, 1.0, cz), (ew / 2 + 2, y1, cz), (xj, y1, cz)]
    trunk_len = sum(_len(a, b) for a, b in zip(trunk_pts, trunk_pts[1:]))
    trunk_dn, trunk_d = pick_dn(q_design, vmax)
    trunk_dp = dp_kpa(q_design, trunk_d, trunk_len * ff)

    segs = [{"pts": trunk_pts, "dn": trunk_dn, "q": q_design, "L": trunk_len}]
    worst_dp, worst_len = 0.0, 0.0
    for run in runs:
        pts = [(x, y1, cz if z is None else z) for x, z, _ in run]
        edges = [{"a": a, "b": b, "L": _len(a, b) * ff, "Lg": _len(a, b), "demand_end": run[k + 1][2]}
                 for k, (a, b) in enumerate(zip(pts, pts[1:]))]
        if ring:
            for e in edges:
                e["dn"], e["d"] = pick_dn(q_design / 2, vmax)
            for _ in range(3):   # 유량 해석 -> 구간별 재선정 반복
                fl = _ring_flows(edges, q_each, q_design)
                for e, f in zip(edges, fl):
                    e["dn"], e["d"] = pick_dn(max(abs(f), 1e-6), vmax)
            fl = _ring_flows(edges, q_each, q_design)
        else:
            rem = sum(d for _, _, d in run) * q_each
            fl = []
            for e in edges:
                fl.append(rem)
                rem -= e["demand_end"] * q_each
            for e, f in zip(edges, fl):
                e["dn"], e["d"] = pick_dn(max(f, 1e-6), vmax)
        # 분기점 기준 각 노드까지 누적 손실 -> 최원단(최대 손실) 노드.
        # 루프는 수두합=0 이므로 정방향 누적만으로 각 노드 압력이 유일하게 정해진다 (유량 역전 구간은 음수로 누적)
        acc_dp, acc_len = 0.0, 0.0
        for e, f in zip(edges, fl):
            acc_dp += math.copysign(dp_kpa(abs(f), e["d"], e["L"]), f)
            acc_len += e["Lg"]
            if e["demand_end"] and acc_dp > worst_dp:
                worst_dp, worst_len = acc_dp, acc_len
            segs.append({"pts": [e["a"], e["b"]], "dn": e["dn"], "q": abs(f), "L": e["Lg"]})

    head = 2 * (trunk_dp + worst_dp) + pc["evaporator_dp_kpa"] + pc["coil_dp_kpa"]
    out = []
    for s in segs:
        sup = [[round(c, 2) for c in p] for p in s["pts"]]
        ret = [[x, round(y + 0.9, 2), z] for x, y, z in sup]
        common = {"dn": s["dn"], "design_flow_m3_h": round(s["q"] * 3600, 1), "length_m": round(s["L"], 1)}
        out.append({"kind": "chw_supply", "points": sup, **common})
        out.append({"kind": "chw_return", "points": ret, **common})
    return {"segments": out, "head_kpa": round(head, 1), "critical_m": round(trunk_len + worst_len, 1),
            "friction_kpa": round(2 * (trunk_dp + worst_dp), 1),
            # 다중 존 건물 배관(hvac/building_view.py)용: 실 내부 분기만 (트렁크는 실 동측 벽 '인입점'에서 끊음)
            "branch": {"dp_kpa": worst_dp, "length_m": worst_len, "inlet": (ew / 2, y1, cz), "junction": trunk_pts[-1],
                       "trunk_dn": trunk_dn, "trunk_d": trunk_d}}


def cw_network(cfg, ew, q_design):
    """냉각수: 칠러 <-> 냉각탑 단일 배관. 양정 = 마찰 + 응축기 (유량^2 비례) + 냉각탑 정수두(고정)"""
    pc = cfg["piping"]
    pl = plant_positions(cfg, ew)
    px, _, cz = pl["chiller"]["pos"]
    _, _, cd = pl["chiller"]["size"]
    tw, th, td = pl["tower"]["size"]
    tz = pl["tower"]["pos"][2]
    sup = [(px - tw / 2 + 1, 0.6, tz - td / 2), (px - tw / 2 + 1, 0.6, cz + cd / 2)]
    ret = [(px + tw / 2 - 1, 1.8, cz + cd / 2), (px + tw / 2 - 1, th - 0.8, tz - td / 2 - 1.5),
           (px + tw / 2 - 1, th - 0.8, tz - td / 2)]
    dn, d = pick_dn(q_design, pc["max_velocity_m_s"])
    length = sum(_len(a, b) for path in (sup, ret) for a, b in zip(path, path[1:]))
    fr = dp_kpa(q_design, d, length * pc["fitting_factor"])
    segs = [{"kind": k, "points": [[round(c, 2) for c in p] for p in path], "dn": dn,
             "design_flow_m3_h": round(q_design * 3600, 1),
             "length_m": round(sum(_len(a, b) for a, b in zip(path, path[1:])), 1)}
            for k, path in (("cw_supply", sup), ("cw_return", ret))]
    return {"segments": segs, "dynamic_kpa": round(fr + pc["condenser_dp_kpa"], 1),
            "static_kpa": pc["tower_static_kpa"]}


def pump_kw(q_m3s, head_kpa, eff):
    return q_m3s * head_kpa / eff
