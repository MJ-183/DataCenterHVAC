"""비교 그래프 (out/*.png). esif_compare.py · sensitivity.py 실행 후"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

OUT = Path(__file__).resolve().parent / "out"
for f in font_manager.findSystemFonts():
    if any(k in f for k in ("NotoSansCJK", "malgun", "Malgun", "NanumGothic")):
        font_manager.fontManager.addfont(f)
names = {x.name for x in font_manager.fontManager.ttflist}
for n in ("Noto Sans CJK KR", "Malgun Gothic", "NanumGothic"):
    if n in names:
        plt.rcParams["font.family"] = n
        break
plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": .3,
                     "axes.unicode_minus": False, "font.size": 10})
MEAS, SIM_A, SIM_B = "#222831", "#9aa5b1", "#1f6fd1"

s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
A = pd.read_csv(OUT / "monthly_A_default.csv", index_col=0)
B = pd.read_csv(OUT / "monthly_B_calibrated.csv", index_col=0)
C = pd.read_csv(OUT / "monthly_C_test2019.csv", index_col=0)

# 1. 월별 PUE
fig, ax = plt.subplots(figsize=(8, 3.4), dpi=150)
x = range(len(A))
ax.plot(x, A.meas_pue, "-o", color=MEAS, lw=2, ms=4, label="실측 ESIF")
ax.plot(x, A.sim_pue, "--o", color=SIM_A, lw=2, ms=4, label="시뮬 A: 공개 사실 + 기본 계수")
ax.plot(x, B.sim_pue, "-o", color=SIM_B, lw=2, ms=4, label="시뮬 B: 계수 비례 보정")
ax.set_xticks(list(x), A.index, rotation=45, ha="right")
ax.set_ylabel("월 PUE")
ax.set_title("월별 PUE — 2016-09 ~ 2017-08 (NREL 공개 1차년도 기간)", loc="left", fontsize=11)
ax.legend(frameon=False, fontsize=9, ncol=3, loc="upper left")
ax.set_ylim(1.02, 1.055)
fig.tight_layout(); fig.savefig(OUT / "fig1_monthly_pue.png"); plt.close(fig)

# 2. 항목별 평균 전력
r = s["runs"]
items = [("tower_vs_cooling", "냉각탑계\n(cooling_kw)"), ("pump", "펌프\n(pump_kw)"), ("fan_vs_hvac", "공조 팬\n(hvac_kw)")]
fig, ax = plt.subplots(figsize=(8, 3.4), dpi=150)
w = .26
for i, (k, lab) in enumerate(items):
    vals = [r["A_default_cal"][k]["measured_mean_kw"], r["A_default_cal"][k]["sim_mean_kw"], r["B_calibrated_cal"][k]["sim_mean_kw"]]
    for j, (v, c) in enumerate(zip(vals, (MEAS, SIM_A, SIM_B))):
        ax.bar(i + (j - 1) * w, v, w * .92, color=c, label=["실측", "시뮬 A (기본 계수)", "시뮬 B (보정)"][j] if i == 0 else None)
        ax.text(i + (j - 1) * w, v + .4, f"{v:.1f}", ha="center", fontsize=8.5)
ax.set_xticks(range(3), [l for _, l in items])
ax.set_ylabel("기간 평균 전력 [kW]")
ax.set_title(f"냉각 설비 전력 항목별 평균 — IT 평균 {r['A_default_cal']['it_mean_kw']:,.0f} kW", loc="left", fontsize=11)
ax.legend(frameon=False, fontsize=9)
fig.tight_layout(); fig.savefig(OUT / "fig2_components.png"); plt.close(fig)

# 3. 검증 연도 2019: 위 IT, 아래 기계 전력 (축 하나씩)
fig, (ax0, ax) = plt.subplots(2, 1, figsize=(8, 4.4), dpi=150, sharex=True, gridspec_kw={"height_ratios": [1, 2]})
x = range(len(C))
ax0.plot(x, C.it_power_kw, "-", color="#7a8796", lw=2)
ax0.axhline(A.it_power_kw.mean(), color="#7a8796", ls=":", lw=1)
ax0.text(len(C) - 1, A.it_power_kw.mean() + 20, f"보정 기간 평균 {A.it_power_kw.mean():,.0f} kW", ha="right", fontsize=8.5, color="#5b6573")
ax0.set_ylabel("IT [kW]"); ax0.set_ylim(700, 1500)
ax0.set_title("검증 연도 2019 — 보정 계수 그대로 적용 (IT 부하 약 1.3배)", loc="left", fontsize=11)
ax.plot(x, C.meas_mech_kw, "-o", color=MEAS, lw=2, ms=4, label="실측 기계 전력 합계")
ax.plot(x, C.sim_mech_kw, "-o", color=SIM_B, lw=2, ms=4, label="시뮬 C (B 계수를 2019 에 적용)")
ax.set_xticks(list(x), C.index, rotation=45, ha="right")
ax.set_ylabel("월 평균 [kW]")
ax.legend(frameon=False, fontsize=9, loc="upper left")
fig.tight_layout(); fig.savefig(OUT / "fig3_test2019.png"); plt.close(fig)

# 4. 칠러 필요 시간 민감도
sv = json.loads((OUT / "sensitivity.json").read_text(encoding="utf-8"))["tmy3_chiller_needed_hours"]
fig, ax = plt.subplots(figsize=(8, 3.0), dpi=150)
labs = list(sv)[::-1]; vals = [sv[k] for k in labs]
ax.barh(range(len(vals)), vals, color=[SIM_B if i == 0 else SIM_A for i in range(len(vals))][::-1])
for i, v in enumerate(vals):
    ax.text(v + 5, i, f"{v} h", va="center", fontsize=9)
ax.set_yticks(range(len(vals)), labs, fontsize=8.5)
ax.set_xlabel("연간 칠러 필요 시간 [h] (실제 ESIF = 0 h, 칠러 없음)")
ax.set_title("칠러 필요 시간 오차의 분해 (TMY3 기상)", loc="left", fontsize=11)
ax.grid(axis="y", visible=False)
fig.tight_layout(); fig.savefig(OUT / "fig4_chiller_hours.png"); plt.close(fig)
print("saved figs")

# 5. 항목 x 실행 소분할: 칸마다 '실측 vs 그 시뮬' 두 선만, 범례도 칸마다 따로
ITEMS = [("cooling_kw", "sim_tower_kw", "냉각탑계 [kW]"), ("pump_kw", "sim_pump_kw", "펌프 [kW]"),
         ("hvac_kw", "sim_fan_kw", "공조 팬 [kW]"), ("meas_pue", "sim_pue", "월 PUE")]
RUNS = [(A, "시뮬 A (기본 계수)", SIM_A, "보정 기간 2016-09~2017-08"),
        (B, "시뮬 B (보정)", SIM_B, "보정 기간 2016-09~2017-08"),
        (C, "시뮬 C (B 계수, 2019)", "#d9822b", "검증 기간 2019")]
fig, axs = plt.subplots(len(ITEMS), len(RUNS), figsize=(12, 10), dpi=150, sharey="row")
for i, (mc, sc_, lab) in enumerate(ITEMS):
    for jx, (df, name, col, per) in enumerate(RUNS):
        ax = axs[i][jx]
        x = range(len(df))
        ax.plot(x, df[mc], "-o", color=MEAS, lw=1.8, ms=3, label="실측")
        ax.plot(x, df[sc_], "-o", color=col, lw=1.8, ms=3, label=name)
        err = (df[sc_].mean() - df[mc].mean()) / df[mc].mean() * 100 if mc != "meas_pue" else (df[sc_].mean() - df[mc].mean())
        ax.set_title((f"평균 차 {err:+.1f}%" if mc != "meas_pue" else f"평균 차 {err:+.4f}"),
                     loc="right", fontsize=8.5, color="#5b6573")
        ax.legend(frameon=True, framealpha=.85, edgecolor="none", fontsize=8, loc="best")
        ax.set_xticks(list(x)[::2], [v[2:] for v in df.index][::2], fontsize=7.5)
        if jx == 0:
            ax.set_ylabel(lab)
        if i == 0:
            ax.set_title(f"{name}\n{per}", fontsize=10, loc="left", pad=14)
fig.suptitle("항목별 · 실행별 비교 (월 평균) — 칸마다 실측과 해당 시뮬만", x=.01, ha="left", fontsize=12)
fig.tight_layout(); fig.savefig(OUT / "fig5_split_compare.png"); plt.close(fig)
print("saved fig5")
