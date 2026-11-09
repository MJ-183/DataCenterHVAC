"""IT 부하 데이터 가져오기(자동 컬럼 매칭) 테스트: backend 폴더에서  python -m pytest tests/test_itdata.py"""
import base64
import io
import json
from datetime import datetime, timedelta

import openpyxl
import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, itdata
from hvac.schemas import CoolingLayout

client = TestClient(app)
T0 = datetime(2026, 8, 1)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def one(name, data, **ov):
    (a,) = itdata.inspect_files([(name, data)], {name: ov} if ov else None)
    return a


def test_korean_long_cp949_with_title_row_and_watt_guess():
    rows = ["IT 전력 리포트", "측정일시,랙번호,소비전력"]
    rows += [f"{T0 + timedelta(hours=h):%Y-%m-%d %H:%M},A-0{r},{5000 + r}" for h in range(24) for r in (1, 2)]
    a = one("x.csv", "\n".join(rows).encode("cp949"))
    assert a["header_row"] == 1 and a["kind"] == "long"
    assert a["mapping"] == {"timestamp": "측정일시", "rack": "랙번호", "power": "소비전력"}
    assert a["unit"] == "W"
    recs = itdata.records(a)
    assert recs[0][3] == pytest.approx(5.001)


def test_wide_format_headers_are_rack_ids():
    rows = ["Timestamp,A01 (kW),A02 (kW),A03 (kW)"] + [f"2026/08/01 {h:02d}:00,1,2,3" for h in range(24)]
    a = one("w.csv", "\n".join(rows).encode())
    assert a["kind"] == "wide" and a["unit"] == "kW"
    assert {r[2] for r in itdata.records(a)} == {"A01", "A02", "A03"}


def test_total_table_with_voltage_current_is_not_wide():
    rows = ["date;time;IT load kW;voltage V;current A"] + [f"2026-08-01;{h}:00;1500;380;2400" for h in range(24)]
    a = one("t.csv", "\n".join(rows).encode())
    assert a["kind"] == "total" and a["mapping"]["power"] == "IT load kW"
    assert any("전류" in w for w in a["warnings"])


def test_hour_1_to_24_is_shifted():
    rows = ["hour,load"] + [f"{h},{1000 + h}" for h in range(1, 25)]
    a = one("h.csv", "\n".join(rows).encode())
    hours = sorted(r[1] for r in itdata.records(a))
    assert hours[0] == 0 and hours[-1] == 23


def test_cumulative_kwh_with_korean_ampm_timestamps():
    rows = ["일시\t랙\t적산전력량(kWh)"]
    for h in range(25):
        t = T0 + timedelta(hours=h)
        ap, hh = ("오전" if t.hour < 12 else "오후"), (t.hour % 12 or 12)
        rows.append(f"{t:%Y-%m-%d} {ap} {hh}:00:00\tR-1\t{1000 + 6 * h}")
    a = one("k.tsv", "\n".join(rows).encode("utf-8-sig"))
    assert a["kind"] == "long" and "energy" in a["mapping"]
    recs = itdata.records(a)
    assert len(recs) == 24 and all(r[3] == pytest.approx(6.0) for r in recs)


def test_headerless_long():
    rows = [f"2026-08-01 {h:02d}:00,RK{r},{3 + r}" for h in range(24) for r in range(1, 4)]
    a = one("n.csv", "\n".join(rows).encode())
    assert a["header_row"] == -1 and a["kind"] == "long"
    assert a["mapping"] == {"timestamp": "col1", "rack": "col2", "power": "col3"}


def test_xlsx_inventory_and_utilization_sheets():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "랙목록"
    ws.append(["Rack Name", "Rated Power (kW)", "Cooling Type"])
    for i in range(1, 5):
        ws.append([f"A{i:02d}", 8, "공랭" if i < 4 else "DLC"])
    ws2 = wb.create_sheet("계측")
    ws2.append(["Time", "Rack", "Util %"])
    for h in range(24):
        for i in range(1, 5):
            ws2.append([T0 + timedelta(hours=h), f"A{i:02d}", 50])
    b = io.BytesIO()
    wb.save(b)
    inv, ts = itdata.inspect_files([("inv.xlsx", b.getvalue())])
    assert inv["kind"] == "inventory" and inv["mapping"]["design_kw"] == "Rated Power (kW)"
    assert ts["kind"] == "long" and ts["unit"] == "%"
    out = itdata.build([inv, ts], sample(), [("A1", None), ("A2", None), ("A3", None), ("A4", None)])
    assert out["design_kw"] == 32 and out["utilization"] == [0.5] * 24      # 50% x 8 kW x 4
    assert out["report"]["racks"]["matched"] == 4                         # A01 ↔ A1 (앞자리 0 무시)


def test_override_mapping_and_unit():
    rows = ["t,who,val"] + [f"2026-08-01 {h:02d}:00,X1,2" for h in range(24)]
    a = one("o.csv", "\n".join(rows).encode(), mapping={"timestamp": "t", "rack": "who", "power": "val"}, unit="MW")
    assert a["kind"] == "long" and itdata.records(a)[0][3] == 2000


def test_id_matching_rules():
    m = itdata.match_ids(["rack-a01", "A-2", "RACK_B10", "zz"], ["A01", "A02", "B10", "C1"])
    assert m == {"rack-a01": "A01", "A-2": "A02", "RACK_B10": "B10"}


def test_peak_day_selection_and_gap_fill():
    recs = [(T0.date(), h, "A1", 5.0) for h in range(24) if h != 5]
    recs += [((T0 + timedelta(days=1)).date(), h, "A1", 8.0 if h == 14 else 4.0) for h in range(24)]
    prof, chosen, period, notes = itdata.day_profiles(recs, "peak")
    assert chosen == "2026-08-02" and prof["A1"][14] == 8.0
    prof, chosen, *_ = itdata.day_profiles(recs, "2026-08-01")
    assert prof["A1"][5] == pytest.approx(5.0)                             # 결측 보간
    prof, chosen, *_ = itdata.day_profiles(recs, "mean")
    assert prof["A1"][0] == pytest.approx(4.5)


def test_total_only_is_spread_and_design_from_peak():
    sc = sample()
    lay = CoolingLayout().model_dump()
    racks, grid = itdata.layout_racks(sc, lay)
    rows = ["hour,IT kW"] + [f"{h},{1000 + 10 * h}" for h in range(24)]
    a = one("tot.csv", "\n".join(rows).encode())
    out = itdata.build([a], sc, racks)
    assert out["design_kw"] == 1230 and out["utilization"][-1] == 1.0
    sc2 = itdata.apply_to_scenario(sc, lay, out, grid)
    items = sc2["cooling_layout"]["racks"]["items"]
    assert len(items) == 320 and sum(it["kw_profile"][0] for it in items) == pytest.approx(1000, rel=1e-3)


def test_api_apply_with_cad_racks():
    rows = ["Timestamp,R01-001,R01-002,R02-001"] + [f"2026-08-01 {h:02d}:00,5,6,7" for h in range(24)]
    f = {"name": "w.csv", "data_b64": base64.b64encode("\n".join(rows).encode()).decode()}
    r = client.post("/itload/inspect", json={"files": [f]})
    assert r.status_code == 200 and r.json()["tables"][0]["kind"] == "wide"
    r = client.post("/itload/apply", json={"files": [f], "scenario": sample()})
    assert r.status_code == 200, r.text
    sc = r.json()["scenario"]
    assert sc["it_load"]["design_kw"] == 18                               # 측정 최대 합계
    items = {it["id"]: it for it in sc["cooling_layout"]["racks"]["items"]}
    assert items["R01-001"]["kw_profile"][0] == 5 and items["R02-001"]["kw_profile"][0] == 7
    ev = client.post("/cooling/evaluate", json=sc)
    assert ev.status_code == 200
    kw = {x["id"]: x["kw"] for x in ev.json()["airflow"]["racks"]}
    assert kw["R01-002"] == pytest.approx(6.0, rel=1e-3) and kw["R01-003"] == 0
