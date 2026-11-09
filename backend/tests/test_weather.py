"""기상 수집·변환 테스트 (네트워크 없이 모의 응답 사용)"""
import json
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, simulate
from hvac import weather as wx
from hvac.schemas import Scenario

client = TestClient(app)
DAY = date(2025, 7, 15)


def rad_end(k):
    """끝 시각 k 의 직전 1시간 평균 일사 [W/m2] (모의값)"""
    k %= 24
    return 100.0 * (k - 5) if 6 <= k <= 12 else 100.0 * (19 - k) if 13 <= k <= 18 else 0.0


def expected_ghi(h):
    return (rad_end(h) + rad_end(h + 1)) / 2


# ---------- Open-Meteo ----------
def open_meteo_payload(start, days):
    t0 = datetime(start.year, start.month, start.day)
    times = [t0 + timedelta(hours=i) for i in range(24 * days)]
    return json.dumps({"latitude": 37.55, "longitude": 127.0, "elevation": 38.0, "timezone": "Asia/Seoul",
                       "hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
                                  "temperature_2m": [20.0 + t.hour for t in times],
                                  "relative_humidity_2m": [60.0] * len(times),
                                  "shortwave_radiation": [rad_end(t.hour) for t in times]}}).encode()


def test_open_meteo_alignment():
    seen = {}

    def get(url):
        q = parse_qs(urlparse(url).query)
        seen.update(q)
        s, e = date.fromisoformat(q["start_date"][0]), date.fromisoformat(q["end_date"][0])
        return open_meteo_payload(s, (e - s).days + 1)

    d = wx.fetch_open_meteo(37.57, 126.98, DAY, http_get=get)
    assert seen["end_date"] == ["2025-07-16"]           # 일사 변환용 다음날 00시 포함
    w = d["weather"]
    assert w["dry_bulb_c"] == [20.0 + h for h in range(24)]
    assert w["ghi_w_m2"] == [expected_ghi(h) for h in range(24)]
    assert d["meta"]["source"] == "open_meteo" and d["meta"]["date"] == "2025-07-15"
    Scenario.model_validate({**json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8")),
                             "weather": w})


# ---------- 기상청 ASOS ----------
def kma_items(start, end, drop_ta_hour=None):
    out, t = [], start
    while t <= end:
        r = rad_end(t.hour)
        out.append({"tm": t.strftime("%Y-%m-%d %H:%M"), "stnId": "108", "stnNm": "서울",
                    "ta": "" if t.hour == drop_ta_hour else str(25.0 + t.hour / 10),
                    "hm": "70", "icsr": str(round(r / wx.MJ_TO_WM2, 4)) if r else
                    ("0.0" if 5 < t.hour < 21 else "")})   # 해가 있는 시간의 0 은 0.0, 야간은 빈 값
        t += timedelta(hours=1)
    return out


def kma_get_factory(items, page_size=10):
    def get(url):
        q = parse_qs(urlparse(url).query)
        assert q["dataCd"] == ["ASOS"] and q["dateCd"] == ["HR"] and q["stnIds"] == ["108"]
        page = int(q["pageNo"][0])
        chunk = items[(page - 1) * page_size: page * page_size]
        return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                                        "body": {"dataType": "JSON", "items": {"item": chunk},
                                                 "pageNo": page, "numOfRows": page_size,
                                                 "totalCount": len(items)}}}, ensure_ascii=False).encode()
    return get


def test_kma_paging_units_and_gap_fill():
    t0 = datetime(2025, 7, 15)
    items = kma_items(t0, t0 + timedelta(hours=24), drop_ta_hour=10)
    d = wx.fetch_kma_asos(108, DAY, service_key="abc", http_get=kma_get_factory(items),
                          today=date(2025, 8, 1))
    w = d["weather"]
    assert w["dry_bulb_c"][10] == pytest.approx(26.0, abs=0.01)   # 9시 25.9, 11시 26.1 사이 보간
    assert w["ghi_w_m2"] == pytest.approx([expected_ghi(h) for h in range(24)], abs=0.2)  # MJ→W 환산
    assert d["meta"]["station_name"] == "서울"
    assert any("보간" in n for n in d["meta"]["notes"])


def test_kma_rules():
    with pytest.raises(wx.WeatherError, match="전일"):
        wx.fetch_kma_asos(108, DAY, service_key="abc", http_get=lambda u: b"", today=DAY)
    err = json.dumps({"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED"}}})
    with pytest.raises(wx.WeatherError, match="30"):
        wx.fetch_kma_asos(108, DAY, service_key="abc", http_get=lambda u: err.encode(), today=date(2025, 8, 1))
    # 인코딩된 키는 그대로, 디코딩 키는 인코딩
    assert "serviceKey=a%2Bb" in wx.kma_url("a+b", 108, datetime(2025, 7, 15), datetime(2025, 7, 16))
    assert "serviceKey=a%2Bb" in wx.kma_url("a%2Bb", 108, datetime(2025, 7, 15), datetime(2025, 7, 16))


def test_long_gap_and_unit_check():
    t0 = datetime(2025, 7, 15)
    items = kma_items(t0, t0 + timedelta(hours=24))
    for it in items[8:13]:
        it["ta"] = ""
    with pytest.raises(wx.WeatherError, match="연속 5시간"):
        wx.fetch_kma_asos(108, DAY, service_key="k", http_get=kma_get_factory(items), today=date(2025, 8, 1))
    items = kma_items(t0, t0 + timedelta(hours=24))
    for it in items:
        if it["icsr"]:
            it["icsr"] = str(float(it["icsr"]) * 1000)     # 단위 착오(kJ 등) 가정
    with pytest.raises(wx.WeatherError, match="단위"):
        wx.fetch_kma_asos(108, DAY, service_key="k", http_get=kma_get_factory(items), today=date(2025, 8, 1))


# ---------- EPW ----------
def make_epw():
    head = ["LOCATION,SEOUL,-,KOR,TMY,471080,37.57,126.97,9.0,86.0"] + [f"HDR{i}" for i in range(7)]
    rows = []
    for d in (1, 2):
        for hr in range(1, 25):
            r = [1999, 1, d, hr, 60, "?", 20.0, 0, 50, 101325, 0, 0, 0, rad_end(hr), 0, 0] + [0] * 19
            rows.append(",".join(str(x) for x in r))
    return "\n".join(head + rows) + "\n"


def test_epw_alignment():
    # 기온 = 20 + (일-1) + hour/10 → 어느 행이 어느 시각으로 갔는지 식별 가능
    lines = make_epw().splitlines()
    fixed = lines[:8]
    for ln in lines[8:]:
        c = ln.split(",")
        c[6] = str(20 + int(c[3]) / 10 + (int(c[2]) - 1))
        fixed.append(",".join(c))
    d = wx.weather_from_epw("\n".join(fixed), 1, 2)
    w = d["weather"]
    assert w["dry_bulb_c"][0] == pytest.approx(22.4)   # 1/1 hour24
    assert w["dry_bulb_c"][5] == pytest.approx(21.5)   # 1/2 hour5
    assert w["ghi_w_m2"] == [expected_ghi(h) for h in range(24)]
    assert d["meta"]["latitude"] == 37.57


# ---------- 설계일·시나리오·API ----------
def test_design_days_and_engine_run():
    def get(url):
        q = parse_qs(urlparse(url).query)
        s, e = date.fromisoformat(q["start_date"][0]), date.fromisoformat(q["end_date"][0])
        payload = json.loads(open_meteo_payload(s, (e - s).days + 1))
        hr = payload["hourly"]
        hr["temperature_2m"] = [t + (5 if hr["time"][i].startswith("2025-07-16") else 0)
                                for i, t in enumerate(hr["temperature_2m"])]
        return json.dumps(payload).encode()

    days = wx.fetch_open_meteo_range(37.57, 126.98, DAY, DAY + timedelta(days=2), http_get=get)
    dd = wx.design_days(days)
    assert dd["hottest_dry_bulb"]["date"] == "2025-07-16" and dd["n_days"] == 3
    sc = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    new = wx.apply_to_scenario(sc, days[1])
    assert new["site"]["date"] == "2025-07-16" and "open_meteo" in new["_note"]
    r = simulate(Scenario.model_validate(new).to_engine_dict())
    assert r["hourly"][14]["weather"]["dry_bulb_c"] == pytest.approx(39.0)


def test_api_epw_and_fetch(monkeypatch):
    r = client.get("/weather/sources")
    assert r.status_code == 200 and r.json()["open_meteo"]["key_required"] is False
    lines = make_epw().splitlines()
    fixed = lines[:8] + [",".join([*ln.split(",")[:6], "25", *ln.split(",")[7:]]) for ln in lines[8:]]
    r = client.post("/weather/epw", json={"epw_text": "\n".join(fixed), "month": 1, "day": 2})
    assert r.status_code == 200 and r.json()["weather"]["dry_bulb_c"] == [25.0] * 24
    r = client.post("/weather/epw", json={"epw_text": "nope", "month": 1, "day": 2})
    assert r.status_code == 422

    def get(url):
        q = parse_qs(urlparse(url).query)
        s, e = date.fromisoformat(q["start_date"][0]), date.fromisoformat(q["end_date"][0])
        return open_meteo_payload(s, (e - s).days + 1)
    monkeypatch.setattr(wx, "_http_get", get)
    r = client.post("/weather/fetch", json={"source": "open_meteo", "date": "2025-07-15",
                                            "latitude": 37.57, "longitude": 126.98})
    assert r.status_code == 200 and len(r.json()["weather"]["ghi_w_m2"]) == 24
    r = client.post("/weather/fetch", json={"source": "open_meteo", "date": "2025-07-15"})
    assert r.status_code == 422
    monkeypatch.delenv("KMA_SERVICE_KEY", raising=False)   # .env 는 conftest 가 임시 파일로 바꿈
    r = client.post("/weather/fetch", json={"source": "kma_asos", "date": "2025-07-15", "station_id": 108})
    assert r.status_code == 503
    r = client.post("/weather/design-days", json={"source": "open_meteo", "start": "2025-07-01",
                                                  "end": "2025-07-03", "latitude": 37.57, "longitude": 126.98})
    assert r.status_code == 200 and set(r.json()["weather"]) == {"hottest_dry_bulb", "hottest_wet_bulb",
                                                                  "typical_wet_bulb"}


# ---------- 기상청 인증키 입력 (화면 → .env, 재시작 없이 반영) ----------
VILAGE_OK = json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"}, "body": {
    "dataType": "JSON", "items": {"item": [{"baseDate": "20261030", "baseTime": "0500", "category": "TMP",
                                           "fcstDate": "20261030", "fcstTime": "0600", "fcstValue": "9", "nx": 60, "ny": 127}]},
    "pageNo": 1, "numOfRows": 10, "totalCount": 1}}}).encode()
# 포털이 실제로 돌려준 본문 (ASOS 미신청 키, HTTP 403)
NOT_REGISTERED = ('{"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", '
                  '"returnAuthMsg": "등록되지 않은 서비스키", "returnReasonCode": "30"}}}').encode()


def by_service(asos):
    return lambda url: VILAGE_OK if "VilageFcst" in url else asos(url)


def test_kma_key_entry_and_check(monkeypatch):
    monkeypatch.delenv("KMA_SERVICE_KEY", raising=False)
    assert client.get("/weather/kma-key").json()["configured"] is False
    t0 = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    monkeypatch.setattr(wx, "_http_get", by_service(kma_get_factory(kma_items(t0, t0 + timedelta(hours=24)), page_size=50)))
    r = client.put("/weather/kma-key", json={"key": "abcdEFGH1234+/=="}).json()
    assert r["configured"] and r["masked"].endswith("/==") and "abcdEFGH" not in json.dumps(r)   # 키는 돌려주지 않음
    assert [(t["service"], t["ok"]) for t in r["tests"]] == [("vilage", True), ("asos", True)]
    assert wx.kma_key() == "abcdEFGH1234+/=="          # 저장 즉시 다음 호출에 쓰임
    assert client.get("/weather/sources").json()["kma_asos"]["key_configured"]
    client.delete("/weather/kma-key")
    assert wx.kma_key() is None


def test_kma_403_not_registered_is_per_service(monkeypatch):
    """ASOS 만 활용신청이 안 된 키: HTTP 403 본문의 코드 30 을 읽어 그 서비스만 실패로, 원인을 설명"""
    import io
    import urllib.error

    def urlopen(req, timeout=None):
        if "VilageFcst" in req.full_url:
            return io.BytesIO(VILAGE_OK)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(NOT_REGISTERED))
    monkeypatch.setattr(wx.urllib.request, "urlopen", urlopen)
    r = client.put("/weather/kma-key", json={"key": "a" * 64}).json()
    vil, asos = r["tests"]
    assert vil["ok"] and not asos["ok"]
    assert "30" in asos["message"] and "활용신청" in asos["message"]


def test_kma_key_errors_are_explained(monkeypatch):
    monkeypatch.setattr(wx, "_http_get", lambda u: json.dumps(
        {"response": {"header": {"resultCode": "22", "resultMsg": "LIMITED"}}}).encode())
    r = client.put("/weather/kma-key", json={"key": "k" * 20}).json()
    assert all(not t["ok"] and "요청 한도" in t["message"] for t in r["tests"])
    xml = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"           b"<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    monkeypatch.setattr(wx, "_http_get", lambda u: xml)
    r = client.post("/weather/kma-key/test").json()
    assert "등록되지 않은 인증키" in r["tests"][0]["message"]
    assert client.put("/weather/kma-key", json={"key": "abc def ghij klmn"}).status_code == 422


def test_latest_vilage_base():
    assert wx.latest_vilage_base(datetime(2026, 10, 30, 5, 9)) == datetime(2026, 10, 30, 2)
    assert wx.latest_vilage_base(datetime(2026, 10, 30, 5, 10)) == datetime(2026, 10, 30, 5)
    assert wx.latest_vilage_base(datetime(2026, 10, 30, 1, 0)) == datetime(2026, 10, 29, 23)


# ---------- 기상청 ASOS 1년 (연간 예측) ----------
def asos_year_items(stn, name, with_solar, year=2025):
    out, t, end = [], datetime(year, 1, 1), datetime(year + 1, 1, 1)
    while t <= end:
        r = rad_end(t.hour)
        out.append({"tm": t.strftime("%Y-%m-%d %H:%M"), "stnId": str(stn), "stnNm": name, "ta": "20.0", "hm": "60",
                    "icsr": (str(round(r / wx.MJ_TO_WM2, 4)) if r else ("0.0" if 5 < t.hour < 21 else "")) if with_solar else "",
                    "dc10Tca": "5"})
        t += timedelta(hours=1)
    return out


def asos_router(by_stn, page_size=999):
    def get(url):
        q = parse_qs(urlparse(url).query)
        items = by_stn[int(q["stnIds"][0])]
        page = int(q["pageNo"][0])
        return json.dumps({"response": {"header": {"resultCode": "00"}, "body": {
            "items": {"item": items[(page - 1) * page_size: page * page_size]}, "totalCount": len(items)}}},
            ensure_ascii=False).encode()
    return get


def test_kma_year_solar_sources():
    """해남(261)처럼 일사 관측이 없는 관측소: 다른 관측소 일사를 빌리거나, 없으면 전운량으로 추정"""
    get = asos_router({261: asos_year_items(261, "해남", False), 165: asos_year_items(165, "목포", True)})
    kw = dict(service_key="k", http_get=get, today=date(2026, 10, 30))
    own = wx.fetch_kma_asos_year(165, 2025, 34.8, 126.4, **kw)
    assert own["meta"]["solar_method"] == "observed" and own["meta"]["n_days"] == 365
    borrowed = wx.fetch_kma_asos_year(261, 2025, 34.6, 126.6, solar_station=165, **kw)
    assert "목포(165)" in borrowed["meta"]["solar_source"] and borrowed["meta"]["solar_method"] == "observed"
    cloud = wx.fetch_kma_asos_year(261, 2025, 34.6, 126.6, **kw)
    assert cloud["meta"]["solar_method"] == "cloud"
    for d in cloud["days"][::30]:      # 0 ≤ 추정 일사 ≤ 맑은 날 일사
        n = wx.day_of_year(d["date"])
        for h, g in enumerate(d["weather"]["ghi_w_m2"]):
            assert 0 <= g <= wx.clear_sky_ghi(max(wx.solar_geometry(n, h, 34.6, 126.6, 135)[0], 0)) + 120
    assert max(cloud["days"][180]["weather"]["ghi_w_m2"]) > 300           # 여름 한낮, 운량 5/10
    with pytest.raises(wx.WeatherError, match="끝나지 않았"):
        wx.fetch_kma_asos_year(261, 2026, 34.6, 126.6, **kw)


def test_annual_run_uses_kma_by_default(tmp_path, monkeypatch):
    from api import annual as annual_api
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    annual_api.CACHE.clear()
    sc = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    assert client.post("/annual/run", json={"scenario": sc, "year": 2025}).status_code == 503   # 키 없음 → 안내
    from hvac import secrets
    secrets.put("KMA_SERVICE_KEY", "abc")
    monkeypatch.setattr(wx, "_http_get", asos_router({108: asos_year_items(108, "서울", True)}))
    r = client.post("/annual/run", json={"scenario": sc, "year": 2025})
    assert r.status_code == 200, r.text
    assert r.json()["weather_file"] == "annual_kma_108_2025_auto.json" and r.json()["n_days"] == 365
    st = client.get("/annual/stations").json()
    hn = next(x for x in st["stations"] if x["id"] == 261)
    assert st["key_configured"] and hn["name"] == "해남" and (hn["latitude"], hn["longitude"]) == (34.5538, 126.5691)


def test_region_sets_site_and_design_day(tmp_path, monkeypatch):
    """데이터 입력의 지역 선택: 사이트 위치·관측소·연도를 저장하고, 그 해 관측에서 운전 현황 하루를 고른다.
    이후 연간 계산은 요청에 관측소·연도를 주지 않아도 이 조건을 따른다"""
    from api import annual as annual_api
    from hvac import secrets
    monkeypatch.setattr(wx, "WEATHER_DIR", tmp_path)
    annual_api.CACHE.clear()
    secrets.put("KMA_SERVICE_KEY", "abc")
    hn, mp = asos_year_items(261, "해남", False), asos_year_items(165, "목포", True)
    hn[24 * 200 + 15]["ta"] = "33.0"                  # 2025-07-20 15시가 가장 더운 날
    monkeypatch.setattr(wx, "_http_get", asos_router({261: hn, 165: mp}))
    sc = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    r = client.post("/weather/region", json={"scenario": sc, "station_id": 261, "solar_station_id": 165,
                                             "year": 2025, "design_day": "hottest_dry_bulb"})
    assert r.status_code == 200, r.text
    out = r.json()
    s = out["site"]
    assert (s["latitude"], s["longitude"], s["asos_station_id"], s["asos_solar_station_id"], s["weather_year"]) ==         (34.5538, 126.5691, 261, 165, 2025)
    assert s["name"] == "해남 데이터센터" and s["date"] == "2025-07-20" and max(out["weather"]["dry_bulb_c"]) > 30 and "해남(261)" in out["_note"]
    run = client.post("/annual/run", json={"scenario": out})
    assert run.status_code == 200, run.text
    assert run.json()["weather_file"] == "annual_kma_261_2025_auto165.json"
    keep = client.post("/weather/region", json={"scenario": sc, "station_id": 108, "year": 2024, "design_day": "keep"})
    assert keep.json()["weather"] == sc["weather"] and keep.json()["site"]["asos_station_id"] == 108
    assert client.post("/weather/region", json={"scenario": sc, "station_id": 1, "year": 2025}).status_code == 422
