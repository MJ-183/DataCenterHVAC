"""
랙 편집·증설 배치 API (Phase 15).
- POST /cooling/racks/apply : 랙별 냉각 방식(φ)·설계 kW 변경, 열 단위 φ, 새 랙 추가 → 시나리오 (엔진 냉각 방식 동기화, 되돌리기 가능)
- POST /jobs/placement      : 새 랙 묶음 → 추천 자리 (작업, 진행률)
"""
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.platform import register
from hvac import mixed, placement, provenance
from hvac.schemas import CoolingLayout, Fraction, RackItem, Scenario

router = APIRouter(prefix="/cooling/racks", tags=["racks"])


class RackUpdate(BaseModel):
    liquid_fraction: Fraction | None = None
    design_kw: Annotated[float, Field(ge=0)] | None = None
    clear_liquid: bool = Field(default=False, description="true 면 φ 지정 해제 (공랭)")


class RackApply(BaseModel):
    scenario: Scenario
    updates: dict[str, RackUpdate] = Field(default_factory=dict, description="랙 id → 변경")
    rows: dict[int, Fraction | None] = Field(default_factory=dict, description="열 번호(0부터) → φ (격자·CAD 공통)")
    add_items: list[RackItem] = Field(default_factory=list)
    unit_airflow_m3_s: float | None = Field(default=None, gt=0, description="새 랙 추가 시 CRAH 1대 정격 고정값")


@router.post("/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def racks_apply(req: RackApply):
    sc = req.scenario.to_engine_dict()
    from hvac.multizone import SingleZoneOnly, single_zone_only
    try:
        single_zone_only(sc, "랙 편집")
    except SingleZoneOnly as e:
        raise HTTPException(422, str(e))
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    had_layout = bool(sc.get("cooling_layout"))
    c = placement.to_items(sc, cfg) if (req.updates or req.add_items or not cfg["racks"].get("items")) else cfg
    items = {it["id"]: it for it in c["racks"]["items"]}
    unknown = [k for k in req.updates if k not in items]
    if unknown:
        raise HTTPException(422, f"없는 랙 id: {', '.join(unknown[:5])}")
    for rid, u in req.updates.items():
        it = items[rid]
        if u.clear_liquid:
            it.pop("liquid_fraction", None)
        elif u.liquid_fraction is not None:
            it["liquid_fraction"] = u.liquid_fraction
        if u.design_kw is not None:
            it["design_kw"] = u.design_kw
    for j, phi in req.rows.items():
        for it in c["racks"]["items"]:
            if it["row"] == j:
                if phi is None:
                    it.pop("liquid_fraction", None)
                else:
                    it["liquid_fraction"] = phi
    if req.add_items:
        dup = [a.id for a in req.add_items if a.id in items]
        if dup:
            raise HTTPException(422, f"이미 있는 랙 id: {', '.join(dup[:5])}")
        new = [a.model_dump(exclude_none=True) for a in req.add_items]
        c["racks"]["items"] += new
        c["racks"]["rows"] = max(c["racks"]["rows"], max(it["row"] for it in c["racks"]["items"]) + 1)
        provenance.set_value(sc, "it_load.design_kw",
                             round(sc["it_load"]["design_kw"] + sum(a.get("design_kw") or 0 for a in new), 3), "user",
                             note=f"증설 랙 {len(new)}대")
        if req.unit_airflow_m3_s:
            c["crah"]["unit_airflow_m3_s"] = req.unit_airflow_m3_s
    if not had_layout:
        provenance.mark(sc, "cooling_layout", "user", previous=None)
        sc["cooling_layout"] = c
    else:
        provenance.set_value(sc, "cooling_layout.racks", c["racks"], "user", note="랙 편집")
        if req.add_items and req.unit_airflow_m3_s:
            provenance.set_value(sc, "cooling_layout.crah.unit_airflow_m3_s", req.unit_airflow_m3_s, "user")
    mixed.sync(sc)
    try:
        return Scenario.model_validate(sc)
    except ValueError as e:
        raise HTTPException(422, f"랙 변경이 시나리오 검증을 통과하지 못했습니다: {e}")


class NewRacks(BaseModel):
    count: int = Field(ge=1, le=500)
    design_kw: float = Field(gt=0, le=500)
    liquid_fraction: Fraction = 0.0
    label: str = Field(default="", max_length=40)


class PlacementRequest(BaseModel):
    scenario: Scenario
    new_racks: Annotated[list[NewRacks], Field(min_length=1, max_length=10)]


def placement_job(req: PlacementRequest, progress):
    sc = req.scenario.to_engine_dict()
    from hvac.multizone import single_zone_only
    single_zone_only(sc, "증설 배치")
    cfg = (req.scenario.cooling_layout or CoolingLayout()).model_dump()
    return placement.place(sc, cfg, [g.model_dump() for g in req.new_racks], progress=progress)


register("placement", PlacementRequest, placement_job)
