"""
운전 브리핑 API (Phase 14).
- POST /outlook        : 시나리오 + period(day = 내일, month = 다음 달) → 운전 브리핑
  내일: 기상청 단기예보 (사이트 위치의 격자, 1시간 캐시). 받지 못하면 지역 관측소 연간 관측의 같은 월·일 값으로 대신하고 크게 표시
  다음 달: 지역 관측소(데이터 입력의 지역 선택)의 지난 관측 연도 같은 달 날씨로 한 달 계산
- POST /outlook/apply  : 권장 운전점 → 현재 운전점 (출처 optimizer, 되돌리기 가능)
"""
import time
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hvac import datasets, outlook, provenance
from hvac import weather as wx
from hvac.kma_grid import to_grid
from hvac.schemas import CoolingLayout, Scenario

router = APIRouter(prefix="/outlook", tags=["outlook"])
WX_CACHE: dict = {}
WX_TTL_S = 3600


class OutlookRequest(BaseModel):
    scenario: Scenario
    period: Literal["day", "month"] = Field(default="day", description="day = 내일, month = 다음 달")
    capacity_kw: float | None = Field(default=None, gt=0, description="냉방 설비 용량 [kW] (선택)")
    today: date | None = Field(default=None, description="기준일 (테스트용, 없으면 서버 오늘)")


def _fallback(sc, target, why):
    """예보 실패 → 지역 관측소의 저장된 연간 관측(최근 연도)에서 같은 월·일"""
    stn = sc["site"].get("asos_station_id") or 108
    for p in sorted(wx.WEATHER_DIR.glob(f"annual_kma_{stn}_*.json"), reverse=True):
        ann = wx.load_annual(p)
        d = next((x for x in ann["days"] if x["date"].endswith(target.strftime("-%m-%d"))), None)
        if d:
            y = ann["meta"].get("year")
            return {"weather": d["weather"], "meta": {
                "source": "annual_fallback", "source_detail": f"예보 대신 {y}년 같은 날 기상청 ASOS 관측",
                "fallback_note": f"단기예보를 받지 못해({why}) {ann['meta'].get('station_name', stn)} {y}년 "
                                 f"{target.month}월 {target.day}일 관측으로 대신 계산했습니다 (실제 내일 날씨와 다를 수 있음)"}}
    return None


def _weather(sc, target):
    site = sc["site"]
    grid = to_grid(site["latitude"], site["longitude"])
    key = (grid, target.isoformat())
    hit = WX_CACHE.get(key)
    if hit and time.time() - hit[0] < WX_TTL_S:
        return hit[1]
    if not wx.kma_key():
        raise HTTPException(503, "기상청 API 인증키가 없습니다 — '데이터 입력'에서 입력하세요 (내일 브리핑은 기상청 단기예보 사용)")
    try:
        d = wx.fetch_kma_vilage(site["latitude"], site["longitude"], target, site["std_meridian"])
    except wx.WeatherError as e:
        fb = _fallback(sc, target, e)
        if fb is None:
            raise HTTPException(502, f"단기예보를 받지 못했습니다 ({e}). 잠시 후 다시 시도하거나 '데이터 입력'에서 지역을 적용해 "
                                     "관측 연도를 받아 두면 같은 날 관측값으로 대신 볼 수 있습니다")
        return fb
    WX_CACHE[key] = (time.time(), d)
    return d


@router.post("")
def run_outlook(req: OutlookRequest):
    sc = req.scenario.to_engine_dict()
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    today = req.today or date.today()
    it_ds = None
    if sc["it_load"].get("dataset_id"):
        try:
            it_ds = datasets.load("it", sc["it_load"]["dataset_id"])
        except datasets.DatasetError:
            it_ds = None
    try:
        if req.period == "month":
            from api.annual import kma_year
            y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
            _, ann = kma_year(sc["site"])
            return outlook.next_month(sc, cfg, y, m, ann, it_ds, req.capacity_kw)
        target = today + timedelta(days=1)
        return outlook.tomorrow(sc, cfg, target, _weather(sc, target), it_ds, req.capacity_kw)
    except ValueError as e:
        raise HTTPException(422, str(e))


class Setpoint(BaseModel):
    supply_temp_c: float
    chw_supply_c: float
    chw_return_c: float
    airflow_ratio: float = Field(gt=0, le=3)


class OutlookApply(BaseModel):
    scenario: Scenario
    setpoint: Setpoint
    month: int | None = Field(default=None, ge=1, le=12,
                              description="예측 날짜의 월. 월별 스케줄이 있으면 그 달 값도 권장값으로 바꿈 (브리핑과 일치)")


@router.post("/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def apply_setpoint(req: OutlookApply):
    sc = req.scenario.to_engine_dict()
    if not sc.get("cooling_layout"):
        sc["cooling_layout"] = CoolingLayout().model_dump(exclude_none=True)
        provenance.mark(sc, "cooling_layout", "optimizer", previous=None)
    sp = req.setpoint
    for path, v in (("cooling_layout.supply_temp_c", sp.supply_temp_c), ("cooling_layout.crah.airflow_ratio", sp.airflow_ratio),
                    ("cooling_plant.chw_supply_c", sp.chw_supply_c), ("cooling_plant.chw_return_c", sp.chw_return_c)):
        provenance.set_value(sc, path, v, "optimizer", note="내일 운전 권장값")
    sched = sc["cooling_layout"].get("operating_schedule")
    if sched and req.month:
        new = [{**e, **sp.model_dump()} if e["month"] == req.month else e for e in sched]
        if new != sched:
            provenance.set_value(sc, "cooling_layout.operating_schedule", new, "optimizer",
                                 note=f"{req.month}월 스케줄을 내일 운전 권장값으로")
    return Scenario.model_validate(sc)
