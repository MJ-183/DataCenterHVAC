"""
기상 데이터 수집 → 엔진 입력 weather(24개 시간값) 변환  (Phase 5)

지원 소스
- open_meteo : Open-Meteo Historical Weather API (키 불필요, 재분석 자료 ERA5/IFS 기반 격자값 — 관측소 실측 아님)
               https://archive-api.open-meteo.com/v1/archive
- kma_asos   : 기상청 지상(종관, ASOS) 시간자료 조회서비스 (공공데이터포털, 인증키 필요, 관측소 실측)
               https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList   (전일 D-1 까지 제공)
- epw        : EnergyPlus 기상 파일 (.epw, 보통 TMY 표준기상년) — 파일로 입력

엔진 시간 기준 (engine.py)
- weather 배열의 h번째 값 = 현지 표준시 h:00 '순간값' 으로 취급 (태양 위치도 h:00 으로 계산)

시간 정렬 규칙 (소스별로 다르므로 여기서 통일)
- 기온·습도: 각 소스의 h:00 값을 그대로 사용 (EPW 는 hour=h 행 = h:00 값, h=0 은 전날 hour 24 행)
- 일사량: 세 소스 모두 '직전 1시간 평균(누적)' 값이 그 시간의 끝 시각에 붙어 있음
          (Open-Meteo: preceding hour mean / ASOS icsr: 직전 1시간 누적 / EPW: 직전 1시간 누적)
          → h:00 순간값 ≈ (h-1~h 평균 + h~h+1 평균) / 2   (가정: 시간 내 선형 변화)
- 기상청 icsr 단위: MJ/m² (1시간 누적) 로 보고 W/m² = MJ × 1e6 / 3600 으로 환산.
  공공데이터포털 문서에 단위가 명시돼 있지 않아 '가정'이며, 결과 sanity check(최대 1400 W/m²)로 감시

결측 처리
- 일사 결측: 야간(값 없음)은 0. 단, 주간 결측은 앞뒤 값으로 선형 보간
- 기온·습도 결측: 연속 3시간 이하이면 선형 보간, 그보다 길면 오류 (임의로 채우지 않음)
- 보간·가정 적용 내역은 meta.notes 에 모두 기록
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .engine import DATA_DIR, day_of_year, solar_geometry, wet_bulb

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
KMA_ASOS_URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
MJ_TO_WM2 = 1e6 / 3600.0
MAX_GAP_H = 3
GHI_MAX = 1400.0
WEATHER_DIR = DATA_DIR / "weather"

# 참고용 주요 ASOS 지점번호. 지점별 일사 관측 여부가 다르므로 기상청 지점정보로 확인할 것
KMA_STATIONS = {"서울": 108, "인천": 112, "수원": 119, "춘천": 101, "대전": 133,
                "대구": 143, "광주": 156, "부산": 159, "제주": 184}

HttpGet = Callable[[str], bytes]


class WeatherError(ValueError):
    pass


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "datacenter-hvac-sim/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        # 공공데이터포털은 키 오류를 HTTP 401/403 + 본문(JSON 또는 XML 의 returnReasonCode)으로 돌려준다 → 코드로 설명
        code = _portal_code(e.read().decode("utf-8", "replace")) if e.fp else None
        if code:
            raise kma_error(code) from e
        if e.code in (401, 403):
            raise WeatherError(f"기상 API 인증 실패 (HTTP {e.code}) — 인증키가 맞는지, 이 서비스에 활용신청·승인됐는지 확인") from e
        raise WeatherError(f"기상 API 호출 실패: {e}") from e
    except OSError as e:  # URLError, timeout 포함
        raise WeatherError(f"기상 API 호출 실패: {e}") from e


# ---------- 공통: 시계열(끝 시각 기준) → 24개 시간값 ----------
def _fill(values: list[float | None], name: str, notes: list[str], night_zero: bool = False,
          is_night: list[bool] | None = None) -> list[float]:
    v = list(values)
    if night_zero and is_night:
        for i, x in enumerate(v):
            if x is None and is_night[i]:
                v[i] = 0.0
    missing = [i for i, x in enumerate(v) if x is None]
    if not missing:
        return [float(x) for x in v]
    if len(missing) == len(v):
        raise WeatherError(f"{name}: 값이 모두 비어 있습니다")
    # 연속 결측 길이 검사
    run, longest = 0, 0
    for x in v:
        run = run + 1 if x is None else 0
        longest = max(longest, run)
    if longest > MAX_GAP_H:
        raise WeatherError(f"{name}: 연속 {longest}시간 결측 (허용 {MAX_GAP_H}시간 이하) — 다른 날짜나 소스를 사용하세요")
    known = [i for i, x in enumerate(v) if x is not None]
    for i in missing:
        lo = max((k for k in known if k < i), default=None)
        hi = min((k for k in known if k > i), default=None)
        if lo is None:
            v[i] = v[hi]
        elif hi is None:
            v[i] = v[lo]
        else:
            v[i] = v[lo] + (v[hi] - v[lo]) * (i - lo) / (hi - lo)
    notes.append(f"{name}: {len(missing)}개 시간 결측을 선형 보간 (index {missing})")
    return [float(x) for x in v]


def _radiation_to_instant(r_end: list[float]) -> list[float]:
    """r_end[k] (k=0..24) = (k-1)~k 시 평균 [W/m2] → h:00 순간값 근사 (h=0..23)"""
    if len(r_end) != 25:
        raise WeatherError("일사량 변환에는 0~24시(25개) 끝시각 값이 필요합니다")
    return [round(max(0.0, (r_end[h] + r_end[h + 1]) / 2.0), 1) for h in range(24)]


def _finish(day: date, t: list[float], rh: list[float], ghi: list[float], meta: dict) -> dict:
    rh = [round(min(100.0, max(0.0, x)), 1) for x in rh]
    t = [round(x, 2) for x in t]
    for x in t:
        if not -40.0 <= x <= 50.0:
            raise WeatherError(f"기온 {x}℃ 가 정상 범위(-40~50℃)를 벗어났습니다")
    if max(ghi) > GHI_MAX:
        raise WeatherError(f"일사량 최대 {max(ghi)} W/m2 가 {GHI_MAX} 를 넘습니다 — 단위 환산을 확인하세요")
    meta = {**meta, "date": day.isoformat(),
            "time_basis": "현지 표준시 h:00 순간값 (일사는 앞뒤 1시간 평균의 평균으로 근사)"}
    return {"weather": {"dry_bulb_c": t, "rh_pct": rh, "ghi_w_m2": ghi}, "meta": meta}


def _day_from_series(day: date, temp: dict, rh: dict, rad: dict, meta: dict) -> dict:
    """temp/rh/rad: datetime(끝 시각, 현지 표준시) → 값(None 허용). rad 단위 W/m2(직전 1시간 평균)"""
    notes: list[str] = list(meta.get("notes", []))
    t0 = datetime(day.year, day.month, day.day)
    hours = [t0 + timedelta(hours=h) for h in range(24)]
    ends = [t0 + timedelta(hours=k) for k in range(25)]
    t = _fill([temp.get(x) for x in hours], "기온", notes)
    h = _fill([rh.get(x) for x in hours], "상대습도", notes)
    r_raw = [rad.get(x) for x in ends]
    lat, lon = meta.get("latitude"), meta.get("longitude")
    if lat is None or lon is None:
        raise WeatherError("일사 야간 판정에 위도·경도가 필요합니다")
    std_mer = meta.get("std_meridian", 135.0)
    n = day_of_year(day.isoformat())
    up = [solar_geometry(n, k, lat, lon, std_mer)[0] > 0 for k in range(-1, 25)]  # k-1, k 시각의 해 유무
    night = [not up[k] and not up[k + 1] for k in range(25)]   # 구간 (k-1, k] 내내 해가 지평선 아래
    n_night = sum(1 for k in range(25) if r_raw[k] is None and night[k])
    if n_night:
        notes.append(f"일사량: 해가 진 시간 {n_night}개 구간의 빈 값을 0으로 처리")
    r = _fill(r_raw, "일사량", notes, night_zero=True, is_night=night)
    return _finish(day, t, h, _radiation_to_instant(r), {**meta, "notes": notes})


# ---------- Open-Meteo ----------
def open_meteo_url(lat: float, lon: float, start: date, end: date, timezone: str = "Asia/Seoul") -> str:
    q = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
         "hourly": "temperature_2m,relative_humidity_2m,shortwave_radiation", "timezone": timezone}
    return OPEN_METEO_URL + "?" + urllib.parse.urlencode(q)


def _parse_open_meteo(raw: bytes) -> tuple[dict, dict, dict, dict]:
    d = json.loads(raw)
    if d.get("error"):
        raise WeatherError(f"Open-Meteo 오류: {d.get('reason')}")
    hr = d["hourly"]
    times = [datetime.fromisoformat(s) for s in hr["time"]]
    mk = lambda key: dict(zip(times, hr[key]))
    info = {"grid_latitude": d.get("latitude"), "grid_longitude": d.get("longitude"),
            "elevation_m": d.get("elevation"), "timezone": d.get("timezone")}
    return mk("temperature_2m"), mk("relative_humidity_2m"), mk("shortwave_radiation"), info


def fetch_open_meteo_range(lat: float, lon: float, start: date, end: date, timezone: str = "Asia/Seoul",
                           http_get: HttpGet | None = None) -> list[dict]:
    """start~end 각 날짜의 weather. 일사 변환을 위해 end+1일 00시까지 요청"""
    tmp, rh, rad, info = _parse_open_meteo((http_get or _http_get)(open_meteo_url(lat, lon, start, end + timedelta(days=1), timezone)))
    meta = {"source": "open_meteo", "source_detail": "Open-Meteo Historical Weather API (재분석 격자값, 관측소 실측 아님)",
            "latitude": lat, "longitude": lon, **info,
            "attribution": "Weather data by Open-Meteo.com (CC BY 4.0); Copernicus C3S ERA5 등",
            "notes": []}
    out, d = [], start
    while d <= end:
        out.append(_day_from_series(d, tmp, rh, rad, meta))
        d += timedelta(days=1)
    return out


def annual_path(lat: float, lon: float, year: int, out_dir: Path | None = None) -> Path:
    return (out_dir or WEATHER_DIR) / f"annual_{lat:.2f}_{lon:.2f}_{year}.json"


def fetch_open_meteo_year(lat: float, lon: float, year: int, timezone: str = "Asia/Seoul",
                          http_get: HttpGet | None = None, today: date | None = None) -> dict:
    """1년(1/1~12/31) 일별 weather → {"meta", "days": [{"date", "weather"}]}. 끝난 연도만 (재분석 자료 지연)"""
    if year >= (today or date.today()).year:
        raise WeatherError(f"{year}년은 아직 끝나지 않았습니다 — 지난 연도를 지정하세요 (재분석 자료는 며칠 늦게 공개)")
    if year < 1940:
        raise WeatherError("Open-Meteo 재분석 자료는 1940년부터입니다")
    try:
        days = fetch_open_meteo_range(lat, lon, date(year, 1, 1), date(year, 12, 31), timezone, http_get)
    except KeyError as e:
        raise WeatherError(f"Open-Meteo 응답에 {e} 값이 없습니다 (기간 확인)") from e
    m0 = days[0]["meta"]
    notes = [f"{d['meta']['date']}: {n}" for d in days for n in d["meta"]["notes"]]
    meta = {k: m0.get(k) for k in ("source", "source_detail", "latitude", "longitude", "grid_latitude",
                                     "grid_longitude", "elevation_m", "timezone", "attribution")}
    meta.update({"year": year, "n_days": len(days), "time_basis": m0["time_basis"], "notes": notes})
    return {"meta": meta, "days": [{"date": d["meta"]["date"], "weather": d["weather"]} for d in days]}


def save_annual(annual: dict, path: Path | None = None) -> Path:
    """path 가 폴더면 그 안에 annual_<lat>_<lon>_<year>.json, 파일 경로면 그 이름으로 저장"""
    m = annual["meta"]
    p = path if path is not None and path.suffix == ".json" else annual_path(m["latitude"], m["longitude"], m["year"], path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(annual, ensure_ascii=False), encoding="utf-8")
    return p


def load_annual(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict) or not d.get("days") or "meta" not in d:
        raise WeatherError(f"{Path(path).name}: 연간 기상 파일 형식이 아닙니다 (meta, days 필요)")
    for x in d["days"]:
        w = x.get("weather") or {}
        if any(len(w.get(k) or []) != 24 for k in ("dry_bulb_c", "rh_pct", "ghi_w_m2")):
            raise WeatherError(f"{Path(path).name}: {x.get('date')} 의 기상값이 24개가 아닙니다")
    return d


OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def std_timezone(std_meridian: float) -> str:
    """표준 자오선 → 고정 오프셋 IANA 이름 (엔진은 표준시 기준이라 서머타임을 피한다). 135 → Etc/GMT-9"""
    off = int(round(std_meridian / 15.0))
    return "Etc/GMT" + ("" if off == 0 else f"{-off:+d}")


def fetch_open_meteo_forecast(lat: float, lon: float, day: date, std_meridian: float = 135.0,
                              http_get: HttpGet | None = None) -> dict:
    """Open-Meteo 예보 → 그날 weather (Phase 14). 일사 변환용으로 다음날 0시까지 요청. 시간 정렬 규칙은 과거값과 같음"""
    q = {"latitude": lat, "longitude": lon, "start_date": day.isoformat(),
         "end_date": (day + timedelta(days=1)).isoformat(),
         "hourly": "temperature_2m,relative_humidity_2m,shortwave_radiation", "timezone": std_timezone(std_meridian)}
    tmp, rh, rad, info = _parse_open_meteo((http_get or _http_get)(OPEN_METEO_FORECAST_URL + "?" + urllib.parse.urlencode(q)))
    meta = {"source": "open_meteo_forecast", "source_detail": "Open-Meteo 예보 (실제와 다를 수 있음)",
            "latitude": lat, "longitude": lon, **info, "std_meridian": std_meridian,
            "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)", "notes": []}
    return _day_from_series(day, tmp, rh, rad, meta)


def fetch_open_meteo(lat: float, lon: float, day: date, timezone: str = "Asia/Seoul",
                     http_get: HttpGet | None = None) -> dict:
    return fetch_open_meteo_range(lat, lon, day, day, timezone, http_get)[0]


# ---------- 기상청 ASOS (공공데이터포털) ----------
def kma_url(service_key: str, stn: int, start: datetime, end: datetime, page: int = 1, rows: int = 999) -> str:
    q = {"pageNo": page, "numOfRows": rows, "dataType": "JSON", "dataCd": "ASOS", "dateCd": "HR",
         "startDt": start.strftime("%Y%m%d"), "startHh": start.strftime("%H"),
         "endDt": end.strftime("%Y%m%d"), "endHh": end.strftime("%H"), "stnIds": stn}
    # 포털의 'Encoding' 키(%포함)는 그대로, 'Decoding' 키는 URL 인코딩해서 붙인다 (이중 인코딩 방지)
    key = service_key if "%" in service_key else urllib.parse.quote(service_key, safe="")
    return f"{KMA_ASOS_URL}?serviceKey={key}&" + urllib.parse.urlencode(q)


def _num(x) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def kma_key() -> str | None:
    """기상청(공공데이터포털) 인증키 — 매번 backend/.env 를 다시 읽음 (화면에서 저장하면 바로 반영)"""
    from . import secrets
    return secrets.get("KMA_SERVICE_KEY")


# 공공데이터포털 Open API 공통 오류코드 (기상청 ASOS 활용가이드 '첨부. Open API 에러 코드 정리' 와 대조함)
KMA_ERRORS = {
    "01": "어플리케이션 에러", "02": "데이터베이스 에러", "03": "데이터 없음", "04": "HTTP 에러",
    "05": "서비스 연결 실패", "10": "잘못된 요청 파라미터", "11": "필수 요청 파라미터 없음",
    "12": "해당 오픈API 서비스가 없거나 폐기됨", "20": "서비스 접근 거부 — 이 서비스의 활용신청이 승인됐는지 확인",
    "21": "일시적으로 사용할 수 없는 인증키", "22": "일일 요청 한도 초과 — 내일 다시 시도",
    "30": "등록되지 않은 인증키 (HTTP 403) — 이 키로 이 서비스에 활용신청이 안 돼 있습니다. 공공데이터포털에서 "
          "이 서비스를 활용신청하세요 (다른 서비스가 정상이면 키 자체는 맞음, 승인 후 반영까지 시간이 걸릴 수 있음)",
    "31": "기한이 만료된 인증키 — 포털에서 활용기간 연장", "32": "등록되지 않은 IP", "33": "서명되지 않은 호출",
    "99": "기타 에러",
}


def kma_error(code: str, msg: str | None = None) -> "WeatherError":
    return WeatherError(f"기상청 API 오류 {code}: {KMA_ERRORS.get(code, msg or '알 수 없는 오류')}"
                        + (f" ({msg})" if msg and code not in KMA_ERRORS else ""))


def _portal_code(txt: str) -> str | None:
    """포털 게이트웨이 오류 본문 → 오류코드. JSON {"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"returnReasonCode":"30"}}} 또는 같은 구조의 XML"""
    m = re.search(r"returnReasonCode\W+(\d+)", txt) or re.search(r"<resultCode>\s*(\d+)\s*</resultCode>", txt)
    return m.group(1) if m else None


def _parse_kma_page(raw: bytes) -> tuple[list[dict], int]:
    txt = raw.decode("utf-8", "replace")
    try:
        d = json.loads(txt)
    except ValueError:
        code = _portal_code(txt)
        if code:
            raise kma_error(code)
        raise WeatherError(f"기상청 API 응답이 JSON 이 아닙니다 (인증키·요청 확인): {txt[:300]}")
    if "OpenAPI_ServiceResponse" in d:   # 게이트웨이 오류가 HTTP 200 + JSON 으로 오는 경우
        raise kma_error(_portal_code(txt) or "99")
    resp = d.get("response", {})
    head = resp.get("header", {})
    code = str(head.get("resultCode", ""))
    if code == "03":
        return [], 0
    if code != "00":
        raise kma_error(code, head.get("resultMsg"))
    body = resp.get("body", {})
    items = (body.get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items, int(body.get("totalCount") or len(items))


def _kma_items(key, stn, start, end, yesterday, http_get):
    """start 0시 ~ end 다음날 0시(전일을 넘으면 end 23시) 시간자료, 페이지 반복 (1페이지 최대 999행)"""
    t_start = datetime(start.year, start.month, start.day, 0)
    last = end + timedelta(days=1)
    t_end = datetime(last.year, last.month, last.day, 0) if last <= yesterday else \
        datetime(end.year, end.month, end.day, 23)
    items, page = [], 1
    while True:
        got, total = _parse_kma_page((http_get or _http_get)(kma_url(key, stn, t_start, t_end, page)))
        items += got
        if not got or len(items) >= total:
            break
        page += 1
    if not items:
        raise WeatherError(f"지점 {stn}, {start}~{end} 자료가 없습니다")
    return items


def _kma_series(items):
    """ASOS 행 → (기온, 습도, 일사[W/m2, 직전 1시간 평균], 전운량[0~10]) dict, 키 = 관측 시각(끝 시각)"""
    tmp, rh, rad, cloud = {}, {}, {}, {}
    for it in items:
        tm = datetime.strptime(str(it["tm"]).strip()[:13], "%Y-%m-%d %H")
        tmp[tm], rh[tm] = _num(it.get("ta")), _num(it.get("hm"))
        mj = _num(it.get("icsr"))
        rad[tm] = None if mj is None else mj * MJ_TO_WM2
        cloud[tm] = _num(it.get("dc10Tca"))
    return tmp, rh, rad, cloud


def fetch_kma_asos_range(stn: int, start: date, end: date, service_key: str | None = None,
                         http_get: HttpGet | None = None, today: date | None = None,
                         latitude: float = 37.57, longitude: float = 126.98) -> list[dict]:
    """latitude/longitude: 관측소 근사 위치 (일사 야간 판정에만 사용, 기본 서울)"""
    key = service_key or kma_key()
    if not key:
        raise WeatherError("기상청 API 인증키가 없습니다 — '데이터 입력' 화면에서 입력하거나 backend/.env 의 KMA_SERVICE_KEY 에 설정")
    yesterday = (today or date.today()) - timedelta(days=1)
    if end > yesterday:
        raise WeatherError(f"기상청 ASOS 시간자료는 전일({yesterday})까지만 제공됩니다")
    items = _kma_items(key, stn, start, end, yesterday, http_get)
    tmp, rh, rad, _ = _kma_series(items)
    name = str(items[0].get("stnNm", "")).strip()
    meta = {"source": "kma_asos", "source_detail": "기상청 지상(종관, ASOS) 시간자료 (관측소 실측)",
            "station_id": stn, "station_name": name, "latitude": latitude, "longitude": longitude,
            "attribution": "출처: 기상청 (공공데이터포털, 공공누리 제1유형)",
            "notes": ["일사(icsr)를 MJ/m² 1시간 누적값으로 보고 W/m² 로 환산 (단위 가정)"]}
    out, d = [], start
    while d <= end:
        out.append(_day_from_series(d, tmp, rh, rad, meta))
        d += timedelta(days=1)
    return out


def fetch_kma_asos(stn: int, day: date, service_key: str | None = None, http_get: HttpGet | None = None,
                   today: date | None = None, latitude: float = 37.57, longitude: float = 126.98) -> dict:
    return fetch_kma_asos_range(stn, day, day, service_key, http_get, today, latitude, longitude)[0]


# ---------- 기상청 ASOS 1년 (연간 예측용) ----------
# 일사 관측이 없는 관측소(예: 해남 261)는 다른 관측소 일사 또는 전운량으로 추정한다 (가정 모델, 계수는 문헌값)
HAURWITZ_A, HAURWITZ_B = 1098.0, 0.057     # 맑은 날 GHI = A·cosZ·exp(-B/cosZ) [W/m2] (Haurwitz 1945)
KC_A, KC_B = 0.75, 3.4                     # 구름 감쇠 1 - A·(N/8)^B, N = 운량[옥타] (Kasten & Czeplak 1980)
SOLAR_MIN_COVERAGE = 0.8                   # 낮 시간 중 일사 관측이 이 비율 이상이면 그 관측값 사용


def clear_sky_ghi(cos_z: float) -> float:
    return HAURWITZ_A * cos_z * math.exp(-HAURWITZ_B / cos_z) if cos_z > 0.01 else 0.0


def cloud_ghi(tm_end: datetime, cloud10: float, lat: float, lon: float, std_mer: float = 135.0) -> float:
    """(tm_end-1h, tm_end] 평균 일사 추정 [W/m2]: 구간 중앙 시각의 맑은 날 일사 x 운량 감쇠. cloud10 = 전운량(0~10)"""
    mid = tm_end - timedelta(minutes=30)
    cz = solar_geometry(day_of_year(mid.date().isoformat()), mid.hour + mid.minute / 60, lat, lon, std_mer)[0]
    octa = min(max(cloud10, 0.0), 10.0) * 0.8
    return clear_sky_ghi(cz) * (1 - KC_A * (octa / 8) ** KC_B)


def _daytime_coverage(rad: dict, lat, lon, std_mer) -> float:
    day = [tm for tm in rad if solar_geometry(day_of_year(tm.date().isoformat()), tm.hour - 0.5, lat, lon, std_mer)[0] > 0.1]
    return sum(1 for tm in day if rad[tm] is not None) / len(day) if day else 0.0


def kma_annual_path(stn: int, year: int, solar: str, out_dir: Path | None = None) -> Path:
    return (out_dir or WEATHER_DIR) / f"annual_kma_{stn}_{year}_{solar}.json"


def fetch_kma_asos_year(stn: int, year: int, latitude: float, longitude: float, std_meridian: float = 135.0,
                        solar_station: int | None = None, solar: str = "auto", service_key: str | None = None,
                        http_get: HttpGet | None = None, today: date | None = None, progress=None) -> dict:
    """기상청 ASOS 1년 시간자료 → 연간 기상 {"meta", "days"} (Open-Meteo 연간 파일과 같은 형식).
    기온·습도: stn. 일사: solar="auto" 이면 stn 일사 관측 → solar_station 일사 관측 → 전운량 추정 순,
    "cloud" 이면 전운량 추정만. latitude/longitude = 일사 추정·야간 판정 위치 (시나리오 사이트)"""
    key = service_key or kma_key()
    if not key:
        raise WeatherError("기상청 API 인증키가 없습니다 — '연간 예측' 화면에서 입력하세요")
    today = today or date.today()
    if year >= today.year:
        raise WeatherError(f"{year}년은 아직 끝나지 않았습니다 — 지난 연도를 지정하세요 (ASOS 는 전일까지 제공)")
    start, end, yesterday = date(year, 1, 1), date(year, 12, 31), today - timedelta(days=1)
    if progress:
        progress(0.05, f"기상청 ASOS {stn} 지점 {year}년 받는 중")
    items = _kma_items(key, stn, start, end, yesterday, http_get)
    tmp, rh, rad, cloud = _kma_series(items)
    name = str(items[0].get("stnNm", "")).strip()
    notes, solar_src = [], None
    if solar == "auto" and _daytime_coverage(rad, latitude, longitude, std_meridian) >= SOLAR_MIN_COVERAGE:
        solar_src = f"{name}({stn}) 일사 관측"
    elif solar == "auto" and solar_station and solar_station != stn:
        if progress:
            progress(0.35, f"일사: {solar_station} 지점 관측 받는 중")
        s_items = _kma_items(key, solar_station, start, end, yesterday, http_get)
        s_rad = _kma_series(s_items)[2]
        if _daytime_coverage(s_rad, latitude, longitude, std_meridian) >= SOLAR_MIN_COVERAGE:
            rad = s_rad
            solar_src = f"{str(s_items[0].get('stnNm', '')).strip()}({solar_station}) 일사 관측 (다른 관측소 값을 빌려 씀)"
            notes.append(f"{name}({stn})은 일사를 관측하지 않아 {solar_src}")
    if solar_src is None:   # 전운량으로 추정 (일사 관측이 없는 시간만이 아니라 1년 전체를 같은 방식으로)
        rad = {tm: None if c is None else round(cloud_ghi(tm, c, latitude, longitude, std_meridian), 1)
               for tm, c in cloud.items()}
        solar_src = "전운량으로 추정 (맑은 날 일사 Haurwitz x 운량 감쇠 Kasten-Czeplak, 가정 모델)"
        notes.append(f"일사: {name}({stn}) 일사 관측이 없어 전운량(dc10Tca)으로 추정한 값입니다")
    meta = {"source": "kma_asos", "source_detail": f"기상청 ASOS 시간자료 · {name}({stn}) · 일사: {solar_src}",
            "station_id": stn, "station_name": name, "solar_source": solar_src,
            "latitude": latitude, "longitude": longitude, "std_meridian": std_meridian,
            "attribution": "출처: 기상청 (공공데이터포털, 공공누리 제1유형)", "notes": []}
    days, prev, d = [], None, start
    while d <= end:
        if progress and d.day == 1:
            progress(0.6 + 0.35 * (d.month - 1) / 12, f"{d.month}월 정리 중")
        try:
            x = _day_from_series(d, tmp, rh, rad, meta)
            prev = x
            notes += [f"{d}: {n}" for n in x["meta"]["notes"] if "해가 진 시간" not in n]   # 야간 0 처리는 매일 같아 생략
        except WeatherError as e:
            if prev is None:   # 1월 1일부터 결측 → 다음 정상일을 쓰도록 나중에 채움
                days.append({"date": d.isoformat(), "weather": None})
                notes.append(f"{d}: {e} → 다음 정상일 값으로 대체")
                d += timedelta(days=1)
                continue
            x = prev
            notes.append(f"{d}: {e} → 전날 값으로 대체")
        days.append({"date": d.isoformat(), "weather": x["weather"]})
        d += timedelta(days=1)
    nxt = next((x["weather"] for x in days if x["weather"]), None)
    if nxt is None:
        raise WeatherError(f"지점 {stn} {year}년 자료가 모두 결측입니다")
    for x in days:
        if x["weather"] is None:
            x["weather"] = nxt
    meta.update({"year": year, "n_days": len(days), "time_basis": "현지 표준시 h:00 순간값 (일사는 앞뒤 1시간 평균의 평균)",
                 "notes": notes, "solar_method": "observed" if "관측" in solar_src else "cloud"})
    return {"meta": meta, "days": days}


def _check_asos(key, http_get, today):
    d = (today or date.today()) - timedelta(days=1)
    fetch_kma_asos(108, d, key, http_get, today)
    return f"서울(108) {d} 시간자료 수신"


KMA_VILAGE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
VILAGE_BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)   # 단기예보 발표 시각 (활용가이드 대조 전 — 사용자 정리 기준)
VILAGE_DELAY_MIN = 10                              # 발표 후 조회 가능까지 (가정, 가이드 대조 전)


def latest_vilage_base(now: datetime) -> datetime:
    """now 기준 조회 가능한 가장 최근 단기예보 발표 시각"""
    t = now - timedelta(minutes=VILAGE_DELAY_MIN)
    for back in range(0, 2):
        d = t.date() - timedelta(days=back)
        hs = [h for h in VILAGE_BASE_HOURS if back or h <= t.hour]
        if hs:
            return datetime(d.year, d.month, d.day, hs[-1])
    raise AssertionError("unreachable")


def vilage_url(service_key: str, base: datetime, nx: int, ny: int, page: int = 1, rows: int = 1000) -> str:
    q = {"pageNo": page, "numOfRows": rows, "dataType": "JSON", "base_date": base.strftime("%Y%m%d"),
         "base_time": base.strftime("%H%M"), "nx": nx, "ny": ny}
    key = service_key if "%" in service_key else urllib.parse.quote(service_key, safe="")
    return f"{KMA_VILAGE_URL}?serviceKey={key}&" + urllib.parse.urlencode(q)


# 하늘상태 → 전운량(0~10) 대표값. 기상청 SKY 구분 맑음(0~5)·구름많음(6~8)·흐림(9~10) 의 가운데 (가정). 강수 중이면 10
SKY_CLOUD10 = {1: 2.5, 3: 7.0, 4: 9.5}
PTY_LABEL = {0: "없음", 1: "비", 2: "비/눈", 3: "눈", 4: "소나기"}
SKY_LABEL = {1: "맑음", 3: "구름많음", 4: "흐림"}


def fetch_kma_vilage(lat: float, lon: float, day: date, std_meridian: float = 135.0, service_key: str | None = None,
                     http_get: HttpGet | None = None, now: datetime | None = None) -> dict:
    """기상청 단기예보(동네예보 격자) → 그날 24시간 Weather + 강수형태·강수확률.
    기온 TMP·습도 REH 는 정시 예보값, 일사는 하늘상태(SKY)·강수형태(PTY) → 전운량 → 맑은 날 일사 x 운량 감쇠 (가정 모델)"""
    from .kma_grid import to_grid
    key = service_key or kma_key()
    if not key:
        raise WeatherError("기상청 API 인증키가 없습니다 — '데이터 입력'에서 입력하세요")
    nx, ny = to_grid(lat, lon)
    base = latest_vilage_base(now or datetime.now())
    items = []
    for b in (base, base - timedelta(hours=3)):     # 발표 직후 자료가 아직 없으면(03) 직전 발표
        items, page = [], 1
        while True:
            got, total = _parse_kma_page((http_get or _http_get)(vilage_url(key, b, nx, ny, page)))
            items += got
            if not got or len(items) >= total:
                break
            page += 1
        if items:
            base = b
            break
    v: dict[str, dict[datetime, str]] = {}
    for it in items:
        tm = datetime.strptime(f"{it['fcstDate']}{it['fcstTime']}", "%Y%m%d%H%M")
        v.setdefault(it["category"], {})[tm] = str(it["fcstValue"]).strip()
    t0 = datetime(day.year, day.month, day.day)
    hours = [t0 + timedelta(hours=h) for h in range(25)]
    if not any(h in v.get("TMP", {}) for h in hours[:24]):
        raise WeatherError(f"단기예보({base:%m-%d %H}시 발표, 격자 {nx},{ny})에 {day} 예보가 없습니다 — 단기예보는 약 3일 뒤까지만 제공")
    num = lambda cat, h: _num(v.get(cat, {}).get(h))
    tmp = {h: num("TMP", h) for h in hours}
    reh = {h: num("REH", h) for h in hours}
    cloud = {}
    for h in hours:
        sky, pty = num("SKY", h), num("PTY", h)
        cloud[h] = None if sky is None else 10.0 if pty else SKY_CLOUD10.get(int(sky), 7.0)
    rad = {}
    for k, end in enumerate(hours):   # 구간 (end-1h, end] — 앞뒤 정시 운량 중 큰 값 (가정)
        cs = [c for c in (cloud.get(end - timedelta(hours=1)), cloud.get(end)) if c is not None]
        rad[end] = round(cloud_ghi(end, max(cs), lat, lon, std_meridian), 1) if cs else None
    meta = {"source": "kma_vilage", "source_detail": f"기상청 단기예보 ({base:%m-%d %H}시 발표, 격자 {nx},{ny})",
            "grid": [nx, ny], "base_time": base.isoformat(), "latitude": lat, "longitude": lon, "std_meridian": std_meridian,
            "attribution": "출처: 기상청 (공공데이터포털, 공공누리 제1유형)",
            "notes": ["일사: 하늘상태(SKY)·강수형태(PTY) → 전운량 대표값(맑음 2.5·구름많음 7·흐림 9.5·강수 10) → "
                      "맑은 날 일사 x 운량 감쇠 (가정 모델)"]}
    out = _day_from_series(day, tmp, reh, rad, meta)
    lab = lambda d, x: d.get(int(x)) if x is not None else None
    out["precip"] = {"pop_pct": [num("POP", h) for h in hours[:24]],
                     "pty": [lab(PTY_LABEL, num("PTY", h)) for h in hours[:24]],
                     "sky": [lab(SKY_LABEL, num("SKY", h)) for h in hours[:24]],
                     "pcp": [v.get("PCP", {}).get(h) for h in hours[:24]]}
    return out


def _check_vilage(key, http_get, today):
    base = latest_vilage_base(datetime.now())
    for b in (base, base - timedelta(hours=3)):     # 발표 직후 자료가 아직 없으면(03) 직전 발표
        items, total = _parse_kma_page((http_get or _http_get)(vilage_url(key, b, 60, 127, rows=10)))
        if items:
            return f"서울 종로(60, 127) {b:%m-%d %H}시 발표 예보 수신 (항목 {total}개)"
    raise WeatherError("단기예보 자료 없음 (03) — 잠시 후 다시 확인")


# 인증키 확인용 서비스 목록 (공공데이터포털은 서비스마다 활용신청이 따로 필요): (id, 이름, 확인 함수)
KMA_SERVICES: list[tuple[str, str, Callable]] = [
    ("vilage", "단기예보 조회서비스 (내일 운전 브리핑)", _check_vilage),
    ("asos", "지상(종관, ASOS) 시간자료 (지역 관측 · 연간 예측 · 다음 달 브리핑)", _check_asos),
]


def kma_check(service_key: str | None = None, http_get: HttpGet | None = None, today: date | None = None) -> list[dict]:
    """인증키로 각 서비스를 한 번씩 호출해 본다 → [{service, name, ok, message}]"""
    key = service_key or kma_key()
    out = []
    for sid, name, fn in KMA_SERVICES:
        if not key:
            out.append({"service": sid, "name": name, "ok": False, "message": "인증키 없음"})
            continue
        try:
            out.append({"service": sid, "name": name, "ok": True, "message": fn(key, http_get, today)})
        except WeatherError as e:
            out.append({"service": sid, "name": name, "ok": False, "message": str(e)})
    return out


# ---------- EPW ----------
def parse_epw(text: str) -> tuple[dict, dict, dict, dict, dict]:
    """EPW → (기온, 습도, GHI) 시계열. 키: (월, 일, 끝시각 hour 1~24). 연도는 무시(TMY 는 월마다 연도가 다름)"""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][0].strip().upper() != "LOCATION":
        raise WeatherError("EPW 파일 형식이 아닙니다 (첫 줄이 LOCATION 이어야 함)")
    loc = rows[0]
    info = {"location": ", ".join(x for x in loc[1:4] if x), "latitude": _num(loc[6]),
            "longitude": _num(loc[7]), "tz_offset_h": _num(loc[8]), "elevation_m": _num(loc[9])}
    tmp, rh, ghi = {}, {}, {}
    for r in rows[8:]:
        if len(r) < 16:
            continue
        k = (int(r[1]), int(r[2]), int(r[3]))
        t, h, g = _num(r[6]), _num(r[8]), _num(r[13])
        tmp[k] = None if t is None or t >= 99.9 else t      # EPW 결측 코드 99.9 / 999 / 9999
        rh[k] = None if h is None or h >= 999 else h
        ghi[k] = None if g is None or g >= 9999 else g
    if len(tmp) < 24:
        raise WeatherError("EPW 데이터 행이 부족합니다")
    return tmp, rh, ghi, info, {"n_rows": len(tmp)}


def weather_from_epw(text: str, month: int, day: int, year_for_engine: int = 2026, name: str = "",
                     parsed: tuple | None = None) -> dict:
    """parsed: parse_epw(text) 결과를 미리 넘기면 다시 읽지 않음 (여러 날을 꺼낼 때)"""
    tmp, rh, ghi, info, _ = parsed or parse_epw(text)
    target = date(year_for_engine, month, day)  # 엔진 태양 계산용 날짜 (연도는 TMY 와 무관)
    prev = target - timedelta(days=1)
    t0 = datetime(target.year, target.month, target.day)

    def series(src):
        # 0시 = 전날 hour 24 행, k시(1~24) = 당일 hour k 행 (EPW hour 는 1시간 구간의 끝 시각)
        out = {t0: src.get((prev.month, prev.day, 24))}
        for k in range(1, 25):
            out[t0 + timedelta(hours=k)] = src.get((target.month, target.day, k))
        return out
    if (month, day, 1) not in tmp:
        raise WeatherError(f"EPW 에 {month}월 {day}일 자료가 없습니다")
    if (prev.month, prev.day, 24) not in tmp:
        # 1월 1일 0시는 12월 31일 24시로 순환 (TMY 는 1년 1회 반복으로 가정)
        tmp[(prev.month, prev.day, 24)] = tmp.get((12, 31, 24))
        rh[(prev.month, prev.day, 24)] = rh.get((12, 31, 24))
        ghi[(prev.month, prev.day, 24)] = ghi.get((12, 31, 24))
    meta = {"source": "epw", "source_detail": "EnergyPlus 기상파일 (보통 TMY 표준기상년 — 특정 연도 실측 아님)",
            "file": name, **info, "std_meridian": 15.0 * (info["tz_offset_h"] or 9.0), "notes": []}
    return _day_from_series(target, series(tmp), series(rh), series(ghi), meta)


# ---------- 설계일 선정 ----------
def summarize_day(d: dict) -> dict:
    w = d["weather"]
    wb = [wet_bulb(t, h) for t, h in zip(w["dry_bulb_c"], w["rh_pct"])]
    return {"date": d["meta"]["date"], "max_dry_bulb_c": max(w["dry_bulb_c"]),
            "mean_dry_bulb_c": round(sum(w["dry_bulb_c"]) / 24, 2),
            "max_wet_bulb_c": round(max(wb), 2), "mean_wet_bulb_c": round(sum(wb) / 24, 2),
            "daily_ghi_kwh_m2": round(sum(w["ghi_w_m2"]) / 1000, 2)}


def design_days(days: list[dict]) -> dict:
    """기간 중 냉방 설계에 쓸 날: 최고 건구(외피·외기 부하), 최고 습구(냉각탑·칠러 COP), 습구 중앙값(대표일)"""
    s = [summarize_day(d) for d in days]
    by_wb = sorted(s, key=lambda x: x["mean_wet_bulb_c"])
    return {"hottest_dry_bulb": max(s, key=lambda x: x["max_dry_bulb_c"]),
            "hottest_wet_bulb": max(s, key=lambda x: x["max_wet_bulb_c"]),
            "typical_wet_bulb": by_wb[len(by_wb) // 2],
            "n_days": len(s), "days": s}


# ---------- 저장 / 시나리오 반영 ----------
def apply_to_scenario(scenario: dict, day: dict) -> dict:
    sc = json.loads(json.dumps(scenario))
    sc["weather"] = day["weather"]
    sc["site"]["date"] = day["meta"]["date"]
    m = day["meta"]
    where = m.get("station_name") or m.get("location") or f"{m.get('latitude')},{m.get('longitude')}"
    sc["_note"] = f"기상: {m['source']} ({where}) {m['date']} — {m['source_detail']}. 그 외 값은 가정값."
    return sc


def save_day(day: dict, out_dir: Path = WEATHER_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = day["meta"]
    tag = m.get("station_id") or (Path(m["file"]).stem if m.get("file") else
                                  f"{m.get('latitude')}_{m.get('longitude')}")
    p = out_dir / f"{m['source']}_{tag}_{m['date']}.json"
    p.write_text(json.dumps(day, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------- CLI ----------
def _main(argv=None):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    ap = argparse.ArgumentParser(description="기상 데이터 → 시뮬레이터 weather 변환")
    ap.add_argument("source", choices=["open-meteo", "kma", "epw"])
    ap.add_argument("--date", help="YYYY-MM-DD (epw 는 MM-DD 도 가능)")
    ap.add_argument("--start"), ap.add_argument("--end")
    ap.add_argument("--year", type=int, help="연간 수집 (open-meteo): 1/1~12/31 → data/weather/annual_<lat>_<lon>_<year>.json")
    ap.add_argument("--lat", type=float, default=37.57), ap.add_argument("--lon", type=float, default=126.98)
    ap.add_argument("--stn", type=int, default=108, help="ASOS 지점번호 (서울 108)")
    ap.add_argument("--epw", help=".epw 파일 경로")
    ap.add_argument("--scenario", help="이 시나리오의 weather·date 를 바꿔 --scenario-out 으로 저장")
    ap.add_argument("--scenario-out")
    a = ap.parse_args(argv)

    if a.year:
        if a.source != "open-meteo":
            sys.exit("--year 연간 수집은 open-meteo 만 지원합니다")
        ann = fetch_open_meteo_year(a.lat, a.lon, a.year)
        print("saved", save_annual(ann), f"({ann['meta']['n_days']}일, 보간 메모 {len(ann['meta']['notes'])}건)")
        return
    if a.start and a.end:
        s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
        if a.source == "open-meteo":
            days = fetch_open_meteo_range(a.lat, a.lon, s, e)
        elif a.source == "kma":
            days = fetch_kma_asos_range(a.stn, s, e, latitude=a.lat, longitude=a.lon)
        else:
            sys.exit("epw 는 --date 로 하루씩 변환합니다")
        dd = design_days(days)
        for k in ("hottest_dry_bulb", "hottest_wet_bulb", "typical_wet_bulb"):
            print(k, dd[k])
            save_day(next(x for x in days if x["meta"]["date"] == dd[k]["date"]))
        return
    if not a.date:
        sys.exit("--date 또는 --start/--end 를 지정하세요")
    if a.source == "open-meteo":
        day = fetch_open_meteo(a.lat, a.lon, date.fromisoformat(a.date))
    elif a.source == "kma":
        day = fetch_kma_asos(a.stn, date.fromisoformat(a.date), latitude=a.lat, longitude=a.lon)
    else:
        parts = [int(x) for x in a.date.split("-")]
        mm, dd_ = parts[-2], parts[-1]
        day = weather_from_epw(Path(a.epw).read_text(encoding="utf-8", errors="replace"), mm, dd_,
                               name=Path(a.epw).name)
    p = save_day(day)
    print("saved", p)
    print(json.dumps(summarize_day(day), ensure_ascii=False))
    for n in day["meta"]["notes"]:
        print(" -", n)
    if a.scenario:
        sc = json.loads(Path(a.scenario).read_text(encoding="utf-8"))
        out = Path(a.scenario_out or Path(a.scenario).with_name(Path(a.scenario).stem + "_" + day["meta"]["date"] + ".json"))
        out.write_text(json.dumps(apply_to_scenario(sc, day), ensure_ascii=False, indent=2), encoding="utf-8")
        print("scenario saved", out)


if __name__ == "__main__":
    _main()
