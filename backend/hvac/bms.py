"""
BMS 기간 데이터 가져오기 (Phase 12) - 열 자동 매칭 → 날짜별 시간 지표.

읽기는 tabular (IT 가져오기와 같은 파서). 열 역할은 헤더 동의어 점수로 고르고, 값이 숫자인 열만 지표로 쓴다.
시각: 일시 열 하나, 또는 날짜 + 시(0~23 / 1~24) 열. 1시간 평균으로 묶는다.
단위: 헤더에 RT/USRT 가 있으면 냉방부하 x 3.517 → kW, W 이면 /1000, L/h 이면 보급수 /1000 → m3/h.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta

from .tabular import TabularError, find_header, norm_header, profile_column, read_tables, to_date, to_datetime, to_num

BmsError = TabularError
METRICS = {   # 역할 → (화면 이름, 단위)
    "outdoor_temp_c": ("외기 건구온도", "℃"), "outdoor_rh_pct": ("외기 상대습도", "%"),
    "it_kw": ("IT 전력", "kW"), "total_cooling_kw": ("총 냉방부하", "kW"), "chiller_kw": ("칠러 전력", "kW"),
    "chiller_cop": ("칠러 COP", "-"), "crah_fan_kw": ("CRAH 팬 전력", "kW"), "pump_kw": ("냉수·냉각수 펌프 전력", "kW"),
    "makeup_m3_h": ("냉각탑 보급수", "m³/h"), "rack_inlet_max_c": ("랙 흡입온도 최대", "℃"),
    "chw_supply_c": ("냉수 공급온도", "℃"), "chw_return_c": ("냉수 환수온도", "℃"),
}
SYN = {
    "timestamp": ["timestamp", "datetime", "일시", "측정일시", "수집일시", "time stamp", "logtime"],
    "date": ["date", "일자", "날짜"],
    "hour": ["hour", "시간", "시각", "hh", "시"],
    "outdoor_temp_c": ["외기온도", "외기건구", "oat", "outdoortemp", "outsidetemp", "ambienttemp", "외기"],
    "outdoor_rh_pct": ["외기습도", "외기상대습도", "outdoorrh", "outsiderh", "ambientrh", "oarh", "humidity", "습도"],
    "it_kw": ["itkw", "itload", "it전력", "it부하", "서버전력", "upsoutput", "ups출력", "it"],
    "total_cooling_kw": ["냉방부하", "coolingload", "totalcooling", "냉동능력", "냉방량", "rt", "usrt"],
    "chiller_kw": ["칠러전력", "냉동기전력", "chillerkw", "chillerpower", "chiller"],
    "chiller_cop": ["cop"],
    "crah_fan_kw": ["crah", "crac", "항온항습", "팬전력", "fan"],
    "pump_kw": ["펌프", "pump"],
    "makeup_m3_h": ["보급수", "makeup", "보충수"],
    "rack_inlet_max_c": ["흡입", "inlet", "랙온도", "racktemp", "서버흡입"],
    "chw_supply_c": ["냉수공급", "chws", "chwsupply", "냉수출구"],
    "chw_return_c": ["냉수환수", "chwr", "chwreturn", "냉수입구"],
}
TIME_ROLES = ("timestamp", "date", "hour")


def _score(header, role):
    hn = norm_header(header)
    best = 0.0
    for w in SYN[role]:
        wn = norm_header(w)
        if not wn:
            continue
        if hn == wn:
            best = max(best, 1.0)
        elif len(wn) <= 3:   # 짧은 단어(it, rt, cop, hh)는 단어 단위로만
            if re.search(rf"(^|[^a-z]){re.escape(wn)}([^a-z]|$)", str(header).lower()):
                best = max(best, 0.8)
        elif wn in hn:
            best = max(best, 0.6 + 0.3 * len(wn) / max(len(hn), 1))
    return best


def match(header, profiles):
    """열 → 역할 (점수 높은 것부터 하나씩). 지표는 숫자 열(80% 이상)만"""
    cand = []
    for i, h in enumerate(header):
        p = profiles[i]
        for role in SYN:
            s = _score(h, role)
            if role == "timestamp":
                s = max(s, 0.9 if p["dt_frac"] > 0.8 else 0) if p["dt_frac"] > 0.5 else 0
            elif role == "date":
                s = s if p["date_frac"] > 0.8 else 0
            elif role == "hour":
                s = s if p["num_frac"] > 0.9 and p.get("max", 99) <= 24 and p.get("min", -1) >= 0 else 0
            elif p["num_frac"] < 0.8:
                s = 0
            if s >= 0.5:
                cand.append((s, i, role))
    used_c, used_r, mapping = set(), set(), {}
    for s, i, role in sorted(cand, key=lambda x: -x[0]):
        if i in used_c or role in used_r:
            continue
        used_c.add(i)
        used_r.add(role)
        mapping[role] = header[i]
    if "timestamp" in mapping:
        mapping.pop("date", None)
        mapping.pop("hour", None)
    return mapping


def _unit_factor(role, header):
    h = str(header).lower()
    if role == "total_cooling_kw" and re.search(r"(us)?rt\b|냉동톤", h):
        return 3.517, "RT → kW (x3.517)"
    if role.endswith("_kw") and re.search(r"\(w\)|\[w\]|\bw\b", h) and "kw" not in h:
        return 0.001, "W → kW"
    if role == "makeup_m3_h" and re.search(r"l/h|lph|ℓ", h):
        return 0.001, "L/h → m³/h"
    return 1.0, None


def inspect(files, overrides=None):
    """files: [(name, bytes)] → 표별 {label, header, mapping, rows, profiles}"""
    out = []
    for name, data in files:
        for t in read_tables(name, data):
            rows = t["rows"]
            hi = find_header(rows)
            if hi < 0:
                continue
            header = [str(h).strip() if h not in (None, "") else f"col{k}" for k, h in enumerate(rows[hi])]
            body = [list(r) + [None] * (len(header) - len(r)) for r in rows[hi + 1:]]
            profiles = [profile_column(h, [r[k] for r in body]) for k, h in enumerate(header)]
            label = t["label"]
            mapping = (overrides or {}).get(label) or match(header, profiles)
            out.append({"label": label, "header": header, "body": body, "mapping": mapping,
                        "n_rows": len(body), "samples": {h: profiles[k]["samples"][:3] for k, h in enumerate(header)}})
    if not out:
        raise BmsError("표를 찾지 못했습니다 (헤더 줄 + 숫자 데이터가 필요합니다)")
    return out


def public(t):
    m = t["mapping"]
    has_time = "timestamp" in m or ("date" in m and "hour" in m)
    return {"label": t["label"], "header": t["header"], "n_rows": t["n_rows"], "mapping": m, "samples": t["samples"],
            "metrics": [k for k in METRICS if k in m], "time_ok": has_time,
            "roles": [{"value": r, "label": METRICS[r][0] if r in METRICS else {"timestamp": "일시", "date": "날짜",
                                                                                "hour": "시(0~23)"}[r]}
                      for r in (*TIME_ROLES, *METRICS)]}


def records(t):
    """표 → {date: {metric: {hour: [값...]}}}, 단위 메모"""
    m = t["mapping"]
    idx = {r: t["header"].index(h) for r, h in m.items() if h in t["header"]}
    if "timestamp" not in idx and not ("date" in idx and "hour" in idx):
        raise BmsError(f"'{t['label']}': 시각 열(일시, 또는 날짜+시)을 찾지 못했습니다 — 열 역할을 지정해 주세요")
    hours = [to_num(r[idx["hour"]]) for r in t["body"]] if "hour" in idx else []
    one_based = bool(hours) and min(x for x in hours if x is not None) >= 1 and max(x for x in hours if x is not None) == 24
    facs, notes = {}, []
    for r in METRICS:
        if r in m:
            facs[r], n = _unit_factor(r, m[r])
            if n:
                notes.append(f"{METRICS[r][0]}: {n}")
    acc = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in t["body"]:
        if "timestamp" in idx:
            ts = to_datetime(row[idx["timestamp"]])
            if not ts:
                continue
        else:
            d, h = to_date(row[idx["date"]]), to_num(row[idx["hour"]])
            if d is None or h is None:
                continue
            ts = datetime(d.year, d.month, d.day) + timedelta(hours=h - (1 if one_based else 0))
        for r, f in facs.items():
            v = to_num(row[idx[r]])
            if v is not None:
                acc[ts.date()][r][ts.hour].append(v * f)
    if not acc:
        raise BmsError(f"'{t['label']}': 시각과 지표 값을 읽지 못했습니다")
    days = {d: {r: {h: sum(v) / len(v) for h, v in hs.items()} for r, hs in ms.items()} for d, ms in acc.items()}
    return days, notes


def dataset(tables):
    days, notes = {}, []
    for t in tables:
        d, n = records(t)
        notes += n
        for k, v in d.items():
            days.setdefault(k, {}).update(v)
    dates = sorted(days)
    metrics = sorted({r for v in days.values() for r in v})
    out_days = {d.isoformat(): {r: [round(hs[h], 4) if h in hs else None for h in range(24)] for r, hs in v.items()}
                for d, v in days.items()}
    summary = {"period": [dates[0].isoformat(), dates[-1].isoformat()], "n_days": len(dates), "metrics": metrics,
               "metric_labels": {r: METRICS[r][0] for r in metrics},
               "has_weather": "outdoor_temp_c" in metrics and "outdoor_rh_pct" in metrics, "notes": notes}
    return {"meta": {"period": summary["period"], "n_days": len(dates), "metrics": metrics},
            "days": out_days, "summary": summary}
