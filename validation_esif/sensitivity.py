"""칠러 필요 시간(실제 0 h) 오차 원인 분해: 고도 습구 보정, 접근온도 가정 — 엔진과 같은 판정식(free_fraction)으로 계산"""
import json, math, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "backend"))
from hvac import weather as wx
from hvac.engine import wet_bulb, p_sat

def wb_psy(t, rh, p_kpa):
    """ASHRAE 습공기식, 대기압 p 에서의 습구온도 (이분법)"""
    pv = rh / 100 * p_sat(t); w = 0.622 * pv / (p_kpa - pv)
    lo, hi = -40.0, t
    for _ in range(60):
        tw = (lo + hi) / 2; ws = 0.622 * p_sat(tw) / (p_kpa - p_sat(tw))
        w_tw = ((2501 - 2.326 * tw) * ws - 1.006 * (t - tw)) / (2501 + 1.86 * t - 4.186 * tw)
        (lo, hi) = (tw, hi) if w_tw < w else (lo, tw)
    return tw

P_SITE = 101.325 * (1 - 2.25577e-5 * 1829) ** 5.25588   # 표준대기, 고도 1,829 m (EPW 표기)
text = (HERE / "golden_tmy3.epw").read_text(encoding="latin-1")
hrs = []
for line in text.splitlines()[8:]:
    c = line.split(",")
    hrs.append((float(c[6]), float(c[8])))
def chiller_hours(wb_fn, tower=4.0, hx=1.5, cdu=3.0, tcs_s=24.0):
    fws = tcs_s - cdu
    n_liq = sum(1 for t, rh in hrs if wb_fn(t, rh) + tower + hx > fws)
    return n_liq
sea = lambda t, rh: wet_bulb(t, rh)
alt = lambda t, rh: wb_psy(t, rh, P_SITE)
diff = [sea(t, rh) - alt(t, rh) for t, rh in hrs if t > 20]
rows = {
 "기본 (해수면 습구, 냉각탑 4 K + 열교환기 1.5 K + CDU 3 K)": chiller_hours(sea),
 "고도 1,829 m 습구로 보정": chiller_hours(alt),
 "CDU 접근 3 → 1.5 K": chiller_hours(sea, cdu=1.5),
 "고도 보정 + CDU 1.5 K": chiller_hours(alt, cdu=1.5),
 "고도 보정 + CDU 1.5 K + 냉각탑 접근 3 K": chiller_hours(alt, tower=3.0, cdu=1.5),
}
out = {"site_pressure_kpa": round(P_SITE, 2), "wet_bulb_bias_when_t_gt_20c_mean_k": round(sum(diff) / len(diff), 2),
       "max_wet_bulb_sea_c": round(max(sea(t, h) for t, h in hrs), 2), "max_wet_bulb_alt_c": round(max(alt(t, h) for t, h in hrs), 2),
       "tmy3_chiller_needed_hours": rows}
(HERE / "out" / "sensitivity.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
