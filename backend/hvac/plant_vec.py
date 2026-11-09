"""
벡터화 연성 에너지 모델 (Phase 10) - plant.energy() 와 같은 식을 (일 x 시) numpy 배열로 계산.

연간 최적화·내일 예측·투자 검토는 '같은 구조, 운전점 수백 개 x 365일' 을 평가하므로 스칼라 반복 대신 이 모듈을 쓴다.
식은 plant.energy 와 1:1 대응 (무충돌 원칙 C8, tests/test_plant_vec.py 패리티 테스트). 차이는 시간별 반올림을 하지 않는 것뿐.
운전점(fan_kw, chw_s, chw_r)은 스칼라 또는 (D,) 배열 → 월마다 다른 운전점을 한 번에 평가할 수 있다.
"""
from dataclasses import dataclass

import numpy as np

from . import realism
from .engine import CP_WATER, RHO_WATER, plant_param


@dataclass
class DayMatrix:
    dates: list
    air_load: np.ndarray     # (D,24) 엔진 공기 루프 부하 [kW]
    fan_engine: np.ndarray   # 엔진 CRAH 팬 발열 (기류 모델 팬 전력으로 교체됨)
    t_fc: np.ndarray         # 프리쿨링 수온 [℃]
    wet_bulb: np.ndarray
    liq_kw: np.ndarray       # 액체 루프 보조 칠러 + 펌프 전력
    liq_heat: np.ndarray     # 액체 루프 방열 (냉각수로)
    it_kw: np.ndarray
    tower_kw: np.ndarray     # 엔진 냉각탑 팬 (연성 모델 합계에는 없음, 투자 분석에서 더함)
    makeup_m3: np.ndarray    # 엔진 보급수 [m3/h]


def day_matrix(results, dates=None):
    """results: engine.simulate(.., 60) 결과 리스트 (같은 냉각 방식)"""
    def arr(f):
        return np.array([[f(x) for x in r["hourly"]] for r in results], dtype=float)

    def liq(x):
        L = x["plant"]["liquid_loop"]
        if not L:
            return 0.0, 0.0
        ch = L["chiller_load_kw"] / L["chiller_cop"]
        return ch + x["plant"]["power_kw"]["liquid_pump"], L["load_kw"] + ch
    lq = [[liq(x) for x in r["hourly"]] for r in results]
    return DayMatrix(
        dates=dates or [r["date"] for r in results],
        air_load=arr(lambda x: x["plant"]["air_loop"]["load_kw"]),
        fan_engine=arr(lambda x: x["load_kw"]["crah_fan"]),
        t_fc=arr(lambda x: x["plant"]["free_cooling_water_c"]),
        wet_bulb=arr(lambda x: x["weather"]["wet_bulb_c"]),
        liq_kw=np.array([[a for a, _ in d] for d in lq], dtype=float),
        liq_heat=np.array([[b for _, b in d] for d in lq], dtype=float),
        it_kw=arr(lambda x: x["load_kw"]["it"]),
        tower_kw=arr(lambda x: x["plant"]["power_kw"]["tower_fan"]),
        makeup_m3=arr(lambda x: x["makeup_water_m3_h"]["total"]),
    )


def _col(v, d):
    """스칼라 / (D,) → (D,1) 브로드캐스트"""
    a = np.asarray(v, dtype=float)
    return a.reshape(-1, 1) if a.ndim == 1 else np.full((d, 1), float(a))


def chiller_cop_vec(cp, wet_bulb, supply_c):
    wb = np.clip(1 + cp["cop_wb_sensitivity"] * (cp["design_wet_bulb_c"] - wet_bulb), 0.6, 1.3)
    sup = 1 + plant_param(cp, "cop_supply_sensitivity") * (supply_c - plant_param(cp, "cop_ref_supply_c"))
    return cp["chiller_cop_rated"] * wb * np.clip(sup, 0.7, 1.6)


def free_fraction_vec(t_fc, t_s, t_r):
    x = (t_r - t_fc) / np.maximum(t_r - t_s, 1e-9)
    return np.where(t_fc <= t_s, 1.0, np.where(t_fc >= t_r, 0.0, x))


def energy_matrix(dm, sc, cfg, fan_kw, chw_s, chw_r, nets):
    """plant.energy 와 같은 식. 반환: 항목별 (D,24) kW 배열 dict (+ 'total')"""
    cp, pc = sc["cooling_plant"], cfg["piping"]
    D = dm.air_load.shape[0]
    fan, s, r = _col(fan_kw, D), _col(chw_s, D), _col(chw_r, D)
    dt_cw = cp["cw_return_c"] - cp["cw_supply_c"]
    eff = pc["pump_efficiency"]
    chw_head, cw = nets["chw"]["head_kpa"], nets["cw"]
    q_air = np.maximum(dm.air_load - dm.fan_engine + fan, 0.0)
    chw_q = q_air / (CP_WATER * (r - s)) / RHO_WATER
    p_chw = chw_q * (chw_head * (chw_q / nets["chw_q_design"]) ** 2) / eff
    load = q_air + p_chw
    rc = realism.control_on(cp)
    if not plant_param(cp, "free_cooling"):
        x_fc = np.zeros_like(load)
    elif rc:   # R1 제어 이력 — 이어지는 날짜는 전날 상태를 이어받음 (스칼라 순차 계산과 같은 결과)
        x_fc = realism.control_matrix(dm.t_fc, np.broadcast_to(s, (D, 1)), np.broadcast_to(r, (D, 1)), rc, dm.dates)
    else:
        x_fc = free_fraction_vec(dm.t_fc, s, r)
    cop = chiller_cop_vec(cp, dm.wet_bulb, s)
    p_ch = load * (1 - x_fc) / cop
    cw_q = (load + p_ch + dm.liq_heat) / (CP_WATER * dt_cw) / RHO_WATER
    cw_head = cw["dynamic_kpa"] * (cw_q / nets["cw_q_design"]) ** 2 + cw["static_kpa"]
    p_cw = cw_q * cw_head / eff
    fan_b = np.broadcast_to(fan, load.shape)
    total = p_ch + fan_b + p_chw + p_cw + dm.liq_kw
    return {"chiller": p_ch, "crah_fan": fan_b, "chw_pump": p_chw, "cw_pump": p_cw, "liquid": dm.liq_kw,
            "total": total, "free_fraction": x_fc, "cooling": load}
