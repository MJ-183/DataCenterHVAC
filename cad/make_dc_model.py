"""
데이터센터 더미 3D CAD 모델 생성 (DXF R2018, 단위 m)
치수·배치는 시뮬레이터 백엔드(/cooling/evaluate) 결과를 그대로 사용 → 3D 화면·계산과 같은 좌표
좌표 변환: 시뮬레이터 (x=동, y=위, z=남, 원점=건물 중심) → CAD (X=동, Y=북, Z=위)
"""
import json, sys
import ezdxf
from ezdxf.render import forms
from ezdxf.math import Vec3, Matrix44

ev = json.load(open(sys.argv[1]))
out = sys.argv[2]
g = ev["geometry"]; B = g["building"]; EW, NS, H = B["ew_m"], B["ns_m"], B["h_m"]

def P(x, y, z):            # sim -> CAD
    return Vec3(x, -z, y)

doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.M
doc.header["$INSUNITS"] = 6
msp = doc.modelspace()
LAYERS = {  # name: (ACI color, 설명)
    "A-OUTLINE": (6, "건물 외곽선 (2D, 가져오기용)"), "A-SLAB": (8, "기초 슬래브"), "A-WALL": (9, "외벽"), "A-ROOF": (253, "지붕"), "A-GLAZ": (5, "창"),
    "A-FLOR-RAISED": (252, "이중마루"), "DC-RACK": (3, "서버 랙"), "DC-CRAH": (4, "CRAH"),
    "M-CHILLER": (30, "칠러"), "M-TOWER": (7, "냉각탑"), "M-PAD": (8, "설비 패드"),
    "M-CHW-SUP": (5, "냉수 공급"), "M-CHW-RET": (140, "냉수 환수"),
    "M-CW-SUP": (3, "냉각수 공급"), "M-CW-RET": (1, "냉각수 환수"), "X-TEXT": (2, "라벨"),
}
for n, (c, d) in LAYERS.items():
    doc.layers.add(n, color=c).description = d

def box(layer, cx, cy, cz, sx, sy, sz, target=None):
    """sim 좌표 중심(cx,cy,cz)·크기(sx=동서, sy=높이, sz=남북) 상자 → MESH"""
    m = forms.cube().scale(sx, sz, sy).translate(*P(cx, cy, cz))
    m.render_mesh(target or msp, dxfattribs={"layer": layer})

def box_minmax(layer, x0, y0, z0, x1, y1, z1, target=None):
    box(layer, (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2, abs(x1 - x0), abs(y1 - y0), abs(z1 - z0), target)

# ---- 건물 ----
T = 0.3
box_minmax("A-SLAB", -EW/2 - 1, -0.5, -NS/2 - 1, EW/2 + 1, 0, NS/2 + 1)
box_minmax("A-WALL", -EW/2, 0, -NS/2, EW/2, H, -NS/2 + T)       # 북벽
box_minmax("A-WALL", -EW/2, 0, NS/2 - T, EW/2, H, NS/2)         # 남벽
box_minmax("A-WALL", -EW/2, 0, -NS/2 + T, -EW/2 + T, H, NS/2 - T)  # 서벽
box_minmax("A-WALL", EW/2 - T, 0, -NS/2 + T, EW/2, H, NS/2 - T)    # 동벽
box_minmax("A-ROOF", -EW/2, H, -NS/2, EW/2, H + 0.4, NS/2)
# 창 (시나리오 창 면적 N20 E10 S30 W10 m2 → 1.5 m 높이 띠창 = CAD 가져오기 기본 창 높이와 같게)
win = {w["orientation"]: w["area_m2"] for w in json.load(open(sys.argv[3]))["envelope"]["windows"]}
wh, y0 = 1.5, 3.0
for o, a in win.items():
    L = a / wh; e = 0.05
    if o in "NS":
        z = -NS/2 - e if o == "N" else NS/2 + e
        box("A-GLAZ", 0, y0 + wh/2, z, L, wh, 0.02)
    else:
        x = EW/2 + e if o == "E" else -EW/2 - e
        box("A-GLAZ", x, y0 + wh/2, 0, 0.02, wh, L)


# ---- 2D 평면 도형 (Z=0) : CAD 가져오기(hvac/cad.py)는 LINE/LWPOLYLINE/블록만 읽고 MESH 는 건너뜀 ----
def rect2d(layer, x0, z0, x1, z1):
    pts = [P(x0, 0, z0), P(x1, 0, z0), P(x1, 0, z1), P(x0, 0, z1)]
    msp.add_lwpolyline([(p.x, p.y) for p in pts], close=True, dxfattribs={"layer": layer})
rect2d("A-OUTLINE", -EW/2, -NS/2, EW/2, NS/2)                       # 외벽 바깥면 = 건물 외곽선
rect2d("A-WALL", -EW/2, -NS/2, EW/2, NS/2)                          # 외벽 바깥선
rect2d("A-WALL", -EW/2 + T, -NS/2 + T, EW/2 - T, NS/2 - T)          # 외벽 안쪽선
for o, a in win.items():                                            # 창: 외벽 바깥선 위의 선 (폭 = 면적 / 1.5 m)
    L = a / wh
    if o in "NS":
        z = -NS/2 if o == "N" else NS/2
        a_, b_ = P(-L/2, 0, z), P(L/2, 0, z)
    else:
        x = EW/2 if o == "E" else -EW/2
        a_, b_ = P(x, 0, -L/2), P(x, 0, L/2)
    msp.add_line(a_, b_, dxfattribs={"layer": "A-GLAZ"})

# ---- 전산실 ----
RF = 0.6   # 이중마루 높이 (가정, 표시용)
box_minmax("A-FLOR-RAISED", -EW/2 + T, 0, -NS/2 + T, EW/2 - T, RF, NS/2 - T)
rk = ev["airflow"]["racks"]
rw, rd, rh = 0.6, 1.2, 2.2
blk = doc.blocks.new("RACK_600x1200x2200")
forms.cube().scale(rw, rd, rh).translate(0, 0, rh / 2).render_mesh(blk, dxfattribs={"layer": "0"})
for r in rk:
    msp.add_blockref("RACK_600x1200x2200", P(r["x_m"], RF, r["z_m"]), dxfattribs={"layer": "DC-RACK"})
cb = doc.blocks.new("CRAH_2600x1000x2400")
forms.cube().scale(2.6, 1.0, 2.4).translate(0, 0, 1.2).render_mesh(cb, dxfattribs={"layer": "0"})
for c in ev["airflow"]["crah"]:
    msp.add_blockref("CRAH_2600x1000x2400", P(c["x_m"], RF, c["z_m"]), dxfattribs={"layer": "DC-CRAH"})

# ---- 옥외 설비 ----
ch, tw = g["chiller"], g["tower"]
box("M-PAD", ch["pos"][0], -0.15, (ch["pos"][2] + tw["pos"][2]) / 2, 14, 0.3, 24)
box("M-CHILLER", *ch["pos"], *ch["size"])
box("M-TOWER", *tw["pos"], *tw["size"])
for e_, L_ in ((ch, "M-CHILLER"), (tw, "M-TOWER")):                 # 설비 평면 외곽 (2D)
    (cx, _, cz), (sx, _, sz) = e_["pos"], e_["size"]
    rect2d(L_, cx - sx/2, cz - sz/2, cx + sx/2, cz + sz/2)
fan = forms.cylinder(count=24, radius=min(tw["size"][0], tw["size"][2]) * 0.38, top_center=(0, 0, 1.2))
fan.translate(*P(tw["pos"][0], tw["size"][1], tw["pos"][2])).render_mesh(msp, dxfattribs={"layer": "M-TOWER"})

# ---- 배관 (DN 실치수 원통) ----
lay = {"chw_supply": "M-CHW-SUP", "chw_return": "M-CHW-RET", "cw_supply": "M-CW-SUP", "cw_return": "M-CW-RET"}
n_pipe = 0
for s in ev["piping"]["segments"]:
    rad = s["dn"] / 2000.0
    pts = [P(*p) for p in s["points"]]
    for a, b in zip(pts, pts[1:]):
        if (b - a).magnitude < 1e-6:
            continue
        forms.cylinder_2p(count=12, radius=rad, base_center=a, top_center=b).render_mesh(
            msp, dxfattribs={"layer": lay[s["kind"]]})
        n_pipe += 1

# ---- 라벨 ----
def label(t, x, y, z, h=1.0):
    msp.add_text(t, height=h, dxfattribs={"layer": "X-TEXT"}).set_placement(P(x, y, z))
label(f"DATA CENTER {EW:.0f}x{NS:.0f}x{H:.0f}m", -EW/2, H + 1.0, NS/2 + 2, 1.5)
label("CHILLER", ch["pos"][0] - 3, ch["size"][1] + 0.5, ch["pos"][2])
label("COOLING TOWER", tw["pos"][0] - 4, tw["size"][1] + 1.8, tw["pos"][2])
label(f"RACKS {len(rk)} / CRAH {len(ev['airflow']['crah'])}", -12, RF + rh + 0.5, 0)

doc.set_modelspace_vport(height=120, center=(10, 0))
doc.saveas(out)
print("saved", out, "racks", len(rk), "crah", len(ev["airflow"]["crah"]), "pipe pieces", n_pipe)
