"""
연간 시뮬레이션 API (Phase 9).
- GET  /annual/weather-files : data/weather/annual_*.json 목록
- POST /annual/run           : 시나리오 + (연간 기상 파일 이름 | 위도·경도·연도) → 365일 x 냉각 방식 3종 집계
  위도·경도·연도로 요청하면 저장된 파일을 쓰고, 없으면 Open-Meteo 에서 받아 저장한다. 결과는 메모리 캐시(LRU)
"""
import hashlib
import json
import re
from collections import OrderedDict
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.platform import register
from hvac import annual, datasets, provenance, weather as wx
from hvac.annual_opt import optimize_year
from hvac.optimize import apply_candidate
from hvac.schemas import CRAH_PER_SIDE_MAX, CoolingLayout, Scenario, ScheduleEntry

router = APIRouter(prefix="/annual", tags=["annual"])
FILE_RE = re.compile(r"^annual_(-?\d+(\.\d+)?_-?\d+(\.\d+)?_\d{4}|kma_\d+_\d{4}_(cloud|auto\d*))\.json$")
CACHE: "OrderedDict[str, dict]" = OrderedDict()
CACHE_SIZE = 8


class AnnualRequest(BaseModel):
    scenario: Scenario
    source: Literal["kma_asos", "open_meteo"] = Field(
        default="kma_asos", description="kma_asos = 기상청 ASOS 시간자료 (인증키 필요), open_meteo = 재분석 격자값")
    station_id: int | None = Field(default=None, description="kma_asos 관측소 (비우면 site.asos_station_id, 그것도 없으면 서울 108)")
    solar_station_id: int | None = Field(default=None, description="관측소에 일사 관측이 없을 때 빌려 쓸 관측소 (비우면 site 값)")
    solar: Literal["auto", "cloud"] | None = Field(
        default=None, description="auto = 관측 일사 우선, cloud = 전운량 추정만 (비우면 site 값, 그것도 없으면 auto)")
    weather_file: str | None = Field(default=None, description="(내부·테스트용) data/weather 의 저장된 연간 기상 파일")
    latitude: float | None = Field(default=None, ge=-90, le=90, description="비우면 시나리오 사이트 위도")
    longitude: float | None = Field(default=None, ge=-180, le=180)
    year: int | None = Field(default=None, ge=1940, le=2100, description="비우면 site.weather_year, 그것도 없으면 작년")
    base_multiplier: float = Field(default=1.0, gt=0, le=5, description="일별 IT 배율 기본값")
    growth_pct_per_year: float = Field(default=0.0, ge=-90, le=500, description="연간 증설률 [%/년], 선형")
    it_source: Literal["scenario", "dataset"] = Field(default="scenario", description="dataset = 시나리오의 IT 데이터셋 사용")
    coupled: bool = Field(default=False, description="연성 모델(기류·배관) 월별 kWh 도 계산")


def _weather(req: AnnualRequest, sc: dict):
    if req.weather_file:
        if not FILE_RE.match(req.weather_file):
            raise HTTPException(422, "weather_file 은 annual_<lat>_<lon>_<year>.json 형식의 파일 이름이어야 합니다")
        p = wx.WEATHER_DIR / req.weather_file
        if not p.exists():
            raise HTTPException(404, f"{req.weather_file} 파일이 없습니다")
    else:
        if req.source == "kma_asos":
            return kma_year(sc["site"], req.year, req.station_id, req.solar_station_id, req.solar,
                            req.latitude, req.longitude)
        lat = req.latitude if req.latitude is not None else sc["site"]["latitude"]
        lon = req.longitude if req.longitude is not None else sc["site"]["longitude"]
        year = req.year or sc["site"].get("weather_year") or date.today().year - 1
        p = wx.annual_path(lat, lon, year)
        try:
            if not p.exists():
                wx.save_annual(wx.fetch_open_meteo_year(lat, lon, year))
        except wx.WeatherError as e:
            raise HTTPException(502 if "호출 실패" in str(e) else 422, str(e))
    return p, _load(p)


def _load(p):
    try:
        return wx.load_annual(p)
    except (wx.WeatherError, ValueError) as e:
        raise HTTPException(422, str(e))


def kma_year(site: dict, year=None, station=None, solar_station=None, solar=None, lat=None, lon=None):
    """기상청 ASOS 1년치 (저장 파일 우선, 없으면 받아서 저장). 비운 값은 시나리오 site 의 지역 설정을 따른다
    ('데이터 입력 → 지역 · 기상청 API' 에서 고른 관측소·일사 출처·연도)"""
    year = year or site.get("weather_year") or date.today().year - 1
    stn = station or site.get("asos_station_id") or 108
    s_stn = solar_station or site.get("asos_solar_station_id")
    solar = solar or site.get("asos_solar") or "auto"
    tag = "cloud" if solar == "cloud" else f"auto{s_stn or ''}"
    p = wx.kma_annual_path(stn, year, tag)
    try:
        if not p.exists():
            if not wx.kma_key():
                raise HTTPException(503, "기상청 API 인증키가 없습니다 — '데이터 입력 → 지역 · 기상청 API'에 입력하세요")
            wx.save_annual(wx.fetch_kma_asos_year(
                stn, year, site["latitude"] if lat is None else lat, site["longitude"] if lon is None else lon,
                site.get("std_meridian", 135.0), solar_station=s_stn, solar=solar), p)
    except wx.WeatherError as e:
        raise HTTPException(502 if "호출 실패" in str(e) else 422, str(e))
    return p, _load(p)


def it_dataset(it_source, sc):
    if it_source != "dataset":
        return None
    ds_id = sc["it_load"].get("dataset_id")
    if not ds_id:
        raise HTTPException(422, "시나리오에 IT 데이터셋이 없습니다 — 'IT 데이터'에서 기간 데이터를 적용하세요")
    try:
        return datasets.load("it", ds_id)
    except datasets.DatasetError as e:
        raise HTTPException(404, str(e))


@router.get("/stations")
def stations():
    """기상청 ASOS 유인관서 목록 (활용가이드 지점코드 표 + 관측지점정보 좌표) + 인증키 설정 여부"""
    from hvac.kma_stations import ASOS_COORDS, ASOS_STATIONS
    return {"key_configured": bool(wx.kma_key()),
            "stations": [{"id": k, "name": n, "office": o, "latitude": ASOS_COORDS[k][0],
                          "longitude": ASOS_COORDS[k][1], "altitude_m": ASOS_COORDS[k][2]}
                         for k, (n, o) in ASOS_STATIONS.items()]}


@router.get("/weather-files")
def weather_files():
    out = []
    for p in sorted(wx.WEATHER_DIR.glob("annual_*.json")):
        if not FILE_RE.match(p.name):
            continue
        try:
            m = json.loads(p.read_text(encoding="utf-8"))["meta"]
        except (ValueError, KeyError):
            continue
        out.append({"file": p.name, "latitude": m.get("latitude"), "longitude": m.get("longitude"),
                    "year": m.get("year"), "n_days": m.get("n_days"), "source": m.get("source")})
    return out


@router.post("/run")
def run(req: AnnualRequest):
    sc = req.scenario.to_engine_dict()
    path, ann = _weather(req, sc)
    ds = it_dataset(req.it_source, sc)
    key = hashlib.sha1(json.dumps([sc, path.name, path.stat().st_mtime, req.base_multiplier,
                                   req.growth_pct_per_year, req.it_source, req.coupled], sort_keys=True,
                                  default=str).encode()).hexdigest()
    if key in CACHE:
        CACHE.move_to_end(key)
        return {**CACHE[key], "cached": True}
    res = annual.run(sc, ann, base_multiplier=req.base_multiplier, growth_pct_per_year=req.growth_pct_per_year,
                     it_dataset=ds, coupled=req.coupled)
    res["weather_file"] = path.name
    CACHE[key] = res
    while len(CACHE) > CACHE_SIZE:
        CACHE.popitem(last=False)
    return {**res, "cached": False}


# ---------- Phase 13: 연간 최적화 ----------
class AnnualOptRequest(AnnualRequest):
    containment: list[Literal["open", "cold_aisle", "hot_aisle"]] = ["open", "cold_aisle", "hot_aisle"]
    topology: list[Literal["radial", "dual", "ring"]] = ["radial", "dual", "ring"]
    crah_per_side: list[Annotated[int, Field(ge=1, le=CRAH_PER_SIDE_MAX)]] | None = None
    max_step_c: float = Field(default=2.0, ge=0, le=10, description="인접 월 급기온도 변경 폭 상한 [℃]")


def annual_opt_job(req: AnnualOptRequest, progress):
    sc = req.scenario.to_engine_dict()
    path, ann = _weather(req, sc)
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    res = optimize_year(sc, cfg, ann, it_dataset=it_dataset(req.it_source, sc), base_multiplier=req.base_multiplier,
                        growth_pct_per_year=req.growth_pct_per_year, containment=req.containment,
                        topology=req.topology, crah_per_side=req.crah_per_side, max_step_c=req.max_step_c,
                        progress=progress)
    res["weather_file"] = path.name
    return res


register("annual_optimize", AnnualOptRequest, annual_opt_job)


class Structure(BaseModel):
    containment: Literal["open", "cold_aisle", "hot_aisle"]
    topology: Literal["radial", "dual", "ring"]
    crah_per_side: int = Field(ge=1, le=CRAH_PER_SIDE_MAX)


class ScheduleApplyRequest(BaseModel):
    scenario: Scenario
    structure: Structure
    schedule: Annotated[list[ScheduleEntry], Field(min_length=1, max_length=12)]


@router.post("/schedule/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def schedule_apply(req: ScheduleApplyRequest):
    """구조 + 월별 스케줄 저장. 현재 운전점은 시나리오 날짜가 속한 달의 값 (24시간 화면·냉각 구조 평가용)"""
    sc = req.scenario.to_engine_dict()
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    month = int(sc["site"]["date"][5:7])
    sched = [e.model_dump() for e in req.schedule]
    e = next((x for x in sched if x["month"] == month), sched[0])
    params = {**req.structure.model_dump(), "airflow_ratio": e["airflow_ratio"],
              "tile_balancing": cfg["crah"]["tile_balancing"], "supply_temp_c": e["supply_temp_c"],
              "chw_supply_c": e["chw_supply_c"], "chw_return_c": e["chw_return_c"]}
    sc2, lay = apply_candidate(sc, cfg, params)
    lay["operating_schedule"] = sched
    provenance.set_value(sc, "cooling_layout", lay, "schedule", note="연간 최적화 월별 스케줄")
    for k in ("chw_supply_c", "chw_return_c"):
        provenance.set_value(sc, f"cooling_plant.{k}", sc2["cooling_plant"][k], "schedule")
    return Scenario.model_validate(sc)
