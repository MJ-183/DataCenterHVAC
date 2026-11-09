"""
실측 비교 (Phase 19) — 장기 실측(시간 단위)과 같은 조건의 시뮬레이션 비교. 엔진은 호출만 한다 (수식 변경 없음).

실행 3가지
  A: 사실 + 기본 계수 (보정 기간)
  B: A 에서 항목별 기간 평균이 실측과 같아지도록 계수만 비례 보정 (보정 기간)
  C: B 계수를 검증 기간에 그대로 적용
지표 (실측이 있는 시간만): 평균 kW, 비율(시뮬 ÷ 실측), NMBE, CV(RMSE), 시간 상관, IT·습구와의 상관, 월별 표,
  기간 PUE, 칠러 필요 시간, WUE, G14 판정(시간값 |NMBE| ≤ 10%, CV ≤ 30%)
오차 부호: hvac/validation.py 와 같은 '계산 − 실측' (양수면 시뮬이 과대).
  NMBE = Σ(시뮬 − 실측) / ((n − 1) × 실측 평균) × 100,  CV(RMSE) = √(Σ(시뮬 − 실측)² / (n − 1)) / 실측 평균 × 100
  (ASHRAE Guideline 14 공식, 자유도 n − 1. G14 원래 부호는 실측 − 계산이라 NMBE 부호가 반대)
분석 전용 (엔진에 넣지 않음): structure_check (고정분 + 비례 회귀), sensitivity_chiller_hours (고도 습구·접근온도)
"""
import hashlib
import json
import math
from datetime import date, datetime

import numpy as np

from . import DATA_DIR
from .engine import PLANT_DEFAULTS, p_sat, simulate_days, wet_bulb
from .realdata import COEF, FORMULA, SIM_KEYS, SIM_LABEL, fill_day, hour_dt, period_hours, series
from .schemas import Scenario

VERSION = 1                     # 결과 형식·계산이 바뀌면 올림 (디스크 캐시 무효화)
CACHE_DIR = DATA_DIR / "real_cache"
G14_NMBE, G14_CV = 10.0, 30.0
CHILLER_ON_KW = 0.05
RUN_LABEL = {"A": "A 기본 계수", "B": "B 보정", "C": "C 검증"}
SIGN_NOTE = "오차 = 시뮬 − 실측 (양수면 시뮬이 과대). NMBE·CV(RMSE) 는 ASHRAE G14 공식(자유도 n−1), 부호만 프로젝트 규칙"


# ---------- 시뮬레이션 ----------
def _day_scenarios(sc, ds, mapping, p0, p1, weather, over):
    h0, n = period_hours(p0, p1)
    it_all = series(ds, mapping["it"], h0, n)
    if np.isnan(it_all).all():
        raise ValueError(f"{p0} ~ {p1} 기간에 IT 실측이 없습니다")
    design = float(np.nanmax(it_all))
    mean = float(np.nanmean(it_all))
    scs, src = [], {}
    for k in range(n // 24):
        d = date.fromordinal(p0.toordinal() + k)
        it = it_all[k * 24:(k + 1) * 24]
        it = np.full(24, mean) if np.isnan(it).all() else fill_day(it)
        w, s = weather.day(d)
        src[s] = src.get(s, 0) + 1
        x = json.loads(json.dumps(sc))
        x["site"]["date"] = d.isoformat()
        x["weather"] = w
        x["it_load"] = {"design_kw": round(design, 1), "utilization": [round(float(v) / design, 5) for v in it]}
        if over:
            x["cooling_plant"].update(over)
        scs.append(Scenario.model_validate(x).to_engine_dict())
    return scs, src


def run_period(sc, ds, mapping, p0, p1, weather, over=None):
    """기간 시뮬레이션 + 실측 맞춤 → 시간 표 (실측 IT 와 항목이 모두 있는 시간만) + 기상 출처 날짜 수"""
    scs, src = _day_scenarios(sc, ds, mapping, p0, p1, weather, over)
    res = simulate_days(scs)
    h0, n = period_hours(p0, p1)
    sim = {k: np.empty(n) for k in (*SIM_KEYS, "it", "ups", "light", "makeup", "db", "wb")}
    for di, r in enumerate(res):
        for h in r["hourly"]:
            i = di * 24 + h["hour"]
            pk, ld = h["plant"]["power_kw"], h["load_kw"]
            for k in SIM_KEYS:
                sim[k][i] = pk[k]
            sim["it"][i], sim["ups"][i], sim["light"][i] = ld["it"], ld["ups_loss"], ld["lighting"]
            sim["makeup"][i] = h["makeup_water_m3_h"]["total"]
            sim["db"][i], sim["wb"][i] = h["weather"]["dry_bulb_c"], h["weather"]["wet_bulb_c"]
    meas = {c: series(ds, c, h0, n) for c in {mapping["it"], *mapping["items"], *mapping["pue_terms"]} - {"it"}}
    ok = ~np.isnan(meas[mapping["it"]])
    for c in mapping["items"]:
        ok &= ~np.isnan(meas[c])
    j = {"h": np.arange(h0, h0 + n)[ok], "it": meas[mapping["it"]][ok],
         "sim_it": sim["it"][ok], "sim_ups": sim["ups"][ok], "sim_light": sim["light"][ok],
         "sim_makeup": sim["makeup"][ok], "db": sim["db"][ok], "wb": sim["wb"][ok],
         "sim_chiller": sim["chiller"][ok], "meas": {}, "sim": {}}
    for c, keys in mapping["items"].items():
        j["meas"][c] = meas[c][ok]
        j["sim"][c] = sum(sim[k][ok] for k in keys)
    j["meas"]["mech_total"] = sum(j["meas"][c] for c in mapping["items"])
    j["sim"]["mech_total"] = sum(sim[k][ok] for k in SIM_KEYS)       # 냉방 설비 전체 (칠러 포함)
    j["other"] = {c: meas[c][ok] for c in mapping["pue_terms"] if c != "it" and c not in mapping["items"]}
    return j, src


# ---------- 지표 ----------
def g14(meas, sim):
    """(NMBE, CV(RMSE)) [%] — 부호 = 시뮬 − 실측"""
    n, mbar = len(meas), float(np.mean(meas))
    if n < 2 or mbar == 0:
        return None, None
    e = sim - meas
    return round(float(e.sum() / ((n - 1) * mbar) * 100), 1), round(float(math.sqrt((e ** 2).sum() / (n - 1)) / mbar * 100), 1)


def g14_pass(nmbe, cv):
    return None if nmbe is None else bool(abs(nmbe) <= G14_NMBE and cv <= G14_CV)


def _corr(a, b):
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return round(float(np.corrcoef(a, b)[0, 1]), 3)


def _pue(j, mapping):
    it_m = float(j["it"].sum())
    pue_m = (it_m * (1 + mapping.get("ups_loss", 0.0)) + float(j["meas"]["mech_total"].sum())
             + sum(float(np.nansum(v)) for v in j["other"].values())) / it_m
    s_it = float(j["sim_it"].sum())
    pue_s = (s_it + float(j["sim_ups"].sum()) + float(j["sim_light"].sum()) + float(j["sim"]["mech_total"].sum())) / s_it
    return pue_m, pue_s


def metrics(j, mapping, facility=None):
    pue_m, pue_s = _pue(j, mapping)
    chiller_cols = [c for c, ks in mapping["items"].items() if "chiller" in ks]
    if chiller_cols:
        ch_meas = int((j["meas"][chiller_cols[0]] > CHILLER_ON_KW).sum())
    elif (facility or {}).get("chiller_count") == 0:
        ch_meas = 0
    else:
        ch_meas = None
    out = {"hours": int(len(j["h"])), "it_mean_kw": round(float(j["it"].mean()), 1),
           "pue": {"measured": round(pue_m, 4), "sim": round(pue_s, 4), "diff": round(pue_s - pue_m, 4)},
           "chiller_hours": {"sim": int((j["sim_chiller"] > CHILLER_ON_KW).sum()), "measured": ch_meas},
           "chiller_mean_kw": round(float(j["sim_chiller"].mean()), 2), "items": {}}
    labels = {**mapping.get("labels", {}), "mech_total": "기계 합계"}
    for c in [*mapping["items"], "mech_total"]:
        m, s = j["meas"][c], j["sim"][c]
        nmbe, cv = g14(m, s)
        out["items"][c] = {
            "label": labels.get(c, c), "sim_keys": mapping["items"].get(c, list(SIM_KEYS)),
            "measured_mean_kw": round(float(m.mean()), 2), "sim_mean_kw": round(float(s.mean()), 2),
            "ratio_sim_over_meas": round(float(s.mean() / m.mean()), 3) if m.mean() else None,
            "nmbe_pct": nmbe, "cv_rmse_pct": cv, "g14_pass": g14_pass(nmbe, cv),
            "corr_hourly": _corr(m, s),
            "corr_with_it": {"measured": _corr(m, j["it"]), "sim": _corr(s, j["sim_it"])},
            "corr_with_wet_bulb": {"measured": _corr(m, j["wb"]), "sim": _corr(s, j["wb"])}}
    out["wue_l_per_kwh"] = {"sim": round(float(j["sim_makeup"].sum()) * 1000 / float(j["sim_it"].sum()), 3),
                            "measured": None}
    return out


def _group(j, keyfn, mapping):
    """시간 표 → 월·일 평균 표 (실측·시뮬 항목, IT, 기상, PUE)"""
    keys = [keyfn(hour_dt(h)) for h in j["h"]]
    order = list(dict.fromkeys(keys))
    idx = {k: np.array([i for i, x in enumerate(keys) if x == k]) for k in order}
    rows = []
    for k in order:
        ii = idx[k]
        r = {"t": k, "hours": int(len(ii)), "it_kw": round(float(j["it"][ii].mean()), 3),
             "dry_bulb_c": round(float(j["db"][ii].mean()), 3), "wet_bulb_c": round(float(j["wb"][ii].mean()), 3),
             "sim_chiller_kw": round(float(j["sim_chiller"][ii].mean()), 3)}
        for c in [*mapping["items"], "mech_total"]:
            r[f"meas_{c}"] = round(float(j["meas"][c][ii].mean()), 3)
            r[f"sim_{c}"] = round(float(j["sim"][c][ii].mean()), 3)
        other = sum(float(np.nanmean(v[ii])) if (~np.isnan(v[ii])).any() else 0.0 for v in j["other"].values())
        it = r["it_kw"]
        r["meas_pue"] = round((it * (1 + mapping.get("ups_loss", 0.0)) + r["meas_mech_total"] + other) / it, 4)
        s_it = float(j["sim_it"][ii].mean())
        r["sim_pue"] = round((s_it + float(j["sim_ups"][ii].mean()) + float(j["sim_light"][ii].mean())
                              + float(j["sim"]["mech_total"][ii].mean())) / s_it, 4)
        rows.append(r)
    return rows


def monthly(j, mapping):
    return _group(j, lambda d: d.strftime("%Y-%m"), mapping)


def daily(j, mapping):
    return _group(j, lambda d: d.strftime("%Y-%m-%d"), mapping)


def hourly_table(j, mapping):
    """화면 그래프용 시간값 (주간 보기·산점도). 2자리 반올림"""
    r = lambda a, n=2: [round(float(x), n) for x in a]
    other = sum((np.nan_to_num(v) for v in j["other"].values()), np.zeros(len(j["h"])))
    pue_m = (j["it"] * (1 + mapping.get("ups_loss", 0.0)) + j["meas"]["mech_total"] + other) / j["it"]
    pue_s = (j["sim_it"] + j["sim_ups"] + j["sim_light"] + j["sim"]["mech_total"]) / j["sim_it"]
    return {"start": hour_dt(int(j["h"][0])).isoformat(sep=" ") if len(j["h"]) else None,
            "offset_h": [int(h - j["h"][0]) for h in j["h"]], "it_kw": r(j["it"]), "sim_it_kw": r(j["sim_it"]),
            "wet_bulb_c": r(j["wb"]), "meas": {**{c: r(v) for c, v in j["meas"].items()}, "pue": r(pue_m, 4)},
            "sim": {**{c: r(v) for c, v in j["sim"].items()}, "pue": r(pue_s, 4)}}


# ---------- 보정 ----------
def _coef(cp, k):
    return cp[k] if k in cp else PLANT_DEFAULTS[k]


def calibrate(j, sc, mapping):
    """항목별 기간 평균이 실측과 같아지도록 계수만 비례 조정 → (B 계수, 항목별 비율 r = 실측 ÷ 시뮬 평균, 반올림 전)"""
    cp = sc["cooling_plant"]
    over, ratios = {}, {}
    for c, keys in mapping["items"].items():
        coefs = [COEF[k] for k in keys if k in COEF]
        s = float(j["sim"][c].mean())
        if not coefs or s <= 0:
            continue
        r = float(j["meas"][c].mean()) / s
        ratios[c] = r
        for k in coefs:
            over[k] = round(_coef(cp, k) * r, 5)
    return over, ratios


def _fmt_kw(x):
    return f"{x:+.2f}"


def calibration_steps(A, B, C, sc, mapping, over, ratios):
    """시뮬 A → B 보정 단계 (화면에 그대로 쓰는 구조화 출력). 문장은 템플릿, 숫자는 계산값만"""
    cp = sc["cooling_plant"]
    items = list(mapping["items"])
    lab = lambda c: A["items"][c]["label"]
    it = A["it_mean_kw"]
    calib_items = [c for c in items if c in ratios]
    coef_names = [COEF[k] for c in calib_items for k in mapping["items"][c] if k in COEF]
    steps = []

    # 0. 무엇을 바꾸고 무엇을 두는가
    steps.append({"step": 0, "title": "무엇을 바꾸고 무엇을 두는가", "bullets": [
        f"바꾸는 것: 공개 자료가 없어 프로젝트 기본값을 쓴 보조동력 계수 {len(coef_names)}개 ({', '.join(coef_names)})",
        "두는 것: 공개 사실(시나리오 '사실' 입력, IT 실측), 접근온도, 칠러 판정 → 칠러 필요 시간은 보정해도 그대로 "
        f"({A['chiller_hours']['sim']} h → {B['chiller_hours']['sim']} h)",
        "방법: 항목별 기간 평균을 실측에 맞추는 비례 보정 1회. 엔진에서 각 전력이 계수에 비례하므로 "
        "계수 × (실측 평균 ÷ 시뮬 평균) 이면 평균이 맞는다",
        "프로젝트 BMS 보정(Phase 12, Levenberg–Marquardt)과 달리 시간별 잔차를 최소화하지 않는다. "
        "오차가 '계수' 문제인지 '구조' 문제인지 가르는 것이 목적"]})

    # 1. 항목별 차이
    rows = []
    for c in items:
        a = A["items"][c]
        keys = mapping["items"][c]
        formula = FORMULA[keys[0]] if len(keys) == 1 else \
            f"{' + '.join(SIM_LABEL[k] for k in keys)} = " + " + ".join(FORMULA[k] for k in keys) + \
            (" (계수들에 같은 비율)" if c in ratios else "")
        rows.append({"item": c, "label": lab(c), "measured_mean_kw": a["measured_mean_kw"], "sim_A_mean_kw": a["sim_mean_kw"],
                     "ratio": round(ratios[c], 4) if c in ratios else None, "formula": formula,
                     "calibrated": c in ratios})
    if not any("chiller" in ks for ks in mapping["items"].values()):
        rows.append({"item": "chiller", "label": "칠러 (참고)", "measured_mean_kw": 0 if A["chiller_hours"]["measured"] == 0 else None,
                     "sim_A_mean_kw": A["chiller_mean_kw"], "ratio": None, "calibrated": False,
                     "formula": "보정 안 함 — 칠러 판정은 접근온도·습구식 문제 (칠러 민감도 참고)"})
    steps.append({"step": 1, "title": "시뮬 A 의 항목별 차이", "rows": rows,
                  "text": f"보정 기간 {A['period'][0]} ~ {A['period'][1]} ({A['hours']:,} h), IT 평균 {it:,.1f} kW. "
                          "비율 r = 실측 평균 ÷ 시뮬 A 평균 (반올림 전 평균으로 계산)"})

    # 2. 계수 조정
    rows = []
    for c in calib_items:
        for k in mapping["items"][c]:
            if k in COEF:
                rows.append({"coefficient": COEF[k], "item": c, "A": _coef(cp, COEF[k]), "ratio": round(ratios[c], 4),
                             "B": over[COEF[k]]})
    steps.append({"step": 2, "title": "계수 조정", "rows": rows, "text": "B 계수 = A 계수 × r (소수 5자리 반올림)"})

    # 3. 재실행 확인
    rows, resid = [], {}
    for c in [*items, "mech_total"]:
        a, b = A["items"][c], B["items"][c]
        resid[c] = round(b["sim_mean_kw"] - b["measured_mean_kw"], 2)
        rows.append({"item": c, "label": b["label"], "measured_mean_kw": b["measured_mean_kw"], "sim_A_mean_kw": a["sim_mean_kw"],
                     "sim_B_mean_kw": b["sim_mean_kw"], "residual_kw": resid[c], "nmbe_pct": b["nmbe_pct"],
                     "cv_rmse_pct": b["cv_rmse_pct"], "g14_pass": b["g14_pass"]})
    text = []
    exact = [lab(c) for c in calib_items if abs(resid[c]) < 0.005]
    left = [c for c in calib_items if abs(resid[c]) >= 0.005]
    if exact:
        text.append(f"{'·'.join(exact)}{'은' if len(exact) == 1 else '는'} 평균이 정확히 맞습니다.")
    fan_up = "crah_fan_heat_ratio" in over and over["crah_fan_heat_ratio"] > _coef(cp, "crah_fan_heat_ratio")
    coupled = [c for c in left if resid[c] > 0 and set(mapping["items"][c]) & {"tower_fan", "cw_pump"}]
    if fan_up and coupled:
        text.append(f"{'·'.join(lab(c) for c in coupled)}{'은' if len(coupled) == 1 else '는'} "
                    f"{'·'.join(_fmt_kw(resid[c]) + ' kW' for c in coupled)} 남습니다. 팬 발열비를 올리면 팬 발열이 공기측 부하와 "
                    "방열량(q_rej)에 더해져, 방열량에 비례하는 냉각탑 팬·냉각수 펌프도 조금 커지기 때문입니다(계수끼리의 결합).")
    other = [c for c in left if c not in coupled]
    if other:
        text.append(f"{'·'.join(lab(c) + ' ' + _fmt_kw(resid[c]) + ' kW' for c in other)} 의 잔차는 보정한 다른 계수가 "
                    "같은 부하(방열량·공기 부하)를 바꿔 생긴 결합 잔차입니다.")
    ch_b = B["chiller_mean_kw"]
    if resid["mech_total"] > 0 and ch_b > 0.5 * resid["mech_total"]:
        text.append(f"기계 합계의 나머지 {_fmt_kw(resid['mech_total'])} kW 는 대부분 보정하지 않은 칠러 전력({ch_b:.2f} kW)입니다.")
    steps.append({"step": 3, "title": "다시 실행해 확인 (시뮬 B)", "rows": rows, "text": " ".join(text)})

    # 4. PUE 기여 분해
    rows = []
    for c in items:
        da = A["items"][c]["sim_mean_kw"] - A["items"][c]["measured_mean_kw"]
        db = B["items"][c]["sim_mean_kw"] - B["items"][c]["measured_mean_kw"]
        rows.append({"item": c, "label": lab(c), "diff_A_kw": round(da, 2), "pue_contrib_A": round(da / it, 4),
                     "diff_B_kw": round(db, 2), "pue_contrib_B": round(db / B["it_mean_kw"], 4)})
    if not any("chiller" in ks for ks in mapping["items"].values()):
        rows.append({"item": "chiller", "label": "칠러", "diff_A_kw": A["chiller_mean_kw"],
                     "pue_contrib_A": round(A["chiller_mean_kw"] / it, 4), "diff_B_kw": B["chiller_mean_kw"],
                     "pue_contrib_B": round(B["chiller_mean_kw"] / B["it_mean_kw"], 4)})
    pos = [r["label"] for r in rows if r["pue_contrib_A"] >= 0.001]
    neg = [r["label"] for r in rows if r["pue_contrib_A"] <= -0.001]
    gross = sum(abs(r["pue_contrib_A"]) for r in rows)
    text = f"PUE: 실측 {A['pue']['measured']} / A {A['pue']['sim']} / B {B['pue']['sim']}. 항목별 기여 = 평균 전력 차 ÷ IT 평균({it:,.1f} kW)."
    if pos and neg and abs(A["pue"]["diff"]) < 0.5 * gross:
        text += (f" A 에서는 {'·'.join(pos)} 과대(+)와 {'·'.join(neg)} 과소(−)가 서로 상쇄돼 PUE 오차가 "
                 f"{A['pue']['diff']:+.4f} 로 작게 보였습니다. PUE 하나만 맞는다고 항목이 맞는 것은 아닙니다.")
    steps.append({"step": 4, "title": "PUE 변화의 분해", "rows": rows, "text": text,
                  "pue": {"measured": A["pue"]["measured"], "A": A["pue"]["sim"], "B": B["pue"]["sim"]}})

    # 5. 검증 기간
    if C:
        mt = C["items"]["mech_total"]
        ok = mt["g14_pass"]
        text = (f"같은 B 계수를 IT 평균 {C['it_mean_kw']:,.1f} kW 인 {C['period'][0]} ~ {C['period'][1]} 에 적용하면 기계 합계 "
                f"NMBE {mt['nmbe_pct']:+.1f}%, CV(RMSE) {mt['cv_rmse_pct']}% (기준 |NMBE| ≤ {G14_NMBE:g}%, CV ≤ {G14_CV:g}%) 로 "
                + ("기준 안에 들어옵니다." if ok else "기준을 벗어납니다."))
        it_g = C["it_mean_kw"] / A["it_mean_kw"]
        m_g = mt["measured_mean_kw"] / A["items"]["mech_total"]["measured_mean_kw"]
        if not ok and it_g > 1.05 and m_g < it_g and mt["nmbe_pct"] > 0:
            text += (f" 실측은 IT 가 {it_g:.2f}배가 되어도 기계 전력이 {A['items']['mech_total']['measured_mean_kw']:.2f} → "
                     f"{mt['measured_mean_kw']:.2f} kW ({m_g:.2f}배)로 덜 늘지만, 엔진은 부하에 비례해 늘기 때문입니다. "
                     "비례 계수만으로는 고칠 수 없는 구조 오차입니다(구조 검증 참고).")
        rows = [{"item": c, "label": C["items"][c]["label"], "measured_mean_kw": C["items"][c]["measured_mean_kw"],
                 "sim_mean_kw": C["items"][c]["sim_mean_kw"], "nmbe_pct": C["items"][c]["nmbe_pct"],
                 "cv_rmse_pct": C["items"][c]["cv_rmse_pct"], "g14_pass": C["items"][c]["g14_pass"]} for c in [*items, "mech_total"]]
        steps.append({"step": 5, "title": "보정의 한계 — 검증 기간에 적용", "rows": rows, "text": text,
                      "g14_pass": ok, "pue": C["pue"]})
    return steps


def steps_markdown(steps) -> str:
    """보정 단계 → 마크다운 (보고서·대조용)"""
    md = ["# 시뮬 A → 시뮬 B 보정 단계", ""]
    for s in steps:
        md += [f"## {s['step']}. {s['title']}", ""]
        md += [f"- {b}" for b in s.get("bullets", [])]
        if s.get("rows"):
            keys = [k for k in s["rows"][0] if k not in ("item", "calibrated")]
            md += ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
            md += ["| " + " | ".join("" if r.get(k) is None else str(r.get(k)) for k in keys) + " |" for r in s["rows"]]
        if s.get("text"):
            md += ["", s["text"]]
        md.append("")
    return "\n".join(md)


# ---------- 분석 전용: 구조 검증 ----------
def _X(j):
    return np.column_stack([np.ones(len(j["h"])), j["it"], np.clip(j["wb"], 0, None), np.clip(-j["db"], 0, None)])


def structure_check(jB, jC, mapping):
    """실측 = 고정분 + a·IT + b·max(습구, 0) + c·max(−건구, 0) 최소제곱. 보정 기간에 맞추고 검증 기간에 적용해
    엔진 비례 보정(C)과 비교 — 분석용이며 엔진에 넣지 않는다"""
    out = {}
    labels = {**mapping.get("labels", {}), "mech_total": "기계 합계"}
    for c in [*mapping["items"], "mech_total"]:
        beta, *_ = np.linalg.lstsq(_X(jB), jB["meas"][c], rcond=None)
        r = {"label": labels.get(c, c),
             "coef": {"fixed_kw": round(float(beta[0]), 2), "per_it_kw": round(float(beta[1]), 5),
                      "per_wb_k_above0": round(float(beta[2]), 3), "per_k_below0_dry": round(float(beta[3]), 3)},
             "fixed_share_of_mean_cal": round(float(beta[0] / jB["meas"][c].mean()), 2)}
        if jC is not None:
            pred = _X(jC) @ beta
            r["engine_calibrated_test"] = dict(zip(("nmbe_pct", "cv_rmse_pct"), g14(jC["meas"][c], jC["sim"][c])))
            r["affine_test"] = dict(zip(("nmbe_pct", "cv_rmse_pct"), g14(jC["meas"][c], pred)))
        out[c] = r
    return out


# ---------- 분석 전용: 칠러 필요 시간 민감도 ----------
def site_pressure_kpa(elevation_m):
    return 101.325 * (1 - 2.25577e-5 * elevation_m) ** 5.25588      # 표준대기


def wb_psy(t, rh, p_kpa):
    """ASHRAE 습공기식, 대기압 p 에서의 습구온도 (이분법)"""
    pv = rh / 100 * p_sat(t)
    w = 0.622 * pv / (p_kpa - pv)
    lo, hi = -40.0, t
    for _ in range(60):
        tw = (lo + hi) / 2
        ws = 0.622 * p_sat(tw) / (p_kpa - p_sat(tw))
        w_tw = ((2501 - 2.326 * tw) * ws - 1.006 * (t - tw)) / (2501 + 1.86 * t - 4.186 * tw)
        lo, hi = (tw, hi) if w_tw < w else (lo, tw)
    return tw


def sensitivity_chiller_hours(hours, sc, source):
    """칠러 필요 시간: 엔진과 같은 판정(냉각탑 출구 + 열교환기 접근 > 공급 수온)으로 해수면 습구 vs 사이트 고도 습구,
    접근온도 변경별. hours = [(건구, 상대습도)]"""
    cp = sc["cooling_plant"]
    pp = lambda k: _coef(cp, k)
    elev = sc["site"].get("elevation_m")
    liquid = pp("architecture") != "air_chw"
    sea = [wet_bulb(t, rh) for t, rh in hours]
    alt = [wb_psy(t, rh, site_pressure_kpa(elev)) for t, rh in hours] if elev is not None else None

    def count(wbs, tower, hx, cdu):
        supply = pp("tcs_supply_c") - cdu if liquid else cp["chw_supply_c"]
        return sum(1 for x in wbs if x + tower + hx > supply)

    tw, hx, cdu = pp("tower_approach_k"), pp("hx_approach_k"), pp("cdu_approach_k")
    loop = "CDU" if liquid else "공기측"
    rows = [{"label": f"기본 (해수면 습구, 냉각탑 {tw:g} K + 열교환기 {hx:g} K" + (f" + CDU {cdu:g} K)" if liquid else ")"),
             "wet_bulb": "sea", "tower_k": tw, "hx_k": hx, "cdu_k": cdu, "hours": count(sea, tw, hx, cdu)}]
    if liquid:
        rows.append({"label": f"CDU 접근 {cdu:g} → 1.5 K", "wet_bulb": "sea", "tower_k": tw, "hx_k": hx, "cdu_k": 1.5,
                     "hours": count(sea, tw, hx, 1.5)})
    if alt is not None:
        rows.append({"label": f"고도 {elev:,.0f} m 습구로 보정", "wet_bulb": "site", "tower_k": tw, "hx_k": hx, "cdu_k": cdu,
                     "hours": count(alt, tw, hx, cdu)})
        if liquid:
            rows.append({"label": "고도 보정 + CDU 1.5 K", "wet_bulb": "site", "tower_k": tw, "hx_k": hx, "cdu_k": 1.5,
                         "hours": count(alt, tw, hx, 1.5)})
            rows.append({"label": "고도 보정 + CDU 1.5 K + 냉각탑 접근 3 K", "wet_bulb": "site", "tower_k": 3.0, "hx_k": hx,
                         "cdu_k": 1.5, "hours": count(alt, 3.0, hx, 1.5)})
    out = {"source": source, "hours_total": len(hours), "loop": loop, "elevation_m": elev, "rows": rows,
           "max_wet_bulb_sea_c": round(max(sea), 2) if sea else None,
           "note": "엔진 습구식(Stull)은 해수면 기준. 고도 습구는 분석에만 쓰고 엔진에는 넣지 않음"}
    if alt is not None:
        diff = [s - a for (t, _), s, a in zip(hours, sea, alt) if t > 20]
        out.update(site_pressure_kpa=round(site_pressure_kpa(elev), 2), max_wet_bulb_alt_c=round(max(alt), 2) if alt else None,
                   wet_bulb_bias_when_t_gt_20c_mean_k=round(sum(diff) / len(diff), 2) if diff else None)
    return out


# ---------- 전체 실행 + 디스크 캐시 ----------
def cache_key(sc, data_hash, mapping, periods, weather_desc):
    """계산에 쓰는 값만 (provenance 는 기록 시각이 들어 있어 제외)"""
    sc = {k: v for k, v in sc.items() if k != "provenance"}
    return hashlib.sha1(json.dumps([VERSION, sc, data_hash, mapping, periods, weather_desc], sort_keys=True,
                                   default=str, ensure_ascii=False).encode()).hexdigest()[:16]


def cache_get(key, suffix=""):
    p = CACHE_DIR / f"{key}{suffix}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def cache_put(key, data, suffix=""):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}{suffix}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _period(p):
    return tuple(x if isinstance(x, date) else date.fromisoformat(str(x)) for x in p)


def run(sc, ds, mapping, periods, weather, facility=None, sensitivity_hours=None):
    """A·B·C 실행 + 지표·월별·일별·시간값 + 보정 단계 + 구조 검증 + 칠러 민감도"""
    cal = _period(periods["cal"])
    test = _period(periods["test"]) if periods.get("test") else None
    sc = Scenario.model_validate(sc).to_engine_dict()
    t0 = datetime.now()
    jA, srcA = run_period(sc, ds, mapping, *cal, weather)
    A = {"period": [d.isoformat() for d in cal], "weather_days": srcA, **metrics(jA, mapping, facility)}
    over, ratios = calibrate(jA, sc, mapping)
    jB, srcB = run_period(sc, ds, mapping, *cal, weather, over)
    B = {"period": A["period"], "weather_days": srcB, "coefficients": over, **metrics(jB, mapping, facility)}
    runs, tables = {"A": A, "B": B}, {"A": jA, "B": jB}
    jC = None
    if test:
        jC, srcC = run_period(sc, ds, mapping, *test, weather, over)
        runs["C"] = {"period": [d.isoformat() for d in test], "weather_days": srcC, "coefficients": over,
                     **metrics(jC, mapping, facility)}
        tables["C"] = jC
    for k, r in runs.items():
        r["label"] = RUN_LABEL[k]
    hrs, sens_src = sensitivity_hours, "EPW 전체 시간 (TMY3)"
    if hrs is None:                    # EPW 가 없으면 보정 기간에 실제로 쓴 기상
        hrs, sens_src = [], "보정 기간 시뮬 기상"
        for d in range((cal[1] - cal[0]).days + 1):
            w, _ = weather.day(date.fromordinal(cal[0].toordinal() + d))
            hrs += list(zip(w["dry_bulb_c"], w["rh_pct"]))
    return {
        "sign_note": SIGN_NOTE, "g14": {"nmbe_pct": G14_NMBE, "cv_rmse_pct": G14_CV},
        "runs": runs,
        "monthly": {k: monthly(j, mapping) for k, j in tables.items()},
        "daily": {k: daily(j, mapping) for k, j in tables.items()},
        "hourly": {k: hourly_table(j, mapping) for k, j in tables.items()},
        "calibration": {"coefficients": over, "ratios": {c: round(r, 4) for c, r in ratios.items()},
                        "steps": calibration_steps(A, B, runs.get("C"), sc, mapping, over, ratios)},
        "structure": structure_check(jB, jC, mapping),
        "sensitivity": sensitivity_chiller_hours(hrs, sc, sens_src),
        "elapsed_s": round((datetime.now() - t0).total_seconds(), 1),
    }
