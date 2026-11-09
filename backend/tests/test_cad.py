"""CAD(DXF) 가져오기 테스트: backend 폴더에서  python -m pytest tests/test_cad.py"""
import base64
import json

import ezdxf
import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, cad
from hvac.cad_sample import make
from hvac.plant import evaluate
from hvac.schemas import CoolingLayout, Scenario

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def built(tmp_path, rotate=0.0, north=0.0, **kw):
    p = tmp_path / f"dc_{rotate}.dxf"
    make(str(p), rotate, **kw)
    parsed = cad.load(p.read_bytes(), p.name)
    roles = {l["name"]: l["suggested_role"] for l in cad.inspect(parsed)["layers"]}
    sc = sample()
    lay = CoolingLayout().model_dump()
    b = cad.build(parsed, roles, sc=sc, layout=lay, north_deg=north, height_m=10)
    return b, Scenario.model_validate(cad.apply_to_scenario(sc, lay, b)).to_engine_dict()


def test_role_suggestion_by_layer_and_block_name():
    assert cad.suggest_role("A-WALL") == "wall"
    assert cad.suggest_role("A-GLAZ") == "window"
    assert cad.suggest_role("E-RACK") == "rack"
    assert cad.suggest_role("CABLE-TRAY") == "ignore"          # CAB 은 토큰 단위로만
    assert cad.suggest_role("M-CRAH") == "crah"
    assert cad.suggest_role("냉각탑") == "tower"
    assert cad.suggest_role("ELECTRICAL") == "ignore"          # CT 는 토큰 단위로만


def test_sample_drawing_envelope_and_layout(tmp_path):
    b, sc = built(tmp_path)
    r = b["report"]
    assert r["counts"] == {"racks": 240, "rows": 8, "crah": 10, "windows": 8}
    assert r["building"]["ew_m"] == pytest.approx(60) and r["building"]["ns_m"] == pytest.approx(50)
    # 외벽 이중선 중 바깥선, 벽 = 둘레 x 층고 - 창 (남 2 m x 5개, 북 2 m x 3개, 창 높이 1.5 m)
    assert r["envelope"]["walls"] == {"N": 591.0, "E": 500.0, "S": 585.0, "W": 500.0}
    assert r["envelope"]["windows"] == {"N": 9.0, "S": 15.0}
    assert sc["envelope"]["roof"]["area_m2"] == 3000 and sc["internal"]["floor_area_m2"] == 3000
    lay = sc["cooling_layout"]
    assert (lay["racks"]["width_m"], lay["racks"]["depth_m"], lay["racks"]["aisle_m"]) == (0.6, 1.2, 2.4)
    ids = [it["id"] for it in lay["racks"]["items"]]
    assert ids[0] == "A01" and len(set(ids)) == 240               # 블록 속성 RACK_ID
    assert {it["design_kw"] for it in lay["racks"]["items"]} == {6, 8, 10, 12}
    assert len(lay["crah"]["positions"]) == 10
    assert lay["plant"]["gap_east_m"] == pytest.approx(12.0)


def test_rotated_drawing_gives_same_result_with_north_angle(tmp_path):
    """도면을 반시계 30° 돌려 그리면 진북은 도면 +Y 에서 시계방향 -30°"""
    b0, _ = built(tmp_path)
    b1, sc1 = built(tmp_path, rotate=30, north=-30)
    assert b1["report"]["envelope"] == b0["report"]["envelope"]
    assert b1["report"]["counts"] == b0["report"]["counts"]
    assert b1["report"]["building"]["rotation_deg"] == pytest.approx(30)
    xs0 = sorted((it["x_m"], it["z_m"]) for it in b0["layout"]["items"])
    xs1 = sorted((it["x_m"], it["z_m"]) for it in b1["layout"]["items"])
    assert all(a == pytest.approx(b, abs=0.01) for p, q in zip(xs0, xs1) for a, b in zip(p, q))


def test_cad_layout_runs_through_coupled_evaluation(tmp_path):
    _, sc = built(tmp_path)
    ev = evaluate(sc, sc["cooling_layout"])
    racks = ev["airflow"]["racks"]
    assert len(racks) == 240 and racks[0]["id"] == "A01"
    # 랙 발열은 설계 kW(속성) 비례: 12 kW 열 랙 = 6 kW 열 랙의 2배
    by_id = {r["id"]: r["kw"] for r in racks}
    a, d = next(it for it in sc["cooling_layout"]["racks"]["items"] if it["design_kw"] == 12), \
        next(it for it in sc["cooling_layout"]["racks"]["items"] if it["design_kw"] == 6)
    assert by_id[a["id"]] == pytest.approx(2 * by_id[d["id"]], rel=1e-3)
    assert sum(by_id.values()) == pytest.approx(ev["airflow"]["it_kw"], rel=1e-3)
    assert ev["geometry"]["building"] == {"ew_m": 60.0, "ns_m": 50.0, "h_m": 10.0}


def test_rack_ids_from_text_and_rectangles(tmp_path):
    """블록 대신 닫힌 사각형 + 문자로 그린 랙, 단위 미지정(m)"""
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (30, 0), (30, 20), (0, 20)], close=True, dxfattribs={"layer": "OUTLINE"})
    for j, y in enumerate((8.0, 11.6)):
        for i in range(10):
            x = 10 + i * 0.6
            msp.add_lwpolyline([(x, y), (x + 0.6, y), (x + 0.6, y + 1.2), (x, y + 1.2)], close=True,
                               dxfattribs={"layer": "RACK"})
            msp.add_text(f"R{j}{i}", dxfattribs={"layer": "TXT", "height": 0.1}).set_placement((x + 0.1, y + 0.5))
    p = tmp_path / "rect.dxf"
    doc.saveas(p)
    parsed = cad.load(p.read_bytes(), p.name)
    b = cad.build(parsed, {"OUTLINE": "outline", "RACK": "rack"}, sc=sample(), layout=CoolingLayout().model_dump())
    ids = {it["id"] for it in b["layout"]["items"]}
    assert len(ids) == 20 and "R00" in ids and "R19" in ids
    assert b["layout"]["rows"] == 2 and b["layout"]["aisle_m"] == pytest.approx(2.4)
    assert "(m)" in b["report"]["notes"][0]                       # ezdxf.new() 기본 $INSUNITS=6
    parsed["insunits"] = 0
    b = cad.build(parsed, {"OUTLINE": "outline", "RACK": "rack"}, sc=sample(), layout=CoolingLayout().model_dump())
    assert "m 추정" in b["report"]["notes"][0] and b["report"]["counts"]["racks"] == 20


def test_dwg_is_rejected_with_guidance():
    with pytest.raises(cad.CadError, match="DXF"):
        cad.load(b"AC1032" + b"\0" * 100, "plan.dwg")


def test_missing_outline_role_is_error(tmp_path):
    p = tmp_path / "s.dxf"
    make(str(p))
    parsed = cad.load(p.read_bytes(), p.name)
    with pytest.raises(cad.CadError, match="외곽선"):
        cad.build(parsed, {"E-RACK": "rack"}, sc=sample(), layout=CoolingLayout().model_dump())


def test_api_inspect_then_build(tmp_path):
    p = tmp_path / "api.dxf"
    make(str(p))
    r = client.post("/cad/inspect", json={"file_name": p.name, "data_b64": base64.b64encode(p.read_bytes()).decode()})
    assert r.status_code == 200, r.text
    ins = r.json()
    assert ins["units"]["scale_m"] == 0.001 and "E-RACK" in ins["preview"]["layers"]
    roles = {l["name"]: l["suggested_role"] for l in ins["layers"]}
    r = client.post("/cad/build", json={"cad_id": ins["cad_id"], "roles": roles, "scenario": sample(),
                                        "options": {"height_m": 8}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["scenario"]["building"]["height_m"] == 8 and len(out["preview"]["racks"]) == 240
    assert client.post("/cooling/evaluate", json=out["scenario"]).status_code == 200
    assert client.post("/cad/build", json={"cad_id": "nope", "roles": roles, "scenario": sample()}).status_code == 404
