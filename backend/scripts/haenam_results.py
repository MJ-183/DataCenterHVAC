"""
해남 40 MW 대표 결과 — 냉각 방식 3종 연간 지표 + 같은 시나리오에 서울(108) 2025 관측 기상만 바꾼 비교.

backend 폴더에서:  .venv/Scripts/python.exe scripts/haenam_results.py [--md 출력.md]
반올림 규칙: 전력량 MWh 정수(0.5 올림 없이 round), PUE 소수 셋째 자리, 보급수 m3 정수, WUE L/kWh 소수 셋째 자리,
            절감률 % 소수 첫째 자리. 모든 숫자는 annual.run() 계산값에서 바로 만든다.
"""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hvac import DATA_DIR, annual, datasets  # noqa: E402
from hvac import weather as wx  # noqa: E402
from hvac.kma_stations import ASOS_COORDS  # noqa: E402

ARCH_LABEL = {"air_chw": "공랭 (CRAH+칠러)", "hybrid_dlc": "DLC 혼합 (액체 0.75)", "immersion": "액침"}
WEATHER = {"haenam": ("해남(261) 2025", "annual_kma_261_2025_auto165.json", 261),
           "seoul": ("서울(108) 2025", "annual_kma_108_2025_auto.json", 108)}


def load_scenario():
    return json.loads((DATA_DIR / "scenario_haenam_ai.json").read_text(encoding="utf-8"))


def run(sc, key):
    _, fname, stn = WEATHER[key]
    ann = wx.load_annual(wx.WEATHER_DIR / fname)
    s = copy.deepcopy(sc)
    if key != "haenam":   # 기상만 교체: 태양 위치(위경도)와 관측소 id 도 기상과 같은 곳으로, 나머지 입력은 그대로
        lat, lon, _ = ASOS_COORDS[stn]
        s["site"].update(latitude=lat, longitude=lon, asos_station_id=stn)
        s["site"].pop("asos_solar_station_id", None)
    ds = datasets.load("it", s["it_load"]["dataset_id"])
    t = time.time()
    res = annual.run(s, ann, it_dataset=ds)
    return res, time.time() - t


def rows(res):
    out = {}
    for c in res["comparison"]:
        out[c["architecture"]] = {
            "pue": c["pue"], "cooling_mwh": round(c["cooling_power_kwh"] / 1000), "chiller_mwh": round(c["chiller_kwh"] / 1000),
            "makeup_m3": round(c["makeup_m3"]), "wue": c["wue_l_per_kwh"], "free_h": c["free_h"], "partial_h": c["partial_h"],
            "facility_mwh": round(c["facility_kwh"] / 1000), "cooling_kwh": c["cooling_power_kwh"]}
    return out


def conclusion(r, where):
    a, h = r["air_chw"], r["hybrid_dlc"]
    sv = a["cooling_kwh"] - h["cooling_kwh"]
    return (f"{where} 40 MW에서 hybrid_dlc 는 air_chw 대비 냉방 설비 전력을 연 {sv / 1000:,.0f} MWh"
            f"({sv / a['cooling_kwh'] * 100:.1f}%) 절감, PUE {a['pue']:.3f} → {h['pue']:.3f}")


def table(r):
    lines = ["| 냉각 방식 | PUE | 냉방 설비 전력 MWh | 칠러 MWh | 보급수 m3 | WUE L/kWh | 프리쿨링 h | 부분 프리쿨링 h |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, v in r.items():
        lines.append(f"| {ARCH_LABEL[k]} | {v['pue']:.3f} | {v['cooling_mwh']:,} | {v['chiller_mwh']:,} | {v['makeup_m3']:,} | "
                     f"{v['wue']:.3f} | {v['free_h']:,.0f} | {v['partial_h']:,.0f} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    args = ap.parse_args()
    sc = load_scenario()
    out = {}
    for key in WEATHER:
        res, sec = run(sc, key)
        out[key] = (rows(res), sec, res)
    md = []
    for key, (r, sec, res) in out.items():
        it = 'IT 데이터셋' if res['it_source'] == 'dataset' else '시나리오 24시간 모양'
        md += [f"### {WEATHER[key][0]} 기상 (연간 계산 {sec:.1f}초, IT 부하 = {it})", "", table(r), "",
               "**" + conclusion(r, "해남" if key == "haenam" else "서울 기상") + "**", ""]
    h, s = out["haenam"][0], out["seoul"][0]
    md += ["### 지역 차이 (해남 − 서울 기상, 같은 40 MW 시나리오)", "",
           "| 냉각 방식 | PUE 차 | 냉방 설비 전력 MWh 차 | 보급수 m3 차 | 프리쿨링 h 차 |", "|---|---:|---:|---:|---:|"]
    for k in h:
        md.append(f"| {ARCH_LABEL[k]} | {h[k]['pue'] - s[k]['pue']:+.3f} | {h[k]['cooling_mwh'] - s[k]['cooling_mwh']:+,} | "
                  f"{h[k]['makeup_m3'] - s[k]['makeup_m3']:+,} | {h[k]['free_h'] - s[k]['free_h']:+,.0f} |")
    text = "\n".join(md)
    print(text)
    if args.md:
        Path(args.md).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
