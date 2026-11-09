"""연간 시뮬레이션 테스트 (Phase 9, 네트워크 없이 모의 기상 사용)"""
import json
import math
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api import annual as annual_api
from api.main import app
from hvac import DATA_DIR, annual, simulate
from hvac import weather as wx

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def season_t(d: date, h: int) -> float:
    """1월 하순 최저(약 -2℃), 7월 하순 최고(약 30℃) + 일교차 (모의값)"""
    doy = d.timetuple().tm_yday
    return 14 - 12 * math.cos(2 * math.pi * (doy - 20) / 365) + 4 * math.sin(math.pi * (h - 9) / 12)


def synthetic_year(year=2025, n_days=None):
    days, d = [], date(year, 1, 1)
    base = sample()["weather"]
    while d.year == year and (n_days is None or len(days) < n_days):
        days.append({"date": d.isoformat(), "weather": {"dry_bulb_c": [round(season_t(d, h), 2) for h in range(24)],
                                                         "rh_pct": [65.0] * 24, "ghi_w_m2": base["ghi_w_m2"]}})
        d += timedelta(days=1)
    return {"meta": {"source": "test", "source_detail": "모의 기상", "latitude": 37.57, "longitude": 126.98,
                     "year": year, "n_days": len(days), "notes": []}, "days": days}


def test_open_meteo_year_fetch_save_load(tmp_path):
    def get(url):
        t0 = datetime(2025, 1, 1)
        times = [t0 + timedelta(hours=i) for i in range(24 * 366)]   # 12/31 다음날 0시까지 포함
        return json.dumps({"latitude": 37.55, "longitude": 127.0, "elevation": 38.0, "timezone": "Asia/Seoul",
                           "hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
                                      "temperature_2m": [season_t(t.date(), t.hour) for t in times],
                                      "relative_humidity_2m": [60.0] * len(times),
                                      "shortwave_radiation": [0.0] * len(times)}}).encode()
    ann = wx.fetch_open_meteo_year(37.57, 126.98, 2025, http_get=get, today=date(2026, 3, 1))
    assert ann["meta"]["year"] == 2025 and ann["meta"]["n_days"] == 365
    assert ann["days"][0]["date"] == "2025-01-01" and ann["days"][-1]["date"] == "2025-12-31"
    assert ann["meta"]["grid_latitude"] == 37.55
    p = wx.save_annual(ann, tmp_path)
    assert p.name == "annual_37.57_126.98_2025.json"
    assert wx.load_annual(p)["days"][100] == ann["days"][100]
    with pytest.raises(wx.WeatherError):   # 끝나지 않은 연도
        wx.fetch_open_meteo_year(37.57, 126.98, 2026, http_get=get, today=date(2026, 3, 1))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"meta": {}, "days": [{"date": "2025-01-01", "weather": {"dry_bulb_c": [1]}}]}))
    with pytest.raises(wx.WeatherError):
        wx.load_annual(bad)


def test_day_rows_match_engine_and_months_sum():
    sc, ann = sample(), synthetic_year(n_days=59)
    r = annual.run(sc, ann)
    a = next(x for x in r["architectures"] if x["architecture"] == "air_chw")
    # 하루 결과 = 그날 기상으로 바꾼 시나리오의 simulate() 결과
    day = ann["days"][40]
    s = sample()
    s["weather"], s["site"]["date"] = day["weather"], day["date"]
    d = simulate(s)["daily_total"]
    row = a["daily"][40]
    assert row["date"] == day["date"] and row["multiplier"] == 1.0
    for k, e in (("it_kwh", "it_kwh"), ("cooling_kwh", "cooling_kwh"), ("facility_kwh", "facility_kwh"),
                 ("cooling_power_kwh", "cooling_power_kwh"), ("makeup_m3", "makeup_m3"),
                 ("free_h", "free_cooling_hours")):
        assert row[k] == pytest.approx(d[e], abs=0.11)
    assert row["free_h"] + row["partial_h"] + row["chiller_h"] == pytest.approx(24)
    # 월 합계 = 일 합계의 합, 1·2월 두 달
    assert [m["month"] for m in a["monthly"]] == [1, 2]
    jan = [x for x in a["daily"] if x["month"] == 1]
    assert a["monthly"][0]["cooling_power_kwh"] == pytest.approx(sum(x["cooling_power_kwh"] for x in jan), abs=31)
    assert a["monthly"][0]["n_days"] == 31
    assert a["annual"]["facility_kwh"] == pytest.approx(sum(m["facility_kwh"] for m in a["monthly"]), abs=2)
    pk = a["monthly"][0]["peak_day"]
    assert pk["peak_cooling_kw"] == max(x["peak_cooling_kw"] for x in jan)
    assert pk["date"] in r["peak_days"]
    # 히트맵: 월 x 24시, 0~1
    hm = a["heatmap"]
    assert hm["months"] == [1, 2] and len(hm["free"]) == 2 and len(hm["free"][0]) == 24
    assert all(0 <= v <= 1 for row_ in hm["free"] + hm["partial"] for v in row_)


def test_seasons_architectures_and_growth():
    sc = sample()
    r = annual.run(sc, synthetic_year(), growth_pct_per_year=20)
    assert r["n_days"] == 365 and r["current"] == "air_chw"
    by = {a["architecture"]: a for a in r["architectures"]}
    air = by["air_chw"]
    jan, jul = air["monthly"][0], air["monthly"][6]
    assert jan["free_h"] > jul["free_h"] and jan["pue"] < jul["pue"]
    assert jul["chiller_kwh"] > jan["chiller_kwh"]
    # 액체 냉각일수록 칠러·설비 전력이 적다 (같은 기상·IT)
    fac = {a: by[a]["annual"]["facility_kwh"] for a in by}
    assert fac["immersion"] < fac["hybrid_dlc"] < fac["air_chw"]
    assert {c["architecture"] for c in r["comparison"]} == set(by)
    assert all(by[a]["annual"]["it_kwh"] == air["annual"]["it_kwh"] for a in by)
    # 연 20% 선형 증설: 첫날 1.0, 마지막 날 1 + 0.2 x 364/365
    d = air["daily"]
    assert d[0]["multiplier"] == 1.0 and d[-1]["multiplier"] == pytest.approx(1 + 0.2 * 364 / 365, abs=1e-4)
    assert d[-1]["it_kwh"] / d[0]["it_kwh"] == pytest.approx(d[-1]["multiplier"], rel=1e-3)
    assert any("20% 선형" in n for n in r["notes"]) and any("모의 기상" in n for n in r["notes"])


def test_input_not_mutated_and_performance():
    sc = sample()
    before = json.dumps(sc, sort_keys=True)
    t = time.perf_counter()
    annual.run(sc, synthetic_year())
    assert time.perf_counter() - t < 8.0   # 365일 x 3방식 (보통 1~2초)
    assert json.dumps(sc, sort_keys=True) == before


def test_api_run_files_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    annual_api.CACHE.clear()
    ann = synthetic_year(n_days=40)
    wx.save_annual(ann, tmp_path)
    files = client.get("/annual/weather-files").json()
    assert [f["file"] for f in files] == ["annual_37.57_126.98_2025.json"]

    body = {"scenario": sample(), "weather_file": "annual_37.57_126.98_2025.json", "growth_pct_per_year": 5}
    r1 = client.post("/annual/run", json=body)
    assert r1.status_code == 200, r1.text
    j = r1.json()
    assert j["cached"] is False and j["n_days"] == 40 and len(j["architectures"]) == 3
    assert client.post("/annual/run", json=body).json()["cached"] is True
    # 위경도·연도로 요청 → 같은 파일 재사용 (네트워크 호출 없음)
    monkeypatch.setattr(wx, "_http_get", lambda url: pytest.fail("저장된 파일이 있으면 다시 받지 않아야 함"))
    r2 = client.post("/annual/run", json={"scenario": sample(), "year": 2025, "source": "open_meteo"})
    assert r2.status_code == 200 and r2.json()["weather_file"] == "annual_37.57_126.98_2025.json"

    assert client.post("/annual/run", json={**body, "weather_file": "../scenario_sample.json"}).status_code == 422
    assert client.post("/annual/run", json={**body, "weather_file": "annual_1.00_2.00_2020.json"}).status_code == 404
    assert client.post("/annual/run", json={**body, "growth_pct_per_year": -95}).status_code == 422


def test_simulate_unchanged_by_annual_module():
    """연간 계산 후에도 /simulate 결과는 그대로 (회귀)"""
    before = client.post("/simulate", json=sample()).json()
    annual.run(sample(), synthetic_year(n_days=3))
    assert client.post("/simulate", json=sample()).json() == before
