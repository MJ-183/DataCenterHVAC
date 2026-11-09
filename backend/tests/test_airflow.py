"""기류 모델 테스트: backend 폴더에서  python -m pytest tests/test_airflow.py"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR
from hvac.airflow import analyze
from hvac.schemas import CoolingLayout

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def run(**over):
    cfg = CoolingLayout.model_validate(over).model_dump()
    return analyze(sample(), cfg)


def test_energy_balance_crah_removes_it_heat():
    for containment in ("open", "cold_aisle", "hot_aisle"):
        r = run(containment=containment)
        assert r["metrics"]["crah_heat_kw"] == pytest.approx(r["it_kw"], rel=2e-3)


def test_rack_layout_matches_3d_defaults():
    r = run()
    assert (r["room"]["rows"], r["room"]["per_row"]) == (8, 40) and r["metrics"]["rack_count"] == 320
    assert len(r["crah"]) == 10
    assert r["hour"] in range(24) and sum(x["kw"] for x in r["racks"]) == pytest.approx(r["it_kw"], rel=1e-3)


def test_containment_improves_inlet_temperature():
    open_, cold, hot = (run(containment=c)["metrics"] for c in ("open", "cold_aisle", "hot_aisle"))
    assert open_["max_inlet_c"] > cold["max_inlet_c"] > hot["max_inlet_c"]
    assert open_["shi"] > hot["shi"]


def test_more_airflow_lowers_inlet_but_costs_fan_power():
    lo = run(crah={"airflow_ratio": 0.8})["metrics"]
    hi = run(crah={"airflow_ratio": 1.2})["metrics"]
    assert hi["max_inlet_c"] <= lo["max_inlet_c"] and hi["crah_fan_kw"] > lo["crah_fan_kw"]
    assert hi["crah_fan_kw"] / lo["crah_fan_kw"] == pytest.approx((1.2 / 0.8) ** 3, rel=1e-2)


def test_tile_balancing_reduces_hotspots():
    far = run(crah={"tile_balancing": 0.0})["metrics"]
    bal = run(crah={"tile_balancing": 1.0})["metrics"]
    assert bal["max_inlet_c"] <= far["max_inlet_c"]


def test_high_density_row_runs_hotter():
    r = run(racks={"row_density": [1, 1, 1, 1, 1, 1, 1, 3]})
    rows = {}
    for x in r["racks"]:
        rows.setdefault(x["row"], []).append(x["inlet_c"])
    assert max(rows[7]) > max(rows[0])


def test_api_analyze_and_default_layout():
    assert client.get("/cooling/layout/default").json()["containment"] == "open"
    r = client.post("/cooling/analyze", json={"scenario": sample(), "layout": {"containment": "hot_aisle"}, "hour": 14})
    assert r.status_code == 200 and r.json()["hour"] == 14
    assert r.json()["metrics"]["rack_count"] == 320


@pytest.mark.parametrize("body", [
    {"layout": {"containment": "weird"}},
    {"layout": {"racks": {"rows": 2, "row_density": [1]}}},
    {"layout": {"crah": {"airflow_ratio": 0}}},
    {"hour": 24},
])
def test_api_rejects_bad_input(body):
    assert client.post("/cooling/analyze", json={"scenario": copy.deepcopy(sample()), **body}).status_code == 422
