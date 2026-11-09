"""AI 기능 테스트 - 실제 OpenAI 호출 없이 가짜 LLM 으로 검증"""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from api import ai
from api.main import app
from hvac import DATA_DIR, simulate
from hvac.insights import compute_facts, unverified_numbers
from hvac.scenario_patch import FieldChange, PatchError, ScenarioEdit, ValidationError, apply_changes, numeric_paths

client = TestClient(app)


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


class FakeLLM:
    model = "fake-model"

    def __init__(self, text="", edit=None):
        self._text, self._edit, self.calls = text, edit, []

    def text(self, system, user):
        self.calls.append((system, user))
        return self._text

    def parse(self, system, user, schema):
        self.calls.append((system, user))
        return self._edit


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch, tmp_path):
    """실제 backend/.env(사용자 키)를 읽지 않도록"""
    monkeypatch.setattr(ai, "ENV_PATH", tmp_path / ".env")


def test_env_file_read_each_call(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=\nOPENAI_MODEL=\n", encoding="utf-8")
    assert client.get("/ai/status").json()["enabled"] is False
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-x\nOPENAI_MODEL=m1\n", encoding="utf-8")
    assert client.get("/ai/status").json() == {"enabled": True, "model": "m1", "reason": None}


@pytest.fixture
def fake():
    holder = {}

    def use(llm):
        holder["llm"] = llm
        app.dependency_overrides[ai.get_llm] = lambda: llm
        return llm
    yield use
    app.dependency_overrides.clear()


# ---------- 키 없음 ----------
def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    st = client.get("/ai/status").json()
    assert st["enabled"] is False and "OPENAI_API_KEY" in st["reason"]
    res = client.post("/simulate", json=sample()).json()
    assert client.post("/explain", json=res).status_code == 503
    assert client.post("/scenario/parse", json={"text": "x", "scenario": sample()}).status_code == 503
    assert client.get("/health").status_code == 200        # 나머지 앱은 정상


def test_status_never_exposes_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-123")
    monkeypatch.setenv("OPENAI_MODEL", "some-model")
    body = client.get("/ai/status").text
    assert body and "sk-secret" not in body
    assert client.get("/ai/status").json() == {"enabled": True, "model": "some-model", "reason": None}


def test_strict_schema_generation():
    """OpenAI 구조화 출력이 받아들이는 strict JSON 스키마로 변환 가능해야 함"""
    from openai.lib._pydantic import to_strict_json_schema
    s = to_strict_json_schema(ScenarioEdit)
    assert s["additionalProperties"] is False


# ---------- 해설 ----------
def test_facts_hand_check():
    r = simulate(sample())
    f = compute_facts(r)
    peak = max(r["hourly"], key=lambda x: x["load_kw"]["total_cooling"])
    assert f["cooling_load_kw"]["peak"] == {"time": f"{peak['hour']:02d}:00", "value": peak["load_kw"]["total_cooling"]}
    assert abs(sum(f["load_group_share_pct"].values()) - 100) < 0.3
    assert f["daily_total"] == r["daily_total"]


def test_unverified_numbers():
    r = simulate(sample())
    f = compute_facts(r)
    peak = f["cooling_load_kw"]["peak"]
    it_share = f["load_group_share_pct"]["IT"]
    ok = f"피크는 {peak['time']} {peak['value']:,} kW, IT 비중 {it_share}%, 일 보급수 {r['daily_total']['makeup_m3']} m³"
    assert unverified_numbers(ok, f, r) == []
    assert unverified_numbers("칠러 전력은 9999.9 kW", f, r) == ["9999.9"]


def test_explain_endpoint(fake):
    res = client.post("/simulate", json=sample()).json()
    llm = fake(FakeLLM(text="- 일 냉방량 43,215 kWh\n- 근거 없는 값 123.45"))
    r = client.post("/explain", json=res)
    assert r.status_code == 200
    body = r.json()
    assert body["unverified_numbers"] == ["123.45"]
    system, user = llm.calls[0]
    assert "없는 값은 추정하지 말 것" in system
    assert "facts" in user


# ---------- 시나리오 변경분 ----------
def test_apply_multiply_and_wildcard():
    sc = sample()
    new, applied = apply_changes(sc, [
        FieldChange(path="it_load.design_kw", op="multiply", value=1.2, hours=[], reason="20% 증설"),
        FieldChange(path="envelope.walls.*.u_value", op="set", value=0.2, hours=[], reason="단열"),
    ])
    assert new["it_load"]["design_kw"] == 2400
    assert all(w["u_value"] == 0.2 for w in new["envelope"]["walls"])
    assert len(applied) == 1 + len(sc["envelope"]["walls"])
    assert sc["it_load"]["design_kw"] == 2000                   # 원본 불변


def test_apply_hourly_array():
    new, applied = apply_changes(sample(), [
        FieldChange(path="weather.dry_bulb_c", op="add", value=2, hours=[13, 14], reason="+2℃")])
    assert [a.path for a in applied] == ["weather.dry_bulb_c.13", "weather.dry_bulb_c.14"]
    assert new["weather"]["dry_bulb_c"][13] == sample()["weather"]["dry_bulb_c"][13] + 2


@pytest.mark.parametrize("ch,err", [
    (FieldChange(path="it_load.nope", op="set", value=1, hours=[], reason=""), PatchError),
    (FieldChange(path="site.name", op="set", value=1, hours=[], reason=""), PatchError),
    (FieldChange(path="it_load.design_kw", op="set", value=1, hours=[3], reason=""), PatchError),
    (FieldChange(path="it_load.utilization", op="set", value=1, hours=[24], reason=""), PatchError),
    (FieldChange(path="cooling_plant.chw_return_c", op="set", value=10, hours=[], reason=""), ValidationError),
    (FieldChange(path="cooling_plant.tower_cycles_of_concentration", op="set", value=1, hours=[], reason=""),
     ValidationError),
])
def test_apply_rejects(ch, err):
    with pytest.raises(err):
        apply_changes(sample(), [ch])


def test_numeric_paths():
    p = numeric_paths(sample())
    assert "it_load.design_kw" in p and "it_load.utilization" in p and "envelope.walls.0.u_value" in p
    assert "site.name" not in p and not any(x.startswith("_") for x in p)


def test_parse_endpoint_ok_then_simulated_by_engine(fake):
    fake(FakeLLM(edit=ScenarioEdit(
        changes=[FieldChange(path="it_load.design_kw", op="multiply", value=1.5, hours=[], reason="50% 증설")],
        unsupported=["칠러 1대 추가"], summary="IT 50% 증설")))
    r = client.post("/scenario/parse", json={"text": "IT 50% 증설, 칠러 1대 추가", "scenario": sample()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changes"] == [{"path": "it_load.design_kw", "old": 2000, "new": 3000, "reason": "50% 증설"}]
    assert body["unsupported"] == ["칠러 1대 추가"]
    assert body["scenario"]["_note"] == sample()["_note"]
    sim = client.post("/simulate", json=body["scenario"]).json()
    assert sim["hourly"][0]["load_kw"]["it"] == 3000 * sample()["it_load"]["utilization"][0]


def test_parse_endpoint_validation_error(fake):
    fake(FakeLLM(edit=ScenarioEdit(
        changes=[FieldChange(path="cooling_plant.chw_supply_c", op="set", value=20, hours=[], reason="")],
        unsupported=[], summary="")))
    r = client.post("/scenario/parse", json={"text": "냉수 20도", "scenario": sample()})
    assert r.status_code == 422
    assert "검증" in r.json()["detail"]["message"]


def test_explain_subhourly_sends_hourly_only(fake):
    res = client.post("/simulate?step_min=5", json=sample()).json()
    llm = fake(FakeLLM(text="ok"))
    assert client.post("/explain", json=res).status_code == 200
    user = llm.calls[0][1]
    assert '"minute": 5' not in user and '"minute": 0' in user   # 정시만 전달
    f = compute_facts(res)
    hourly_peak = compute_facts(simulate(sample()))["cooling_load_kw"]["peak"]["value"]
    assert f["cooling_load_kw"]["peak"]["value"] >= hourly_peak  # 5분 해상도 피크 >= 정시 피크
