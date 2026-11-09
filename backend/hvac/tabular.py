"""
표 형식 파일 공통 파서 (Phase 10, itdata.py 에서 분리) - IT 계측·BMS 내보내기 파일이 함께 쓴다.
CSV / TSV / ; / | 구분, Excel(.xlsx, 시트마다 별도 표). 인코딩 utf-8(BOM) → cp949 → latin-1 순.
값 해석(숫자·날짜·시각), 헤더 줄 찾기, 열 프로파일(값 모양 통계)까지. 열 역할 매칭은 사용하는 쪽이 한다.
"""
import csv
import math
import io
import re
import statistics
from datetime import date, datetime, time

ENCODINGS = ("utf-8-sig", "cp949", "latin-1")
MAX_ROWS = 2_000_000


class TabularError(ValueError):
    pass


ItDataError = TabularError   # 기존 메시지·예외 이름 호환 (아래 코드는 itdata 에서 옮겨 온 그대로)


# ---------- 표 읽기 ----------
def _decode(data: bytes):
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ItDataError("텍스트 인코딩을 알 수 없습니다")


def read_tables(name: str, data: bytes) -> list[dict]:
    """파일 → [{label, rows(list[list]), encoding}] (엑셀은 시트별)"""
    low = name.lower()
    if low.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001 - openpyxl 은 다양한 예외를 던짐
            raise ItDataError(f"엑셀 파일을 읽을 수 없습니다: {e}")
        out = []
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(v not in (None, "") for v in r)]
            if rows:
                out.append({"label": f"{name} [{ws.title}]", "rows": rows, "encoding": "xlsx"})
        if not out:
            raise ItDataError("엑셀에 데이터가 있는 시트가 없습니다")
        return out
    if low.endswith(".xls"):
        raise ItDataError("구형 .xls 는 지원하지 않습니다. .xlsx 또는 CSV 로 저장해 주세요")
    text, enc = _decode(data)
    sample = text[:20000]
    counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
    delim = max(counts, key=counts.get) if max(counts.values()) else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        raise ItDataError("빈 파일입니다")
    return [{"label": name, "rows": rows, "encoding": enc, "delimiter": delim}]


# ---------- 값 해석 ----------
_NUM_RE = re.compile(r"^\s*[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][-+]?\d+)?\s*%?\s*$")
_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M:%S",
               "%Y/%m/%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d %H:%M",
               "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H", "%Y%m%d%H")
_D_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%m/%d/%Y")
_T_RE = re.compile(r"^\s*(오전|오후|AM|PM|am|pm)?\s*(\d{1,2}):(\d{2})(:(\d{2})(\.\d+)?)?\s*(AM|PM|am|pm)?\s*$")


def to_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip()
    if not s or not _NUM_RE.match(s):
        return None
    return float(s.replace(",", "").replace("%", ""))


def to_time(v):
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    m = _T_RE.match(str(v or ""))
    if not m:
        return None
    h, mi, se = int(m.group(2)), int(m.group(3)), int(m.group(5) or 0)
    ap = (m.group(1) or m.group(7) or "").upper()
    if ap in ("오후", "PM") and h < 12:
        h += 12
    if ap in ("오전", "AM") and h == 12:
        h = 0
    if h == 24 and mi == 0:
        return time(23, 59, 59)      # 24:00 표기 → 그날 마지막 (1시간 버킷 23시)
    return time(h, mi, se) if h < 24 and mi < 60 and se < 60 else None


def to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for f in _D_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def to_datetime(v):
    if isinstance(v, datetime):
        return v
    if v is None or isinstance(v, (int, float)):
        return None
    s = str(v).strip().replace("  ", " ")
    if not s or len(s) < 8:
        return None
    s2 = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", s)          # 시간대 표기는 무시 (현지 시각으로 간주)
    s2 = re.sub(r"\.\d+$", "", s2)
    for f in _DT_FORMATS:
        try:
            return datetime.strptime(s2, f)
        except ValueError:
            pass
    m = re.match(r"^(\S+)\s+(.+)$", s2)                    # '2026-08-01 오후 1:00:00' 같은 형태
    if m:
        d, t = to_date(m.group(1)), to_time(m.group(2))
        if d and t:
            return datetime.combine(d, t)
    return None


def norm_header(h):
    return re.sub(r"[\s_\-\.\[\]\(\)\{\}/:]+", "", str(h or "").lower())


def unit_in_header(h):
    s = str(h or "")
    low = s.lower()
    if re.search(r"kwh|kw\s*h|전력량", low):
        return "kWh"
    if re.search(r"mwh", low):
        return "MWh"
    if re.search(r"(^|[^a-z])mw([^a-z]|$)|\[mw\]|\(mw\)", low):
        return "MW"
    if re.search(r"(^|[^a-z])kw([^a-z]|$)|kilowatt|\[kw\]|\(kw\)|kw$", low):
        return "kW"
    if re.search(r"(^|[^a-z])w([^a-z]|$)|watt|\[w\]|\(w\)|_w$", low):
        return "W"
    if "%" in s or re.search(r"percent|pct|가동률|사용률", low):
        return "%"
    return None


# ---------- 헤더·열 분석 ----------
def find_header(rows):
    """헤더 행 번호 (없으면 -1). 처음 30줄 중 '문자 칸이 절반 이상 + 다음 줄에 숫자/날짜 칸이 있는' 첫 줄"""
    width = max(len(r) for r in rows[:50])
    for i, r in enumerate(rows[:30]):
        cells = [c for c in r if c not in (None, "") and str(c).strip()]
        if len(cells) < min(2, width):
            continue
        texty = sum(1 for c in cells if to_num(c) is None and to_datetime(c) is None and to_date(c) is None)
        if texty / len(cells) < 0.5:
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else []
        if any(to_num(c) is not None or to_datetime(c) or to_date(c) or to_time(c) for c in nxt):
            return i
    return -1


def profile_column(name, vals):
    nn = [v for v in vals if v not in (None, "") and str(v).strip() != ""]
    n = max(len(nn), 1)
    nums = [x for x in (to_num(v) for v in nn) if x is not None]
    p = {"name": name, "n": len(nn), "num_frac": len(nums) / n,
         "dt_frac": sum(1 for v in nn if to_datetime(v)) / n,
         "date_frac": sum(1 for v in nn if not isinstance(v, (int, float)) and to_date(v) and not to_datetime(v)) / n,
         "time_frac": sum(1 for v in nn if not isinstance(v, (int, float, datetime)) and to_time(v)) / n,
         "unique": len(set(map(str, nn))) / n, "unit": unit_in_header(name)}
    if nums:
        p.update(min=min(nums), max=max(nums), median=statistics.median(nums))
        p["int_frac"] = sum(1 for x in nums if float(x).is_integer()) / len(nums)
        inc = sum(1 for a, b in zip(nums, nums[1:]) if b >= a)
        p["mono_frac"] = inc / max(len(nums) - 1, 1)
    texts = [str(v).strip() for v in nn if to_num(v) is None]
    p["idlike_frac"] = sum(1 for t in texts if re.search(r"\d", t) and len(t) <= 40) / n
    p["samples"] = [str(v)[:40] for v in nn[:5]]
    return p


