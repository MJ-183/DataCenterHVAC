"""
CAD(DXF) 도면 → 시나리오 외피·전산실 배치 (Phase 8-1).

입력은 DXF 만 받는다 (DWG 는 AutoCAD 등에서 'DXF 로 저장' 후 업로드).
1) inspect(): 모델 공간의 LINE / LWPOLYLINE / POLYLINE / CIRCLE / INSERT(블록) / TEXT / MTEXT 를 읽어
   레이어·블록별 개수, 역할 추정(이름 키워드), 미리보기 선분을 돌려준다.
2) build(): 사용자가 확정한 역할(레이어·블록 → outline/wall/window/rack/crah/chiller/tower/ignore)로
   - 외곽선(outline 우선, 없으면 wall 의 가장 큰 닫힌 폴리선, 그것도 없으면 wall 선의 외접 사각형)
     → 방위별 벽 면적 = 변 길이 x 층고 - 창 면적, 지붕·바닥 면적 = 외곽선 면적
   - 랙(블록 또는 닫힌 4각형) → 중심 좌표, 열 번호, id(블록 속성 > 랙 안의 문자 > 자동 번호), 설계 kW(속성)
   - CRAH / 칠러 / 냉각탑 → 좌표 (cooling_layout.crah.positions, plant)
가정·한계 (결과 notes 에 기록):
- 진북 각도(north_deg)는 도면 +Y 축에서 시계방향으로 잰 진북 방향. 벽 방위는 외향 법선을 N/E/S/W 중 가장 가까운 쪽에 배정
- 3D·기류 좌표계는 랙 열이 x 축과 평행하도록 도면을 rotation_deg 만큼 돌린 것 (외피 방위 계산은 회전 전 진북 기준)
- 랙 전면 방향은 도면에서 읽지 않고 기존 규칙(짝수 열 +z, 홀수 열 -z)을 따른다 (콜드/핫아일 표시용)
- 폴리선의 호(bulge)는 직선으로, 블록 안의 중첩 블록은 외곽 크기만 사용. 모델 공간 최상위 객체만 읽음
다중 층 (Phase 18 연장): 레이어 이름 접두어(F2-, 2F-, L2-, 2층)로 층을 읽는다. 역할 있는 층이 둘 이상이면
hvac/cad_zones.py 가 층·존별로 해석해 다중 존 시나리오를 만든다. 층이 하나면 아래 단일 존 경로 그대로
"""
import copy
import io
import math
import re
import statistics
from collections import Counter, defaultdict

import ezdxf
from ezdxf import bbox, recover

ROLES = ("outline", "wall", "window", "rack", "crah", "cdu", "chiller", "tower", "slot", "zone", "ignore")
ROLE_LABEL = {"outline": "건물 외곽선", "wall": "외벽", "window": "창", "rack": "랙", "crah": "CRAH/CRAC", "cdu": "CDU (다중 층)",
              "chiller": "칠러", "tower": "냉각탑", "slot": "빈 랙 자리 (증설용)", "zone": "존 경계 (다중 층)",
              "ignore": "무시"}
# (키워드, 토큰 단위 일치만 허용) - 위에서부터 먼저 맞는 역할
ROLE_KEYWORDS = [
    ("zone", [("ZONE", False), ("ROOM", False), ("존", False)]),
    ("outline", [("OUTLINE", False), ("FOOTPRINT", False), ("BOUNDARY", False), ("외곽", False), ("경계", False)]),
    ("window", [("GLAZ", False), ("WINDOW", False), ("WIN", True), ("WDW", True), ("창호", False), ("창", True)]),
    ("rack", [("RACK", False), ("CABINET", False), ("CAB", True), ("SERVER", False), ("SVR", True), ("랙", False),
              ("서버", False)]),
    ("crah", [("CRAH", False), ("CRAC", False), ("AHU", True), ("PAC", True), ("항온항습", False), ("공조기", False)]),
    ("cdu", [("CDU", False)]),
    ("chiller", [("CHILLER", False), ("CHLR", True), ("CH", True), ("냉동기", False), ("칠러", False)]),
    ("tower", [("TOWER", False), ("CT", True), ("CTW", True), ("냉각탑", False)]),
    ("slot", [("SLOT", False), ("FUTURE", False), ("SPARE", False), ("예비", False), ("증설", False)]),
    ("wall", [("WALL", False), ("WAL", True), ("벽", False)]),
]
INSUNITS_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1}
UNIT_LABEL = {1: "inch", 2: "ft", 4: "mm", 5: "cm", 6: "m", 14: "dm"}
ID_TAGS = ("RACKID", "RACK_ID", "ID", "RACK", "NAME", "TAG", "NO", "NUMBER", "랙번호", "번호", "명칭", "이름")
KW_TAGS = ("KW", "POWER", "LOAD", "전력", "용량", "부하")
PREVIEW_MAX_POINTS = 60000
WINDOW_MAX_DIST_M = 3.0


# 층 접두어 (레이어 이름) → 층 번호 n, floor = n - 1. 접두어 없는 레이어(SITE-, ROOF- 등)는 공통
FLOOR_PATTERNS = [re.compile(p, re.I) for p in (r"^F(\d+)-", r"^(\d+)F-", r"^L(\d+)-", r"^(\d+)층")]


class CadError(ValueError):
    pass


def floor_of(layer):
    """레이어 이름의 층 접두어 → floor (0 = 1층). 없으면 None (공통)"""
    for rx in FLOOR_PATTERNS:
        m = rx.match(layer)
        if m and int(m.group(1)) >= 1:
            return int(m.group(1)) - 1
    return None


def floor_label(f):
    return f"F{f + 1}"


def block_role(block_roles, block, layer):
    """블록 역할: '블록@F2' (그 층에 놓인 블록만) > '블록' (모든 층) > None (레이어 따름)"""
    f = floor_of(layer)
    if f is not None and f"{block}@{floor_label(f)}" in block_roles:
        return block_roles[f"{block}@{floor_label(f)}"]
    return block_roles.get(block)


# ---------- 읽기 ----------
def _tokens(name):
    return [t for t in re.split(r"[^0-9A-Za-z가-힣]+", name.upper()) if t]


def suggest_role(name):
    up, toks = name.upper(), _tokens(name)
    for role, kws in ROLE_KEYWORDS:
        for kw, exact in kws:
            if (kw in toks) if exact else (kw in up):
                return role
    return "ignore"


def _block_rect(doc, name, cache):
    """블록 정의의 로컬 외곽 사각형 (x0, y0, x1, y1). 비어 있으면 None"""
    if name not in cache:
        blk = doc.blocks.get(name)
        # 속성 정의·문자는 외형 크기에서 제외 (글자 영역 때문에 랙 폭이 커지지 않도록)
        ext = (bbox.extents([e for e in blk if e.dxftype() not in ("ATTDEF", "TEXT", "MTEXT")], fast=True)
               if blk is not None else None)
        cache[name] = ((float(ext.extmin.x), float(ext.extmin.y), float(ext.extmax.x), float(ext.extmax.y))
                       if ext is not None and ext.has_data else None)
    return cache[name]


def load(data: bytes, file_name: str = "") -> dict:
    """DXF 바이트 → 도형 목록 (도면 단위 그대로). DWG 이면 안내 오류"""
    head = data[:6]
    if head.startswith(b"AC10") or file_name.lower().endswith(".dwg"):
        raise CadError("DWG 파일은 받지 않습니다. AutoCAD 에서 '다른 이름으로 저장 → DXF' 로 내보낸 파일을 올려 주세요")
    try:
        doc, auditor = recover.read(io.BytesIO(data))
    except (IOError, ezdxf.DXFStructureError) as e:
        raise CadError(f"DXF 를 읽을 수 없습니다: {e}")
    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    shapes, texts, skipped = [], [], Counter()
    block_cache = {}
    for e in doc.modelspace():
        t, layer = e.dxftype(), e.dxf.get("layer", "0")
        try:
            if t == "LINE":
                s, q = e.dxf.start, e.dxf.end
                shapes.append({"layer": layer, "type": t, "kind": "line", "pts": [(s.x, s.y), (q.x, q.y)],
                               "closed": False})
            elif t == "LWPOLYLINE":
                pts = [(x, y) for x, y in e.get_points("xy")]
                shapes.append({"layer": layer, "type": t, "kind": "poly", "pts": pts, "closed": bool(e.closed),
                               "arc": e.has_arc})
            elif t == "POLYLINE":
                if e.is_poly_face_mesh or e.is_polygon_mesh:
                    skipped[t] += 1
                    continue
                pts = [(v.x, v.y) for v in e.points()]
                shapes.append({"layer": layer, "type": t, "kind": "poly", "pts": pts, "closed": bool(e.is_closed)})
            elif t == "CIRCLE":
                c, r = e.dxf.center, e.dxf.radius
                shapes.append({"layer": layer, "type": t, "kind": "circle", "pts": [(c.x, c.y)], "r": r,
                               "closed": True})
            elif t == "INSERT":
                name = e.dxf.name
                rect = _block_rect(doc, name, block_cache)
                attribs = {a.dxf.tag.strip(): a.dxf.text.strip() for a in e.attribs}
                m = e.matrix44()
                if rect is None:
                    p = m.transform((0, 0, 0))
                    pts, kind = [(p.x, p.y)], "point"
                else:
                    x0, y0, x1, y1 = rect
                    pts = [(v.x, v.y) for v in m.transform_vertices([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0),
                                                                     (x0, y1, 0)])]
                    kind = "rect"
                shapes.append({"layer": layer, "type": t, "kind": kind, "pts": pts, "closed": True,
                               "block": name, "attribs": attribs})
            elif t in ("TEXT", "MTEXT"):
                p = e.dxf.insert
                txt = e.dxf.text if t == "TEXT" else e.plain_text()
                txt = " ".join(txt.split())
                if txt:
                    h = e.dxf.get("height" if t == "TEXT" else "char_height", 0) or 0
                    texts.append({"layer": layer, "pos": (p.x, p.y), "text": txt, "h": float(h)})
            else:
                skipped[t] += 1
        except (AttributeError, ValueError, ezdxf.DXFError):
            skipped[t] += 1
    for sh in shapes:   # ezdxf 가 numpy 실수를 줄 때가 있어 파이썬 float 로 통일
        sh["pts"] = [(float(x), float(y)) for x, y in sh["pts"]]
    for t in texts:
        t["pos"] = (float(t["pos"][0]), float(t["pos"][1]))
    if not shapes:
        raise CadError("모델 공간에 읽을 수 있는 도형(선·폴리선·원·블록)이 없습니다")
    return {"file_name": file_name, "insunits": insunits, "shapes": shapes, "texts": texts,
            "skipped": dict(skipped), "audit_errors": len(auditor.errors), "dxf_version": doc.dxfversion}


def unit_scale(parsed, override=None):
    """도면 단위 → m 배율과 근거"""
    if override:
        return float(override), "사용자 지정"
    u = parsed["insunits"]
    if u in INSUNITS_M:
        return INSUNITS_M[u], f"$INSUNITS={u} ({UNIT_LABEL[u]})"
    xs = [p[0] for s in parsed["shapes"] for p in s["pts"]]
    ys = [p[1] for s in parsed["shapes"] for p in s["pts"]]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span > 1500:
        return 0.001, f"단위 미지정 → 도면 크기 {span:.0f} 로 mm 추정 (가정)"
    return 1.0, f"단위 미지정 → 도면 크기 {span:.0f} 로 m 추정 (가정)"


def inspect(parsed):
    """레이어·블록 요약, 역할 추정, 미리보기 (도면 좌표, m 단위, y 위쪽)"""
    scale, why = unit_scale(parsed)
    layers, blocks = defaultdict(Counter), defaultdict(lambda: {"count": 0, "layers": Counter(), "attribs": set(),
                                                                "floors": Counter()})
    for s in parsed["shapes"]:
        layers[s["layer"]][s["type"]] += 1
        if s.get("block"):
            b = blocks[s["block"]]
            b["count"] += 1
            b["layers"][s["layer"]] += 1
            b["attribs"].update(s["attribs"])
            f = floor_of(s["layer"])
            if f is not None:
                b["floors"][floor_label(f)] += 1
    for t in parsed["texts"]:
        layers[t["layer"]]["TEXT"] += 1
    preview, n_pts, truncated = defaultdict(list), 0, False
    for s in sorted(parsed["shapes"], key=lambda s: s["type"] != "INSERT"):
        if n_pts > PREVIEW_MAX_POINTS:
            truncated = True
            break
        pts = s["pts"]
        if s["kind"] == "circle":
            c, r = pts[0], s["r"]
            pts = [(c[0] + r * math.cos(a * math.pi / 6), c[1] + r * math.sin(a * math.pi / 6)) for a in range(12)]
        elif s["kind"] == "point":
            c = pts[0]
            pts = [(c[0] - 0.3 / scale, c[1]), (c[0] + 0.3 / scale, c[1])]
        closed = s["closed"] and len(pts) > 2
        preview[s["layer"]].append({"pts": [[round(x * scale, 3), round(y * scale, 3)] for x, y in pts],
                                    "closed": closed})
        n_pts += len(pts)
    xs = [p[0] * scale for s in parsed["shapes"] for p in s["pts"]]
    ys = [p[1] * scale for s in parsed["shapes"] for p in s["pts"]]
    return {
        "file_name": parsed["file_name"], "dxf_version": parsed["dxf_version"],
        "units": {"insunits": parsed["insunits"], "scale_m": scale, "basis": why},
        "extents_m": [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)],
        "layers": [{"name": n, "entities": dict(c), "suggested_role": suggest_role(n),
                    "floor": None if floor_of(n) is None else floor_label(floor_of(n))} for n, c in sorted(layers.items())],
        "blocks": [{"name": n, "count": b["count"], "layers": dict(b["layers"]), "attribute_tags": sorted(b["attribs"]),
                    "suggested_role": suggest_role(n), "floors": dict(b["floors"])} for n, b in sorted(blocks.items())],
        "floors": [{"floor": f, "label": floor_label(f), "layers": sum(1 for n in layers if floor_of(n) == f)}
                   for f in sorted({floor_of(n) for n in layers} - {None})],
        "preview": {"layers": dict(preview), "truncated": truncated},
        "skipped": parsed["skipped"], "audit_errors": parsed["audit_errors"],
        "roles": [{"value": r, "label": ROLE_LABEL[r]} for r in ROLES],
    }


# ---------- 기하 ----------
def _signed_area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


def _dedupe(pts, tol):
    out = []
    for p in pts:
        if not out or math.dist(out[-1], p) > tol:
            out.append(p)
    if len(out) > 2 and math.dist(out[0], out[-1]) <= tol:
        out.pop()
    return out


def _rect_of(shape, tol):
    """사각형으로 볼 수 있는 도형 → 4 꼭짓점. 아니면 None"""
    if shape["kind"] == "rect":
        return shape["pts"]
    if shape["kind"] == "poly" and shape["closed"]:
        p = _dedupe(shape["pts"], tol)
        if len(p) == 4:
            return p
    return None


def _center(pts):
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _seg_dist(p, a, b):
    ax, ay, bx, by = *a, *b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _orientation(az):
    return "NESW"[int(((az % 360) + 45) // 90) % 4]


def _ang180(dx, dy):
    return math.degrees(math.atan2(dy, dx)) % 180


def _ang_diff(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


def _id_like(txt):
    return 0 < len(txt) <= 24 and re.search(r"[0-9A-Za-z가-힣]", txt) and not re.fullmatch(r"[\d.,]+\s*(kW|KW|kw)?", txt)


def _attr_pick(attribs, tags):
    up = {k.upper(): v for k, v in attribs.items() if v}
    for t in tags:
        for k, v in up.items():
            if k == t or (len(t) > 2 and t in k):
                return v
    return None


def _num(txt):
    m = re.search(r"-?\d+(?:\.\d+)?", (txt or "").replace(",", ""))
    return float(m.group()) if m else None


# ---------- 시나리오 생성 ----------
DEFAULT_WALL = {"u_value": 0.35, "absorptance": 0.7}
DEFAULT_WINDOW = {"u_value": 1.8, "shgc": 0.4}


def _closed(shs):
    return [_dedupe(x["pts"], 0.01) for x in shs if x["kind"] in ("poly", "rect") and x["closed"]]


def rack_shapes(shapes, warns):
    """랙 역할 도형 → 사각형 랙 [{c, w, d, ang, sh, rect}] (사각형 아닌 것은 제외)"""
    racks, skip = [], 0
    for sh in shapes:
        rc = _rect_of(sh, 0.01)
        if rc is None:
            skip += 1
            continue
        a, b = math.dist(rc[0], rc[1]), math.dist(rc[1], rc[2])
        short = (rc[0], rc[1]) if a <= b else (rc[1], rc[2])
        ang = None if abs(a - b) / max(a, b, 1e-9) < 0.1 else _ang180(short[1][0] - short[0][0], short[1][1] - short[0][1])
        racks.append({"c": _center(rc), "w": min(a, b), "d": max(a, b), "ang": ang, "sh": sh, "rect": rc})
    if skip:
        warns.append(f"랙 역할 중 사각형이 아닌 도형 {skip}개는 제외")
    return racks


def row_angle(racks, fp):
    """랙 열 방향 θ [deg, -90~90) (랙이 없으면 외곽선 가장 긴 변)"""
    angs = [r["ang"] for r in racks if r["ang"] is not None]
    if angs:
        theta = 0.5 * math.degrees(math.atan2(sum(math.sin(math.radians(2 * a)) for a in angs),
                                              sum(math.cos(math.radians(2 * a)) for a in angs)))
    else:
        edges = [(fp[i], fp[(i + 1) % len(fp)]) for i in range(len(fp))]
        p, q = max(edges, key=lambda e: math.dist(*e))
        theta = _ang180(q[0] - p[0], q[1] - p[1])
    theta = ((theta + 90) % 180) - 90
    return 0.0 if abs(theta) < 0.2 else theta


def frame(theta, polys):
    """회전 θ 와 polys 외접 사각형 중심 기준 3D 좌표 함수 scene(p) → (x, z), 동서·남북 길이"""
    cos_t, sin_t = math.cos(math.radians(theta)), math.sin(math.radians(theta))
    rot = lambda p: (p[0] * cos_t + p[1] * sin_t, -p[0] * sin_t + p[1] * cos_t)
    pr = [rot(p) for poly in polys for p in poly]
    u0, u1 = min(p[0] for p in pr), max(p[0] for p in pr)
    v0, v1 = min(p[1] for p in pr), max(p[1] for p in pr)
    uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
    scene = lambda p: (round(float(rot(p)[0] - uc), 3), round(float(-(rot(p)[1] - vc)), 3))
    return scene, u1 - u0, v1 - v0


def rack_layout(keep, scene, texts, rg):
    """랙 → (items, rack_info, 메모). scene: 도면 m 좌표 → 배치 좌표, texts: [(도면 m 좌표, 문자)], rg: 기존 racks 설정"""
    for r in keep:
        r["s"] = scene(r["c"])
    width = statistics.median(r["w"] for r in keep)
    depth = statistics.median(r["d"] for r in keep)
    keep.sort(key=lambda r: r["s"][1])
    rows, gap = [[keep[0]]], max(0.5 * depth, 0.3)
    for r in keep[1:]:
        if r["s"][1] - rows[-1][-1]["s"][1] <= gap:
            rows[-1].append(r)
        else:
            rows.append([r])
    if len(rows) > 60:
        raise CadError(f"랙 열이 {len(rows)}개입니다 (최대 60). 랙 역할 레이어를 확인해 주세요")
    zs = [statistics.mean(r["s"][1] for r in row) for row in rows]
    aisle = (max(statistics.median(b - a for a, b in zip(zs, zs[1:])) - depth, 0.3) if len(zs) > 1
             else rg.get("aisle_m", 2.4))
    grid = defaultdict(list)
    for pos, txt in texts:
        x, z = scene(pos)
        grid[(int(x // 2), int(z // 2))].append((x, z, txt))
    prev_items = {it["id"]: it for it in (rg.get("items") or [])}
    seen, src, items = Counter(), Counter(), []
    for j, row in enumerate(rows):
        row.sort(key=lambda r: r["s"][0])
        if len(row) > 200:
            raise CadError(f"{j + 1}열의 랙이 {len(row)}개입니다 (최대 200)")
        for i, r in enumerate(row):
            x, z = r["s"]
            att = r["sh"].get("attribs") or {}
            rid = _attr_pick(att, ID_TAGS)
            if rid:
                src["attr"] += 1
            else:
                near = [(math.hypot(tx - x, tz - z), txt) for gx in (-1, 0, 1) for gz in (-1, 0, 1)
                        for tx, tz, txt in grid.get((int(x // 2) + gx, int(z // 2) + gz), [])
                        if abs(tx - x) <= width / 2 + 0.3 and abs(tz - z) <= depth / 2 + 0.3]
                if near:
                    rid = min(near)[1]
                    src["text"] += 1
                else:
                    rid = f"R{j + 1:02d}-{i + 1:03d}"
                    src["auto"] += 1
            rid = rid[:60]
            seen[rid] += 1
            if seen[rid] > 1:
                rid = f"{rid}#{seen[rid]}"
            kw = _num(_attr_pick(att, KW_TAGS)) if att else None
            it = {"id": rid, "x_m": x, "z_m": z, "row": j}
            if kw is not None and kw >= 0:
                it["design_kw"] = kw
            old = prev_items.get(rid)
            if old and old.get("kw_profile"):
                it["kw_profile"] = old["kw_profile"]
                it.setdefault("design_kw", old.get("design_kw"))
            if it.get("design_kw") is None:
                it.pop("design_kw", None)
            items.append(it)
    info = {"rows": len(rows), "per_row": max(len(r) for r in rows), "width_m": round(width, 3),
            "depth_m": round(depth, 3), "aisle_m": round(aisle, 3)}
    note = (f"랙 {len(items)}개, {len(rows)}열 (랙 {width:.2f} x {depth:.2f} m, 통로 {aisle:.2f} m). "
            f"id 출처: 블록 속성 {src['attr']}, 문자 {src['text']}, 자동 번호 {src['auto']}")
    return items, info, note


def plant_layout(by_role, scene, ew, layout, notes, warns, multi=False):
    """옥외 칠러·냉각탑 (건물 동측 설비동만 지원) → (cooling_layout.plant, 칠러 상자, 냉각탑 상자).
    multi (다중 층 도면): 칠러·냉각탑 닫힌 폴리선이 2개 이상이면 각 중심을 *_positions (건물 전체 보기 배치)로"""
    def equip(role):
        shs = [x for x in by_role[role] if x["pts"]]
        if not shs:
            return None
        sh = max(shs, key=lambda x: abs(_signed_area(x["pts"])) if len(x["pts"]) > 2 else 0)
        pts = [scene(p) for p in sh["pts"]]
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        return {"x": (min(xs) + max(xs)) / 2, "z": (min(zs) + max(zs)) / 2,
                "w": max(max(xs) - min(xs), 1.0), "d": max(max(zs) - min(zs), 1.0)}

    plant = dict(layout.get("plant") or {})
    ch, tw = equip("chiller"), equip("tower")
    if ch:
        gap = ch["x"] - ch["w"] / 2 - ew / 2
        if gap >= 2:
            plant.update(gap_east_m=round(gap, 2), chiller_z_m=round(ch["z"], 2))
            plant["chiller_size_m"] = [round(ch["w"], 2), (plant.get("chiller_size_m") or [8, 3, 3])[1], round(ch["d"], 2)]
            notes.append("칠러 위치·크기 반영")
        else:
            warns.append("칠러가 건물 동측 밖에 있지 않아 기본 위치 유지 (3D·배관 모델은 동측 설비동만 지원)")
    if tw:
        plant.update(tower_z_m=round(tw["z"], 2))
        plant["tower_size_m"] = [round(tw["w"], 2), (plant.get("tower_size_m") or [7, 5, 7])[1], round(tw["d"], 2)]
        notes.append("냉각탑 z 위치·크기 반영 (x 는 칠러와 같은 열에 둠, 단순화)")
    if multi:
        for role, key, label in (("chiller", "chiller_positions", "칠러"), ("tower", "tower_positions", "냉각탑")):
            cs = [_center([scene(p) for p in x["pts"]]) for x in by_role[role] if x["pts"] and x.get("closed")]
            plant[key] = [[round(x, 3), round(z, 3)] for x, z in cs] if len(cs) >= 2 else None
            if len(cs) >= 2:
                notes.append(f"{label} {len(cs)}대 위치 반영 (건물 전체 보기·배관 경로, 대수 산정보다 도면 우선)")
    return plant, ch, tw


def envelope_lists(gross, win_area, prev, warns, who=""):
    """방위별 벽 총면적·창 면적 → (walls, windows). U값·흡수율·SHGC 는 prev 외피에서 방위별로 이어받음"""
    pick = lambda lst, o, d: next(({k: w[k] for k in d} for w in lst if w["orientation"] == o), dict(d))
    walls, windows = [], []
    for o in "NESW":
        if gross[o] <= 0:
            continue
        net = gross[o] - win_area[o]
        if net < 0:
            warns.append(f"{who}{o} 면 창 면적이 벽 면적보다 커서 벽 면적을 0 으로 둠")
        walls.append({"orientation": o, "area_m2": round(max(net, 0.0), 1), **pick(prev["walls"], o, DEFAULT_WALL)})
        if win_area[o] > 0:
            windows.append({"orientation": o, "area_m2": round(min(win_area[o], gross[o]), 1),
                            **pick(prev["windows"], o, DEFAULT_WINDOW)})
    return walls, windows


def build(parsed, roles, block_roles=None, sc=None, layout=None, *, north_deg=0.0, height_m=10.0,
          window_height_m=1.5, unit_scale_m=None, floor_heights_m=None, rack_kw=None, zone_overrides=None):
    """역할 지정 → (envelope, internal_floor_area, building, layout 갱신분, report, preview)
    sc: 기존 시나리오 dict (외피 U값·창 SHGC 를 방위별로 이어받음), layout: 기존 CoolingLayout dict
    역할 있는 층(레이어 접두어)이 둘 이상이면 다중 존 (cad_zones.build_zones: floor_heights_m·rack_kw·zone_overrides 사용)"""
    block_roles = {k: v for k, v in (block_roles or {}).items() if v}
    bad = {v for v in [*roles.values(), *block_roles.values()] if v not in ROLES}
    if bad:
        raise CadError(f"알 수 없는 역할: {', '.join(sorted(bad))}")
    s, basis = unit_scale(parsed, unit_scale_m)
    notes, warns = [f"도면 단위: {basis}, 1 단위 = {s} m"], []

    def role_of(sh):
        if sh.get("block"):
            r = block_role(block_roles, sh["block"], sh["layer"])
            if r:
                return r
        return roles.get(sh["layer"], "ignore")

    shapes = [{**sh, "pts": [(x * s, y * s) for x, y in sh["pts"]], "r": sh.get("r", 0) * s,
               "role": role_of(sh), "floor": floor_of(sh["layer"])} for sh in parsed["shapes"]]
    floors = {sh["floor"] for sh in shapes if sh["role"] != "ignore"} - {None}
    if len(floors) >= 2:
        from .cad_zones import build_zones
        return build_zones(parsed, shapes, s, notes, warns, sc, layout or {}, north_deg=north_deg,
                           window_height_m=window_height_m, floor_heights_m=floor_heights_m, rack_kw=rack_kw,
                           zone_overrides=zone_overrides)
    if sc and sc.get("zones"):
        warns.append(f"층이 하나인 도면이라 기존 다중 존({len(sc['zones'])}개)을 지우고 단일 존으로 적용합니다")
    by_role = defaultdict(list)
    for sh in shapes:
        by_role[sh["role"]].append(sh)
    if any(sh.get("arc") for sh in by_role["outline"] + by_role["wall"]):
        notes.append("외곽선·벽의 호(bulge)는 직선으로 근사")

    # 1) 외곽선
    cand = [p for p in _closed(by_role["outline"]) if len(p) >= 3] or [p for p in _closed(by_role["wall"]) if len(p) >= 3]
    if cand:
        fp = max(cand, key=lambda p: abs(_signed_area(p)))
        notes.append(f"외곽선: {'outline' if _closed(by_role['outline']) else 'wall'} 레이어의 가장 큰 닫힌 폴리선 "
                     f"({len(fp)}각형)")
    else:
        src = by_role["wall"] or by_role["outline"]
        if not src:
            raise CadError("건물 외곽선(outline) 또는 외벽(wall) 역할의 레이어를 하나 이상 지정해 주세요")
        xs = [p[0] for x in src for p in x["pts"]]
        ys = [p[1] for x in src for p in x["pts"]]
        fp = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
        warns.append("닫힌 외곽선이 없어 외벽 선들의 외접 사각형을 건물 외곽으로 사용 (가정)")
    if _signed_area(fp) < 0:
        fp = fp[::-1]
    fp_area = abs(_signed_area(fp))
    if fp_area < 20:
        raise CadError(f"외곽선 면적이 {fp_area:.1f} m² 로 너무 작습니다. 단위(mm/m)를 확인해 주세요")

    # 2) 랙 사각형 → 열 방향(θ)
    racks = rack_shapes(by_role["rack"], warns)
    theta = row_angle(racks, fp)
    if theta:
        notes.append(f"랙 열이 동서(x) 방향이 되도록 3D·기류 좌표계를 도면 대비 {theta:.1f}° 회전 (외피 방위는 회전 전 진북 기준)")
    scene, ew, ns = frame(theta, [fp])

    # 3) 외피 (진북 기준 방위)
    edges = []
    gross = Counter()
    for i in range(len(fp)):
        p, q = fp[i], fp[(i + 1) % len(fp)]
        L = math.dist(p, q)
        if L < 0.05:
            continue
        o = edge_orientation(p, q, north_deg)
        edges.append((p, q, o))
        gross[o] += L * height_m
    win_area, win_n, win_far = Counter(), 0, 0
    for sh in by_role["window"]:
        w = window_of(sh)
        if w is None:
            continue
        c, width = w
        d, o = min((_seg_dist(c, p, q), o) for p, q, o in edges)
        if d > WINDOW_MAX_DIST_M:
            win_far += 1
            continue
        win_area[o] += width * window_height_m
        win_n += 1
    if win_far:
        warns.append(f"외곽선에서 {WINDOW_MAX_DIST_M} m 넘게 떨어진 창 {win_far}개는 내부 창으로 보고 제외")
    if win_n:
        notes.append(f"창 {win_n}개: 면적 = 도면상 폭 x 창 높이 {window_height_m} m (가정)")
    prev = sc["envelope"] if sc else {"walls": [], "windows": [], "roof": {"u_value": 0.25, "absorptance": 0.7}}
    walls, windows = envelope_lists(gross, win_area, prev, warns)
    envelope = {"walls": walls, "roof": {**prev["roof"], "area_m2": round(fp_area, 1)}, "windows": windows}
    notes.append(f"층고 {height_m} m, 외곽선 면적 {fp_area:.0f} m² → 지붕·바닥 면적. U값·흡수율·SHGC 는 기존 시나리오 값 유지")

    # 4) 랙 배치
    layout = layout or {}
    rg = layout.get("racks", {})
    keep = [r for r in racks if r["ang"] is None or _ang_diff(r["ang"], theta) <= 20]
    if len(keep) < len(racks):
        warns.append(f"주 열 방향과 {20}° 넘게 어긋난 랙 {len(racks) - len(keep)}개 제외 (직교 열은 아직 미지원)")
    items, rack_info = [], {}
    if keep:
        texts = [((t["pos"][0] * s, t["pos"][1] * s), t["text"]) for t in parsed["texts"] if _id_like(t["text"])]
        items, rack_info, note = rack_layout(keep, scene, texts, rg)
        notes.append(note)
        notes.append("랙 전면 방향은 도면에서 읽지 않고 짝수 열 남(+z)·홀수 열 북(-z) 규칙으로 표시 (가정)")
    else:
        warns.append("랙이 없어 기존 격자 배치(rows x per_row)를 유지")

    # 5) CRAH / 설비
    crah = [scene(_center(sh["pts"])) for sh in by_role["crah"] if sh["pts"]]
    slots = [[round(v, 3) for v in scene(_center(sh["pts"]))] for sh in by_role["slot"] if sh["pts"]]
    if slots:
        notes.append(f"빈 랙 자리 {len(slots)}곳 (증설 배치 후보)")
    if crah:
        notes.append(f"CRAH {len(crah)}대 좌표 반영")
    elif keep:
        warns.append("CRAH 역할이 없어 기본 배치(남·북 벽 per_side 대)를 유지")
    plant, ch, tw = plant_layout(by_role, scene, ew, layout, notes, warns)

    building = {"source": parsed["file_name"] or "cad", "ew_m": round(ew, 2), "ns_m": round(ns, 2),
                "height_m": height_m, "rotation_deg": round(theta, 2), "footprint": [list(scene(p)) for p in fp]}
    preview = {
        "footprint": building["footprint"],
        "walls": [[list(scene(p)) for p in x["pts"]] for x in by_role["wall"]][:3000],
        "windows": [[list(scene(p)) for p in x["pts"]] for x in by_role["window"]][:2000],
        "racks": [{"id": it["id"], "x": it["x_m"], "z": it["z_m"], "row": it["row"]} for it in items],
        "rack_size": [rack_info.get("width_m", 0.6), rack_info.get("depth_m", 1.2)],
        "crah": [list(p) for p in crah],
        "chiller": ch, "tower": tw,
    }
    report = {
        "notes": notes, "warnings": warns,
        "counts": {"racks": len(items), "rows": rack_info.get("rows", 0), "crah": len(crah), "windows": win_n},
        "building": {"ew_m": building["ew_m"], "ns_m": building["ns_m"], "height_m": height_m,
                     "floor_area_m2": round(fp_area, 1), "rotation_deg": building["rotation_deg"]},
        "envelope": {"walls": {w["orientation"]: w["area_m2"] for w in walls},
                     "windows": {w["orientation"]: w["area_m2"] for w in windows}, "roof_m2": round(fp_area, 1)},
    }
    lay = {**rack_info, "items": items or None, "crah": crah or None, "plant": plant, "slots": slots or None}
    return {"envelope": envelope, "floor_area_m2": round(fp_area, 1), "building": building, "layout": lay,
            "report": report, "preview": preview}


def edge_orientation(p, q, north_deg):
    """CCW 다각형 변 p→q 의 외향 법선 방위 (N/E/S/W, 진북 기준)"""
    L = math.dist(p, q)
    nx, ny = (q[1] - p[1]) / L, -(q[0] - p[0]) / L
    return _orientation(math.degrees(math.atan2(nx, ny)) - north_deg)


def window_of(sh):
    """창 도형 → (중심, 도면상 폭 = 가장 긴 변). 점 하나면 None"""
    pts = sh["pts"]
    if len(pts) < 2:
        return None
    segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts) if sh["closed"] else len(pts) - 1)]
    return _center(pts), max(math.dist(a, b) for a, b in segs)


def apply_to_scenario(sc, layout, built):
    """build() 결과를 시나리오 dict 에 반영한 새 dict (sc 는 바꾸지 않음).
    다중 층이면 zones·건물 합계 외피·IT 설계부하, 단일 층이면 기존 다중 존 정보를 지우고 단일 존으로"""
    sc2, lay = copy.deepcopy(sc), copy.deepcopy(layout)
    if built.get("zones"):
        sc2.update(zones=built["zones"], envelope=built["envelope"], building=built["building"])
        sc2["internal"]["floor_area_m2"] = built["floor_area_m2"]
        sc2["it_load"]["design_kw"] = built["it_design_kw"]
        lay["crah"]["positions"] = None          # 전산실별 crah_positions 사용
        if built["layout"]["crah_per_side"]:
            lay["crah"]["per_side"] = built["layout"]["crah_per_side"]
        lay["plant"] = built["layout"]["plant"]
        sc2["cooling_layout"] = lay
        return sc2
    sc2.pop("zones", None)
    sc2["envelope"] = built["envelope"]
    sc2["internal"]["floor_area_m2"] = built["floor_area_m2"]
    sc2["building"] = built["building"]
    b = built["layout"]
    if b["items"]:
        lay["racks"].update({k: b[k] for k in ("rows", "per_row", "width_m", "depth_m", "aisle_m")},
                            items=b["items"], row_density=None)
    if b.get("slots"):
        lay["racks"]["slots"] = b["slots"]
    if b["crah"]:
        lay["crah"]["positions"] = [list(p) for p in b["crah"]]
        lay["crah"]["per_side"] = max(1, min(30, math.ceil(len(b["crah"]) / 2)))
    lay["plant"] = b["plant"]
    sc2["cooling_layout"] = lay
    return sc2
