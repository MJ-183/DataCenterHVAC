"""Phase 14 운전 브리핑: 단기예보 정렬·권장 운전점 제약·IT 데이터셋 반영·예보 실패 대체·다음 달·적용/되돌리기"""
import json
import math
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from api import outlook as outlook_api
from api.main import app
from hvac import DATA_DIR, datasets, itdata, secrets
from hvac import weather as wx
from tests.test_annual import synthetic_year

client = TestClient(app)
TODAY = date(2026, 7, 14)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def vilage_payload(start, days, base=datetime(2026, 7, 14, 5)):
    """단기예보 모의 응답: 정시마다 TMP·REH·SKY·PTY·POP·PCP (오후 15~17시 소나기)"""
    items = []
    for k in range(24 * days + 1):
        t = datetime(start.year, start.month, start.day) + timedelta(hours=k)
        rain = 15 <= t.hour <= 17
        for cat, v in (("TMP", round(26 + 5 * math.sin((t.hour - 9) / 24 * 2 * math.pi), 1)), ("REH", 70),
                       ("SKY", 4 if rain else 1), ("PTY", 4 if rain else 0), ("POP", 60 if rain else 10),
                       ("PCP", "1.0mm" if rain else "강수없음")):
            items.append({"baseDate": base.strftime("%Y%m%d"), "baseTime": base.strftime("%H%M"), "category": cat,
                          "fcstDate": t.strftime("%Y%m%d"), "fcstTime": t.strftime("%H00"), "fcstValue": str(v),
                          "nx": 60, "ny": 127})
    return items


@pytest.fixture(autouse=True)
def fake_forecast(monkeypatch):
    outlook_api.WX_CACHE.clear()
    secrets.put("KMA_SERVICE_KEY", "k")
    items = vilage_payload(TODAY + timedelta(days=1), 2)
    seen = []

    def get(url):
        seen.append(url)
        q = parse_qs(urlparse(url).query)
        assert "VilageFcstInfoService_2.0/getVilageFcst" in url and (q["nx"][0], q["ny"][0]) == ("60", "127")
        page, rows = int(q["pageNo"][0]), int(q["numOfRows"][0])
        return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {
            "items": {"item": items[(page - 1) * rows: page * rows]}, "totalCount": len(items)}}}).encode()
    monkeypatch.setattr(wx, "_http_get", get)
    return seen


def test_briefing_and_recommendation(fake_forecast):
    r = client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat(), "capacity_kw": 2500})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["date"] == "2026-07-15" and b["weekday"] == "수" and len(b["hourly"]) == 24
    rec = b["recommended"]
    assert rec and rec["inlet_margin_c"] >= 0 and rec["daily_kwh"] <= b["current"]["daily_kwh"]
    assert b["saving_kwh"] >= 0 and any("권장 운전점" in x for x in b["bullets"])
    assert any("IT 데이터셋이 없어" in n for n in b["notes"])
    client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat()})
    assert len(fake_forecast) == 1                      # 같은 격자·날짜는 캐시 (798 < 1000 이라 1페이지)
    assert "기상청 단기예보" in b["title"] and b["precip"]["pty"][16] == "소나기" and b["precip"]["pop_pct"][16] == 60
    w = b["weather"]["ghi_w_m2"]
    assert w[0] == 0 and w[12] > w[16] > 0                # 소나기 시간은 흐림 → 일사 감소


def test_no_key_message():
    secrets.put("KMA_SERVICE_KEY", "")
    r = client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat()})
    assert r.status_code == 503 and "데이터 입력" in r.json()["detail"]


def test_next_month_from_region_observations(tmp_path, monkeypatch):
    """다음 달 브리핑: 지역 관측소의 지난 연도 같은 달 관측으로 한 달 계산 (예보 아님)"""
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    sc = sample()
    sc["site"].update(asos_station_id=108, weather_year=2025)
    wx.save_annual(synthetic_year(), tmp_path / "annual_kma_108_2025_auto.json")
    r = client.post("/outlook", json={"scenario": sc, "period": "month", "today": TODAY.isoformat()})
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["period"], b["year"], b["month"], b["observed_year"], len(b["daily"])) == ("month", 2026, 8, 2025, 31)
    assert b["daily"][0]["obs_date"] == "2025-08-01" and "2025년 8월" in b["weather_note"]
    rec = b["recommended"]
    assert rec and rec["monthly_kwh"] <= b["current"]["monthly_kwh"] and rec["inlet_margin_c"] >= 0
    assert sum(d["recommended_kwh"] for d in b["daily"]) == pytest.approx(rec["monthly_kwh"], abs=31)
    dec = client.post("/outlook", json={"scenario": sc, "period": "month", "today": "2026-12-05"}).json()
    assert (dec["year"], dec["month"]) == (2027, 1)


def test_capacity_alert():
    b = client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat(), "capacity_kw": 1500}).json()
    assert any(a["level"] == "crit" and "용량" in a["text"] for a in b["alerts"])


def test_dataset_used(tmp_path, monkeypatch):
    monkeypatch.setattr(datasets, "ROOT", tmp_path)
    recs = [(TODAY - timedelta(days=k), float(h), "R", 1500.0) for k in range(1, 29) for h in range(24)]
    ds = datasets.save("it", itdata.dataset(recs, 2000), "x")
    sc = sample()
    sc["it_load"]["dataset_id"] = ds["meta"]["id"]
    b = client.post("/outlook", json={"scenario": sc, "today": TODAY.isoformat()}).json()
    assert b["hourly"][3]["it_kw"] == pytest.approx(1500, rel=0.01) and "같은 요일" in b["notes"][0]


def test_fallback_to_annual_file(tmp_path, monkeypatch):
    def fail(url):
        raise wx.WeatherError("기상 API 호출 실패: offline")
    monkeypatch.setattr(wx, "_http_get", fail)
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    assert client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat()}).status_code == 502
    wx.save_annual(synthetic_year(), tmp_path / "annual_kma_108_2025_auto.json")
    b = client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat()}).json()
    assert b["weather_source"] == "annual_fallback" and "대신 계산" in b["weather_note"]


def _apply_rec(sc, month=None):
    b = client.post("/outlook", json={"scenario": sc, "today": TODAY.isoformat()}).json()
    sp = {k: b["recommended"][k] for k in ("supply_temp_c", "chw_supply_c", "chw_return_c", "airflow_ratio")}
    assert b["recommended"]["inlet_margin_c"] >= outlook_api.outlook.MARGIN_WARN_C
    return client.post("/outlook/apply", json={"scenario": sc, "setpoint": sp, "month": month}).json(), sp


@pytest.mark.parametrize("ratio", [1.0, 0.5])   # 0.5: 적용 전 흡입 상한 초과 상태
def test_recommendation_leaves_no_warnings(ratio):
    """권장 운전점을 적용하면 브리핑과 '현황' 냉각 구조 평가 모두 주의·경고가 없어야 한다"""
    sc = sample()
    lay = client.get("/cooling/layout/default").json()
    lay["crah"]["airflow_ratio"] = ratio
    sc["cooling_layout"] = lay
    applied, _ = _apply_rec(sc)
    b = client.post("/outlook", json={"scenario": applied, "today": TODAY.isoformat()}).json()
    assert not [a for a in b["alerts"] if a["level"] != "info"] and b["issues"] == []
    ev = client.post("/cooling/evaluate", json=applied).json()
    assert ev["issues"] == [] and ev["summary"]["feasible"]


def test_apply_updates_month_schedule():
    sc = sample()
    lay = client.get("/cooling/layout/default").json()
    lay["operating_schedule"] = [{"month": m, "supply_temp_c": 18, "chw_supply_c": 12, "chw_return_c": 18, "airflow_ratio": 0.5}
                                 for m in (7, 8)]
    sc["cooling_layout"] = lay
    applied, sp = _apply_rec(sc, month=7)
    jul, aug = applied["cooling_layout"]["operating_schedule"]
    assert jul == {"month": 7, **sp} and aug["airflow_ratio"] == 0.5
    b = client.post("/outlook", json={"scenario": applied, "today": TODAY.isoformat()}).json()
    assert b["current_label"] == "7월 스케줄" and b["issues"] == []


def test_apply_and_revert():
    b = client.post("/outlook", json={"scenario": sample(), "today": TODAY.isoformat()}).json()
    sp = {k: b["recommended"][k] for k in ("supply_temp_c", "chw_supply_c", "chw_return_c", "airflow_ratio")}
    sc = client.post("/outlook/apply", json={"scenario": sample(), "setpoint": sp}).json()
    assert sc["cooling_layout"]["supply_temp_c"] == sp["supply_temp_c"]
    back = client.post("/scenario/revert", json={"scenario": sc, "paths": list(sc["provenance"])}).json()
    assert back == client.get("/scenario/sample").json()
