"""API 테스트: backend 폴더에서  python -m pytest"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, simulate
from hvac.schemas import Scenario, SimulationResult

client = TestClient(app)


def load(name):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_scenario_sample_roundtrip():
    r = client.get("/scenario/sample")
    assert r.status_code == 200
    assert r.json() == load("scenario_sample.json")   # _note 포함, 값 동일


def test_simulate_matches_result_sample():
    r = client.post("/simulate", json=load("scenario_sample.json"))
    assert r.status_code == 200
    body = r.json()
    assert body.pop("step_min") == 60
    assert body.pop("architecture") == "air_chw"
    for x in body["hourly"]:            # Phase 4 이후 추가된 키 제외 후 비교
        for k in ("sun", "surface_solar_w_m2", "minute", "t_h", "plant"):
            x.pop(k)
    expected = load("result_sample.json")
    body["daily_total"] = {k: body["daily_total"][k] for k in expected["daily_total"]}
    assert body == expected


def test_output_schema_covers_engine_output():
    """엔진 출력의 모든 키가 스키마에 정의되어 있는지 (extra=forbid 로 누락 시 실패)"""
    raw = simulate(load("scenario_sample.json"))
    assert SimulationResult.model_validate(raw).model_dump(exclude_unset=True) == raw   # 현실성 필드는 켰을 때만


def test_engine_dict_matches_raw_json():
    """pydantic 을 거친 입력으로 계산해도 원본 dict 입력과 결과가 같아야 함"""
    sample = load("scenario_sample.json")
    assert simulate(Scenario.model_validate(sample).to_engine_dict()) == simulate(sample)


def _bad(mutate):
    sc = copy.deepcopy(load("scenario_sample.json"))
    mutate(sc)
    return client.post("/simulate", json=sc)


@pytest.mark.parametrize("mutate", [
    lambda s: s["weather"]["dry_bulb_c"].pop(),                                  # 23개
    lambda s: s["cooling_plant"].update(chw_return_c=12.0),                     # 냉수 ΔT = 0
    lambda s: s["cooling_plant"].update(cw_return_c=30.0),                      # 냉각수 ΔT < 0
    lambda s: s["cooling_plant"].update(tower_cycles_of_concentration=1.0),     # COC = 1
    lambda s: s["envelope"]["walls"][0].update(orientation="NE"),               # 미지원 방위
    lambda s: s["weather"]["rh_pct"].__setitem__(0, 120),                       # RH > 100
    lambda s: s["it_load"].update(desing_kw=1),                                 # 오타 필드
])
def test_invalid_input_rejected(mutate):
    assert _bad(mutate).status_code == 422


def test_cors_allows_vite_dev_server():
    r = client.options("/simulate", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_simulate_step_min_query():
    r = client.post("/simulate?step_min=5", json=load("scenario_sample.json"))
    assert r.status_code == 200
    body = r.json()
    assert body["step_min"] == 5 and len(body["hourly"]) == 288
    assert client.post("/simulate?step_min=7", json=load("scenario_sample.json")).status_code == 422
