"""
운전 브리핑 (Phase 14) - 내일: 예보 기상 + IT 예측 → 냉방부하·프리쿨링 시간·권장 운전점·위험 알림.
다음 달: 예보가 없으므로 지역 관측소의 지난 관측 연도 같은 달(기상청 ASOS) 날씨로 한 달을 계산 (next_month).

- 기상: 기상청 단기예보 (실패하면 지역 관측소 연간 관측 파일의 같은 월·일 값으로 대신하고 크게 표시)
- IT: IT 데이터셋이 있으면 최근 4주 같은 요일 평균 x 추세, 없으면 시나리오 24시간 가동률
- 현재 운전: 월별 스케줄이 있으면 그 달 값, 없으면 현재 운전점 / 권장: 현재 구조에서 내일 하루 기준 최소 전력 운전점
- 모델: 연성 모델 (하루 최적화와 같은 제약: 내일 IT 피크 시각 랙 흡입온도 상한, 코일 접근온도)
- 권장값은 표시만 한다. 적용은 사용자가 결정 (/outlook/apply)
"""
import calendar
import copy
import math
from datetime import date

from .airflow import analyze
from .annual_opt import CHW_MIN, DTS, RATIOS, SUPPLY_MAX, SUPPLY_MIN
from .engine import simulate, simulate_days
from .itprofile import forecast_kw
from .plant import energy, networks, peak_hour
from .plant_vec import day_matrix, energy_matrix

MARGIN_WARN_C = 1.0      # 흡입온도 여유 주의 기준 [K] (가정)
CAPACITY_WARN = 0.9      # 설비 용량 대비 피크 비율 주의 기준 (가정)
WD = "월화수목금토일"


def _windows(modes, want):
    """시간별 모드 → 'HH~HH시' 구간 목록"""
    out, start = [], None
    for h in range(25):
        on = h < 24 and modes[h] == want
        if on and start is None:
            start = h
        if not on and start is not None:
            out.append(f"{start:02d}~{h:02d}시")
            start = None
    return out


def _power_note(hrs_c, hrs_r, wbs, cur, rec, cur_label):
    """현재 vs 권장 전력 곡선이 왜 그런 모양인지 (칠러 켜지는 시간, 팬 전력 차이, 하루 합계)"""
    on_c = [h["chiller_kw"] > 0.5 for h in hrs_c]
    on_r = [h["chiller_kw"] > 0.5 for h in hrs_r]
    out = []
    if any(on_c):
        hs = [k for k in range(24) if on_c[k]]
        peak = max(hrs_c, key=lambda h: h["total_kw"])
        out.append(f"{cur_label}(냉수 {cur['chw_supply_c']:g}/{cur['chw_return_c']:g}℃)은 {', '.join(_windows(on_c, True))}에 "
                   f"습구온도가 {min(wbs[k] for k in hs):.1f}~{max(wbs[k] for k in hs):.1f}℃라 냉각탑 물만으로는 냉수를 충분히 식히지 못해 "
                   f"칠러가 켜집니다 (최대 {peak['total_kw']:.0f} kW, {peak['hour']}시, 칠러 {peak['chiller_kw']:.0f} kW).")
    if sum(on_r) < sum(on_c):
        out.append(f"권장 운전점은 냉수를 {rec['chw_supply_c']:g}/{rec['chw_return_c']:g}℃로 올려 프리쿨링 시간을 늘립니다"
                   + (f" (칠러 {', '.join(_windows(on_r, True))}만 가동)." if any(on_r) else " (칠러 하루 종일 정지)."))
    fan_d = hrs_r[0]["crah_fan_kw"] - hrs_c[0]["crah_fan_kw"]
    worse = [k for k in range(24) if hrs_r[k]["total_kw"] > hrs_c[k]["total_kw"] + 0.5]
    if worse and fan_d > 0.5:
        out.append(f"대신 급기를 {rec['supply_temp_c']:g}℃로 올리면서 흡입 여유 {MARGIN_WARN_C:g}℃를 지키려고 풍량을 "
                   f"{cur['airflow_ratio'] * 100:.0f}→{rec['airflow_ratio'] * 100:.0f}%로 높여 CRAH 팬이 {fan_d:+.0f} kW 늘어, "
                   f"{', '.join(_windows([k in worse for k in range(24)], True))}에는 권장 쪽 전력이 더 큽니다.")
    elif worse:
        out.append(f"{', '.join(_windows([k in worse for k in range(24)], True))}에는 권장 쪽 전력이 더 큽니다 "
                   f"(냉수 ΔT 가 달라 펌프·칠러 부하 분담이 바뀜).")
    if worse:
        out.append("운전점은 하루 하나로 정하므로, 시간대별로 손해와 이득을 합한 하루 합계가 가장 작은 값을 고른 결과입니다.")
    tc, tr = sum(h["total_kw"] for h in hrs_c), sum(h["total_kw"] for h in hrs_r)
    out.append(f"하루 합계 {tc:,.0f} → {tr:,.0f} kWh ({(tr - tc) / max(tc, 1e-9) * 100:+.1f}%).")
    return " ".join(out)


def _search(sc, cfg, res, also=(), margin=MARGIN_WARN_C, results=None):
    """권장 운전점 탐색 — 제약 격자 전수 탐색 (현재 구조 고정).

    결정변수: CRAH 풍량 배율(RATIOS 13단) x 냉수 ΔT(DTS 5단) = 65 조합, 조합마다 나머지는 해석적으로 정함:
      급기온도   = (흡입 상한 - 여유 margin - 흡입 상승폭) 을 0.5℃ 내림   (상승폭은 급기온도와 무관 → 역산)
      냉수 공급  = 급기온도 - 코일 접근온도,  냉수 환수 = 냉수 공급 + ΔT
    제약: 흡입 여유 ≥ margin, 냉수 환수 ≤ CRAH 환기 - 코일 환수 접근온도, 급기·냉수 하한/상한.
          sc(내일) 피크 시각과 also 의 각 시나리오(현재 '현황' 하루) 피크 시각을 모두 만족 → 적용 후 어느 화면에서도 주의·경고 없음
    목적: 내일 하루(results 를 주면 그 날들 전체) 연성 냉방 전력량 [kWh] (칠러 + CRAH 팬 + 냉수·냉각수 펌프) 최소"""
    dm = day_matrix(results or [res])
    limit, app, ret_app = cfg["inlet_limit_c"], cfg["coil_approach_k"], cfg["coil_return_approach_k"]
    states = [sc, *also]
    best = None
    for ratio in RATIOS:
        c = copy.deepcopy(cfg)
        c["crah"]["airflow_ratio"] = ratio
        ms = [analyze(s, c, peak_hour(s))["metrics"] for s in states]
        m = ms[0]
        rise = max(x["max_inlet_c"] - c["supply_temp_c"] for x in ms)
        ret_rise = min(x["crah_return_c"] - c["supply_temp_c"] for x in ms)
        t = min(math.floor((limit - margin - rise - 0.01) * 2) / 2, SUPPLY_MAX)
        ret = t + ret_rise
        cs = round(t - app, 2)
        if t < SUPPLY_MIN or cs < CHW_MIN:
            continue
        for dt in DTS:
            cr = round(cs + dt, 2)
            if cr > ret - ret_app + 1e-9:
                continue
            nets = networks(sc, c, res, dt)
            kwh = float(energy_matrix(dm, sc, c, m["crah_fan_kw"], cs, cr, nets)["total"].sum())
            if best is None or kwh < best["kwh"]:
                best = {"kwh": kwh, "supply_temp_c": t, "chw_supply_c": cs, "chw_return_c": cr, "airflow_ratio": ratio,
                        "max_inlet_c": round(rise + t, 2), "fan_kw": m["crah_fan_kw"], "margin_target": margin}
    return best


def _coupled(sc, cfg, res, sp):
    c = copy.deepcopy(cfg)
    c["crah"]["airflow_ratio"], c["supply_temp_c"] = sp["airflow_ratio"], sp["supply_temp_c"]
    s = {**sc, "cooling_plant": {**sc["cooling_plant"], "chw_supply_c": sp["chw_supply_c"],
                                  "chw_return_c": sp["chw_return_c"]}}
    m = analyze(s, c, peak_hour(s))["metrics"]
    nets = networks(s, c, res, sp["chw_return_c"] - sp["chw_supply_c"])
    hrs, daily = energy(s, c, res, m["crah_fan_kw"], sp["chw_supply_c"], sp["chw_return_c"], nets)
    return hrs, daily, m


def tomorrow(sc, cfg, target: date, weather_day, it_ds=None, capacity_kw=None):
    notes = []
    if it_ds:
        kw, info = forecast_kw(it_ds, target)
        dk = sc["it_load"]["design_kw"] or 1.0
        util = [v / dk for v in kw]
        it_note = f"IT: {info['basis']} x 추세 {info['trend_factor']:.3f} (데이터 마지막 날 {info['data_last_day']})"
    else:
        util = list(sc["it_load"]["utilization"])
        it_note = "IT: IT 데이터셋이 없어 시나리오 24시간 가동률 사용 — 'IT 데이터'에서 기간 데이터를 올리면 요일·추세를 반영합니다"
    notes.append(it_note)
    s = {**sc, "weather": weather_day["weather"], "site": {**sc["site"], "date": target.isoformat()},
         "it_load": {**sc["it_load"], "utilization": util}}
    res = simulate(s, 60)
    sched = {e["month"]: e for e in (cfg.get("operating_schedule") or [])}
    cp = sc["cooling_plant"]
    e = sched.get(target.month)
    cur = {"supply_temp_c": e["supply_temp_c"], "chw_supply_c": e["chw_supply_c"], "chw_return_c": e["chw_return_c"],
           "airflow_ratio": e["airflow_ratio"]} if e else \
        {"supply_temp_c": cfg["supply_temp_c"], "chw_supply_c": cp["chw_supply_c"], "chw_return_c": cp["chw_return_c"],
         "airflow_ratio": cfg["crah"]["airflow_ratio"]}
    cur_label = f"{target.month}월 스케줄" if e else "현재 운전점"
    hrs_c, day_c, m_c = _coupled(s, cfg, res, cur)
    # 권장값은 적용 후 '현황'(현재 시나리오 하루)에서도 주의·경고가 없도록 두 상태를 함께 만족시킨다.
    # 여유 1℃ 를 지킬 수 없으면 상한만 지키는 운전점으로 물러선다 (그 경우 '여유 작음' 주의는 남음)
    rec = _search(s, cfg, res, also=(sc,)) or _search(s, cfg, res, also=(sc,), margin=0.0)
    hrs_r, day_r, m_r = _coupled(s, cfg, res, rec) if rec else (hrs_c, day_c, m_c)
    peak = max(res["hourly"], key=lambda x: x["load_kw"]["total_cooling"])
    modes = [h["mode"] for h in hrs_r]
    limit = cfg["inlet_limit_c"]
    alerts = []
    margin_c = round(limit - m_c["max_inlet_c"], 2)
    margin_r = round(limit - m_r["max_inlet_c"], 2)
    if margin_c < 0:
        alerts.append({"level": "crit", "text": f"{cur_label}으로는 피크 {peak_hour(s)}시 랙 흡입온도가 상한을 "
                                                f"{-margin_c:.1f}℃ 넘습니다 — 권장 운전점이나 풍량 상향 필요"})
    elif margin_c < MARGIN_WARN_C:
        alerts.append({"level": "warn", "text": f"{cur_label}의 랙 흡입온도 여유가 {margin_c:.1f}℃ 로 작습니다"})
    if rec and margin_r < MARGIN_WARN_C:
        alerts.append({"level": "warn", "text": f"여유 {MARGIN_WARN_C:g}℃ 를 지키는 운전점이 없어, 권장 운전점은 흡입 상한만 지킵니다 "
                                                f"(여유 {margin_r:.1f}℃) — 구조 개선(타일·컨테인먼트·CRAH)이 필요합니다"})
    if capacity_kw:
        ratio = peak["load_kw"]["total_cooling"] / capacity_kw
        if ratio > CAPACITY_WARN:
            alerts.append({"level": "warn" if ratio <= 1 else "crit",
                           "text": f"피크 냉방부하가 설비 용량의 {ratio * 100:.0f}% 입니다"})
    if not rec:
        alerts.append({"level": "warn", "text": "제약을 만족하는 권장 운전점을 찾지 못했습니다 (구조·흡입 상한 확인)"})
    issues = []
    if margin_c < MARGIN_WARN_C:
        from .advice import inlet_issue
        c_cur = copy.deepcopy(cfg)
        c_cur["crah"]["airflow_ratio"], c_cur["supply_temp_c"] = cur["airflow_ratio"], cur["supply_temp_c"]
        s_cur = {**s, "cooling_plant": {**s["cooling_plant"], "chw_supply_c": cur["chw_supply_c"],
                                         "chw_return_c": cur["chw_return_c"]}}
        iss = inlet_issue(s_cur, c_cur, peak_hour(s), label=cur_label, where="내일 피크")
        if iss:
            if e:
                iss["cause"] += f" (월별 스케줄 사용 중: 설정을 바꿔도 {target.month}월은 스케줄 값이 우선 — '권장 운전점 적용'을 쓰세요)"
            issues.append(iss)
    free_w, part_w = _windows(modes, "free"), _windows(modes, "partial")
    n_ch = sum(1 for x in modes if x == "chiller")
    saving = day_c["total"] - (rec["kwh"] if rec else day_c["total"])
    wd = WD[target.weekday()]
    src = weather_day["meta"]
    bullets = [f"최대 냉방부하 {peak['load_kw']['total_cooling']:,.0f} kW ({peak['hour']}시)"
               + (f" — 설비 용량 대비 {peak['load_kw']['total_cooling'] / capacity_kw * 100:.0f}%" if capacity_kw else ""),
               "프리쿨링: " + (", ".join(free_w) + " 칠러 정지" if free_w else "완전 프리쿨링 없음")
               + (f" / 부분 {', '.join(part_w)}" if part_w else "") + f" / 칠러 단독 {n_ch}시간"]
    if rec:
        bullets.append(f"권장 운전점: 급기 {rec['supply_temp_c']:g}℃, 냉수 {rec['chw_supply_c']:g}/{rec['chw_return_c']:g}℃, "
                       f"풍량 {rec['airflow_ratio'] * 100:.0f}% (흡입 여유 {margin_r:.1f}℃) → {cur_label} 대비 "
                       f"{abs(saving):,.0f} kWh({abs(saving) / max(day_c['total'], 1e-9) * 100:.1f}%) "
                       + ("절감" if saving >= 0 else "증가 — 현재 운전점이 흡입 여유 기준을 지키지 못해 안전 쪽으로 조정"))
    wbs = [x["weather"]["wet_bulb_c"] for x in res["hourly"]]
    return {
        "power_note": _power_note(hrs_c, hrs_r, wbs, cur, rec, cur_label) if rec else None,
        "date": target.isoformat(), "weekday": wd,
        "title": f"{target.month}/{target.day}({wd}) · {sc['site']['name']} · {src.get('source_detail', '')}",
        "bullets": bullets, "alerts": alerts, "issues": issues, "notes": notes,
        "period": "day", "weather_source": src.get("source"), "weather_note": src.get("fallback_note"),
        "precip": weather_day.get("precip"),
        "current_label": cur_label, "current": {**cur, "daily_kwh": round(day_c["total"], 0),
                                                 "max_inlet_c": m_c["max_inlet_c"], "inlet_margin_c": margin_c},
        "recommended": None if not rec else {k: rec[k] for k in ("supply_temp_c", "chw_supply_c", "chw_return_c",
                                                                 "airflow_ratio")}
        | {"daily_kwh": round(rec["kwh"], 0), "max_inlet_c": m_r["max_inlet_c"], "inlet_margin_c": margin_r},
        "saving_kwh": round(saving, 0),
        "hourly": [{"hour": x["hour"], "dry_bulb_c": x["weather"]["dry_bulb_c"], "wet_bulb_c": x["weather"]["wet_bulb_c"],
                    "rh_pct": x["weather"]["rh_pct"],
                    "it_kw": x["load_kw"]["it"], "cooling_kw": x["load_kw"]["total_cooling"],
                    "current_kw": hc["total_kw"], "recommended_kw": hr["total_kw"], "mode": hr["mode"],
                    "current_mode": hc["mode"], "current_chiller_kw": hc["chiller_kw"], "recommended_chiller_kw": hr["chiller_kw"],
                    "current_fan_kw": hc["crah_fan_kw"], "recommended_fan_kw": hr["crah_fan_kw"]}
                   for x, hc, hr in zip(res["hourly"], hrs_c, hrs_r)],
        "peak_cooling_kw": peak["load_kw"]["total_cooling"], "makeup_m3": res["daily_total"]["makeup_m3"],
        "weather": weather_day["weather"], "utilization": [round(u, 5) for u in util],
    }


def _month_energy(s_d, cfg, res_d, dm, sp):
    """설계일(s_d) 기류로 팬 전력·흡입온도, 한 달(dm) 전체 시간별 전력 (plant_vec, 칠러+팬+펌프+액체 루프)"""
    c = copy.deepcopy(cfg)
    c["crah"]["airflow_ratio"], c["supply_temp_c"] = sp["airflow_ratio"], sp["supply_temp_c"]
    s = {**s_d, "cooling_plant": {**s_d["cooling_plant"], "chw_supply_c": sp["chw_supply_c"],
                                   "chw_return_c": sp["chw_return_c"]}}
    m = analyze(s, c, peak_hour(s))["metrics"]
    nets = networks(s, c, res_d, sp["chw_return_c"] - sp["chw_supply_c"])
    return energy_matrix(dm, s, c, m["crah_fan_kw"], sp["chw_supply_c"], sp["chw_return_c"], nets), m


def next_month(sc, cfg, year: int, month: int, annual, it_ds=None, capacity_kw=None):
    """다음 달 운전 브리핑. 기상 = annual(지역 관측소의 지난 관측 연도)의 같은 달 날씨를 날짜 순서대로 대응 (예보 아님).
    한 달 모든 날을 엔진으로 계산하고, 권장 운전점은 그 달 습구온도가 가장 높은 날의 IT 피크 기류로 제약을 확인하면서
    한 달 냉방 전력량이 최소인 값 (하루 브리핑과 같은 격자 탐색). 월별 스케줄이 있으면 그 달 값이 '현재'"""
    obs = [d for d in annual["days"] if int(d["date"][5:7]) == month]
    if not obs:
        raise ValueError(f"관측 자료에 {month}월이 없습니다")
    y_obs = annual["meta"].get("year")
    n = calendar.monthrange(year, month)[1]
    notes, results, scs = [], [], []
    for i in range(n):
        t = date(year, month, i + 1)
        o = obs[min(i, len(obs) - 1)]
        if it_ds:
            kw = forecast_kw(it_ds, t)[0]
            util = [v / (sc["it_load"]["design_kw"] or 1.0) for v in kw]
        else:
            util = list(sc["it_load"]["utilization"])
        s = {**sc, "weather": o["weather"], "site": {**sc["site"], "date": t.isoformat()},
             "it_load": {**sc["it_load"], "utilization": util}}
        scs.append((s, o["date"]))
    results = simulate_days([s for s, _ in scs])
    notes.append("IT: IT 데이터셋의 날짜별 예측 (최근 같은 요일 평균 x 추세)" if it_ds else
                 "IT: IT 데이터셋이 없어 매일 시나리오 24시간 가동률 사용 — 'IT 데이터'에서 기간 데이터를 올리면 요일·추세를 반영합니다")
    if len(obs) < n:
        notes.append(f"{y_obs}년 {month}월은 {len(obs)}일이라 마지막 날 관측을 {n - len(obs)}일 더 사용")
    wb_max = [max(x["weather"]["wet_bulb_c"] for x in r["hourly"]) for r in results]
    k = max(range(n), key=lambda i: wb_max[i])
    s_d, res_d = scs[k][0], results[k]
    dm = day_matrix(results)
    sched = {e["month"]: e for e in (cfg.get("operating_schedule") or [])}
    cp, e = sc["cooling_plant"], sched.get(month)
    cur = {"supply_temp_c": e["supply_temp_c"], "chw_supply_c": e["chw_supply_c"], "chw_return_c": e["chw_return_c"],
           "airflow_ratio": e["airflow_ratio"]} if e else \
        {"supply_temp_c": cfg["supply_temp_c"], "chw_supply_c": cp["chw_supply_c"], "chw_return_c": cp["chw_return_c"],
         "airflow_ratio": cfg["crah"]["airflow_ratio"]}
    cur_label = f"{month}월 스케줄" if e else "현재 운전점"
    rec = _search(s_d, cfg, res_d, also=(sc,), results=results) or \
        _search(s_d, cfg, res_d, also=(sc,), margin=0.0, results=results)
    em_c, m_c = _month_energy(s_d, cfg, res_d, dm, cur)
    em_r, m_r = _month_energy(s_d, cfg, res_d, dm, rec) if rec else (em_c, m_c)
    limit = cfg["inlet_limit_c"]
    margin_c, margin_r = round(limit - m_c["max_inlet_c"], 2), round(limit - m_r["max_inlet_c"], 2)
    pk_i = max(range(n), key=lambda i: max(x["load_kw"]["total_cooling"] for x in results[i]["hourly"]))
    pk = max(results[pk_i]["hourly"], key=lambda x: x["load_kw"]["total_cooling"])
    alerts = []
    d_label = f"{month}/{k + 1} (가장 습한 날)"
    if margin_c < 0:
        alerts.append({"level": "crit", "text": f"{cur_label}으로는 {d_label} 피크 {peak_hour(s_d)}시 랙 흡입온도가 상한을 "
                                                f"{-margin_c:.1f}℃ 넘습니다 — 권장 운전점이나 풍량 상향 필요"})
    elif margin_c < MARGIN_WARN_C:
        alerts.append({"level": "warn", "text": f"{cur_label}의 랙 흡입온도 여유가 {margin_c:.1f}℃ 로 작습니다 ({d_label})"})
    if rec and margin_r < MARGIN_WARN_C:
        alerts.append({"level": "warn", "text": f"여유 {MARGIN_WARN_C:g}℃ 를 지키는 운전점이 없어, 권장 운전점은 흡입 상한만 지킵니다 "
                                                f"(여유 {margin_r:.1f}℃) — 구조 개선(타일·컨테인먼트·CRAH)이 필요합니다"})
    if capacity_kw and pk["load_kw"]["total_cooling"] / capacity_kw > CAPACITY_WARN:
        ratio = pk["load_kw"]["total_cooling"] / capacity_kw
        alerts.append({"level": "warn" if ratio <= 1 else "crit", "text": f"피크 냉방부하가 설비 용량의 {ratio * 100:.0f}% 입니다"})
    if not rec:
        alerts.append({"level": "warn", "text": "제약을 만족하는 권장 운전점을 찾지 못했습니다 (구조·흡입 상한 확인)"})
    issues = []
    if margin_c < MARGIN_WARN_C:
        from .advice import inlet_issue
        c_cur = copy.deepcopy(cfg)
        c_cur["crah"]["airflow_ratio"], c_cur["supply_temp_c"] = cur["airflow_ratio"], cur["supply_temp_c"]
        s_cur = {**s_d, "cooling_plant": {**s_d["cooling_plant"], "chw_supply_c": cur["chw_supply_c"],
                                           "chw_return_c": cur["chw_return_c"]}}
        iss = inlet_issue(s_cur, c_cur, peak_hour(s_d), label=cur_label, where=f"{month}월 가장 습한 날 피크")
        if iss:
            issues.append(iss)

    def hours_by_mode(em):
        x = em["free_fraction"]
        return (x >= 0.999).sum(axis=1), ((x > 0.001) & (x < 0.999)).sum(axis=1)
    free_c, part_c = hours_by_mode(em_c)
    free_r, part_r = hours_by_mode(em_r)
    tot_c, tot_r = em_c["total"].sum(axis=1), em_r["total"].sum(axis=1)
    kwh_c, kwh_r = float(tot_c.sum()), float(tot_r.sum())
    saving = kwh_c - kwh_r
    daily = []
    for i, r in enumerate(results):
        hs = r["hourly"]
        daily.append({"date": scs[i][0]["site"]["date"], "obs_date": scs[i][1],
                      "max_dry_bulb_c": round(max(x["weather"]["dry_bulb_c"] for x in hs), 1),
                      "min_dry_bulb_c": round(min(x["weather"]["dry_bulb_c"] for x in hs), 1),
                      "max_wet_bulb_c": round(wb_max[i], 1),
                      "peak_cooling_kw": round(max(x["load_kw"]["total_cooling"] for x in hs), 0),
                      "current_kwh": round(float(tot_c[i]), 0), "recommended_kwh": round(float(tot_r[i]), 0),
                      "current_free_h": int(free_c[i]), "current_partial_h": int(part_c[i]),
                      "free_h": int(free_r[i]), "partial_h": int(part_r[i])})
    fh_c, fh_r = int(free_c.sum()), int(free_r.sum())
    ph_c, ph_r = int(part_c.sum()), int(part_r.sum())
    h_all = 24 * n
    bullets = [f"한 달 냉방 설비 전력량 {kwh_c / 1000:,.1f} MWh ({cur_label}) · 최대 냉방부하 "
               f"{pk['load_kw']['total_cooling']:,.0f} kW ({month}/{pk_i + 1} {pk['hour']}시)"
               + (f" — 설비 용량 대비 {pk['load_kw']['total_cooling'] / capacity_kw * 100:.0f}%" if capacity_kw else ""),
               f"기온 {min(d['min_dry_bulb_c'] for d in daily):.1f}~{max(d['max_dry_bulb_c'] for d in daily):.1f}℃, "
               f"습구 최고 {max(wb_max):.1f}℃ ({month}/{k + 1})",
               f"프리쿨링 ({cur_label}): 완전 {fh_c}시간 · 부분 {ph_c}시간 · 칠러 단독 {h_all - fh_c - ph_c}시간 (전체 {h_all}시간)"]
    if rec:
        bullets.append(f"권장 운전점: 급기 {rec['supply_temp_c']:g}℃, 냉수 {rec['chw_supply_c']:g}/{rec['chw_return_c']:g}℃, "
                       f"풍량 {rec['airflow_ratio'] * 100:.0f}% (흡입 여유 {margin_r:.1f}℃) → 완전 프리쿨링 {fh_r}시간, "
                       f"{cur_label} 대비 {abs(saving) / 1000:,.1f} MWh({abs(saving) / max(kwh_c, 1e-9) * 100:.1f}%) "
                       + ("절감" if saving >= 0 else "증가 — 현재 운전점이 흡입 여유 기준을 지키지 못해 안전 쪽으로 조정"))
    fan_d = (m_r["crah_fan_kw"] - m_c["crah_fan_kw"]) if rec else 0.0
    power_note = (f"{cur_label}은 한 달 {h_all}시간 중 칠러 단독 {h_all - fh_c - ph_c}시간, 권장 운전점은 "
                  f"{h_all - fh_r - ph_r}시간입니다. " if rec else "") + \
        (f"권장 쪽은 풍량을 바꿔 CRAH 팬이 {fan_d:+.0f} kW 달라집니다. " if abs(fan_d) > 0.5 else "") + \
        f"한 달 합계 {kwh_c / 1000:,.1f} → {kwh_r / 1000:,.1f} MWh ({(kwh_r - kwh_c) / max(kwh_c, 1e-9) * 100:+.1f}%)."
    src = annual["meta"]
    notes.append(f"기상: {src.get('source_detail', '')} 의 {y_obs}년 {month}월 관측을 {year}년 {month}월 같은 날짜에 대응 "
                 "(다음 달은 예보가 없어 지난 관측으로 계산 — 실제 날씨와 다름)")
    return {
        "period": "month", "year": year, "month": month, "observed_year": y_obs,
        "date": date(year, month, k + 1).isoformat(), "weekday": WD[date(year, month, k + 1).weekday()],
        "title": f"{year}년 {month}월 · {sc['site']['name']} · {src.get('station_name', '')} {y_obs}년 {month}월 관측 기준",
        "bullets": bullets, "alerts": alerts, "issues": issues, "notes": notes,
        "weather_source": "kma_asos_month",
        "weather_note": f"다음 달은 기상 예보가 없어 지역 관측소의 {y_obs}년 {month}월 실제 관측(기상청 ASOS)으로 계산한 값입니다",
        "current_label": cur_label,
        "current": {**cur, "monthly_kwh": round(kwh_c, 0), "max_inlet_c": m_c["max_inlet_c"], "inlet_margin_c": margin_c},
        "recommended": None if not rec else {x: rec[x] for x in ("supply_temp_c", "chw_supply_c", "chw_return_c", "airflow_ratio")}
        | {"monthly_kwh": round(kwh_r, 0), "max_inlet_c": m_r["max_inlet_c"], "inlet_margin_c": margin_r},
        "saving_kwh": round(saving, 0), "power_note": power_note, "daily": daily,
        "peak_cooling_kw": pk["load_kw"]["total_cooling"],
        "makeup_m3": round(sum(r["daily_total"]["makeup_m3"] for r in results), 1),
        # '운전 현황에서 보기' = 그 달 가장 습한 날
        "weather": s_d["weather"], "utilization": [round(u, 5) for u in s_d["it_load"]["utilization"]],
    }
