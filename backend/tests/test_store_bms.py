"""시나리오 저장 / BMS 비교 테스트"""
import json
import math

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, simulate
from hvac.validation import CsvError, compare, parse_bms_csv

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HVAC_SCENARIO_DIR", str(tmp_path))


def test_save_list_load_delete():
    sc = sample()
    sc["it_load"]["design_kw"] = 2500
    assert client.put("/scenarios/여름 증설안", json=sc).status_code == 200
    names = [x["name"] for x in client.get("/scenarios").json()]
    assert names == ["여름 증설안"]
    assert client.get("/scenarios/여름 증설안").json() == sc
    assert client.delete("/scenarios/여름 증설안").status_code == 200
    assert client.get("/scenarios/여름 증설안").status_code == 404


@pytest.mark.parametrize("bad", ["..", "a/b", "x" * 61, ".hidden"])
def test_bad_names(bad):
    assert client.put(f"/scenarios/{bad}", json=sample()).status_code in (404, 422)


def test_invalid_scenario_not_saved():
    sc = sample()
    sc["cooling_plant"]["chw_return_c"] = 0
    assert client.put("/scenarios/x", json=sc).status_code == 422
    assert client.get("/scenarios").json() == []


# ---------- BMS ----------
def test_compare_hand_calc():
    r = simulate(sample())
    c0 = r["hourly"][0]["load_kw"]["total_cooling"]
    c1 = r["hourly"][1]["load_kw"]["total_cooling"]
    csv_text = f"hour,total_cooling_kw\n0,{c0 - 100}\n1,{c1 + 50}\n2,\n"
    m = compare(r, parse_bms_csv(csv_text))[0]
    s = m["stats"]
    assert s["n"] == 2
    assert math.isclose(s["mae"], 75)
    assert math.isclose(s["rmse"], math.sqrt((100**2 + 50**2) / 2), abs_tol=1e-3)
    mean_meas = (c0 - 100 + c1 + 50) / 2
    assert math.isclose(s["nmbe_pct"], round((100 - 50) / (2 * mean_meas) * 100, 2))
    assert s["max_abs_error_hour"] == 0


@pytest.mark.parametrize("text", [
    "",
    "time,total_cooling_kw\n0,1\n",
    "hour,foo\n0,1\n",
    "hour,total_cooling_kw\n24,1\n",
    "hour,total_cooling_kw\n0,abc\n",
    "hour,total_cooling_kw\n0,1\n0,2\n",
])
def test_csv_errors(text):
    with pytest.raises(CsvError):
        parse_bms_csv(text)


def test_bms_endpoints():
    tpl = client.get("/bms/template").json()
    assert tpl["csv"].startswith("hour,total_cooling_kw")
    res = client.post("/simulate", json=sample()).json()
    r = client.post("/bms/compare", json={"csv": "﻿hour,chiller_cop\n12,6.0\n", "result": res})
    assert r.status_code == 200
    assert r.json()["metrics"][0]["points"][0]["calculated"] == res["hourly"][12]["chiller"]["cop"]
    assert client.post("/bms/compare", json={"csv": "x", "result": res}).status_code == 422


def test_bms_compare_uses_on_the_hour_steps():
    res = client.post("/simulate?step_min=5", json=sample()).json()
    r = client.post("/bms/compare", json={"csv": "hour,total_cooling_kw\n13,1000\n", "result": res}).json()
    on_hour = next(x for x in res["hourly"] if x["hour"] == 13 and x["minute"] == 0)
    assert r["metrics"][0]["points"][0]["calculated"] == on_hour["load_kw"]["total_cooling"]
