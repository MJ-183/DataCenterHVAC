"""CAD 다중 층·다중 존 가져오기 (Phase 18 연장, hvac/cad_zones.py) — 3층 더미 도면(7존, 전산실 3개)을 정답 시나리오와 대조"""
import base64
import json
import os
import tempfile

import ezdxf
import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, cad
from hvac.cad_sample import make
from hvac.schemas import CoolingLayout, Scenario

client = TestClient(app)
CAD_DIR = DATA_DIR.parents[1] / "cad"
DXF_3F = CAD_DIR / "datacenter_3f_dummy.dxf"
RACK_BLOCK = "RACK_600x1200x2200"


def load(name):
    return Scenario.model_validate(json.loads((DATA_DIR / name).read_text(encoding="utf-8"))).to_engine_dict()


ANSWER = load("scenario_3f_dummy_mz.json")


@pytest.fixture(scope="module")
def parsed3f():
    return cad.load(DXF_3F.read_bytes(), DXF_3F.name)


def roles_of(parsed):
    return {l["name"]: l["suggested_role"] for l in cad.inspect(parsed)["layers"]}


def build(parsed, roles=None, block_roles=None, sc=None, **opt):
    sc = sc or ANSWER
    lay = CoolingLayout.model_validate(sc.get("cooling_layout") or {}).model_dump()
    b = cad.build(parsed, roles or roles_of(parsed), block_roles or {}, sc, lay, **opt)
    return b, Scenario.model_validate(cad.apply_to_scenario(sc, lay, b)).to_engine_dict()


def near(a, b, rel=0.01):
    return a == pytest.approx(b, rel=rel, abs=1e-6)


def env_map(z, key):
    return {w["orientation"]: w["area_m2"] for w in z["envelope"][key] if w["area_m2"] > 0}


def pairs(sc):
    return {(frozenset((z["id"], p["zone"])), p["kind"]): p["area_m2"] for z in sc["zones"] for p in z["partitions"]}


def test_floor_prefix_patterns():
    assert [cad.floor_of(n) for n in ("F2-DC-RACK", "2F-WALL", "L3-ZONE", "3층-랙", "f1-a-wall")] == [1, 1, 2, 2, 0]
    assert [cad.floor_of(n) for n in ("SITE-M-CHILLER", "ROOF-A-ROOF", "A-WALL", "F0-X", "FLOOR-1")] == [None] * 5
    assert cad.suggest_role("F2-ZONE") == "zone" and cad.suggest_role("SERVER-ROOM") == "zone"


def test_inspect_reports_floors(parsed3f):
    ins = cad.inspect(parsed3f)
    assert [f["label"] for f in ins["floors"]] == ["F1", "F2", "F3"]
    lay = {l["name"]: l for l in ins["layers"]}
    assert lay["F2-DC-RACK"]["floor"] == "F2" and lay["SITE-M-CHILLER"]["floor"] is None
    rack = next(b for b in ins["blocks"] if b["name"] == RACK_BLOCK)
    assert rack["floors"] == {"F1": 400, "F2": 400, "F3": 400}


def test_zones_match_answer(parsed3f):
    b, sc = build(parsed3f)
    assert sorted((z["floor"], z["id"], z["kind"]) for z in sc["zones"]) == \
           sorted((z["floor"], z["id"], z["kind"]) for z in ANSWER["zones"])
    ans = {z["id"]: z for z in ANSWER["zones"]}
    for z in sc["zones"]:
        a = ans[z["id"]]
        assert near(z["floor_area_m2"], a["floor_area_m2"])                         # 4,080 / 1,560 / 624 / 936 m2
        for k in ("ew_m", "ns_m", "height_m", "x_m", "z_m"):
            assert z["geometry"][k] == pytest.approx(a["geometry"][k], abs=0.05)
        for key in ("walls", "windows"):                                           # 방위별 외벽·창
            got, exp = env_map(z, key), env_map(a, key)
            assert got.keys() == exp.keys() and all(near(got[o], exp[o]) for o in exp), (z["id"], key)
        assert near(z["envelope"]["roof"]["area_m2"], a["envelope"]["roof"]["area_m2"])
        assert near(z["it_design_kw"], a["it_design_kw"])
        if a["kind"] == "data_hall":
            assert len(z["racks"]["items"]) == 400 and near(z["it_design_kw"], 16000)
            assert len(z["crah_positions"]) == 2 * ANSWER["cooling_layout"]["crah"]["per_side"]
    got, exp = pairs(sc), pairs(ANSWER)
    assert got.keys() == exp.keys() and all(near(got[k], exp[k]) for k in exp)
    assert sum(k[1] == "slab" for k in got) == 5 and sum(k[1] == "wall" for k in got) == 5
    assert sc["it_load"]["design_kw"] == pytest.approx(48000) and b["report"]["counts"]["racks"] == 1200


def test_rack_coordinates_are_zone_local(parsed3f):
    """CAD 랙 좌표 = 정답 격자(rack_positions)와 같은 존 좌표계"""
    from hvac.airflow import rack_positions
    _, sc = build(parsed3f)
    z = next(z for z in sc["zones"] if z["id"] == "hall_2f")
    a = next(z for z in ANSWER["zones"] if z["id"] == "hall_2f")
    exp, _, _ = rack_positions({**ANSWER["cooling_layout"], "racks": a["racks"]}, a["geometry"]["ew_m"], a["geometry"]["ns_m"])
    got = sorted((round(it["x_m"], 2), round(it["z_m"], 2)) for it in z["racks"]["items"])
    assert got == sorted((round(x, 2), round(zz, 2)) for _, _, x, zz in exp)


def test_simulation_matches_answer_via_api():
    r = client.post("/cad/inspect", json={"file_name": DXF_3F.name, "data_b64": base64.b64encode(DXF_3F.read_bytes()).decode()})
    assert r.status_code == 200, r.text
    ins = r.json()
    roles = {l["name"]: l["suggested_role"] for l in ins["layers"]}
    r = client.post("/cad/build", json={"cad_id": ins["cad_id"], "roles": roles, "scenario": ANSWER,
                                        "options": {"floor_heights_m": {"0": 6, "1": 6, "2": 6}}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["scenario"]["zones"]) == 7 and len(out["preview"]["floors"]) == 3
    assert out["scenario"]["provenance"]["zones"]["source"] == "cad"
    a = client.post("/simulate", json=ANSWER).json()["daily_total"]
    b = client.post("/simulate", json=out["scenario"]).json()["daily_total"]
    for k in ("cooling_kwh", "pue"):
        assert near(b[k], a[k]), k
    assert client.post("/cooling/evaluate", json=out["scenario"]).status_code == 200


def test_block_role_is_isolated_per_floor(parsed3f):
    roles = {**roles_of(parsed3f), "F1-DC-RACK": "ignore", "F2-DC-RACK": "ignore", "F3-DC-RACK": "ignore"}
    b, _ = build(parsed3f, roles, {f"{RACK_BLOCK}@F2": "rack"})
    by = {z["id"]: z for z in b["report"]["zones"]}
    assert b["report"]["counts"]["racks"] == 400 and by["hall_2f"]["racks"] == 400
    assert by["hall_1f"]["racks"] == 0 and by["hall_3f"]["racks"] == 0
    b, _ = build(parsed3f, roles, {RACK_BLOCK: "rack"})                    # 층 지정 없는 블록 역할 = 모든 층 (각 층 안에서)
    assert b["report"]["counts"]["racks"] == 1200


def test_auto_ids_and_kinds_without_labels(parsed3f):
    p = {**parsed3f, "texts": [t for t in parsed3f["texts"] if "|" not in t["text"]]}
    b, sc = build(p, sc=load("scenario_haenam_ai.json"))
    assert [(z["id"], z["kind"]) for z in sc["zones"]] == [
        ("F1_Z1", "data_hall"), ("F1_Z2", "office"), ("F1_Z3", "support"), ("F2_Z1", "data_hall"), ("F2_Z2", "support"),
        ("F3_Z1", "data_hall"), ("F3_Z2", "support")]
    assert "추정" in b["report"]["zones"][0]["kind_source"]
    assert sc["zones"][3]["name"].startswith("2층")                          # 이름은 존 안 문자 (X-TEXT)


def test_floor_without_zone_boundary_is_one_zone(parsed3f):
    roles = {k: ("ignore" if v == "zone" else v) for k, v in roles_of(parsed3f).items()}
    b, sc = build(parsed3f, roles, sc=load("scenario_haenam_ai.json"))
    assert [(z["floor"], z["kind"]) for z in sc["zones"]] == [(0, "data_hall"), (1, "data_hall"), (2, "data_hall")]
    assert near(sc["zones"][0]["floor_area_m2"], 94 * 60)
    assert {p["kind"] for z in sc["zones"] for p in z["partitions"]} == {"slab"}
    assert [z["id"] for z in sc["zones"]] == ["F1_Z1", "F2_Z1", "F3_Z1"]                # 라벨 2~3개 → 자동 id
    assert any("F1: 존 하나에 라벨 3개" in w for w in b["report"]["warnings"])


def test_options_floor_height_rack_kw_and_overrides(parsed3f):
    b, sc = build(parsed3f, floor_heights_m={0: 5.0}, zone_overrides={"hall_2f": {"it_design_kw": 18000, "name": "A홀"},
                                                                      "office_1f": {"kind": "support"}})
    z = {z["id"]: z for z in sc["zones"]}
    assert z["elec_1f"]["geometry"]["height_m"] == 5 and z["hall_2f"]["geometry"]["height_m"] == 6
    assert env_map(z["elec_1f"], "walls") == {"S": 130.0, "E": 180.0}                  # 26 x 5, 36 x 5
    assert z["hall_2f"]["name"] == "A홀" and z["office_1f"]["kind"] == "support"
    assert sc["it_load"]["design_kw"] == pytest.approx(16000 * 2 + 18000)
    assert sc["building"]["height_m"] == pytest.approx(17)


def test_missing_rack_kw_uses_input(parsed3f):
    shapes = [{**s, "attribs": {k: v for k, v in (s.get("attribs") or {}).items() if k != "KW"}} for s in parsed3f["shapes"]]
    b, sc = build({**parsed3f, "shapes": shapes}, rack_kw=30.0)
    assert sc["it_load"]["design_kw"] == pytest.approx(1200 * 30)
    assert "30 kW" in b["report"]["zones"][0]["it_source"]


def test_single_floor_drawing_unchanged():
    """접두어 없는 기존 도면 = 기존 결과, 층 접두어(F1-)가 하나뿐이어도 같은 결과"""
    sc = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    p = cad.load((CAD_DIR / "datacenter_dummy.dxf").read_bytes(), "datacenter_dummy.dxf")
    b = cad.build(p, roles_of(p), {}, sc, CoolingLayout().model_dump(), height_m=10)
    assert "zones" not in b and b["report"]["counts"] == {"racks": 320, "rows": 8, "crah": 10, "windows": 4}
    d = tempfile.mkdtemp()
    f = os.path.join(d, "s.dxf")
    make(f)
    doc = ezdxf.readfile(f)
    p0 = cad.load(open(f, "rb").read(), "s.dxf")
    for e in doc.modelspace():
        e.dxf.layer = "F1-" + e.dxf.layer
    doc.saveas(f)
    p1 = cad.load(open(f, "rb").read(), "s.dxf")
    b0 = cad.build(p0, roles_of(p0), {}, sc, CoolingLayout().model_dump())
    b1 = cad.build(p1, roles_of(p1), {}, sc, CoolingLayout().model_dump())
    assert b1["report"] == b0["report"] and b1["envelope"] == b0["envelope"] and b1["layout"] == b0["layout"]


def test_single_floor_over_multizone_scenario_becomes_single_zone(tmp_path):
    f = tmp_path / "s.dxf"
    make(str(f))
    p = cad.load(f.read_bytes(), "s.dxf")
    sc = load("scenario_haenam_ai_mz.json")
    lay = CoolingLayout.model_validate(sc["cooling_layout"]).model_dump()
    b = cad.build(p, roles_of(p), {}, sc, lay)
    out = Scenario.model_validate(cad.apply_to_scenario(sc, lay, b)).to_engine_dict()
    assert "zones" not in out and any("단일 존" in w for w in b["report"]["warnings"])


def test_cdu_and_plant_positions_from_drawing(parsed3f):
    """CDU 역할(층별 블록) · 칠러·냉각탑 폴리선 여러 개 → plant.*_positions (건물 좌표), 기본 배치 규칙과 같은 위치"""
    from hvac.building_view import plant_layout
    from hvac.multizone import simulate_building
    from hvac.plant_units import size_units
    assert cad.suggest_role("F2-M-CDU") == "cdu"
    b, sc = build(parsed3f)
    pl = sc["cooling_layout"]["plant"]
    assert b["report"]["counts"]["cdu"] == 18 and len(pl["cdu_positions"]) == 18
    assert {f: sum(c["floor"] == f for c in pl["cdu_positions"]) for f in (0, 1, 2)} == {0: 6, 1: 6, 2: 6}
    auto = plant_layout(ANSWER, ANSWER["cooling_layout"], size_units(ANSWER["cooling_layout"], simulate_building(ANSWER)))
    for key, ref in (("chiller_positions", auto["chiller"]), ("tower_positions", auto["tower"])):
        assert sorted(map(tuple, pl[key])) == pytest.approx(sorted(ref), abs=0.01), key
    assert sorted((c["floor"], c["x_m"], c["z_m"]) for c in pl["cdu_positions"]) == \
           pytest.approx(sorted((f, x, z) for f, _, x, z in auto["cdu"]), abs=0.01)
