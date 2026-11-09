r"""
샘플 DXF 도면 생성 (테스트·화면 확인용, 실제 건물 아님).
  python -m hvac.cad_sample data\cad\sample_dc.dxf [--rotate 30]
mm 단위, 도면 +Y = 북. 60 x 50 m 건물(외벽 이중선), 랙 8열 x 30개(블록 + RACK_ID/KW 속성),
CRAH 남·북 각 5대, 남·북 창, 동측 12 m 떨어진 칠러·냉각탑.
"""
import argparse
import math

import ezdxf

RACK_W, RACK_D, AISLE = 600, 1200, 2400


def make(path, rotate_deg=0.0, rows=8, per_row=30):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for name in ("A-WALL", "A-GLAZ", "E-RACK", "M-CRAH", "M-CHLR", "M-CT", "A-ANNO"):
        doc.layers.add(name)
    c, s = math.cos(math.radians(rotate_deg)), math.sin(math.radians(rotate_deg))
    R = lambda x, y: (x * c - y * s, x * s + y * c)

    rack = doc.blocks.new("RACK_42U")
    rack.add_lwpolyline([(0, 0), (RACK_W, 0), (RACK_W, RACK_D), (0, RACK_D)], close=True)
    rack.add_attdef("RACK_ID", (50, 500), dxfattribs={"height": 100})
    rack.add_attdef("KW", (50, 300), dxfattribs={"height": 100})
    crah = doc.blocks.new("CRAH_UNIT")
    crah.add_lwpolyline([(-1300, -500), (1300, -500), (1300, 500), (-1300, 500)], close=True)

    W, H = 60000, 50000
    for off in (0, 300):     # 외벽 이중선 (바깥 / 안쪽)
        msp.add_lwpolyline([R(off, off), R(W - off, off), R(W - off, H - off), R(off, H - off)], close=True,
                           dxfattribs={"layer": "A-WALL"})
    for x0 in (8000, 18000, 28000, 38000, 48000):
        msp.add_line(R(x0, 0), R(x0 + 2000, 0), dxfattribs={"layer": "A-GLAZ"})
    for x0 in (15000, 30000, 45000):
        msp.add_line(R(x0, H), R(x0 + 2000, H), dxfattribs={"layer": "A-GLAZ"})

    pitch = RACK_D + AISLE
    y0 = H / 2 - (rows - 1) * pitch / 2 - RACK_D / 2
    x0 = W / 2 - per_row * RACK_W / 2
    for j in range(rows):
        for i in range(per_row):
            # 랙 id 는 북쪽 열부터 A, B, ... (3D 좌표계 열 번호 0 = 가장 북쪽 열과 같은 순서)
            ref = msp.add_blockref("RACK_42U", R(x0 + i * RACK_W, y0 + j * pitch),
                                   dxfattribs={"layer": "E-RACK", "rotation": rotate_deg})
            kw = 6 + 2 * (j % 4)
            ref.add_auto_attribs({"RACK_ID": f"{chr(ord('A') + rows - 1 - j)}{i + 1:02d}", "KW": f"{kw}"})
    for k in range(5):
        x = 6000 + k * (W - 12000) / 4
        for y in (900, H - 900):
            msp.add_blockref("CRAH_UNIT", R(x, y), dxfattribs={"layer": "M-CRAH", "rotation": rotate_deg})
    msp.add_lwpolyline([R(W + 12000, 19000), R(W + 20000, 19000), R(W + 20000, 22000), R(W + 12000, 22000)],
                       close=True, dxfattribs={"layer": "M-CHLR"})
    msp.add_lwpolyline([R(W + 12500, 30000), R(W + 19500, 30000), R(W + 19500, 37000), R(W + 12500, 37000)],
                       close=True, dxfattribs={"layer": "M-CT"})
    msp.add_text("SAMPLE DATA HALL (가상 도면)", dxfattribs={"layer": "A-ANNO", "height": 800}).set_placement(R(2000, H + 2000))
    doc.saveas(path)
    return path


def make_it_sample(path, rows=8, per_row=30, days=3):
    """make() 도면의 랙과 짝이 맞는 가상 IT 계측 CSV (한글 헤더, W 단위 표기 없음, 1시간 간격).
    랙 id 는 'A-01' 처럼 도면(A01)과 표기를 다르게 해 id 정규화 매칭을 확인할 수 있게 함"""
    lines = ["IT 부하 계측 내보내기 (가상 데이터)", "측정일시,랙번호,소비전력"]
    for d in range(days):
        for h in range(24):
            shape = 0.72 + 0.13 * math.sin(math.pi * (h - 8) / 12) * (h >= 8 and h <= 20) + 0.03 * d
            for j in range(rows):
                design = 6 + 2 * ((rows - 1 - j) % 4)
                for i in range(per_row):
                    w = design * 1000 * shape * (0.9 + 0.2 * ((i * 7 + j * 3) % 10) / 10)
                    lines.append(f"2026-08-{d + 1:02d} {h:02d}:00,{chr(ord('A') + j)}-{i + 1:02d},{w:.0f}")
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--rotate", type=float, default=0.0)
    ap.add_argument("--it", help="짝이 맞는 IT 계측 CSV 경로 (선택)")
    a = ap.parse_args()
    print(make(a.out, a.rotate))
    if a.it:
        print(make_it_sample(a.it))
