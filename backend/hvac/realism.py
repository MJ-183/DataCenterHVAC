"""
운전 현실성 모델 (Phase 16). cooling_plant.realism 으로 단계별로 켜고 끈다. 전부 끄면(기본) 기존 단순 모델과 완전히 같다.

R1 제어 이력 (realism.control)
  단순 모델은 매 스텝 프리쿨링 수온 T_fc 만 보고 free/partial/chiller 를 정하므로, 습구가 전환점(T_fc = 냉수 공급온도)
  근처에서 흔들리면 칠러가 매시간 켜졌다 꺼진다. 현실성 모델은 루프별 칠러 운전 상태를 시간 순서로 들고 간다.
  - 켜짐: T_fc > 공급 + hys/2 이고 정지 후 chiller_min_off_h 가 지났을 때
  - 꺼짐: T_fc < 공급 - hys/2 이고 운전 후 chiller_min_on_h 가 지났을 때
  - 그 사이(이력 구간)나 최소 운전/정지 시간 중에는 직전 상태 유지
  - 칠러가 켜져 있으면 최소 부하 = 루프 부하 x chiller_min_plr 를 칠러가 맡음 (판형 열교환기 분담을 줄임 → 전력 증가)
    R2 에서 칠러 대수·용량이 들어오면 정격 용량 기준으로 바뀜
  - 칠러가 꺼져 있는데 T_fc > 공급이면 판형 열교환기만으로 운전하고 냉수 공급온도가 T_fc 까지 뜸 (supply_overshoot_k 로 표시)
  초기 상태: 다일·연간은 전날 마지막 상태를 이어받고, 단일일은 '전날도 같은 날씨' 가정으로 하루를 한 번 미리 돌려 정함
"""
import numpy as np

# 기본값 — 모두 가정값 (출처 없음, 작업 지시의 기본값). 보고서 가정값 목록에 포함
REALISM_DEFAULTS = {
    "control": False,
    "fc_hysteresis_k": 1.0,        # 프리쿨링 ↔ 칠러 전환 이력 폭 [K]
    "chiller_min_on_h": 2.0,       # 칠러 최소 운전 시간 [h]
    "chiller_min_off_h": 2.0,      # 칠러 최소 정지 시간 [h]
    "stage_min_interval_h": 1.0,   # 칠러 대수 변경 최소 간격 [h] (R2 대수 제어에서 사용)
    "chiller_min_plr": 0.2,        # 칠러 최소 부하율 [-] (R1 에서는 루프 부하 대비, R2 에서 정격 용량 대비)
}
ASSUMPTIONS = [
    ("fc_hysteresis_k", "프리쿨링 전환 이력 폭", "K", "가정값 (현장 BAS 설정값 확인 필요)"),
    ("chiller_min_on_h", "칠러 최소 운전 시간", "h", "가정값 (운전 관행, 제조사 압축기 보호 타이머와 별개)"),
    ("chiller_min_off_h", "칠러 최소 정지 시간", "h", "가정값"),
    ("stage_min_interval_h", "칠러 대수 변경 최소 간격", "h", "가정값"),
    ("chiller_min_plr", "칠러 최소 부하율", "-", "가정값 (R2 에서 참조 곡선 자료값으로 교체 예정)"),
]
REASONS = ("threshold", "hysteresis_hold", "min_on", "min_off", "staging")
_FREE = 1e9   # '오래전에 전환됨' (최소 시간 제약 없음)


def config(cp):
    """cooling_plant.realism → 기본값을 채운 dict (없으면 None = 단순 모델)"""
    r = cp.get("realism")
    if not r:
        return None
    return {**REALISM_DEFAULTS, **r}


def control_on(cp):
    rc = config(cp)
    return rc if rc and rc["control"] else None


# ---------- R1 제어: 스칼라 (엔진·plant.energy) ----------
def initial_state(t_fc0, supply):
    return {"on": t_fc0 > supply, "t": _FREE}


def control_step(state, t_fc, supply, ret, rc, dt_h):
    """한 스텝 → (프리쿨링 비율 x, 이유, 공급온도 상승 K, 새 상태). x 는 engine.free_fraction 과 같은 정의"""
    hys = rc["fc_hysteresis_k"] / 2.0
    on, t = state["on"], state["t"]
    switched = False
    if on:
        if t_fc < supply - hys:
            if t >= rc["chiller_min_on_h"] - 1e-9:
                on, switched, reason = False, True, "threshold"
            else:
                reason = "min_on"
        else:
            reason = "threshold" if t_fc > supply else "hysteresis_hold"
    else:
        if t_fc > supply + hys:
            if t >= rc["chiller_min_off_h"] - 1e-9:
                on, switched, reason = True, True, "threshold"
            else:
                reason = "min_off"
        else:
            reason = "threshold" if t_fc <= supply else "hysteresis_hold"
    raw = _raw_fraction(t_fc, supply, ret)
    if on:
        x, over = 1.0 - max(1.0 - raw, rc["chiller_min_plr"]), 0.0
    else:
        x, over = 1.0, max(t_fc - supply, 0.0)
    return x, reason, over, {"on": on, "t": dt_h if switched else t + dt_h}


def _raw_fraction(t_fc, s, r):
    return 1.0 if t_fc <= s else 0.0 if t_fc >= r else (r - t_fc) / (r - s)


def run_series(t_fcs, supply, ret, rc, dt_h, init=None):
    """시간 순서 T_fc 목록 → ([(x, reason, overshoot, on)], 마지막 상태, 초기화 설명, 시작 상태).
    init 이 없으면 같은 T_fc 를 한 번 미리 돌려 마지막 상태로 시작 ('전날도 같은 날씨' 가정)"""
    how = "carried"
    if init is None:
        st = initial_state(t_fcs[0], supply)
        for t in t_fcs:
            st = control_step(st, t, supply, ret, rc, dt_h)[3]
        init, how = st, "warmup"
    st, out = dict(init), []
    for t in t_fcs:
        x, why, over, st = control_step(st, t, supply, ret, rc, dt_h)
        out.append((x, why, over, st["on"]))
    return out, st, how, init


def switches(states, init_on):
    prev, n = init_on, 0
    for on in states:
        n += on != prev
        prev = on
    return n


# ---------- R1 제어: 벡터 (plant_vec, 일 x 시) ----------
def _step_vec(on, t, t_fc, s, r, rc, dt_h):
    hys = rc["fc_hysteresis_k"] / 2.0
    want_off = on & (t_fc < s - hys)
    want_on = ~on & (t_fc > s + hys)
    go_off = want_off & (t >= rc["chiller_min_on_h"] - 1e-9)
    go_on = want_on & (t >= rc["chiller_min_off_h"] - 1e-9)
    sw = go_off | go_on
    on = (on & ~go_off) | go_on
    raw = np.where(t_fc <= s, 1.0, np.where(t_fc >= r, 0.0, (r - t_fc) / np.maximum(r - s, 1e-9)))
    x = np.where(on, 1.0 - np.maximum(1.0 - raw, rc["chiller_min_plr"]), 1.0)
    return x, on, np.where(sw, dt_h, t + dt_h)


def _pass(t_fc, s, r, rc, on0, t0):
    D, H = t_fc.shape
    x = np.empty((D, H))
    on, t = on0.copy(), t0.copy()
    for h in range(H):
        x[:, h], on, t = _step_vec(on, t, t_fc[:, h], s[:, 0], r[:, 0], rc, 1.0)
    return x, on, t


def control_matrix(t_fc, s, r, rc, dates=None):
    """(D,24) T_fc → (D,24) 프리쿨링 비율. s, r 은 (D,1). 날짜가 하루씩 이어지는 행은 전날 마지막 상태를 이어받고
    (스칼라 순차 계산과 같은 결과가 나올 때까지 반복 — 상태 기억은 보통 몇 시간이라 2~3회면 수렴),
    끊긴 행(첫 행 포함)은 그날을 한 번 미리 돌려 초기화 (스칼라 run_series 와 같은 규칙)"""
    D = t_fc.shape[0]
    on0 = t_fc[:, 0] > s[:, 0]
    t0 = np.full(D, _FREE)
    _, on_w, t_w = _pass(t_fc, s, r, rc, on0, t0)    # 같은 날 미리 돌린 마지막 상태
    cont = np.zeros(D, dtype=bool)
    if dates is not None and D > 1:
        from datetime import date
        ds = [date.fromisoformat(str(d)) for d in dates]
        cont[1:] = [(b - a).days == 1 for a, b in zip(ds, ds[1:])]
    on_i, t_i = on_w.copy(), t_w.copy()
    x, on_e, t_e = _pass(t_fc, s, r, rc, on_i, t_i)
    if not cont.any():
        return x
    for _ in range(D):
        on_n, t_n = on_w.copy(), t_w.copy()
        on_n[1:] = np.where(cont[1:], on_e[:-1], on_w[1:])
        t_n[1:] = np.where(cont[1:], t_e[:-1], t_w[1:])
        if np.array_equal(on_n, on_i) and np.allclose(np.minimum(t_n, 1e6), np.minimum(t_i, 1e6)):
            break
        on_i, t_i = on_n, t_n
        x, on_e, t_e = _pass(t_fc, s, r, rc, on_i, t_i)
    return x
