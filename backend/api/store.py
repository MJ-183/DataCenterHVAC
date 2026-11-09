"""
시나리오 저장/불러오기 (Phase 6) + BMS 실측 비교.
저장 위치: backend/data/scenarios/<이름>.json  (환경변수 HVAC_SCENARIO_DIR 로 변경 가능)
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hvac import DATA_DIR
from hvac.schemas import Scenario, SimulationResult
from hvac.validation import METRICS, CsvError, compare, parse_bms_csv

router = APIRouter(tags=["scenarios"])

NAME_RE = re.compile(r"^[\w가-힣 .()\-]{1,60}$")


def scenario_dir() -> Path:
    d = Path(os.getenv("HVAC_SCENARIO_DIR") or DATA_DIR / "scenarios")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(name: str) -> Path:
    name = name.strip()
    if not NAME_RE.match(name) or name.startswith(".") or ".." in name:
        raise HTTPException(422, "이름은 1~60자의 한글/영문/숫자/공백/._-() 만 사용할 수 있습니다")
    return scenario_dir() / f"{name}.json"


class SavedInfo(BaseModel):
    name: str
    saved_at: str
    note: str | None = None


@router.get("/scenarios", response_model=list[SavedInfo])
def list_scenarios():
    out = []
    for p in sorted(scenario_dir().glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append(SavedInfo(name=d["name"], saved_at=d["saved_at"], note=d["scenario"].get("_note")))
        except (OSError, ValueError, KeyError):
            continue  # 손상된 파일은 목록에서 제외
    return out


@router.get("/scenarios/{name}", response_model=Scenario, response_model_by_alias=True, response_model_exclude_none=True)
def get_scenario(name: str):
    p = _path(name)
    if not p.exists():
        raise HTTPException(404, f"'{name}' 시나리오가 없습니다")
    return Scenario.model_validate(json.loads(p.read_text(encoding="utf-8"))["scenario"])


@router.put("/scenarios/{name}", response_model=SavedInfo)
def save_scenario(name: str, scenario: Scenario):
    p = _path(name)
    info = SavedInfo(name=name.strip(), saved_at=datetime.now().isoformat(timespec="seconds"), note=scenario.note)
    p.write_text(json.dumps({"name": info.name, "saved_at": info.saved_at, "scenario": scenario.to_engine_dict()},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return info


@router.delete("/scenarios/{name}")
def delete_scenario(name: str):
    p = _path(name)
    if not p.exists():
        raise HTTPException(404, f"'{name}' 시나리오가 없습니다")
    p.unlink()
    return {"deleted": name}


# ---------- BMS 비교 ----------
class BmsCompareRequest(BaseModel):
    csv: str
    result: SimulationResult


@router.get("/bms/template", tags=["bms"])
def bms_template():
    """CSV 양식 (값은 비어 있음 - 실측값을 채워 업로드)"""
    header = "hour," + ",".join(METRICS)
    rows = "\n".join(f"{h}," + "," * (len(METRICS) - 1) for h in range(24))
    return {"csv": header + "\n" + rows + "\n",
            "columns": {k: {"label": v[0], "unit": v[1]} for k, v in METRICS.items()}}


@router.post("/bms/compare", tags=["bms"])
def bms_compare(req: BmsCompareRequest):
    try:
        measured = parse_bms_csv(req.csv)
    except CsvError as e:
        raise HTTPException(422, str(e))
    return {"metrics": compare(req.result.model_dump(), measured)}
