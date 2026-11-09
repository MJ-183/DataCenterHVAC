"""esif_approx.dxf 층별 평면 미리보기 (cad/preview_esif.png)"""
from pathlib import Path
import ezdxf, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy
for f in font_manager.findSystemFonts():
    if "NotoSansCJK" in f or "malgun" in f.lower():
        font_manager.fontManager.addfont(f)
for n in ("Noto Sans CJK KR", "Malgun Gothic"):
    if n in {x.name for x in font_manager.fontManager.ttflist}:
        plt.rcParams["font.family"] = n; break
D = Path(__file__).resolve().parent
doc = ezdxf.readfile(D / "esif_approx.dxf")
msp = doc.modelspace()
panels = [("F1 펌프실 (최하층)", "F1-"), ("F2 기계실: 팬월·판형 열교환기·ERW 펌프·전력 분배", "F2-"),
          ("F3 HPC 전산실 930 m²: 랙·CDU·열복도 차폐·증설 자리", "F3-"), ("F4 기계실 + 옥상: 냉각탑 7기·열사이펀", ("F4-", "ROOF-"))]
fig, axs = plt.subplots(2, 2, figsize=(13, 13.6), dpi=150)
cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
for ax, (title, pre) in zip(axs.flat, panels):
    pre = pre if isinstance(pre, tuple) else (pre,)
    ents = [e for e in msp if e.dxf.layer.startswith(pre) and e.dxftype() in ("LWPOLYLINE", "INSERT", "TEXT", "MESH")
            and not (e.dxftype() == "MESH" and ("WALL" in e.dxf.layer or "SLAB" in e.dxf.layer or "CHASE" in e.dxf.layer
                                                or "ROOF-A" in e.dxf.layer or "HAC" in e.dxf.layer or "CEILING" in e.dxf.layer))]
    ctx = RenderContext(doc)
    Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_entities(ents)
    ax.set_title(title, fontsize=11, loc="left")
    ax.set_xlim(-17, 17); ax.set_ylim(-18.5, 18.5); ax.set_aspect("equal")
    ax.text(15.5, 17, "N↑", ha="right", fontsize=11)
    ax.plot([-15, -5], [-17.8, -17.8], color="k", lw=2); ax.text(-10, -17.4, "10 m", ha="center", fontsize=8)
fig.suptitle("NREL ESIF HPC 데이터센터 근사 도면 — 공개 자료 + 가정 (실제 설계 도면 아님)", x=.02, ha="left", fontsize=13)
fig.tight_layout(); fig.savefig(D / "preview_esif.png"); print("ok")
