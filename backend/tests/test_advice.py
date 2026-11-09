"""주의·경고 해결 방법 (hvac.advice) - 권장값을 실제로 적용하면 문제가 풀리는지 확인"""
import copy
import json

from hvac import DATA_DIR, placement
from hvac.advice import evaluate_issues, inlet_issue
from hvac.airflow import analyze
from hvac.plant import evaluate
from hvac.cooling_schemas import EvaluateResult
from hvac.schemas import CoolingLayout, Scenario

SETTERS = {
    "layout.supply": lambda c, v: c.__setitem__("supply_temp_c", v),
    "layout.ratio": lambda c, v: c["crah"].__setitem__("airflow_ratio", v),
    "layout.tile": lambda c, v: c["crah"].__setitem__("tile_balancing", v),
    "layout.containment": lambda c, v: c.__setitem__("containment", v),
}


def base():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        s = Scenario.model_validate(json.load(f))
    return s.to_engine_dict(), (s.cooling_layout or CoolingLayout()).model_dump()


def test_sample_has_no_issues():
    sc, cfg = base()
    e = evaluate(sc, cfg)
    assert e["issues"] == []
    EvaluateResult.model_validate(e)


def test_inlet_fixes_actually_solve():
    sc, cfg = base()
    cfg["crah"]["airflow_ratio"] = 0.45
    iss = inlet_issue(sc, cfg)
    assert iss and iss["level"] == "crit" and iss["fixes"]
    limit = cfg["inlet_limit_c"]
    checked = 0
    for f in iss["fixes"]:
        if f["solves"] and f["target"] in SETTERS:
            c = copy.deepcopy(cfg)
            SETTERS[f["target"]](c, f["value"])
            assert analyze(sc, c)["metrics"]["max_inlet_c"] <= limit + 1e-9, f
            checked += 1
    assert checked >= 2
    # 해결되는 방법이 먼저, 화면 이동형(opt.day)은 맨 뒤
    assert iss["fixes"][-1]["target"] == "opt.day"


def test_coil_supply_issue():
    sc, cfg = base()
    cfg["supply_temp_c"] = 16.0
    issues = evaluate_issues(sc, cfg, 10, [])
    coil = next(i for i in issues if i["code"] == "coil_supply")
    chws = next(f for f in coil["fixes"] if f["target"] == "layout.chws")
    assert chws["value"] == 16.0 - cfg["coil_approach_k"]


def test_placement_explains_high_inlet():
    sc, cfg = base()
    r = placement.place(sc, cfg, [{"count": 4, "design_kw": 30, "liquid_fraction": 0.0}], max_moves=10)
    assert r["after"]["max_inlet_c"] > cfg["inlet_limit_c"]
    assert r["issue"]["level"] == "crit" and "타일" in r["issue"]["cause"]
    hot = max(r["items"], key=lambda it: it["inlet_c"])
    a = r["air"][hot["id"]]
    assert a["supplied_m3_s"] < a["required_m3_s"]          # 급기 부족 → 재순환
    assert any(f["target"] == "layout.tile" and f["solves"] for f in r["issue"]["fixes"])
