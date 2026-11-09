"""Phase 16 투자 검토: 현금흐름 손계산, IRR, 패키지 상호작용, 예시값 안내, 작업 API"""
import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.platform import run_sync
from hvac import DATA_DIR, invest
from hvac import weather as wx
from hvac.schemas import CoolingLayout, Economics
from tests.test_annual import synthetic_year

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def test_cashflow_hand_calculation():
    eco = Economics(electricity_krw_per_kwh=100, water_krw_per_m3=0, discount_rate=0.1, years=3).model_dump()
    base = {1: {"kwh": 1000.0, "water_m3": 0.0}, 3: {"kwh": 1000.0, "water_m3": 0.0}}
    var = {1: {"kwh": 500.0, "water_m3": 0.0}, 3: {"kwh": 500.0, "water_m3": 0.0}}
    cf = invest.cashflow(base, var, capex=100_000, om=0, life=15, eco=eco)
    # 연 50,000원 x 3년, 할인율 10%: NPV = -100000 + 50000(1/1.1 + 1/1.21 + 1/1.331) = 24342.6
    assert cf["npv_krw"] == pytest.approx(24343, abs=1)
    assert cf["payback_years"] == pytest.approx(2.0)
    assert cf["irr"] == pytest.approx(0.2338, abs=1e-3)
    assert cf["cumulative_krw"] == [-100000, -50000, 0, 50000]


def test_irr_high_return_and_none():
    assert invest._irr([-1, 10]) == pytest.approx(9.0, abs=1e-3)
    assert invest._irr([-100, 1, 1]) is not None and invest._irr([-100, 1, 1]) < 0
    assert invest._irr([100, 1]) is None


def test_catalog_quantities():
    cat = {m["id"]: m for m in invest.catalog(sample(), CoolingLayout().model_dump())}
    assert cat["cold_aisle"]["quantity"] == 320 and cat["dlc"]["quantity"] == 2000
    assert not cat["free_cooling"]["applicable"] and cat["schedule"]["applicable"]


def test_run_package_and_demo_flag():
    ann = synthetic_year(n_days=31)
    eco = Economics(years=5, it_growth_pct_per_year=10).model_dump()
    r = invest.run(sample(), CoolingLayout().model_dump(), ann, eco,
                   [{"id": "schedule"}, {"id": "hot_aisle", "capex_krw": 1e8}], [["schedule", "hot_aisle"]])
    by = {x["key"]: x for x in r["results"]}
    assert by["schedule"]["demo_capex"] and not by["hot_aisle"]["demo_capex"]
    assert by["hot_aisle"]["capex_krw"] == 1e8 and by["schedule+hot_aisle"]["capex_krw"] == 1e8 + 30_000_000
    assert "sum_of_singles_kwh" in by["schedule+hot_aisle"]
    assert r["demo"] and "시연 계산" in r["notes"][0]
    npvs = [x["npv_krw"] for x in r["results"]]
    assert npvs == sorted(npvs, reverse=True) and r["summary"].startswith("가장 유리한 안")
    assert len(by["schedule"]["years"]) == 5


def test_api_catalog_and_job(tmp_path, monkeypatch):
    c = client.post("/invest/catalog", json={"scenario": sample()}).json()
    assert c["economics_is_default"] and "예시" in c["note"]
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    wx.save_annual(synthetic_year(n_days=31), tmp_path)
    res = run_sync("invest", {"scenario": sample(), "weather_file": "annual_37.57_126.98_2025.json",
                              "measures": [{"id": "schedule"}]})
    assert res["results"][0]["key"] == "schedule" and res["demo"]
