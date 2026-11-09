"""해남 40 MW AI DC 시나리오 (data/scenario_haenam_ai.json) — 스키마·연간 실행·규모 점검·시나리오 선택 API"""
import json
import shutil

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import app
from hvac import DATA_DIR, annual, datasets
from hvac import weather as wx
from hvac.airflow import analyze, building_dims, rack_positions
from hvac.plant import evaluate
from hvac.plant_units import size_units
from hvac.schemas import CoolingLayout, CrahConfig, Scenario

client = TestClient(app)
REAL_DS = DATA_DIR / "datasets"   # conftest 가 datasets.ROOT 를 임시 폴더로 바꾸기 전 실제 위치


def haenam():
    return Scenario.model_validate(json.loads((DATA_DIR / "scenario_haenam_ai.json").read_text(encoding="utf-8")))


@pytest.fixture
def sc():
    return haenam().to_engine_dict()


@pytest.fixture
def cfg(sc):
    return CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()


@pytest.fixture
def ds(sc):
    """커밋된 IT 데이터셋을 테스트용 임시 저장소로 복사"""
    ds_id = sc["it_load"]["dataset_id"]
    dst = datasets.ROOT / "it" / f"{ds_id}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REAL_DS / "it" / f"{ds_id}.json", dst)
    return datasets.load("it", ds_id)


def test_schema_and_reported_values(sc):
    assert sc["it_load"]["design_kw"] == 40000
    assert sc["internal"]["floor_area_m2"] == 16978
    assert sc["site"]["asos_station_id"] == 261 and sc["site"]["asos_solar_station_id"] == 165
    assert sc["cooling_plant"]["architecture"] == "hybrid_dlc" and sc["cooling_plant"]["liquid_fraction"] == 0.75
    prov = sc["provenance"]
    assert prov["it_load.design_kw"]["ref"].startswith("https://") and prov["envelope"]["source"] == "assumed"
    for k, e in prov.items():   # 가정값에는 근거(note)가 있어야 함
        assert e.get("note"), k


def test_design_day_is_wet_bulb_max_of_weather_file(sc):
    ann = wx.load_annual(wx.WEATHER_DIR / "annual_kma_261_2025_auto165.json")
    days = [{"weather": d["weather"], "meta": {"date": d["date"]}} for d in ann["days"]]
    assert sc["site"]["date"] == wx.design_days(days)["hottest_wet_bulb"]["date"]


def test_envelope_dims_match_floor(sc):
    ew, ns, h = building_dims(sc)
    assert ew * ns == pytest.approx(16978 / 2, rel=0.02)   # 창 면적만큼 벽이 줄어 약간 다름
    assert h == pytest.approx(12.0, rel=0.03)


def test_racks_fit_floor_and_total_400(sc, cfg):
    ew, ns, _ = building_dims(sc)
    racks, rows, per = rack_positions(cfg, ew, ns)
    assert len(racks) == 400 and (rows, per) == (cfg["racks"]["rows"], cfg["racks"]["per_row"])
    assert sc["it_load"]["design_kw"] / len(racks) == 100


def test_scale_check_units_crah_piping(sc, cfg):
    r = evaluate(sc, cfg)
    u = r["units"]
    # 대수 = ceil(설계 부하 / 1대 용량) + 예비 1
    for k in ("chiller", "tower", "cdu"):
        assert u[k]["duty"] >= 2 and u[k]["total"] == u[k]["duty"] + 1
        assert (u[k]["duty"] - 1) * u[k]["unit_kw"] < u[k]["design_kw"] <= u[k]["duty"] * u[k]["unit_kw"]
    assert u["tower"]["design_kw"] > 38000      # 40 MW 규모 방열
    assert cfg["crah"]["per_side"] > 5           # 샘플 기본값(5)이 아니라 규모에 맞게
    assert r["airflow"]["crah"][0]["airflow_m3_s"] <= 15.0 + 1e-6
    dns = {s["kind"]: s["dn"] for s in r["piping"]["segments"]}
    assert max(s["dn"] for s in r["piping"]["segments"]) < 1500   # 관경 표 상한에 걸리지 않음
    assert dns["cw_supply"] >= 1000
    assert r["summary"]["racks_over_limit"] == 0 and r["warnings"] == []


def test_crah_limit_scaled_but_bounded():
    CrahConfig(per_side=60)
    with pytest.raises(ValidationError):
        CrahConfig(per_side=121)


def test_sample_has_no_units():
    sample = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    r = evaluate(sample, CoolingLayout().model_dump())
    assert r["units"] is None
    assert size_units(CoolingLayout().model_dump(), {"hourly": []}) is None


def test_peak_hour_airflow_ok(sc, cfg):
    m = analyze(sc, cfg)["metrics"]
    assert m["rack_count"] == 400 and m["racks_over_limit"] == 0


def test_annual_runs_with_dataset(sc, ds):
    ann = wx.load_annual(wx.WEATHER_DIR / "annual_kma_261_2025_auto165.json")
    res = annual.run(sc, ann, it_dataset=ds)
    assert res["it_source"] == "dataset" and res["n_days"] == 365
    by = {c["architecture"]: c for c in res["comparison"]}
    it_mwh = res["architectures"][0]["annual"]["it_kwh"] / 1000
    assert it_mwh == pytest.approx(40000 * 0.78 * 8760 / 1000, rel=0.01)   # 데이터셋 연평균 가동률 0.78
    assert by["immersion"]["pue"] < by["hybrid_dlc"]["pue"] < by["air_chw"]["pue"]
    assert by["hybrid_dlc"]["cooling_power_kwh"] < by["air_chw"]["cooling_power_kwh"]


def test_preset_api():
    ids = [p["id"] for p in client.get("/scenario/presets").json()]
    assert ids == ["haenam", "haenam_mz", "dummy_3f", "seoul"]
    r = client.get("/scenario/preset/haenam")
    assert r.status_code == 200 and r.json()["it_load"]["design_kw"] == 40000
    assert client.get("/scenario/preset/seoul").json() == client.get("/scenario/sample").json()
    assert client.get("/scenario/preset/nope").status_code == 404


def test_cooling_evaluate_api_returns_units():
    sc = client.get("/scenario/preset/haenam").json()
    r = client.post("/cooling/evaluate", json=sc)
    assert r.status_code == 200 and set(r.json()["units"]) == {"chiller", "tower", "cdu"}
