"""
CAD(DXF) 다중 층·다중 존 → 다중 존 시나리오 (Phase 18 연장). cad.build 가 역할 있는 층이 둘 이상이면 여기로 넘긴다.

층: 레이어 이름 접두어 (cad.floor_of: F2-, 2F-, L2-, 2층 → floor 1). 접두어 없는 레이어(SITE-, ROOF- 등)는 공통
    - 옥외 칠러·냉각탑은 기존 규칙 그대로 (cad.plant_layout)
    - 층에 외곽선이 없으면 공통 외곽선을 쓴다
존: '존 경계' 역할 레이어의 닫힌 폴리선. 경계 안 문자 "id | kind" (kind: data_hall/support/office) 를 읽는다
    - 문자가 없으면 자동 id(F2_Z1)와 종류 추정: 랙이 있으면 전산실, 외벽에 창이 있으면 사무, 그 외 지원
    - 존 경계가 없는 층은 층 외곽선 = 존 하나
블록 역할은 그 블록이 놓인 레이어의 층 안에서만 적용 (cad.block_role, '블록@F2'). 랙·CRAH·창도 그 층의 존에만 배정

build_zones() 계산:
- geometry: 경계의 외접 사각형 (3D 좌표계, 원점 = 건물 중심), height = 그 층 층고
- 외기 접촉면: 존 경계 변 중 층 외곽선과 겹치는 길이 x 층고 → 방위별 벽 (진북 기준, 단일 층과 같은 방위 규칙)
  창은 그 변에서 3 m 안의 창 선: 폭 x 창 높이
- 지붕: 바로 위층 존과 겹치지 않는 면적 (최상층 = 존 면적). 1층 바닥은 단열 (엔진과 같음)
- partitions: 같은 층 맞닿은 변 길이 x 층고 = wall, 바로 위층 존과 평면이 겹친 면적 = slab. 쌍마다 한 번 (앞 존·아래층 쪽)
- IT: 전산실 존 안 랙 블록의 KW 속성 합 (속성 없는 랙 = 랙 수 x 랙당 kW 입력값). 합 = it_load.design_kw
- 랙 items 는 존 좌표계 (원점 = 존 중심), CRAH 는 존별 crah_positions. 운전점·설비(cooling_layout 공통)는 기존 값
- 도면에 없는 값 (실내 설정·조명·인원·외기량·기타 발열·냉각 방식)은 같은 id 의 기존 존 값을 이어받는다.
  새 존은 인원·외기량을 건물 값의 면적 비례로 나눈다 (가정값, 보고서에 기록)
"""
import copy
import math
import re
from collections import Counter, defaultdict

from . import cad
from .schemas import CRAH_PER_SIDE_MAX

FLOOR_HEIGHT_M = 6.0           # 층고 기본값 [m] (화면에서 층별 입력)
SLAB_U, WALL_U = 2.0, 1.5      # 층간 슬래브·내벽 열관류율 [W/m2.K] (가정값, 해남 다중 존 예시와 같음)
EDGE_TOL_M = 1.0               # 존 경계 변이 외곽선·이웃 존 변과 '맞닿았다'고 보는 거리 (벽 두께 포함)
ANG_TOL_DEG = 2.0
RACK_KW_FALLBACK = 10.0        # 랙 kW 속성·입력값·기존 밀도가 모두 없을 때 (가정값, 경고)
LABEL_RE = re.compile(r"^\s*([A-Za-z0-9_\-]{1,32})\s*[|｜]\s*([A-Za-z_가-힣]+)\s*$")
KIND_WORDS = {"data_hall": "data_hall", "datahall": "data_hall", "hall": "data_hall", "전산실": "data_hall",
              "support": "support", "지원": "support", "지원실": "support", "전기실": "support", "기계실": "support",
              "office": "office", "사무": "office", "사무실": "office"}
KIND_LABEL = {"data_hall": "전산실", "support": "지원실", "office": "사무실"}
INHERIT = ("indoor", "lighting_w_m2", "people", "misc_kw", "outdoor_air_m3_h", "architecture", "liquid_fraction")
DEFAULT_ROOF = {"u_value": 0.25, "absorptance": 0.7}


# ---------- 기하 ----------
def _area(poly):
    return abs(cad._signed_area(poly))


def _ccw(poly):
    return poly if cad._signed_area(poly) >= 0 else poly[::-1]


def _edges(poly):
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def _inside(pt, poly):
    x, y = pt
    c = False
    for (x1, y1), (x2, y2) in _edges(poly):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _dist(pt, poly):
    return 0.0 if _inside(pt, poly) else min(cad._seg_dist(pt, a, b) for a, b in _edges(poly))


def _convex(poly):
    cr = [(b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
          for a, b, c in zip(poly, poly[1:] + poly[:1], poly[2:] + poly[:2])]
    return all(v >= -1e-9 for v in cr)


def _clip(subj, clip):
    """Sutherland-Hodgman: subj ∩ clip (clip 은 반시계 볼록 다각형)"""
    out = list(subj)
    for a, b in _edges(clip):
        inp, out = out, []
        if not inp:
            break
        side = lambda p: (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        for p, q in _edges(inp):
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def overlap_area(a, b):
    """두 다각형(반시계)의 겹침 면적, 근사 여부 (둘 다 오목하면 외접 사각형끼리의 겹침)"""
    for subj, clip in ((a, b), (b, a)):
        if _convex(clip):
            c = _clip(subj, clip)
            return (_area(c) if len(c) >= 3 else 0.0), False
    bx = lambda p: (min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p))
    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = bx(a), bx(b)
    return max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0)), True


def shared(p, q, a, b, tol=EDGE_TOL_M):
    """선분 pq 와 ab 가 나란하고 tol 안으로 붙어 있으면 겹친 길이와 겹친 구간 (pq 위). 아니면 (0, None)"""
    L = math.dist(p, q)
    if L < 1e-6 or math.dist(a, b) < 1e-6:
        return 0.0, None
    if cad._ang_diff(cad._ang180(q[0] - p[0], q[1] - p[1]), cad._ang180(b[0] - a[0], b[1] - a[1])) > ANG_TOL_DEG:
        return 0.0, None
    ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
    off = lambda r: abs((r[0] - p[0]) * uy - (r[1] - p[1]) * ux)
    if max(off(a), off(b)) > tol:
        return 0.0, None
    ta, tb = (a[0] - p[0]) * ux + (a[1] - p[1]) * uy, (b[0] - p[0]) * ux + (b[1] - p[1]) * uy
    lo, hi = max(0.0, min(ta, tb)), min(L, max(ta, tb))
    if hi - lo <= 0.01:
        return 0.0, None
    return hi - lo, ((p[0] + ux * lo, p[1] + uy * lo), (p[0] + ux * hi, p[1] + uy * hi))


# ---------- 도우미 ----------
def _bbox(pts):
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(zs), max(xs), max(zs)


def _base_rack_kw(sc):
    """기존 시나리오의 랙당 설계 kW (IT 설계부하 / 랙 수). 모르면 None"""
    def n_of(r):
        return len(r["items"]) if r.get("items") else r.get("rows", 0) * r.get("per_row", 0)
    racks = [z["racks"] for z in sc.get("zones") or [] if z.get("racks")]
    if not racks and (sc.get("cooling_layout") or {}).get("racks"):
        racks = [sc["cooling_layout"]["racks"]]
    n = sum(n_of(r) for r in racks)
    kw = (sc.get("it_load") or {}).get("design_kw")
    return kw / n if n and kw else None


def _env_props(env):
    return env or {"walls": [], "windows": [], "roof": dict(DEFAULT_ROOF)}


def _fmt_o(c):
    return " / ".join(f"{o} {c[o]:.1f}" for o in "NESW" if c[o] > 0) or "없음"


# ---------- 본체 ----------
def build_zones(parsed, shapes, s, notes, warns, sc, layout, *, north_deg=0.0, window_height_m=1.5,
                floor_heights_m=None, rack_kw=None, zone_overrides=None):
    """다중 층 도면 → zones·건물 합계 외피·IT·설비 위치. 반환 dict 는 cad.apply_to_scenario 가 반영"""
    sc = sc or {}
    fh = {int(k): float(v) for k, v in (floor_heights_m or {}).items()}
    over = zone_overrides or {}
    floors = sorted({sh["floor"] for sh in shapes if sh["role"] != "ignore"} - {None})
    H = {f: fh.get(f, FLOOR_HEIGHT_M) for f in floors}
    lab = {f: cad.floor_label(f) for f in floors}
    by = defaultdict(list)
    for sh in shapes:
        by[(sh["role"], sh["floor"])].append(sh)
    on = lambda role, f: by[(role, f)]
    notes.append(f"층 {len(floors)}개 ({', '.join(lab.values())}): 레이어 이름 접두어로 인식, 층고 "
                 + ", ".join(f"{lab[f]} {H[f]:g} m" for f in floors)
                 + (" (입력값)" if fh else f" (기본값 {FLOOR_HEIGHT_M:g} m)") + ". 접두어 없는 레이어는 공통")
    loose = sum(len(on(r, None)) for r in ("rack", "crah", "zone", "window", "slot"))
    if loose:
        warns.append(f"층 접두어가 없는 레이어의 랙·CRAH·존 경계·창 {loose}개는 어느 층인지 몰라 제외")
    if any(sh.get("arc") for sh in shapes if sh["role"] in ("outline", "wall", "zone")):
        notes.append("외곽선·벽·존 경계의 호(bulge)는 직선으로 근사")

    # 1) 층 외곽선
    fps, fp_src = {}, {}
    for f in floors:
        for role, scope in (("outline", f), ("outline", None), ("wall", f), ("wall", None)):
            cand = [p for p in cad._closed(on(role, scope)) if len(p) >= 3]
            if cand:
                fps[f] = max(cand, key=_area)
                fp_src[f] = f"{role} 레이어 ({'공통' if scope is None else lab[f]})의 가장 큰 닫힌 폴리선"
                break
        else:
            zs = [p for p in cad._closed(on("zone", f)) if len(p) >= 3]
            if not zs:
                raise cad.CadError(f"{lab[f]}: 외곽선(outline)·외벽(wall)·존 경계 중 닫힌 폴리선이 하나도 없습니다")
            x0, y0, x1, y1 = _bbox([p for z in zs for p in z])
            fps[f] = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            fp_src[f] = "존 경계들의 외접 사각형 (가정)"
            warns.append(f"{lab[f]}: 외곽선이 없어 존 경계들의 외접 사각형을 층 외곽으로 사용 (가정)")
        fps[f] = _ccw(fps[f])
        if _area(fps[f]) < 20:
            raise cad.CadError(f"{lab[f]} 외곽선 면적이 {_area(fps[f]):.1f} m² 로 너무 작습니다. 단위(mm/m)를 확인해 주세요")

    # 2) 랙 → 열 방향, 좌표계 (모든 층 공통)
    racks = {f: cad.rack_shapes(on("rack", f), warns) for f in floors}
    theta = cad.row_angle([r for f in floors for r in racks[f]], fps[floors[0]])
    if theta:
        notes.append(f"랙 열이 동서(x) 방향이 되도록 3D·기류 좌표계를 도면 대비 {theta:.1f}° 회전 (외피 방위는 회전 전 진북 기준)")
    scene, ew, ns = cad.frame(theta, list(fps.values()))
    for f in floors:
        bad = [r for r in racks[f] if r["ang"] is not None and cad._ang_diff(r["ang"], theta) > 20]
        if bad:
            warns.append(f"{lab[f]}: 주 열 방향과 20° 넘게 어긋난 랙 {len(bad)}개 제외 (직교 열은 아직 미지원)")
            racks[f] = [r for r in racks[f] if r not in bad]

    # 3) 존 경계 → 존 후보 (층별), 랙·CRAH·문자 배정
    texts = defaultdict(list)
    for t in parsed["texts"]:
        f = cad.floor_of(t["layer"])
        if f in H:
            texts[f].append({"pos": (t["pos"][0] * s, t["pos"][1] * s), "text": t["text"], "h": t.get("h", 0) * s})
    raw = []
    for f in floors:
        polys, seen = [], set()
        for p in cad._closed(on("zone", f)):
            if len(p) < 3 or _area(p) < 1.0:
                continue
            key = (round(_area(p)), *(round(v) for v in cad._center(p)))
            if key not in seen:
                seen.add(key)
                polys.append(_ccw(p))
        src = "존 경계 폴리선"
        if not polys:
            polys, src = [fps[f]], "층 외곽선 (존 경계 없음 → 층 = 존 하나)"
        zs = []
        for poly in polys:
            sp = [scene(p) for p in poly]
            x0, z0, x1, z1 = _bbox(sp)
            zs.append({"floor": f, "poly": poly, "scene": sp, "src": src, "racks": [], "crah": [], "win": Counter(),
                       "ext": Counter(), "segs": [], "ew": x1 - x0, "ns": z1 - z0, "cx": (x0 + x1) / 2,
                       "cz": (z0 + z1) / 2})
        zs.sort(key=lambda z: (round(z["cx"], 1), round(z["cz"], 1)))
        for i, z in enumerate(zs):
            z["n"] = i + 1
        # 랙·CRAH: 중심이 든 존 (없으면 가까운 존, 랙 1 m·CRAH 3 m 안)
        for what, items, tol in (("racks", racks[f], 1.0), ("crah", [sh for sh in on("crah", f) if sh["pts"]], 3.0)):
            lost = 0
            for it in items:
                c = it["c"] if what == "racks" else cad._center(it["pts"])
                d, zi = min(((_dist(c, z["poly"]), i) for i, z in enumerate(zs)), default=(math.inf, None))
                if d <= tol:
                    zs[zi][what].append(it if what == "racks" else c)
                else:
                    lost += 1
            if lost:
                warns.append(f"{lab[f]}: 어느 존에도 들지 않는 {'랙' if what == 'racks' else 'CRAH'} {lost}개는 제외")
        # 외기 접촉 변 (층 외곽선과 겹치는 부분)
        fe = [(a, b, cad.edge_orientation(a, b, north_deg)) for a, b in _edges(fps[f]) if math.dist(a, b) >= 0.05]
        for z in zs:
            for p, q in _edges(z["poly"]):
                for a, b, o in fe:
                    L, seg = shared(p, q, a, b)
                    if L:
                        z["ext"][o] += L
                        z["segs"].append((seg, o))
        # 창: 가장 가까운 외기 접촉 변의 존·방위
        far = 0
        for sh in on("window", f):
            w = cad.window_of(sh)
            if w is None:
                continue
            c, width = w
            d, zi, o = min(((cad._seg_dist(c, *seg), i, o) for i, z in enumerate(zs) for seg, o in z["segs"]),
                           default=(math.inf, None, None))
            if d > cad.WINDOW_MAX_DIST_M:
                far += 1
                continue
            zs[zi]["win"][o] += width * window_height_m
        if far:
            warns.append(f"{lab[f]}: 존 외벽에서 {cad.WINDOW_MAX_DIST_M} m 넘게 떨어진 창 {far}개는 내부 창으로 보고 제외")
        # 라벨 "id | kind" 와 이름 문자
        for z in zs:
            inside = [t for t in texts[f] if _inside(t["pos"], z["poly"])]
            lbls = [(t, m) for t in inside if (m := LABEL_RE.match(t["text"])) and m.group(2).lower() in KIND_WORDS]
            if len(lbls) > 1:
                warns.append(f"{lab[f]}: 존 하나에 라벨 {len(lbls)}개 ({', '.join(m.group(1) for _, m in lbls)}) — "
                             "존 경계가 빠졌을 수 있어 라벨 대신 자동 id 사용")
            z["label"] = ((lbls[0][1].group(1), KIND_WORDS[lbls[0][1].group(2).lower()], lbls[0][0]["pos"])
                          if len(lbls) == 1 else None)
            rc = [r["c"] for r in z["racks"]]
            names = [t for t in inside if not LABEL_RE.match(t["text"]) and len(t["text"]) <= 40
                     and re.search(r"[A-Za-z가-힣]", t["text"])
                     and not any(abs(t["pos"][0] - x) < 1 and abs(t["pos"][1] - y) < 1 for x, y in rc)]
            if z["label"]:
                lp = z["label"][2]
                names = [t for t in names if math.dist(t["pos"], lp) <= 8]
                z["name_txt"] = min(names, key=lambda t: math.dist(t["pos"], lp))["text"] if names else None
            else:
                z["name_txt"] = max(names, key=lambda t: t["h"])["text"] if names else None
        raw += zs

    # 4) id·종류·이름 (라벨 > 추정), 사용자 수정값
    prev = {z["id"]: z for z in sc.get("zones") or []}
    ids = Counter()
    for z in raw:
        if z["label"]:
            zid, kind, src = z["label"][0], z["label"][1], "도면 문자"
        else:
            zid = f"F{z['floor'] + 1}_Z{z['n']}"
            kind = "data_hall" if z["racks"] else "office" if sum(z["win"].values()) > 0 else "support"
            src = "자동 (" + ("랙 있음 → 전산실" if z["racks"] else "외벽 창 → 사무" if kind == "office" else "랙·창 없음 → 지원실") + ", 추정)"
        ids[zid] += 1
        if ids[zid] > 1:
            warns.append(f"존 id '{zid}' 가 겹쳐 '{zid}_{ids[zid]}' 로 바꿈")
            zid = f"{zid}_{ids[zid]}"
        o = over.get(zid) or {}
        name = o.get("name") or z["name_txt"] or (prev[zid]["name"] if zid in prev else None) \
            or f"{z['floor'] + 1}층 {KIND_LABEL[kind]} {z['n']}"
        z.update(id=zid, kind=o.get("kind") or kind, kind_src="사용자 수정" if o.get("kind") else src,
                 name=name[:40], over=o)

    # 5) 존 사이 경계 (같은 층 내벽, 바로 위층 슬래브), 지붕
    prev_pu = {}
    for z in prev.values():
        for p in z.get("partitions") or []:
            prev_pu[(frozenset((z["id"], p["zone"])), p.get("kind", "wall"))] = p["u_value"]
    parts, approx = [], 0
    for i, a in enumerate(raw):
        for b in raw[i + 1:]:
            if a["floor"] == b["floor"]:
                L = sum(shared(p, q, c, d)[0] for p, q in _edges(a["poly"]) for c, d in _edges(b["poly"]))
                area, kind = L * H[a["floor"]], "wall"
            elif floors.index(b["floor"]) - floors.index(a["floor"]) in (1, -1):
                area, ap = overlap_area(a["poly"], b["poly"])
                approx += ap and area > 0
                kind = "slab"
            else:
                continue
            if area < 0.5:
                continue
            lo, hi = (a, b) if a["floor"] <= b["floor"] else (b, a)
            u = prev_pu.get((frozenset((a["id"], b["id"])), kind))
            parts.append({"a": lo, "b": hi, "kind": kind, "area_m2": round(area, 1),
                          "u_value": u if u is not None else SLAB_U if kind == "slab" else WALL_U,
                          "u_src": "기존 시나리오" if u is not None else "가정값"})
    if approx:
        warns.append(f"오목한 존끼리의 층간 겹침 {approx}쌍은 외접 사각형으로 근사")
    for z in raw:
        k = floors.index(z["floor"])
        up = [u for u in raw if k + 1 < len(floors) and u["floor"] == floors[k + 1]]
        covered = sum(overlap_area(z["poly"], u["poly"])[0] for u in up)
        z["area"] = _area(z["poly"])
        z["roof"] = max(z["area"] - covered, 0.0) if z["area"] - covered >= 0.5 else 0.0

    # 6) 존 → 시나리오 zones
    rk_in = rack_kw
    base_kw = _base_rack_kw(sc)
    per_kw, per_src = ((rk_in, "입력값") if rk_in else (base_kw, "기존 시나리오 IT 설계부하 / 랙 수 (가정)") if base_kw
                       else (RACK_KW_FALLBACK, f"기본 {RACK_KW_FALLBACK:g} kW (가정값)"))
    total_area = sum(z["area"] for z in raw)
    vent = (sc.get("ventilation") or {}).get("outdoor_air_m3_h", 0.0)
    people = (sc.get("internal") or {}).get("people", 0.0)
    sc_env = _env_props(sc.get("envelope"))
    id_texts = {f: [(t["pos"], t["text"]) for t in texts[f] if cad._id_like(t["text"])] for f in floors}
    zones, rep, prev_rows = [], [], {}
    used_missing = False
    for z in raw:
        f, zid, kind, o, pz = z["floor"], z["id"], z["kind"], z["over"], prev.get(z["id"])
        zn = []
        env0 = _env_props(pz["envelope"]) if pz else sc_env
        gross = Counter({k: v * H[f] for k, v in z["ext"].items()})
        walls, windows = cad.envelope_lists(gross, z["win"], env0, warns, who=f"{zid}: ")
        roof_p = {k: env0["roof"][k] for k in ("u_value", "absorptance") if k in env0["roof"]} or dict(DEFAULT_ROOF)
        zone = {"id": zid, "name": z["name"], "floor": f, "kind": kind,
                "geometry": {"ew_m": round(z["ew"], 2), "ns_m": round(z["ns"], 2), "height_m": H[f],
                             "x_m": round(z["cx"], 2), "z_m": round(z["cz"], 2)},
                "envelope": {"walls": walls, "roof": {**roof_p, "area_m2": round(z["roof"], 1)}, "windows": windows},
                "floor_area_m2": round(z["area"], 1), "partitions": []}
        zn.append(f"경계: {z['src']}, 종류: {z['kind_src']}")
        zn.append(f"면적 {z['area']:,.0f} m², 외벽 길이 {_fmt_o(z['ext'])} m"
                  + (f", 창 {_fmt_o(z['win'])} m²" if z["win"] else "")
                  + (f", 지붕 {z['roof']:,.0f} m²" if z["roof"] else ""))
        # 랙·CRAH (전산실만)
        n_rack, n_crah, it_kw, it_src = 0, 0, 0.0, "–"
        if kind == "data_hall" and z["racks"]:
            zscene = lambda p, z=z: (round(scene(p)[0] - z["cx"], 3), round(scene(p)[1] - z["cz"], 3))
            rg = (pz or {}).get("racks") or {}
            items, info, note = cad.rack_layout(z["racks"], zscene, id_texts[f], rg)
            zone["racks"] = {**info, "items": items}
            n_rack = len(items)
            kws = [it.get("design_kw") for it in items]
            miss = sum(k is None for k in kws)
            known = sum(k for k in kws if k is not None)
            it_kw = known + miss * per_kw
            it_src = (f"랙 KW 속성 합 ({n_rack - miss}개)" if not miss
                      else f"랙 KW 속성 {n_rack - miss}개 + 속성 없는 랙 {miss}개 x {per_kw:g} kW ({per_src})")
            used_missing |= miss > 0
            zn.append(note)
            if z["crah"]:
                zone["crah_positions"] = [[round(scene(c)[0] - z["cx"], 3), round(scene(c)[1] - z["cz"], 3)]
                                          for c in z["crah"]]
                n_crah = len(z["crah"])
        elif kind == "data_hall":
            it_kw = (pz or {}).get("it_design_kw") or 0.0
            it_src = "랙 없음 → 기존 존 값" if pz else "랙 없음 → 0"
            warns.append(f"{zid}: 전산실인데 랙이 없습니다 (IT {it_kw:g} kW, 기본 랙 격자)")
        elif z["racks"]:
            warns.append(f"{zid}: 전산실이 아니어서 랙 {len(z['racks'])}개를 무시")
        if o.get("it_design_kw") is not None:
            it_kw, it_src = float(o["it_design_kw"]), "사용자 입력"
        zone["it_design_kw"] = round(it_kw, 3)
        if kind != "data_hall" and it_kw:
            zn.append(f"IT {it_kw:g} kW (전산실이 아닌 존 — 랙·기류 모델 없이 부하만)")
        # 도면에 없는 값
        if pz:
            for k in INHERIT:
                if pz.get(k) is not None:
                    zone[k] = copy.deepcopy(pz[k])
            zn.append(f"실내 설정·조명·인원·외기량·기타 발열·냉각 방식: 기존 존 {zid} 값 유지")
        else:
            share = z["area"] / total_area if total_area else 0.0
            zone["people"] = round(people * share, 2)
            zone["outdoor_air_m3_h"] = round(vent * share, 1)
            zn.append(f"가정값: 인원·외기량 = 건물 값 x 면적 비율 {share:.1%}, 실내 설정·조명 = 건물 값, 기타 발열 0"
                      + (" (전기·UPS실이면 장비 발열을 입력해 주세요)" if kind == "support" else ""))
        zones.append(zone)
        rep.append({"id": zid, "name": zone["name"], "floor": f, "floor_label": lab[f], "kind": kind,
                    "kind_source": z["kind_src"], "boundary": z["src"], "area_m2": round(z["area"], 1),
                    "it_kw": zone["it_design_kw"], "it_source": it_src, "racks": n_rack, "crah": n_crah,
                    "ext_wall_m": {k: round(v, 1) for k, v in z["ext"].items()},
                    "walls": {w["orientation"]: w["area_m2"] for w in walls},
                    "windows": {w["orientation"]: w["area_m2"] for w in windows}, "roof_m2": round(z["roof"], 1),
                    "inherited": bool(pz), "notes": zn})
        prev_rows[zid] = zone
    if used_missing and not rk_in:
        warns.append(f"KW 속성이 없는 랙은 랙당 {per_kw:g} kW ({per_src}) — 화면에서 랙당 kW 를 입력할 수 있습니다")
    if not any(z["kind"] == "data_hall" for z in zones):
        raise cad.CadError("전산실(data_hall) 존이 없습니다. 존 표에서 종류를 바꾸거나 랙 역할 레이어를 확인해 주세요")
    total_it = sum(z["it_design_kw"] for z in zones)
    if total_it <= 0:
        raise cad.CadError("전산실 IT 설계부하 합이 0 입니다. 랙 KW 속성·랙당 kW·존 IT 를 확인해 주세요")
    for p in parts:
        prev_rows[p["a"]["id"]]["partitions"].append({"zone": p["b"]["id"], "area_m2": p["area_m2"],
                                                       "u_value": p["u_value"], "kind": p["kind"]})
    notes.append(f"존 {len(zones)}개, 내부 경계 {len(parts)}쌍 (슬래브 {sum(p['kind'] == 'slab' for p in parts)}, "
                 f"내벽 {sum(p['kind'] == 'wall' for p in parts)}). 슬래브 U {SLAB_U:g}, 내벽 U {WALL_U:g} W/m²K "
                 "(가정값, 같은 쌍이 기존 시나리오에 있으면 그 값). 1층 바닥은 단열")
    notes.append(f"창 면적 = 도면상 폭 x 창 높이 {window_height_m} m (가정). 외피 U값·흡수율·SHGC 는 기존 존(없으면 건물) 값을 방위별로 유지")

    # 7) 건물 합계·설비
    agg_w, agg_win = Counter(), Counter()
    for z in zones:
        for w in z["envelope"]["walls"]:
            agg_w[w["orientation"]] += w["area_m2"]
        for w in z["envelope"]["windows"]:
            agg_win[w["orientation"]] += w["area_m2"]
    pick = lambda lst, o, d: next(({k: w[k] for k in d} for w in lst if w["orientation"] == o), dict(d))
    roof_sum = sum(z["envelope"]["roof"]["area_m2"] for z in zones)
    envelope = {"walls": [{"orientation": o, "area_m2": round(agg_w[o], 1), **pick(sc_env["walls"], o, cad.DEFAULT_WALL)}
                          for o in "NESW" if agg_w[o] > 0],
                "roof": {**{k: sc_env["roof"][k] for k in ("u_value", "absorptance") if k in sc_env["roof"]},
                         "area_m2": round(roof_sum, 1)},
                "windows": [{"orientation": o, "area_m2": round(agg_win[o], 1),
                             **pick(sc_env["windows"], o, cad.DEFAULT_WINDOW)} for o in "NESW" if agg_win[o] > 0]}
    every = defaultdict(list)
    for sh in shapes:
        every[sh["role"]].append(sh)
    plant, ch, tw = cad.plant_layout(every, scene, ew, layout, notes, warns, multi=True)
    cdus = [{"floor": f, "x_m": round(scene(cad._center(sh["pts"]))[0], 3), "z_m": round(scene(cad._center(sh["pts"]))[1], 3)}
            for f in floors for sh in on("cdu", f) if sh["pts"]]
    plant["cdu_positions"] = cdus or None
    if cdus:
        notes.append("CDU " + ", ".join(f"{lab[f]} {n}대" for f in floors if (n := sum(c['floor'] == f for c in cdus)))
                     + " 위치 반영 (건물 전체 보기·FWS 배관)")
    if on("cdu", None):
        warns.append(f"층 접두어가 없는 레이어의 CDU {len(on('cdu', None))}대는 어느 층인지 몰라 제외")
    max_crah = max((len(z.get("crah_positions") or []) for z in zones), default=0)
    total_area = round(sum(z["floor_area_m2"] for z in zones), 1)
    building = {"source": parsed["file_name"] or "cad", "ew_m": round(ew, 2), "ns_m": round(ns, 2),
                "height_m": round(sum(H.values()), 2), "rotation_deg": round(theta, 2),
                "footprint": [list(scene(p)) for p in fps[floors[0]]]}

    # 8) 미리보기 (층별, 3D 좌표계)
    fprev = []
    for f in floors:
        zf = [(z, r) for z, r in zip(zones, raw) if z["floor"] == f]
        rk = [{"id": it["id"], "x": round(it["x_m"] + z["geometry"]["x_m"], 3), "z": round(it["z_m"] + z["geometry"]["z_m"], 3),
               "row": it["row"], "zone": z["id"]} for z, _ in zf for it in ((z.get("racks") or {}).get("items") or [])]
        size = next(([z["racks"]["width_m"], z["racks"]["depth_m"]] for z, _ in zf if z.get("racks")), [0.6, 1.2])
        fprev.append({
            "floor": f, "label": lab[f], "height_m": H[f], "outline": fp_src[f],
            "footprint": [list(scene(p)) for p in fps[f]], "walls": [],
            "windows": [[list(scene(p)) for p in x["pts"]] for x in on("window", f)][:2000],
            "racks": rk, "rack_size": size,
            "crah": [[round(p[0] + z["geometry"]["x_m"], 3), round(p[1] + z["geometry"]["z_m"], 3)]
                     for z, _ in zf for p in z.get("crah_positions") or []],
            "chiller": ch, "tower": tw,
            "zones": [{"id": z["id"], "name": z["name"], "kind": z["kind"], "poly": [list(p) for p in r["scene"]],
                       "x": z["geometry"]["x_m"], "z": z["geometry"]["z_m"]} for z, r in zf],
        })
    main = max(fprev, key=lambda p: len(p["racks"]))
    preview = {**{k: v for k, v in main.items() if k not in ("floor", "label", "height_m", "outline", "zones")},
               "floors": fprev}
    report = {
        "notes": notes, "warnings": warns,
        "counts": {"racks": sum(r["racks"] for r in rep), "rows": sum((z.get("racks") or {}).get("rows", 0) for z in zones),
                   "crah": sum(r["crah"] for r in rep), "windows": sum(len(on("window", f)) for f in floors),
                   "zones": len(zones), "floors": len(floors), "cdu": len(cdus),
                   "chillers": len(plant.get("chiller_positions") or []) or int(bool(ch)),
                   "towers": len(plant.get("tower_positions") or []) or int(bool(tw))},
        "building": {"ew_m": building["ew_m"], "ns_m": building["ns_m"], "height_m": building["height_m"],
                     "floor_area_m2": total_area, "rotation_deg": building["rotation_deg"]},
        "envelope": {"walls": {w["orientation"]: w["area_m2"] for w in envelope["walls"]},
                     "windows": {w["orientation"]: w["area_m2"] for w in envelope["windows"]},
                     "roof_m2": envelope["roof"]["area_m2"]},
        "floors": [{"floor": f, "label": lab[f], "height_m": H[f], "outline": fp_src[f],
                    "zones": [z["id"] for z in zones if z["floor"] == f]} for f in floors],
        "zones": rep,
        "partitions": [{"a": p["a"]["id"], "b": p["b"]["id"], "kind": p["kind"], "area_m2": p["area_m2"],
                        "u_value": p["u_value"], "u_source": p["u_src"]} for p in parts],
        "it_design_kw": round(total_it, 3), "rack_kw": per_kw, "rack_kw_source": per_src,
    }
    return {"zones": zones, "envelope": envelope, "floor_area_m2": total_area, "building": building,
            "it_design_kw": round(total_it, 3),
            "layout": {"plant": plant, "crah_per_side": max(1, min(CRAH_PER_SIDE_MAX, math.ceil(max_crah / 2)))
                       if max_crah else None},
            "report": report, "preview": preview}
