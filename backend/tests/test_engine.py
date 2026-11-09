"""손계산 대조 테스트: backend 폴더에서  python -m pytest"""
import copy
import json
import math

from hvac import DATA_DIR
from hvac.engine import simulate, enthalpy, wet_bulb


def base():
    with open(DATA_DIR / "scenario_sample.json", encoding="utf-8") as f:
        return json.load(f)


def test_chw_flow_hand_calc():
    """외피·외기·조명 등을 0으로 두고 IT만 남겨 냉수 유량을 손계산과 비교"""
    sc = base()
    for w in sc["envelope"]["walls"] + sc["envelope"]["windows"]:
        w["area_m2"] = 0
    sc["envelope"]["roof"]["area_m2"] = 0
    sc["internal"].update(lighting_w_m2=0, people=0, ups_loss_ratio=0)
    sc["ventilation"]["outdoor_air_m3_h"] = 0
    sc["cooling_plant"]["crah_fan_heat_ratio"] = 0
    sc["it_load"]["utilization"] = [0.5] * 24      # 1000 kW
    r = simulate(sc)["hourly"][12]
    # 1000 / (4.186*6) = 39.82 kg/s -> 143.3 m3/h
    assert math.isclose(r["chilled_water"]["flow_m3_h"], 143.3, abs_tol=0.2)
    # 냉각수: (1000 + 1000/COP) / (4.186*5) * 3.6
    q_rej = 1000 + 1000 / r["chiller"]["cop"]
    assert math.isclose(r["condenser_water"]["flow_m3_h"], q_rej / (4.186 * 5) * 3.6, abs_tol=0.2)
    # 보급수 = 증발 * COC/(COC-1)  (비산은 블로다운에서 차감되므로 합계 불변)
    evap = q_rej / 2430 * 3.6
    assert math.isclose(r["makeup_water_m3_h"]["total"], evap * 4 / 3, abs_tol=0.01)


def test_psychrometrics():
    # 24℃/50%: 엔탈피 약 47.7 kJ/kg, 20℃/50% 습구 약 13.7℃ (Stull 논문 예시)
    assert math.isclose(enthalpy(24, 50), 47.7, abs_tol=0.5)
    assert math.isclose(wet_bulb(20, 50), 13.7, abs_tol=0.1)


def test_night_no_solar():
    r = simulate(base())["hourly"][2]
    assert r["load_kw"]["window_solar"] == 0


VIS_KEYS = ("sun", "surface_solar_w_m2", "minute", "t_h", "plant")  # Phase 4 이후 추가된 시간별 출력 키
DAILY_V1 = ("cooling_kwh", "chiller_kwh", "chw_m3", "cw_m3", "makeup_m3")  # Phase 1 일 합계 키


def strip_vis(result):
    r = copy.deepcopy(result)
    r.pop("step_min", None)
    r.pop("architecture", None)
    for x in r["hourly"]:
        for k in VIS_KEYS:
            x.pop(k, None)
    r["daily_total"] = {k: r["daily_total"][k] for k in DAILY_V1}
    return r


def test_regression_sample():
    """Phase 1 결과(result_sample.json)와 기존 출력값이 완전히 동일해야 함 (시각화 전용 키 제외)"""
    with open(DATA_DIR / "result_sample.json", encoding="utf-8") as f:
        expected = json.load(f)
    assert strip_vis(simulate(base())) == expected


def test_sun_position_hand_calc():
    """7/15 서울: 적위 = 23.45 sin(360(284+196)/365) = 21.5°, 남중고도 = 90 - (37.57 - 21.5) = 73.9°"""
    hourly = simulate(base())["hourly"]
    noon = max(hourly, key=lambda x: x["sun"]["altitude_deg"])
    decl = 23.45 * math.sin(math.radians(360 * (284 + 196) / 365))
    # 매시 정각만 계산하므로 남중 시각(약 12:35 KST)과의 차이만큼 낮게 나옴 -> 3° 허용
    assert noon["sun"]["altitude_deg"] <= 90 - (37.57 - decl) + 0.01
    assert noon["sun"]["altitude_deg"] > 90 - (37.57 - decl) - 3
    assert hourly[0]["sun"]["altitude_deg"] < 0          # 자정: 지평선 아래
    assert hourly[8]["sun"]["azimuth_deg"] < 0           # 오전: 동쪽(-)
    assert hourly[16]["sun"]["azimuth_deg"] > 0          # 오후: 서쪽(+)


def test_surface_solar_consistent_with_window_gain():
    """남측 창 일사 = SHGC x 면적 x 남측 입사량 (창이 남측만 있을 때)"""
    sc = base()
    sc["envelope"]["windows"] = [{"orientation": "S", "area_m2": 30, "u_value": 1.8, "shgc": 0.4}]
    for x in simulate(sc)["hourly"]:
        assert math.isclose(x["load_kw"]["window_solar"], 0.4 * 30 * x["surface_solar_w_m2"]["S"] / 1000, abs_tol=0.02)
        assert x["surface_solar_w_m2"]["roof"] == x["weather"]["ghi_w_m2"]


# ---------- 서브시간(연속) 계산 ----------
def test_step60_is_default():
    assert simulate(base(), 60) == simulate(base())


def test_subhourly_on_the_hour_equals_hourly():
    """5분 계산의 정시 스텝은 1시간 계산 결과와 완전히 같아야 함 (같은 입력, 같은 식)"""
    hourly = simulate(base(), 60)["hourly"]
    fine = simulate(base(), 5)["hourly"]
    assert len(fine) == 288
    assert [x for x in fine if x["minute"] == 0] == hourly


def test_subhourly_interpolation_hand_calc():
    sc = base()
    fine = {(x["hour"], x["minute"]): x for x in simulate(sc, 5)["hourly"]}
    x = fine[(12, 30)]
    assert x["t_h"] == 12.5
    assert math.isclose(x["weather"]["dry_bulb_c"], (32.0 + 32.6) / 2, abs_tol=1e-9)
    assert math.isclose(x["load_kw"]["it"], 2000 * (0.84 + 0.85) / 2, abs_tol=0.05)
    # 23:30 은 23시와 (다음날로 가정한) 0시 값 사이
    assert math.isclose(fine[(23, 30)]["weather"]["dry_bulb_c"], (26.8 + 26.5) / 2, abs_tol=1e-9)
    # 12:15 의 GHI = 830 + (850-830) x 0.25
    assert math.isclose(fine[(12, 15)]["weather"]["ghi_w_m2"], 835.0, abs_tol=1e-9)


def test_subhourly_sun_moves_continuously():
    fine = simulate(base(), 5)["hourly"]
    az = [x["sun"]["azimuth_deg"] for x in fine if 6 <= x["t_h"] <= 18]
    assert all(b > a for a, b in zip(az, az[1:]))          # 낮 동안 방위각 단조 증가 (동 -> 서)
    alt = [x["sun"]["altitude_deg"] for x in fine]
    assert max(abs(b - a) for a, b in zip(alt, alt[1:])) < 1.6   # 5분 사이 고도 변화는 작아야 함


def test_subhourly_daily_total_is_integral():
    r = simulate(base(), 5)
    dt = 5 / 60
    assert r["daily_total"]["cooling_kwh"] == round(sum(x["load_kw"]["total_cooling"] for x in r["hourly"]) * dt, 0)
    # 선형 보간이므로 정시 합(직사각형 근사)과 크게 다르지 않아야 함
    hourly = simulate(base(), 60)["daily_total"]
    for k in DAILY_V1 + ("it_kwh", "cooling_power_kwh", "facility_kwh", "pue", "wue_l_per_kwh"):
        assert abs(r["daily_total"][k] - hourly[k]) / hourly[k] < 0.01, k


def test_invalid_step():
    import pytest
    with pytest.raises(ValueError):
        simulate(base(), 7)
