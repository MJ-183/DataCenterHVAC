"""
실측 비교용 장기 시간 데이터 (Phase 19).

일반 형식: 시간 단위 표. timestamp(현지 표준시), it_kw(필수), 계측 항목 열 여러 개, (선택) 외기 건구·상대습도.
1시간보다 촘촘한 표는 정시 구간 평균으로 묶는다. 빈 칸·숫자가 아닌 값은 결측(NaN).

항목 매핑(mapping): 실측 열 → 시뮬 전력 키 묶음 + PUE 정의
  {"timestamp": "timestamp_mst", "it": "it_power_kw",
   "items": {"cooling_kw": ["tower_fan"], "pump_kw": ["chw_pump", "cw_pump", "liquid_pump"], "hvac_kw": ["crah_fan"]},
   "labels": {"cooling_kw": "냉각탑계", ...},
   "pue_terms": ["it", "cooling_kw", "pump_kw", "hvac_kw", "plug_and_light_kw"], "ups_loss": 0,
   "pue_column": "pue", "dry_bulb": null, "rh": null}
실측 PUE = (Σ pue_terms + ups_loss × IT) / IT.  시뮬 PUE = (IT + UPS 손실 + 조명 + 냉방 설비 합) / IT.

프리셋(data/real_presets/*.json): 데이터 파일·매핑·기간·기상·시설 사실/가정. 시나리오 입력마다
provenance 에 출처를 남긴다 — 사실 = source 'published' (ref = 출처), 가정 = source 'assumed'.
"""
import copy
import hashlib
import json
import math
import re
from collections import OrderedDict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import provenance
from . import weather as wx
from .engine import DATA_DIR
from .itdata import role_scores, profile_column
from .tabular import TabularError, find_header, norm_header, read_tables, to_datetime, to_num

REPO = DATA_DIR.parents[1]
PRESET_DIR = DATA_DIR / "real_presets"

# 시뮬 전력 키 (engine plant.power_kw) → 이름, 보정 계수, 계산식
SIM_KEYS = ("tower_fan", "chw_pump", "cw_pump", "liquid_pump", "crah_fan", "chiller")
SIM_LABEL = {"tower_fan": "냉각탑 팬", "chw_pump": "냉수 펌프", "cw_pump": "냉각수 펌프", "liquid_pump": "액체 루프 펌프",
             "crah_fan": "CRAH 팬", "chiller": "칠러"}
COEF = {"tower_fan": "tower_fan_kw_per_kw", "chw_pump": "chw_pump_kw_per_kw", "cw_pump": "cw_pump_kw_per_kw",
        "liquid_pump": "liquid_pump_kw_per_kw", "crah_fan": "crah_fan_heat_ratio"}
FORMULA = {"tower_fan": "냉각탑 팬 = 계수 × 방열량(q_rej)", "cw_pump": "냉각수 계수 × 방열량",
           "chw_pump": "냉수 계수 × 공기 부하", "liquid_pump": "액체 계수 × 액체 부하",
           "crah_fan": "CRAH 팬 = 발열비 × 공기측 부하(액체 회수분 제외)", "chiller": "칠러 = 칠러 부하 ÷ COP"}

# 열 이름 → 매핑 후보 (정규화 헤더에 포함되면). 개수 열(n_*, count_*)은 제외
SUGGEST = {
    "it": ["itpower", "itkw", "itload", "it전력", "it부하", "serverpower"],
    "tower_fan": ["cooling", "tower", "drycooler", "냉각탑", "냉각"],
    "pump": ["pump", "펌프"],
    "crah_fan": ["hvac", "crah", "fanwall", "ahu", "공조", "fan", "팬"],
    "chiller": ["chiller", "칠러", "냉동기"],
    "lighting": ["plugandlight", "plug", "light", "조명", "콘센트"],
    "pue": ["pue"],
    "dry_bulb": ["drybulb", "outsideairtemp", "oat", "외기온도", "건구"],
    "rh": ["outsideairhumidity", "humidity", "rh", "습도"],
}
COUNT_RE = re.compile(r"^(n|count|cnt)[_\s]", re.I)
PUE_CHECK_TOL = 0.001

_DS_CACHE: "OrderedDict[str, dict]" = OrderedDict()


class RealDataError(ValueError):
    pass


# ---------- 표 → 시간 데이터 ----------
def _num(v):
    if v is None:
        return math.nan
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if math.isfinite(v) else math.nan
    s = str(v).strip()
    if not s:
        return math.nan
    try:
        x = float(s)
        return x if math.isfinite(x) else math.nan
    except ValueError:
        x = to_num(s)
        return math.nan if x is None else x


def _ts(v):
    if isinstance(v, datetime):
        return v
    s = str(v or "").strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return to_datetime(s)


def _hour_index(d: datetime) -> int:
    return int((d - datetime(1970, 1, 1)).total_seconds() // 3600)


def hour_dt(h: int) -> datetime:
    return datetime(1970, 1, 1) + timedelta(hours=int(h))


def _guess_timestamp(header, body):
    best, score = None, 0.0
    for j, h in enumerate(header):
        p = profile_column(h, [r[j] if j < len(r) else None for r in body[:500]])
        s = role_scores(p)["timestamp"]
        if s > score:
            best, score = h, s
    return best


def parse_table(name: str, data: bytes, timestamp: str | None = None) -> dict:
    """파일 → {"columns": {열: 시간 평균 배열}, "start": 첫 정시(시간 인덱스), "hours": 길이, ...}
    시간 인덱스 = 1970-01-01 00:00 부터의 시간 수 (현지 표준시 그대로, 시간대 변환 없음)"""
    try:
        t = read_tables(name, data)[0]
    except TabularError as e:
        raise RealDataError(str(e))
    rows = t["rows"]
    hi = find_header(rows)
    if hi < 0:
        raise RealDataError("헤더 줄을 찾지 못했습니다 (첫 줄에 열 이름이 필요합니다)")
    header = [str(c or "").strip() for c in rows[hi]]
    body = rows[hi + 1:]
    ts_col = timestamp or _guess_timestamp(header, body)
    if ts_col not in header:
        raise RealDataError(f"일시 열을 찾지 못했습니다 (지정: {timestamp!r}, 열: {', '.join(header)})")
    ti = header.index(ts_col)
    idx, vals, bad = [], [], 0
    for r in body:
        d = _ts(r[ti]) if ti < len(r) else None
        if d is None:
            bad += 1
            continue
        idx.append(_hour_index(d))
        vals.append([_num(r[j]) if j < len(r) else math.nan for j in range(len(header))])
    if not idx:
        raise RealDataError(f"'{ts_col}' 열에서 일시를 읽지 못했습니다")
    idx = np.asarray(idx)
    start, n = int(idx.min()), int(idx.max() - idx.min() + 1)
    raw = np.asarray(vals, dtype=float)
    cols = {}
    pos = idx - start
    for j, h in enumerate(header):
        if j == ti or not h:
            continue
        v = raw[:, j]
        ok = ~np.isnan(v)
        s = np.zeros(n)
        c = np.zeros(n)
        np.add.at(s, pos[ok], v[ok])      # 정시 구간 평균 (이미 시간 단위면 그대로)
        np.add.at(c, pos[ok], 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            cols[h] = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    return {"name": name, "timestamp": ts_col, "start": start, "hours": n, "columns": cols,
            "rows": len(body), "bad_rows": bad, "hash": hashlib.sha1(data).hexdigest()}


def to_store(ds: dict) -> dict:
    """데이터셋 저장소(JSON)용 — NaN 은 null"""
    return {"timestamp": ds["timestamp"], "start": ds["start"], "hours": ds["hours"], "hash": ds["hash"],
            "rows": ds["rows"], "bad_rows": ds["bad_rows"],
            "columns": {k: [None if math.isnan(x) else round(float(x), 6) for x in v] for k, v in ds["columns"].items()}}


def from_store(d: dict) -> dict:
    cols = {k: np.array([math.nan if x is None else x for x in v], dtype=float) for k, v in d["columns"].items()}
    return {**{k: d[k] for k in ("timestamp", "start", "hours", "hash", "rows", "bad_rows")},
            "name": d.get("meta", {}).get("name", ""), "columns": cols}


def load_file(path: Path, timestamp: str | None = None) -> dict:
    """서버 파일 (프리셋) — 내용 해시로 메모리 캐시"""
    data = path.read_bytes()
    key = hashlib.sha1(data).hexdigest() + str(timestamp)
    if key in _DS_CACHE:
        _DS_CACHE.move_to_end(key)
        return _DS_CACHE[key]
    ds = parse_table(path.name, data, timestamp)
    _DS_CACHE[key] = ds
    while len(_DS_CACHE) > 4:
        _DS_CACHE.popitem(last=False)
    return ds


def series(ds: dict, col: str, h0: int, n: int) -> np.ndarray:
    """열 col 의 [h0, h0+n) 시간값 (범위 밖·결측 = NaN)"""
    out = np.full(n, np.nan)
    v = ds["columns"].get(col)
    if v is None:
        return out
    a, b = max(h0, ds["start"]), min(h0 + n, ds["start"] + ds["hours"])
    if b > a:
        out[a - h0:b - h0] = v[a - ds["start"]:b - ds["start"]]
    return out


def fill_day(v: np.ndarray) -> np.ndarray:
    """하루 안 선형 보간, 양 끝은 가장 가까운 값 (모두 결측이면 그대로)"""
    ok = ~np.isnan(v)
    if ok.all() or not ok.any():
        return v
    x = np.arange(len(v))
    return np.interp(x, x[ok], v[ok])


def period_hours(p0: date, p1: date) -> tuple[int, int]:
    h0 = _hour_index(datetime.combine(p0, datetime.min.time()))
    return h0, ((p1 - p0).days + 1) * 24


# ---------- 열 분석·매핑 제안 ----------
def _suggest_role(col: str):
    if COUNT_RE.match(col):
        return None
    hn = norm_header(col)
    best, score = None, 0
    for role, kws in SUGGEST.items():
        for kw in kws:
            k = norm_header(kw)
            if hn == k:
                s = 3
            elif len(k) >= 3 and k in hn:
                s = 2
            elif len(k) == 2 and re.search(rf"(^|[^a-z]){k}([^a-z]|$)", col.lower()):
                s = 1
            else:
                continue
            if s > score:
                best, score = role, s
    return best


def suggest_mapping(ds: dict) -> dict:
    roles = {c: _suggest_role(c) for c in ds["columns"]}
    pick = lambda r: next((c for c, x in roles.items() if x == r), None)
    items, labels = {}, {}
    for c, r in roles.items():
        if r == "tower_fan":
            items[c], labels[c] = ["tower_fan"], "냉각탑계"
        elif r == "pump":
            items[c], labels[c] = ["chw_pump", "cw_pump", "liquid_pump"], "펌프"
        elif r == "crah_fan":
            items[c], labels[c] = ["crah_fan"], "공조 팬"
        elif r == "chiller":
            items[c], labels[c] = ["chiller"], "칠러"
    it, light = pick("it"), pick("lighting")
    return {"timestamp": ds["timestamp"], "it": it, "items": items, "labels": labels, "lighting": light,
            "pue_column": pick("pue"), "pue_terms": [x for x in ["it", *items, light] if x], "ups_loss": 0.0,
            "dry_bulb": pick("dry_bulb"), "rh": pick("rh")}


def inspect(ds: dict) -> dict:
    """열 후보·결측 비율·기간 범위·매핑 제안 (화면 '데이터 확인')"""
    cols = []
    for c, v in ds["columns"].items():
        ok = ~np.isnan(v)
        cols.append({"name": c, "role": _suggest_role(c), "missing_pct": round(100 * (1 - ok.mean()), 1),
                     **({"min": round(float(v[ok].min()), 3), "max": round(float(v[ok].max()), 3),
                         "mean": round(float(v[ok].mean()), 3)} if ok.any() else {})})
    first, last = hour_dt(ds["start"]), hour_dt(ds["start"] + ds["hours"] - 1)
    return {"timestamp": ds["timestamp"], "range": [first.isoformat(sep=" "), last.isoformat(sep=" ")],
            "hours": ds["hours"], "rows": ds["rows"], "bad_rows": ds["bad_rows"], "columns": cols,
            "mapping": suggest_mapping(ds)}


def check_mapping(ds: dict, mapping: dict):
    need = [mapping["it"], *mapping["items"], *(c for c in mapping["pue_terms"] if c != "it")]
    need += [c for c in (mapping.get("lighting"), mapping.get("pue_column"), mapping.get("dry_bulb"), mapping.get("rh")) if c]
    missing = [c for c in need if c not in ds["columns"]]
    if missing:
        raise RealDataError(f"데이터에 없는 열: {', '.join(missing)}")
    bad = [k for ks in mapping["items"].values() for k in ks if k not in SIM_KEYS]
    if bad:
        raise RealDataError(f"알 수 없는 시뮬 키: {', '.join(bad)} (가능: {', '.join(SIM_KEYS)})")
    used = [k for ks in mapping["items"].values() for k in ks]
    if len(used) != len(set(used)):
        raise RealDataError("시뮬 키 하나가 실측 항목 둘 이상에 매핑되었습니다")


def pue_check(ds: dict, mapping: dict, p0: date, p1: date) -> dict | None:
    """기간 실측 PUE 를 매핑 정의로 다시 계산해 원본 pue 열과 비교 (데이터·매핑 확인 장치)"""
    col = mapping.get("pue_column")
    if not col:
        return None
    h0, n = period_hours(p0, p1)
    it = series(ds, mapping["it"], h0, n)
    num = it * (1 + mapping.get("ups_loss", 0.0))
    for c in mapping["pue_terms"]:
        if c != "it":
            num = num + series(ds, c, h0, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        calc = num / it
    raw = series(ds, col, h0, n)
    ok = ~np.isnan(calc) & ~np.isnan(raw) & (it > 0)
    if not ok.any():
        return {"hours": 0, "median_abs_diff": None, "ok": False,
                "warning": "PUE 재계산과 원본 PUE 가 함께 있는 시간이 없습니다"}
    med = float(np.median(np.abs(calc[ok] - raw[ok])))
    good = med <= PUE_CHECK_TOL
    return {"hours": int(ok.sum()), "median_abs_diff": round(med, 6), "ok": good,
            "warning": None if good else f"매핑으로 다시 계산한 PUE 와 원본 '{col}' 열의 차이 중앙값이 {med:.4f} 로 "
                                         f"{PUE_CHECK_TOL} 을 넘습니다 — 항목 매핑이나 PUE 항목을 확인하세요"}


# ---------- 기상 ----------
@lru_cache(maxsize=4)
def _tmy(path: str, mtime: float) -> dict:
    text = Path(path).read_text(encoding="latin-1")
    parsed = wx.parse_epw(text)
    out = {}
    for m in range(1, 13):
        for d in range(1, 32):
            try:
                out[(m, d)] = wx.weather_from_epw(text, m, d, parsed=parsed)["weather"]
            except (ValueError, wx.WeatherError):
                continue
    return out


def epw_hours(path: Path) -> list[tuple[float, float]]:
    """EPW 전체 시간의 (건구, 상대습도) — 칠러 민감도 분석용"""
    out = []
    for line in path.read_text(encoding="latin-1").splitlines()[8:]:
        c = line.split(",")
        if len(c) > 8:
            out.append((float(c[6]), float(c[8])))
    return out


class WeatherSource:
    """날짜 → 엔진 기상 24시간 + 출처. 현장 온습도(있으면) 우선, 없거나 3시간 넘게 결측이면 EPW(TMY3). 일사는 EPW"""

    def __init__(self, epw: Path | None = None, site: dict | None = None, site_temp: str | None = None,
                 site_rh: str | None = None, temp_unit: str = "C"):
        self.epw = epw
        self.tmy = _tmy(str(epw), epw.stat().st_mtime) if epw else None
        self.site, self.t_col, self.rh_col, self.unit = site, site_temp, site_rh, temp_unit
        if self.tmy is None and self.site is None:
            raise RealDataError("기상 자료가 없습니다: 현장 외기 열(건구·상대습도)이나 EPW 파일이 필요합니다")

    def describe(self) -> dict:
        return {"epw": self.epw.name if self.epw else None,
                "site": bool(self.site), "site_columns": [self.t_col, self.rh_col] if self.site else None}

    def day(self, d: date):
        w = None
        if self.tmy is not None:
            w = self.tmy.get((d.month, d.day)) or self.tmy[(2, 28)]
        if self.site is None:
            return dict(w), "tmy3"
        h0, _ = period_hours(d, d)
        t = series(self.site, self.t_col, h0, 24)
        rh = series(self.site, self.rh_col, h0, 24)
        if np.isnan(t).sum() > 3 or np.isnan(rh).sum() > 3:
            if w is None:
                raise RealDataError(f"{d}: 현장 기상 결측이 많고 EPW 도 없습니다")
            return dict(w), "tmy3(현장 결측)"
        if self.unit == "F":
            t = (t - 32) / 1.8
        t, rh = fill_day(t), np.clip(fill_day(rh), 1, 100)
        ghi = w["ghi_w_m2"] if w else [0.0] * 24
        return {"dry_bulb_c": [round(float(x), 2) for x in t], "rh_pct": [round(float(x), 1) for x in rh],
                "ghi_w_m2": ghi}, ("site" if w else "site(일사 0, 가정)")


# ---------- 프리셋 ----------
def presets() -> list[dict]:
    out = []
    for p in sorted(PRESET_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": d["id"], "label": d["label"], "description": d["description"],
                    "periods": d["periods"], "available": (REPO / d["data"]["csv"]).exists(), "sources": d.get("sources", [])})
    return out


def preset(pid: str) -> dict:
    p = PRESET_DIR / f"{pid}.json"
    if not re.match(r"^[a-z0-9_]{1,40}$", pid) or not p.exists():
        raise RealDataError(f"'{pid}' 실측 프리셋이 없습니다")
    return json.loads(p.read_text(encoding="utf-8"))


def preset_dataset(pr: dict) -> dict:
    f = REPO / pr["data"]["csv"]
    if not f.exists():
        raise RealDataError(f"프리셋 데이터 파일이 없습니다: {pr['data']['csv']}")
    return load_file(f, pr["data"].get("timestamp"))


def preset_weather(pr: dict) -> WeatherSource:
    w = pr["weather"]
    epw = REPO / w["epw"] if w.get("epw") and (REPO / w["epw"]).exists() else None
    site = None
    sc = w.get("site_csv")
    if sc and (REPO / sc).exists():
        site = load_file(REPO / sc, w.get("site_timestamp"))
    return WeatherSource(epw, site, w.get("site_temp"), w.get("site_rh"), w.get("site_temp_unit", "C"))


def preset_scenario(pr: dict, overrides: dict | None = None) -> tuple[dict, list[dict]]:
    """프리셋 시나리오 (engine dict) + 입력 출처 표. overrides = {경로: 값} 은 사용자 수정 (source 'user')"""
    sc = json.loads((DATA_DIR / pr["scenario"]["base"]).read_text(encoding="utf-8"))
    for k in pr["scenario"].get("drop", []):
        sc.pop(k, None)
    table = []
    for x in pr["scenario"]["inputs"]:
        src = "published" if x["basis"] == "fact" else "assumed"
        provenance.set_value(sc, x["path"], copy.deepcopy(x["value"]), src, ref=x.get("ref"), note=x.get("note"))
        table.append({"path": x["path"], "label": x.get("label", x["path"]), "value": x["value"],
                      "basis": x["basis"], "ref": x.get("ref"), "note": x.get("note"), "editable": x["basis"] != "fact"})
    for path, v in (overrides or {}).items():
        provenance.set_value(sc, path, copy.deepcopy(v), "user", note="실측 비교 화면에서 수정")
        row = next((r for r in table if r["path"] == path), None)
        if row:
            row.update(value=v, basis="user", note="사용자 수정")
        else:
            table.append({"path": path, "label": path, "value": v, "basis": "user", "ref": None,
                          "note": "사용자 수정", "editable": True})
    for p in list(sc.get("provenance") or {}):   # 프리셋 기록에는 되돌리기 값이 필요 없음 (기준 시나리오가 따로 없음)
        sc["provenance"][p].pop("previous", None)
        sc["provenance"][p].pop("absent_before", None)
    for x in pr["scenario"].get("notes", []):
        table.append({"path": None, "label": x["label"], "value": x["value"], "basis": x["basis"],
                      "ref": x.get("ref"), "note": x.get("note"), "editable": False})
    return sc, table
