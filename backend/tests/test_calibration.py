"""Phase 12 BMS 보정: 참값을 아는 모델로 BMS 를 합성 → 보정으로 참값 회복, 열 누락 단계 건너뜀, 적용·되돌리기"""
import base64
import copy
import json
import random
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import calibration as cal_api
from api.main import app
from api.platform import run_sync
from hvac import DATA_DIR, bms, calibrate, datasets

client = TestClient(app)
TRUE = {"internal.misc_kw": 60.0, "cooling_plant.chiller_cop_rated": 5.0, "cooling_layout.crah.fan_kw_per_m3_s": 0.5}


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def truth_scenario():
    sc = sample()
    sc["cooling_layout"] = calibrate._layout(sc)
    for p, v in TRUE.items():
        calibrate._set(sc, p, v)
    return sc


def synthetic_csv(n_days=20, noise=0.02, cols=("it", "cool", "chiller", "fan")):
    rnd = random.Random(3)
    sc = truth_scenario()
    days = []
    for k in range(n_days):
        d = date(2025, 7, 1) + timedelta(days=k)
        w = copy.deepcopy(sc["weather"])
        w["dry_bulb_c"] = [t - 0.3 * k for t in w["dry_bulb_c"]]
        util = [u * (1 + 0.01 * (k % 5)) for u in sc["it_load"]["utilization"]]
        days.append({"date": d.isoformat(), "weather": w, "util": util, "meas": {}})
    pr = calibrate.predict(sc, days, ["total_cooling_kw", "chiller_kw", "crah_fan_kw"])
    head = ["측정일시", "외기온도(℃)", "외기습도(%)"]
    head += [h for c, h in (("it", "IT 전력(kW)"), ("cool", "냉방부하(kW)"), ("chiller", "칠러 전력(kW)"),
                            ("fan", "CRAH 팬 전력(kW)")) if c in cols]
    lines = [",".join(head)]
    for i, d in enumerate(days):
        for h in range(24):
            n = lambda v: v * (1 + rnd.uniform(-noise, noise))
            row = [f"{d['date']} {h:02d}:00", f"{d['weather']['dry_bulb_c'][h]:.2f}", f"{d['weather']['rh_pct'][h]:.1f}"]
            vals = {"it": d["util"][h] * sc["it_load"]["design_kw"], "cool": pr["total_cooling_kw"][i, h],
                    "chiller": pr["chiller_kw"][i, h], "fan": pr["crah_fan_kw"][i, h]}
            row += [f"{n(vals[c]):.2f}" for c in ("it", "cool", "chiller", "fan") if c in cols]
            lines.append(",".join(row))
    return "\n".join(lines).encode("utf-8")


def ds_from(csv):
    return bms.dataset(bms.inspect([("bms.csv", csv)]))


def test_column_matching_and_units():
    t = bms.inspect([("b.csv", "일시,외기온도,외기습도,냉방부하(RT),칠러전력(kW),보급수(L/h)\n"
                                 "2025-07-01 00:00,25,70,500,100,2000\n2025-07-01 01:00,25,70,520,101,2100\n".encode())])[0]
    m = t["mapping"]
    assert m["timestamp"] == "일시" and m["outdoor_temp_c"] == "외기온도" and m["outdoor_rh_pct"] == "외기습도"
    assert m["total_cooling_kw"] == "냉방부하(RT)" and m["chiller_kw"] == "칠러전력(kW)" and m["makeup_m3_h"] == "보급수(L/h)"
    ds = bms.dataset([t])
    day = ds["days"]["2025-07-01"]
    assert day["total_cooling_kw"][0] == pytest.approx(500 * 3.517) and day["makeup_m3_h"][1] == pytest.approx(2.1)
    assert ds["summary"]["has_weather"]


def test_recovers_true_parameters():
    ds = ds_from(synthetic_csv())
    res = calibrate.run(sample(), ds)
    got = {p["path"]: p["after"] for p in res["params"]}
    for p, v in TRUE.items():
        assert got[p] == pytest.approx(v, rel=0.05), (p, got[p])
    skipped = {s["stage"] for s in res["skipped"]}
    assert {"pump", "airflow", "water"} <= skipped          # 열이 없는 단계는 건너뜀
    m = {x["metric"]: x for x in res["metrics"]}
    assert m["chiller_kw"]["after"]["cv_rmse_pct"] < m["chiller_kw"]["before"]["cv_rmse_pct"]
    assert m["chiller_kw"]["after"]["pass"] and res["verdict"] in ("pass", "partial")
    assert res["train_days"] == 14 and res["valid_days"] == 6 and not res["short"]
    assert len(res["series"]["measured"]) == len(res["series"]["after"])


def test_short_period_and_missing_weather():
    csv = synthetic_csv(n_days=3)
    res = calibrate.run(sample(), ds_from(csv), stages=["load"])
    assert res["short"] and any("14일 미만" in n for n in res["notes"])
    no_wx = "일시,냉방부하(kW)\n2025-07-01 00:00,1000\n2025-07-01 01:00,1000\n".encode()
    with pytest.raises(ValueError):
        calibrate.run(sample(), ds_from(no_wx))


def test_api_flow_apply_and_revert(tmp_path, monkeypatch):
    monkeypatch.setattr(datasets, "ROOT", tmp_path)
    monkeypatch.setattr(cal_api, "_weather_days", lambda sc, ds: ({}, None))
    files = [{"name": "bms.csv", "data_b64": base64.b64encode(synthetic_csv(n_days=8)).decode()}]
    ins = client.post("/bms/inspect", json={"files": files}).json()
    assert ins["has_weather"] and any(s["stage"] == "chiller" and s["possible"] for s in ins["stages"])
    d = client.post("/bms/dataset", json={"files": files}).json()
    assert d["n_days"] == 8
    res = run_sync("calibrate", {"scenario": sample(), "dataset_id": d["id"], "stages": ["chiller"]})
    assert res["params"][0]["path"] == "cooling_plant.chiller_cop_rated"
    r = client.post("/calibration/apply", json={"scenario": sample(), "run_id": res["run_id"]})
    assert r.status_code == 200
    sc = r.json()
    assert sc["cooling_plant"]["chiller_cop_rated"] == res["params"][0]["after"]
    assert sc["provenance"]["cooling_plant.chiller_cop_rated"]["source"] == "bms_calibrated"
    back = client.post("/scenario/revert", json={"scenario": sc, "paths": list(sc["provenance"])}).json()
    assert back["cooling_plant"]["chiller_cop_rated"] == 6.0
    assert client.post("/calibration/apply", json={"scenario": sample(), "run_id": "zzz"}).status_code == 404


def test_lm_bounds():
    x, _, _ = calibrate._lm(lambda x: np.array([x[0] - 5.0]), [1.0], np.array([0.0]), np.array([3.0]))
    assert x[0] == pytest.approx(3.0)
