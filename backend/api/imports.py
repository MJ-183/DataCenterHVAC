"""
CAD(DXF)·IT 부하 데이터 가져오기 API (Phase 8).
- GET  /cad/roles             : 역할 목록
- POST /cad/inspect           : DXF(base64) → 레이어·블록·역할 추정·미리보기, cad_id (서버 메모리에 잠시 보관)
- POST /cad/build             : cad_id + 역할 지정 + 옵션 + 시나리오 → 외피·배치를 반영한 시나리오, 보고서, 미리보기
                                (역할 있는 층이 둘 이상이면 층·존별 해석 → 다중 존 시나리오, hvac/cad_zones.py)
- POST /itload/inspect        : 파일들(base64) → 표별 자동 컬럼 매칭 결과
- POST /itload/apply          : 파일들 + (선택) 매칭 덮어쓰기 + 하루 선택 + 시나리오 → IT 부하·랙 프로파일 반영 시나리오, 보고서
"""
import base64
import binascii
import uuid
from collections import OrderedDict
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hvac import cad, datasets, itdata, provenance
from hvac.schemas import CoolingLayout, Scenario, ZoneKind

router = APIRouter()
MAX_FILE_BYTES = 50 * 1024 * 1024
CAD_CACHE: "OrderedDict[str, dict]" = OrderedDict()
CAD_CACHE_SIZE = 8


def _bytes(b64: str, name: str) -> bytes:
    try:
        data = base64.b64decode(b64.split(",", 1)[-1] if b64.startswith("data:") else b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, f"{name}: base64 디코딩 실패")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, f"{name}: 파일이 {MAX_FILE_BYTES // 1024 // 1024} MB 를 넘습니다")
    return data


def _layout(sc: Scenario) -> dict:
    return (sc.cooling_layout or CoolingLayout()).model_dump()


def _scenario(d: dict) -> Scenario:
    try:
        return Scenario.model_validate(d)
    except ValueError as e:
        raise HTTPException(422, f"가져온 값이 시나리오 검증을 통과하지 못했습니다: {e}")


# ---------- CAD ----------
class CadFile(BaseModel):
    file_name: str = Field(max_length=255)
    data_b64: str


class ZoneOverride(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    kind: ZoneKind | None = None
    it_design_kw: float | None = Field(default=None, ge=0)


class CadOptions(BaseModel):
    north_deg: float = Field(default=0.0, ge=-360, le=360, description="진북 방향: 도면 +Y 에서 시계방향 [deg]")
    height_m: float = Field(default=10.0, gt=2, le=60, description="층고 [m]")
    window_height_m: float = Field(default=1.5, gt=0, le=10, description="창 높이 [m] (가정)")
    unit_scale_m: float | None = Field(default=None, gt=0, description="도면 1 단위 = ? m (없으면 $INSUNITS)")
    # 다중 층 (Phase 18 연장)
    floor_heights_m: dict[int, Annotated[float, Field(gt=2, le=60)]] | None = Field(
        default=None, description="층별 층고 [m] (키 = floor, 0 = 1층). 없는 층은 6 m")
    rack_kw: float | None = Field(default=None, gt=0, le=500, description="KW 속성이 없는 랙의 설계 kW (없으면 기존 밀도)")
    zone_overrides: dict[str, ZoneOverride] | None = Field(default=None, description="존 id → 이름·종류·IT 수정")


class CadBuildRequest(BaseModel):
    cad_id: str
    roles: dict[str, str] = Field(description="레이어 이름 → 역할")
    block_roles: dict[str, str] = Field(default_factory=dict, description="블록 이름 → 역할 (빈 문자열 = 레이어 따름). "
                                        "'블록@F2' 는 그 층 레이어에 놓인 블록에만")
    options: CadOptions = CadOptions()
    scenario: Scenario


@router.get("/cad/roles", tags=["import"])
def cad_roles():
    return [{"value": r, "label": cad.ROLE_LABEL[r]} for r in cad.ROLES]


@router.post("/cad/inspect", tags=["import"])
def cad_inspect(f: CadFile):
    try:
        parsed = cad.load(_bytes(f.data_b64, f.file_name), f.file_name)
        out = cad.inspect(parsed)
    except cad.CadError as e:
        raise HTTPException(422, str(e))
    cid = uuid.uuid4().hex
    CAD_CACHE[cid] = parsed
    while len(CAD_CACHE) > CAD_CACHE_SIZE:
        CAD_CACHE.popitem(last=False)
    return {"cad_id": cid, **out}


@router.post("/cad/build", tags=["import"])
def cad_build(req: CadBuildRequest):
    parsed = CAD_CACHE.get(req.cad_id)
    if parsed is None:
        raise HTTPException(404, "도면이 서버에 없습니다 (서버 재시작 등). 파일을 다시 올려 주세요")
    sc = req.scenario.to_engine_dict()
    lay = _layout(req.scenario)
    try:
        built = cad.build(parsed, req.roles, req.block_roles, sc, lay, **req.options.model_dump())
    except cad.CadError as e:
        raise HTTPException(422, str(e))
    out = cad.apply_to_scenario(sc, lay, built)
    paths = (("envelope", "building", "zones", "it_load.design_kw", "internal.floor_area_m2") if built.get("zones")
             else ("envelope", "building", "cooling_layout.racks", "cooling_layout.crah.positions"))
    for path in paths:
        provenance.mark(out, path, "cad", ref=parsed.get("file_name") if isinstance(parsed, dict) else None)
    new = _scenario(out)
    return {"scenario": new.model_dump(mode="json", by_alias=True, exclude_none=True),
            "report": built["report"], "preview": built["preview"]}


# ---------- IT 부하 ----------
class ItFile(BaseModel):
    name: str = Field(max_length=255)
    data_b64: str


class TableOverride(BaseModel):
    mapping: dict[str, str] | None = Field(default=None, description="역할 → 열 이름 (지정하면 그 표는 자동 매칭 대신 사용)")
    kind: Literal["long", "wide", "total", "inventory", "ignore"] | None = None
    unit: Literal["W", "kW", "MW", "Wh", "kWh", "MWh", "%", "fraction"] | None = None


class ItInspectRequest(BaseModel):
    files: Annotated[list[ItFile], Field(min_length=1, max_length=10)]
    overrides: dict[str, TableOverride] = Field(default_factory=dict, description="표 label → 덮어쓰기")


class ItApplyRequest(ItInspectRequest):
    day: str = Field(default="peak", description="peak(설계일) | mean(시각별 평균) | YYYY-MM-DD")
    scenario: Scenario
    save_dataset: bool = Field(default=True, description="기간 전체를 IT 데이터셋으로 저장 (연간·내일 예측에 사용)")


def _analyses(req: ItInspectRequest):
    files = [(f.name, _bytes(f.data_b64, f.name)) for f in req.files]
    ov = {k: v.model_dump(exclude_none=True) for k, v in req.overrides.items()}
    try:
        out = itdata.inspect_files(files, ov)
    except itdata.ItDataError as e:
        raise HTTPException(422, str(e))
    for a in out:
        if ov.get(a["label"], {}).get("kind") == "ignore":
            a["kind"] = "ignore"
    return out


@router.post("/itload/inspect", tags=["import"])
def itload_inspect(req: ItInspectRequest):
    return {"tables": [itdata.public(a) for a in _analyses(req)],
            "roles": [{"value": r, "label": itdata.ROLE_LABEL[r]} for r in itdata.ROLES]}


@router.post("/itload/apply", tags=["import"])
def itload_apply(req: ItApplyRequest):
    from hvac.multizone import SingleZoneOnly, single_zone_only
    try:
        single_zone_only(req.scenario.to_engine_dict(), "IT 데이터 적용")
    except SingleZoneOnly as e:
        raise HTTPException(422, str(e))
    analyses = [a for a in _analyses(req) if a["kind"] != "ignore"]
    sc = req.scenario.to_engine_dict()
    lay = _layout(req.scenario)
    racks, grid = itdata.layout_racks(sc, lay)
    try:
        built = itdata.build(analyses, sc, racks, req.day)
    except itdata.ItDataError as e:
        raise HTTPException(422, str(e))
    recs = built.pop("records")
    out = itdata.apply_to_scenario(sc, lay, built, grid)
    ds_summary = None
    if req.save_dataset:
        ds = itdata.dataset(recs, out["it_load"]["design_kw"])
        if ds is None:
            built["report"].setdefault("notes", []).append("날짜 정보가 없어 IT 데이터셋은 만들지 않았습니다 (하루 프로파일만 사용)")
        else:
            saved = datasets.save("it", ds, ", ".join(f.name for f in req.files))
            out["it_load"]["dataset_id"] = saved["meta"]["id"]
            ds_summary = {"id": saved["meta"]["id"], **saved["summary"]}
    for path in ("it_load.utilization", "it_load.design_kw", "cooling_layout.racks"):
        provenance.mark(out, path, "it_data", ref=", ".join(f.name for f in req.files)[:200])
    new = _scenario(out)
    return {"scenario": new.model_dump(mode="json", by_alias=True, exclude_none=True), "report": built["report"],
            "tables": [itdata.public(a) for a in analyses], "dataset": ds_summary}

