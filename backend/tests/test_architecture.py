"""Phase 3.5 냉각 방식(air_chw / hybrid_dlc / immersion)·프리쿨링·PUE/WUE 테스트: backend 폴더에서  python -m pytest"""
import copy
import json
import math

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR
from hvac.airflow import analyze
from hvac.engine import PLANT_DEFAULTS, compare_architectures, free_fraction, simulate, wet_bulb
from hvac.plant import evaluate
from hvac.schemas import ArchitectureComparison, CoolingLayout, SimulationResult

client = TestClient(app)


def base(**plant):
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        sc = json.load(f)
    sc["cooling_plant"].update(plant)
    return sc


def winter(sc):
    """외기 28℃ 하강, RH 60% -> 습구 약 0℃ 이하 (전 시간 프리쿨링 가능)"""
    sc["weather"]["dry_bulb_c"] = [t - 28 for t in sc["weather"]["dry_bulb_c"]]
    sc["weather"]["rh_pct"] = [60] * 24
    return sc


# ---------- 호환성 ----------
def test_scenario_without_new_fields_uses_defaults():
    old = base()
    for k in PLANT_DEFAULTS:
        old["cooling_plant"].pop(k)
    assert simulate(old) == simulate(base())


def test_output_schema_covers_all_architectures():
    for a in ("air_chw", "hybrid_dlc", "immersion"):
        raw = simulate(base(architecture=a), 30)
        assert SimulationResult.model_validate(raw).model_dump(exclude_unset=True) == raw   # 현실성 필드는 켰을 때만
        assert raw["architecture"] == a
        on = simulate(base(architecture=a, realism={"control": True}), 30)
        assert SimulationResult.model_validate(on).model_dump(exclude_unset=True) == on


# ---------- 열·전력 수지 ----------
@pytest.mark.parametrize("arch", ["air_chw", "hybrid_dlc", "immersion"])
def test_energy_and_power_balance(arch):
    for x in simulate(base(architecture=arch))["hourly"]:
        L, P = x["load_kw"], x["plant"]
        # 냉각탑 방열 = 전체 냉방량 + 칠러 전력
        assert x["condenser_water"]["heat_rejection_kw"] == pytest.approx(L["total_cooling"] + x["chiller"]["power_kw"], abs=0.15)
        # 냉방량 = 공기 루프 + 액체 루프
        liq = P["liquid_loop"]["load_kw"] if P["liquid_loop"] else 0.0
        assert L["total_cooling"] == pytest.approx(P["air_loop"]["load_kw"] + liq, abs=0.15)
        pw = P["power_kw"]
        parts = pw["chiller"] + pw["crah_fan"] + pw["chw_pump"] + pw["cw_pump"] + pw["liquid_pump"] + pw["tower_fan"]
        assert pw["cooling_total"] == pytest.approx(parts, abs=0.2)
        assert pw["facility"] == pytest.approx(L["it"] + L["ups_loss"] + L["lighting"] + pw["cooling_total"], abs=0.2)
        assert P["pue"] == pytest.approx(pw["facility"] / L["it"], abs=1e-3)
        assert P["wue_l_per_kwh"] == pytest.approx(x["makeup_water_m3_h"]["total"] * 1000 / L["it"], abs=2e-3)


def test_daily_pue_wue_hand_calc():
    r = simulate(base())
    h, d = r["hourly"], r["daily_total"]
    it = sum(x["load_kw"]["it"] for x in h)
    fac = sum(x["plant"]["power_kw"]["facility"] for x in h)
    assert d["pue"] == pytest.approx(fac / it, abs=1e-3)
    assert d["wue_l_per_kwh"] == pytest.approx(sum(x["makeup_water_m3_h"]["total"] for x in h) * 1000 / it, abs=1e-3)
    assert d["pue"] > 1


def test_hybrid_dlc_heat_split_hand_calc():
    a = simulate(base())["hourly"][14]
    b = simulate(base(architecture="hybrid_dlc"))["hourly"][14]
    lq = b["plant"]["liquid_loop"]
    assert lq["load_kw"] == pytest.approx(0.75 * b["load_kw"]["it"], abs=0.1)
    # FWS 공급 = TCS 공급 - CDU 접근온도 = 32 - 3
    assert (lq["fws_supply_c"], lq["fws_return_c"]) == (29.0, 39.0)
    # FWS 유량 = 액체 부하 / (cp x 10K)
    assert lq["fws_flow_m3_h"] == pytest.approx(lq["load_kw"] / (4.186 * 10) * 3.6, abs=0.2)
    # CRAH 팬 발열은 공기측 부하에만 비례 -> 줄어듦, 냉수 유량도 줄어듦
    assert b["load_kw"]["crah_fan"] < a["load_kw"]["crah_fan"]
    assert b["chilled_water"]["flow_m3_h"] < a["chilled_water"]["flow_m3_h"]
    # 보조 칠러 COP 는 높은 공급수온만큼 보정 (1 + 0.03 x (29 - 12)) 배, 상한 1.6
    assert lq["chiller_cop"] == pytest.approx(min(a["chiller"]["cop"] * (1 + 0.03 * 17), 6.0 * 1.3 * 1.6), rel=1e-2)


def test_immersion_captures_more_than_dlc():
    b = simulate(base(architecture="hybrid_dlc"))["hourly"][14]
    c = simulate(base(architecture="immersion"))["hourly"][14]
    assert c["plant"]["liquid_loop"]["load_kw"] == pytest.approx(0.95 * c["load_kw"]["it"], abs=0.1)
    assert c["plant"]["air_loop"]["load_kw"] < b["plant"]["air_loop"]["load_kw"]


def test_pue_ordering_under_default_assumptions():
    """기본 가정값·샘플 여름날 기준 (계수를 바꾸면 순서가 달라질 수 있음)"""
    c = {a["architecture"]: a["daily_total"] for a in compare_architectures(base())["architectures"]}
    assert c["immersion"]["pue"] < c["hybrid_dlc"]["pue"] < c["air_chw"]["pue"]
    assert c["immersion"]["chiller_kwh"] < c["hybrid_dlc"]["chiller_kwh"] < c["air_chw"]["chiller_kwh"]


# ---------- 프리쿨링 ----------
def test_free_fraction_function():
    assert free_fraction(10, 12, 18) == 1.0
    assert free_fraction(12, 12, 18) == 1.0
    assert free_fraction(15, 12, 18) == 0.5
    assert free_fraction(18, 12, 18) == 0.0
    assert free_fraction(25, 12, 18) == 0.0


def test_full_free_cooling_in_winter():
    r = simulate(winter(base()))
    for x in r["hourly"]:
        assert x["plant"]["mode"] == "free" and x["chiller"]["power_kw"] == 0
        # 칠러가 꺼져도 냉각탑은 전체 열을 방출
        assert x["condenser_water"]["heat_rejection_kw"] == pytest.approx(x["load_kw"]["total_cooling"], abs=0.1)
        assert x["plant"]["free_cooling_water_c"] == pytest.approx(x["weather"]["wet_bulb_c"] + 4.0 + 1.5, abs=0.02)
    assert r["daily_total"]["free_cooling_hours"] == 24 and r["daily_total"]["chiller_kwh"] == 0


def test_partial_free_cooling_hand_calc():
    """냉각탑 접근온도를 조정해 03시 프리쿨링 수온 = 15℃ (냉수 12/18 사이) -> 비율 0.5"""
    sc = base()
    twb = wet_bulb(sc["weather"]["dry_bulb_c"][3], sc["weather"]["rh_pct"][3])
    sc["cooling_plant"]["tower_approach_k"] = 15 - 1.5 - twb
    x = simulate(sc)["hourly"][3]
    assert x["plant"]["mode"] == "partial" and x["plant"]["air_loop"]["free_fraction"] == pytest.approx(0.5, abs=1e-3)
    assert x["chiller"]["power_kw"] == pytest.approx(x["load_kw"]["total_cooling"] * 0.5 / x["chiller"]["cop"], abs=0.2)


def test_free_cooling_disabled():
    sc = winter(base(free_cooling=False))
    r = simulate(sc)
    assert all(x["plant"]["mode"] == "chiller" for x in r["hourly"])
    assert r["daily_total"]["free_cooling_hours"] == 0 and r["daily_total"]["chiller_kwh"] > 0


def test_summer_sample_hybrid_is_partial():
    """여름 샘플: 프리쿨링 수온(습구+5.5) 은 냉수 환수 18℃ 보다 높아 공기 루프는 칠러,
    FWS 29/39℃ 에는 충분히 낮아 액체 루프는 프리쿨링(야간 완전, 주간 부분) -> 전체 모드는 부분 프리쿨링"""
    hours = simulate(base(architecture="hybrid_dlc"))["hourly"]
    for x in hours:
        P = x["plant"]
        assert P["air_loop"]["mode"] == "chiller" and P["liquid_loop"]["mode"] in ("free", "partial")
        assert P["mode"] == "partial"
        expect = "free" if P["free_cooling_water_c"] <= 29.0 else "partial"
        assert P["liquid_loop"]["mode"] == expect
    assert {x["plant"]["liquid_loop"]["mode"] for x in hours} == {"free", "partial"}


@pytest.mark.parametrize("step", [60, 15])
def test_mode_hours_sum_to_day(step):
    r = simulate(winter(base(architecture="hybrid_dlc")), step)
    d = r["daily_total"]
    chiller_h = sum(x["plant"]["mode"] == "chiller" for x in r["hourly"]) * step / 60
    assert d["free_cooling_hours"] + d["partial_free_cooling_hours"] + chiller_h == pytest.approx(24)


# ---------- 방식 비교 API ----------
def test_compare_endpoint_same_weather_and_it():
    r = client.post("/simulate/compare", json=base())
    assert r.status_code == 200
    body = r.json()
    assert ArchitectureComparison.model_validate(body).model_dump() == body
    assert body["current"] == "air_chw"
    runs = {a["architecture"]: a for a in body["architectures"]}
    assert set(runs) == {"air_chw", "hybrid_dlc", "immersion"}
    assert len({a["daily_total"]["it_kwh"] for a in runs.values()}) == 1      # 같은 IT 부하
    assert all(len(a["hourly"]) == 24 for a in runs.values())
    assert client.post("/simulate/compare?step_min=7", json=base()).status_code == 422


@pytest.mark.parametrize("plant", [
    {"architecture": "water_side"},
    {"tcs_return_c": 30.0},             # TCS ΔT < 0
    {"liquid_fraction": 1.5},
    {"cdu_approach_k": -1},
])
def test_invalid_plant_rejected(plant):
    assert client.post("/simulate", json=base(**plant)).status_code == 422


# ---------- 냉각 구조(기류·배관) 모델과의 연동 ----------
def test_airflow_model_uses_air_fraction():
    cfg = CoolingLayout().model_dump()
    a = analyze(base(architecture="hybrid_dlc"), cfg)
    assert a["air_fraction"] == 0.25
    assert a["metrics"]["crah_heat_kw"] == pytest.approx(0.25 * a["it_kw"], rel=2e-3)
    full = analyze(base(), cfg)
    assert a["metrics"]["max_inlet_c"] <= full["metrics"]["max_inlet_c"]


def test_cooling_evaluate_includes_liquid_loop_power():
    cfg = CoolingLayout().model_dump()
    air = evaluate(base(), cfg)["summary"]["daily_kwh"]
    hyb = evaluate(base(architecture="hybrid_dlc"), cfg)["summary"]["daily_kwh"]
    assert air["liquid"] == 0 and hyb["liquid"] > 0
    assert hyb["chiller"] < air["chiller"]


def test_cooling_evaluate_free_cooling_in_winter():
    e = evaluate(winter(base()), CoolingLayout().model_dump())
    assert all(h["mode"] == "free" and h["chiller_kw"] == 0 for h in e["hourly"])
    assert math.isclose(e["summary"]["daily_kwh"]["chiller"], 0)
