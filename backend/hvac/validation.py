"""
실측 BMS 데이터(CSV)와 계산값 비교 (Phase 6).

CSV 형식: 첫 줄 헤더, 'hour'(0~23) 열 필수, 아래 지표 열 중 원하는 것만. 빈 칸은 결측으로 무시.
오차 = 계산 - 실측 (양수면 계산이 과대)
  MAE  = mean|e|,  RMSE = sqrt(mean e^2)
  NMBE = sum(e) / (n * mean(실측)) * 100 [%]
  CV(RMSE) = RMSE / mean(실측) * 100 [%]
  (ASHRAE Guideline 14 는 자유도 n-p 를 쓰고 부호가 실측-계산이다. 여기서는 p=0, 계산-실측 부호를 사용)
"""
import csv
import io
import math

METRICS = {
    "total_cooling_kw": ("총 냉방부하", "kW", lambda x: x["load_kw"]["total_cooling"]),
    "chw_flow_m3_h": ("냉수 유량", "m³/h", lambda x: x["chilled_water"]["flow_m3_h"]),
    "cw_flow_m3_h": ("냉각수 유량", "m³/h", lambda x: x["condenser_water"]["flow_m3_h"]),
    "chiller_power_kw": ("칠러 전력", "kW", lambda x: x["chiller"]["power_kw"]),
    "chiller_cop": ("칠러 COP", "-", lambda x: x["chiller"]["cop"]),
    "makeup_m3_h": ("보급수", "m³/h", lambda x: x["makeup_water_m3_h"]["total"]),
}


class CsvError(ValueError):
    pass


def parse_bms_csv(text: str) -> dict[str, dict[int, float]]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CsvError("CSV 헤더가 없습니다")
    cols = [c.strip() for c in reader.fieldnames]
    if "hour" not in cols:
        raise CsvError("'hour' 열이 필요합니다")
    known = [c for c in cols if c in METRICS]
    unknown = [c for c in cols if c not in METRICS and c != "hour"]
    if not known:
        raise CsvError(f"비교 가능한 열이 없습니다. 지원 열: {', '.join(METRICS)}")
    if unknown:
        raise CsvError(f"알 수 없는 열: {', '.join(unknown)} (지원 열: {', '.join(METRICS)})")
    data = {c: {} for c in known}
    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        try:
            h = int(float(row["hour"]))
        except ValueError:
            raise CsvError(f"{line_no}행: hour 값 '{row['hour']}' 이 숫자가 아닙니다")
        if not 0 <= h <= 23:
            raise CsvError(f"{line_no}행: hour 는 0~23 이어야 합니다 ({h})")
        for c in known:
            v = row.get(c, "")
            if v == "":
                continue
            try:
                f = float(v)
            except ValueError:
                raise CsvError(f"{line_no}행 {c}: '{v}' 는 숫자가 아닙니다")
            if not math.isfinite(f):
                raise CsvError(f"{line_no}행 {c}: 유한한 숫자가 아닙니다")
            if h in data[c]:
                raise CsvError(f"{line_no}행: hour {h} 가 중복되었습니다 ({c})")
            data[c][h] = f
    return data


def compare(result: dict, measured: dict[str, dict[int, float]]) -> list[dict]:
    out = []
    by_hour = {x["hour"]: x for x in result["hourly"] if x.get("minute", 0) == 0}  # 실측은 정시 값과 비교
    for key, series in measured.items():
        label, unit, get = METRICS[key]
        points = [{"hour": h, "measured": m, "calculated": get(by_hour[h]),
                   "error": round(get(by_hour[h]) - m, 4)} for h, m in sorted(series.items())]
        n = len(points)
        stats = None
        if n:
            errs = [p["error"] for p in points]
            mean_m = sum(p["measured"] for p in points) / n
            rmse = math.sqrt(sum(e * e for e in errs) / n)
            worst = max(points, key=lambda p: abs(p["error"]))
            stats = {
                "n": n,
                "mae": round(sum(abs(e) for e in errs) / n, 3),
                "rmse": round(rmse, 3),
                "nmbe_pct": round(sum(errs) / (n * mean_m) * 100, 2) if mean_m else None,
                "cv_rmse_pct": round(rmse / mean_m * 100, 2) if mean_m else None,
                "max_abs_error": round(abs(worst["error"]), 3),
                "max_abs_error_hour": worst["hour"],
            }
        out.append({"key": key, "label": label, "unit": unit, "points": points, "stats": stats})
    return out
