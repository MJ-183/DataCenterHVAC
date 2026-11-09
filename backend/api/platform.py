"""
공통 기반 API (Phase 10).
- 작업(Job): POST /jobs/{kind} → job_id, GET /jobs/{id} (진행률·단계·결과), DELETE /jobs/{id} (취소)
  오래 걸리는 계산(연간 최적화, 보정, 랙 배치, 투자 검토)을 백그라운드 스레드에서 돌린다. 결과는 메모리에 1시간 보관
- 데이터셋: GET /datasets?kind=, GET /datasets/{kind}/{id}, DELETE /datasets/{kind}/{id}
- 시나리오 상태: POST /scenario/status (시작 가이드·데이터 신뢰도), POST /scenario/revert (적용 되돌리기)
"""
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hvac import datasets, provenance
from hvac.schemas import Scenario

router = APIRouter()
POOL = ThreadPoolExecutor(max_workers=2)
JOBS: "OrderedDict[str, dict]" = OrderedDict()
JOB_KEEP_S = 3600
LOCK = threading.Lock()
# kind → (요청 모델, 실행 함수(req, progress) → 결과 dict). 각 기능 라우터가 register() 로 등록
REGISTRY: dict[str, tuple[type[BaseModel], Callable]] = {}


class JobCancelled(Exception):
    pass


def register(kind, model, fn):
    REGISTRY[kind] = (model, fn)


def _gc():
    now = time.time()
    for k in [k for k, j in JOBS.items() if j["status"] in ("done", "error", "cancelled") and now - j["t"] > JOB_KEEP_S]:
        JOBS.pop(k, None)


def _run(job_id, fn, req):
    job = JOBS[job_id]

    def progress(frac, stage=""):
        if job["cancel"]:
            raise JobCancelled()
        job.update(progress=round(max(0.0, min(1.0, frac)), 3), stage=stage)
    job["status"] = "running"
    try:
        job["result"] = fn(req, progress)
        job.update(status="done", progress=1.0, stage="완료")
    except JobCancelled:
        job.update(status="cancelled", stage="취소됨")
    except HTTPException as e:
        job.update(status="error", error=str(e.detail))
    except Exception as e:   # noqa: BLE001 - 작업 실패를 화면에 전달
        msg = str(e) if isinstance(e, (ValueError, KeyError)) else f"계산 중 오류: {type(e).__name__}: {e}"
        job.update(status="error", error=msg)
    job["t"] = time.time()


@router.post("/jobs/{kind}", tags=["jobs"])
def start_job(kind: str, body: dict):
    if kind not in REGISTRY:
        raise HTTPException(404, f"알 수 없는 작업 종류: {kind}")
    model, fn = REGISTRY[kind]
    try:
        req = model.model_validate(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with LOCK:
        _gc()
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"id": job_id, "kind": kind, "status": "queued", "progress": 0.0, "stage": "대기 중",
                        "result": None, "error": None, "cancel": False, "t": time.time()}
    POOL.submit(_run, job_id, fn, req)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}", tags=["jobs"])
def get_job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "작업이 없습니다 (만료되었거나 서버가 다시 시작됨)")
    return {k: j[k] for k in ("id", "kind", "status", "progress", "stage", "result", "error")}


@router.delete("/jobs/{job_id}", tags=["jobs"])
def cancel_job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    j["cancel"] = True
    return {"ok": True}


def run_sync(kind, body):
    """테스트·동기 호출용: 등록된 작업 함수를 그대로 실행"""
    model, fn = REGISTRY[kind]
    return fn(model.model_validate(body), lambda *a: None)


# ---------- 데이터셋 ----------
@router.get("/datasets", tags=["datasets"])
def list_datasets(kind: str | None = None):
    if kind and kind not in datasets.KINDS:
        raise HTTPException(422, f"kind 는 {datasets.KINDS} 중 하나")
    return datasets.listing(kind)


@router.get("/datasets/{kind}/{ds_id}", tags=["datasets"])
def get_dataset(kind: str, ds_id: str):
    try:
        d = datasets.load(kind, ds_id)
    except datasets.DatasetError as e:
        raise HTTPException(404, str(e))
    return {"meta": d["meta"], "summary": d.get("summary")}


@router.delete("/datasets/{kind}/{ds_id}", tags=["datasets"])
def delete_dataset(kind: str, ds_id: str):
    try:
        datasets.delete(kind, ds_id)
    except datasets.DatasetError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ---------- 시나리오 상태 ----------
@router.post("/scenario/status", tags=["scenario"])
def scenario_status(scenario: Scenario):
    sc = scenario.to_engine_dict()
    return {"guide": provenance.guide(sc), "trust": provenance.trust(sc)}


class RevertRequest(BaseModel):
    scenario: Scenario
    paths: list[str] = Field(min_length=1)


@router.post("/scenario/revert", tags=["scenario"], response_model=Scenario, response_model_by_alias=True,
             response_model_exclude_none=True)
def scenario_revert(req: RevertRequest):
    sc, done = provenance.revert(req.scenario.to_engine_dict(), req.paths)
    if not done:
        raise HTTPException(422, "되돌릴 기록이 없습니다")
    return Scenario.model_validate(sc)
