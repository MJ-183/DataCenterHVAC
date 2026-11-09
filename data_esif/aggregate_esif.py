"""NREL ESIF 데이터센터 PUE 원본(1~5분) → 시간 평균 CSV. 원본은 읽기만 한다.
시각: 원본 문자열의 GMT 오프셋으로 UTC 변환 후, 현지 표준시(MST, UTC-7, 서머타임 미적용)로 표기"""
import pandas as pd, re, sys, time
SRC = "esif.influx.buildingData.PUE.combined.csv"
cols = ["cooling_kw","energy_reuse","ere","hvac_kw","it_power_kw","plug_and_light_kw","pue","pump_kw"]
parts, n, t0 = [], 0, time.time()
for ch in pd.read_csv(SRC, usecols=["ts"] + cols, chunksize=500_000, low_memory=False):
    s = ch["ts"].astype(str).str.replace(r"\s*\(.*\)$", "", regex=True)
    ts = pd.to_datetime(s, format="%a %b %d %Y %H:%M:%S GMT%z", errors="coerce", utc=True)
    ch = ch.drop(columns="ts"); ch.index = ts
    ch = ch[ch.index.notna()]
    for c in cols: ch[c] = pd.to_numeric(ch[c], errors="coerce")
    g = ch.groupby(ch.index.floor("h"))
    parts.append(pd.concat([g.sum(min_count=1).add_suffix("__s"), g.count().add_suffix("__n")], axis=1))
    n += len(ch); print("rows", n, round(time.time()-t0,1), flush=True)
a = pd.concat(parts).groupby(level=0).sum(min_count=1)
out = pd.DataFrame(index=a.index)
for c in cols:
    out[c] = a[c+"__s"] / a[c+"__n"].where(a[c+"__n"] > 0)
    out["n_"+c] = a[c+"__n"]
out.index = out.index.tz_convert("Etc/GMT+7").tz_localize(None)
out.index.name = "timestamp_mst"
out.sort_index().to_csv("data_esif/esif_hourly_mst.csv", float_format="%.4f")
print("done", len(out), out.index.min(), out.index.max(), round(time.time()-t0,1))
