"""
연간 시뮬레이션 (Phase 9) - 365일 각각 engine.simulate() 를 돌려 계절별 냉방부하·전력·보급수를 집계.

- 기상: 연간 파일(weather.fetch_open_meteo_year) 의 그날 24시간 값. 태양 위치는 시나리오 site 위경도로 계산
- IT: 시나리오의 24시간 가동률 모양 x 일별 배율. 배율 = base x (1 + 증설률[%/년]/100 x 경과일/연간일수) (선형, 가정)
  IT 데이터셋(Phase 11)을 주면 그날 24시간 = 데이터셋(같은 날짜 > 같은 월·요일 > 요일 > 전체 평균) x 배율
  설계부하 design_kw 는 고정 (배율이 커져 가동률이 1 을 넘어도 그대로 계산하고 notes 에 기록)
- 냉각 방식 air_chw / hybrid_dlc / immersion 을 같은 기상·IT 로 각각 계산 (나머지 입력·계수 동일)
- 계산 간격 60분. 일 합계는 엔진 시간값(반올림된 값)의 합
"""
from collections import Counter, defaultdict
from datetime import date

from .engine import ARCHITECTURES, plant_param, simulate_days
from .itprofile import day_kw, usage_note

MODES = ("free", "partial", "chiller")
SUM_KEYS = ("it_kwh", "cooling_kwh", "cooling_power_kwh", "chiller_kwh", "crah_fan_kwh", "pump_kwh",
            "tower_fan_kwh", "facility_kwh", "makeup_m3", "free_h", "partial_h", "chiller_h")


def day_multipliers(n_days, base=1.0, growth_pct_per_year=0.0):
    return [base * (1 + growth_pct_per_year / 100.0 * i / n_days) for i in range(n_days)]


def _day_scenario(sc, day, util, arch, plant_over=None):
    """simulate() 는 입력을 바꾸지 않으므로 바뀌는 부분만 새 dict (얕은 복사)"""
    return {**sc, "weather": day["weather"], "site": {**sc["site"], "date": day["date"]},
            "it_load": {**sc["it_load"], "utilization": util},
            "cooling_plant": {**sc["cooling_plant"], "architecture": arch, **(plant_over or {})}}


def day_utilizations(sc, days, mults, it_dataset=None):
    """일별 24시간 가동률과 근거 집계"""
    if not it_dataset:
        u = sc["it_load"]["utilization"]
        return [[x * m for x in u] for m in mults], None
    dk = sc["it_load"]["design_kw"] or 1.0
    out, used = [], Counter()
    for day, m in zip(days, mults):
        kw, how = day_kw(it_dataset, date.fromisoformat(day["date"]))
        used[how] += 1
        out.append([round(v / dk * m, 5) for v in kw])
    return out, used


def _day_row(day, mult, res):
    hs = res["hourly"]
    p = lambda k: sum(x["plant"]["power_kw"][k] for x in hs)
    peak = max(hs, key=lambda x: x["load_kw"]["total_cooling"])
    d = res["daily_total"]
    row = {"date": day["date"], "month": int(day["date"][5:7]), "multiplier": round(mult, 4),
           "it_kwh": d["it_kwh"], "cooling_kwh": d["cooling_kwh"], "cooling_power_kwh": d["cooling_power_kwh"],
           "chiller_kwh": p("chiller"), "crah_fan_kwh": p("crah_fan"),
           "pump_kwh": p("chw_pump") + p("cw_pump") + p("liquid_pump"), "tower_fan_kwh": p("tower_fan"),
           "facility_kwh": d["facility_kwh"], "makeup_m3": d["makeup_m3"],
           "free_h": d["free_cooling_hours"], "partial_h": d["partial_free_cooling_hours"],
           "peak_cooling_kw": peak["load_kw"]["total_cooling"], "peak_hour": peak["hour"],
           "max_dry_bulb_c": max(x["weather"]["dry_bulb_c"] for x in hs),
           "max_wet_bulb_c": max(x["weather"]["wet_bulb_c"] for x in hs)}
    row["chiller_h"] = 24 - row["free_h"] - row["partial_h"]
    row["pue"] = round(row["facility_kwh"] / row["it_kwh"], 3) if row["it_kwh"] > 0 else None
    return row, [x["plant"]["mode"] for x in hs]


def _aggregate(rows):
    out = {k: sum(r[k] for r in rows) for k in SUM_KEYS}
    out = {k: round(v, 1 if k.endswith("_h") or k == "makeup_m3" else 0) for k, v in out.items()}
    it = out["it_kwh"]
    out["pue"] = round(out["facility_kwh"] / it, 3) if it > 0 else None
    out["wue_l_per_kwh"] = round(out["makeup_m3"] * 1000.0 / it, 3) if it > 0 else None
    pk = max(rows, key=lambda r: r["peak_cooling_kw"])
    out["peak_day"] = {"date": pk["date"], "peak_cooling_kw": pk["peak_cooling_kw"], "hour": pk["peak_hour"],
                       "multiplier": pk["multiplier"], "max_dry_bulb_c": pk["max_dry_bulb_c"],
                       "max_wet_bulb_c": pk["max_wet_bulb_c"]}
    out["n_days"] = len(rows)
    return out


def _run_arch(sc, days, mults, utils, arch, sched=None):
    rows, modes = [], []
    scs = []
    for day, u in zip(days, utils):
        e = (sched or {}).get(int(day["date"][5:7]))
        over = {"chw_supply_c": e["chw_supply_c"], "chw_return_c": e["chw_return_c"]} if e else None
        scs.append(_day_scenario(sc, day, u, arch, over))
    for day, m, res in zip(days, mults, simulate_days(scs)):   # 현실성 R1 제어는 전날 상태를 이어받음
        r, md = _day_row(day, m, res)
        rows.append(r)
        modes.append(md)
    by_month = defaultdict(list)
    for i, r in enumerate(rows):
        by_month[r["month"]].append(i)
    monthly, heat = [], {"free": [], "partial": []}
    for mo in sorted(by_month):
        idx = by_month[mo]
        monthly.append({"month": mo, **_aggregate([rows[i] for i in idx])})
        for mode in heat:   # 그 달 중 h시가 해당 모드였던 날의 비율
            heat[mode].append([round(sum(modes[i][h] == mode for i in idx) / len(idx), 3) for h in range(24)])
    for r in rows:
        for k in SUM_KEYS:
            r[k] = round(r[k], 1)
    return {"architecture": arch, "annual": _aggregate(rows), "monthly": monthly,
            "heatmap": {"months": sorted(by_month), **heat}, "daily": rows}


def run(sc, annual, *, base_multiplier=1.0, growth_pct_per_year=0.0, architectures=ARCHITECTURES, it_dataset=None,
        use_schedule=True, coupled=False):
    """sc: 엔진 dict, annual: {"meta", "days": [{"date", "weather"}]}, it_dataset: IT 데이터셋(선택)
    use_schedule: cooling_layout.operating_schedule 이 있으면 월별 냉수 온도를 엔진에 반영 (Phase 13)
    coupled: 현재 냉각 방식의 연성 모델(기류·배관) 월별 kWh 도 함께 계산"""
    days = annual["days"]
    if not days:
        raise ValueError("연간 기상 자료가 비어 있습니다")
    mults = day_multipliers(len(days), base_multiplier, growth_pct_per_year)
    utils, used = day_utilizations(sc, days, mults, it_dataset)
    sched_list = (sc.get("cooling_layout") or {}).get("operating_schedule") if use_schedule else None
    sched = {e["month"]: e for e in sched_list} if sched_list else None
    archs = [_run_arch(sc, days, mults, utils, a, sched) for a in architectures]

    wm = annual["meta"]
    where = (f"관측소 실측, 태양 위치는 사이트 {wm.get('latitude')}, {wm.get('longitude')}" if wm.get("source") == "kma_asos"
             else f"요청 {wm.get('latitude')}, {wm.get('longitude')} / 격자 {wm.get('grid_latitude')}, {wm.get('grid_longitude')}")
    notes = [
        f"기상: {wm.get('source_detail', wm.get('source'))} — {where}, {wm.get('year', '')}년 {len(days)}일. "
        f"{wm.get('attribution', '')}",
        (usage_note(used) + f" x 일별 배율 (기본 {base_multiplier:g}" if used else
         f"IT: 시나리오 24시간 가동률 모양 x 일별 배율 (기본 {base_multiplier:g}")
        + (f", 연 {growth_pct_per_year:g}% 선형 증가 → 마지막 날 x{mults[-1]:.3f}" if growth_pct_per_year else ", 연중 일정")
        + f"). 설계부하 {sc['it_load']['design_kw']:g} kW 고정 — 가정값",
        "냉각 방식별 비교는 같은 기상·IT 부하에서 냉각 방식만 바꾼 결과 (계수는 시나리오·기본 가정값)",
        "실내 온습도·외피·내부발열은 연중 동일, 단일 존 정상상태(축열 무시), 계산 간격 60분",
    ]
    lat, lon = wm.get("latitude"), wm.get("longitude")
    if lat is not None and (abs(lat - sc["site"]["latitude"]) > 0.5 or abs(lon - sc["site"]["longitude"]) > 0.5):
        notes.append(f"주의: 기상 위치({lat}, {lon})가 시나리오 사이트({sc['site']['latitude']}, "
                     f"{sc['site']['longitude']})와 0.5° 이상 다릅니다 (태양 위치는 사이트 기준)")
    if sched:
        notes.append(f"월별 운전 스케줄({len(sched)}개월)의 냉수 온도를 반영 (연간 최적화 적용값)")
    over = sum(1 for u in utils if max(u) > 1.0)
    if over:
        notes.append(f"주의: {over}일은 배율 적용 후 가동률이 1 을 넘습니다 (설계부하 초과로 그대로 계산)")
    if wm.get("notes"):
        notes.append(f"기상 결측 보간 {len(wm['notes'])}건 (기상 파일 meta.notes 참고)")

    by = {a["architecture"]: a for a in archs}
    peak_dates = {m["peak_day"]["date"] for a in archs for m in a["monthly"]}
    day_by_date = {d["date"]: (d, m, u) for d, m, u in zip(days, mults, utils)}
    return {
        "site": sc["site"]["name"], "year": wm.get("year"), "n_days": len(days),
        "current": plant_param(sc["cooling_plant"], "architecture"),
        "base_multiplier": base_multiplier, "growth_pct_per_year": growth_pct_per_year,
        "weather_meta": {k: v for k, v in wm.items() if k != "notes"},
        "comparison": [{"architecture": a, **{k: by[a]["annual"][k] for k in (
            "cooling_power_kwh", "chiller_kwh", "facility_kwh", "pue", "makeup_m3", "wue_l_per_kwh",
            "free_h", "partial_h", "chiller_h")}, "peak_cooling_kw": by[a]["annual"]["peak_day"]["peak_cooling_kw"]}
            for a in by],
        "architectures": archs,
        # 피크일을 24시간 화면으로 열 때 쓸 그날 기상과 IT 배율
        "peak_days": {dt: {"weather": day_by_date[dt][0]["weather"], "multiplier": round(day_by_date[dt][1], 4),
                           "utilization": [round(x, 5) for x in day_by_date[dt][2]]}
                      for dt in sorted(peak_dates)},
        "it_source": "dataset" if used else "scenario",
        "uses_schedule": bool(sched),
        "coupled": _coupled(sc, annual, utils, sched_list) if coupled else None,
        "notes": notes,
    }


def _coupled(sc, annual, utils, schedule):
    from .annual_opt import coupled_annual
    from .schemas import CoolingLayout
    cfg = CoolingLayout.model_validate(sc.get("cooling_layout") or {}).model_dump()
    return coupled_annual(sc, cfg, annual, utils, schedule)
