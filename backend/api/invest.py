"""
투자 검토 API (Phase 16).
- POST /invest/catalog : 시나리오 → 적용 가능한 개선안·수량·예시 투자비, 기본 경제 조건 (예시값)
- POST /jobs/invest    : 시나리오 + 연간 기상 + 경제 조건 + 개선안·패키지 → NPV·IRR·회수기간·민감도 (작업, 진행률)
예시 단가·요금으로 계산하면 결과에 demo=true 와 '예시 데이터로 시연 계산' 문구가 붙는다.
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.annual import AnnualRequest, _weather, it_dataset
from api.platform import register
from hvac import invest
from hvac.schemas import CoolingLayout, Economics, Measure, Scenario

router = APIRouter(prefix="/invest", tags=["invest"])


class CatalogRequest(BaseModel):
    scenario: Scenario


@router.post("/catalog")
def catalog(req: CatalogRequest):
    sc = req.scenario.to_engine_dict()
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    eco = req.scenario.economics or Economics()
    return {"measures": invest.catalog(sc, cfg), "economics": eco.model_dump(),
            "economics_is_default": req.scenario.economics is None,
            "note": "투자비 단가와 요금 기본값은 예시 데이터입니다 (시연용) — 실제 견적·요금으로 바꿔 쓰세요"}


class InvestRequest(AnnualRequest):
    economics: Economics | None = None
    measures: list[Measure] = Field(min_length=1, max_length=12)
    packages: list[list[str]] = Field(default_factory=list, max_length=4)


def invest_job(req: InvestRequest, progress):
    sc = req.scenario.to_engine_dict()
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    _, ann = _weather(req, sc)
    eco_m = req.economics or req.scenario.economics
    eco = (eco_m or Economics()).model_dump()
    eco["defaults_used"] = eco_m is None or eco_m == Economics(measures=eco_m.measures)
    return invest.run(sc, cfg, ann, eco, [m.model_dump() for m in req.measures], req.packages,
                      it_dataset(req.it_source, sc), progress)


register("invest", InvestRequest, invest_job)
