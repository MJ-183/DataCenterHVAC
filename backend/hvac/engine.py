"""
데이터센터 HVAC 시뮬레이터 - Phase 1 계산 엔진 (단일 존, 1시간 x 24 스텝)

입력: scenario JSON (scenario_sample.json 참고)
출력: 시간별 부하 구성 / 냉수 / 냉각수 / 보급수 / 칠러 전력 + 일 합계

단순화 가정 (Phase 5에서 고도화 대상):
- 단일 존, 정상상태(축열 무시), 실내 온습도 고정
- 외벽·지붕: 상당외기온도(sol-air) 방식, 장파복사 보정 없음
- 일사 분리: Erbs 모델, 경사면 확산은 등방성 모델
- 칠러 COP: 정격 COP를 외기 습구온도와 공급수온으로 선형 보정 (실제 장비 곡선 아님, 부분부하 효율 미반영)

냉각 방식 (Phase 3.5, cooling_plant.architecture):
- air_chw    : 전 발열 -> CRAH(공기) -> 냉수(CHW) -> 칠러 -> 냉각수 -> 냉각탑   (기존 모델, 기본값)
- hybrid_dlc : IT 발열 x liquid_fraction 은 냉각판 -> TCS -> CDU -> 건물측 냉각수(FWS) 로,
               나머지는 air_chw 와 같은 공기 경로로
- immersion  : IT 발열 x immersion_capture 는 액침 냉각액 -> CDU -> FWS 로, 나머지는 공기 경로로
  액체로 회수된 열은 CRAH 를 거치지 않으므로 CRAH 팬 발열도 공기측 부하에만 비례한다.
- 프리쿨링(수측 이코노마이저): 냉각탑 출구 수온 = 외기 습구 + tower_approach_k, 판형 열교환기를 거친 수온
  T_fc = 냉각탑 출구 + hx_approach_k. 루프별로 T_fc <= 공급온도면 칠러 OFF(free), 공급 < T_fc < 환수면
  환수를 T_fc 까지 예냉하는 부분 프리쿨링(partial, 비율 = (환수 - T_fc)/(환수 - 공급)), 그 외 칠러(chiller).
- 보조동력(펌프·냉각탑 팬)은 처리 열량에 비례하는 계수(kW/kW)로 근사. PUE = (IT + UPS 손실 + 조명 + 냉방 설비)/IT,
  WUE = 냉각탑 보급수[L] / IT 전력량[kWh]. 변압기 손실 등 기타 설비 전력과 서버 팬 절감(액침)은 미반영.
- 신규 계수는 모두 가정값이며 PLANT_DEFAULTS 에 모여 있다 (시나리오에 없으면 이 값을 사용).
- 서브시간 계산(step_min < 60): 시간별 입력(기상·가동률)은 정시 값 사이를 선형 보간(가정),
  23시 이후는 0시 값으로 보간(같은 설계일이 반복된다고 가정). 태양 위치는 실제 분 단위 시각으로 계산.
  각 스텝의 계산식은 1시간 계산과 동일하며, 일 합계는 sum(값 x 스텝길이[h]).
"""
import copy
import json
import math
import sys
from pathlib import Path

from . import realism

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STEP_MINUTES = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)  # 허용 계산 간격 [분] (60의 약수)

CP_WATER = 4.186      # kJ/kg.K
RHO_WATER = 1000.0    # kg/m3 (근사)
RHO_AIR = 1.2         # kg/m3
H_FG = 2430.0         # kJ/kg, 냉각탑 증발잠열 근사
P_ATM = 101.325       # kPa
SOLAR_CONST = 1367.0  # W/m2
GROUND_REFL = 0.2
H_OUT = 17.0          # W/m2.K, 외표면 열전달계수

ORIENT_AZ = {"S": 0.0, "W": 90.0, "N": 180.0, "E": -90.0}  # 남=0, 서=+

ARCHITECTURES = ("air_chw", "hybrid_dlc", "immersion")
# 냉각 방식·프리쿨링·보조동력 계수 기본값 - 모두 가정값 (실측·제조사 자료로 교체 필요).
# 시나리오 cooling_plant 에 키가 없으면 이 값을 쓴다 (기존 저장 시나리오 호환).
PLANT_DEFAULTS = {
    "architecture": "air_chw",
    "liquid_fraction": 0.75,         # DLC 냉각판이 회수하는 IT 발열 비율 [-]
    "immersion_capture": 0.95,       # 액침 냉각액이 회수하는 IT 발열 비율 [-]
    "tcs_supply_c": 32.0,            # TCS(서버측 2차 루프) 공급온도 [℃] (ASHRAE W32 급)
    "tcs_return_c": 42.0,            # TCS 환수온도 [℃]
    "cdu_approach_k": 3.0,           # CDU 열교환기 접근온도 [K]: FWS 공급 = TCS 공급 - 접근온도
    "free_cooling": True,            # 수측 이코노마이저(판형 열교환기) 설치 여부
    "tower_approach_k": 4.0,         # 냉각탑 출구 수온 - 외기 습구 [K]
    "hx_approach_k": 1.5,            # 프리쿨링 판형 열교환기 접근온도 [K]
    "cop_supply_sensitivity": 0.03,  # 공급수온 1℃ 상승당 칠러 COP 증가율 [-/K]
    "cop_ref_supply_c": 12.0,        # 정격 COP 기준 공급수온 [℃]
    "chw_pump_kw_per_kw": 0.015,     # 냉수 펌프 전력 / 공기측 냉방량 [kW/kW]
    "cw_pump_kw_per_kw": 0.012,      # 냉각수 펌프 전력 / 냉각탑 방열량 [kW/kW]
    "liquid_pump_kw_per_kw": 0.02,   # TCS+FWS(CDU) 펌프 전력 / 액체측 냉방량 [kW/kW]
    "tower_fan_kw_per_kw": 0.006,    # 냉각탑 팬 전력 / 방열량 [kW/kW]
}


def plant_param(cp, key):
    return cp.get(key, PLANT_DEFAULTS[key])


def liquid_capture(cp):
    """IT 발열 중 액체 루프로 회수되는 비율 (air_chw = 0)"""
    a = plant_param(cp, "architecture")
    return {"air_chw": 0.0, "hybrid_dlc": plant_param(cp, "liquid_fraction"),
            "immersion": plant_param(cp, "immersion_capture")}[a]


def chiller_cop(cp, wet_bulb_c, supply_c):
    """정격 COP x 습구 보정(0.6~1.3) x 공급수온 보정(0.7~1.6). 공급수온 = 기준이면 기존 습구 보정과 동일"""
    wb = min(max(1 + cp["cop_wb_sensitivity"] * (cp["design_wet_bulb_c"] - wet_bulb_c), 0.6), 1.3)
    sup = 1 + plant_param(cp, "cop_supply_sensitivity") * (supply_c - plant_param(cp, "cop_ref_supply_c"))
    return cp["chiller_cop_rated"] * wb * min(max(sup, 0.7), 1.6)


def free_fraction(t_fc, t_supply, t_return):
    """프리쿨링으로 처리하는 열량 비율. 1=칠러 OFF, 0=칠러 단독, 그 사이 = 환수 예냉(부분)"""
    if t_fc <= t_supply:
        return 1.0
    if t_fc >= t_return:
        return 0.0
    return (t_return - t_fc) / (t_return - t_supply)


def plant_mode(loops):
    """루프 (부하, 프리쿨링 비율) 목록 → 설비 모드 (부하가 있는 루프만 봄)"""
    active = [x for q, x in loops if q > 1e-9]
    return ("free" if active and all(x >= 1 for x in active)
            else "chiller" if all(x <= 0 for x in active) else "partial")


def loop_mode(x):
    return "free" if x >= 1.0 else "partial" if x > 0.0 else "chiller"


# ---------- 습공기 ----------
def p_sat(t):
    return 0.61094 * math.exp(17.625 * t / (t + 243.04))  # kPa


def humidity_ratio(t, rh):
    pv = rh / 100.0 * p_sat(t)
    return 0.622 * pv / (P_ATM - pv)


def enthalpy(t, rh):
    w = humidity_ratio(t, rh)
    return 1.006 * t + w * (2501.0 + 1.86 * t)  # kJ/kg dry air


def wet_bulb(t, rh):
    """Stull(2011) 근사식"""
    return (t * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(t + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


# ---------- 태양 ----------
def day_of_year(date_str):
    y, m, d = (int(x) for x in date_str.split("-"))
    cum = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    leap = 1 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) and m > 2 else 0
    return cum[m - 1] + d + leap


def solar_geometry(n, clock_hour, lat, lon, std_mer):
    b = math.radians(360.0 * (n - 81) / 364.0)
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)  # min
    solar_time = clock_hour + (4.0 * (lon - std_mer) + eot) / 60.0
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0)))
    phi = math.radians(lat)
    omega = math.radians(15.0 * (solar_time - 12.0))
    cos_z = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(omega)
    cos_z = max(-1.0, min(1.0, cos_z))
    zen = math.acos(cos_z)
    if math.sin(zen) < 1e-6:
        az = 0.0
    else:
        c = (cos_z * math.sin(phi) - math.sin(decl)) / (math.sin(zen) * math.cos(phi))
        az = math.copysign(math.acos(max(-1.0, min(1.0, c))), omega)
    return cos_z, zen, az, n


def split_ghi(ghi, cos_z, n):
    """Erbs 모델로 GHI -> DNI, DHI"""
    if ghi <= 0:
        return 0.0, 0.0
    if cos_z < 0.05:
        return 0.0, ghi
    i0h = SOLAR_CONST * (1 + 0.033 * math.cos(math.radians(360.0 * n / 365.0))) * cos_z
    kt = min(ghi / i0h, 1.0)
    if kt <= 0.22:
        kd = 1 - 0.09 * kt
    elif kt <= 0.80:
        kd = 0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4
    else:
        kd = 0.165
    dhi = kd * ghi
    dni = (ghi - dhi) / cos_z
    return dni, dhi


def incident_vertical(ghi, dni, dhi, zen, sun_az, surf_az_deg):
    cos_theta = math.sin(zen) * math.cos(sun_az - math.radians(surf_az_deg))
    beam = dni * max(cos_theta, 0.0)
    return beam + dhi * 0.5 + ghi * GROUND_REFL * 0.5


# ---------- 시뮬레이션 ----------
def interp_hourly(arr, t):
    """시간별 24개 값을 시각 t[h] 에서 선형 보간 (23~24시는 0시 값으로 순환). 정시면 원래 값 그대로"""
    i = int(t)
    f = t - i
    if f == 0:
        return arr[i % 24]
    return arr[i % 24] + (arr[(i + 1) % 24] - arr[i % 24]) * f


def _fc_series(sc, step_min, pp):
    """스텝별 프리쿨링 수온 T_fc (R1 제어를 시간 순서로 미리 돌리는 데 사용 — 본 계산과 같은 식)"""
    wx = sc["weather"]
    out = []
    for k in range(24 * 60 // step_min):
        h = k * step_min / 60.0
        twb = wet_bulb(interp_hourly(wx["dry_bulb_c"], h), interp_hourly(wx["rh_pct"], h))
        out.append(twb + pp["tower_approach_k"] + pp["hx_approach_k"])
    return out


REASON_RANK = {"min_on": 4, "min_off": 3, "staging": 2, "hysteresis_hold": 1, "threshold": 0}


def simulate(sc, step_min=60, init_state=None):
    """단일 존이면 simulate_zone, zones(다중 존, 적층 건물)가 있으면 존별 simulate_zone 결과를 합산 (hvac/multizone.py)"""
    if sc.get("zones"):
        from .multizone import simulate_building
        return simulate_building(sc, step_min, init_state)
    return simulate_zone(sc, step_min, init_state)


def simulate_zone(sc, step_min=60, init_state=None):
    """step_min: 계산 간격 [분], 60의 약수. 60 이면 기존 1시간 x 24 스텝 결과와 동일.
    init_state: 현실성 R1 제어의 시작 상태 {"air": .., "liquid": ..} (전날 결과의 control.final_state).
    없으면 같은 날을 한 번 미리 돌려 초기화. realism.control 이 꺼져 있으면 쓰지 않는다"""
    if step_min not in STEP_MINUTES:
        raise ValueError("step_min 은 60의 약수(1~60)여야 합니다")
    site, wx, ind = sc["site"], sc["weather"], sc["indoor"]
    env, it, intl = sc["envelope"], sc["it_load"], sc["internal"]
    vent, cp = sc["ventilation"], sc["cooling_plant"]

    n = day_of_year(site["date"])
    t_in, rh_in = ind["temp_c"], ind["rh_pct"]
    h_in = enthalpy(t_in, rh_in)
    dt_chw = cp["chw_return_c"] - cp["chw_supply_c"]
    dt_cw = cp["cw_return_c"] - cp["cw_supply_c"]
    coc = cp["tower_cycles_of_concentration"]
    pp = {k: plant_param(cp, k) for k in PLANT_DEFAULTS}
    arch = pp["architecture"]
    phi = liquid_capture(cp)
    liquid = arch != "air_chw"
    fws_s = pp["tcs_supply_c"] - pp["cdu_approach_k"]       # 건물측 냉각수(FWS) 공급/환수
    fws_r = pp["tcs_return_c"] - pp["cdu_approach_k"]

    dt_h = step_min / 60.0
    rc = realism.control_on(cp)
    ctrl = None
    if rc:   # R1: 루프별 칠러 운전 상태를 시간 순서로 (이력·최소 운전/정지 시간)
        fcs = _fc_series(sc, step_min, pp) if pp["free_cooling"] else None
        ctrl, init = {}, init_state or {}
        for name, s_, r_, use in (("air", cp["chw_supply_c"], cp["chw_return_c"], True), ("liquid", fws_s, fws_r, liquid)):
            if use and fcs is not None:
                ctrl[name] = realism.run_series(fcs, s_, r_, rc, dt_h, init.get(name))
    hours = []
    for k in range(24 * 60 // step_min):
        h = k * step_min / 60.0   # 시각 [h, 실수]
        t_o, rh_o, ghi = (interp_hourly(wx[key], h) for key in ("dry_bulb_c", "rh_pct", "ghi_w_m2"))
        cos_z, zen, sun_az, _ = solar_geometry(n, h, site["latitude"], site["longitude"], site["std_meridian"])
        dni, dhi = split_ghi(ghi, cos_z, n)

        # 외피 관류 (sol-air) + 창 일사
        q_wall = 0.0
        for w in env["walls"]:
            i_s = incident_vertical(ghi, dni, dhi, zen, sun_az, ORIENT_AZ[w["orientation"]])
            t_sa = t_o + w["absorptance"] * i_s / H_OUT
            q_wall += w["u_value"] * w["area_m2"] * (t_sa - t_in) / 1000.0
        r = env["roof"]
        t_sa_roof = t_o + r["absorptance"] * ghi / H_OUT
        q_roof = r["u_value"] * r["area_m2"] * (t_sa_roof - t_in) / 1000.0

        q_win_cond, q_solar = 0.0, 0.0
        for w in env["windows"]:
            i_s = incident_vertical(ghi, dni, dhi, zen, sun_az, ORIENT_AZ[w["orientation"]])
            q_win_cond += w["u_value"] * w["area_m2"] * (t_o - t_in) / 1000.0
            q_solar += w["shgc"] * w["area_m2"] * i_s / 1000.0

        # 내부 발열
        q_it = it["design_kw"] * interp_hourly(it["utilization"], h)
        q_ups = q_it * intl["ups_loss_ratio"]
        q_light = intl["lighting_w_m2"] * intl["floor_area_m2"] / 1000.0
        q_people = intl["people"] * intl["people_kw_each"]
        q_misc = intl.get("misc_kw") or 0.0   # 기타 발열 (BMS 보정, 기본 0)

        # 외기 도입 (현열+잠열)
        m_air = RHO_AIR * vent["outdoor_air_m3_h"] / 3600.0
        q_vent = m_air * (enthalpy(t_o, rh_o) - h_in)

        q_pre = q_wall + q_roof + q_win_cond + q_solar + q_it + q_ups + q_light + q_people + q_vent + q_misc
        # 액체 루프로 회수되는 IT 발열은 CRAH 를 거치지 않음 (air_chw 는 q_liq = 0 -> 기존 식과 동일)
        q_liq = q_it * phi
        q_pre_air = q_pre - q_liq
        q_fan = cp["crah_fan_heat_ratio"] * max(q_pre_air, 0.0)
        q_air = max(q_pre_air + q_fan, 0.0)
        q_cool = q_air + q_liq

        # 냉수 (칠러 증발기 <-> CRAH) - 공기측 부하만
        chw_kg_s = q_air / (CP_WATER * dt_chw)
        chw_m3_h = chw_kg_s / RHO_WATER * 3600.0

        # 프리쿨링 판정: 냉각탑 출구 수온(+판형 열교환기) 과 루프별 요구 공급/환수온도 비교
        twb = wet_bulb(t_o, rh_o)
        t_tower = twb + pp["tower_approach_k"]
        t_fc = t_tower + pp["hx_approach_k"]
        x_air = free_fraction(t_fc, cp["chw_supply_c"], cp["chw_return_c"]) if pp["free_cooling"] else 0.0
        x_liq = free_fraction(t_fc, fws_s, fws_r) if (liquid and pp["free_cooling"]) else 0.0
        c_air = ctrl["air"][0][k] if ctrl and "air" in ctrl else None
        c_liq = ctrl["liquid"][0][k] if ctrl and "liquid" in ctrl else None
        if c_air:
            x_air = c_air[0]
        if c_liq:
            x_liq = c_liq[0]

        # 칠러: 프리쿨링으로 처리하지 못한 열만. 액체 루프 보조 칠러는 높은 공급수온만큼 COP 보정
        cop = chiller_cop(cp, twb, cp["chw_supply_c"])
        ch_air = q_air * (1 - x_air)
        ch_liq = q_liq * (1 - x_liq)
        cop_liq = chiller_cop(cp, twb, fws_s) if liquid else cop
        p_chiller = ch_air / cop + (ch_liq / cop_liq if liquid else 0.0)

        # 냉각수 (칠러 응축기 / 프리쿨링 열교환기 <-> 냉각탑): 냉각탑은 전체 열 + 칠러 전력을 방출
        q_rej = q_cool + p_chiller
        cw_kg_s = q_rej / (CP_WATER * dt_cw)
        cw_m3_h = cw_kg_s / RHO_WATER * 3600.0

        # 보급수 = 증발 + 비산 + 블로다운
        evap = q_rej / H_FG / RHO_WATER * 3600.0
        drift = cp["tower_drift_ratio"] * cw_m3_h
        blowdown = max(evap / (coc - 1) - drift, 0.0)
        makeup = evap + drift + blowdown

        # 설비 전력 (보조동력은 kW/kW 계수, 가정값) -> PUE, WUE
        p_chw_pump = pp["chw_pump_kw_per_kw"] * q_air
        p_cw_pump = pp["cw_pump_kw_per_kw"] * q_rej
        p_liq_pump = pp["liquid_pump_kw_per_kw"] * q_liq
        p_tower = pp["tower_fan_kw_per_kw"] * q_rej
        p_cooling = p_chiller + q_fan + p_chw_pump + p_cw_pump + p_liq_pump + p_tower
        p_facility = q_it + q_ups + q_light + p_cooling
        mode = plant_mode(((q_air, x_air), (q_liq, x_liq)))
        plant = {
            "mode": mode,
            **({"mode_reason": max((c[1] for q, c in ((q_air, c_air), (q_liq, c_liq)) if c and q > 1e-9),
                                   key=REASON_RANK.get, default="threshold")} if rc else {}),
            "tower_water_c": round(t_tower, 2), "free_cooling_water_c": round(t_fc, 2),
            "air_loop": {"load_kw": round(q_air, 1), "free_fraction": round(x_air, 3),
                         "chiller_load_kw": round(ch_air, 1), "mode": loop_mode(x_air), **_ctrl_out(c_air)},
            "liquid_loop": None if not liquid else {
                "load_kw": round(q_liq, 1), "free_fraction": round(x_liq, 3),
                "chiller_load_kw": round(ch_liq, 1), "mode": loop_mode(x_liq), "chiller_cop": round(cop_liq, 2),
                **_ctrl_out(c_liq),
                "tcs_supply_c": pp["tcs_supply_c"], "tcs_return_c": pp["tcs_return_c"],
                "fws_supply_c": round(fws_s, 2), "fws_return_c": round(fws_r, 2),
                "fws_flow_m3_h": round(q_liq / (CP_WATER * (fws_r - fws_s)) / RHO_WATER * 3600.0, 1),
            },
            "power_kw": {"chiller": round(p_chiller, 1), "crah_fan": round(q_fan, 1),
                         "chw_pump": round(p_chw_pump, 2), "cw_pump": round(p_cw_pump, 2),
                         "liquid_pump": round(p_liq_pump, 2), "tower_fan": round(p_tower, 2),
                         "cooling_total": round(p_cooling, 1), "facility": round(p_facility, 1)},
            "pue": round(p_facility / q_it, 3) if q_it > 0 else None,
            "wue_l_per_kwh": round(makeup * 1000.0 / q_it, 3) if q_it > 0 else None,
        }

        # 3D 시각화용 (Phase 4): 계산에 쓰인 태양 위치와 면별 입사 일사량. 부하 계산에는 영향 없음
        sun = {"altitude_deg": round(90.0 - math.degrees(zen), 2), "azimuth_deg": round(math.degrees(sun_az), 2)}
        surface_solar = {o: round(incident_vertical(ghi, dni, dhi, zen, sun_az, az), 1)
                         for o, az in ORIENT_AZ.items()}
        surface_solar["roof"] = round(ghi, 1)

        hours.append({
            "hour": int(h),
            "minute": (k * step_min) % 60,
            "t_h": round(h, 4),
            "sun": sun,
            "surface_solar_w_m2": surface_solar,
            "weather": {"dry_bulb_c": round(t_o, 2), "rh_pct": round(rh_o, 2), "wet_bulb_c": round(twb, 2),
                        "ghi_w_m2": round(ghi, 1)},
            "load_kw": {
                "it": round(q_it, 1), "ups_loss": round(q_ups, 1),
                "lighting": round(q_light, 1), "people": round(q_people, 2),
                "wall_conduction": round(q_wall, 2), "roof_conduction": round(q_roof, 2),
                "window_conduction": round(q_win_cond, 2), "window_solar": round(q_solar, 2),
                "ventilation": round(q_vent, 2), "crah_fan": round(q_fan, 1),
                "total_cooling": round(q_cool, 1),
            },
            "chilled_water": {"flow_m3_h": round(chw_m3_h, 1), "supply_c": cp["chw_supply_c"], "return_c": cp["chw_return_c"]},
            "chiller": {"cop": round(cop, 2), "power_kw": round(p_chiller, 1)},
            "condenser_water": {"heat_rejection_kw": round(q_rej, 1), "flow_m3_h": round(cw_m3_h, 1),
                                "supply_c": cp["cw_supply_c"], "return_c": cp["cw_return_c"]},
            "makeup_water_m3_h": {"evaporation": round(evap, 3), "drift": round(drift, 4),
                                  "blowdown": round(blowdown, 3), "total": round(makeup, 3)},
            "plant": plant,
        })

    out = {"site": site["name"], "date": site["date"], "step_min": step_min, "architecture": arch,
           "hourly": hours, "daily_total": daily_total(hours, dt_h)}
    if rc:
        out["control"] = _ctrl_summary(ctrl, init_state)
    return out


def daily_total(hours, dt_h):
    """일 합계 = sum(시간당 값 x 스텝 길이[h])  (1시간 스텝이면 단순 합)"""
    s = lambda f: sum(f(x) for x in hours) * dt_h
    it_kwh = s(lambda x: x["load_kw"]["it"])
    fac_kwh = s(lambda x: x["plant"]["power_kw"]["facility"])
    daily = {
        "cooling_kwh": round(sum(x["load_kw"]["total_cooling"] for x in hours) * dt_h, 0),
        "chiller_kwh": round(sum(x["chiller"]["power_kw"] for x in hours) * dt_h, 0),
        "chw_m3": round(sum(x["chilled_water"]["flow_m3_h"] for x in hours) * dt_h, 0),
        "cw_m3": round(sum(x["condenser_water"]["flow_m3_h"] for x in hours) * dt_h, 0),
        "makeup_m3": round(sum(x["makeup_water_m3_h"]["total"] for x in hours) * dt_h, 1),
        # Phase 3.5
        "it_kwh": round(it_kwh, 0),
        "cooling_power_kwh": round(s(lambda x: x["plant"]["power_kw"]["cooling_total"]), 0),
        "facility_kwh": round(fac_kwh, 0),
        "pue": round(fac_kwh / it_kwh, 3) if it_kwh > 0 else None,
        "wue_l_per_kwh": round(s(lambda x: x["makeup_water_m3_h"]["total"]) * 1000.0 / it_kwh, 3) if it_kwh > 0 else None,
        "free_cooling_hours": round(s(lambda x: x["plant"]["mode"] == "free"), 2),
        "partial_free_cooling_hours": round(s(lambda x: x["plant"]["mode"] == "partial"), 2),
    }
    return daily


def _ctrl_out(c):
    """R1 제어 결과 → 루프 출력 필드 (제어가 꺼져 있으면 빈 dict — 기존 출력 그대로)"""
    if not c:
        return {}
    return {"chiller_on": c[3], "reason": c[1], "supply_overshoot_k": round(c[2], 2)}


def _ctrl_summary(ctrl, init_state):
    notes, sw, final = [], {}, {}
    for name, (series, st, how, init) in ctrl.items():
        sw[name] = realism.switches([c[3] for c in series], init["on"])
        final[name] = st
    if any(how == "warmup" for _, _, how, _ in ctrl.values()):
        notes.append("제어 초기 상태: 전날도 같은 날씨였다고 보고 하루를 한 번 미리 돌려 정함 (단일일 계산)")
    else:
        notes.append("제어 초기 상태: 전날 마지막 상태를 이어받음")
    return {"chiller_switches": sw, "final_state": final, "notes": notes}


def simulate_days(scs, step_min=60):
    """여러 날을 순서대로 — 날짜가 하루씩 이어지면 R1 제어 상태를 전날에서 이어받는다 (연간·다일 실행)"""
    from datetime import date as _d
    out, prev = [], None
    for s in scs:
        init = None
        if prev and "control" in prev and                 (_d.fromisoformat(s["site"]["date"]) - _d.fromisoformat(prev["date"])).days == 1:
            init = prev["control"]["final_state"]
        prev = simulate(s, step_min, init)
        out.append(prev)
    return out


def compare_architectures(sc, step_min=60):
    """같은 날씨·같은 IT 부하에서 냉각 방식만 바꿔 계산 (나머지 입력·계수는 동일)"""
    out = []
    for a in ARCHITECTURES:
        s = copy.deepcopy(sc)
        s["cooling_plant"]["architecture"] = a
        r = simulate(s, step_min)
        out.append({
            "architecture": a,
            "daily_total": r["daily_total"],
            "hourly": [{"t_h": x["t_h"], "mode": x["plant"]["mode"], "pue": x["plant"]["pue"],
                        "facility_kw": x["plant"]["power_kw"]["facility"],
                        "cooling_power_kw": x["plant"]["power_kw"]["cooling_total"],
                        "chiller_kw": x["chiller"]["power_kw"]} for x in r["hourly"]],
        })
    return {"step_min": step_min, "current": plant_param(sc["cooling_plant"], "architecture"), "architectures": out}


if __name__ == "__main__":
    # backend 폴더에서: python -m hvac.engine [scenario.json] [결과 저장 경로]
    # (data/result_sample.json 은 회귀 테스트 기준값이므로 기본으로 덮어쓰지 않음)
    path = sys.argv[1] if len(sys.argv) > 1 else DATA_DIR / "scenario_sample.json"
    with open(path, encoding="utf-8") as f:
        scenario = json.load(f)
    result = simulate(scenario)
    out = sys.argv[2] if len(sys.argv) > 2 else "result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"냉각 방식: {result['architecture']}")
    print(f"{'h':>2} {'Tdb':>5} {'Twb':>5} {'IT':>6} {'외피+일사':>8} {'외기':>6} {'총부하':>7} "
          f"{'냉수m3h':>8} {'COP':>5} {'칠러kW':>7} {'냉각수m3h':>9} {'보급수m3h':>9} {'모드':>8} {'PUE':>6}")
    for x in result["hourly"]:
        L = x["load_kw"]
        env_sum = L["wall_conduction"] + L["roof_conduction"] + L["window_conduction"] + L["window_solar"]
        print(f"{x['hour']:>2} {x['weather']['dry_bulb_c']:>5.1f} {x['weather']['wet_bulb_c']:>5.1f} "
              f"{L['it']:>6.0f} {env_sum:>8.1f} {L['ventilation']:>6.1f} {L['total_cooling']:>7.0f} "
              f"{x['chilled_water']['flow_m3_h']:>8.1f} {x['chiller']['cop']:>5.2f} {x['chiller']['power_kw']:>7.0f} "
              f"{x['condenser_water']['flow_m3_h']:>9.1f} {x['makeup_water_m3_h']['total']:>9.2f} "
              f"{x['plant']['mode']:>8} {x['plant']['pue'] or 0:>6.3f}")
    print("일 합계:", result["daily_total"])
