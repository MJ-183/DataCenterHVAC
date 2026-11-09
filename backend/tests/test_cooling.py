"""배관·연성 평가·최적화 테스트: backend 폴더에서  python -m pytest tests/test_cooling.py"""
import copy
import json
import math

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR
from hvac.cooling_schemas import EvaluateResult, OptimizeResult
from hvac.optimize import optimize
from hvac.piping import dp_kpa, pick_dn
from hvac.plant import evaluate
from hvac.schemas import CoolingLayout, Scenario

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def cfg(**over):
    return CoolingLayout.model_validate(over).model_dump()


# ---------- 배관 ----------
def test_darcy_weisbach_reference_value():
    # DN100 (내경 102.3mm), 1.5 m/s, 100 m -> 손실 약 2.4 kPa/10m 수준 (Moody 선도 f≈0.019)
    q = 1.5 * math.pi * 0.1023 ** 2 / 4
    assert 20 < dp_kpa(q, 0.1023, 100) < 28
    assert dp_kpa(0, 0.1, 100) == 0


def test_pipe_sizing_respects_velocity_limit():
    for q in (0.001, 0.02, 0.08, 0.3):
        dn, d = pick_dn(q, 2.0)
        assert q / (math.pi * d * d / 4) <= 2.0


@pytest.mark.parametrize("topo", ["radial", "dual", "ring"])
def test_network_segments_and_head(topo):
    e = evaluate(sample(), cfg(piping={"topology": topo}))
    p = e["piping"]
    assert p["topology"] == topo and p["chw_head_kpa"] > 50 + 80   # 기기 손실 이상
    kinds = {s["kind"] for s in p["segments"]}
    assert kinds == {"chw_supply", "chw_return", "cw_supply", "cw_return"}
    trunk = p["segments"][0]
    assert trunk["design_flow_m3_h"] == pytest.approx(p["chw_design_flow_m3_h"], rel=1e-3)


def test_dual_header_shorter_critical_path_than_radial():
    r = evaluate(sample(), cfg(piping={"topology": "radial"}))["piping"]
    d = evaluate(sample(), cfg(piping={"topology": "dual"}))["piping"]
    assert d["chw_critical_m"] < r["chw_critical_m"] and d["chw_head_kpa"] < r["chw_head_kpa"]


def test_ring_flows_conserve_mass():
    """루프: 각 CRAH 지점에서 유입-유출 = CRAH 유량 (구간 유량 합으로 확인)"""
    p = evaluate(sample(), cfg(piping={"topology": "ring"}))["piping"]
    sup = [s for s in p["segments"] if s["kind"] == "chw_supply"]
    trunk, loop = sup[0], sup[1:]
    first, last = loop[0], loop[-1]      # 분기점에서 나가는 두 방향
    assert first["design_flow_m3_h"] + last["design_flow_m3_h"] == pytest.approx(trunk["design_flow_m3_h"], abs=0.3)


# ---------- 연성 평가 ----------
def test_evaluate_schema_and_shapes():
    e = evaluate(sample(), cfg())
    assert EvaluateResult.model_validate(e).model_dump(exclude_unset=True) == e   # API 와 같이 (다중 존 필드는 없을 때 생략)
    assert len(e["rack_inlet_c"]) == 24 and len(e["rack_inlet_c"][0]) == e["airflow"]["metrics"]["rack_count"]
    assert e["summary"]["feasible"] and e["summary"]["violations"] == []


def test_daily_energy_is_sum_of_hours():
    e = evaluate(sample(), cfg())
    assert e["summary"]["daily_kwh"]["total"] == pytest.approx(sum(h["total_kw"] for h in e["hourly"]), abs=0.5)
    for h in e["hourly"]:
        parts = h["chiller_kw"] + h["crah_fan_kw"] + h["chw_pump_kw"] + h["cw_pump_kw"]
        assert h["total_kw"] == pytest.approx(parts, abs=0.2)


def test_higher_chw_supply_improves_cop():
    lo = sample()
    hi = copy.deepcopy(lo)
    hi["cooling_plant"].update(chw_supply_c=15.0, chw_return_c=21.0)
    a = evaluate(lo, cfg())["summary"]
    b = evaluate(hi, cfg(supply_temp_c=21.0))["summary"]
    assert b["cop_peak"] > a["cop_peak"] and b["daily_kwh"]["chiller"] < a["daily_kwh"]["chiller"]


def test_violations_reported():
    s = evaluate(sample(), cfg(supply_temp_c=16.0))["summary"]       # 16 < 12 + 6
    assert not s["feasible"] and any("코일 접근" in v for v in s["violations"])
    s = evaluate(sample(), cfg(supply_temp_c=26.0, crah={"airflow_ratio": 0.6}))["summary"]
    assert any("흡입온도 초과" in v for v in s["violations"])


# ---------- 최적화 ----------
def test_optimizer_improves_and_respects_constraints():
    o = optimize(sample(), cfg())
    assert OptimizeResult.model_validate(o).model_dump() == o
    best = o["candidates"][0]
    assert best["daily_kwh"]["total"] < o["baseline"]["daily_kwh"]["total"]
    for c in o["candidates"]:
        p = c["params"]
        assert c["feasible"] and c["max_inlet_c"] <= 27.0
        assert p["supply_temp_c"] >= p["chw_supply_c"] + 6.0 - 1e-9
        assert p["chw_return_c"] <= c["crah_return_c"] - 2.0 + 1e-9
    totals = [c["daily_kwh"]["total"] for c in o["candidates"]]
    assert totals == sorted(totals)


def test_optimizer_candidate_reproduced_by_evaluate():
    """최적화가 보고한 값과, 그 후보를 적용해 다시 평가한 값이 일치해야 함"""
    o = optimize(sample(), cfg(), {"containment": ["cold_aisle"], "topology": ["dual"]})
    best = o["candidates"][0]
    r = client.post("/cooling/apply", json={"scenario": sample(), "params": best["params"]})
    assert r.status_code == 200
    applied = r.json()
    e = client.post("/cooling/evaluate", json=applied).json()["summary"]
    assert e["daily_kwh"]["total"] == pytest.approx(best["daily_kwh"]["total"], abs=1.0)
    assert e["max_inlet_c"] == pytest.approx(best["max_inlet_c"], abs=0.05)
    assert e["feasible"]


def test_optimizer_respects_allowed_containment():
    o = optimize(sample(), cfg(), {"containment": ["open"], "topology": ["radial"]})
    assert {c["params"]["containment"] for c in o["candidates"]} == {"open"}


def test_optimizer_crah_count_changes_result_and_is_reproduced():
    """CRAH 대수 후보는 1대 크기를 고정 → 대수가 결과(총풍량·팬 전력)를 바꾸고, 적용 후 재평가와 일치"""
    sp = {"containment": ["hot_aisle"], "topology": ["ring"]}
    tot = {n: optimize(sample(), cfg(), {**sp, "crah_per_side": [n]})["candidates"][0]["daily_kwh"]["total"]
           for n in (3, 5, 7)}
    assert tot[3] > tot[5] > tot[7]          # 같은 풍량이면 대수가 많을수록 팬 전력 감소 (풍량^3)
    best = optimize(sample(), cfg(), {**sp, "crah_per_side": [3]})["candidates"][0]
    applied = client.post("/cooling/apply", json={"scenario": sample(), "params": best["params"]}).json()
    assert applied["cooling_layout"]["crah"]["unit_airflow_m3_s"] > 0
    e = client.post("/cooling/evaluate", json=applied).json()["summary"]
    assert e["daily_kwh"]["total"] == pytest.approx(best["daily_kwh"]["total"], abs=1.0)


@pytest.mark.parametrize("arch", ["hybrid_dlc", "immersion"])
def test_optimizer_liquid_architectures_have_candidates(arch):
    """액체 냉각은 공기측 설계 풍량이 공기로 나가는 발열만큼 → 후보가 있고 방식에 따라 결과가 다름"""
    s = sample()
    s["cooling_plant"]["architecture"] = arch
    sp = {"containment": ["cold_aisle"], "topology": ["dual"]}
    o = optimize(s, cfg(), sp)
    assert o["baseline"]["feasible"] and o["candidates"]
    air = optimize(sample(), cfg(), sp)["candidates"][0]["daily_kwh"]["total"]
    assert o["candidates"][0]["daily_kwh"]["total"] < air


# ---------- API / 시나리오 저장 호환 ----------
def test_scenario_with_cooling_layout_roundtrip():
    sc = sample()
    sc["cooling_layout"] = {"containment": "hot_aisle", "piping": {"topology": "ring"}}
    m = Scenario.model_validate(sc)
    assert m.cooling_layout.containment == "hot_aisle"
    r = client.post("/simulate", json=sc)                # 부하 엔진은 cooling_layout 무시
    base = client.post("/simulate", json=sample())
    assert r.status_code == 200 and r.json() == base.json()


def test_api_evaluate_uses_scenario_layout():
    sc = sample()
    sc["cooling_layout"] = {"piping": {"topology": "dual"}}
    r = client.post("/cooling/evaluate", json=sc)
    assert r.status_code == 200 and r.json()["piping"]["topology"] == "dual"


def test_api_optimize_small_space():
    r = client.post("/cooling/optimize", json={"scenario": sample(), "space": {
        "containment": ["hot_aisle"], "airflow_ratio": [0.8, 1.0], "tile_balancing": [0.5], "topology": ["ring"],
        "chw_delta_t": [6], "top_n": 3}})
    assert r.status_code == 200
    body = r.json()
    assert body["airflow_evaluations"] == 2 and len(body["candidates"]) >= 1
