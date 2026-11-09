"""Phase 10 (출처·되돌리기·작업·데이터셋) + Phase 11 (IT 데이터셋·연간 IT 소스)"""
import base64
import json
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api import platform
from api.main import app
from hvac import DATA_DIR, annual, datasets, itdata, itprofile, provenance

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def ds_root(tmp_path, monkeypatch):
    monkeypatch.setattr(datasets, "ROOT", tmp_path)
    return tmp_path


# ---------- Phase 10 ----------
def test_provenance_set_revert_and_api():
    sc = sample()
    provenance.set_value(sc, "cooling_plant.chiller_cop_rated", 6.8, "bms_calibrated", "r1")
    provenance.set_value(sc, "cooling_plant.chiller_cop_rated", 7.0, "bms_calibrated", "r2")   # 두 번 적용해도 원래 값으로 복원
    assert sc["provenance"]["cooling_plant.chiller_cop_rated"]["previous"] == 6.0
    r = client.post("/scenario/revert", json={"scenario": sc, "paths": ["cooling_plant.chiller_cop_rated"]})
    assert r.status_code == 200
    assert r.json() == client.get("/scenario/sample").json()   # 완전히 원래대로
    st = client.post("/scenario/status", json=sc).json()
    assert st["guide"]["bms"] is True and 0 <= st["trust"]["measured_pct"] <= 100
    assert any(i["path"] == "cooling_plant.chiller_cop_rated" and i["source"] == "bms_calibrated" for i in st["trust"]["items"]) \
        or "cooling_plant.chiller_cop_rated" not in provenance.assumed_fields()
    assert client.post("/scenario/revert", json={"scenario": sample(), "paths": ["x"]}).status_code == 422


def test_revert_removes_new_field():
    sc = sample()
    provenance.set_value(sc, "internal.misc_kw", 12.0, "bms_calibrated")
    back, done = provenance.revert(sc, ["internal.misc_kw"])
    assert done and "misc_kw" not in back["internal"] and "provenance" not in back


def test_jobs_progress_result_and_cancel():
    class Req(BaseModel):
        n: int

    def work(req, progress):
        for i in range(req.n):
            progress(i / req.n, f"{i}단계")
            time.sleep(0.02)
        return {"sum": req.n * 2}
    platform.register("_test", Req, work)
    jid = client.post("/jobs/_test", json={"n": 5}).json()["job_id"]
    for _ in range(200):
        j = client.get(f"/jobs/{jid}").json()
        if j["status"] == "done":
            break
        time.sleep(0.02)
    assert j["status"] == "done" and j["result"] == {"sum": 10} and j["progress"] == 1.0
    jid = client.post("/jobs/_test", json={"n": 200}).json()["job_id"]
    client.delete(f"/jobs/{jid}")
    for _ in range(200):
        j = client.get(f"/jobs/{jid}").json()
        if j["status"] in ("cancelled", "done"):
            break
        time.sleep(0.02)
    assert j["status"] == "cancelled"
    assert client.post("/jobs/nope", json={}).status_code == 404
    assert client.post("/jobs/_test", json={"n": "x"}).status_code == 422


def test_dataset_store(ds_root):
    d = datasets.save("it", {"meta": {"n_days": 1}, "summary": {"a": 1}}, "f.csv")
    i = d["meta"]["id"]
    assert [m["id"] for m in client.get("/datasets?kind=it").json()] == [i]
    assert client.get(f"/datasets/it/{i}").json()["summary"] == {"a": 1}
    assert client.get("/datasets/it/../x").status_code == 404
    assert client.delete(f"/datasets/it/{i}").status_code == 200
    assert client.get(f"/datasets/it/{i}").status_code == 404


# ---------- Phase 11 ----------
def synthetic_recs(start=date(2025, 6, 2), n_days=56, racks=4):
    """평일 1.0, 주말 0.8, 낮 피크 (랙당 10 kW 기준)"""
    recs = []
    for k in range(n_days):
        d = start + timedelta(days=k)
        f = 1.0 if d.weekday() < 5 else 0.8
        for h in range(24):
            for r in range(racks):
                recs.append((d, float(h), f"R{r}", 10 * f * (0.8 + 0.2 * (8 <= h <= 18))))
    return recs


def test_dataset_typical_profiles_and_fallback():
    ds = itdata.dataset(synthetic_recs(), 50.0)
    s = ds["summary"]
    assert s["n_days"] == 56 and s["coverage"] == 1.0 and s["weekend_ratio"] == pytest.approx(0.8, abs=1e-3)
    wk = ds["typical"]["by_daytype"]["weekday"]
    assert wk[12] == pytest.approx(40.0) and wk[3] == pytest.approx(32.0)
    kw, how = itprofile.day_kw(ds, date(2025, 6, 10))
    assert how == "date"
    kw, how = itprofile.day_kw(ds, date(2026, 6, 13))      # 토요일, 같은 월 있음
    assert how == "month_daytype" and kw[12] == pytest.approx(32.0)
    kw, how = itprofile.day_kw(ds, date(2026, 12, 14))     # 12월 없음 → 요일 유형 평균
    assert how == "daytype" and kw[12] == pytest.approx(40.0)
    assert itdata.dataset([(None, 1.0, "R", 1.0)], 1) is None


def test_forecast_kw_trend():
    recs = []
    start = date(2025, 6, 2)
    for k in range(42):
        d = start + timedelta(days=k)
        for h in range(24):
            recs.append((d, float(h), "R", 100.0 * (1 + 0.01 * k)))   # 하루 1% 선형 증가
    ds = itdata.dataset(recs, 200)
    last = start + timedelta(days=41)
    kw, info = itprofile.forecast_kw(ds, last + timedelta(days=1))
    assert kw[0] > 100 * (1 + 0.01 * 41) * 0.97        # 추세 반영 (같은 요일 평균보다 큼)
    assert info["trend_factor"] > 1 and "같은 요일" in info["basis"]


def test_annual_with_dataset_reproduces_weekend_ratio():
    from tests.test_annual import synthetic_year
    sc = sample()
    sc["it_load"]["design_kw"] = 50.0
    ds = itdata.dataset(synthetic_recs(), 50.0)
    r = annual.run(sc, synthetic_year(n_days=28), it_dataset=ds, architectures=("air_chw",))
    d = r["architectures"][0]["daily"]
    wk = [x["it_kwh"] for x in d if date.fromisoformat(x["date"]).weekday() < 5]
    we = [x["it_kwh"] for x in d if date.fromisoformat(x["date"]).weekday() >= 5]
    assert (sum(we) / len(we)) / (sum(wk) / len(wk)) == pytest.approx(0.8, abs=0.01)
    assert r["it_source"] == "dataset" and any("IT 데이터셋" in n for n in r["notes"])
    assert all(len(v["utilization"]) == 24 for v in r["peak_days"].values())


def test_itload_apply_saves_dataset(ds_root):
    csv = (DATA_DIR / "itload" / "sample_it_hourly.csv").read_bytes()
    body = {"files": [{"name": "it.csv", "data_b64": base64.b64encode(csv).decode()}], "scenario": sample()}
    j = client.post("/itload/apply", json=body).json()
    ds = j["dataset"]
    assert ds and ds["n_days"] >= 3 and j["scenario"]["it_load"]["dataset_id"] == ds["id"]
    assert j["scenario"]["provenance"]["it_load.utilization"]["source"] == "it_data"
    a = client.post("/annual/run", json={"scenario": j["scenario"], "year": 2025, "source": "open_meteo", "it_source": "dataset"})
    assert a.status_code == 200, a.text
    assert a.json()["it_source"] == "dataset"
    j2 = client.post("/itload/apply", json={**body, "save_dataset": False}).json()
    assert j2["dataset"] is None and "dataset_id" not in j2["scenario"]["it_load"]
    assert client.post("/annual/run", json={"scenario": sample(), "year": 2025, "source": "open_meteo", "it_source": "dataset"}).status_code == 422
