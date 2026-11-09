"""
BMS 실측 보정 (Phase 12) - 모델 계수를 실측에 맞춘다. 보정은 '계수' 만 바꾸고 설계·운전 변수는 건드리지 않는다.

단계 (앞 단계 결과를 고정한 채 다음 단계를 푼다 → 계수끼리 서로 보상하는 식별성 문제 완화):
  load    기타 발열 internal.misc_kw                          ← 총 냉방부하
  chiller 칠러 정격 COP, 습구 보정률                            ← 칠러 전력·COP
  free    냉각탑 접근온도                                       ← 칠러 전력 (프리쿨링 구간이 있을 때만)
  fan     CRAH 팬 kW/(m3/s)                                     ← CRAH 팬 전력 (연성 모델)
  pump    펌프 종합효율                                         ← 펌프 전력 (연성 모델)
  airflow 재순환 배율 recirculation_scale                       ← 랙 흡입온도 최대 (4시간 간격 시각)
  water   냉각탑 농축배수                                       ← 보급수
방법: 정규화 잔차((계산-실측)/실측 평균)의 유계 Levenberg–Marquardt (유한차분, numpy 직접 구현 - scipy 불필요)
검증: 날짜 앞 70% 로 보정, 뒤 30% 로 검증 (14일 미만이면 전체로 보정·검증하고 '검증 기간 부족' 표시)
판정: 시간 단위 CV(RMSE) <= 30%, |NMBE| <= 10% (ASHRAE Guideline 14 시간 기준)
"""
import copy
import math
import uuid
from datetime import date

import numpy as np

from .airflow import analyze
from .engine import simulate
from .itprofile import day_kw
from .plant import energy, networks, peak_hour
from .provenance import get_path
from .schemas import CoolingLayout

STAGES = [
    ("load", "부하", [("internal.misc_kw", None, None)], ["total_cooling_kw"]),
    ("chiller", "칠러", [("cooling_plant.chiller_cop_rated", 2.0, 10.0), ("cooling_plant.cop_wb_sensitivity", 0.0, 0.06)],
     ["chiller_kw", "chiller_cop"]),
    ("free", "프리쿨링", [("cooling_plant.tower_approach_k", 1.5, 8.0)], ["chiller_kw"]),
    ("fan", "CRAH 팬", [("cooling_layout.crah.fan_kw_per_m3_s", 0.1, 1.0)], ["crah_fan_kw"]),
    ("pump", "펌프", [("cooling_layout.piping.pump_efficiency", 0.4, 0.9)], ["pump_kw"]),
    ("airflow", "기류 재순환", [("cooling_layout.recirculation_scale", 0.3, 2.0)], ["rack_inlet_max_c"]),
    ("water", "냉각탑 물", [("cooling_plant.tower_cycles_of_concentration", 2.0, 10.0)], ["makeup_m3_h"]),
]
PARAM_LABEL = {"internal.misc_kw": "기타 발열 [kW]", "cooling_plant.chiller_cop_rated": "칠러 정격 COP",
               "cooling_plant.cop_wb_sensitivity": "습구 1℃당 COP 보정률",
               "cooling_plant.tower_approach_k": "냉각탑 접근온도 [K]",
               "cooling_layout.crah.fan_kw_per_m3_s": "CRAH 팬 kW/(m³/s)",
               "cooling_layout.piping.pump_efficiency": "펌프 종합효율",
               "cooling_layout.recirculation_scale": "재순환 배율",
               "cooling_plant.tower_cycles_of_concentration": "냉각탑 농축배수"}
INLET_HOURS = (0, 4, 8, 12, 16, 20)
CV_MAX, NMBE_MAX = 30.0, 10.0


def _set(sc, path, v):
    keys = path.split(".")
    cur = sc
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = v


def _fill24(vals):
    """None 섞인 24개 → 순환 선형 보간 (값이 18개 미만이면 None)"""
    known = [h for h, v in enumerate(vals) if v is not None]
    if len(known) < 18:
        return None
    out = list(vals)
    for h in range(24):
        if out[h] is None:
            p = max((k for k in known if k < h), default=known[-1] - 24)
            n = min((k for k in known if k > h), default=known[0] + 24)
            vp, vn = vals[p % 24], vals[n % 24]
            out[h] = vp + (vn - vp) * (h - p) / (n - p)
    return out


def prepare_days(sc, bms_ds, weather_days=None, it_ds=None):
    """보정에 쓸 날: [{date, weather, util, meas}], 메모. weather_days: {date: weather} (Open-Meteo 과거값 등)"""
    days, notes, it_how = [], [], {"bms": 0, "dataset": 0, "scenario": 0}
    wd = weather_days or {}
    dk = sc["it_load"]["design_kw"] or 1.0
    skipped = 0
    for ds, m in sorted(bms_ds["days"].items()):
        base = wd.get(ds)
        t = _fill24(m["outdoor_temp_c"]) if "outdoor_temp_c" in m else None
        rh = _fill24(m["outdoor_rh_pct"]) if "outdoor_rh_pct" in m else None
        if t and rh:
            w = {"dry_bulb_c": [round(x, 2) for x in t], "rh_pct": [round(min(max(x, 0), 100), 1) for x in rh],
                 "ghi_w_m2": base["ghi_w_m2"] if base else sc["weather"]["ghi_w_m2"]}
        elif base:
            w = base
        else:
            skipped += 1
            continue
        if "it_kw" in m and _fill24(m["it_kw"]):
            util, how = [v / dk for v in _fill24(m["it_kw"])], "bms"
        elif it_ds and ds in it_ds["daily_total_kw"]:
            util, how = [v / dk for v in day_kw(it_ds, date.fromisoformat(ds))[0]], "dataset"
        else:
            util, how = list(sc["it_load"]["utilization"]), "scenario"
        it_how[how] += 1
        days.append({"date": ds, "weather": w, "util": util, "meas": m})
    if skipped:
        notes.append(f"기상 자료가 없는 {skipped}일은 제외 (BMS 외기 열 또는 Open-Meteo 과거값 필요)")
    if not days:
        raise ValueError("보정에 쓸 날이 없습니다 — BMS 에 외기 온습도 열을 넣거나, 인터넷 연결(Open-Meteo 과거 기상)을 확인하세요")
    if it_how["scenario"]:
        notes.append(f"IT 전력 정보가 없는 {it_how['scenario']}일은 시나리오 24시간 가동률을 사용 (보정 정확도가 낮아질 수 있음)")
    notes.append(f"IT 소스: BMS {it_how['bms']}일 · IT 데이터셋 {it_how['dataset']}일 · 시나리오 {it_how['scenario']}일")
    return days, notes


def _layout(sc):
    return CoolingLayout.model_validate(sc.get("cooling_layout") or {}).model_dump()


def predict(sc, days, metrics):
    """현재 계수로 날마다 계산 → {metric: (D,24) 배열, 없는 시각 nan}"""
    cfg = _layout(sc)
    out = {k: np.full((len(days), 24), np.nan) for k in metrics}
    need_coupled = bool({"crah_fan_kw", "pump_kw"} & set(metrics))
    for i, d in enumerate(days):
        s = {**sc, "weather": d["weather"], "site": {**sc["site"], "date": d["date"]},
             "it_load": {**sc["it_load"], "utilization": d["util"]}}
        if metrics == ["rack_inlet_max_c"]:
            for h in INLET_HOURS:
                out["rack_inlet_max_c"][i, h] = analyze(s, cfg, h)["metrics"]["max_inlet_c"]
            continue
        res = simulate(s, 60)
        hs = res["hourly"]
        for k, f in (("total_cooling_kw", lambda x: x["load_kw"]["total_cooling"]),
                     ("chiller_kw", lambda x: x["chiller"]["power_kw"]), ("chiller_cop", lambda x: x["chiller"]["cop"]),
                     ("makeup_m3_h", lambda x: x["makeup_water_m3_h"]["total"])):
            if k in out:
                out[k][i] = [f(x) for x in hs]
        if need_coupled:
            fan = analyze(s, cfg, peak_hour(s))["metrics"]["crah_fan_kw"]
            cp = s["cooling_plant"]
            nets = networks(s, cfg, res, cp["chw_return_c"] - cp["chw_supply_c"])
            hrs, _ = energy(s, cfg, res, fan, cp["chw_supply_c"], cp["chw_return_c"], nets)
            if "crah_fan_kw" in out:
                out["crah_fan_kw"][i] = fan
            if "pump_kw" in out:
                out["pump_kw"][i] = [h["chw_pump_kw"] + h["cw_pump_kw"] for h in hrs]
        if "rack_inlet_max_c" in out:
            for h in INLET_HOURS:
                out["rack_inlet_max_c"][i, h] = analyze(s, cfg, h)["metrics"]["max_inlet_c"]
    return out


def measured(days, metric):
    return np.array([[np.nan if v is None else v for v in d["meas"].get(metric, [None] * 24)] for d in days], float)


def stats(pred, meas):
    mask = ~np.isnan(pred) & ~np.isnan(meas)
    if mask.sum() < 6:
        return None
    e = pred[mask] - meas[mask]
    mu = float(np.mean(meas[mask]))
    rmse = float(np.sqrt(np.mean(e ** 2)))
    if abs(mu) < 1e-9:
        return {"n": int(mask.sum()), "rmse": round(rmse, 3), "cv_rmse_pct": None, "nmbe_pct": None, "pass": None}
    cv = rmse / abs(mu) * 100
    nmbe = float(np.sum(e)) / (mask.sum() * mu) * 100
    return {"n": int(mask.sum()), "rmse": round(rmse, 3), "cv_rmse_pct": round(cv, 2), "nmbe_pct": round(nmbe, 2),
            "pass": bool(cv <= CV_MAX and abs(nmbe) <= NMBE_MAX)}


def _lm(fun, x0, lo, hi, iters=20):
    """유계 Levenberg–Marquardt. fun(x) → 잔차 벡터"""
    x = np.clip(np.asarray(x0, float), lo, hi)
    r = fun(x)
    cost, lam, n_eval = float(r @ r), 1e-2, 1
    for _ in range(iters):
        J = np.zeros((len(r), len(x)))
        for j in range(len(x)):
            h = 1e-3 * (hi[j] - lo[j])
            xh = x.copy()
            xh[j] = xh[j] + h if xh[j] + h <= hi[j] else xh[j] - h
            J[:, j] = (fun(xh) - r) / (xh[j] - x[j])
            n_eval += 1
        A, g = J.T @ J, J.T @ r
        improved = False
        for _ in range(6):
            step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -g)
            xn = np.clip(x + step, lo, hi)
            rn = fun(xn)
            n_eval += 1
            cn = float(rn @ rn)
            if cn < cost:
                rel = (cost - cn) / max(cost, 1e-12)
                x, r, cost, lam, improved = xn, rn, cn, lam / 3, True
                break
            lam *= 4
        if not improved or rel < 1e-6:
            break
    return x, cost, n_eval


def run(sc, bms_ds, weather_days=None, it_ds=None, stages=None, progress=None):
    sc = copy.deepcopy(sc)
    sc["cooling_layout"] = _layout(sc)
    days, notes = prepare_days(sc, bms_ds, weather_days, it_ds)
    have = {k for d in days for k, v in d["meas"].items() if any(x is not None for x in v)}
    n = len(days)
    short = n < 14
    split = n if short else max(1, int(round(n * 0.7)))
    train, valid = days[:split], (days if short else days[split:])
    if short:
        notes.append(f"데이터 {n}일 — 14일 미만이라 같은 기간으로 보정·검증 (검증 기간 부족)")
    metrics_all = [m for m in ("total_cooling_kw", "chiller_kw", "chiller_cop", "crah_fan_kw", "pump_kw",
                               "rack_inlet_max_c", "makeup_m3_h") if m in have]
    if not metrics_all:
        raise ValueError("보정에 쓸 지표 열이 없습니다 (총 냉방부하·칠러 전력·팬·펌프·흡입온도·보급수 중 하나 이상)")

    def evaluate_set(s, dd):
        res = {}
        for m in metrics_all:
            pred = predict(s, dd, [m])[m]
            res[m] = stats(pred, measured(dd, m))
        return res
    before = evaluate_set(sc, valid)
    params, skipped = [], []
    todo = [st for st in STAGES if stages is None or st[0] in stages]
    for k, (sid, label, plist, mets) in enumerate(todo):
        if progress:
            progress(k / (len(todo) + 1), f"{label} 보정")
        mets = [m for m in mets if m in have]
        if not mets:
            skipped.append({"stage": sid, "label": label, "reason": f"{label} 관련 실측 열이 없음"})
            continue
        if sid == "free":
            ch = measured(train, "chiller_kw")
            mx = np.nanmax(ch) if np.any(~np.isnan(ch)) else 0
            if not mx or not np.any(ch < 0.05 * mx):
                skipped.append({"stage": sid, "label": label, "reason": "칠러가 멈춘(프리쿨링) 시간이 실측에 없음"})
                continue
        if sid == "chiller" and "chiller_kw" in mets:
            mets = ["chiller_kw"]
        bounds = []
        for path, lo, hi in plist:
            if path == "internal.misc_kw":
                lim = 0.2 * sc["it_load"]["design_kw"]
                lo, hi = -lim, lim
            bounds.append((path, lo, hi))
        x0 = np.array([get_path(sc, p) if get_path(sc, p) is not None else 0.0 for p, _, _ in bounds], float)
        lo = np.array([b[1] for b in bounds])
        hi = np.array([b[2] for b in bounds])
        meas = {m: measured(train, m) for m in mets}
        scale = {m: max(abs(float(np.nanmean(meas[m]))), 1e-6) for m in mets}

        def fun(x, bounds=bounds, mets=mets, meas=meas, scale=scale):
            s = copy.deepcopy(sc)
            for (p, _, _), v in zip(bounds, x):
                _set(s, p, float(v))
            pr = predict(s, train, mets)
            parts = []
            for m in mets:
                e = (pr[m] - meas[m]) / scale[m]
                parts.append(np.where(np.isnan(e), 0.0, e).ravel())
            return np.concatenate(parts)
        x, _, n_eval = _lm(fun, x0, lo, hi)
        for (p, l_, h_), v0, v in zip(bounds, x0, x):
            rv = round(float(v), 4)
            _set(sc, p, rv)
            at_bound = bool(math.isclose(v, l_, abs_tol=1e-3 * (h_ - l_)) or math.isclose(v, h_, abs_tol=1e-3 * (h_ - l_)))
            params.append({"path": p, "label": PARAM_LABEL[p], "stage": sid, "stage_label": label,
                           "before": round(float(v0), 4), "after": rv, "bounds": [l_, h_], "at_bound": at_bound,
                           "metrics": mets, "evaluations": n_eval})
    if progress:
        progress(len(todo) / (len(todo) + 1), "검증")
    after = evaluate_set(sc, valid)
    m0 = metrics_all[0]
    series = None
    if valid:
        vd = valid[:7]
        sc0 = copy.deepcopy(sc)
        for p in params:
            _set(sc0, p["path"], p["before"])
        series = {"metric": m0, "dates": [d["date"] for d in vd],
                  "measured": [None if np.isnan(v) else round(float(v), 2) for v in measured(vd, m0).ravel()],
                  "before": [None if np.isnan(v) else round(float(v), 2) for v in predict(sc0, vd, [m0])[m0].ravel()],
                  "after": [None if np.isnan(v) else round(float(v), 2) for v in predict(sc, vd, [m0])[m0].ravel()]}
    ok = [a["pass"] for a in after.values() if a and a["pass"] is not None]
    verdict = "pass" if ok and all(ok) else "partial" if any(ok) else "fail"
    return {"run_id": uuid.uuid4().hex[:12], "params": params, "skipped": skipped,
            "metrics": [{"metric": m, "before": before[m], "after": after[m]} for m in metrics_all],
            "verdict": verdict, "short": short, "n_days": n, "train_days": len(train), "valid_days": len(valid),
            "series": series, "notes": notes,
            "criteria": f"시간 단위 CV(RMSE) ≤ {CV_MAX:g}%, |NMBE| ≤ {NMBE_MAX:g}% (ASHRAE Guideline 14)"}
