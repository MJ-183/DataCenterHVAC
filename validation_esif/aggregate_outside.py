"""ESIF 현장 외기(온도 ℉, 상대습도 %) 원본 → 시간 평균 CSV (MST, UTC-7). 원본은 읽기만 한다.
사용: data.nlr.gov/submissions/300 의 'ESIF DC Outside Weather Station Timeseries Data (Zipped CSV)' 를
      data_esif/ 에 넣고 (zip 그대로 또는 압축 해제한 csv)  →  python validation_esif/aggregate_outside.py
출력: data_esif/esif_outside_hourly_mst.csv  (있으면 esif_compare.py 가 TMY3 대신 현장 온습도를 사용)"""
import sys
import zipfile
from pathlib import Path

import pandas as pd

D = Path(__file__).resolve().parents[1] / "data_esif"
cands = sorted(D.glob("*outside*.csv")) + sorted(D.glob("*outside*.zip"))
cands = [p for p in cands if "hourly" not in p.name]
if not cands:
    sys.exit("data_esif/ 에 outside 원본(csv 또는 zip)이 없습니다")
src = cands[0]
if src.suffix == ".zip":
    z = zipfile.ZipFile(src)
    name = [n for n in z.namelist() if n.endswith(".csv")][0]
    fh = z.open(name)
else:
    fh = open(src, "rb")
cols = ["outside_air_temp", "outside_air_humidity"]
parts = []
for ch in pd.read_csv(fh, chunksize=500_000, low_memory=False):
    s = ch["ts"].astype(str).str.replace(r"\s*\(.*\)$", "", regex=True)
    ts = pd.to_datetime(s, format="%a %b %d %Y %H:%M:%S GMT%z", errors="coerce", utc=True)
    if ts.isna().mean() > 0.5:                      # 다른 형식(ISO 등)이면 일반 파서
        ts = pd.to_datetime(ch["ts"], errors="coerce", utc=True)
    ch = ch[[c for c in cols if c in ch]].apply(pd.to_numeric, errors="coerce")
    ch.index = ts
    ch = ch[ch.index.notna()]
    ch = ch[(ch.outside_air_temp.between(-40, 120)) & (ch.outside_air_humidity.between(0, 100))]  # 센서 이상값 제외
    g = ch.groupby(ch.index.floor("h"))
    parts.append(pd.concat([g.sum(min_count=1).add_suffix("__s"), g.count().add_suffix("__n")], axis=1))
    print("chunk", len(ch), flush=True)
a = pd.concat(parts).groupby(level=0).sum(min_count=1)
out = pd.DataFrame({c: a[c + "__s"] / a[c + "__n"].where(a[c + "__n"] > 0) for c in cols})
out["n"] = a["outside_air_temp__n"]
out.index = out.index.tz_convert("Etc/GMT+7").tz_localize(None)
out.index.name = "timestamp_mst"
out.sort_index().to_csv(D / "esif_outside_hourly_mst.csv", float_format="%.3f")
print("saved", len(out), out.index.min(), out.index.max())
