"""
기상 데이터 수집 API (Phase 5).  계산은 하지 않고 hvac.weather 로 수집·변환만 한다.
- GET  /weather/sources      : 소스 목록, 기상청 인증키 설정 여부
- POST /weather/fetch        : open_meteo / kma_asos 하루치 → Weather + meta
- POST /weather/epw          : EPW 파일 텍스트 + 월/일 → Weather + meta
- POST /weather/design-days  : 기간 조회 후 최고 건구·최고 습구·대표일 선정
- GET/PUT/DELETE /weather/kma-key, POST /weather/kma-key/test : 기상청 인증키 입력·확인
기상청 인증키는 backend/.env 의 KMA_SERVICE_KEY. 화면에서 입력하면 .env 에 저장되고 바로 쓰인다 (서버 재시작 불필요).
키 자체는 프론트로 돌려보내지 않는다 (끝 4자리만)
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hvac import provenance, secrets
from hvac import weather as wx
from hvac.schemas import Scenario, Weather

router = APIRouter(prefix="/weather", tags=["weather"])
KEY_NAME = "KMA_SERVICE_KEY"
MAX_RANGE_DAYS = 366


class WeatherOut(BaseModel):
    weather: Weather
    meta: dict


class FetchRequest(BaseModel):
    source: Literal["open_meteo", "kma_asos"]
    date: date
    latitude: float | None = Field(default=None, ge=-90, le=90, description="open_meteo 필수, kma_asos 는 일사 야간 판정용(기본 서울)")
    longitude: float | None = Field(default=None, ge=-180, le=180, description="open_meteo 용")
    timezone: str = "Asia/Seoul"
    station_id: int | None = Field(default=None, description="kma_asos 지점번호 (서울 108)")


class RangeRequest(BaseModel):
    source: Literal["open_meteo", "kma_asos"]
    start: date
    end: date
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = "Asia/Seoul"
    station_id: int | None = None


class EpwRequest(BaseModel):
    epw_text: str
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    year_for_engine: int = Field(default=2026, description="엔진 태양 계산용 연도 (TMY 와 무관)")
    file_name: str = ""


def _fetch_range(source, start, end, lat, lon, tz, stn) -> list[dict]:
    if end < start or (end - start).days >= MAX_RANGE_DAYS:
        raise HTTPException(422, f"기간은 1~{MAX_RANGE_DAYS}일이어야 합니다")
    try:
        if source == "open_meteo":
            if lat is None or lon is None:
                raise HTTPException(422, "open_meteo 는 latitude, longitude 가 필요합니다")
            return wx.fetch_open_meteo_range(lat, lon, start, end, tz)
        if stn is None:
            raise HTTPException(422, "kma_asos 는 station_id 가 필요합니다")
        if not wx.kma_key():
            raise HTTPException(503, "기상청 API 인증키가 없습니다 — '데이터 입력' 화면에서 입력하세요")
        return wx.fetch_kma_asos_range(stn, start, end, latitude=lat if lat is not None else 37.57,
                                       longitude=lon if lon is not None else 126.98)
    except wx.WeatherError as e:
        raise HTTPException(502 if "호출 실패" in str(e) else 422, str(e))


@router.get("/sources")
def sources():
    return {
        "open_meteo": {"key_required": False, "kind": "재분석 격자값 (관측소 실측 아님)", "license": "CC BY 4.0"},
        "kma_asos": {"key_required": True, "key_configured": bool(wx.kma_key()),
                     "kind": "기상청 관측소 실측 (전일까지)", "stations_example": wx.KMA_STATIONS},
        "epw": {"key_required": False, "kind": "EnergyPlus 기상파일 (보통 TMY)"},
    }


class KeyIn(BaseModel):
    key: str = Field(min_length=10, max_length=400, description="공공데이터포털 일반 인증키 (Encoding/Decoding 어느 쪽이든)")
    test: bool = Field(default=True, description="저장 후 바로 서비스 호출로 확인")


def _key_status(tests=None):
    k = wx.kma_key()
    return {"configured": bool(k), "masked": secrets.mask(k),
            "services": [{"service": s, "name": n} for s, n, _ in wx.KMA_SERVICES],
            "tests": tests}


@router.get("/kma-key")
def kma_key_status():
    return _key_status()


@router.put("/kma-key")
def kma_key_save(req: KeyIn):
    key = req.key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(422, "인증키에 공백·줄바꿈이 있습니다 — 포털의 키를 그대로 복사하세요")
    secrets.put(KEY_NAME, key)
    return _key_status(wx.kma_check(key) if req.test else None)


@router.post("/kma-key/test")
def kma_key_test():
    if not wx.kma_key():
        raise HTTPException(503, "기상청 API 인증키가 없습니다")
    return _key_status(wx.kma_check())


@router.delete("/kma-key")
def kma_key_clear():
    secrets.put(KEY_NAME, "")
    return _key_status()


@router.post("/fetch", response_model=WeatherOut)
def fetch(req: FetchRequest):
    return _fetch_range(req.source, req.date, req.date, req.latitude, req.longitude, req.timezone,
                        req.station_id)[0]


@router.post("/epw", response_model=WeatherOut)
def epw(req: EpwRequest):
    try:
        return wx.weather_from_epw(req.epw_text, req.month, req.day, req.year_for_engine, req.file_name)
    except (wx.WeatherError, ValueError) as e:
        raise HTTPException(422, str(e))


@router.post("/design-days")
def design_days(req: RangeRequest):
    days = _fetch_range(req.source, req.start, req.end, req.latitude, req.longitude, req.timezone,
                        req.station_id)
    dd = wx.design_days(days)
    by_date = {d["meta"]["date"]: d for d in days}
    picks = {k: by_date[dd[k]["date"]] for k in ("hottest_dry_bulb", "hottest_wet_bulb", "typical_wet_bulb")}
    return {"summary": dd, "weather": picks}


# ---------- 지역 선택 (데이터 입력) — 이후 모든 분석이 이 조건을 따른다 ----------
DAY_LABEL = {"hottest_wet_bulb": "최고 습구온도일", "hottest_dry_bulb": "최고 건구온도일", "typical_wet_bulb": "습구 중앙값 대표일"}


class RegionRequest(BaseModel):
    scenario: Scenario
    station_id: int = Field(description="기상청 ASOS 관측소 (사이트 위치·연간 기상·브리핑 예보 위치)")
    solar_station_id: int | None = Field(default=None, description="일사 관측이 없을 때 빌려 쓸 관측소")
    solar: Literal["auto", "cloud"] = "auto"
    year: int = Field(ge=1940, le=2100, description="연간 분석 관측 연도")
    design_day: Literal["hottest_wet_bulb", "hottest_dry_bulb", "typical_wet_bulb", "keep"] = Field(
        default="hottest_wet_bulb", description="운전 현황(24시간)에 쓸 그 해의 하루. keep = 기상은 그대로")


@router.post("/region", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def region(req: RegionRequest):
    """관측소 좌표(기상자료개방포털 관측지점정보)로 사이트 위치·이름을 바꾸고 관측소·일사·연도를 저장.
    (운전 현황·AI 요약·브리핑 제목이 사이트 이름을, 브리핑 예보가 사이트 위치의 단기예보 격자를 따른다)
    design_day 를 고르면 그 해 ASOS 관측에서 그날 24시간 기상을 골라 운전 현황 기상으로 넣는다"""
    from api.annual import kma_year
    from hvac.kma_stations import ASOS_COORDS, ASOS_STATIONS
    for s in (req.station_id, req.solar_station_id):
        if s is not None and s not in ASOS_COORDS:
            raise HTTPException(422, f"관측소 {s} 는 ASOS 유인관서 목록에 없습니다")
    sc = req.scenario.to_engine_dict()
    lat, lon, _ = ASOS_COORDS[req.station_id]
    name = ASOS_STATIONS[req.station_id][0]
    src = f"기상청 관측지점정보 {name}({req.station_id})"
    for path, v in (("site.name", f"{name} 데이터센터"), ("site.latitude", lat), ("site.longitude", lon), ("site.std_meridian", 135.0),
                    ("site.asos_station_id", req.station_id), ("site.asos_solar_station_id", req.solar_station_id),
                    ("site.asos_solar", req.solar), ("site.weather_year", req.year)):
        provenance.set_value(sc, path, v, "user", note=f"지역 선택: {src}")
    if req.solar_station_id is None:
        sc["site"].pop("asos_solar_station_id", None)
    if req.design_day == "keep":
        return sc
    _, ann = kma_year(sc["site"])
    days = [{"weather": d["weather"], "meta": {"date": d["date"]}} for d in ann["days"]]
    pick = wx.design_days(days)[req.design_day]["date"]
    day = next(d for d in ann["days"] if d["date"] == pick)
    provenance.set_value(sc, "weather", day["weather"], "weather_api", ref=f"kma_asos:{req.station_id}:{pick}")
    sc["site"]["date"] = pick
    m = ann["meta"]
    sc["_note"] = (f"지역: {name}({req.station_id}) 위치 {lat}, {lon} · 운전 현황 기상 = {req.year}년 "
                   f"{DAY_LABEL[req.design_day]} {pick} ({m.get('source_detail', '기상청 ASOS')}). 그 외 값은 가정값.")
    return sc
