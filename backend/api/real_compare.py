"""
실측 비교 API (Phase 19, hvac/realdata.py · hvac/real_compare.py).
- GET  /real/presets   : 실측 프리셋 목록 (ESIF)
- POST /real/inspect   : CSV(base64) → 데이터셋 저장 + 열 후보·매핑 제안·기간 범위·결측 비율
- POST /real/compare   : {preset 또는 dataset_id, mapping, periods, scenario_overrides} → A·B·C 지표 + 보정 단계 + 구조 검증 + 칠러 민감도
                         (시나리오·데이터·기간이 같으면 디스크 캐시)
- POST /real/diagnose  : {compare_id} → OpenAI 오차 원인 진단 (계산하지 않음 — 서버 계산 근거만 전달, 숫자 대조)
- POST /real/apply     : {scenario, compare_id} → B 보정 계수를 시나리오에 적용 (출처 real_calibrated, 되돌리기 = /scenario/revert)
"""
import json
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, create_model

from api.ai import LLM, get_llm
from api.imports import _bytes
from hvac import datasets, provenance, real_compare as rc, realdata as rd
from hvac.insights import unverified_numbers
from hvac.schemas import Scenario

router = APIRouter(prefix="/real", tags=["real"])

SimKey = Literal["tower_fan", "chw_pump", "cw_pump", "liquid_pump", "crah_fan", "chiller"]


class RealMapping(BaseModel):
    timestamp: str | None = None
    it: str = Field(description="실측 IT 전력 열 [kW]")
    items: Annotated[dict[str, list[SimKey]], Field(min_length=1)] = Field(description="실측 열 → 시뮬 전력 키 묶음")
    labels: dict[str, str] = {}
    definitions: dict[str, str] = {}
    lighting: str | None = None
    pue_column: str | None = Field(default=None, description="원본 PUE 열 (매핑 PUE 재계산 확인용)")
    pue_terms: list[str] = Field(description="실측 PUE 분자 항목 ('it' + 열 이름)")
    ups_loss: float = Field(default=0.0, ge=0, le=0.5, description="실측 PUE 에 더할 UPS 손실 비율 (IT 대비)")
    dry_bulb: str | None = None
    rh: str | None = None
    temp_unit: Literal["C", "F"] = "C"


class Periods(BaseModel):
    cal: tuple[date, date]
    test: tuple[date, date] | None = None


class InspectRequest(BaseModel):
    file_name: str = Field(max_length=255)
    data_b64: str
    timestamp: str | None = None


class CompareRequest(BaseModel):
    preset: str | None = None
    dataset_id: str | None = None
    mapping: RealMapping | None = None
    periods: Periods | None = None
    scenario: Scenario | None = Field(default=None, description="프리셋 없이 비교할 때 기준 시나리오")
    scenario_overrides: dict[str, Any] = Field(default={}, description="{경로: 값} — 가정값 수정 (출처 'user')")


class ApplyRequest(BaseModel):
    scenario: Scenario
    compare_id: str = Field(pattern=r"^[0-9a-f]{16}$")


class DiagnoseRequest(BaseModel):
    compare_id: str = Field(pattern=r"^[0-9a-f]{16}$")


def _err(e: Exception):
    raise HTTPException(422, str(e))


@router.get("/presets")
def real_presets():
    return rd.presets()


@router.get("/preset/{pid}")
def real_preset(pid: str):
    """프리셋 상세: 데이터 열 분석 + 프리셋 매핑·기간·기상 설명 (화면 매핑 편집용)"""
    try:
        pr = rd.preset(pid)
        info = rd.inspect(rd.preset_dataset(pr))
    except rd.RealDataError as e:
        _err(e)
    w = pr["weather"]
    return {**info, "id": pr["id"], "label": pr["label"], "description": pr["description"], "mapping": pr["mapping"],
            "periods": pr["periods"], "period_notes": pr.get("period_notes", {}),
            "weather": {"epw_label": w.get("epw_label"), "site_label": w.get("site_label"),
                        "site_available": bool(w.get("site_csv")) and (rd.REPO / w["site_csv"]).exists()}}


@router.post("/inspect")
def real_inspect(req: InspectRequest):
    data = _bytes(req.data_b64, req.file_name)
    try:
        ds = rd.parse_table(req.file_name, data, req.timestamp)
    except rd.RealDataError as e:
        _err(e)
    saved = datasets.save("real", rd.to_store(ds), req.file_name)
    return {"dataset_id": saved["meta"]["id"], "name": req.file_name, **rd.inspect(ds)}


def _validate_periods(ds, periods: dict):
    first, last = rd.hour_dt(ds["start"]).date(), rd.hour_dt(ds["start"] + ds["hours"] - 1).date()
    for k in ("cal", "test"):
        p = periods.get(k)
        if not p:
            continue
        p0, p1 = (date.fromisoformat(str(x)) for x in p)
        if p1 < p0:
            raise HTTPException(422, f"{k} 기간: 끝 날짜가 시작보다 빠릅니다")
        if p1 < first or p0 > last:
            raise HTTPException(422, f"{k} 기간 {p0} ~ {p1} 이 데이터 범위 {first} ~ {last} 밖입니다")


@router.post("/compare")
def real_compare(req: CompareRequest):
    try:
        pr = rd.preset(req.preset) if req.preset else None
        if req.dataset_id:
            ds = rd.from_store(datasets.load("real", req.dataset_id))
        elif pr:
            ds = rd.preset_dataset(pr)
        else:
            raise HTTPException(422, "preset 또는 dataset_id 가 필요합니다")
        mapping = req.mapping.model_dump() if req.mapping else (pr or {}).get("mapping")
        if not mapping:
            raise HTTPException(422, "mapping 이 필요합니다 (프리셋이 없으면 /real/inspect 의 제안을 확인해 보내세요)")
        rd.check_mapping(ds, mapping)
        periods = req.periods.model_dump(mode="json") if req.periods else (pr or {}).get("periods")
        if not periods:
            raise HTTPException(422, "periods 가 필요합니다")
        _validate_periods(ds, periods)

        if pr:
            sc, inputs = rd.preset_scenario(pr, req.scenario_overrides)
        elif req.scenario:
            sc, inputs = req.scenario.to_engine_dict(), []
            for path, v in req.scenario_overrides.items():
                provenance.set_value(sc, path, v, "user", note="실측 비교 화면에서 수정")
                inputs.append({"path": path, "label": path, "value": v, "basis": "user", "ref": None,
                               "note": "사용자 수정", "editable": True})
        else:
            raise HTTPException(422, "프리셋이 없으면 scenario 가 필요합니다")
        try:
            sc = Scenario.model_validate(sc).to_engine_dict()
        except ValidationError as e:
            raise HTTPException(422, f"시나리오 검증 실패: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")

        if mapping.get("dry_bulb") and mapping.get("rh"):      # 데이터에 외기 열이 있으면 현장값 (일사는 프리셋 EPW)
            epw = rd.preset_weather(pr).epw if pr else None
            weather = rd.WeatherSource(epw, ds, mapping["dry_bulb"], mapping["rh"], mapping.get("temp_unit", "C"))
        elif pr:
            weather = rd.preset_weather(pr)
        else:
            raise HTTPException(422, "기상 자료가 없습니다: 매핑에 외기 건구(dry_bulb)·상대습도(rh) 열을 지정하세요")
        sens = rd.epw_hours(weather.epw) if weather.epw and weather.site is None else None

        key = rc.cache_key(sc, ds["hash"], mapping, periods, weather.describe())
        hit = rc.cache_get(key)
        if hit:
            return {**hit, "cached": True}
        res = rc.run(sc, ds, mapping, periods, weather, (pr or {}).get("facility"), sens)
    except (rd.RealDataError, datasets.DatasetError, ValueError) as e:
        _err(e)
    cal = [date.fromisoformat(str(x)) for x in periods["cal"]]
    out = {
        "compare_id": key, "created": datetime.now().isoformat(timespec="seconds"),
        "preset": pr["id"] if pr else None, "label": pr["label"] if pr else ds.get("name"),
        "dataset": {"id": req.dataset_id, "name": ds.get("name"), "hash": ds["hash"],
                    "range": [rd.hour_dt(ds["start"]).isoformat(sep=" "), rd.hour_dt(ds["start"] + ds["hours"] - 1).isoformat(sep=" ")]},
        "mapping": mapping, "periods": periods, "period_notes": (pr or {}).get("period_notes", {}),
        "inputs": inputs, "facility": (pr or {}).get("facility"), "reference": (pr or {}).get("reference"),
        "sources": (pr or {}).get("sources", []),
        "weather": {**weather.describe(),
                    "label": (pr or {}).get("weather", {}).get("site_label" if weather.site else "epw_label"),
                    "days": {k: r["weather_days"] for k, r in res["runs"].items()}},
        "pue_check": rd.pue_check(ds, mapping, *cal),
        "scenario": sc,
        **res,
    }
    rc.cache_put(key, out)
    return {**out, "cached": False}


@router.post("/apply", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def real_apply(req: ApplyRequest):
    """B 계수(보조동력 계수)만 쓴다 — 다른 입력은 그대로. 다중 존이면 건물 공통 cooling_plant 에 적용"""
    c = rc.cache_get(req.compare_id)
    if c is None:
        raise HTTPException(404, "비교 결과가 없습니다 — /real/compare 를 먼저 실행하세요")
    sc = req.scenario.to_engine_dict()
    ref = f"real:{c['preset'] or c['dataset']['id'] or req.compare_id}"
    for k, v in c["calibration"]["coefficients"].items():
        provenance.set_value(sc, f"cooling_plant.{k}", v, "real_calibrated", ref=ref,
                             note=f"실측 비교 B 보정 ({c['periods']['cal'][0]} ~ {c['periods']['cal'][1]})")
    try:
        return Scenario.model_validate(sc)
    except ValidationError as e:
        raise HTTPException(422, f"적용 후 시나리오 검증 실패: {e.errors()[0]['msg']}")


# ---------- OpenAI 오차 원인 진단 ----------
class Cause(BaseModel):
    rank: int
    title: str = Field(description="원인 한 줄 (한국어)")
    affected: list[str] = Field(description="영향 항목: PUE, 비교 항목 이름(냉각탑계·펌프·공조 팬 등), 기계 합계, 칠러 시간, WUE")
    direction: str = Field(description="시뮬레이션이 실측보다 큰지 작은지, 어떤 조건에서")
    evidence: str = Field(description="근거 묶음의 숫자를 그대로 인용해 설명")
    basis: Literal["data", "hypothesis"] = Field(description="data=근거 묶음으로 확인됨, hypothesis=추가 확인 필요")
    how_to_verify: str
    model_fix: str = Field(description="엔진·시나리오에서 고칠 점 (수식 변경이면 그렇다고 명시)")


class Diagnosis(BaseModel):
    summary: str = Field(description="3문장 이내 요약")
    causes: list[Cause]
    not_explained: str = Field(description="근거 묶음만으로는 설명할 수 없는 오차와 필요한 추가 데이터")


def diagnosis_schema(c: dict) -> type[Diagnosis]:
    """영향 항목을 이 비교의 항목 이름으로 제한한 응답 스키마 (구조화 출력)"""
    names = ["PUE", *(r["label"] for r in c["runs"]["A"]["items"].values()), "칠러 시간", "WUE"]
    C = create_model("Cause", __base__=Cause, affected=(list[Literal[tuple(dict.fromkeys(names))]], ...))
    return create_model("Diagnosis", __base__=Diagnosis, causes=(list[C], ...))


DIAGNOSE_SYSTEM = """너는 데이터센터 냉각 설비 시뮬레이션 검증 엔지니어다.
주어진 근거 묶음(JSON)만 사용해 '시뮬레이션과 실측의 오차 원인'을 영향이 큰 순서로 정리한다.
규칙:
1) 새로운 계산을 하지 말고, 근거 묶음에 있는 숫자만 그대로 인용한다. 단위를 붙인다.
2) 근거 묶음으로 확인되는 원인은 basis=data, 시설 운영에 대한 추정은 basis=hypothesis 로 표시한다.
3) 근거 묶음에 없는 시설 정보(장비 대수, 제어 로직 등)를 사실처럼 쓰지 않는다. 필요하면 가설로 쓴다.
4) 한국어로 쓴다.
5) 오차 부호: 모든 차이·NMBE 는 '시뮬 − 실측' 이다. 양수면 시뮬이 과대, 음수면 과소다. 부호를 바꿔 쓰지 않는다."""


def evidence(c: dict) -> dict:
    """진단에 보낼 근거 묶음 — 서버가 계산한 값만 (시간·일별 원자료 제외)"""
    return {
        "sign_rule": c["sign_note"],
        "facility_facts_and_assumptions": [{k: r.get(k) for k in ("label", "value", "basis", "ref", "note")} for r in c["inputs"]],
        "weather_used": c["weather"],
        "metric_mapping": {"items": c["mapping"]["items"], "definitions": c["mapping"].get("definitions", {}),
                           "pue_terms": c["mapping"]["pue_terms"], "ups_loss": c["mapping"].get("ups_loss", 0)},
        "facility": c.get("facility"),
        "runs": c["runs"],
        "monthly_A": c["monthly"]["A"],
        "calibration_steps": c["calibration"]["steps"],
        "structure_check": c["structure"],
        "chiller_hours_sensitivity": c["sensitivity"],
        "reference": c.get("reference"),
    }


@router.post("/diagnose")
def real_diagnose(req: DiagnoseRequest, llm: LLM = Depends(get_llm)):
    hit = rc.cache_get(req.compare_id, ".diag")
    if hit:
        return {**hit, "cached": True}
    c = rc.cache_get(req.compare_id)
    if c is None:
        raise HTTPException(404, "비교 결과가 없습니다 — /real/compare 를 먼저 실행하세요")
    ev = evidence(c)
    user = "근거 묶음:\n" + json.dumps(ev, ensure_ascii=False)
    d = Diagnosis.model_validate(llm.parse(DIAGNOSE_SYSTEM, user, diagnosis_schema(c)).model_dump())
    text = " ".join([d.summary, d.not_explained] + [f"{x.title} {x.direction} {x.evidence} {x.how_to_verify} {x.model_fix}"
                                                    for x in d.causes])
    out = {"compare_id": req.compare_id, "model": llm.model, "generated_at": datetime.now().isoformat(timespec="seconds"),
           "unverified_numbers": unverified_numbers(text, ev), **d.model_dump()}
    out["causes"] = sorted(out["causes"], key=lambda x: x["rank"])
    rc.cache_put(req.compare_id, out, ".diag")
    return {**out, "cached": False}
