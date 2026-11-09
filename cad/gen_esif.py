"""
NREL ESIF HPC 데이터센터 근사 도면 (DXF) — 공개 자료의 사실 + 명시한 가정. 실제 설계 도면이 아님.

사용: python cad/gen_esif.py            → cad/esif_approx.dxf, cad/esif_approx_facts.json
좌표: CAD X = 동, Y = 북, Z = 위, 단위 m, 원점 = 데이터센터 블록 중심. 1 ft = 0.3048 m

레이어 규칙 (프로젝트 CAD 가져오기와 호환)
- 데이터센터 블록 F1~F4: Fn-A-OUTLINE, Fn-A-WALL, Fn-ZONE("id | kind"), F3-DC-RACK(블록 KW 속성), F3-DC-CDU,
  F3-DC-SLOT(증설 자리), ROOF-M-TOWER(데이터센터용 냉각탑 4기)
- 참고용(가져오기에서 무시됨): CTX-*(사무동·고천장 실험동 매스), Fn-M-*(열교환기·펌프·팬월 F2-M-FANARRAY·집수조·배관, 이름에 WALL/AHU 를 쓰지 않음),
  ROOF-M-LABCOOLING(사무·실험동용 냉각탑 3기), ROOF-M-TSC(열사이펀 건식냉각기)
"""
import json
from pathlib import Path

import ezdxf
from ezdxf.math import Vec3
from ezdxf.render import forms

OUT = Path(__file__).resolve().parent
FT = 0.3048

# ---------------- 사실 (출처) / 가정 ----------------
FACTS = {
    "machine_room_m2": (929.0, "약 930 m2 (10,000 ft2), 기둥 없는 단일 공간 — NREL fy17osti 66690, fy12osti 55423"),
    "ceiling_m": (11 * FT, "천장 높이 11 ft — NREL fy12osti 55423"),
    "stack": ("최상층 전산실 / 가운데 층 펌프·전력 분배 / 최하층 펌프실(중앙 냉난방 연결)", "NREL fy12osti 55423"),
    "mech_below": ("ERW→건물 난방수 판형 열교환기는 전산실 아래 기계실", "NREL fy17osti 66690"),
    "mech_4f": ("열 재활용 후 남은 ERW 는 4층 기계실로 감 (열사이펀·냉각탑 열교환기 방향)", "NREL 데이터센터 냉각 시스템 페이지"),
    "return_chases": ("천장 플레넘의 더운 공기가 북·남측 환기 통로(chase)로 내려가 팬월 공조기로", "NREL fy12osti 55423"),
    "hot_aisle": ("열복도 완전 차폐, 일반 CRAH 없음, 팬월 급기 20.6~23.3 ℃", "NREL fy17osti 66690"),
    "towers": ("옥외 플랫폼에 냉각탑 7기: 데이터센터용 4기(부하별 단계 운전, 보통 2기) + 사무·실험동용 3기", "NREL fy17osti 66690"),
    "sumps": ("냉각탑마다 원격 실내 집수조 (기저 히터 불필요)", "NREL fy17osti 66690"),
    "tsc": ("열사이펀 건식냉각기 1기, ESIF 옥상, 향후 냉각탑용 플랫폼 위, 2016-08 가동", "NREL fy17osti 68168, 66690"),
    "pipes": ("ERW 환수관 350 mm(14 in), 열사이펀 공급·환수 150 mm(6 in) 신설", "NREL fy17osti 66690"),
    "it_2016": ("가동 시 IT 0.91 MW = 액체냉각 HPC 0.76 MW + 공랭 0.15 MW", "NREL fy17osti 66690"),
    "cdu": ("Peregrine CDU 10대, 노드 랙 사이 별도 IT 랙에 설치", "NREL fy17osti 66690"),
    "peregrine_nodes": (2592, "Peregrine 서버 노드 2,592 — NREL fy17osti 66690"),
    "apollo_rack": (144, "HP Apollo 8000 랙당 서버 144 — HPCwire 2014-06-10"),
    "rack_kw": ("전산 랙 60~80 kW(액체), 공랭 랙 1~5 kW, 480 VAC 직접 공급", "NREL fy19osti 74464, Sustainable Data Centers"),
    "capacity": ("설계 최대 10 MW, 2.5 MW 단위 증설, 배관은 10 MW 기준", "NREL fy17osti 66690"),
    "office_depth_m": (60 * FT, "사무동 깊이 60 ft — EERE Energy Efficiency in Design-Build Course"),
    "height_3st_m": (45 * FT, "45 ft = 3층 — 같은 자료"),
    "building_m2": (182500 * FT ** 2, "건물 연면적 182,500 ft2, 사무동·데이터센터·고천장 실험동 3개 매스 — NREL, SmithGroup"),
    "elevation_m": (1770.0, "해발 약 1,770 m (5,800 ft) — NREL fy17osti 66690"),
}
ASSUMED = {
    "room_shape": "전산실 30.5 x 30.5 m 정사각형 (100 x 100 ft, 면적 사실에 맞춘 가정)",
    "chase_w": "북·남 환기 통로 폭 각 1.5 m → 블록 30.5 x 33.5 m",
    "floor_h": "층고 F1 4.6 / F2 4.6 / F3 4.9 (천장 3.35 + 플레넘 1.25 + 슬래브 0.3) / F4 4.6 m",
    "racks": "Peregrine 노드 랙 18 = 2,592 ÷ 144 (전부 Apollo 랙이라 가정), 랙당 42.2 kW = 0.76 MW ÷ 18 (2016 가동값)",
    "air_racks": "공랭 랙 50 x 3 kW = 0.15 MW (1~5 kW 범위의 중간값)",
    "rows": "랙 열 동서 방향, 액체 2열 x 14(노드 9 + CDU 5), 공랭 2열 x 25, 열복도 차폐, 증설 자리 2열 x 14",
    "fan_walls": "팬월 위치는 공개되지 않음 → F2 의 북·남 환기 통로 아래, 각 24 m 길이로 가정",
    "mech": "F2 판형 열교환기 1, ERW 펌프 2+1, 전력 분배반 6 / F1 난방수 펌프 2 / F4 냉각탑 열교환기 1, 집수조 4 — 대수·크기 가정",
    "tower_size": "냉각탑 1기 3.7 x 3.7 x 4.0 m (초기 600 RT ÷ 4기 = 150 RT 급 가정), 플랫폼은 데이터센터 블록 옥상(가정)",
    "tsc_size": "열사이펀 12 x 2.4 x 2.6 m (크기 미공개, 가정)",
    "massing": "사무동 북측(동서 110 m x 깊이 18.3 m, 3층 13.7 m), 실험동 남측(110 x 45 m, 높이 12 m) — 배치·길이 가정",
}

EW, NS_ROOM, CH = 30.5, 30.5, 1.5
NS = NS_ROOM + 2 * CH
FH = [4.6, 4.6, 4.9, 4.6]
ELEV = [sum(FH[:i]) for i in range(5)]
X0, X1, Y0, Y1 = -EW / 2, EW / 2, -NS / 2, NS / 2
RY0, RY1 = Y0 + CH, Y1 - CH                     # 전산실 남·북 경계 (통로 안쪽)
RW, RD, RH = 0.6, 1.2, 2.2
NODE_KW = round(760.0 / 18, 1)
AIR_KW = 3.0

doc = ezdxf.new("R2018", setup=True)
doc.units = ezdxf.units.M
doc.header["$INSUNITS"] = 6
msp = doc.modelspace()


def lay(name, color):
    if name not in doc.layers:
        doc.layers.add(name, color=color)
    return name


def box(layer, x0, y0, z0, x1, y1, z1):
    forms.cube().scale(abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)) \
        .translate((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2).render_mesh(msp, dxfattribs={"layer": layer})


def rect(layer, x0, y0, x1, y1, z):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={"layer": layer, "elevation": z})


def text(layer, s, x, y, z, h=0.8):
    msp.add_text(s, height=h, dxfattribs={"layer": layer}).set_placement(Vec3(x, y, z))


def pipe(layer, a, b, d):
    forms.cylinder_2p(count=12, radius=d / 2, base_center=Vec3(*a), top_center=Vec3(*b)) \
        .render_mesh(msp, dxfattribs={"layer": layer})


# 블록
rb = doc.blocks.new("RACK_600x1200x2200")
forms.cube().scale(RW, RD, RH).translate(0, 0, RH / 2).render_mesh(rb, dxfattribs={"layer": "0"})
rb.add_attdef("RACK_ID", (0, 0, RH + 0.1), dxfattribs={"height": 0.12, "invisible": True})
rb.add_attdef("KW", (0, 0, RH + 0.3), dxfattribs={"height": 0.12, "invisible": True})
cb = doc.blocks.new("CDU_UNIT_600x1200x2200")
forms.cube().scale(RW, RD, RH).translate(0, 0, RH / 2).render_mesh(cb, dxfattribs={"layer": "0"})
sb = doc.blocks.new("SLOT_600x1200")
sb.add_lwpolyline([(-RW / 2, -RD / 2), (RW / 2, -RD / 2), (RW / 2, RD / 2), (-RW / 2, RD / 2)], close=True)

count = {"node": 0, "cdu": 0, "air": 0, "slot": 0}
ZONES = {0: ("pump_1f", "support", "L1 PUMP ROOM"), 1: ("mech_2f", "support", "L2 MECH (PUMPS, POWER, HX, FAN WALLS)"),
         2: ("dc_3f", "data_hall", "L3 HPC DATA CENTER 10,000 FT2"), 3: ("mech_4f", "support", "L4 MECH (TOWER HX, SUMPS)")}

for f in range(4):
    F, e0, h = f"F{f + 1}", ELEV[f], FH[f]
    L = {k: lay(f"{F}-{k}", c) for k, c in (("A-OUTLINE", 6), ("A-WALL", 9), ("A-SLAB", 8), ("ZONE", 30),
                                            ("X-TEXT", 2), ("A-CHASE", 251))}
    rect(L["A-OUTLINE"], X0, Y0, X1, Y1, e0)
    rect(L["A-WALL"], X0, Y0, X1, Y1, e0)
    rect(L["A-WALL"], X0 + 0.3, Y0 + 0.3, X1 - 0.3, Y1 - 0.3, e0)
    box(L["A-SLAB"], X0, Y0, e0 - 0.3, X1, Y1, e0)
    for (a, b, c, d) in ((X0, Y0, X1, Y0 + 0.3), (X0, Y1 - 0.3, X1, Y1), (X0, Y0, X0 + 0.3, Y1), (X1 - 0.3, Y0, X1, Y1)):
        box(L["A-WALL"], a, b, e0, c, d, e0 + h)
    zid, kind, name = ZONES[f]
    zy0, zy1 = (RY0, RY1) if f == 2 else (Y0, Y1)      # 3층 존 = 전산실 930 m2 (환기 통로 제외)
    rect(L["ZONE"], X0, zy0, X1, zy1, e0)
    text(L["ZONE"], f"{zid} | {kind}", X0 + 1, zy0 + (6.5 if f == 1 else 2.5), e0 + 0.05, 1.0)
    text(L["X-TEXT"], name, X0 + 1, (RY0 if f == 2 else Y0) + (8.2 if f == 1 else 4.2), e0 + 0.05, 0.9)
    if f in (1, 2):   # 북·남 환기 통로 (F3 플레넘 → F2 팬월)
        for yy0, yy1 in ((Y0 + 0.3, RY0), (RY1, Y1 - 0.3)):
            rect(L["A-CHASE"], X0 + 3, yy0, X1 - 3, yy1, e0)
            box(L["A-CHASE"], X0 + 3, yy0, e0, X1 - 3, yy0 + 0.1, e0 + h)
            box(L["A-CHASE"], X0 + 3, yy1 - 0.1, e0, X1 - 3, yy1, e0 + h)

# ---- F3 전산실 ----
e3 = ELEV[2]
LR, LC, LS, LH, LP = (lay("F3-DC-RACK", 3), lay("F3-DC-CDU", 140), lay("F3-DC-SLOT", 8),
                      lay("F3-DC-HAC", 1), lay("F3-M-ERW", 5))
box(lay("F3-A-CEILING", 253), X0 + 0.3, RY0, e3 + FACTS["ceiling_m"][0], X1 - 0.3, RY1, e3 + FACTS["ceiling_m"][0] + 0.05)


def row(y, x_start, n, pattern, face):
    """y: 열 중심, pattern: 위치별 'N'(노드)/'C'(CDU)/'A'(공랭)/'S'(증설), face: +1 = 북쪽이 앞면"""
    for i in range(n):
        x = x_start + RW / 2 + i * RW
        k = pattern[i % len(pattern)]
        if k == "S":
            msp.add_blockref("SLOT_600x1200", Vec3(x, y, e3), dxfattribs={"layer": LS})
            count["slot"] += 1
        elif k == "C":
            msp.add_blockref("CDU_UNIT_600x1200x2200", Vec3(x, y, e3), dxfattribs={"layer": LC})
            count["cdu"] += 1
        else:
            kw = NODE_KW if k == "N" else AIR_KW
            tag = "N" if k == "N" else "A"
            count["node" if k == "N" else "air"] += 1
            br = msp.add_blockref("RACK_600x1200x2200", Vec3(x, y, e3), dxfattribs={"layer": LR})
            br.add_auto_attribs({"RACK_ID": f"{tag}{count['node' if k == 'N' else 'air']:02d}", "KW": f"{kw:g}"})


def contained(y_a, y_b, x0, x1):
    """두 열 사이 열복도 차폐 (천장까지)"""
    lo, hi = min(y_a, y_b) + RD / 2, max(y_a, y_b) - RD / 2
    rect(LH, x0, lo, x1, hi, e3)
    box(LH, x0, lo, e3 + RH, x1, hi, e3 + FACTS["ceiling_m"][0])
    for xx in (x0, x1):
        box(LH, xx - 0.03, lo, e3, xx + 0.03, hi, e3 + RH)


# 액체냉각 Peregrine: 2열 x 14 (노드 9 + CDU 5), 뒷면끼리 마주보는 열복도 1.2 m
LIQ = "NCNNCNNCNNCNNC"
yl1, yl2 = 4.0, 4.0 + RD + 1.2
xl0 = -11.0
row(yl1, xl0, 14, LIQ, -1)
row(yl2, xl0, 14, LIQ, +1)
contained(yl1, yl2, xl0, xl0 + 14 * RW)
# 증설 자리 (2.5 MW 단위 증설, 액체 열 옆)
xs0 = xl0 + 14 * RW + 2.4
row(yl1, xs0, 14, "S", -1)
row(yl2, xs0, 14, "S", +1)
# 공랭 2열 x 25
ya1, ya2 = -7.0, -7.0 + RD + 1.2
xa0 = -11.0
row(ya1, xa0, 25, "A", -1)
row(ya2, xa0, 25, "A", +1)
contained(ya1, ya2, xa0, xa0 + 25 * RW)
text(lay("F3-X-TEXT", 2), "PEREGRINE (LIQUID, 18 NODE + 10 CDU)", xl0, yl2 + 1.5, e3 + 0.05, 0.6)
text("F3-X-TEXT", "FUTURE (2.5 MW STEPS)", xs0, yl2 + 2.5, e3 + 0.05, 0.6)
text("F3-X-TEXT", "AIR-COOLED 50 x 3 kW (HOT AISLE CONTAINED)", xa0, ya2 + 1.5, e3 + 0.05, 0.6)

# ERW 배관: 동측 입상관 (공급·환수 350 mm), 액체 열 아래 분기 (가정 경로)
xr, yr = X1 - 2.0, 0.0
for dx, nm in ((0.0, "S"), (0.8, "R")):
    pipe(LP, (xr + dx, yr, ELEV[1] + 1.0), (xr + dx, yr, ELEV[3] + 1.5), 0.35)
    pipe(LP, (xr + dx, yr, e3 + 0.3), (xr + dx, (yl1 + yl2) / 2, e3 + 0.3), 0.20)
    pipe(LP, (xl0, (yl1 + yl2) / 2 + dx * 0.3, e3 + 0.3), (xr + dx, (yl1 + yl2) / 2 + dx * 0.3, e3 + 0.3), 0.20)
text("F3-X-TEXT", "ERW RISER 350 mm S/R", xr - 6, yr - 1.5, e3 + 0.05, 0.5)

# ---- F2 기계실 ----
e2 = ELEV[1]
M2 = lay("F2-M-EQUIP", 30)
FW = lay("F2-M-FANARRAY", 4)
for yy in (Y0 + 0.4, Y1 - 1.4):            # 팬월: 통로 아래 (가정)
    box(FW, -12.0, yy, e2, 12.0, yy + 1.0, e2 + 3.0)
    rect(FW, -12.0, yy, 12.0, yy + 1.0, e2)
text("F2-X-TEXT", "FAN WALL (N)", -4, Y1 - 3.0, e2 + 0.05, 0.6)
text("F2-X-TEXT", "FAN WALL (S)", -4, Y0 + 1.8, e2 + 0.05, 0.6)
box(M2, 4.0, -2.0, e2, 7.0, -1.0, e2 + 2.5); text("F2-X-TEXT", "HX ERW-PHW", 4.0, -3.0, e2 + 0.05, 0.5)
for i in range(3):
    box(M2, 8.5 + i * 2.0, -2.0, e2, 9.9 + i * 2.0, -1.0, e2 + 1.5)
text("F2-X-TEXT", "ERW PUMPS 2+1", 8.5, -3.0, e2 + 0.05, 0.5)
for i in range(6):
    box(M2, -12.0 + i * 1.4, 3.0, e2, -10.8 + i * 1.4, 3.6, e2 + 2.2)
text("F2-X-TEXT", "480 V POWER DISTRIBUTION", -12.0, 4.3, e2 + 0.05, 0.5)
# ---- F1 펌프실 ----
e1 = ELEV[0]
M1 = lay("F1-M-EQUIP", 30)
for i in range(2):
    box(M1, 2.0 + i * 2.2, -4.0, e1, 3.5 + i * 2.2, -3.0, e1 + 1.5)
text("F1-X-TEXT", "PHW PUMPS (TO CAMPUS HEATING)", 2.0, -5.2, e1 + 0.05, 0.5)
# ---- F4 기계실 ----
e4 = ELEV[3]
M4 = lay("F4-M-EQUIP", 30)
box(M4, 4.0, 2.0, e4, 7.0, 3.0, e4 + 2.5); text("F4-X-TEXT", "HX ERW-TOWER WATER", 4.0, 0.8, e4 + 0.05, 0.5)
for i in range(4):
    forms.cylinder(count=20, radius=1.1, top_center=(0, 0, 2.4)).translate(-10.0 + i * 3.0, -6.0, e4) \
        .render_mesh(msp, dxfattribs={"layer": M4})
text("F4-X-TEXT", "TOWER SUMP TANKS x4", -11.0, -8.2, e4 + 0.05, 0.5)

# ---- 지붕·옥상 플랫폼 ----
top = ELEV[4]
box(lay("ROOF-A-ROOF", 253), X0, Y0, top, X1, Y1, top + 0.3)
TW = 3.7
lay("ROOF-M-TOWER", 160); lay("ROOF-M-LABCOOLING", 8); lay("ROOF-M-TSC", 140); lay("ROOF-X-TEXT", 2)
for i in range(7):
    x = X0 + 1.5 + i * (TW + 0.4)
    layer = "ROOF-M-TOWER" if i < 4 else "ROOF-M-LABCOOLING"
    y = Y1 - 2.0 - TW
    box(layer, x, y, top + 0.3, x + TW, y + TW, top + 0.3 + 4.0)
    rect(layer, x, y, x + TW, y + TW, top + 0.3)
    forms.cylinder(count=20, radius=TW * 0.36, top_center=(0, 0, 0.8)).translate(x + TW / 2, y + TW / 2, top + 4.3) \
        .render_mesh(msp, dxfattribs={"layer": layer})
text("ROOF-X-TEXT", "HPC COOLING TOWERS x4", X0 + 1.5, Y1 - 1.2, top + 4.6, 0.7)
text("ROOF-X-TEXT", "LAB/OFFICE TOWERS x3", X0 + 1.5 + 4 * (TW + 0.4), Y1 - 1.2, top + 4.6, 0.7)
box("ROOF-M-TSC", -6.0, -4.0, top + 0.3, 6.0, -1.6, top + 2.9)
rect("ROOF-M-TSC", -6.0, -4.0, 6.0, -1.6, top + 0.3)
text("ROOF-X-TEXT", "THERMOSYPHON COOLER (2016)", -6.0, -5.2, top + 3.2, 0.7)
pipe(lay("ROOF-M-TSC-PIPE", 5), (xr, yr, ELEV[3] + 1.5), (xr, yr, top + 1.0), 0.15)
pipe("ROOF-M-TSC-PIPE", (xr, yr, top + 1.0), (6.0, -2.8, top + 1.0), 0.15)

# ---- 주변 매스 (가져오기 무시) ----
CO, CL = lay("CTX-OFFICE-BAR", 252), lay("CTX-HIGHBAY-LAB", 252)
od = FACTS["office_depth_m"][0]
box(CO, -55, Y1, 0, 55, Y1 + od, FACTS["height_3st_m"][0]); rect(CO, -55, Y1, 55, Y1 + od, 0)
box(CL, -55, Y0 - 45, 0, 55, Y0, 12.0); rect(CL, -55, Y0 - 45, 55, Y0, 0)
text(lay("CTX-X-TEXT", 2), "OFFICE BAR (3 STORIES, 60 FT DEEP)", -50, Y1 + od / 2, FACTS["height_3st_m"][0] + 0.5, 2.0)
text("CTX-X-TEXT", "HIGH BAY LABS (14 LABS)", -50, Y0 - 22, 12.5, 2.0)
text("CTX-X-TEXT", "N ↑", 50, Y1 + od + 4, 0, 3.0)

doc.set_modelspace_vport(height=140, center=(0, 0))
doc.saveas(OUT / "esif_approx.dxf")
meta = {"facts": {k: {"value": v, "source": s} for k, (v, s) in FACTS.items()}, "assumed": ASSUMED,
        "geometry": {"block_ew_m": EW, "block_ns_m": NS, "room_ew_m": EW, "room_ns_m": NS_ROOM,
                     "floor_heights_m": FH, "racks": count, "node_kw": NODE_KW, "air_kw": AIR_KW,
                     "it_design_kw": round(count["node"] * NODE_KW + count["air"] * AIR_KW, 1)}}
(OUT / "esif_approx_facts.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(meta["geometry"], ensure_ascii=False))
