"""
3층 더미 데이터센터 — 다중 존 시나리오(JSON)와 DXF 도면을 같은 치수 정의에서 만든다.
사용: python gen_3f.py <backend 폴더> <출력 폴더>
좌표: 시뮬레이터 (x=동, y=위, z=남, 원점=건물 중심) → CAD (X=동, Y=북, Z=위), 단위 m
모든 치수·발열·설비 값은 가정값(더미). 근거 없는 실제 시설 도면이 아님.
"""
import copy, json, math, sys
from pathlib import Path

BACKEND, OUT = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(BACKEND))
from hvac.schemas import Scenario
from hvac.multizone import simulate_building
from hvac.airflow import rack_positions, crah_positions
from hvac.plant_units import size_units
from hvac.building_view import RISER_OFFSET_M, plant_layout

# ---------------- 치수 정의 (가정값) ----------------
EW, NS, FH, NF = 94.0, 60.0, 6.0, 3            # 건물 동서·남북 [m], 층고 [m], 층수
XW0, XSPLIT, XE1 = -47.0, 21.0, 47.0           # 서측 띠(전산실/전기실) | 동측 띠(사무·지원)
WEST = dict(ew=XSPLIT - XW0, cx=(XW0 + XSPLIT) / 2)
EAST = dict(ew=XE1 - XSPLIT, cx=(XSPLIT + XE1) / 2)
U_WALL, ABS, U_SLAB, U_PART = 0.35, 0.7, 2.0, 1.5
RACK = {"rows": 10, "per_row": 40, "width_m": 0.6, "depth_m": 1.2, "aisle_m": 2.4}
RACK_KW = 40.0
ZOFF = -6.0                                      # 1층 동측 띠: 북 = 사무(NOC), 남 = 전기·UPS (경계 z)
WIN = {"N": 20.0, "E": 20.0}                   # 1층 사무동 창 [m2] (북·동 외벽)
WIN_H = 1.5                                      # 창 높이 [m] (CAD 가져오기 기본값과 같게)

def wall(o, a): return {"orientation": o, "area_m2": round(a, 1), "u_value": U_WALL, "absorptance": ABS}
def env(side, floor, windows=None, z0=-NS / 2, z1=NS / 2):
    s = WEST if side == "W" else EAST
    w = windows or {}
    walls = [wall(o, a) for o, a in (("N", s["ew"] * FH - w.get("N", 0) if z0 <= -NS / 2 else 0),
                                     ("S", s["ew"] * FH - w.get("S", 0) if z1 >= NS / 2 else 0),
                                     (side, (z1 - z0) * FH - w.get(side, 0))) if a > 0]
    roof = s["ew"] * (z1 - z0) if floor == NF - 1 else 0.0
    return {"walls": walls, "roof": {"area_m2": round(roof, 1), "u_value": 0.25, "absorptance": ABS},
            "windows": [{"orientation": o, "area_m2": a, "u_value": 1.8, "shgc": 0.4} for o, a in w.items()]}
def geom(side, z0=-NS / 2, z1=NS / 2):
    s = WEST if side == "W" else EAST
    return {"ew_m": s["ew"], "ns_m": z1 - z0, "height_m": FH, "x_m": s["cx"], "z_m": (z0 + z1) / 2}

AW, AE = WEST["ew"] * NS, EAST["ew"] * NS
NOC = dict(z0=-NS / 2, z1=ZOFF); ELEC = dict(z0=ZOFF, z1=NS / 2)
AN, AEL = EAST["ew"] * (ZOFF + NS / 2), EAST["ew"] * (NS / 2 - ZOFF)
def hall(f, name, parts):
    return {"id": f"hall_{f + 1}f", "name": name, "floor": f, "kind": "data_hall", "geometry": geom("W"),
            "envelope": env("W", f), "it_design_kw": 16000.0, "floor_area_m2": AW, "outdoor_air_m3_h": 5000.0,
            "racks": dict(RACK), "partitions": parts}
def sup(f, parts):
    return {"id": f"support_{f + 1}f", "name": f"{f + 1}층 전기실·CDU실", "floor": f, "kind": "support",
            "geometry": geom("E"), "envelope": env("E", f), "indoor": {"temp_c": 30.0, "rh_pct": 50.0},
            "floor_area_m2": AE, "misc_kw": 150.0, "outdoor_air_m3_h": 2000.0, "partitions": parts}
slab = lambda z, a: {"zone": z, "area_m2": a, "u_value": U_SLAB, "kind": "slab"}
pwall = lambda z, a: {"zone": z, "area_m2": a, "u_value": U_PART, "kind": "wall"}
zones = [
  hall(0, "1층 전산실 A", [slab("hall_2f", AW), pwall("office_1f", (ZOFF + NS / 2) * FH), pwall("elec_1f", (NS / 2 - ZOFF) * FH)]),
  hall(1, "2층 전산실 B", [slab("hall_3f", AW), pwall("support_2f", NS * FH)]),
  hall(2, "3층 전산실 C", [pwall("support_3f", NS * FH)]),
  {"id": "office_1f", "name": "1층 사무·관제(NOC)", "floor": 0, "kind": "office", "geometry": geom("E", **NOC),
   "envelope": env("E", 0, WIN, **NOC), "floor_area_m2": AN, "lighting_w_m2": 12.0, "people": 25.0,
   "outdoor_air_m3_h": 5000.0, "partitions": [pwall("elec_1f", EAST["ew"] * FH), slab("support_2f", AN)]},
  {"id": "elec_1f", "name": "1층 전기·UPS실", "floor": 0, "kind": "support", "geometry": geom("E", **ELEC),
   "envelope": env("E", 0, None, **ELEC), "indoor": {"temp_c": 30.0, "rh_pct": 50.0}, "floor_area_m2": AEL,
   "misc_kw": 250.0, "outdoor_air_m3_h": 2000.0, "partitions": [slab("support_2f", AEL)]},
  sup(1, [slab("support_3f", AE)]),
  sup(2, []),
]

base = json.load(open(BACKEND / "data/scenario_haenam_ai_mz.json", encoding="utf-8"))
sc = {k: copy.deepcopy(base[k]) for k in ("site", "weather", "indoor", "it_load", "internal", "ventilation",
                                          "cooling_plant", "cooling_layout")}
sc["_note"] = ("3층 더미 데이터센터 (다중 존 7개). 도면 datacenter_3f_dummy.dxf 와 같은 치수. 94 x 60 m, 층고 6 m, "
               "1~3층 서측 전산실 A·B·C(각 16 MW, 랙 400개 x 40 kW), 동측은 1층 사무·전기, 2·3층 전기·CDU실. "
               "기상·IT 모양·설비 계수는 해남 다중 존 예시와 같음. 치수·발열·랙 밀도·내부 경계 U값은 모두 가정값")
sc["site"]["name"] = "3층 더미 데이터센터 (해남 기상, 다중 존)"
tot = {o: 0.0 for o in "NESW"}
for z in zones:
    for w in z["envelope"]["walls"]:
        tot[w["orientation"]] += w["area_m2"]
sc["envelope"] = {"walls": [wall(o, a) for o, a in tot.items()],
                  "roof": {"area_m2": EW * NS, "u_value": 0.25, "absorptance": ABS},
                  "windows": [{"orientation": o, "area_m2": a, "u_value": 1.8, "shgc": 0.4} for o, a in WIN.items()]}
sc["internal"]["floor_area_m2"] = EW * NS * NF
sc["it_load"]["design_kw"] = sum(z.get("it_design_kw", 0.0) for z in zones)
sc["zones"] = zones
sc = Scenario.model_validate(sc).to_engine_dict()
(OUT / "scenario_3f_dummy_mz.json").write_text(json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

res = simulate_building(sc)
units = size_units(sc["cooling_layout"], res) or {}
summary = {"daily_total": res["daily_total"],
           "zones": [{"id": z["id"], "name": z["name"], "cooling_kwh": z.get("daily_total", {}).get("cooling_kwh")} for z in res["zones"]],
           "units": {k: {kk: v[kk] for kk in ("unit_kw", "design_kw", "duty", "standby", "total")} for k, v in units.items()}}
json.dump(summary, open(OUT / "sim_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False, indent=1))

# ---------------- DXF ----------------
import ezdxf
from ezdxf.render import forms
from ezdxf.math import Vec3
def P(x, y, z): return Vec3(x, -z, y)
doc = ezdxf.new("R2018", setup=True); doc.units = ezdxf.units.M; doc.header["$INSUNITS"] = 6
msp = doc.modelspace()
COL = {"A-OUTLINE": 6, "A-WALL": 9, "A-GLAZ": 5, "A-SLAB": 8, "A-CORE": 252, "ZONE": 30, "DC-RACK": 3, "DC-CRAH": 4,
       "M-CDU": 140, "M-CHW-RISER": 5, "X-TEXT": 2}
def lay(name, color):
    if name not in doc.layers: doc.layers.add(name, color=color)
    return name
def box(layer, x0, y0, z0, x1, y1, z1):
    forms.cube().scale(abs(x1 - x0), abs(z1 - z0), abs(y1 - y0)).translate(*P((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)) \
        .render_mesh(msp, dxfattribs={"layer": layer})
def rect2d(layer, x0, z0, x1, z1, elev):
    pts = [P(x0, 0, z0), P(x1, 0, z0), P(x1, 0, z1), P(x0, 0, z1)]
    msp.add_lwpolyline([(p.x, p.y) for p in pts], close=True, dxfattribs={"layer": layer, "elevation": elev})
def text(layer, s, x, y, z, h=1.0):
    msp.add_text(s, height=h, dxfattribs={"layer": layer}).set_placement(P(x, y, z))

# 블록: 랙(속성 RACK_ID, KW), CRAH, CDU
rb = doc.blocks.new("RACK_600x1200x2200")
forms.cube().scale(0.6, 1.2, 2.2).translate(0, 0, 1.1).render_mesh(rb, dxfattribs={"layer": "0"})
rb.add_attdef("RACK_ID", (0, 0, 2.3), dxfattribs={"height": 0.15, "invisible": True})
rb.add_attdef("KW", (0, 0, 2.5), dxfattribs={"height": 0.15, "invisible": True})
cb = doc.blocks.new("CRAH_2600x1000x2400")
forms.cube().scale(2.6, 1.0, 2.4).translate(0, 0, 1.2).render_mesh(cb, dxfattribs={"layer": "0"})
db = doc.blocks.new("CDU_1200x1200x2200")
forms.cube().scale(1.2, 1.2, 2.2).translate(0, 0, 1.1).render_mesh(db, dxfattribs={"layer": "0"})

T = 0.3; RF = 0.6
n_rack = n_crah = n_cdu = 0
cfg = copy.deepcopy(sc["cooling_layout"])
plant = plant_layout(sc, cfg, units)        # 칠러·냉각탑·CDU 위치 = 시뮬레이터 건물 전체 보기의 기본 배치와 같은 규칙
for f in range(NF):
    F = f"F{f + 1}"; e0 = f * FH
    L = {k: lay(f"{F}-{k}", c) for k, c in COL.items()}
    # 3D: 슬래브, 외벽, 코어(계단·승강기), 내벽
    box(L["A-SLAB"], XW0, e0 - 0.3, -NS / 2, XE1, e0, NS / 2)
    box(L["A-WALL"], XW0, e0, -NS / 2, XE1, e0 + FH, -NS / 2 + T)
    box(L["A-WALL"], XW0, e0, NS / 2 - T, XE1, e0 + FH, NS / 2)
    box(L["A-WALL"], XW0, e0, -NS / 2 + T, XW0 + T, e0 + FH, NS / 2 - T)
    box(L["A-WALL"], XE1 - T, e0, -NS / 2 + T, XE1, e0 + FH, NS / 2 - T)
    box(L["A-WALL"], XSPLIT - 0.1, e0, -NS / 2 + T, XSPLIT + 0.1, e0 + FH, NS / 2 - T)
    if f == 0:
        box(L["A-WALL"], XSPLIT + 0.1, e0, ZOFF - 0.1, XE1 - T, e0 + FH, ZOFF + 0.1)     # 1층 사무 | 전기실 내벽
    box(L["A-CORE"], XE1 - 9, e0, -NS / 2 + T, XE1 - T, e0 + FH, -NS / 2 + 10)      # 계단·승강기 코어
    # 2D 평면 (해당 층 바닥 높이): 외곽선, 외벽 안·밖, 존 경계 + 라벨
    rect2d(L["A-OUTLINE"], XW0, -NS / 2, XE1, NS / 2, e0)
    rect2d(L["A-WALL"], XW0, -NS / 2, XE1, NS / 2, e0)
    rect2d(L["A-WALL"], XW0 + T, -NS / 2 + T, XE1 - T, NS / 2 - T, e0)
    for z in [z for z in zones if z["floor"] == f]:
        g = z["geometry"]; x0, x1 = g["x_m"] - g["ew_m"] / 2, g["x_m"] + g["ew_m"] / 2
        z0, z1 = g["z_m"] - g["ns_m"] / 2, g["z_m"] + g["ns_m"] / 2
        rect2d(L["ZONE"], x0, z0, x1, z1, e0)
        text(L["ZONE"], f"{z['id']} | {z['kind']}", x0 + 2, e0 + 0.05, z0 + 3, 1.2)
        text(L["X-TEXT"], z["name"], x0 + 2, e0 + 0.05, z0 + 5, 1.4)
        if z["kind"] == "data_hall":
            box(L["A-SLAB"], x0 + T, e0, -NS / 2 + T, x1 - 0.1, e0 + RF, NS / 2 - T)   # 이중마루
            zcfg = {**cfg, "racks": z["racks"]}
            pos, rows, per = rack_positions(zcfg, g["ew_m"], g["ns_m"])
            for j, i, x, zz in pos:
                br = msp.add_blockref("RACK_600x1200x2200", P(g["x_m"] + x, e0 + RF, zz), dxfattribs={"layer": L["DC-RACK"]})
                br.add_auto_attribs({"RACK_ID": f"{F}-R{j + 1:02d}-{i + 1:03d}", "KW": f"{RACK_KW:g}"})
                n_rack += 1
            for x, zz in crah_positions(zcfg, g["ew_m"], g["ns_m"]):
                msp.add_blockref("CRAH_2600x1000x2400", P(g["x_m"] + x, e0 + RF, zz), dxfattribs={"layer": L["DC-CRAH"]})
                n_crah += 1
    for cf, _, cx, cz in plant["cdu"]:                  # CDU: 전산실 IT 비율로 나눠 같은 층 지원 존 (가정)
        if cf == f:
            msp.add_blockref("CDU_1200x1200x2200", P(cx, e0, cz), dxfattribs={"layer": L["M-CDU"]})
            n_cdu += 1
    # 창 (1층 사무동): 2D 선 + 3D 판
    if f == 0:
        for o, a in WIN.items():
            Lw = a / WIN_H
            if o in "NS":
                zz = -NS / 2 if o == "N" else NS / 2
                a_, b_ = P(EAST["cx"] - Lw / 2, e0, zz), P(EAST["cx"] + Lw / 2, e0, zz)
                box(L["A-GLAZ"], EAST["cx"] - Lw / 2, e0 + 2.5, zz - 0.05, EAST["cx"] + Lw / 2, e0 + 2.5 + WIN_H, zz + 0.05)
            else:                                          # 동측 창은 사무동(북쪽 구획) 가운데
                zm = (-NS / 2 + ZOFF) / 2
                a_, b_ = P(XE1, e0, zm - Lw / 2), P(XE1, e0, zm + Lw / 2)
                box(L["A-GLAZ"], XE1 - 0.05, e0 + 2.5, zm - Lw / 2, XE1 + 0.05, e0 + 2.5 + WIN_H, zm + Lw / 2)
            msp.add_line(a_, b_, dxfattribs={"layer": L["A-GLAZ"]})

# 지붕·파라펫
lay("ROOF-A-ROOF", 253); box("ROOF-A-ROOF", XW0, NF * FH, -NS / 2, XE1, NF * FH + 0.4, NS / 2)
for (x0, z0, x1, z1) in ((XW0, -NS / 2, XE1, -NS / 2 + 0.3), (XW0, NS / 2 - 0.3, XE1, NS / 2),
                         (XW0, -NS / 2, XW0 + 0.3, NS / 2), (XE1 - 0.3, -NS / 2, XE1, NS / 2)):
    box("ROOF-A-ROOF", x0, NF * FH + 0.4, z0, x1, NF * FH + 1.6, z1)

# 옥외 설비 (동측 야드): 대수 = plant_units 산정 (운전 + 예비), 위치 = hvac.building_view.plant_layout (건물 전체 보기와 같은 규칙)
lay("SITE-M-PAD", 8); lay("SITE-M-CHILLER", 30); lay("SITE-M-TOWER", 7); lay("SITE-M-CHW-RISER", 5); lay("SITE-X-TEXT", 2)
pl = cfg["plant"]
cw, chh, cd = pl.get("chiller_size_m", [8, 3, 3]); tw, th, td = pl.get("tower_size_m", [7, 5, 7])
nch, nct = len(plant["chiller"]), len(plant["tower"])
xs = [x for x, _ in plant["chiller"] + plant["tower"]]; zs = [z for _, z in plant["chiller"] + plant["tower"]]
box("SITE-M-PAD", min(xs) - cw / 2 - 4, -0.3, min(zs) - td / 2 - 3, max(xs) + tw / 2 + 3, 0, max(zs) + td / 2 + 3)
for xc, zc in plant["chiller"]:
    box("SITE-M-CHILLER", xc - cw / 2, 0, zc - cd / 2, xc + cw / 2, chh, zc + cd / 2)
    rect2d("SITE-M-CHILLER", xc - cw / 2, zc - cd / 2, xc + cw / 2, zc + cd / 2, 0)
for xt, zc in plant["tower"]:
    box("SITE-M-TOWER", xt - tw / 2, 0, zc - td / 2, xt + tw / 2, th, zc + td / 2)
    rect2d("SITE-M-TOWER", xt - tw / 2, zc - td / 2, xt + tw / 2, zc + td / 2, 0)
    forms.cylinder(count=20, radius=tw * 0.36, top_center=(0, 0, 1.0)).translate(*P(xt, th, zc))         .render_mesh(msp, dxfattribs={"layer": "SITE-M-TOWER"})
# 냉수 입상관 (전산실 동측 벽 + RISER_OFFSET_M, 칠러 z) — 공급·환수, 3층 인입점 높이까지. 야드 연결
xr0 = XSPLIT + RISER_OFFSET_M; zr = pl["chiller_z_m"]; top = (NF - 1) * FH + 3.2
xh = min(xs[:nch]) - cw / 2 - 2.0
for dx, y0 in ((0.0, 1.0), (0.9, 1.9)):
    forms.cylinder_2p(count=12, radius=0.25, base_center=P(xr0 + dx, y0, zr), top_center=P(xr0 + dx, top + y0 - 1.0, zr))         .render_mesh(msp, dxfattribs={"layer": "SITE-M-CHW-RISER"})
    forms.cylinder_2p(count=12, radius=0.25, base_center=P(xr0 + dx, y0, zr), top_center=P(xh, y0, zr))         .render_mesh(msp, dxfattribs={"layer": "SITE-M-CHW-RISER"})
xc, xt, span = plant["chiller"][0][0], plant["tower"][0][0], max(zs) - min(zs)
text("SITE-X-TEXT", f"CHILLER x{nch} (N+{pl.get('standby_units', 1)})", xc - 4, chh + 0.5, min(zs) - td / 2 - 1.5, 1.2)
text("SITE-X-TEXT", f"COOLING TOWER x{nct}", xt - 4, th + 1.5, min(zs) - td / 2 - 1.5, 1.2)
text("SITE-X-TEXT", f"3F DUMMY DATA CENTER {EW:.0f} x {NS:.0f} m, H {FH:.0f} m/floor", XW0, NF * FH + 3, NS / 2 + 4, 2.0)
doc.set_modelspace_vport(height=180, center=(20, 0))
doc.saveas(OUT / "datacenter_3f_dummy.dxf")
print("dxf: racks", n_rack, "crah", n_crah, "cdu", n_cdu, "chillers", nch, "towers", nct)
