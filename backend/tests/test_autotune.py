"""자동 튜닝 (Phase 18, hvac/autotune.py) — 우선순위 단계, 목표별 해, 해결 후 경고 없음, 다중 존, API"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, autotune
from hvac.advice import evaluate_issues
from hvac.optimize import apply_candidate
from hvac.plant import peak_hour
from hvac.schemas import CoolingLayout, Scenario

client = TestClient(app)


def load(name, mut=None):
    sc = Scenario.model_validate(json.loads((DATA_DIR / name).read_text(encoding="utf-8"))).to_engine_dict()
    cfg = CoolingLayout.model_validate(sc.get("cooling_layout") or {}).model_dump()
    if mut:
        mut(sc, cfg)
    sc["cooling_layout"] = cfg
    return sc, cfg


def hot(sc, cfg):
    cfg["supply_temp_c"] = 24.0          # 흡입 상한 초과


def coil(sc, cfg):
    sc["cooling_plant"].update(chw_supply_c=16.0, chw_return_c=22.0)   # 급기 18 < 16 + 6


def low_air(sc, cfg):
    cfg["crah"]["airflow_ratio"] = 0.6   # 여유 0.8K (주의)


def test_no_problem_no_change():
    sc, cfg = load("scenario_sample.json")
    r = autotune.run(sc, cfg)
    assert r["status"] == "ok" and not r["before"]["problems"]


def test_first_priority_alone_when_enough():
    sc, cfg = load("scenario_sample.json", low_air)
    r = autotune.run(sc, cfg)
    assert r["status"] == "solved" and r["stage"] == 1
    assert [c["key"] for c in r["changes"]] == ["tile_balancing"]
    assert r["after"]["inlet_margin_c"] >= 1.0 - 1e-9 and r["after"]["issues"] == []


def test_priority_order_is_respected():
    """급기온도를 1순위로 올리면 급기만으로 해결 (타일·풍량은 건드리지 않음)"""
    sc, cfg = load("scenario_sample.json", hot)
    st = autotune.defaults(cfg)
    st.insert(0, st.pop(next(i for i, s in enumerate(st) if s["key"] == "supply_temp_c")))
    r = autotune.run(sc, cfg, st)
    assert r["stage"] == 1 and [c["key"] for c in r["changes"]] == ["supply_temp_c"]


def test_disabled_param_never_changes():
    sc, cfg = load("scenario_sample.json", hot)
    st = [{**s, "enabled": s["key"] != "supply_temp_c" and s["enabled"]} for s in autotune.defaults(cfg)]
    r = autotune.run(sc, cfg, st)
    assert all(c["key"] != "supply_temp_c" for c in r.get("changes") or [])


@pytest.mark.parametrize("mut", [hot, coil, low_air])
@pytest.mark.parametrize("objective", ["min_change", "min_energy"])
def test_solution_has_no_warnings(mut, objective):
    sc, cfg = load("scenario_sample.json", mut)
    r = autotune.run(sc, cfg, objective=objective)
    assert r["status"] == "solved"
    sc2, c2 = apply_candidate(sc, cfg, r["params"])
    assert evaluate_issues(sc2, c2, peak_hour(sc2), []) == []    # 다시 계산해도 주의·경고 없음


def test_min_energy_not_worse_than_min_change():
    sc, cfg = load("scenario_sample.json", hot)
    a = autotune.run(sc, cfg, objective="min_change")
    b = autotune.run(sc, cfg, objective="min_energy")
    assert b["after"]["daily_kwh"] <= a["after"]["daily_kwh"] + 1e-6
    assert len(a["changes"]) <= len(b["changes"])


def test_unsolvable_reports_honestly():
    sc, cfg = load("scenario_sample.json", hot)
    st = [{**s, "enabled": s["key"] == "tile_balancing"} for s in autotune.defaults(cfg)]
    r = autotune.run(sc, cfg, st)
    assert r["status"] == "unsolved" and "해결하지 못했" in r["message"]


def test_multizone_all_halls_satisfied():
    sc, cfg = load("scenario_haenam_ai_mz.json", coil)
    t = time.time()
    r = autotune.run(sc, cfg, objective="min_energy")
    assert time.time() - t < 30 and r["status"] == "solved"
    sc2, c2 = apply_candidate(sc, cfg, r["params"])
    assert evaluate_issues(sc2, c2, peak_hour(sc2), []) == []


def test_influence_items():
    sc, cfg = load("scenario_sample.json", hot)
    inf = autotune.influence(sc, cfg)
    by = {x["key"]: x for x in inf["items"]}
    assert by["supply_temp_c"]["inlet_margin_k"] == pytest.approx(0.5)   # 급기 0.5℃ ↓ = 흡입 0.5℃ ↓
    assert by["airflow_ratio"]["kwh"] > 0                                 # 풍량 ↑ → 팬 전력 ↑


def test_api_job_and_apply():
    d = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    d["cooling_layout"] = {"supply_temp_c": 24.0}
    defaults = client.post("/autotune/defaults", json=d).json()
    assert defaults["settings"][0]["key"] == "tile_balancing"
    bad = {"scenario": d, "settings": [{"key": "airflow_ratio", "min": 1.2, "max": 0.5}]}
    as_ui = [{k: (float(v) if isinstance(v, int) and not isinstance(v, bool) else v) for k, v in s.items()
              if k in ("key", "enabled", "min", "max", "step", "choices")} for s in defaults["settings"]]   # 화면은 숫자를 실수로 보냄
    assert client.post("/autotune/influence", json={"scenario": d, "settings": as_ui}).status_code == 200
    assert client.post("/jobs/autotune", json=bad).status_code == 422
    job = client.post("/jobs/autotune", json={"scenario": d}).json()["job_id"]
    for _ in range(200):
        j = client.get(f"/jobs/{job}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["result"]["status"] == "solved"
    sc = client.post("/autotune/apply", json={"scenario": d, "params": j["result"]["params"]}).json()
    assert sc["cooling_layout"]["supply_temp_c"] == j["result"]["params"]["supply_temp_c"]
    assert sc["provenance"]["cooling_layout"]["source"] == "optimizer"
    assert sc["provenance"]["cooling_layout"]["note"] == "자동 튜닝"
