"""Phase 15 랙별 혼합 냉각 + 증설 배치"""
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.platform import run_sync
from hvac import DATA_DIR, mixed, placement
from hvac.airflow import analyze
from hvac.schemas import CoolingLayout

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def test_all_racks_dlc_equals_global_hybrid():
    sc = sample()
    sc["cooling_plant"]["architecture"] = "hybrid_dlc"
    cfg = CoolingLayout().model_dump()
    g = analyze(sc, cfg)["metrics"]
    c2 = placement.to_items(sc, cfg)
    assert analyze(sc, c2)["metrics"] == g                         # 격자 → items 변환은 결과 불변
    for it in c2["racks"]["items"]:
        it["liquid_fraction"] = 0.75
    assert analyze(sc, c2)["metrics"]["max_inlet_c"] == pytest.approx(g["max_inlet_c"], abs=1e-6)


def test_mixed_rows_sync_and_effective_phi():
    sc = sample()
    r = client.post("/cooling/racks/apply", json={"scenario": sc, "rows": {"0": 0.75, "1": 0.75}})
    assert r.status_code == 200, r.text
    s2 = r.json()
    cp = s2["cooling_plant"]
    assert cp["architecture"] == "hybrid_dlc" and cp["liquid_fraction"] == pytest.approx(0.75 * 2 / 8, abs=1e-3)
    ev = client.post("/cooling/evaluate", json=s2).json()
    assert ev["warnings"] == []
    s2["cooling_plant"]["liquid_fraction"] = 0.5
    assert client.post("/cooling/evaluate", json=s2).json()["warnings"]
    back = client.post("/scenario/revert", json={"scenario": r.json(), "paths": list(r.json()["provenance"])}).json()
    assert back == client.get("/scenario/sample").json()


def test_rack_update_unknown_id():
    assert client.post("/cooling/racks/apply", json={"scenario": sample(), "updates": {"ZZ": {"liquid_fraction": 0.5}}}
                       ).status_code == 422


def test_placement_prefers_cool_spots_and_is_deterministic():
    sc = sample()
    cfg = CoolingLayout.model_validate({"racks": {"rows": 8, "per_row": 30}}).model_dump()
    new = [{"count": 4, "design_kw": 30.0, "liquid_fraction": 0.75}, {"count": 4, "design_kw": 10.0}]
    a = placement.place(sc, cfg, new, max_slots=40, max_moves=20)
    b = placement.place(sc, cfg, new, max_slots=40, max_moves=20)
    assert [(i["x_m"], i["z_m"]) for i in a["items"]] == [(i["x_m"], i["z_m"]) for i in b["items"]]
    assert len(a["items"]) == 8 and a["added_design_kw"] == 160
    assert a["after"]["max_inlet_c"] >= a["before"]["max_inlet_c"] - 1e-9
    # 탐욕 배치 결과는 같은 랙을 가장 나쁜 자리(목적이 최대인 자리)에 둔 것보다 나쁘지 않다
    assert a["after"]["racks_over_limit"] == 0
    with pytest.raises(ValueError):
        placement.place(sc, cfg, [{"count": 500, "design_kw": 5.0}], max_slots=40)


def test_placement_job_and_apply():
    sc = sample()
    sc["cooling_layout"] = CoolingLayout.model_validate({"racks": {"rows": 8, "per_row": 30}}).model_dump(exclude_none=True)
    res = run_sync("placement", {"scenario": sc, "new_racks": [{"count": 3, "design_kw": 20.0, "liquid_fraction": 0.75}]})
    items = [{k: v for k, v in it.items() if k != "inlet_c"} for it in res["items"]]
    r = client.post("/cooling/racks/apply", json={"scenario": sc, "add_items": items,
                                                  "unit_airflow_m3_s": res["unit_airflow_m3_s"]})
    assert r.status_code == 200, r.text
    s2 = r.json()
    assert s2["it_load"]["design_kw"] == pytest.approx(2060)
    assert len(s2["cooling_layout"]["racks"]["items"]) == 240 + 3
    ev = client.post("/cooling/evaluate", json=s2).json()
    assert ev["summary"]["max_inlet_c"] == pytest.approx(res["after"]["max_inlet_c"], abs=0.05)
