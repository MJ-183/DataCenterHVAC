"""Phase 16 운전 현실성 — R1 제어 이력 (프리쿨링 전환 이력·칠러 최소 운전/정지 시간)"""
import copy
import json

import numpy as np
import pytest

from hvac import DATA_DIR, realism, simulate
from hvac.engine import simulate_days
from hvac.plant import energy, networks
from hvac.plant_vec import day_matrix, energy_matrix
from hvac.schemas import CoolingLayout, SimulationResult

R1 = {"control": True}


def sample():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def wobble(sc, amp=0.4, base=7.07, rh=95.0):
    """프리쿨링 전환점(T_fc = 냉수 공급 12℃ ↔ 습구 6.5℃) 근처에서 매시간 ±amp 흔들리는 날 — 단순 모델의 톱니 재현"""
    s = copy.deepcopy(sc)
    s["weather"]["dry_bulb_c"] = [base + (amp if h % 2 else -amp) for h in range(24)]
    s["weather"]["rh_pct"] = [rh] * 24
    return s


def with_r1(sc, **kw):
    s = copy.deepcopy(sc)
    s["cooling_plant"]["realism"] = {**R1, **kw}
    return s


def modes(res):
    return [x["plant"]["mode"] for x in res["hourly"]]


def test_off_is_identical_to_simple_model():
    sc = wobble(sample())
    off = copy.deepcopy(sc)
    off["cooling_plant"]["realism"] = {"control": False}
    assert simulate(off) == simulate(sc)
    assert "control" not in simulate(sc) and "mode_reason" not in simulate(sc)["hourly"][0]["plant"]


def test_hysteresis_removes_sawtooth():
    sc = wobble(sample())
    simple = modes(simulate(sc))
    flips = sum(a != b for a, b in zip(simple, simple[1:]))
    assert flips >= 20                                       # 단순 모델: 매시간 free ↔ partial
    res = simulate(with_r1(sc))
    m = modes(res)
    assert sum(a != b for a, b in zip(m, m[1:])) == 0          # 이력 폭 1K 안에서 흔들림 → 상태 유지
    assert res["control"]["chiller_switches"]["air"] == 0
    reasons = {x["plant"]["mode_reason"] for x in res["hourly"]}
    assert "hysteresis_hold" in reasons
    SimulationResult.model_validate(res)                      # 새 출력 필드가 스키마에 정의돼 있음


def test_min_on_holds_chiller_at_min_load():
    """칠러가 켜진 직후 외기가 크게 떨어져도 최소 운전 시간 동안은 최소 부하로 운전 (전력 증가가 정상)"""
    sc = sample()
    t = [7.07 + 3.0] * 3 + [7.07 - 3.0] * 21       # 3시간 따뜻 → 급랭
    sc["weather"]["dry_bulb_c"], sc["weather"]["rh_pct"] = t, [95.0] * 24
    r = simulate(with_r1(sc, chiller_min_on_h=4.0), init_state={"air": {"on": False, "t": 99.0}})
    hs = r["hourly"]
    on = [x["plant"]["air_loop"]["chiller_on"] for x in hs]
    assert on[:4] == [True] * 4 and on[4] is False             # 0시 켜짐 → 0~3시 4시간 운전 후 4시에 꺼짐
    assert [x["plant"]["mode_reason"] for x in hs[3:4]] == ["min_on"]
    assert hs[3]["plant"]["air_loop"]["free_fraction"] == pytest.approx(0.8)   # 최소 부하율 0.2
    assert hs[3]["chiller"]["power_kw"] > simulate(sc)["hourly"][3]["chiller"]["power_kw"]


def test_min_off_floats_supply():
    sc = sample()
    t = [7.07 - 3.0] * 2 + [7.07 + 3.0] * 22       # 정지 직후 따뜻해짐 → 최소 정지 시간 동안 판형만 (공급온도 상승)
    sc["weather"]["dry_bulb_c"], sc["weather"]["rh_pct"] = t, [95.0] * 24
    r = simulate(with_r1(sc, chiller_min_off_h=4.0), init_state={"air": {"on": False, "t": 0.0}})
    a = [x["plant"]["air_loop"] for x in r["hourly"]]
    assert a[2]["reason"] == "min_off" and a[2]["chiller_on"] is False and a[2]["supply_overshoot_k"] > 2
    assert a[4]["chiller_on"] is True and a[4]["reason"] == "threshold"


def test_subhour_steps_keep_state_per_step():
    sc = wobble(sample())
    r = simulate(with_r1(sc), 15)
    assert len(r["hourly"]) == 96 and r["control"]["chiller_switches"]["air"] == 0
    assert "미리 돌려" in r["control"]["notes"][0]
    on = [x["plant"]["air_loop"]["chiller_on"] for x in simulate(with_r1(sc, chiller_min_on_h=0.5), 15,
                                                                   {"air": {"on": True, "t": 99.0}})["hourly"]]
    assert on[:2] == [True, True]                              # 15분 스텝: 최소 운전 0.5h = 2스텝 유지


def test_multi_day_carries_previous_state():
    sc = with_r1(wobble(sample()))
    days = []
    for d in range(3):
        s = copy.deepcopy(sc)
        s["site"]["date"] = f"2026-07-{15 + d}"
        days.append(s)
    rs = simulate_days(days)
    assert "미리 돌려" in rs[0]["control"]["notes"][0] and "이어받음" in rs[1]["control"]["notes"][0]


@pytest.mark.parametrize("arch", ["air_chw", "hybrid_dlc"])
def test_vector_matches_scalar_with_control(arch):
    """plant.energy(스칼라, 단일일 = 미리 돌려 초기화) == energy_matrix(벡터, 끊긴 날짜 = 같은 규칙)"""
    sc = with_r1(sample())
    sc["cooling_plant"]["architecture"] = arch
    cfg = CoolingLayout().model_dump()
    days = []
    for k, (base, amp) in enumerate([(7.07, 0.4), (9.0, 2.5), (4.0, 3.0), (12.0, 0.6)]):
        s = wobble(sc, amp, base)
        s["site"]["date"] = f"2026-0{k + 1}-10"   # 이어지지 않는 날짜
        days.append(simulate(s, 60))
    dm = day_matrix(days)
    for cs, cr in ((12.0, 18.0), (10.0, 16.0), (14.0, 22.0)):
        nets = networks(sc, cfg, days[0], cr - cs)
        v = energy_matrix(dm, sc, cfg, 20.0, cs, cr, nets)
        for d, res in enumerate(days):
            hrs, _ = energy(sc, cfg, res, 20.0, cs, cr, nets)
            assert np.allclose(v["free_fraction"][d], [h["free_fraction"] for h in hrs], atol=6e-4)
            assert np.allclose(v["total"][d], [h["total_kw"] for h in hrs], atol=0.051)


def test_control_matrix_carry_equals_sequential():
    """이어지는 날짜: 벡터 반복 계산 == 스칼라로 전날 상태를 넘기며 순서대로 계산"""
    rng = np.random.default_rng(3)
    D = 20
    t_fc = 12.0 + np.cumsum(rng.normal(0, 0.7, (D, 24)), axis=1) * 0.5
    rc = {**realism.REALISM_DEFAULTS, "control": True, "chiller_min_on_h": 3.0, "chiller_min_off_h": 3.0}
    dates = [f"2026-03-{d + 1:02d}" for d in range(D)]
    x = realism.control_matrix(t_fc, np.full((D, 1), 12.0), np.full((D, 1), 18.0), rc, dates)
    st, seq = None, []
    for d in range(D):
        out, st, _, _ = realism.run_series(list(t_fc[d]), 12.0, 18.0, rc, 1.0, st)
        seq.append([c[0] for c in out])
    assert np.allclose(x, np.array(seq))
