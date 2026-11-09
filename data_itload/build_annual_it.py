"""
2025년 연간 시간별 IT 부하 (시뮬레이터 연간 계산용 더미, 8,760시간)
근거: 코르도바 대학교 데이터센터 실측(2024-03-21~06-04, 75일)의
  - 시각별 모양: 평일·주말 각각 (평균=1)
  - 일평균 변동: 실측 일평균의 변동계수와 하루 간 자기상관(AR(1))
  - 시간 잔차: 실측에서 (일평균 x 시각 모양)을 뺀 나머지의 표준편차
가정: 설계 2,000 kW, 연평균 가동률 0.78, 연간 선형 증설 +6%/년(1월 0.97 → 12월 1.03 배율), 난수 시드 2025 고정
"""
import numpy as np, pandas as pd
d = pd.read_csv("/home/claude/itload2/ucod_power_raw.csv")
d["t"] = pd.to_datetime(d.timestamp, unit="ms", utc=True).dt.tz_convert("Europe/Madrid")
h = (-d.set_index("t").ptotal / 1000).resample("1h").mean().dropna()
days = h.groupby(h.index.date).size(); keep = days[days == 24].index
h = h[np.isin(h.index.date, keep)]
df = pd.DataFrame({"p": h.values, "date": h.index.date, "hour": h.index.hour, "wd": h.index.dayofweek})
dm = df.groupby("date").p.transform("mean"); df["rel"] = df.p / dm
shape_wd = df[df.wd < 5].groupby("hour").rel.mean().values; shape_we = df[df.wd >= 5].groupby("hour").rel.mean().values
daily = df.groupby("date").p.mean(); dn = daily / daily.mean()
cv = float(dn.std()); ac1 = float(np.corrcoef(dn.values[:-1], dn.values[1:])[0, 1])
wkend_ratio = float(df[df.wd >= 5].p.mean() / df[df.wd < 5].p.mean())
resid = df.rel - np.where(df.wd < 5, shape_wd[df.hour], shape_we[df.hour]); rsd = float(resid.std())
print(f"daily CV {cv:.4f} AR1 {ac1:.3f} weekend/weekday {wkend_ratio:.4f} hourly resid sd {rsd:.4f}")

rng = np.random.default_rng(2025)
idx = pd.date_range("2025-01-01 00:00", "2025-12-31 23:00", freq="1h")
nd = 365
e = np.zeros(nd); s = cv * np.sqrt(1 - ac1 ** 2)
for i in range(1, nd): e[i] = ac1 * e[i - 1] + rng.normal(0, s)
e[0] = rng.normal(0, cv)
growth = 0.97 + 0.06 * np.arange(nd) / (nd - 1)
dates = pd.date_range("2025-01-01", periods=nd, freq="D")
wk = np.where(dates.dayofweek >= 5, wkend_ratio, 1.0)
level = (1 + e) * growth * wk
lv = np.repeat(level, 24)
sh = np.array([shape_we if dt.dayofweek >= 5 else shape_wd for dt in dates]).ravel()
noise = 1 + rng.normal(0, rsd, len(idx))
rel = lv * sh * noise
for DESIGN, name in ((2000.0, "2MW_sample"), (40000.0, "40MW_haenam")):
    kw = DESIGN * 0.78 * rel / rel.mean()
    out = pd.DataFrame({"timestamp": idx.strftime("%Y-%m-%d %H:%M"), "it_power_kw": kw.round(1)})
    out.to_csv(f"it_load_2025_hourly_{name}.csv", index=False)
    print(name, "util mean", round(kw.mean() / DESIGN, 3), "min", round(kw.min() / DESIGN, 3), "max", round(kw.max() / DESIGN, 3),
          "Jan", round(kw[:744].mean() / DESIGN, 3), "Dec", round(kw[-744:].mean() / DESIGN, 3))
ids = [f"R{j + 1:02d}-{i + 1:03d}" for j in range(8) for i in range(40)]
pd.DataFrame({"rack_id": ids, "design_kw": [8.5 if k < 80 else 5.5 for k in range(320)]}).to_csv("it_rack_inventory_2MW_sample.csv", index=False)
