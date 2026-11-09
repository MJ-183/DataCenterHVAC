"""시뮬 A → 시뮬 B 보정 단계 상세 (out/calibration_steps.md, .json). esif_compare.py 실행 후"""
import json
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "out"
s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
A, B, C = (s["runs"][k] for k in ("A_default_cal", "B_calibrated_cal", "C_calibrated_test2019"))
hA = pd.read_csv(OUT / "hourly_A.csv")
hB = pd.read_csv(OUT / "hourly_B.csv")
new = B["coefficients"]
# A 에 쓰인 값: 프로젝트 엔진 기본값 (engine.PLANT_DEFAULTS) + scenario_sample 의 crah_fan_heat_ratio
old = {"tower_fan_kw_per_kw": 0.006, "chw_pump_kw_per_kw": 0.015, "cw_pump_kw_per_kw": 0.012,
       "liquid_pump_kw_per_kw": 0.02, "crah_fan_heat_ratio": 0.05}


def coupling_text(steps, B, mt, hB):
    res = {st["item"]: B[k]["sim_mean_kw"] - st["measured_mean_kw"] for st, k in zip(steps, ("tower_vs_cooling", "pump", "fan_vs_hvac"))}
    parts = ", ".join(f"{n} {v:+.2f} kW" for n, v in res.items())
    t = f"B 의 항목별 잔차는 {parts} 입니다."
    fan_up = [c for st in steps for c in st["coefficients"] if c["name"] == "crah_fan_heat_ratio"][0]
    if any(abs(v) >= 0.005 for n, v in res.items() if n != "공조 팬"):
        t += (" 팬 발열비를 바꾸면 팬 발열이 공기측 부하와 방열량(q_rej)에 더해지거나 빠져, 방열량에 비례하는 냉각탑 팬·냉각수 펌프도 "
              f"따라 변합니다(계수끼리의 결합, 이번 팬 발열비 {fan_up['A']} → {fan_up['B']}).")
    t += (f" 기계 합계의 나머지 {mt['sim_mean_kw'] - mt['measured_mean_kw']:+.2f} kW 는 대부분 보정하지 않은 칠러 전력"
          f"({hB.sim_chiller_kw.mean():.2f} kW)입니다.")
    return t


def offset_text(steps, A, it):
    d = {st["item"]: st["sim_A_mean_kw"] - st["measured_mean_kw"] for st in steps}
    pos = [n for n, v in d.items() if v > 0.05]; neg = [n for n, v in d.items() if v < -0.05]
    gross = sum(abs(v) for v in d.values()) / it
    t = f"A 의 PUE 오차는 {A['pue']['diff']:+.4f} 이고, 항목별 오차 절대값을 더하면 {gross:.4f} 입니다."
    if pos and neg:
        t += f" {'·'.join(pos)} 과대(+)와 {'·'.join(neg)} 과소(−)가 일부 상쇄돼 PUE 오차가 실제 항목 오차보다 작게 보입니다."
    t += " PUE 하나만 맞는다고 항목이 맞는 것은 아닙니다."
    return t


GROUPS = [
    ("냉각탑계", "tower_vs_cooling", "cooling_kw", ["tower_fan_kw_per_kw"],
     "냉각탑 팬 = 계수 × 방열량(q_rej)"),
    ("펌프", "pump", "pump_kw", ["liquid_pump_kw_per_kw", "cw_pump_kw_per_kw", "chw_pump_kw_per_kw"],
     "펌프 = 액체 계수 × 액체 부하 + 냉각수 계수 × 방열량 + 냉수 계수 × 공기 부하 (세 계수에 같은 비율)"),
    ("공조 팬", "fan_vs_hvac", "hvac_kw", ["crah_fan_heat_ratio"],
     "CRAH 팬 = 발열비 × 공기측 부하(액체 회수분 제외)"),
]
it = A["it_mean_kw"]
steps, md = [], []
md += ["# 시뮬 A → 시뮬 B 보정 단계", "",
       f"보정 기간 {B['period'][0]} ~ {B['period'][1]} ({A['hours']:,} h), IT 평균 {it:,.1f} kW.", "",
       "## 0. 무엇을 바꾸고 무엇을 두는가",
       "- **바꾸는 것:** 공개 자료가 없어 프로젝트 기본값을 쓴 보조동력 계수 5개 (냉각탑 팬, 펌프 3종, CRAH 팬 발열비)",
       "- **두는 것:** 공개 사실(액체 회수 95%, 24/35 ℃, IT 실측), 접근온도, 칠러 판정 → 칠러 필요 시간은 보정해도 그대로 "
       f"({A['chiller_hours_sim']} h → {B['chiller_hours_sim']} h)",
       "- **방법:** 항목별 기간 평균을 실측에 맞추는 비례 보정 1회. 엔진에서 각 전력이 계수에 비례하므로 계수 × (실측 평균 ÷ 시뮬 평균) 이면 평균이 맞는다",
       "- 프로젝트 BMS 보정(Phase 12, Levenberg–Marquardt)과 달리 시간별 잔차를 최소화하지 않는다. 오차가 '계수' 문제인지 '구조' 문제인지 가르는 것이 목적", "",
       "## 1. 시뮬 A 의 항목별 차이", "",
       "| 항목 | 실측 평균 kW | 시뮬 A 평균 kW | 비율 r = 실측 ÷ 시뮬 | 계산식 |", "|---|---|---|---|---|"]
for name, key, mcol, coefs, formula in GROUPS:
    m, a = A[key]["measured_mean_kw"], A[key]["sim_mean_kw"]
    r = new[coefs[0]] / old[coefs[0]]   # 반올림 전 평균의 비율 (= 실제 적용한 배율)
    md.append(f"| {name} | {m:.2f} | {a:.2f} | {r:.3f} | {formula} |")
    steps.append({"item": name, "measured_mean_kw": m, "sim_A_mean_kw": a, "ratio": round(r, 4),
                  "coefficients": [{"name": c, "A": old[c], "B": new[c]} for c in coefs],
                  "sim_B_mean_kw": B[key]["sim_mean_kw"]})
ch_a = round(hA.sim_chiller_kw.mean(), 2)
md += [f"| 칠러 (참고) | 0 | {ch_a:.2f} | 보정 안 함 | 칠러 판정은 접근온도·습구식 문제 (README 4절 3번) |", ""]

md += ["## 2. 계수 조정", "", "| 계수 | A (기본값) | × r | B (보정값) |", "|---|---|---|---|"]
for st in steps:
    for c in st["coefficients"]:
        md.append(f"| {c['name']} | {c['A']} | × {st['ratio']:.3f} | {c['B']} |")
md += ["", "## 3. 다시 실행해 확인 (시뮬 B)", "",
       "| 항목 | 실측 | 시뮬 A | 시뮬 B | B 잔차 | B NMBE / CV(RMSE) [%] |", "|---|---|---|---|---|---|"]
for (name, key, *_), st in zip(GROUPS, steps):
    b = B[key]
    md.append(f"| {name} | {st['measured_mean_kw']:.2f} | {st['sim_A_mean_kw']:.2f} | {b['sim_mean_kw']:.2f} | "
              f"{b['sim_mean_kw'] - st['measured_mean_kw']:+.2f} | {b['nmbe_pct']} / {b['cv_rmse_pct']} |")
mt = B["mech_total"]
md += [f"| 기계 합계 (칠러 포함) | {A['mech_total']['measured_mean_kw']:.2f} | {A['mech_total']['sim_mean_kw']:.2f} | "
       f"{mt['sim_mean_kw']:.2f} | {mt['sim_mean_kw'] - mt['measured_mean_kw']:+.2f} | {mt['nmbe_pct']} / {mt['cv_rmse_pct']} |", "",
       coupling_text(steps, B, mt, hB), "",
       "## 4. PUE 변화의 분해", "",
       f"PUE: 실측 {A['pue']['measured']} / A {A['pue']['sim']} / B {B['pue']['sim']}. 항목별 기여 = 평균 전력 차 ÷ IT 평균({it:,.1f} kW).", "",
       "| 항목 | A − 실측 [kW] | PUE 기여 A | B − 실측 [kW] | PUE 기여 B |", "|---|---|---|---|---|"]
for (name, key, *_), st in zip(GROUPS, steps):
    da = st["sim_A_mean_kw"] - st["measured_mean_kw"]; db = B[key]["sim_mean_kw"] - st["measured_mean_kw"]
    md.append(f"| {name} | {da:+.2f} | {da / it:+.4f} | {db:+.2f} | {db / it:+.4f} |")
md += [f"| 칠러 | {ch_a:+.2f} | {ch_a / it:+.4f} | {hB.sim_chiller_kw.mean():+.2f} | {hB.sim_chiller_kw.mean() / it:+.4f} |", "",
       offset_text(steps, A, it), "",
       "## 5. 보정의 한계 — 다른 해(2019)에 적용", "",
       f"같은 B 계수를 IT 평균 {C['it_mean_kw']:,.1f} kW 인 2019 년에 적용하면 기계 합계 NMBE {C['mech_total']['nmbe_pct']}%, "
       f"CV(RMSE) {C['mech_total']['cv_rmse_pct']}% (기준 |NMBE| ≤ 10%, CV ≤ 30%) 로 벗어납니다. "
       f"실측은 IT 가 늘어도 기계 전력이 {A['mech_total']['measured_mean_kw']:.2f} → {C['mech_total']['measured_mean_kw']:.2f} kW 로 덜 늘지만, "
       "엔진은 부하에 비례해 늘기 때문입니다. 비례 계수만으로는 고칠 수 없는 구조 오차입니다(out/structure.json)."]
(OUT / "calibration_steps.md").write_text("\n".join(md) + "\n", encoding="utf-8")
(OUT / "calibration_steps.json").write_text(json.dumps({"steps": steps, "chiller_sim_A_mean_kw": ch_a}, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n".join(md))
