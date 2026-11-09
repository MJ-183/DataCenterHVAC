"""다중 존 (Phase 18, hvac/multizone.py) — 단일 존 패리티·합산 보존·존간 전도·검증·연성 모델·예측/최적화 동작"""
import copy
import json
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import app
from hvac import DATA_DIR, annual, datasets, outlook, simulate
from hvac import weather as wx
from hvac.airflow import analyze
from hvac.annual_opt import optimize_year
from hvac.engine import simulate_zone
from hvac.multizone import halls, interzone_kw
from hvac.optimize import optimize
from hvac.plant import evaluate
from hvac.schemas import CoolingLayout, Scenario

client = TestClient(app)
REAL_DS = DATA_DIR / "datasets"


def load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def sample():
    return load("scenario_sample.json")


def mz():
    return Scenario.model_validate(load("scenario_haenam_ai_mz.json")).to_engine_dict()


def split_sample():
    """서울 샘플을 동·서 두 전산실로 나눔 (외피·IT·인원·외기 절반씩, 실내온도 같음)"""
    sc = sample()
    env = sc["envelope"]
    half = lambda o, side: [{**w, "area_m2": w["area_m2"] / 2} for w in env[side] if w["orientation"] == o]
    whole = lambda o, side: [w for w in env[side] if w["orientation"] == o]

    def zone(zid, end):
        walls = half("N", "walls") + half("S", "walls") + whole(end, "walls")
        wins = half("N", "windows") + half("S", "windows") + whole(end, "windows")
        return {"id": zid, "name": zid, "floor": 0, "kind": "data_hall",
                "geometry": {"ew_m": 30, "ns_m": 50, "height_m": 10, "x_m": -15 if end == "W" else 15},
                "envelope": {"walls": walls, "roof": {**env["roof"], "area_m2": env["roof"]["area_m2"] / 2}, "windows": wins},
                "it_design_kw": sc["it_load"]["design_kw"] / 2, "floor_area_m2": sc["internal"]["floor_area_m2"] / 2,
                "people": sc["internal"]["people"] / 2, "outdoor_air_m3_h": sc["ventilation"]["outdoor_air_m3_h"] / 2,
                "partitions": [{"zone": "east", "area_m2": 500, "u_value": 1.5}] if zid == "west" else []}
    sc["zones"] = [zone("west", "W"), zone("east", "E")]
    return Scenario.model_validate(sc).to_engine_dict()


def test_single_zone_path_unchanged():
    sc = sample()
    assert simulate(sc) == simulate_zone(sc)


def test_one_zone_equals_single_zone():
    sc = sample()
    one = copy.deepcopy(sc)
    one["zones"] = [{"id": "all", "name": "전체", "floor": 0, "kind": "data_hall",
                     "geometry": {"ew_m": 60, "ns_m": 50, "height_m": 10}, "envelope": sc["envelope"],
                     "it_design_kw": sc["it_load"]["design_kw"], "floor_area_m2": sc["internal"]["floor_area_m2"],
                     "people": sc["internal"]["people"], "outdoor_air_m3_h": sc["ventilation"]["outdoor_air_m3_h"]}]
    one = Scenario.model_validate(one).to_engine_dict()
    a, b = simulate(one), simulate(sc)
    assert a["daily_total"] == b["daily_total"]
    for x, y in zip(a["hourly"], b["hourly"]):
        assert x["load_kw"] == pytest.approx(y["load_kw"], abs=0.05)
        assert x["plant"]["power_kw"] == pytest.approx(y["plant"]["power_kw"], abs=0.05)
        assert x["plant"]["mode"] == y["plant"]["mode"]


def test_split_zones_sum_to_single_zone():
    """설비 식이 부하에 선형 → 같은 실내온도로 나눈 두 존의 합 = 단일 존 (시간별 반올림 차이만)"""
    a, b = simulate(split_sample())["daily_total"], simulate(sample())["daily_total"]
    for k in ("cooling_kwh", "chiller_kwh", "it_kwh", "cooling_power_kwh", "facility_kwh", "makeup_m3"):
        assert a[k] == pytest.approx(b[k], rel=2e-3), k
    assert a["free_cooling_hours"] == b["free_cooling_hours"]


def test_interzone_conduction_conserves_heat():
    sc = mz()
    qs = {z["id"]: interzone_kw(sc, z) for z in sc["zones"]}
    assert sum(qs.values()) == pytest.approx(0.0, abs=1e-9)
    assert qs["elec_1f"] < 0 < qs["office_2f"]          # 30℃ 전기실 → 24℃ 사무실로 (위층 슬래브)
    assert qs["hall_2f"] == pytest.approx(0.0)            # 같은 24℃ 끼리는 0


def test_zone_results_and_building_total():
    r = simulate(mz())
    zs = {z["id"]: z for z in r["zones"]}
    assert set(zs) == {"hall_1f", "elec_1f", "hall_2f", "office_2f"}
    total = sum(z["daily_total"]["cooling_kwh"] for z in r["zones"])
    assert r["daily_total"]["cooling_kwh"] == pytest.approx(total, rel=1e-4)
    assert r["daily_total"]["it_kwh"] == pytest.approx(sum(z["daily_total"]["it_kwh"] for z in r["zones"]), rel=1e-4)
    assert zs["elec_1f"]["daily_total"]["it_kwh"] == 0


@pytest.mark.parametrize("mut, msg", [
    (lambda z: z.__setitem__(1, {**z[1], "id": z[0]["id"]}), "중복"),
    (lambda z: z[0]["partitions"].append({"zone": "nope", "area_m2": 1, "u_value": 1}), "partitions"),
    (lambda z: z.__setitem__(0, {**z[0], "it_design_kw": 1.0}), "IT 설계부하 합"),
    (lambda z: [x.__setitem__("kind", "office") for x in z], "전산실"),
])
def test_zone_validation(mut, msg):
    d = load("scenario_haenam_ai_mz.json")
    mut(d["zones"])
    with pytest.raises(ValidationError, match=msg):
        Scenario.model_validate(d)


def test_combined_airflow_is_worst_hall():
    sc = mz()
    cfg = CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()
    m = analyze(sc, cfg, 8)["metrics"]
    each = [analyze(s, s["cooling_layout"], 8)["metrics"] for _, s in halls(sc, cfg)]
    assert m["max_inlet_c"] == max(x["max_inlet_c"] for x in each)
    assert m["rack_count"] == sum(x["rack_count"] for x in each) == 400
    assert m["crah_fan_kw"] >= sum(x["crah_fan_kw"] for x in each)
    assert m["crah_return_c"] == min(x["crah_return_c"] for x in each)


def test_evaluate_selects_hall_for_display():
    sc = mz()
    cfg = CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()
    e1, e2 = evaluate(sc, cfg), evaluate(sc, cfg, "hall_2f")
    assert e1["zone"] == "hall_1f" and e2["zone"] == "hall_2f"
    assert len(e1["rack_inlet_c"][0]) == 200 and e1["geometry"]["building"]["ew_m"] == 95.0
    assert [z["id"] for z in e1["zones"]] == ["hall_1f", "hall_2f"]
    assert e1["summary"] == e2["summary"]       # 요약은 건물 합산 (표시 전산실과 무관)
    r = client.post("/cooling/evaluate?zone=hall_2f", json=load("scenario_haenam_ai_mz.json"))
    assert r.status_code == 200 and r.json()["zone"] == "hall_2f"
    assert "zone" not in client.post("/cooling/evaluate", json=sample()).json()


def test_day_optimizer_respects_every_hall():
    sc = mz()
    cfg = CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()
    o = optimize(sc, cfg, {"containment": ["hot_aisle"], "topology": ["radial"], "tile_balancing": [cfg["crah"]["tile_balancing"]]})
    best = o["candidates"][0]
    assert best["feasible"]
    c = copy.deepcopy(cfg)
    c["crah"]["airflow_ratio"], c["supply_temp_c"] = best["params"]["airflow_ratio"], best["params"]["supply_temp_c"]
    for _, s in halls(sc, c):
        assert analyze(s, s["cooling_layout"])["metrics"]["max_inlet_c"] <= cfg["inlet_limit_c"] + 1e-9


@pytest.fixture
def ds():
    sc = mz()
    ds_id = sc["it_load"]["dataset_id"]
    dst = datasets.ROOT / "it" / f"{ds_id}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REAL_DS / "it" / f"{ds_id}.json", dst)
    return datasets.load("it", ds_id)


def test_annual_and_annual_optimizer_run(ds):
    sc = mz()
    ann = wx.load_annual(wx.WEATHER_DIR / "annual_kma_261_2025_auto165.json")
    r = annual.run(sc, ann, it_dataset=ds, architectures=("hybrid_dlc",))
    assert r["comparison"][0]["pue"] > 1
    cfg = CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()
    y = optimize_year(sc, cfg, ann, it_dataset=ds, containment=("hot_aisle",), topology=("radial",))
    assert y["check"]["ok"] and y["best"]["monthly_kwh"] <= y["current"]["annual_kwh"]


def test_briefing_runs(ds):
    sc = mz()
    cfg = CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()
    ann = wx.load_annual(wx.WEATHER_DIR / "annual_kma_261_2025_auto165.json")
    d = next(x for x in ann["days"] if x["date"] == "2025-07-22")
    t = outlook.tomorrow(sc, cfg, date(2025, 7, 22), {"weather": d["weather"], "meta": {"source": "kma_asos", "date": d["date"],
                                                                                          "source_detail": "test"}}, ds)
    assert t["recommended"] and t["recommended"]["inlet_margin_c"] >= 0


def test_single_zone_only_features_refuse_multizone():
    r = client.post("/cooling/racks/apply", json={"scenario": load("scenario_haenam_ai_mz.json"), "updates": {}})
    assert r.status_code == 422 and "단일 존" in r.json()["detail"]


def test_preset_lists_multizone():
    ids = [p["id"] for p in client.get("/scenario/presets").json()]
    assert "haenam_mz" in ids
    assert client.get("/scenario/preset/haenam_mz").json()["zones"][0]["id"] == "hall_1f"
