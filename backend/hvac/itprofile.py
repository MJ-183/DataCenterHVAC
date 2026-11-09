"""
IT 데이터셋 → 특정 날짜의 24시간 IT 부하 (Phase 11·14).

day_kw(): 연간 계산용. 대체 순서 = 같은 날짜 실측 > 같은 월·날 유형(평일/토/일) 평균 > 날 유형 전체 평균 > 전체 평균
forecast_kw(): 내일 예측용. 최근 4주 같은 요일 시각별 평균 x 추세(최근 28일 일평균 선형 기울기, 주당 ±10% 제한)
날 유형은 요일만 본다 (공휴일 구분 없음 - 가정).
"""
from datetime import date, timedelta

from .itdata import daytype

USED = ("date", "month_daytype", "daytype", "all")
USED_LABEL = {"date": "같은 날짜 실측", "month_daytype": "같은 월·요일 유형 평균", "daytype": "요일 유형 평균",
              "all": "전체 평균"}


def day_kw(ds, d: date):
    """→ (24시간 kW, 사용한 근거)"""
    days, typ = ds["daily_total_kw"], ds["typical"]
    if d.isoformat() in days:
        return days[d.isoformat()], "date"
    t = daytype(d)
    v = typ["by_month_daytype"].get(f"{d.month}|{t}")
    if v:
        return v, "month_daytype"
    if typ["by_daytype"].get(t):
        return typ["by_daytype"][t], "daytype"
    return typ["all"], "all"


def usage_note(counts):
    parts = [f"{USED_LABEL[k]} {counts[k]}일" for k in USED if counts.get(k)]
    return "IT 데이터셋: " + " · ".join(parts)


def forecast_kw(ds, target: date, lookback_weeks=4):
    """→ (24시간 kW, 설명 dict). 데이터 마지막 날 이후 날짜를 예측"""
    days = {date.fromisoformat(k): v for k, v in ds["daily_total_kw"].items()}
    last = max(days)
    same = [days[x] for x in (target - timedelta(weeks=w) for w in range(1, 53)) if x in days][:lookback_weeks]
    if same:
        base, basis = [sum(r[h] for r in same) / len(same) for h in range(24)], f"최근 같은 요일 {len(same)}일 평균"
    else:
        base, used = day_kw(ds, target)
        basis = USED_LABEL[used]
    recent = sorted(x for x in days if x > last - timedelta(days=28))
    trend = 1.0
    if len(recent) >= 7:
        xs = [(x - recent[0]).days for x in recent]
        ys = [sum(days[x]) / 24 for x in recent]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
        if my > 0:
            # 같은 요일 기준일(평균 lookback/2 주 전)에서 목표일까지 추세 반영, 주당 ±10% 제한
            weeks = max((target - last).days / 7 + (len(same) + 1) / 2 if same else 0, 0)
            per_week = max(-0.1, min(0.1, slope * 7 / my))
            trend = 1 + per_week * weeks
    return [round(v * trend, 2) for v in base], {"basis": basis, "trend_factor": round(trend, 4),
                                                   "data_last_day": last.isoformat()}
