"""
IT 부하 데이터 가져오기 (Phase 8-2) - 컬럼 자동 매칭 → 랙별 24시간 프로파일 → 시나리오 반영.

읽기: CSV / TSV / ; / | 구분, Excel(.xlsx, 시트마다 별도 표). 인코딩 utf-8(BOM) → cp949 → latin-1 순 시도.
      제목 줄이 앞에 있으면 '문자 위주 줄 다음에 숫자·날짜 줄이 오는' 첫 줄을 헤더로 본다.
컬럼 역할 (헤더 동의어 점수 + 값 모양 점수, 가장 높은 것부터 하나씩 배정):
  timestamp(일시) / date(날짜) / time(시각) / hour(0~23 또는 1~24) / rack(랙·PDU·열 id)
  power(전력, W·kW·MW) / energy(누적 전력량 kWh → 차분해 전력) / util(가동률 %, 설계 kW 필요)
  design_kw(정격·설계·용량) / cooling(air·dlc·immersion)
표 형태:
  long       시각 + 랙 id + 값             (세로형)
  wide       시각 + 랙별 값 열 여러 개      (가로형, 헤더가 랙 id)
  total      시각 + 값 (랙 없음, 전체 합계)
  inventory  랙 id + 설계 kW / 냉각방식 (시각 없음)
사용자가 mapping(역할 → 열 이름), kind, unit 으로 덮어쓸 수 있다.
프로파일: 1시간 평균으로 묶고, 하루를 고른다 - peak(일 최대 합계가 가장 큰 날, 설계일) / mean(시각별 평균) / YYYY-MM-DD
가정 (결과 notes 에 기록): 단위 표기가 없으면 값 크기로 W/kW 추정, 결측 시각은 같은 랙의 앞뒤 값으로 순환 선형 보간,
  설계 kW 정보가 없으면 측정 최대 합계를 설계부하로 둠, 도면 랙과 짝이 안 맞은 부하는 짝 없는 랙에 설계 kW 비례(없으면 균등) 배분.
"""
import copy
import re
import statistics
from collections import Counter, defaultdict
from datetime import date

ROLES = ("timestamp", "date", "time", "hour", "rack", "power", "energy", "util", "design_kw", "cooling", "ignore")
ROLE_LABEL = {"timestamp": "일시", "date": "날짜", "time": "시각", "hour": "시(0~23)", "rack": "랙/PDU id",
              "power": "전력", "energy": "누적 전력량", "util": "가동률", "design_kw": "설계(정격) kW",
              "cooling": "냉각 방식", "ignore": "사용 안 함"}
SYN = {   # 정규화된 헤더(소문자, 공백·기호 제거)에 포함되면 점수
    "timestamp": ["timestamp", "datetime", "일시", "측정일시", "수집일시", "기록일시", "측정시각", "수집시각", "logtime",
                  "readtime", "time stamp", "datetimeutc", "일자시간"],
    "date": ["date", "일자", "날짜", "측정일", "day"],
    "time": ["time", "시간", "시각", "clock"],
    "hour": ["hour", "hr", "시", "hh", "hourofday"],
    "rack": ["rack", "cabinet", "랙", "서버랙", "asset", "자산", "장비", "location", "위치", "pdu", "분전반", "circuit",
             "회로", "device", "name", "id", "tag", "row", "열"],
    "power": ["kw", "power", "load", "전력", "소비전력", "부하", "사용전력", "유효전력", "demand", "watt", "itload", "w"],
    "energy": ["kwh", "energy", "전력량", "누적", "meter", "적산"],
    "util": ["util", "utilization", "가동률", "사용률", "usage", "percent", "pct", "%"],
    "design_kw": ["design", "rated", "capacity", "nameplate", "contract", "설계", "정격", "용량", "계약", "허용", "max"],
    "cooling": ["cooling", "냉각", "coolingtype", "냉각방식"],
}
COOLING_WORDS = {"air": ["air", "공랭", "crah", "공기"], "dlc": ["dlc", "liquid", "수냉", "액체", "직접", "coldplate"],
                 "immersion": ["immersion", "액침", "침지"]}
from .tabular import (MAX_ROWS, TabularError, _decode, find_header, norm_header, profile_column as _profile_base,  # noqa: F401
                      read_tables, to_date, to_datetime, to_num, to_time, unit_in_header)

ItDataError = TabularError


def profile_column(name, vals):
    p = _profile_base(name, vals)
    nn = [v for v in vals if v not in (None, "") and str(v).strip() != ""]
    texts = [str(v).strip() for v in nn if to_num(v) is None]
    p["cool_frac"] = sum(1 for t in texts if any(w in t.lower() for ws in COOLING_WORDS.values() for w in ws)) / max(len(nn), 1)
    return p


def _syn_score(h, role):
    hn = norm_header(h)
    best = 0.0
    for kw in SYN[role]:
        k = norm_header(kw) if kw != "%" else "%"
        if not k:
            continue
        if hn == k:
            best = max(best, 1.0)
        elif len(k) >= 2 and k in hn:
            best = max(best, 0.7 if len(k) >= 3 else 0.45)
        elif len(k) == 1 and re.search(rf"(^|[^a-z가-힣]){re.escape(k)}([^a-z가-힣]|$)", str(h).lower()):
            best = max(best, 0.4)
    return best


def role_scores(p):
    """열 하나의 역할별 점수 (헤더 동의어 + 값 모양)"""
    h = p["name"]
    s = {r: _syn_score(h, r) for r in SYN}
    u = p["unit"]
    s["timestamp"] += 1.5 * p["dt_frac"]
    s["date"] = (s["date"] + 1.2 * p["date_frac"]) if p["dt_frac"] < 0.5 else 0
    s["time"] = (s["time"] + 1.2 * p["time_frac"]) if p["dt_frac"] < 0.5 else 0
    is_hour = (p["num_frac"] > 0.95 and p.get("int_frac", 0) > 0.99 and 0 <= p.get("min", -1)
               and p.get("max", 99) <= 24)
    s["hour"] = (s["hour"] + 0.8) if is_hour else s["hour"] * 0.3
    s["rack"] += 0.6 * p["idlike_frac"] + (0.3 if p["num_frac"] < 0.2 and p["unique"] < 0.9 else 0)
    if p["num_frac"] > 0.8:
        s["rack"] *= 0.3
    numeric = p["num_frac"] > 0.9
    s["cooling"] += 0.8 * p["cool_frac"]
    for r in ("power", "energy", "util", "design_kw"):
        if not numeric:
            s[r] = 0.0
    if numeric:
        if u in ("kW", "W", "MW"):
            s["power"] += 0.6
        if u in ("kWh", "MWh"):
            s["energy"] += 1.0
            s["power"] *= 0.3
        if u == "%":
            s["util"] += 0.8
        if s["design_kw"] > 0 and (u in ("kW", "W", "MW") or s["power"] > 0):
            s["design_kw"] += 0.6
            s["power"] *= 0.5
        if p.get("mono_frac", 0) > 0.98 and p.get("max", 0) > 10 * max(p.get("min", 0), 1e-9) and s["energy"] > 0:
            s["energy"] += 0.4
        if s["power"] == 0:              # 이름 정보 없는 숫자 열 → 전력 후보 (약하게, 0~24 정수면 '시'보다 낮게)
            s["power"] = 0.36 if is_hour else 0.4
    for r in ("timestamp", "date", "time"):
        if numeric and not p["dt_frac"]:
            s[r] = 0.0
    return s


def analyze_table(t, mapping=None, kind=None, unit=None):
    """표 하나 → 헤더/열 프로파일/역할 매핑/표 종류"""
    rows = t["rows"]
    hi = find_header(rows)
    width = max(len(r) for r in rows[: max(hi, 0) + 200])
    if hi >= 0:
        header = [str(c).strip() if c not in (None, "") else f"col{k + 1}" for k, c in
                  enumerate(list(rows[hi]) + [None] * (width - len(rows[hi])))]
        body = rows[hi + 1:]
    else:
        header, body = [f"col{k + 1}" for k in range(width)], rows
    seen = Counter()
    for k, h in enumerate(header):               # 중복 헤더 구분
        seen[h] += 1
        if seen[h] > 1:
            header[k] = f"{h}_{seen[h]}"
    body = [list(r) + [None] * (width - len(r)) for r in body[:MAX_ROWS]]
    cols = {h: [r[k] for r in body] for k, h in enumerate(header)}
    sample = {h: v[:2000] for h, v in cols.items()}
    profiles = {h: profile_column(h, v) for h, v in sample.items()}
    scores = {h: role_scores(p) for h, p in profiles.items()}

    assigned, used, reasons = {}, set(), {}
    if mapping:
        for r, h in mapping.items():
            if h not in cols:
                raise ItDataError(f"'{t['label']}': 지정한 열 '{h}' 이 없습니다 (있는 열: {', '.join(header)})")
            if r not in ROLES:
                raise ItDataError(f"알 수 없는 역할: {r}")
            if r != "ignore":
                assigned[r] = h
            used.add(h)
            reasons[h] = "사용자 지정"
    single = ("timestamp", "date", "time", "hour", "rack", "power", "energy", "util", "design_kw", "cooling")
    cand = sorted(((sc, r, h) for h, rs in scores.items() if h not in used for r, sc in rs.items()
                   if r in single and sc >= 0.35), reverse=True)
    for sc, r, h in cand:
        if r in assigned or h in used:
            continue
        if r == "hour" and "timestamp" in assigned:
            continue
        if r in ("date", "time") and "timestamp" in assigned:
            continue
        assigned[r] = h
        used.add(h)
        reasons[h] = f"헤더/값 점수 {sc:.2f}"
    # 시각이 이미 있으면 'hour' 열은 버림, date 와 time 이 다 있어야 의미
    if "date" in assigned and "time" not in assigned and "hour" not in assigned and "timestamp" not in assigned:
        assigned["timestamp"] = assigned.pop("date")

    has_time = any(r in assigned for r in ("timestamp", "hour", "time"))
    values = [r for r in ("power", "energy", "util") if r in assigned]
    # 가로형: 시각 열이 있고, 역할이 없는 숫자 열이 3개 이상 (헤더가 랙 id)
    # 가로형 판정용: 헤더에 숫자가 든(랙 id 같은) 숫자 열. 전압·전류·역률 같은 열이 있는 합계 표를 가로형으로 오인하지 않도록
    num_cols = [h for h in header if profiles[h]["num_frac"] > 0.9 and profiles[h]["n"] > 0
                and h not in (assigned.get("hour"), assigned.get("timestamp"))]
    id_cols = [h for h in num_cols if re.search(r"\d", h) and not re.fullmatch(r"col\d+", h)]
    if kind is None:
        if (has_time and "rack" not in assigned and len(id_cols) >= 3 and len(id_cols) >= 0.6 * len(num_cols)
                and not mapping):
            kind = "wide"
        elif has_time and "rack" in assigned and values:
            kind = "long"
        elif has_time and values:
            kind = "total"
        elif "rack" in assigned and ("design_kw" in assigned or "cooling" in assigned or "power" in assigned):
            kind = "inventory"
        else:
            kind = "unknown"
    wide_cols = []
    if kind == "wide":
        wide_cols = [h for h in num_cols if h not in used or h in [assigned.get(v) for v in values]]
        for v in values:
            assigned.pop(v, None)
    if kind == "inventory" and "design_kw" not in assigned and "power" in assigned:
        assigned["design_kw"] = assigned.pop("power")
        reasons[assigned["design_kw"]] += " (시각이 없는 전력 열 → 설계 kW 로 간주)"

    value_col = next((assigned[r] for r in ("power", "energy", "util") if r in assigned), None)
    if unit is None:
        if kind == "wide" and wide_cols:
            us = {profiles[h]["unit"] for h in wide_cols} - {None}
            unit = us.pop() if len(us) == 1 else None
            med = statistics.median([profiles[h].get("median", 0) for h in wide_cols])
            if unit is None:
                unit = "W" if med > 200 else "kW"
                unit_basis = f"단위 표기 없음 → 랙 값 중앙값 {med:.1f} 로 {unit} 추정 (가정)"
            else:
                unit_basis = f"헤더 단위 표기 ({unit})"
        elif value_col:
            p = profiles[value_col]
            unit = p["unit"]
            if "util" in assigned:
                unit = "%" if p.get("max", 0) > 1.5 else "fraction"
                unit_basis = "가동률 값 범위로 판정"
            elif unit is None or unit == "%":
                big = 200 if kind == "long" else 100000
                unit = "W" if p.get("median", 0) > big else "kW"
                if "energy" in assigned:
                    unit = "Wh" if unit == "W" else "kWh"
                unit_basis = f"단위 표기 없음 → 값 중앙값 {p.get('median', 0):.1f} 로 {unit} 추정 (가정)"
            else:
                unit_basis = f"헤더 단위 표기 ({unit})"
        else:
            unit_basis = ""
    else:
        unit_basis = "사용자 지정"
    dk_unit = None
    if "design_kw" in assigned:
        p = profiles[assigned["design_kw"]]
        dk_unit = p["unit"] if p["unit"] in ("W", "kW", "MW") else ("W" if p.get("median", 0) > 500 else "kW")

    warn = []
    for h in header:
        if h not in used and h not in wide_cols and re.search(r"(^|[^a-z])(a|amp|ampere|전류)([^a-z]|$)", h.lower()):
            warn.append(f"'{h}' 는 전류(A) 열로 보여 사용하지 않음 (전압·역률 정보 없이 kW 환산 불가)")
    if kind == "unknown":
        warn.append("시각 열 + 값 열, 또는 랙 id + 설계 kW 열을 찾지 못했습니다. 열 역할을 직접 지정해 주세요")
    return {"label": t["label"], "header_row": hi, "columns": header, "kind": kind, "mapping": assigned,
            "wide_columns": wide_cols, "unit": unit, "unit_basis": unit_basis, "design_kw_unit": dk_unit,
            "profiles": profiles, "reasons": reasons, "warnings": warn, "_cols": cols, "rows_n": len(body)}


# ---------- 레코드 변환 ----------
UNIT_TO_KW = {"W": 0.001, "kW": 1.0, "MW": 1000.0, "Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}


def _stamps(a):
    """행별 (date|None, 시각[h, 실수]) 또는 None"""
    m, c = a["mapping"], a["_cols"]
    n = a["rows_n"]
    out = [None] * n
    hour_shift = 0
    if "hour" in m:
        hs = [to_num(v) for v in c[m["hour"]]]
        valid = [x for x in hs if x is not None]
        if valid and min(valid) >= 1 and max(valid) == 24:
            hour_shift = -1
            a["notes"].append("시 열이 1~24 로 되어 있어 1을 빼서 0~23 으로 사용 (가정: 24 = 23~24시 구간)")
    for k in range(n):
        if "timestamp" in m:
            dt = to_datetime(c[m["timestamp"]][k])
            if dt:
                out[k] = (dt.date(), dt.hour + dt.minute / 60 + dt.second / 3600)
                continue
        d = to_date(c[m["date"]][k]) if "date" in m else None
        if "time" in m:
            tt = to_time(c[m["time"]][k])
            if tt:
                out[k] = (d, tt.hour + tt.minute / 60 + tt.second / 3600)
                continue
        if "hour" in m:
            h = to_num(c[m["hour"]][k])
            if h is not None and 0 <= h + hour_shift < 24:
                out[k] = (d, h + hour_shift)
    return out


def records(a, design_by_id=None, design_total=None):
    """시계열 표 → [(date|None, hour_float, rack_id|None, kW)]"""
    a.setdefault("notes", [])
    stamps = _stamps(a)
    if sum(1 for s in stamps if s) == 0:
        raise ItDataError(f"'{a['label']}': 시각 값을 읽지 못했습니다 (열 '{a['mapping'].get('timestamp') or a['mapping'].get('hour')}')")
    m, c = a["mapping"], a["_cols"]
    out = []
    unit = a["unit"]
    if a["kind"] == "wide":
        f = UNIT_TO_KW.get(unit, 1.0)
        for h in a["wide_columns"]:
            rid = re.sub(r"\s*[\[\(]?\s*(k?w|mw|kwh|%)\s*[\]\)]?\s*$", "", h, flags=re.I).strip() or h
            for k, st in enumerate(stamps):
                v = to_num(c[h][k])
                if st and v is not None:
                    out.append((st[0], st[1], rid, v * f))
        return out
    vc = next(m[r] for r in ("power", "energy", "util") if r in m)
    racks = c[m["rack"]] if a["kind"] == "long" else [None] * a["rows_n"]
    raw = []
    for k, st in enumerate(stamps):
        v = to_num(c[vc][k])
        if st and v is not None:
            rid = id_str(racks[k])
            if a["kind"] == "long" and not rid:
                continue
            raw.append((st[0], st[1], rid, v))
    if "energy" in m:
        f = UNIT_TO_KW.get(unit, 1.0)
        by = defaultdict(list)
        for d, h, rid, v in raw:
            by[rid].append(((d or date.min), h, v))
        resets = 0
        for rid, pts in by.items():
            pts.sort()
            for (d0, h0, v0), (d1, h1, v1) in zip(pts, pts[1:]):
                dt = ((d1 - d0).days * 24 + h1 - h0) if d0 != date.min else (h1 - h0)
                if dt <= 0 or dt > 6:
                    continue
                if v1 < v0:
                    resets += 1
                    continue
                out.append((None if d1 == date.min else d1, (h1 - dt / 2) % 24, rid, (v1 - v0) * f / dt))
        a["notes"].append("누적 전력량(kWh)을 연속 두 값의 차 ÷ 시간 간격으로 전력(kW)으로 환산, 구간 중앙 시각에 배정")
        if resets:
            a["notes"].append(f"누적값이 줄어든 구간 {resets}개(계량기 리셋)는 제외")
        return out
    if "util" in m:
        div = 100.0 if unit == "%" else 1.0
        for d, h, rid, v in raw:
            base = (design_by_id or {}).get(norm_id(rid)) if rid else design_total
            if base is None:
                continue
            out.append((d, h, rid, v / div * base))
        if not out:
            raise ItDataError(f"'{a['label']}': 가동률을 kW 로 바꿀 설계 kW 가 없습니다 (랙 목록 파일 또는 시나리오 설계부하 필요)")
        a["notes"].append("가동률 x 설계 kW 로 전력 환산")
        return out
    f = UNIT_TO_KW.get(unit, 1.0)
    return [(d, h, rid, v * f) for d, h, rid, v in raw]


def inventory(a):
    """랙 목록 표 → {rack_id: {"design_kw", "cooling"}}"""
    m, c = a["mapping"], a["_cols"]
    f = UNIT_TO_KW.get(a.get("design_kw_unit") or "kW", 1.0)
    out = {}
    for k in range(a["rows_n"]):
        rid = id_str(c[m["rack"]][k])
        if not rid:
            continue
        e = out.setdefault(rid, {})
        if "design_kw" in m:
            v = to_num(c[m["design_kw"]][k])
            if v is not None:
                e["design_kw"] = e.get("design_kw", 0) + v * f
        if "cooling" in m:
            t = str(c[m["cooling"]][k] or "").lower()
            e["cooling"] = next((key for key, ws in COOLING_WORDS.items() if any(w in t for w in ws)), None)
    return out


# ---------- id 매칭 ----------
def id_str(v):
    """셀 값 → id 문자열 (엑셀 숫자 101.0 → '101'). 비면 None"""
    if v in (None, ""):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip() or None


def norm_id(s):
    s = re.sub(r"[\s_\-\.#/:]+", "", str(s or "").upper())
    return re.sub(r"(?<!\d)0+(\d)", r"\1", s)


def _alt_id(k):
    return re.sub(r"^(RACK|RK|R|랙)", "", k)


def match_ids(data_ids, layout_ids):
    """데이터 id → 레이아웃 id. 정규화(대문자, 기호 제거, 앞자리 0 제거) 후 일치 → 접두어(RACK/R) 제거 후 일치"""
    by1 = {norm_id(x): x for x in layout_ids}
    by2 = {}
    for x in layout_ids:
        by2.setdefault(_alt_id(norm_id(x)), x)
    out = {}
    taken = set()
    for d in data_ids:
        k = norm_id(d)
        t = by1.get(k) or by2.get(_alt_id(k))
        if t and t not in taken:
            out[d] = t
            taken.add(t)
    return out


# ---------- 하루 프로파일 ----------
def _hourly(recs):
    """[(date, hour_f, rid, kw)] → {rid: {date: {hour: 평균 kW}}}"""
    acc = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for d, h, rid, kw in recs:
        acc[rid][d][int(h) % 24].append(kw)
    return {rid: {d: {h: sum(v) / len(v) for h, v in hs.items()} for d, hs in ds.items()} for rid, ds in acc.items()}


def _fill(day):
    """{hour: kW} (일부 결측) → 24개, 순환 선형 보간"""
    known = sorted(day)
    if not known:
        return None
    out = []
    for h in range(24):
        if h in day:
            out.append(day[h])
            continue
        prev = max((k for k in known if k < h), default=known[-1] - 24)
        nxt = min((k for k in known if k > h), default=known[0] + 24)
        vp, vn = day[prev % 24], day[nxt % 24]
        out.append(vp + (vn - vp) * (h - prev) / (nxt - prev) if nxt != prev else vp)
    return out


def day_profiles(recs, day="peak"):
    """랙별 24시간 프로파일과 선택한 날짜. day: 'peak' | 'mean' | 'YYYY-MM-DD'"""
    hourly = _hourly(recs)
    dates = sorted({d for ds in hourly.values() for d in ds}, key=lambda d: (d is None, d))
    notes = []
    if dates == [None]:
        chosen = None
        notes.append("날짜 정보 없음 → 시(hour) 기준 하루 프로파일")
    elif day == "mean":
        chosen = "mean"
    elif day == "peak":
        tot = defaultdict(lambda: defaultdict(float))
        for ds in hourly.values():
            for d, hs in ds.items():
                for h, v in hs.items():
                    tot[d][h] += v
        chosen = max((d for d in tot if d is not None), key=lambda d: max(tot[d].values()))
        notes.append(f"설계일: IT 합계 최대값이 가장 큰 날 {chosen} 선택 (전체 {len([d for d in dates if d])}일)")
    else:
        try:
            chosen = date.fromisoformat(day)
        except ValueError:
            raise ItDataError(f"day 는 peak / mean / YYYY-MM-DD 중 하나여야 합니다 ({day})")
        if chosen not in dates:
            raise ItDataError(f"{day} 데이터가 없습니다 (기간 {dates[0]} ~ {dates[-1]})")
    prof, filled, fallback = {}, 0, 0
    for rid, ds in hourly.items():
        if chosen == "mean":
            acc = defaultdict(list)
            for hs in ds.values():
                for h, v in hs.items():
                    acc[h].append(v)
            p = _fill({h: sum(v) / len(v) for h, v in acc.items()})
        else:
            hs = ds.get(chosen)
            if not hs:
                acc = defaultdict(list)                 # 그날 데이터가 없는 랙 → 전체 평균 프로파일
                for x in ds.values():
                    for h, v in x.items():
                        acc[h].append(v)
                hs = {h: sum(v) / len(v) for h, v in acc.items()}
                fallback += 1
            filled += 24 - len(hs)
            p = _fill(hs)
        if p:
            prof[rid] = [max(round(v, 4), 0.0) for v in p]
    if filled:
        notes.append(f"결측 시각 {filled}개(랙x시)를 앞뒤 값으로 순환 선형 보간 (가정)")
    if fallback:
        notes.append(f"선택한 날 데이터가 없는 {fallback}개 id 는 전체 기간 시각별 평균 사용")
    if chosen == "mean":
        notes.append(f"전체 {len(dates)}일의 시각별 평균 프로파일")
    period = [str(d) for d in dates if d][:1] + [str(d) for d in dates if d][-1:]
    return prof, (str(chosen) if chosen not in (None, "mean") else chosen), period, notes


# ---------- 전체 흐름 ----------
def inspect_files(files, overrides=None):
    """files: [(name, bytes)] → 표별 분석 결과 (내부 열 데이터 포함)"""
    overrides = overrides or {}
    out = []
    for name, data in files:
        for t in read_tables(name, data):
            o = overrides.get(t["label"], {})
            a = analyze_table(t, o.get("mapping"), o.get("kind"), o.get("unit"))
            a["encoding"] = t.get("encoding")
            out.append(a)
    return out


def public(a):
    """API 응답용 (열 데이터 제외)"""
    p = {k: v for k, v in a.items() if not k.startswith("_")}
    p["profiles"] = [{**a["profiles"][h], "role": next((r for r, c in a["mapping"].items() if c == h),
                                                       "wide" if h in a["wide_columns"] else "ignore"),
                      "reason": a["reasons"].get(h, "")} for h in a["columns"]]
    return p


def layout_racks(sc, cfg):
    """기류 모델 랙 [(id, design_kw|None)]. 격자 배치면 R{열:02d}-{번호:03d} 로 이름 붙임"""
    from .airflow import building_dims, rack_items, rack_positions
    items = rack_items(cfg)
    if items:
        return [(it["id"], it.get("design_kw")) for *_, it in items], None
    ew, ns, _ = building_dims(sc)
    pos, _, _ = rack_positions(cfg, ew, ns)
    return [(f"R{j + 1:02d}-{i + 1:03d}", None) for j, i, _, _ in pos], pos


def apply_to_scenario(sc, cfg, built, grid_pos=None):
    """build() 결과를 시나리오 dict 에 반영한 새 dict. 격자 배치였으면 격자 좌표로 items 를 만든다"""
    sc2, c = copy.deepcopy(sc), copy.deepcopy(cfg)
    if built["design_kw"]:
        sc2["it_load"]["design_kw"] = built["design_kw"]
    if built["utilization"]:
        sc2["it_load"]["utilization"] = built["utilization"]
    if built["rack_kw"] or built["rack_design"]:
        if not c["racks"].get("items"):
            c["racks"]["items"] = [{"id": f"R{j + 1:02d}-{i + 1:03d}", "x_m": round(x, 3), "z_m": round(z, 3), "row": j}
                                   for j, i, x, z in grid_pos]
            c["racks"]["row_density"] = None
        for it in c["racks"]["items"]:
            if it["id"] in built["rack_kw"]:
                it["kw_profile"] = built["rack_kw"][it["id"]]
            if it["id"] in built["rack_design"]:
                it["design_kw"] = built["rack_design"][it["id"]]
    sc2["cooling_layout"] = c
    return sc2


def build(analyses, sc, racks, day="peak"):
    """분석된 표들 → 시나리오 반영분과 보고서
    racks: layout_racks() 결과 [(id, design_kw|None)] (CAD 또는 격자)"""
    notes, warns = [], []
    inv = {}
    for a in analyses:
        if a["kind"] == "inventory":
            for rid, e in inventory(a).items():
                inv.setdefault(rid, {}).update(e)
    inv_norm = {norm_id(k): v.get("design_kw") for k, v in inv.items() if v.get("design_kw") is not None}
    design_total_inv = sum(v for v in inv_norm.values()) or None
    recs = []
    ts_tables = [a for a in analyses if a["kind"] in ("long", "wide", "total")]
    for a in ts_tables:
        r = records(a, inv_norm, design_total_inv or sc["it_load"]["design_kw"])
        notes += [f"[{a['label']}] {n}" for n in a.get("notes", [])]
        if a.get("unit_basis"):
            notes.append(f"[{a['label']}] 단위: {a['unit_basis']}")
        recs += r
    if not recs and not inv:
        raise ItDataError("시계열(시각 + 전력) 또는 랙 목록(랙 id + 설계 kW) 표를 찾지 못했습니다")
    if any(a["kind"] == "total" for a in ts_tables) and any(a["kind"] in ("long", "wide") for a in ts_tables):
        warns.append("랙별 표와 합계 표가 함께 있어 합계 표는 제외 (중복 계산 방지)")
        recs = [x for x in recs if x[2] is not None]

    lay_ids = [rid for rid, _ in racks]
    lay_design = dict(racks)
    total_prof = None
    rack_prof = {}
    period, chosen = [], None
    if recs:
        prof, chosen, period, n2 = day_profiles(recs, day)
        notes += n2
        total_prof = [sum(p[h] for p in prof.values()) for h in range(24)]
        rack_prof = {k: v for k, v in prof.items() if k is not None}
        neg = sum(1 for x in recs if x[3] < 0)
        if neg:
            warns.append(f"음수 전력 값 {neg}개 → 0 으로 처리")
        # 이상치: 랙 값이 그 랙 중앙값의 5배 초과
        by = defaultdict(list)
        for x in recs:
            by[x[2]].append(x[3])
        spikes = sum(sum(1 for v in vs if v > 5 * statistics.median(vs) > 0) for vs in by.values())
        if spikes:
            warns.append(f"랙 중앙값의 5배를 넘는 값 {spikes}개 (단위 혼재·순간 피크 확인 필요, 그대로 사용)")

    # id 매칭
    data_ids = sorted(set(rack_prof) | set(inv), key=natural_key)
    mapped = match_ids(data_ids, lay_ids)
    grid_assign = False
    if data_ids and not mapped and lay_ids and all(re.fullmatch(r"R\d{2}-\d{3}", x) for x in lay_ids):
        # 도면 없이 격자 배치 → 데이터 랙을 id 순서대로 격자에 배정
        mapped = dict(zip(data_ids, lay_ids))
        grid_assign = True
        notes.append("도면(CAD) 랙이 없어 데이터의 랙을 id 순서대로 기본 격자 배치에 배정 (가정)")
    un_data = [d for d in data_ids if d not in mapped]
    un_lay = [x for x in lay_ids if x not in set(mapped.values())]

    items_kw, items_design = {}, {}
    for d, lid in mapped.items():
        if d in rack_prof:
            items_kw[lid] = rack_prof[d]
        if inv.get(d, {}).get("design_kw") is not None:
            items_design[lid] = round(inv[d]["design_kw"], 3)
    # 짝 없는 데이터 부하(PDU·열 단위 id) → 짝 없는 랙에, 합계 표만 있으면 → 전체 랙에 배분
    rest = [0.0] * 24
    for _, p in prof_items(rack_prof, un_data) + ([(None, total_prof)] if total_prof and not rack_prof else []):
        rest = [a + b for a, b in zip(rest, p)]
    if any(rest) and lay_ids:
        targets = un_lay if (un_lay and rack_prof) else lay_ids
        w = [items_design.get(x) or lay_design.get(x) or 0 for x in targets]
        if sum(w) <= 0:
            w = [1.0] * len(targets)
        for x, wi in zip(targets, w):
            share = [v * wi / sum(w) for v in rest]
            items_kw[x] = [round(a + b, 4) for a, b in zip(items_kw.get(x, [0.0] * 24), share)]
        what = "합계 부하" if not rack_prof else "랙과 짝이 안 맞은 부하"
        notes.append(f"{what}(최대 {max(rest):.1f} kW)를 {'짝 없는 랙' if targets is un_lay else '전체 랙'} "
                     f"{len(targets)}개에 {'설계 kW 비례' if len(set(w)) > 1 else '균등'} 배분 (가정)")
    if items_kw:
        for x in lay_ids:
            items_kw.setdefault(x, [0.0] * 24)

    # 설계부하·가동률
    design_kw = None
    if inv_norm:
        design_kw = round(sum(inv[d]["design_kw"] for d in inv if inv[d].get("design_kw") is not None), 2)
        notes.append(f"IT 설계부하 = 랙 목록 설계 kW 합계 {design_kw} kW")
    elif lay_ids and all(lay_design.get(x) is not None for x in lay_ids):
        design_kw = round(sum(lay_design.values()), 2)
        notes.append(f"IT 설계부하 = 도면 랙 속성 설계 kW 합계 {design_kw} kW")
    total = total_prof          # 배분 전 합계 (= 랙 배분 합계, 반올림 누적 오차 없음)
    if total and not design_kw:
        design_kw = round(max(total), 2)
        notes.append(f"설계 kW 정보가 없어 측정 최대 합계 {design_kw} kW 를 IT 설계부하로 둠 (가정, CRAH 설계 풍량 기준)")
    util = [round(v / design_kw, 4) for v in total] if total and design_kw else None
    if util and max(util) > 1.0:
        warns.append(f"측정 최대 부하가 설계부하의 {max(util) * 100:.0f}% 입니다 (설계 kW 확인 필요)")
    cool = Counter(e.get("cooling") for e in inv.values() if e.get("cooling"))
    if cool:
        notes.append("랙 냉각방식: " + ", ".join(f"{k} {v}개" for k, v in cool.items()) +
                     " (냉각 방식 비율은 냉각 설비 설정에서 직접 조정 - 랙별 혼합은 아직 미지원)")
    report = {
        "notes": notes, "warnings": warns,
        "period": period, "day": chosen,
        "racks": {"data": len(data_ids), "matched": len(mapped), "layout": len(lay_ids),
                  "unmatched_data": un_data[:50], "unmatched_data_n": len(un_data),
                  "unmatched_layout_n": len(un_lay) if mapped else 0, "grid_assigned": grid_assign},
        "design_kw": design_kw, "peak_kw": round(max(total), 2) if total else None,
        "total_profile_kw": [round(v, 2) for v in total] if total else None,
    }
    return {"design_kw": design_kw, "utilization": util, "rack_kw": items_kw, "rack_design": items_design,
            "report": report, "records": recs}


# ---------- Phase 11: 기간 전체 데이터셋 ----------
DAYTYPES = ("weekday", "sat", "sun")


def daytype(d):
    wd = d.weekday()
    return "weekday" if wd < 5 else "sat" if wd == 5 else "sun"


def _mean_rows(rows):
    return [round(sum(r[h] for r in rows) / len(rows), 3) for h in range(24)]


def dataset(recs, design_kw):
    """기간 전체 (날짜, 시, 랙, kW) → 일별 24시간 합계 + 월·날유형 대표 프로파일 + 화면 요약. 날짜가 없으면 None"""
    hourly = _hourly(recs)
    dates = sorted({d for ds in hourly.values() for d in ds if d is not None})
    if not dates:
        return None
    days, observed = {}, 0
    for d in dates:
        tot = [0.0] * 24
        for ds in hourly.values():
            hs = ds.get(d)
            if not hs:
                continue
            observed += len(hs)
            for h, v in enumerate(_fill(hs)):
                tot[h] += max(v, 0.0)
        days[d] = tot
    n_ids = len(hourly)
    by_md, by_t = defaultdict(list), defaultdict(list)
    for d, tot in days.items():
        by_md[(d.month, daytype(d))].append(tot)
        by_t[daytype(d)].append(tot)
    typical = {"by_month_daytype": {f"{m}|{t}": _mean_rows(v) for (m, t), v in by_md.items()},
               "by_daytype": {t: _mean_rows(v) for t, v in by_t.items()},
               "all": _mean_rows(list(days.values()))}
    daily_kwh = {d.isoformat(): round(sum(v), 1) for d, v in days.items()}
    wk = [sum(v) for t, rows in by_t.items() if t == "weekday" for v in rows]
    we = [sum(v) for t, rows in by_t.items() if t != "weekday" for v in rows]
    ratio = round((sum(we) / len(we)) / (sum(wk) / len(wk)), 3) if wk and we else None
    coverage = round(observed / (len(dates) * 24 * n_ids), 3) if n_ids else 0.0
    msg = [f"{dates[0]} ~ {dates[-1]} {len(dates)}일, 계측값 비율 {coverage * 100:.0f}%"]
    if ratio is not None:
        msg.append(f"주말 IT 전력량은 평일의 {ratio * 100:.0f}%" + (" — 요일 차이가 큼" if abs(1 - ratio) >= 0.1 else ""))
    months = sorted({d.month for d in dates})
    return {
        "meta": {"period": [dates[0].isoformat(), dates[-1].isoformat()], "n_days": len(dates), "months": months,
                 "coverage": coverage, "design_kw": design_kw, "n_ids": n_ids},
        "daily_total_kw": {d.isoformat(): [round(x, 2) for x in v] for d, v in days.items()},
        "typical": typical,
        "summary": {"period": [dates[0].isoformat(), dates[-1].isoformat()], "n_days": len(dates), "months": months,
                    "coverage": coverage, "weekend_ratio": ratio, "daily_kwh": daily_kwh,
                    "daytype_kw": typical["by_daytype"], "sentence": " · ".join(msg)},
    }


def prof_items(prof, ids):
    return [(k, prof[k]) for k in ids if k in prof]


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s).upper())]
