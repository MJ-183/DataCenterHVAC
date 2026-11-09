"""
NREL ESIF HPC 데이터센터 실측 vs 시뮬레이터 비교 (엔진 수식은 그대로, 시나리오만 ESIF 조건으로)

실행 (프로젝트 루트에서):  python validation_esif/esif_compare.py
입력
  data_esif/esif_hourly_mst.csv              ESIF 전력·PUE 시간 평균 (aggregate_esif.py 결과)
  validation_esif/golden_tmy3.epw            Golden, CO TMY3 (EnergyPlus 저장소) — 현장 기상이 없을 때
  data_esif/esif_outside_hourly_mst.csv      (선택) ESIF 현장 외기 온습도 시간 평균 (aggregate_outside.py 결과)
출력
  validation_esif/out/*.csv, summary.json, 그래프 png

비교 대응
  실측 cooling_kw (냉각탑·건식냉각기 팬, 동결방지 열선, 필터 펌프)  ↔  시뮬 tower_fan
  실측 pump_kw    (ERW·냉각탑수 루프 펌프, 팬월 부스터 펌프)         ↔  시뮬 chw_pump + cw_pump + liquid_pump
  실측 hvac_kw    (팬월, 전기실 팬코일, 외기 공급기)                  ↔  시뮬 crah_fan
  실측 PUE = (IT + cooling + pump + hvac + 조명·콘센트) / IT          ↔  시뮬 PUE (UPS 손실 0, 같은 정의)
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from hvac import DATA_DIR  # noqa: E402
from hvac import weather as wx  # noqa: E402
from hvac.engine import simulate_days, wet_bulb  # noqa: E402
from hvac.schemas import Scenario  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

PERIODS = {"cal": ("2016-09-01", "2017-08-31"),   # NREL 공개 1차년도 (PUE 1.034, WUE 0.7) 와 같은 기간
           "test": ("2019-01-01", "2019-12-31")}  # 보정 계수 검증용 (IT 부하 약 1.3배)

# ---------------- ESIF 조건 (출처 / 가정 구분) ----------------
FACTS = [
    ("cooling_plant.architecture", "hybrid_dlc", "NREL: 랙 직접 액체냉각, 칠러 없음"),
    ("cooling_plant.liquid_fraction", 0.794, "NREL: 2016 가동 IT 0.91 MW = 액체냉각 HPC 0.76 + 공랭 0.15 MW, "
     "액체 랙은 발열의 95% 이상 액체 회수 → 0.76 x 0.95 / 0.91 = 0.794"),
    ("cooling_plant.tcs_supply_c", 24.0, "NREL: IT 냉각수 공급 24 ℃(75 ℉, ASHRAE W2)"),
    ("cooling_plant.tcs_return_c", 35.0, "NREL: 환수 35~40 ℃ → 하한 35 ℃"),
    ("internal.ups_loss_ratio", 0.0, "NREL PUE 집계 5개 항목에 전기 손실 없음 → 같은 정의로 맞춤"),
    ("internal.floor_area_m2", 929.0, "NREL: 전산실 10,000 ft²"),
    ("internal.lighting_w_m2", 4.0, "실측 조명·콘센트 평균 3.7 kW → 929 m² 로 환산 (비교 대상 아닌 입력)"),
    ("internal.people", 0.0, "가정: 무인 전산실"),
    ("it_load", "measured", "실측 it_power_kw 시간값을 그대로 입력"),
]
ASSUMED = [
    ("envelope", "내부 공간", "가정: 건물 내부 전산실 → 외피 면적 최소(벽 각 50 m², 지붕 0, 창 0)"),
    ("ventilation.outdoor_air_m3_h", 1000.0, "가정: 외기 공급기 풍량 미공개 → 약 1 m³/h·m²"),
    ("cooling_plant.chw_supply_c / return_c", "21 / 27", "가정: 팬월 코일도 냉각탑수 계통(칠러 없음)에서 공급"),
    ("나머지 계수", "프로젝트 기본값", "펌프·팬 kW/kW, 냉각탑·열교환기·CDU 접근온도, COC 4 등"),
]


def base_scenario():
    sc = json.loads((DATA_DIR / "scenario_sample.json").read_text(encoding="utf-8"))
    sc.pop("cooling_layout", None)
    sc.pop("provenance", None)
    sc["site"].update({"name": "NREL ESIF HPC DC (Golden, CO)", "latitude": 39.74, "longitude": -105.17,
                       "std_meridian": -105.0})
    sc["envelope"] = {"walls": [{"orientation": o, "area_m2": 50.0, "u_value": 0.35, "absorptance": 0.7}
                                for o in "NESW"],
                      "roof": {"area_m2": 0.0, "u_value": 0.25, "absorptance": 0.7},
                      "windows": []}
    sc["internal"].update({"floor_area_m2": 929.0, "lighting_w_m2": 4.0, "people": 0.0, "ups_loss_ratio": 0.0})
    sc["ventilation"]["outdoor_air_m3_h"] = 1000.0
    cp = sc["cooling_plant"]
    cp.update({"architecture": "hybrid_dlc", "liquid_fraction": 0.794, "tcs_supply_c": 24.0, "tcs_return_c": 35.0,
               "chw_supply_c": 21.0, "chw_return_c": 27.0, "free_cooling": True})
    return sc


# ---------------- 데이터 ----------------
def load_measured():
    d = pd.read_csv(ROOT / "data_esif" / "esif_hourly_mst.csv", parse_dates=["timestamp_mst"]).set_index("timestamp_mst")
    return d[["it_power_kw", "cooling_kw", "pump_kw", "hvac_kw", "plug_and_light_kw", "pue"]]


def load_weather():
    """날짜 → (건구 24, 상대습도 24, 일사 24). 현장 관측이 있으면 온습도는 현장값, 일사는 TMY3"""
    text = (HERE / "golden_tmy3.epw").read_text(encoding="latin-1")
    tmy = {}
    for m in range(1, 13):
        for dd in range(1, 32):
            try:
                w = wx.weather_from_epw(text, m, dd)["weather"]
            except Exception:
                continue
            tmy[(m, dd)] = w
    site = None
    f = ROOT / "data_esif" / "esif_outside_hourly_mst.csv"
    if f.exists():
        s = pd.read_csv(f, parse_dates=["timestamp_mst"]).set_index("timestamp_mst")
        s["t_c"] = (s["outside_air_temp"] - 32) / 1.8
        site = s
    return tmy, site


def day_weather(day, tmy, site):
    key = (day.month, day.day) if (day.month, day.day) in tmy else (2, 28)
    w = tmy[key]
    if site is None:
        return dict(w), "tmy3"
    idx = pd.date_range(pd.Timestamp(day), periods=24, freq="h")
    t = site["t_c"].reindex(idx)
    rh = site["outside_air_humidity"].reindex(idx)
    if t.isna().sum() > 3 or rh.isna().sum() > 3:
        return dict(w), "tmy3(현장 결측)"
    t, rh = t.interpolate(limit_direction="both"), rh.interpolate(limit_direction="both").clip(1, 100)
    return {"dry_bulb_c": [round(x, 2) for x in t], "rh_pct": [round(x, 1) for x in rh],
            "ghi_w_m2": w["ghi_w_m2"]}, "site"


# ---------------- 시뮬레이션 ----------------
def run_period(sc, meas, p0, p1, tmy, site, over=None):
    days = pd.date_range(p0, p1, freq="D")
    scs, src = [], []
    it_all = meas["it_power_kw"]
    design = float(it_all.loc[p0:p1].max())
    for dd in days:
        idx = pd.date_range(dd, periods=24, freq="h")
        it = it_all.reindex(idx)
        if it.isna().all():
            it = pd.Series([float(it_all.loc[p0:p1].mean())] * 24, index=idx)
        it = it.interpolate(limit_direction="both")
        w, s = day_weather(dd.date(), tmy, site)
        src.append(s)
        x = json.loads(json.dumps(sc))
        x["site"]["date"] = dd.date().isoformat()
        x["weather"] = w
        x["it_load"] = {"design_kw": round(design, 1), "utilization": [round(v / design, 5) for v in it]}
        if over:
            x["cooling_plant"].update(over)
        scs.append(Scenario.model_validate(x).to_engine_dict())
    res = simulate_days(scs)
    rows = []
    for dd, r in zip(days, res):
        for h in r["hourly"]:
            pk = h["plant"]["power_kw"]
            rows.append({"timestamp_mst": dd + pd.Timedelta(hours=h["hour"]),
                         "sim_it_kw": h["load_kw"]["it"], "sim_light_kw": h["load_kw"]["lighting"],
                         "sim_tower_kw": pk["tower_fan"],
                         "sim_pump_kw": pk["chw_pump"] + pk["cw_pump"] + pk["liquid_pump"],
                         "sim_fan_kw": pk["crah_fan"], "sim_chiller_kw": pk["chiller"],
                         "sim_makeup_m3_h": h["makeup_water_m3_h"]["total"], "mode": h["plant"]["mode"],
                         "dry_bulb_c": h["weather"]["dry_bulb_c"], "wet_bulb_c": h["weather"]["wet_bulb_c"]})
    out = pd.DataFrame(rows).set_index("timestamp_mst")
    return out, pd.Series(src).value_counts().to_dict()


def joined(sim, meas):
    j = sim.join(meas, how="inner").dropna(subset=["it_power_kw", "cooling_kw", "pump_kw", "hvac_kw"])
    j["meas_mech_kw"] = j.cooling_kw + j.pump_kw + j.hvac_kw
    j["sim_mech_kw"] = j.sim_tower_kw + j.sim_pump_kw + j.sim_fan_kw + j.sim_chiller_kw
    j["meas_pue"] = (j.it_power_kw + j.meas_mech_kw + j.plug_and_light_kw) / j.it_power_kw
    j["sim_pue"] = (j.sim_it_kw + j.sim_mech_kw + j.sim_light_kw) / j.sim_it_kw
    return j


def g14(meas, sim):
    """ASHRAE Guideline 14 지표 (시간값). NMBE 부호: (실측-시뮬)/실측 → 양수면 시뮬이 과소"""
    n = len(meas)
    mbar = meas.mean()
    nmbe = (meas - sim).sum() / ((n - 1) * mbar) * 100
    cv = math.sqrt(((meas - sim) ** 2).sum() / (n - 1)) / mbar * 100
    return round(nmbe, 1), round(cv, 1)


def metrics(j):
    e = lambda c: float(j[c].sum())   # kWh (1시간 간격)
    pue_m = (e("it_power_kw") + e("meas_mech_kw") + e("plug_and_light_kw")) / e("it_power_kw")
    pue_s = (e("sim_it_kw") + e("sim_mech_kw") + e("sim_light_kw")) / e("sim_it_kw")
    out = {"hours": len(j), "it_mean_kw": round(j.it_power_kw.mean(), 1),
           "pue": {"measured": round(pue_m, 4), "sim": round(pue_s, 4), "diff": round(pue_s - pue_m, 4)},
           "chiller_hours_sim": int((j.sim_chiller_kw > 0.05).sum()), "chiller_hours_measured": 0}
    for name, mc, sc_ in (("tower_vs_cooling", "cooling_kw", "sim_tower_kw"), ("pump", "pump_kw", "sim_pump_kw"),
                          ("fan_vs_hvac", "hvac_kw", "sim_fan_kw"), ("mech_total", "meas_mech_kw", "sim_mech_kw")):
        nmbe, cv = g14(j[mc], j[sc_])
        out[name] = {"measured_mean_kw": round(j[mc].mean(), 2), "sim_mean_kw": round(j[sc_].mean(), 2),
                     "ratio_sim_over_meas": round(j[sc_].mean() / j[mc].mean(), 3),
                     "nmbe_pct": nmbe, "cv_rmse_pct": cv,
                     "corr_hourly": round(j[mc].corr(j[sc_]), 3),
                     "corr_with_it": {"measured": round(j[mc].corr(j.it_power_kw), 3),
                                      "sim": round(j[sc_].corr(j.sim_it_kw), 3)},
                     "corr_with_wet_bulb": {"measured": round(j[mc].corr(j.wet_bulb_c), 3),
                                            "sim": round(j[sc_].corr(j.wet_bulb_c), 3)}}
    out["sim_wue_l_per_kwh"] = round(e("sim_makeup_m3_h") * 1000 / e("sim_it_kw"), 3)
    return out


def monthly(j):
    m = j.resample("MS").mean(numeric_only=True)
    m["meas_pue"] = (m.it_power_kw + m.meas_mech_kw + m.plug_and_light_kw) / m.it_power_kw
    m["sim_pue"] = (m.sim_it_kw + m.sim_mech_kw + m.sim_light_kw) / m.sim_it_kw
    cols = ["it_power_kw", "dry_bulb_c", "wet_bulb_c", "cooling_kw", "sim_tower_kw", "pump_kw", "sim_pump_kw",
            "hvac_kw", "sim_fan_kw", "meas_mech_kw", "sim_mech_kw", "meas_pue", "sim_pue", "sim_chiller_kw"]
    m = m[cols].round(3)
    m.index = m.index.strftime("%Y-%m")
    return m


def calibrate(j, sc):
    """기간 평균이 실측과 같아지도록 계수만 비례 조정 (구조는 그대로) — 보정으로 설명되는 오차와 안 되는 오차를 나눔"""
    cp = sc["cooling_plant"]
    pp = lambda k, d: cp.get(k, d)
    r_tower = j.cooling_kw.mean() / j.sim_tower_kw.mean()
    r_pump = j.pump_kw.mean() / j.sim_pump_kw.mean()
    r_fan = j.hvac_kw.mean() / j.sim_fan_kw.mean()
    return {"tower_fan_kw_per_kw": round(pp("tower_fan_kw_per_kw", 0.006) * r_tower, 5),
            "chw_pump_kw_per_kw": round(pp("chw_pump_kw_per_kw", 0.015) * r_pump, 5),
            "cw_pump_kw_per_kw": round(pp("cw_pump_kw_per_kw", 0.012) * r_pump, 5),
            "liquid_pump_kw_per_kw": round(pp("liquid_pump_kw_per_kw", 0.02) * r_pump, 5),
            "crah_fan_heat_ratio": round(pp("crah_fan_heat_ratio", 0.05) * r_fan, 5)}


def main():
    meas = load_measured()
    tmy, site = load_weather()
    sc = Scenario.model_validate(base_scenario()).to_engine_dict()
    summary = {"facts": FACTS, "assumed": ASSUMED, "weather": "site" if site is not None else "tmy3", "runs": {}}

    # A. 공개 사실 + 프로젝트 기본 계수
    simA, srcA = run_period(sc, meas, *PERIODS["cal"], tmy, site)
    jA = joined(simA, meas)
    summary["runs"]["A_default_cal"] = {"period": PERIODS["cal"], "weather_days": srcA, **metrics(jA)}
    monthly(jA).to_csv(OUT / "monthly_A_default.csv")

    # B. 계수만 비례 보정 (2016-09~2017-08 로 맞추고)
    over = calibrate(jA, sc)
    simB, _ = run_period(sc, meas, *PERIODS["cal"], tmy, site, over)
    jB = joined(simB, meas)
    summary["runs"]["B_calibrated_cal"] = {"period": PERIODS["cal"], "coefficients": over, **metrics(jB)}
    monthly(jB).to_csv(OUT / "monthly_B_calibrated.csv")

    # C. 보정 계수를 다른 해(2019)에 그대로 적용 — 검증
    simC, srcC = run_period(sc, meas, *PERIODS["test"], tmy, site, over)
    jC = joined(simC, meas)
    summary["runs"]["C_calibrated_test2019"] = {"period": PERIODS["test"], "weather_days": srcC, **metrics(jC)}
    monthly(jC).to_csv(OUT / "monthly_C_test2019.csv")

    for k, j in (("A", jA), ("B", jB), ("C", jC)):
        j.to_csv(OUT / f"hourly_{k}.csv", float_format="%.3f")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary["runs"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
