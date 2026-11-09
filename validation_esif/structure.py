"""보정 후 남는 오차가 '계수'가 아니라 '모델 구조' 문제인지 확인:
엔진(전력 = 계수 x 부하, 비례)과 '고정분 + 부하 비례 + 습구 항' 회귀를 2016-09~2017-08 로 맞춘 뒤 2019 에 적용해 비교"""
import json
from pathlib import Path
import numpy as np, pandas as pd
OUT = Path(__file__).resolve().parent / "out"
cal = pd.read_csv(OUT / "hourly_B.csv", parse_dates=["timestamp_mst"]).set_index("timestamp_mst")
tst = pd.read_csv(OUT / "hourly_C.csv", parse_dates=["timestamp_mst"]).set_index("timestamp_mst")
def g14(m, s):
    n = len(m); mb = m.mean()
    return round((m - s).sum() / ((n - 1) * mb) * 100, 1), round(np.sqrt(((m - s) ** 2).sum() / (n - 1)) / mb * 100, 1)
def X(d): return np.column_stack([np.ones(len(d)), d.it_power_kw, d.wet_bulb_c.clip(lower=0), (-d.dry_bulb_c).clip(lower=0)])
res = {}
for name, mc, sc in (("냉각탑계(cooling_kw)", "cooling_kw", "sim_tower_kw"), ("펌프(pump_kw)", "pump_kw", "sim_pump_kw"),
                     ("공조 팬(hvac_kw)", "hvac_kw", "sim_fan_kw"), ("기계 합계", "meas_mech_kw", "sim_mech_kw")):
    beta, *_ = np.linalg.lstsq(X(cal), cal[mc], rcond=None)
    pred = X(tst) @ beta
    fixed_share = beta[0] / cal[mc].mean()
    res[name] = {"engine_calibrated_2019": dict(zip(("nmbe_pct", "cv_rmse_pct"), g14(tst[mc], tst[sc]))),
                 "affine_2019": dict(zip(("nmbe_pct", "cv_rmse_pct"), g14(tst[mc], pd.Series(pred, index=tst.index)))),
                 "coef": {"fixed_kw": round(beta[0], 2), "per_it_kw": round(beta[1], 5),
                          "per_wb_k_above0": round(beta[2], 3), "per_k_below0_dry": round(beta[3], 3)},
                 "fixed_share_of_mean_cal": round(fixed_share, 2)}
(OUT / "structure.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1))
