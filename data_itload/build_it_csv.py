"""
코르도바 대학교 데이터센터 실측 전력(10분) → 시간별 IT 부하 CSV
원본: github.com/joaquinjgz/Dataset_university_data_center (Garrido-Zafra 외, 2025)
처리: 유효전력 부호 반전(README: A·B·C상 전류 극성 반대로 측정됨), 현지시각(Europe/Madrid) 변환, 1시간 평균
"""
import pandas as pd, numpy as np
d = pd.read_csv("ucod_power_raw.csv")
d["t"] = pd.to_datetime(d.timestamp, unit="ms", utc=True).dt.tz_convert("Europe/Madrid")
d["p_kw"] = -d.ptotal / 1000.0
d["s_kva"] = d.stotal / 1000.0
s = d.set_index("t")[["p_kw", "s_kva"]]
hr = s.resample("1h").agg(["mean", "count"])
hr.columns = ["p_kw", "n_p", "s_kva", "n_s"]
full_days = hr[hr.n_p >= 5]                       # 10분 자료가 5개 이상인 시간만
print("hours", len(hr), "valid", len(full_days), "missing", (hr.n_p < 5).sum())
h = full_days.copy()
h["local_time"] = h.index.tz_localize(None)
h["hour"] = h.index.hour
h["weekday"] = h.index.dayofweek
# 첫날·마지막날(부분일) 제외
days = h.groupby(h.index.date).size()
keep = days[days == 24].index
h = h[np.isin(h.index.date, keep)]
print("full days", len(keep), keep[0], keep[-1])
out = pd.DataFrame({"timestamp": h["local_time"].dt.strftime("%Y-%m-%d %H:%M"),
                    "it_power_kw": h["p_kw"].round(3), "apparent_kva": h["s_kva"].round(3)})
out.to_csv("it_load_measured_ucordoba_hourly.csv", index=False)

prof = h.groupby("hour").p_kw.mean()
wd = h[h.weekday < 5].groupby("hour").p_kw.mean(); we = h[h.weekday >= 5].groupby("hour").p_kw.mean()
daily = h.groupby(h.index.date).p_kw.mean()
print("mean", round(h.p_kw.mean(), 2), "min", round(h.p_kw.min(), 2), "max", round(h.p_kw.max(), 2))
print("hourly profile trough/peak", round(prof.min() / prof.max(), 3), "peak hour", prof.idxmax(), "trough", prof.idxmin())
print("weekend/weekday", round(we.mean() / wd.mean(), 3))
print("daily mean min/max", round(daily.min(), 2), round(daily.max(), 2))
print("profile", prof.round(2).tolist())
pd.DataFrame({"hour": range(24), "mean_kw": prof.round(3).values, "weekday_kw": wd.round(3).values,
              "weekend_kw": we.round(3).values, "shape_mean1": (prof / prof.mean()).round(4).values}
             ).to_csv("it_load_measured_ucordoba_profile24.csv", index=False)

# 시뮬레이터 규모(설계 2,000 kW)로 환산한 더미: 실측 모양 유지, 평균 가동률 0.78 (샘플 시나리오 평균과 동일, 가정)
DESIGN, MEAN_UTIL = 2000.0, 0.78
scale = DESIGN * MEAN_UTIL / h.p_kw.mean()
sim = pd.DataFrame({"timestamp": out.timestamp, "it_power_kw": (h.p_kw.values * scale).round(1),
})
sim.to_csv("it_load_dummy_2MW_from_measured_hourly.csv", index=False)
print("scaled max util", round(sim.it_power_kw.max() / DESIGN, 3), "min", round(sim.it_power_kw.min() / DESIGN, 3))

# 랙별 더미 (세로형): 320랙 = 8열 x 40 (시뮬레이터 격자 id R01-001 ~ R08-040)
# 실측 합계 모양 x 랙 가중치. 1~2열은 고밀도(설계 8.5 kW), 3~8열 5.5 kW (합계 2,000 kW) - 모두 가정
rng = np.random.default_rng(42)
ids, w, dkw = [], [], []
for j in range(8):
    for i in range(40):
        ids.append(f"R{j + 1:02d}-{i + 1:03d}")
        d_ = 8.5 if j < 2 else 5.5
        dkw.append(round(d_, 3)); w.append(d_ * rng.uniform(0.85, 1.15))
w = np.array(w) / np.sum(w)
tot = sim.it_power_kw.values
noise = 1 + 0.03 * rng.standard_normal((len(tot), len(ids)))          # 랙별 ±3% 변동 (가정)
kw = tot[:, None] * w[None, :] * noise
kw *= (tot / kw.sum(1))[:, None]                                       # 시각별 합계 = 총 부하 유지
long = pd.DataFrame({"timestamp": np.repeat(sim.timestamp.values, len(ids)),
                     "rack_id": np.tile(ids, len(tot)),
                     "power_kw": kw.round(3).ravel()})
# 용량 줄이기: 최근 14일만
last = sorted(set(sim.timestamp.str[:10]))[-14:]
long = long[long.timestamp.str[:10].isin(last)]
long.to_csv("it_load_dummy_racks_long_14d.csv", index=False)
pd.DataFrame({"rack_id": ids, "design_kw": dkw}).to_csv("it_rack_inventory_dummy.csv", index=False)
print("rack rows", len(long), "days", last[0], last[-1])
