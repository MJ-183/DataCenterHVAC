"""자동 튜닝 (Phase 18): /autotune/* — 주의·경고가 나면 사용자가 정한 중요 파라미터 순서대로 바꿔 해결 (hvac/autotune.py)"""
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from api.platform import register
from hvac import autotune, provenance
from hvac.optimize import apply_candidate
from hvac.schemas import CRAH_PER_SIDE_MAX, CoolingLayout, Scenario

router = APIRouter(prefix="/autotune", tags=["autotune"])
TuneKey = Literal["tile_balancing", "airflow_ratio", "supply_temp_c", "chw_supply_c", "chw_delta_t", "containment", "crah_per_side"]


class TuneSetting(BaseModel):
    key: TuneKey
    enabled: bool = True
    min: float | None = None
    max: float | None = None
    step: float | None = Field(default=None, gt=0)
    choices: list[Literal["open", "cold_aisle", "hot_aisle"]] | None = None

    @model_validator(mode="after")
    def _range(self):
        if self.key != "containment":
            d = autotune.PARAMS[self.key]
            self.step = self.step or d["step"]
            if self.min is None or self.max is None:
                raise ValueError(f"{d['label']}: 범위(min, max)가 필요합니다")
            if self.min > self.max:
                raise ValueError(f"{d['label']}: 하한({self.min:g})이 상한({self.max:g})보다 큽니다")
            if self.key == "crah_per_side" and (self.min < 1 or self.max > CRAH_PER_SIDE_MAX):
                raise ValueError(f"CRAH 대수 범위는 1~{CRAH_PER_SIDE_MAX} 입니다")
            if self.key == "airflow_ratio" and (self.min <= 0 or self.max > 3):
                raise ValueError("풍량 배율 범위는 0~3 입니다")
            if self.key == "tile_balancing" and (self.min < 0 or self.max > 1):
                raise ValueError("타일 밸런싱 범위는 0~1 입니다")
        return self


class TuneRequest(BaseModel):
    scenario: Scenario
    settings: Annotated[list[TuneSetting], Field(min_length=1, max_length=7)] | None = Field(
        default=None, description="우선순위 순서 (위가 먼저). 없으면 기본 (공사 항목 꺼짐)")
    margin_c: float = Field(default=1.0, ge=0, le=5, description="목표 랙 흡입온도 여유 [K]")
    objective: Literal["min_change", "min_energy"] = "min_change"

    @model_validator(mode="after")
    def _unique(self):
        if self.settings and len({s.key for s in self.settings}) != len(self.settings):
            raise ValueError("settings 에 같은 파라미터가 두 번 있습니다")
        return self


def _cfg(sc: Scenario) -> dict:
    return (sc.cooling_layout or CoolingLayout()).model_dump()


def _settings(req: TuneRequest):
    return [s.model_dump() for s in req.settings] if req.settings else None


@router.post("/defaults")
def tune_defaults(scenario: Scenario):
    """화면 기본 설정 (파라미터·우선순위·범위)"""
    return {"settings": autotune.defaults(_cfg(scenario)), "margin_c": 1.0, "objective": "min_change"}


@router.post("/influence")
def tune_influence(req: TuneRequest):
    """파라미터별 한 단계 변화의 효과 → 영향도 순서 추천"""
    return autotune.influence(req.scenario.to_engine_dict(), _cfg(req.scenario), req.margin_c, _settings(req))


def tune_job(req: TuneRequest, progress):
    return autotune.run(req.scenario.to_engine_dict(), _cfg(req.scenario), _settings(req), req.margin_c, req.objective, progress)


register("autotune", TuneRequest, tune_job)


class TuneParams(BaseModel):
    containment: Literal["open", "cold_aisle", "hot_aisle"]
    crah_per_side: int = Field(ge=1, le=CRAH_PER_SIDE_MAX)
    airflow_ratio: float = Field(gt=0, le=3)
    tile_balancing: float = Field(ge=0, le=1)
    supply_temp_c: float
    chw_supply_c: float
    chw_return_c: float
    topology: Literal["radial", "dual", "ring"]


class TuneApply(BaseModel):
    scenario: Scenario
    params: TuneParams


@router.post("/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def tune_apply(req: TuneApply):
    """자동 튜닝 결과 적용 (출처 '최적화' 기록 → 되돌리기 가능). 월별 스케줄이 있으면 해제 (현재 운전점 하나로)"""
    sc0 = req.scenario.to_engine_dict()
    sc, layout = apply_candidate(sc0, _cfg(req.scenario), req.params.model_dump())
    layout["operating_schedule"] = None
    provenance.set_value(sc0, "cooling_layout", layout, "optimizer", note="자동 튜닝")
    for k in ("chw_supply_c", "chw_return_c"):
        provenance.set_value(sc0, f"cooling_plant.{k}", sc["cooling_plant"][k], "optimizer", note="자동 튜닝")
    return Scenario.model_validate(sc0)
