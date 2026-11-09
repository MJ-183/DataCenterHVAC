"""회귀 스냅샷 (Phase 10, 무충돌 원칙 C7) - 새 기능의 기본값에서 기존 결과가 그대로인지 확인.
기준을 의도적으로 바꿀 때만:  UPDATE_SNAPSHOTS=1 python -m pytest tests/test_snapshots.py  (사유를 README 에 기록)"""
import json
import os
from pathlib import Path

import pytest

from hvac import DATA_DIR, annual, simulate
from hvac import weather as wx
from hvac.optimize import optimize
from hvac.plant import evaluate
from hvac.schemas import CoolingLayout

SNAP = Path(__file__).parent / "snapshots"
ANNUAL = DATA_DIR / "weather" / "annual_37.57_126.98_2025.json"


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def check(name, value):
    p = SNAP / f"{name}.json"
    value = json.loads(json.dumps(value))
    if os.getenv("UPDATE_SNAPSHOTS") or not p.exists():
        p.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    assert value == json.loads(p.read_text(encoding="utf-8")), f"{name} 스냅샷과 다릅니다"


def test_simulate_sample():
    check("simulate_sample", simulate(sample(), 60))


def test_evaluate_sample_summary():
    check("evaluate_sample_summary", evaluate(sample(), CoolingLayout().model_dump())["summary"])


def test_optimize_sample_top1():
    o = optimize(sample(), CoolingLayout().model_dump(), {"containment": ["cold_aisle", "hot_aisle"], "topology": ["dual"]})
    check("optimize_sample_top1", {"baseline": o["baseline"], "top1": o["candidates"][0]})


@pytest.mark.skipif(not ANNUAL.exists(), reason="연간 기상 파일 없음")
def test_annual_seoul2025_totals():
    r = annual.run(sample(), wx.load_annual(ANNUAL))
    check("annual_seoul2025_totals", {"comparison": r["comparison"],
                                      "monthly_air": [m["cooling_power_kwh"] for m in r["architectures"][0]["monthly"]]})
