"""
다중 존 건물 전체 보기 (Phase 18 연장) — 층·존·랙·CRAH·옥외 설비·CDU·배관을 '건물 좌표계'로 만든다.

좌표계: 원점 = 건물 중심 (존 geometry.x_m/z_m 와 같은 기준), x=동, y=위 (1층 바닥 = 0), z=남.
층 바닥 높이 = 아래층 층고(그 층 존 높이 중 최대) 누적. 전산실 상세(/cooling/evaluate 의 airflow·piping·geometry)는
그 전산실 존 좌표계 그대로이고, 여기서 존 오프셋(x_m, z_m) + 층 바닥 높이만큼 옮긴다.

배치 규칙 (모두 표시·배관 길이용 가정값, 엔진 수식과 무관):
- 칠러: 건물 외곽 동측 벽 + gap_east_m 에 한 열 (z 방향, 간격 = 깊이 + 1.5 m). 대수 = plant_units total (없으면 1대, chiller_z_m)
- 냉각탑: 칠러 열 동측 6 m 뒤 열 (간격 = 깊이 + 1 m). 열 길이가 건물·칠러 열보다 길면 다음 열 (반 칸 엇갈림 — 배관이 탑 사이로)
- CDU: 대수를 전산실 IT 설계부하 비율로 나눠 같은 층 지원(support) 존(가장 넓은 곳)에 격자 배치
  (없으면 같은 층 다른 비전산 존, 그것도 없으면 전산실 안)
- CAD 에서 읽은 위치(cooling_layout.plant.chiller_positions / tower_positions / cdu_positions)가 있으면 그것을 우선
배관 (건물 좌표, PipeSegment 형식 + part):
- 냉수: 칠러 → 야드 헤더 → 건물 인입 → 입상관(수직) → 층 분기 → 전산실 인입점(실 동측 벽) → 기존 전산실 분기(chw_network)
- 냉각수: 냉각탑 → 냉각수 헤더 → 칠러 응축기
- FWS (hybrid_dlc / immersion): 야드 → FWS 입상관 → 층별 CDU (표시용. 액체 루프 펌프 전력은 엔진 계수 그대로)
펌프 양정 (기존 합산 규칙 유지):
- 냉수 = 2 x (야드 + 건물 인입 + 입상관 + 층 분기 + 전산실 내부 최원단 마찰) + 증발기 + 코일, 전산실 중 최대.
  입상관 정수두는 폐회로라 0 (올라간 만큼 환수관으로 내려옴), 마찰은 입상관 길이 포함
- 냉각수: 계산은 기존 단일 배관 모델 그대로. 야드 헤더 기준 2 x (냉각탑 ~ 칠러 최원단 마찰) + 응축기 는
  참고값(cw_yard_dynamic_kpa)으로만 보고 (반영하면 냉각수 펌프 전력이 바뀌므로 승인 후)
"""
import math
from collections import Counter

from .piping import dp_kpa, pick_dn

FLOOR_DEFAULT_M = 6.0
RISER_OFFSET_M = 2.0                  # 전산실 동측 벽 ~ 냉수 입상관
YARD_Y = 1.0                          # 야드 냉수관 높이 (chw_network 트렁크와 같음)
CHW_RET_DY, RISER_RET_DX = 0.9, 0.9   # 환수관 = 공급관 + (dx, 0.9, 0) (입상관만 x 로도 비켜 둠)
CW_Y, CW_RET_DY = 0.6, 1.2
FWS_Y, FWS_RET_DY, FWS_RISER_DZ, FWS_RET_DX = 2.6, 0.5, 2.0, 0.6
HEADER_GAP_M = 2.0                    # 칠러 면 ~ 헤더
CH_GAP, TW_GAP, TW_COL_GAP, TW_FROM_CH = 1.5, 1.0, 2.0, 6.0
CDU_SIZE = (1.2, 2.2, 1.2)            # 폭·높이·깊이 [m]
CDU_PITCH, CDU_MARGIN, CDU_COLS = (2.2, 3.0), 3.0, 6


def floors(sc):
    hs = {}
    for z in sc["zones"]:
        hs[z["floor"]] = max(hs.get(z["floor"], 0.0), z["geometry"]["height_m"])
    out, e = [], 0.0
    for f in range(max(hs) + 1):
        h = hs.get(f, FLOOR_DEFAULT_M)
        out.append({"floor": f, "elevation_m": round(e, 3), "height_m": h})
        e += h
    return out


def zone_rect(z):
    g = z["geometry"]
    x, zz = g.get("x_m") or 0.0, g.get("z_m") or 0.0
    return x - g["ew_m"] / 2, zz - g["ns_m"] / 2, x + g["ew_m"] / 2, zz + g["ns_m"] / 2


def bbox(pts):
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(zs), max(xs), max(zs)


def footprint(sc):
    """건물 외곽 [[x, z], ...]: CAD 외곽선(building.footprint)이 있으면 그것, 없으면 존 사각형들의 외접 사각형"""
    b = sc.get("building") or {}
    if b.get("footprint") and not str(b.get("source", "")).startswith("zone:"):
        return [[float(x), float(z)] for x, z in b["footprint"]]
    x0, z0, x1, z1 = bbox([p for z in sc["zones"] for r in [zone_rect(z)] for p in ((r[0], r[1]), (r[2], r[3]))])
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


def _split(n, weights):
    """정수 n 을 가중치 비율로 나눔 (최대 잔여 배분)"""
    tot = sum(weights)
    if tot <= 0:
        weights, tot = [1.0] * len(weights), float(len(weights))
    raw = [n * w / tot for w in weights]
    cnt = [math.floor(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: (cnt[i] - raw[i], i))[:n - sum(cnt)]:
        cnt[i] += 1
    return cnt


def cdu_layout(sc, n):
    """CDU n 대 → [(floor, zone id, x, z)] 전산실 IT 비율로 나눠 같은 층 지원 존 격자에"""
    halls = [z for z in sc["zones"] if z["kind"] == "data_hall"]
    if not halls or n <= 0:
        return []
    used, out = Counter(), []
    for hz, k in zip(halls, _split(n, [max(z.get("it_design_kw") or 0.0, 0.0) for z in halls])):
        same = [z for z in sc["zones"] if z["floor"] == hz["floor"] and z["kind"] != "data_hall"]
        cand = [z for z in same if z["kind"] == "support"] or same
        tgt = max(cand, key=lambda z: z["geometry"]["ew_m"] * z["geometry"]["ns_m"]) if cand else hz
        x0, z0, _, _ = zone_rect(tgt)
        cols = max(1, min(CDU_COLS, int((tgt["geometry"]["ew_m"] - 2 * CDU_MARGIN) // CDU_PITCH[0]) + 1))
        for _ in range(k):
            r, c = divmod(used[tgt["id"]], cols)
            used[tgt["id"]] += 1
            out.append((hz["floor"], tgt["id"], round(x0 + CDU_MARGIN + c * CDU_PITCH[0], 3),
                        round(z0 + CDU_MARGIN + r * CDU_PITCH[1], 3)))
    return out


def _zone_at(sc, f, x, z):
    for zn in sc["zones"]:
        x0, z0, x1, z1 = zone_rect(zn)
        if zn["floor"] == f and x0 - 1e-6 <= x <= x1 + 1e-6 and z0 - 1e-6 <= z <= z1 + 1e-6:
            return zn["id"]
    return None


def plant_layout(sc, cfg, units):
    """옥외 칠러·냉각탑 중심 [(x, z)], CDU [(floor, zone, x, z)], 출처, 메모"""
    p = cfg["plant"]
    u = units or {}
    fx0, fz0, fx1, fz1 = bbox(footprint(sc))
    zc = (fz0 + fz1) / 2
    cw, _, cd = p["chiller_size_m"]
    tw, _, td = p["tower_size_m"]
    src, notes = {}, []
    n_ch = (u.get("chiller") or {}).get("total") or 1
    n_tw = (u.get("tower") or {}).get("total") or 1
    if p.get("chiller_positions"):
        chs, src["chiller"] = [tuple(c) for c in p["chiller_positions"]], "cad"
        if u.get("chiller") and len(chs) != n_ch:
            notes.append(f"도면 칠러 {len(chs)}대 ≠ 대수 산정 {n_ch}대 — 도면 위치를 그대로 표시")
    else:
        xc = fx1 + p["gap_east_m"] + cw / 2
        pc = cd + CH_GAP
        chs = ([(xc, zc + (i - (n_ch - 1) / 2) * pc) for i in range(n_ch)] if n_ch > 1 else [(xc, p["chiller_z_m"])])
        src["chiller"] = "auto"
    if p.get("tower_positions"):
        tws, src["tower"] = [tuple(t) for t in p["tower_positions"]], "cad"
        if u.get("tower") and len(tws) != n_tw:
            notes.append(f"도면 냉각탑 {len(tws)}대 ≠ 대수 산정 {n_tw}대 — 도면 위치를 그대로 표시")
    else:
        xt = max(x for x, _ in chs) + cw / 2 + TW_FROM_CH + tw / 2
        pt = td + TW_GAP
        if n_tw > 1:
            span = max(fz1 - fz0, len(chs) * (cd + CH_GAP))
            per = max(1, int(span // pt))
            m1 = min(per, n_tw)
            tws = []
            for i in range(n_tw):
                k, j = divmod(i, per)
                tws.append((xt + k * (tw + TW_COL_GAP), zc + (j - (m1 - 1) / 2) * pt + (pt / 2 if k % 2 else 0.0)))
        else:
            tws = [(xt, p["tower_z_m"])]
        src["tower"] = "auto"
    if p.get("cdu_positions"):
        cdus = [(c["floor"], _zone_at(sc, c["floor"], c["x_m"], c["z_m"]), c["x_m"], c["z_m"]) for c in p["cdu_positions"]]
        src["cdu"] = "cad"
        n_cdu = (u.get("cdu") or {}).get("total")
        if n_cdu and n_cdu != len(cdus):
            notes.append(f"도면 CDU {len(cdus)}대 ≠ 대수 산정 {n_cdu}대 — 도면 위치를 그대로 표시")
    else:
        cdus = cdu_layout(sc, (u.get("cdu") or {}).get("total") or 0)
        src["cdu"] = "auto"
    return {"chiller": chs, "tower": tws, "cdu": cdus, "source": src, "notes": notes}


# ---------- 배관 ----------
class _Net:
    """구간 목록 (공급 + 환수). 압력손실은 공급측 구간마다 기억해 경로 합을 구함"""

    def __init__(self, vmax, ff):
        self.vmax, self.ff, self.segs = vmax, ff, []

    def add(self, kind, part, pts, q, ret_shift, q_size=None, zone=None, floor=None, ret_pts=None):
        """pts 방향 = 공급 흐름 방향. q_size: 관경 선정 유량 (예비기 연결관은 운전 유량 0, 관경은 1대 유량).
        환수관 = ret_pts (공급 방향 순서) 또는 공급 점마다 ret_shift, 흐름 방향은 반대"""
        dn, d = pick_dn(max(q_size if q_size is not None else q, 1e-6), self.vmax)
        L = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        common = {"dn": dn, "design_flow_m3_h": round(q * 3600, 1), "length_m": round(L, 1), "part": part,
                  "zone": zone, "floor": floor}
        r = lambda ps: [[round(c, 2) for c in p] for p in ps]
        self.segs.append({"kind": f"{kind}_supply", "points": r(pts), **common})
        self.segs.append({"kind": f"{kind}_return", "points": r((ret_pts or [ret_shift(p) for p in pts])[::-1]),
                          **common})
        return dp_kpa(q, d, L * self.ff), L


def _header(net, kind, part, x, y, taps, ret):
    """z 방향 헤더. taps = [(z, 유입 유량 + / 유출 -)] → 노드 z 목록, 구간 [(z_a, z_b, dp, L)]"""
    zs = sorted({round(z, 6) for z, _ in taps})
    inflow = Counter()
    for z, q in taps:
        inflow[round(z, 6)] += q
    edges, cum = [], 0.0
    for a, b in zip(zs, zs[1:]):
        cum += inflow[a]
        pts = [(x, y, a), (x, y, b)] if cum >= 0 else [(x, y, b), (x, y, a)]
        dp, L = net.add(kind, part, pts, abs(cum), ret)
        edges.append((a, b, dp, L))
    return edges


def _along(edges, za, zb):
    lo, hi = sorted((round(za, 6), round(zb, 6)))
    sel = [e for e in edges if e[0] >= lo - 1e-9 and e[1] <= hi + 1e-9]
    return sum(e[2] for e in sel), sum(e[3] for e in sel)


def _l_path(a, b):
    """수평 L 자 경로 (x 먼저가 아니라 z 먼저: 입상관·헤더 축을 따라간 뒤 꺾음)"""
    (xa, y, za), (xb, _, zb) = a, b
    if abs(xa - xb) < 1e-6 or abs(za - zb) < 1e-6:
        return [a, b]
    return [a, (xa, y, zb), b]


def _standby(items, ref, duty):
    """기준점에서 먼 순서로 예비기 지정 → [True=운전]"""
    order = sorted(range(len(items)), key=lambda i: (abs(items[i][1] - ref), i))
    on = set(order[:duty])
    return [i in on for i in range(len(items))]


def plant_network(sc, cfg, hs, nets, plant_net, engine_res, units):
    """건물 좌표 배관망 + 양정. hs = multizone.halls(), nets = 전산실별 networks() 결과, plant_net = 건물 전체 유량 networks()"""
    pc, pp = cfg["piping"], cfg["plant"]
    net = _Net(pc["max_velocity_m_s"], pc["fitting_factor"])
    lay = plant_layout(sc, cfg, units)
    fl = {f["floor"]: f for f in floors(sc)}
    fx0, fz0, fx1, fz1 = bbox(footprint(sc))
    cw, chh, cd = pp["chiller_size_m"]
    tw, th, td = pp["tower_size_m"]
    u = units or {}

    # 전산실 인입점 (존 좌표 → 건물 좌표)
    halls = []
    for (z, _), n in zip(hs, nets):
        g, e = z["geometry"], fl[z["floor"]]["elevation_m"]
        br = n["chw"]["branch"]
        mv = lambda p, g=g, e=e: (p[0] + (g.get("x_m") or 0.0), p[1] + e, p[2] + (g.get("z_m") or 0.0))
        halls.append({"z": z, "q": n["chw_q_design"], "inlet": mv(br["inlet"]), "junction": mv(br["junction"]),
                      "br": br, "mv": mv, "segs": n["chw"]["segments"][2:]})
    Q = sum(h["q"] for h in halls)
    x_r = min(max(h["inlet"][0] for h in halls) + RISER_OFFSET_M, fx1 - 1.0)
    low = min(halls, key=lambda h: (h["z"]["floor"], h["inlet"][1]))
    z_r = low["inlet"][2]
    ret_chw = lambda p: (p[0] + (RISER_RET_DX if abs(p[0] - x_r) < 1e-6 else 0.0), p[1] + CHW_RET_DY, p[2])

    # 냉수: 칠러 → 야드 헤더 → 건물 인입
    chs = lay["chiller"]
    duty_c = min((u.get("chiller") or {}).get("duty") or len(chs), len(chs))
    run_c = _standby(chs, z_r, duty_c)
    q_c = Q / duty_c
    x_h = min(x for x, _ in chs) - cw / 2 - HEADER_GAP_M
    stub = []
    for (x, zz), on in zip(chs, run_c):
        stub.append(net.add("chw", "chiller_link", [(x - cw / 2, YARD_Y, zz), (x_h, YARD_Y, zz)], q_c if on else 0.0,
                            ret_chw, q_size=q_c))
    hdr = _header(net, "chw", "yard_header", x_h, YARD_Y, [(zz, q_c if on else 0.0) for (_, zz), on in zip(chs, run_c)]
                  + [(z_r, -Q)], ret_chw)
    yard = max(((dp + _along(hdr, zz, z_r)[0], L + _along(hdr, zz, z_r)[1])
                for (dp, L), (_, zz), on in zip(stub, chs, run_c) if on), key=lambda t: t[0])
    feed = net.add("chw", "feed", [(x_h, YARD_Y, z_r), (x_r, YARD_Y, z_r)], Q, ret_chw)

    # 입상관 (층 분기 높이마다 끊음) → 층 분기 → 전산실
    ys = sorted({round(h["inlet"][1], 6) for h in halls})
    riser, y0 = {}, YARD_Y
    acc_dp = acc_L = 0.0
    for y in ys:
        q_up = sum(h["q"] for h in halls if round(h["inlet"][1], 6) >= y)
        dp, L = net.add("chw", "riser", [(x_r, y0, z_r), (x_r, y, z_r)], q_up, ret_chw)
        acc_dp, acc_L, y0 = acc_dp + dp, acc_L + L, y
        riser[y] = (acc_dp, acc_L)
    paths = []
    for h in halls:
        z, y = h["z"], h["inlet"][1]
        tap = net.add("chw", "floor_branch", _l_path((x_r, y, z_r), h["inlet"]), h["q"], ret_chw, zone=z["id"],
                      floor=z["floor"])
        ins = net.add("chw", "hall", [h["inlet"], h["junction"]], h["q"], lambda p: (p[0], p[1] + CHW_RET_DY, p[2]),
                      q_size=h["q"], zone=z["id"], floor=z["floor"])
        for s in h["segs"]:                                   # 기존 전산실 분기 (트렁크 제외)를 건물 좌표로
            pts = [list(h["mv"](p)) for p in s["points"]]
            net.segs.append({**s, "points": [[round(c, 2) for c in p] for p in (pts if s["kind"] == "chw_supply"
                                                                                  else pts[::-1])],
                             "part": "hall", "zone": z["id"], "floor": z["floor"]})
        r_dp, r_L = riser[round(y, 6)]
        dp = yard[0] + feed[0] + r_dp + tap[0] + ins[0] + h["br"]["dp_kpa"]
        L = yard[1] + feed[1] + r_L + tap[1] + ins[1] + h["br"]["length_m"]
        paths.append((dp, L, r_dp, z["id"]))
    worst = max(paths, key=lambda t: t[0])
    head = 2 * worst[0] + pc["evaporator_dp_kpa"] + pc["coil_dp_kpa"]

    # 냉각수: 냉각탑 → 헤더 → 칠러 응축기
    q_cw = plant_net["cw_q_design"]
    tws = lay["tower"]
    duty_t = min((u.get("tower") or {}).get("duty") or len(tws), len(tws))
    z_mid = sum(zz for _, zz in chs) / len(chs)
    run_t = _standby(tws, z_mid, duty_t)
    qt, qc = q_cw / duty_t, q_cw / duty_c
    x_cw = max(x for x, _ in chs) + cw / 2 + HEADER_GAP_M
    ret_cw = lambda p: (p[0], p[1] + CW_RET_DY, p[2])
    t_stub = [net.add("cw", "tower_link", [(x - tw / 2, CW_Y, zz), (x_cw, CW_Y, zz)], qt if on else 0.0, ret_cw, q_size=qt)
              for (x, zz), on in zip(tws, run_t)]
    c_stub = [net.add("cw", "chiller_link", [(x_cw, CW_Y, zz), (x + cw / 2, CW_Y, zz)], qc if on else 0.0, ret_cw,
                      q_size=qc) for (x, zz), on in zip(chs, run_c)]
    cw_hdr = _header(net, "cw", "cw_header", x_cw, CW_Y,
                     [(zz, qt if on else 0.0) for (_, zz), on in zip(tws, run_t)]
                     + [(zz, -qc if on else 0.0) for (_, zz), on in zip(chs, run_c)], ret_cw)
    cw_worst = max(ts[0] + _along(cw_hdr, tz, cz)[0] + cs[0]
                   for ts, (_, tz), ton in zip(t_stub, tws, run_t) if ton
                   for cs, (_, cz), con in zip(c_stub, chs, run_c) if con)
    cw_dyn = 2 * cw_worst + pc["condenser_dp_kpa"]

    # FWS (액체 루프): 야드 → 입상관 → 층별 CDU
    fws_q = max(((x["plant"].get("liquid_loop") or {}).get("fws_flow_m3_h") or 0.0) for x in engine_res["hourly"]) / 3600
    cdus = lay["cdu"]
    if fws_q > 0 and cdus:
        z_f = z_r + FWS_RISER_DZ
        q1 = fws_q / len(cdus)
        ret_f = lambda p: (p[0] + (FWS_RET_DX if abs(p[0] - x_r) < 1e-6 else 0.0), p[1] + FWS_RET_DY, p[2])
        net.add("fws", "fws_feed", [(x_h, FWS_Y, z_f), (x_r, FWS_Y, z_f)], fws_q, ret_f)
        by_f = {}
        for c in cdus:
            by_f.setdefault(c[0], []).append(c)
        y0 = FWS_Y
        for f in sorted(by_f):
            y = fl[f]["elevation_m"] + CDU_SIZE[1] + 0.6
            q_up = q1 * sum(len(v) for k, v in by_f.items() if k >= f)
            net.add("fws", "fws_riser", [(x_r, y0, z_f), (x_r, y, z_f)], q_up, ret_f)
            y0 = y
            left, cur = list(by_f[f]), (x_r, y, z_f)
            while left:                       # 가까운 CDU 부터 직렬 분기 (radial 과 같은 규칙)
                nx = min(left, key=lambda c: (abs(c[2] - cur[0]) + abs(c[3] - cur[2]), c[2], c[3]))
                tgt = (nx[2], y, nx[3])
                net.add("fws", "fws_floor", _l_path(cur, tgt), q1 * len(left), ret_f, floor=f, zone=nx[1])
                y_top = fl[f]["elevation_m"] + CDU_SIZE[1]
                net.add("fws", "cdu_link", [tgt, (nx[2], y_top, nx[3])], q1, ret_f, floor=f, zone=nx[1],
                        ret_pts=[ret_f(tgt), (nx[2] + 0.4, y + FWS_RET_DY, nx[3]), (nx[2] + 0.4, y_top, nx[3])])
                left.remove(nx)
                cur = tgt

    box = lambda x, z, size, base=0.0: {"pos": [round(x, 2), round(base + size[1] / 2, 2), round(z, 2)],
                                        "size": [round(v, 2) for v in size]}
    plant = {
        "chiller": [{**box(x, zz, (cw, chh, cd)), "standby": not on} for (x, zz), on in zip(chs, run_c)],
        "tower": [{**box(x, zz, (tw, th, td)), "standby": not on} for (x, zz), on in zip(tws, run_t)],
        "cdu": [{"floor": f, "zone": zid, **box(x, zz, CDU_SIZE, fl[f]["elevation_m"] if f in fl else 0.0)}
                for f, zid, x, zz in cdus],
        "source": lay["source"],
    }
    top = max(ys)
    return {
        "segments": net.segs, "plant": plant, "notes": lay["notes"],
        "chw_head_kpa": round(head, 1), "chw_friction_kpa": round(2 * worst[0], 1), "chw_critical_m": round(worst[1], 1),
        "critical_zone": worst[3], "riser_friction_kpa": round(2 * worst[2], 2),
        "cw_yard_dynamic_kpa": round(cw_dyn, 1),
        "chw_design_flow_m3_h": round(Q * 3600, 1), "cw_design_flow_m3_h": round(q_cw * 3600, 1),
        "fws_design_flow_m3_h": round(fws_q * 3600, 1) if fws_q > 0 and cdus else None,
        "inlets": [{"zone": h["z"]["id"], "floor": h["z"]["floor"], "point": [round(c, 2) for c in h["inlet"]]}
                   for h in halls],
        "riser": {"x_m": round(x_r, 2), "z_m": round(z_r, 2), "base_m": YARD_Y, "top_m": round(top, 2)},
    }


# ---------- 3D 표시 데이터 ----------
def view(sc, cfg, hs, hourly_af, ph, bn, analyze):
    """evaluate() 응답의 building_view. hourly_af = 시간별 합산 기류 해석 (랙은 전산실 순서로 이어 붙임)"""
    fl = floors(sc)
    elev = {f["floor"]: f["elevation_m"] for f in fl}
    counts = [zm["metrics"]["rack_count"] for zm in hourly_af[ph]["zones"]]
    halls, k = [], 0
    for (z, s), n in zip(hs, counts):
        g, e = z["geometry"], elev[z["floor"]]
        gx, gz = g.get("x_m") or 0.0, g.get("z_m") or 0.0
        racks = []
        for r in hourly_af[ph]["racks"][k:k + n]:
            rid = r["id"] if r["id"] and not r["id"].startswith(f"{z['id']}:") \
                else f"F{z['floor'] + 1}-R{r['row'] + 1:02d}-{r['col'] + 1:03d}"
            racks.append({"id": rid, "row": r["row"], "col": r["col"], "x": round(r["x_m"] + gx, 3), "y": e,
                          "z": round(r["z_m"] + gz, 3), "kw": r["kw"], "inlet_c": r["inlet_c"]})
        af = analyze(s, s["cooling_layout"], ph)
        halls.append({"zone": z["id"], "floor": z["floor"], "racks": racks,
                      "rack_inlet_c": [[r["inlet_c"] for r in a["racks"][k:k + n]] for a in hourly_af],
                      "crah": [{"x": round(c["x_m"] + gx, 3), "y": e, "z": round(c["z_m"] + gz, 3),
                                "airflow": c["airflow_m3_s"]} for c in af["crah"]]})
        k += n
    notes = ["건물 좌표: 원점 = 건물 중심, 층 바닥 높이 = 층고 누적. 랙·CRAH 는 전산실 존 좌표 + 존 위치 + 층 높이",
             "칠러·냉각탑·CDU 배치, 헤더·입상관 경로는 표시·배관 길이용 가정 (도면 위치가 있으면 그것)",
             "냉수 양정 = 2 x (야드 + 입상관 + 층 분기 + 전산실 분기 마찰) + 기기 손실, 입상관 정수두 0 (폐회로)",
             *bn["notes"]]
    if bn["fws_design_flow_m3_h"]:
        notes.append("FWS 배관은 표시용 — 액체 루프 펌프 전력은 엔진 계수(liquid_pump_kw_per_kw) 그대로")
    return {
        "floors": fl,
        "footprint": [[round(x, 3), round(z, 3)] for x, z in footprint(sc)],
        "zones": [{"id": z["id"], "name": z["name"], "kind": z["kind"], "floor": z["floor"],
                   "x_m": z["geometry"].get("x_m") or 0.0, "z_m": z["geometry"].get("z_m") or 0.0,
                   "ew_m": z["geometry"]["ew_m"], "ns_m": z["geometry"]["ns_m"], "height_m": z["geometry"]["height_m"],
                   "elevation_m": elev[z["floor"]]} for z in sc["zones"]],
        "halls": halls,
        "plant": bn["plant"],
        "piping": {k: bn[k] for k in ("segments", "chw_head_kpa", "chw_friction_kpa", "chw_critical_m", "critical_zone",
                                      "riser_friction_kpa", "cw_yard_dynamic_kpa", "chw_design_flow_m3_h",
                                      "cw_design_flow_m3_h", "fws_design_flow_m3_h", "inlets", "riser")},
        "notes": notes,
    }
