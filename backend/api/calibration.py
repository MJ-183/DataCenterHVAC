"""
BMS 보정 API (Phase 12).
- POST /bms/inspect       : 파일들(base64) → 표별 자동 열 매칭, 보정 가능한 단계
- POST /bms/dataset       : 파일들 + (선택) 매칭 덮어쓰기 → BMS 데이터셋 저장 (기간·지표 요약)
- POST /jobs/calibrate    : 시나리오 + BMS 데이터셋 id → 단계별 보정 (작업, 진행률)
- POST /calibration/apply : 보정 결과(run_id) → 계수를 시나리오에 반영 (출처 bms_calibrated, 되돌리기 가능)
기존 /bms/compare (하루 CSV) 는 그대로 둔다.
"""
from collections import OrderedDict
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.imports import ItFile, _bytes
from api.platform import register
from hvac import bms, calibrate, datasets, provenance
from hvac import weather as wx
from hvac.schemas import CoolingLayout, Scenario

router = APIRouter(tags=["calibration"])
RUNS: "OrderedDict[str, dict]" = OrderedDict()
RUNS_SIZE = 16
STAGE_METRICS = {sid: (label, mets) for sid, label, _, mets in calibrate.STAGES}


class BmsFiles(BaseModel):
    files: Annotated[list[ItFile], Field(min_length=1, max_length=10)]
    overrides: dict[str, dict[str, str]] = Field(default_factory=dict, description="표 label → {역할: 열 이름}")


def _tables(req: BmsFiles):
    try:
        return bms.inspect([(f.name, _bytes(f.data_b64, f.name)) for f in req.files], req.overrides or None)
    except bms.BmsError as e:
        raise HTTPException(422, str(e))


def _stages(metrics):
    out = []
    for sid, (label, mets) in STAGE_METRICS.items():
        ok = [m for m in mets if m in metrics]
        out.append({"stage": sid, "label": label, "possible": bool(ok),
                    "needs": [bms.METRICS[m][0] for m in mets]})
    return out


@router.post("/bms/inspect")
def bms_inspect(req: BmsFiles):
    tables = [bms.public(t) for t in _tables(req)]
    metrics = sorted({m for t in tables for m in t["metrics"]})
    return {"tables": tables, "stages": _stages(metrics),
            "has_weather": "outdoor_temp_c" in metrics and "outdoor_rh_pct" in metrics}


@router.post("/bms/dataset")
def bms_dataset(req: BmsFiles):
    try:
        ds = bms.dataset(_tables(req))
    except bms.BmsError as e:
        raise HTTPException(422, str(e))
    saved = datasets.save("bms", ds, ", ".join(f.name for f in req.files))
    return {"id": saved["meta"]["id"], **saved["summary"], "stages": _stages(saved["summary"]["metrics"])}


class CalibrateRequest(BaseModel):
    scenario: Scenario
    dataset_id: str
    stages: list[str] | None = Field(default=None, description="없으면 가능한 단계 모두")


def _weather_days(sc, ds):
    """BMS 기간의 Open-Meteo 과거 기상 (일사량 + 외기 열이 없을 때 온습도). 실패하면 빈 dict + 메모"""
    p0, p1 = (date.fromisoformat(x) for x in ds["meta"]["period"])
    try:
        days = wx.fetch_open_meteo_range(sc["site"]["latitude"], sc["site"]["longitude"], p0, p1)
        return {d["meta"]["date"]: d["weather"] for d in days}, None
    except (wx.WeatherError, KeyError, ValueError) as e:
        return {}, f"Open-Meteo 과거 기상을 받지 못함 ({e}) — BMS 외기 열과 시나리오 일사 모양을 사용"


def calibrate_job(req: CalibrateRequest, progress):
    sc = req.scenario.to_engine_dict()
    from hvac.multizone import single_zone_only
    single_zone_only(sc, "BMS 보정")
    try:
        ds = datasets.load("bms", req.dataset_id)
    except datasets.DatasetError as e:
        raise HTTPException(404, str(e))
    it_ds = None
    if sc["it_load"].get("dataset_id"):
        try:
            it_ds = datasets.load("it", sc["it_load"]["dataset_id"])
        except datasets.DatasetError:
            it_ds = None
    progress(0.02, "기상 자료 준비")
    wdays, wnote = _weather_days(sc, ds)
    res = calibrate.run(sc, ds, wdays, it_ds, req.stages, progress)
    if wnote:
        res["notes"].insert(0, wnote)
    RUNS[res["run_id"]] = {"params": res["params"], "dataset_id": req.dataset_id}
    while len(RUNS) > RUNS_SIZE:
        RUNS.popitem(last=False)
    return res


register("calibrate", CalibrateRequest, calibrate_job)


class ApplyRequest(BaseModel):
    scenario: Scenario
    run_id: str
    paths: list[str] | None = Field(default=None, description="반영할 계수 경로 (없으면 전부)")


@router.post("/calibration/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def calibration_apply(req: ApplyRequest):
    run = RUNS.get(req.run_id)
    if not run:
        raise HTTPException(404, "보정 결과가 서버에 없습니다 (서버 재시작 등) — 보정을 다시 실행하세요")
    sc = req.scenario.to_engine_dict()
    if not sc.get("cooling_layout"):
        sc["cooling_layout"] = CoolingLayout().model_dump(exclude_none=True)
    for p in run["params"]:
        if req.paths is None or p["path"] in req.paths:
            provenance.set_value(sc, p["path"], p["after"], "bms_calibrated", ref=req.run_id,
                                 note=f"BMS 데이터셋 {run['dataset_id']} 보정")
    return Scenario.model_validate(sc)
