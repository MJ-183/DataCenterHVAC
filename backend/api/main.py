"""
FastAPI 서버.  backend 폴더에서:  uvicorn api.main:app --reload
문서: http://127.0.0.1:8000/docs
"""
import json
import os
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api import (ai, annual, autotune, calibration, cooling, imports, invest, outlook, platform, racks, real_compare, store,
                 weather)
from hvac import DATA_DIR, simulate
from hvac.engine import STEP_MINUTES, compare_architectures
from hvac.schemas import ArchitectureComparison, Scenario, SimulationResult


def _load_sample() -> dict:
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


SAMPLE = _load_sample()
# 화면 상단 시나리오 선택 (id, 이름, 파일). 서울 샘플은 회귀 테스트 기준이라 내용을 바꾸지 않는다
PRESETS = [("haenam", "해남 AI DC (40 MW)", "scenario_haenam_ai.json"),
           ("haenam_mz", "해남 AI DC 다중 존 (2층 적층)", "scenario_haenam_ai_mz.json"),
           ("dummy_3f", "3층 더미 DC (다중 존 7개·전산실 3개, CAD 도면 짝)", "scenario_3f_dummy_mz.json"),
           ("seoul", "서울 샘플(회귀용)", "scenario_sample.json")]

app = FastAPI(
    title="Datacenter HVAC Simulator API",
    version="0.6.0",
    description="24시간 데이터센터 냉방부하·냉수·냉각수·보급수 계산. "
                "샘플 시나리오의 기상값은 가상 데이터(실측 아님).",
)

# 프론트 개발 서버(Vite 기본 5173). 다른 주소는 환경변수 HVAC_CORS_ORIGINS(쉼표 구분)로 지정
_origins = os.getenv("HVAC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.include_router(ai.router)
app.include_router(store.router)
app.include_router(cooling.router)
app.include_router(weather.router)
app.include_router(imports.router)
app.include_router(annual.router)
app.include_router(platform.router)
app.include_router(calibration.router)
app.include_router(outlook.router)
app.include_router(racks.router)
app.include_router(invest.router)
app.include_router(autotune.router)
app.include_router(real_compare.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/scenario/sample", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def scenario_sample():
    return Scenario.model_validate(SAMPLE)


@app.get("/scenario/presets")
def scenario_presets():
    return [{"id": k, "label": label} for k, label, f in PRESETS if (DATA_DIR / f).exists()]


@app.get("/scenario/preset/{preset_id}", response_model=Scenario, response_model_by_alias=True,
         response_model_exclude_none=True)
def scenario_preset(preset_id: str):
    f = next((f for k, _, f in PRESETS if k == preset_id), None)
    if f is None or not (DATA_DIR / f).exists():
        raise HTTPException(404, f"'{preset_id}' 시나리오가 없습니다")
    return Scenario.model_validate(json.loads((DATA_DIR / f).read_text(encoding="utf-8")))


@app.post("/simulate", response_model=SimulationResult, response_model_exclude_unset=True)
def run_simulation(
    scenario: Annotated[Scenario, Body(openapi_examples={
        "sample": {"summary": "샘플 시나리오 (가상 기상 데이터)", "value": SAMPLE},
    })],
    step_min: Annotated[int, Query(description=f"계산 간격 [분], {STEP_MINUTES} 중 하나. "
                                                 "60 미만이면 시간별 입력을 선형 보간(가정)")] = 60,
):
    if step_min not in STEP_MINUTES:
        raise HTTPException(422, f"step_min 은 {STEP_MINUTES} 중 하나여야 합니다")
    return simulate(scenario.to_engine_dict(), step_min)


@app.post("/simulate/compare", response_model=ArchitectureComparison)
def compare_cooling_architectures(
    scenario: Scenario,
    step_min: Annotated[int, Query(description="계산 간격 [분]")] = 60,
):
    """같은 날씨·IT 부하에서 냉각 방식(air_chw / hybrid_dlc / immersion)만 바꿔 일 합계·시간별 모드·PUE 비교"""
    if step_min not in STEP_MINUTES:
        raise HTTPException(422, f"step_min 은 {STEP_MINUTES} 중 하나여야 합니다")
    return compare_architectures(scenario.to_engine_dict(), step_min)
