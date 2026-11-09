"""냉각 구조(기류·배관·최적화) 라우터: /cooling/*"""
from fastapi import APIRouter, HTTPException

from hvac.airflow import analyze
from hvac.cooling_schemas import (
    AnalyzeRequest, AnalyzeResult, ApplyRequest, EvaluateResult, OptimizeRequest, OptimizeResult,
)
from hvac.optimize import apply_candidate, optimize
from hvac import provenance
from hvac.plant import evaluate
from hvac.schemas import CoolingLayout, Scenario

router = APIRouter(prefix="/cooling", tags=["cooling"])


def _layout(sc: Scenario, override: CoolingLayout | None = None) -> dict:
    return (override or sc.cooling_layout or CoolingLayout()).model_dump()


def _run(fn, *args):
    try:
        return fn(*args)
    except ValueError as e:     # 배치 불가(랙/CRAH 0개) 등
        raise HTTPException(422, str(e))


@router.get("/layout/default", response_model=CoolingLayout)
def default_layout():
    return CoolingLayout()


@router.post("/analyze", response_model=AnalyzeResult)
def analyze_layout(req: AnalyzeRequest):
    """기류만 (한 시각)"""
    return _run(analyze, req.scenario.to_engine_dict(), _layout(req.scenario, req.layout), req.hour)


@router.post("/evaluate", response_model=EvaluateResult, response_model_exclude_unset=True)
def evaluate_layout(scenario: Scenario, zone: str | None = None):
    """기류 + 배관 + 칠러 연성 평가 (24시간, 3D 표시용 데이터 포함). zone = 다중 존에서 3D 로 볼 전산실 존 id"""
    return _run(evaluate, scenario.to_engine_dict(), _layout(scenario), zone)


@router.post("/optimize", response_model=OptimizeResult)
def optimize_layout(req: OptimizeRequest):
    space = req.space.model_dump(exclude_none=True)
    return _run(optimize, req.scenario.to_engine_dict(), _layout(req.scenario), space)


@router.post("/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def apply_params(req: ApplyRequest):
    """최적화 후보를 시나리오에 반영 (냉수 온도 + cooling_layout)"""
    sc0 = req.scenario.to_engine_dict()
    sc, layout = apply_candidate(sc0, _layout(req.scenario), req.params.model_dump())
    layout["operating_schedule"] = None   # 하루 최적화를 적용하면 월별 스케줄은 해제 (현재 운전점 하나로)
    provenance.set_value(sc0, "cooling_layout", layout, "optimizer", note="하루 기준 최적화")
    for k in ("chw_supply_c", "chw_return_c"):
        provenance.set_value(sc0, f"cooling_plant.{k}", sc["cooling_plant"][k], "optimizer")
    return Scenario.model_validate(sc0)
