"""Phase 13 연간 최적화: 비용 순서, 변경 폭 제한, 스칼라 검증, 스케줄 적용 → 연간·연성 결과 일치, 되돌리기"""
import json

import pytest
from fastapi.testclient import TestClient

from api import annual as annual_api
from api.main import app
from api.platform import run_sync
from hvac import DATA_DIR, annual
from hvac import weather as wx
from hvac.annual_opt import _dp, optimize_year
from hvac.schemas import CoolingLayout
from tests.test_annual import synthetic_year

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def result():
    return optimize_year(sample(), CoolingLayout().model_dump(), synthetic_year(), topology=("dual",))


def test_cost_order_and_check(result):
    best, same = result["best"], result["same_structure"]
    assert best["monthly_kwh"] <= best["fixed_kwh"] + 1
    assert same["monthly_kwh"] <= result["current"]["annual_kwh"]
    assert result["check"]["ok"]
    assert len(best["schedule"]) == 12 and all(e["inlet_margin_c"] >= 0 for e in best["schedule"])
    assert "절감" in result["summary"]


def test_step_limit(result):
    s = result["best"]["schedule"]
    for a, b in zip(s, s[1:] + s[:1]):
        assert abs(a["supply_temp_c"] - b["supply_temp_c"]) <= 2.0 + 1e-9
    r0 = optimize_year(sample(), CoolingLayout().model_dump(), synthetic_year(), topology=("dual",),
                       containment=("hot_aisle",), max_step_c=0.0)
    temps = {e["supply_temp_c"] for e in r0["best"]["schedule"]}
    assert len(temps) == 1


def test_dp_respects_wrap():
    import numpy as np
    cost = np.array([[1.0, 5.0], [5.0, 1.0], [1.0, 5.0]])
    tsup = np.array([[20.0, 24.0]] * 3)
    path, total = _dp(cost, tsup, 10)
    assert path == [0, 1, 0] and total == 3
    path, total = _dp(cost, tsup, 0)
    assert len(set(path)) == 1


def test_schedule_apply_and_annual_consistency(tmp_path, monkeypatch, result):
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    annual_api.CACHE.clear()
    wx.save_annual(synthetic_year(), tmp_path)
    best = result["best"]
    body = {"scenario": sample(), "structure": best["structure"],
            "schedule": [{k: e[k] for k in ("month", "supply_temp_c", "chw_supply_c", "chw_return_c", "airflow_ratio")}
                         for e in best["schedule"]]}
    r = client.post("/annual/schedule/apply", json=body)
    assert r.status_code == 200, r.text
    sc = r.json()
    lay = sc["cooling_layout"]
    july = next(e for e in best["schedule"] if e["month"] == 7)          # 샘플 날짜 7월 → 현재 운전점 = 7월 값
    assert lay["supply_temp_c"] == july["supply_temp_c"] and sc["cooling_plant"]["chw_supply_c"] == july["chw_supply_c"]
    assert len(lay["operating_schedule"]) == 12 and sc["provenance"]["cooling_layout"]["source"] == "schedule"
    a = client.post("/annual/run", json={"scenario": sc, "weather_file": "annual_37.57_126.98_2025.json",
                                         "coupled": True}).json()
    assert a["uses_schedule"] and a["coupled"]["uses_schedule"]
    assert a["coupled"]["annual_kwh"] == pytest.approx(best["monthly_kwh"], rel=0.005)
    back = client.post("/scenario/revert", json={"scenario": sc, "paths": list(sc["provenance"])}).json()
    assert back == client.get("/scenario/sample").json()


def test_job_runs_with_saved_weather(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    wx.save_annual(synthetic_year(n_days=62), tmp_path)
    res = run_sync("annual_optimize", {"scenario": sample(), "weather_file": "annual_37.57_126.98_2025.json",
                                       "topology": ["radial"], "containment": ["open", "cold_aisle"]})
    assert res["months"] == [1, 2, 3] and res["best"]["schedule"]


def test_annual_without_schedule_unchanged():
    sc = sample()
    a = annual.run(sc, synthetic_year(n_days=5))
    assert not a["uses_schedule"] and a["coupled"] is None
