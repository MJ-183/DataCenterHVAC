"""다중 존 건물 전체 보기 (Phase 18 연장, hvac/building_view.py) — 3층 더미 (scenario_3f_dummy_mz.json + 도면) 대조"""
import json
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from api.main import app
from hvac import DATA_DIR, cad
from hvac.plant import evaluate
from hvac.schemas import Scenario

client = TestClient(app)
DXF_3F = DATA_DIR.parents[1] / "cad" / "datacenter_3f_dummy.dxf"


def load(name):
    return Scenario.model_validate(json.loads((DATA_DIR / name).read_text(encoding="utf-8"))).to_engine_dict()


SC = load("scenario_3f_dummy_mz.json")


@pytest.fixture(scope="module")
def ev():
    r = client.post("/cooling/evaluate", json=SC)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def bv(ev):
    return ev["building_view"]


def test_floors_halls_racks(bv):
    assert [(f["floor"], f["elevation_m"], f["height_m"]) for f in bv["floors"]] == [(0, 0, 6), (1, 6, 6), (2, 12, 6)]
    assert [(h["zone"], len(h["racks"]), len(h["crah"])) for h in bv["halls"]] == \
           [("hall_1f", 400, 28), ("hall_2f", 400, 28), ("hall_3f", 400, 28)]
    for h in bv["halls"]:
        e = 6.0 * h["floor"]
        assert all(r["y"] == e for r in h["racks"]) and all(c["y"] == e for c in h["crah"])
        assert len(h["rack_inlet_c"]) == 24 and all(len(row) == 400 for row in h["rack_inlet_c"])
    assert len(bv["zones"]) == 7 and {z["id"]: z["elevation_m"] for z in bv["zones"]}["support_3f"] == 12


def test_rack_coordinates_match_drawing(bv):
    """랙 건물 좌표 = 도면 RACK_ID 블록 삽입점 (CAD X = x, CAD Y = -z), 0.01 m 이내"""
    p = cad.load(DXF_3F.read_bytes(), DXF_3F.name)
    dwg = {}
    for sh in p["shapes"]:
        if sh.get("block") == "RACK_600x1200x2200":
            xs, ys = [q[0] for q in sh["pts"]], [q[1] for q in sh["pts"]]
            dwg[sh["attribs"]["RACK_ID"]] = ((min(xs) + max(xs)) / 2, -(min(ys) + max(ys)) / 2)
    got = {r["id"]: (r["x"], r["z"]) for h in bv["halls"] for r in h["racks"]}
    assert len(got) == 1200 and got.keys() == dwg.keys()
    assert max(max(abs(got[k][0] - dwg[k][0]), abs(got[k][1] - dwg[k][1])) for k in got) <= 0.01


def test_plant_positions(bv, ev):
    pl = bv["plant"]
    assert (len(pl["chiller"]), len(pl["tower"]), len(pl["cdu"])) == (11, 14, 18)
    assert [ev["units"][k]["total"] for k in ("chiller", "tower", "cdu")] == [11, 14, 18]
    assert sum(u["standby"] for u in pl["chiller"]) == 1 and sum(u["standby"] for u in pl["tower"]) == 1
    xs, zs = [p[0] for p in bv["footprint"]], [p[1] for p in bv["footprint"]]
    for u in pl["chiller"] + pl["tower"]:                     # 건물 외곽 밖 (동측 야드)
        assert u["pos"][0] - u["size"][0] / 2 > max(xs)
    zones = {z["id"]: z for z in bv["zones"]}
    for c in pl["cdu"]:                                       # 같은 층 지원 존 안
        z = zones[c["zone"]]
        assert z["kind"] == "support" and z["floor"] == c["floor"]
        assert abs(c["pos"][0] - z["x_m"]) + c["size"][0] / 2 <= z["ew_m"] / 2
        assert abs(c["pos"][2] - z["z_m"]) + c["size"][2] / 2 <= z["ns_m"] / 2
        assert c["pos"][1] == pytest.approx(6.0 * c["floor"] + c["size"][1] / 2)
    assert {f: sum(c["floor"] == f for c in pl["cdu"]) for f in (0, 1, 2)} == {0: 6, 1: 6, 2: 6}
    assert pl["source"] == {"chiller": "auto", "tower": "auto", "cdu": "auto"}


def _components(segs, kind):
    adj = defaultdict(set)
    key = lambda p: tuple(round(c, 2) for c in p)
    for s in segs:
        if s["kind"] != kind:
            continue
        pts = [key(p) for p in s["points"]]
        for a, b in zip(pts, pts[1:]):
            adj[a].add(b)
            adj[b].add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x not in comp:
                comp.add(x)
                stack.extend(adj[x] - comp)
        seen |= comp
        comps.append(comp)
    return comps, key


def test_piping_connected_with_riser(bv):
    pp = bv["piping"]
    segs = pp["segments"]
    for kind in ("chw_supply", "chw_return", "cw_supply", "cw_return", "fws_supply", "fws_return"):
        comps, _ = _components(segs, kind)
        assert len(comps) == 1, (kind, len(comps))             # 끊긴 구간 없음
    comps, key = _components(segs, "chw_supply")
    nodes = comps[0]
    for inl in pp["inlets"]:
        assert key(inl["point"]) in nodes
    for u in bv["plant"]["chiller"]:                          # 칠러 서측 면 → 야드 헤더
        assert key((u["pos"][0] - u["size"][0] / 2, 1.0, u["pos"][2])) in nodes
    fws, _ = _components(segs, "fws_supply")
    for c in bv["plant"]["cdu"]:                              # CDU 상부까지
        assert key((c["pos"][0], c["pos"][1] + c["size"][1] / 2, c["pos"][2])) in fws[0]
    risers = [s for s in segs if s["part"] == "riser" and s["kind"] == "chw_supply"]
    top = max(p[1] for s in risers for p in s["points"])
    inlet3 = next(i for i in pp["inlets"] if i["zone"] == "hall_3f")["point"]
    assert top == pytest.approx(inlet3[1]) == pytest.approx(pp["riser"]["top_m"]) == pytest.approx(12 + 3.2)
    assert all(abs(s["points"][0][0] - s["points"][1][0]) < 1e-9 and abs(s["points"][0][2] - s["points"][1][2]) < 1e-9
               for s in risers)                               # 수직
    assert sorted(s["design_flow_m3_h"] for s in risers)[0] == pytest.approx(pp["chw_design_flow_m3_h"] / 3, rel=0.01)


def test_pump_head_includes_riser_friction(ev, bv):
    """양정 = 2 x (야드 + 입상관 + 층 분기 + 전산실 분기 마찰) + 증발기 + 코일. 입상관 정수두 0 (폐회로)"""
    pp, pc = bv["piping"], SC["cooling_layout"]["piping"]
    assert pp["chw_head_kpa"] == pytest.approx(pp["chw_friction_kpa"] + pc["evaporator_dp_kpa"] + pc["coil_dp_kpa"], abs=0.11)
    assert 0 < pp["riser_friction_kpa"] < pp["chw_friction_kpa"]
    assert ev["summary"]["chw_head_kpa"] == ev["piping"]["chw_head_kpa"] == pp["chw_head_kpa"]
    assert pp["critical_zone"] in {"hall_1f", "hall_2f", "hall_3f"}


def test_cad_positions_take_priority():
    sc = json.loads(json.dumps(SC))
    pl = sc["cooling_layout"]["plant"]
    pl["chiller_positions"] = [[80.0, -10.0], [80.0, 10.0]]
    pl["cdu_positions"] = [{"floor": 1, "x_m": 30.0, "z_m": 20.0}]
    bv = evaluate(sc, sc["cooling_layout"])["building_view"]
    assert [u["pos"][0] for u in bv["plant"]["chiller"]] == [80.0, 80.0]
    assert bv["plant"]["source"]["chiller"] == "cad" and bv["plant"]["cdu"][0]["zone"] == "support_2f"
    assert any("도면 칠러 2대" in n for n in bv["notes"])


def test_single_zone_has_no_building_view():
    sc = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    r = client.post("/cooling/evaluate", json=sc).json()
    assert "building_view" not in r and "zones" not in r
