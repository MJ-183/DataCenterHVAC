"""Phase 19 실측 비교 테스트 — G14 공식·부호, 매핑 PUE 재계산, 합성 데이터 보정 회복, 보정 단계, API, AI 진단(가짜 LLM)"""
import base64
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import ai
from api.main import app
from api.real_compare import Cause, Diagnosis
from hvac import real_compare as rc, realdata as rd
from hvac.engine import simulate_days
from hvac.schemas import Scenario

client = TestClient(app)
FIX = Path(__file__).parent / "fixtures" / "esif_7d.csv"     # ESIF 실측 2017-01-01~07, 2019-01-01~07 (시간 평균)
CAL = (date(2017, 1, 1), date(2017, 1, 7))
TEST = (date(2019, 1, 1), date(2019, 1, 7))
PERIODS = {"cal": [d.isoformat() for d in CAL], "test": [d.isoformat() for d in TEST]}


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


@pytest.fixture
def esif():
    pr = rd.preset("esif")
    sc, _ = rd.preset_scenario(pr)
    return pr, Scenario.model_validate(sc).to_engine_dict(), rd.load_file(FIX, "timestamp_mst"), rd.preset_weather(pr)


# ---------- G14 ----------
def test_g14_hand_calc_and_sign():
    meas, sim = np.array([10.0, 12, 8, 10]), np.array([11.0, 12, 10, 9])
    # e = 시뮬 - 실측 = [1, 0, 2, -1], Σe = 2, n-1 = 3, 평균 10 → NMBE = 2/30 = 6.67 %, CV = √(6/3)/10 = 14.14 %
    assert rc.g14(meas, sim) == (6.7, 14.1)
    assert rc.g14(sim, meas)[0] == pytest.approx(-2 / (3 * 10.5) * 100, abs=0.05)   # 시뮬 과소 → 음수
    assert rc.g14_pass(6.7, 14.1) is True and rc.g14_pass(-10.1, 5) is False and rc.g14_pass(1, 30.1) is False


# ---------- 데이터 ----------
def test_parse_and_pue_recalc_matches_raw(esif):
    pr, _, ds, _ = esif
    assert ds["timestamp"] == "timestamp_mst" and ds["start"] == rd._hour_index(rd.datetime(2017, 1, 1))
    chk = rd.pue_check(ds, pr["mapping"], *CAL)
    assert chk["ok"] and chk["hours"] > 150 and chk["median_abs_diff"] < 0.001
    bad = {**pr["mapping"], "pue_terms": ["it", "cooling_kw"]}             # 항목을 빼면 경고
    assert rd.pue_check(ds, bad, *CAL)["warning"]


def test_inspect_suggests_esif_mapping():
    ds = rd.load_file(FIX)
    info = rd.inspect(ds)
    m = info["mapping"]
    assert m["timestamp"] == "timestamp_mst" and m["it"] == "it_power_kw" and m["pue_column"] == "pue"
    assert m["items"] == {"cooling_kw": ["tower_fan"], "hvac_kw": ["crah_fan"], "pump_kw": ["chw_pump", "cw_pump", "liquid_pump"]}
    assert m["lighting"] == "plug_and_light_kw"
    assert not any(c.startswith("n_") for c in m["items"])                 # 개수 열은 후보에서 제외
    assert info["range"][0].startswith("2017-01-01")


def test_preset_scenario_marks_provenance(esif):
    pr, sc, *_ = esif
    prov = sc["provenance"]
    assert prov["cooling_plant.liquid_fraction"]["source"] == "published"
    assert prov["ventilation.outdoor_air_m3_h"]["source"] == "assumed"
    assert sc["site"]["elevation_m"] == 1829.0
    sc2, table = rd.preset_scenario(pr, {"ventilation.outdoor_air_m3_h": 2000.0})
    assert sc2["ventilation"]["outdoor_air_m3_h"] == 2000.0 and sc2["provenance"]["ventilation.outdoor_air_m3_h"]["source"] == "user"
    assert next(r for r in table if r["path"] == "ventilation.outdoor_air_m3_h")["basis"] == "user"


# ---------- 합성 데이터: 보정이 배율을 회복하는지 ----------
MULT = {"tower_fan": 1.5, "pump": 0.4, "crah_fan": 3.0}


def synthetic(esif):
    """엔진 출력에 알려진 배율을 곱한 '실측' (냉각탑 1.5, 펌프 0.4, 팬 3.0)"""
    pr, sc, ds, w = esif
    scs, _ = rc._day_scenarios(sc, ds, pr["mapping"], *CAL, w, None)
    hours = [h for r in simulate_days(scs) for h in r["hourly"]]
    pk = lambda k: np.array([h["plant"]["power_kw"][k] for h in hours])
    cols = {"it_power_kw": np.array([h["load_kw"]["it"] for h in hours]),
            "cooling_kw": MULT["tower_fan"] * pk("tower_fan"),
            "pump_kw": MULT["pump"] * (pk("chw_pump") + pk("cw_pump") + pk("liquid_pump")),
            "hvac_kw": MULT["crah_fan"] * pk("crah_fan"),
            "plug_and_light_kw": np.array([h["load_kw"]["lighting"] for h in hours])}
    h0, n = rd.period_hours(*CAL)
    return {"name": "synthetic", "timestamp": "t", "start": h0, "hours": n, "columns": cols, "hash": "syn",
            "rows": n, "bad_rows": 0}


def test_calibration_recovers_known_multipliers(esif):
    pr, sc, _, w = esif
    mapping = {**pr["mapping"], "pue_column": None}
    res = rc.run(sc, synthetic(esif), mapping, {"cal": PERIODS["cal"]}, w, pr["facility"], sensitivity_hours=[])
    r = res["calibration"]["ratios"]
    assert r["cooling_kw"] == pytest.approx(1.5, rel=0.01)
    assert r["pump_kw"] == pytest.approx(0.4, rel=0.01)
    assert r["hvac_kw"] == pytest.approx(3.0, rel=0.01)
    co = res["calibration"]["coefficients"]
    assert co["tower_fan_kw_per_kw"] == pytest.approx(0.006 * 1.5, rel=0.01)
    assert co["liquid_pump_kw_per_kw"] == pytest.approx(0.02 * 0.4, rel=0.01)
    assert co["crah_fan_heat_ratio"] == pytest.approx(0.05 * 3.0, rel=0.01)
    B = res["runs"]["B"]["items"]
    assert B["hvac_kw"]["sim_mean_kw"] == pytest.approx(B["hvac_kw"]["measured_mean_kw"], rel=0.01)
    # 팬 발열비를 올리면 방열량이 늘어 냉각탑 팬·냉각수 펌프가 조금 과대 (계수 결합 잔차) — 크기는 작고 부호는 +
    for c in ("cooling_kw", "pump_kw"):
        assert 0 <= B[c]["sim_mean_kw"] - B[c]["measured_mean_kw"] <= 0.05 * B[c]["measured_mean_kw"]
    assert "C" not in res["runs"] and [s["step"] for s in res["calibration"]["steps"]] == [0, 1, 2, 3, 4]


def test_calibration_steps_match_metrics(esif):
    pr, sc, ds, w = esif
    res = rc.run(sc, ds, pr["mapping"], PERIODS, w, pr["facility"], sensitivity_hours=[])
    A, B, C = (res["runs"][k] for k in "ABC")
    st = {s["step"]: s for s in res["calibration"]["steps"]}
    assert sorted(st) == [0, 1, 2, 3, 4, 5]
    for row in st[1]["rows"]:
        if row["item"] in A["items"]:
            assert row["measured_mean_kw"] == A["items"][row["item"]]["measured_mean_kw"]
            assert row["sim_A_mean_kw"] == A["items"][row["item"]]["sim_mean_kw"]
    for row in st[2]["rows"]:
        assert row["B"] == B["coefficients"][row["coefficient"]]
        assert row["B"] == pytest.approx(row["A"] * row["ratio"], abs=1e-5)   # B 는 반올림 전 r, 표의 r 은 4자리
    for row in st[3]["rows"]:
        assert row["sim_B_mean_kw"] == B["items"][row["item"]]["sim_mean_kw"]
        assert row["nmbe_pct"] == B["items"][row["item"]]["nmbe_pct"]
    assert st[4]["pue"] == {"measured": A["pue"]["measured"], "A": A["pue"]["sim"], "B": B["pue"]["sim"]}
    assert f"{C['items']['mech_total']['nmbe_pct']:+.1f}%" in st[5]["text"]
    assert rc.steps_markdown(res["calibration"]["steps"]).startswith("# 시뮬 A → 시뮬 B 보정 단계")
    s = res["structure"]["mech_total"]
    assert set(s) >= {"coef", "fixed_share_of_mean_cal", "engine_calibrated_test", "affine_test"}


def test_sensitivity_altitude_reduces_chiller_hours(esif):
    _, sc, _, _ = esif
    hours = rd.epw_hours(rd.REPO / "validation_esif" / "golden_tmy3.epw")
    s = rc.sensitivity_chiller_hours(hours, sc, "EPW")
    h = {r["label"]: r["hours"] for r in s["rows"]}
    assert s["site_pressure_kpa"] == pytest.approx(81.2, abs=0.05)
    assert h["고도 1,829 m 습구로 보정"] < s["rows"][0]["hours"]
    no_elev = {**sc, "site": {k: v for k, v in sc["site"].items() if k != "elevation_m"}}
    assert all(r["wet_bulb"] == "sea" for r in rc.sensitivity_chiller_hours(hours[:100], no_elev, "EPW")["rows"])


# ---------- API ----------
def _upload():
    b64 = base64.b64encode(FIX.read_bytes()).decode()
    r = client.post("/real/inspect", json={"file_name": FIX.name, "data_b64": b64})
    assert r.status_code == 200, r.text
    return r.json()


def _compare():
    ds = _upload()
    r = client.post("/real/compare", json={"preset": "esif", "dataset_id": ds["dataset_id"], "periods": PERIODS})
    assert r.status_code == 200, r.text
    return r.json()


def test_api_esif_preset_smoke():
    assert any(p["id"] == "esif" for p in client.get("/real/presets").json())
    c = _compare()
    assert set(c["runs"]) == {"A", "B", "C"} and c["cached"] is False
    assert c["pue_check"]["ok"] and c["weather"]["days"]["A"] == {"tmy3": 7}
    assert c["runs"]["C"]["period"] == PERIODS["test"] and len(c["monthly"]["C"]) == 1 and len(c["daily"]["A"]) == 7
    assert c["sign_note"].startswith("오차 = 시뮬 − 실측")
    assert any(r["basis"] == "assumed" for r in c["inputs"]) and any(r["basis"] == "fact" for r in c["inputs"])
    again = client.post("/real/compare", json={"preset": "esif", "dataset_id": c["dataset"]["id"], "periods": PERIODS}).json()
    assert again["cached"] is True and again["compare_id"] == c["compare_id"]


def test_api_compare_errors():
    ds = _upload()
    bad = {"preset": "esif", "dataset_id": ds["dataset_id"], "periods": {"cal": ["2030-01-01", "2030-01-07"]}}
    assert client.post("/real/compare", json=bad).status_code == 422
    m = {**rd.preset("esif")["mapping"], "items": {"no_such_col": ["tower_fan"]}}
    r = client.post("/real/compare", json={"preset": "esif", "dataset_id": ds["dataset_id"], "periods": PERIODS, "mapping": m})
    assert r.status_code == 422 and "no_such_col" in r.text
    assert client.post("/real/compare", json={"dataset_id": ds["dataset_id"], "periods": PERIODS}).status_code == 422


def test_apply_b_coefficients_and_revert():
    c = _compare()
    sc = client.get("/scenario/sample").json()
    r = client.post("/real/apply", json={"scenario": sc, "compare_id": c["compare_id"]})
    assert r.status_code == 200, r.text
    new = r.json()
    for k, v in c["calibration"]["coefficients"].items():
        assert new["cooling_plant"][k] == v
        assert new["provenance"][f"cooling_plant.{k}"]["source"] == "real_calibrated"
        assert new["provenance"][f"cooling_plant.{k}"]["ref"] == "real:esif"
    assert new["internal"] == sc["internal"]                                     # 계수 외 입력은 그대로
    back = client.post("/scenario/revert", json={"scenario": new, "paths": list(new["provenance"])}).json()
    assert back["cooling_plant"] == sc["cooling_plant"] and "provenance" not in back


class FakeLLM:
    model = "fake-model"

    def __init__(self, out):
        self.out, self.calls = out, []

    def text(self, system, user):
        raise AssertionError("text 는 쓰지 않음")

    def parse(self, system, user, schema):
        self.calls.append((system, user))
        return self.out


def test_diagnose_flags_unverified_numbers():
    c = _compare()
    pump = c["runs"]["A"]["items"]["pump_kw"]["sim_mean_kw"]
    d = Diagnosis(summary=f"펌프 평균 {pump} kW 로 과대.", not_explained="없음", causes=[
        Cause(rank=2, title="가설", affected=["공조 팬"], direction="과소", evidence="팬 98765.4 kW", basis="hypothesis",
              how_to_verify="계측", model_fix="수식 변경"),
        Cause(rank=1, title="펌프 계수", affected=["펌프"], direction="과대", evidence=f"A {pump} kW", basis="data",
              how_to_verify="비교", model_fix="계수")])
    llm = FakeLLM(d)
    app.dependency_overrides[ai.get_llm] = lambda: llm
    try:
        r = client.post("/real/diagnose", json={"compare_id": c["compare_id"]})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["unverified_numbers"] == ["98765.4"] and out["model"] == "fake-model"
    assert [x["rank"] for x in out["causes"]] == [1, 2]
    system, user = llm.calls[0]
    assert "시뮬 − 실측" in system and "monthly_A" in user and "offset_h" not in user   # 시간값 원자료는 보내지 않음
    app.dependency_overrides[ai.get_llm] = lambda: FakeLLM(None)
    try:
        assert client.post("/real/diagnose", json={"compare_id": c["compare_id"]}).json()["cached"] is True
    finally:
        app.dependency_overrides.clear()


def test_diagnose_without_key_is_503_and_unknown_id_404():
    c = _compare()
    assert client.post("/real/diagnose", json={"compare_id": c["compare_id"]}).status_code == 503
    app.dependency_overrides[ai.get_llm] = lambda: FakeLLM(None)
    try:
        assert client.post("/real/diagnose", json={"compare_id": "0" * 16}).status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---------- 전체 ESIF 대조 (원본 데이터가 있을 때만) ----------
@pytest.mark.skipif(not (rd.REPO / "data_esif" / "esif_hourly_mst.csv").exists(), reason="ESIF 전체 데이터 없음")
def test_full_esif_matches_prototype():
    c = client.post("/real/compare", json={"preset": "esif"}).json()
    A, B, C = (c["runs"][k] for k in "ABC")
    assert (A["pue"]["sim"], B["pue"]["sim"], C["pue"]["measured"], C["pue"]["sim"]) == (1.0447, 1.0345, 1.0297, 1.0338)
    assert A["items"]["pump_kw"]["sim_mean_kw"] == 28.17 and A["chiller_hours"]["sim"] == 521
    assert B["items"]["mech_total"]["cv_rmse_pct"] == 23.0
    assert B["items"]["mech_total"]["nmbe_pct"] == 3.2 and C["items"]["mech_total"]["nmbe_pct"] == 15.9   # 프로토타입 −3.2 / −15.9 의 부호 변환
    assert B["coefficients"] == {"tower_fan_kw_per_kw": 0.00998, "chw_pump_kw_per_kw": 0.00495, "cw_pump_kw_per_kw": 0.00396,
                                 "liquid_pump_kw_per_kw": 0.0066, "crah_fan_heat_ratio": 0.20949}
