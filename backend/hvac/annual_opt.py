"""
연간 기준 최적화 (Phase 13) - 공사 변수(구조)는 연중 고정, 운전점은 월마다 고른다.

구조 후보: 허용 컨테인먼트 x 배관 방식 x CRAH 대수 (기본 = 현재 대수)
운전점 격자: 풍량 배율 0.3~1.5 (0.1 간격) x 냉수 ΔT {5,6,7,8,10}. 타일 밸런싱은 현재값 고정
월별 제약: 그 달 IT 최대 시각의 기류로 급기온도를 흡입 상한에서 역산 (하루 최적화와 같은 규칙), 코일 접근온도 확인
에너지: 연성 모델(plant_vec, 칠러+CRAH 팬+냉수·냉각수 펌프+액체 루프) 365일 x 24시간
비교: 현재 운전 / 연중 고정 최적(12개월 모두 가능한 한 운전점) / 월별 최적
월별 최적: 인접 월(12월→1월 포함) 급기온도 변경 폭 <= max_step_c 를 지키는 최소 비용 경로 (동적계획법)
결과 검증: 최적 스케줄을 스칼라 plant.energy 로 월 피크일마다 다시 계산해 벡터 결과와 비교
"""
import copy
import math
from collections import defaultdict

import numpy as np

from .airflow import analyze, building_dims, crah_positions, crah_unit_rated
from .annual import day_multipliers, day_utilizations
from .engine import simulate_days
from .plant import energy, networks
from .plant_vec import day_matrix, energy_matrix

CONT_LABEL = {"open": "개방형", "cold_aisle": "콜드아일", "hot_aisle": "핫아일"}
TOPO_LABEL = {"radial": "단일 헤더", "dual": "2계통", "ring": "루프"}
RATIOS = [round(0.3 + 0.1 * i, 2) for i in range(13)]
DTS = [5.0, 6.0, 7.0, 8.0, 10.0]
SUPPLY_MIN, SUPPLY_MAX, CHW_MIN = 16.0, 25.0, 5.0


def _structure_cfg(cfg, sc, st, unit):
    c = copy.deepcopy(cfg)
    c["containment"] = st["containment"]
    c["piping"]["topology"] = st["topology"]
    if st["crah_per_side"] != cfg["crah"]["per_side"]:
        c["crah"].update(per_side=st["crah_per_side"], positions=None, unit_airflow_m3_s=unit)
    return c


def _peak_hours(utils, months):
    """월별 IT 최대 (가동률 24개, 시각)"""
    best = {}
    for u, m in zip(utils, months):
        h = max(range(24), key=lambda k: u[k])
        if m not in best or u[h] > best[m][0][best[m][1]]:
            best[m] = (u, h)
    return best


def _dp(cost, tsup, max_step):
    """cost (12,K) 월별 비용(불가=inf), tsup (12,K) 급기온도 → 원형 최소 경로 (월별 운전점 index), 총비용"""
    M, K = cost.shape
    ok = np.abs(tsup[:, :, None] - np.roll(tsup, 1, axis=0)[:, None, :]) <= max_step + 1e-9   # [m, k, k_prev]
    best_total, best_path = math.inf, None
    for k0 in np.where(np.isfinite(cost[0]))[0]:
        acc = np.full(K, np.inf)
        acc[k0] = cost[0, k0]
        back = []
        for m in range(1, M):
            trans = np.where(ok[m], acc[None, :], np.inf)          # [k, k_prev]
            prev = np.argmin(trans, axis=1)
            acc = trans[np.arange(K), prev] + cost[m]
            back.append(prev)
        acc = np.where(ok[0][k0][None, :].ravel(), acc, np.inf)    # 12월 → 1월 연결
        kl = int(np.argmin(acc))
        if acc[kl] < best_total:
            path = [kl]
            for prev in reversed(back):
                path.append(int(prev[path[-1]]))
            best_total, best_path = float(acc[kl]), path[::-1]
    return best_path, best_total


def optimize_year(sc, cfg, annual, *, it_dataset=None, base_multiplier=1.0, growth_pct_per_year=0.0,
                  containment=("open", "cold_aisle", "hot_aisle"), topology=("radial", "dual", "ring"),
                  crah_per_side=None, max_step_c=2.0, progress=None):
    days = annual["days"]
    mults = day_multipliers(len(days), base_multiplier, growth_pct_per_year)
    utils, used = day_utilizations(sc, days, mults, it_dataset)
    months = [int(d["date"][5:7]) for d in days]
    mlist = sorted(set(months))
    if progress:
        progress(0.02, "365일 부하 계산")
    results = simulate_days([{**sc, "weather": day["weather"], "site": {**sc["site"], "date": day["date"]},
                              "it_load": {**sc["it_load"], "utilization": u}} for day, u in zip(days, utils)])
    dm = day_matrix(results)
    design = max(results, key=lambda r: max(x["load_kw"]["total_cooling"] for x in r["hourly"]))
    peaks = _peak_hours(utils, months)
    month_idx = np.array([mlist.index(m) for m in months])
    unit = crah_unit_rated(sc, cfg, len(crah_positions(cfg, *building_dims(sc)[:2])))
    approach, ret_app, limit = cfg["coil_approach_k"], cfg["coil_return_approach_k"], cfg["inlet_limit_c"]

    structures = [{"containment": c, "topology": t, "crah_per_side": n} for c in containment for t in topology
                  for n in (crah_per_side or [cfg["crah"]["per_side"]])]
    cur_st = {"containment": cfg["containment"], "topology": cfg["piping"]["topology"],
              "crah_per_side": cfg["crah"]["per_side"]}
    if cur_st not in structures:
        structures.append(cur_st)
    net_cache, air_cache = {}, {}

    def nets(c, dt):
        k = (c["crah"]["per_side"], c["piping"]["topology"], dt)
        if k not in net_cache:
            net_cache[k] = networks(sc, c, design, dt)
        return net_cache[k]

    def airflow(c, ratio, m):
        """월 m 피크 시각 기류 (급기 = 현재값 기준, 상승폭은 급기와 무관)"""
        u, h = peaks[m]
        key = (c["containment"], c["crah"]["per_side"], ratio, tuple(u), h)
        if key not in air_cache:
            c2 = copy.deepcopy(c)
            c2["crah"]["airflow_ratio"] = ratio
            met = analyze({**sc, "it_load": {**sc["it_load"], "utilization": u}}, c2, h)["metrics"]
            air_cache[key] = met
        return air_cache[key]

    def monthly_kwh(total):
        out = np.zeros(len(mlist))
        np.add.at(out, month_idx, total.sum(axis=1))
        return out

    def evaluate(c, ratio, setpoints):
        """setpoints: 월 index → (급기, 냉수 공급, 냉수 환수) → 월별 kWh"""
        fan = airflow(c, ratio, mlist[0])["crah_fan_kw"]
        cs = np.array([setpoints[i][1] for i in month_idx])
        cr = np.array([setpoints[i][2] for i in month_idx])
        dt = round(float(setpoints[0][2] - setpoints[0][1]), 3)
        e = energy_matrix(dm, sc, c, fan, cs, cr, nets(c, dt))
        return monthly_kwh(e["total"]), e

    # 현재 운전 (구조·운전점 그대로)
    cp = sc["cooling_plant"]
    cur_cfg = copy.deepcopy(cfg)
    cur_sp = {i: (cfg["supply_temp_c"], cp["chw_supply_c"], cp["chw_return_c"]) for i in range(len(mlist))}
    cur_kwh, _ = evaluate(cur_cfg, cfg["crah"]["airflow_ratio"], cur_sp)
    cur_viol = [m for m in mlist if airflow(cur_cfg, cfg["crah"]["airflow_ratio"], m)["max_inlet_c"] > limit + 1e-9]

    out_structs = []
    for si, st in enumerate(structures):
        if progress:
            progress(0.05 + 0.85 * si / len(structures), f"구조 {si + 1}/{len(structures)} 탐색")
        c = _structure_cfg(cfg, sc, st, unit)
        ops, cost, tsup, info = [], [], [], []
        for ratio in RATIOS:
            mets = [airflow(c, ratio, m) for m in mlist]
            t_m, ret_m = [], []
            for met in mets:
                rise = met["max_inlet_c"] - c["supply_temp_c"]
                t = min(math.floor((limit - rise) * 2) / 2, SUPPLY_MAX)
                t_m.append(t)
                ret_m.append(t + (met["crah_return_c"] - c["supply_temp_c"]))
            for dt in DTS:
                sp, feas = {}, []
                for i, (t, rr) in enumerate(zip(t_m, ret_m)):
                    s_ = round(t - approach, 2)
                    r_ = round(s_ + dt, 2)
                    ok = t >= SUPPLY_MIN and s_ >= CHW_MIN and r_ <= rr - ret_app + 1e-9
                    sp[i] = (t, s_, r_)
                    feas.append(ok)
                if not any(feas):
                    continue
                kwh, _ = evaluate(c, ratio, sp)
                ops.append((ratio, dt))
                cost.append([kwh[i] if feas[i] else np.inf for i in range(len(mlist))])
                tsup.append([sp[i][0] for i in range(len(mlist))])
                info.append((sp, [round(limit - (t + (mets[i]["max_inlet_c"] - c["supply_temp_c"])), 2)
                                  for i, t in enumerate(t_m)]))
        if not ops:
            continue
        C = np.array(cost).T           # (12, K)
        T = np.array(tsup).T
        fixed_tot = np.where(np.all(np.isfinite(C), axis=0), C.sum(axis=0), np.inf)
        kf = int(np.argmin(fixed_tot))
        path, total = _dp(C, T, max_step_c)
        if path is None:
            continue
        sched = []
        for i, k in enumerate(path):
            ratio, dt = ops[k]
            t, s_, r_ = info[k][0][i]
            sched.append({"month": mlist[i], "supply_temp_c": t, "chw_supply_c": s_, "chw_return_c": r_,
                          "airflow_ratio": ratio, "kwh": round(float(C[i, k]), 0), "inlet_margin_c": info[k][1][i]})
        out_structs.append({
            "structure": st, "is_current": st == cur_st,
            "monthly_kwh": round(total, 0),
            "fixed_kwh": None if not np.isfinite(fixed_tot[kf]) else round(float(fixed_tot[kf]), 0),
            "fixed_point": None if not np.isfinite(fixed_tot[kf]) else {
                "airflow_ratio": ops[kf][0], "chw_delta_t": ops[kf][1],
                "monthly_kwh": [round(float(x), 0) for x in C[:, kf]]},
            "schedule": sched, "monthly": [round(float(C[i, k]), 0) for i, k in enumerate(path)],
        })
    if not out_structs:
        raise ValueError("제약(랙 흡입온도 상한, 코일 접근온도)을 만족하는 운전점이 없습니다 — 허용 구조를 넓혀 보세요")
    out_structs.sort(key=lambda x: x["monthly_kwh"])
    best = out_structs[0]
    if progress:
        progress(0.93, "결과 검증")
    check = _verify(sc, cfg, best, results, dm, months, mlist, unit, design)
    base_tot = float(cur_kwh.sum())
    same = next((x for x in out_structs if x["is_current"]), None)
    parts = []
    if base_tot > 0 and same:
        sv = base_tot - same["monthly_kwh"]
        parts.append(f"지금 구조 그대로 운전점만 월별로 바꾸면 연 {sv / 1000:,.0f} MWh({sv / base_tot * 100:.1f}%) 절감")
    if base_tot > 0 and not best["is_current"]:
        sv = base_tot - best["monthly_kwh"]
        st = best["structure"]
        parts.append(f"구조까지 바꾸면({CONT_LABEL[st['containment']]}·{TOPO_LABEL[st['topology']]}·CRAH "
                     f"{crah_text(sc, st['crah_per_side'])}) 연 {sv / 1000:,.0f} MWh({sv / base_tot * 100:.1f}%) 절감 — 공사비는 '투자 검토'에서")
    summary = ". ".join(parts) + "." if parts else ""
    notes = ["연성 모델(기류·배관 반영) 기준. 냉각탑 팬·습도 제어·공사비는 목적함수에 없음 (투자 검토에서 공사비 반영)",
             f"월별 운전점 변경 폭: 급기온도 인접 월 {max_step_c:g}℃ 이내 (12월→1월 포함)",
             "타일 밸런싱은 현재값 고정, 풍량 배율 0.1 간격 격자 (하루 최적화보다 거침)"]
    if used:
        from .itprofile import usage_note
        notes.append(usage_note(used))
    if cur_viol:
        notes.append(f"주의: 현재 운전점은 {', '.join(map(str, cur_viol))}월에 랙 흡입온도 상한을 넘습니다")
    return {"months": mlist, "current": {"structure": cur_st, "annual_kwh": round(base_tot, 0),
                                          "monthly": [round(float(x), 0) for x in cur_kwh],
                                          "violations": cur_viol},
            "structures": out_structs[:6], "best": best, "same_structure": same, "check": check, "summary": summary, "notes": notes,
            "year": annual["meta"].get("year"), "n_days": len(days),
            # 엔진 모델 값 (연성 합계에 없는 냉각탑 팬·보급수 - 투자 검토에서 더함)
            "engine": {"tower_fan_kwh": round(float(dm.tower_kw.sum()), 0), "makeup_m3": round(float(dm.makeup_m3.sum()), 1),
                       "it_kwh": round(float(dm.it_kw.sum()), 0)}}


def _verify(sc, cfg, best, results, dm, months, mlist, unit, design):
    """월 첫날마다 스칼라 plant.energy 로 다시 계산 → 벡터 결과와 상대 차이"""
    c = _structure_cfg(cfg, sc, best["structure"], unit)
    diffs = []
    for e in best["schedule"]:
        i = months.index(e["month"])
        c2 = copy.deepcopy(c)
        c2["crah"]["airflow_ratio"] = e["airflow_ratio"]
        fan = analyze({**sc}, c2)["metrics"]["crah_fan_kw"]
        dt = round(e["chw_return_c"] - e["chw_supply_c"], 3)
        nets = networks(sc, c2, design, dt)
        _, daily = energy(sc, c2, results[i], fan, e["chw_supply_c"], e["chw_return_c"], nets)
        sub = type(dm)(*[getattr(dm, f)[i:i + 1] if f != "dates" else dm.dates[i:i + 1] for f in dm.__dataclass_fields__])
        v = energy_matrix(sub, sc, c2, fan, e["chw_supply_c"], e["chw_return_c"], nets)["total"].sum()
        diffs.append(abs(v - daily["total"]) / max(daily["total"], 1e-9))
    return {"max_rel_diff": round(float(max(diffs)), 6), "ok": bool(max(diffs) < 1e-3)}


def coupled_annual(sc, cfg, annual, utils, schedule=None):
    """연간 탭 '연성 모델 보기': 현재 구조 + (스케줄 | 현재 운전점) 의 월별 kWh"""
    days = annual["days"]
    months = [int(d["date"][5:7]) for d in days]
    res = simulate_days([{**sc, "weather": day["weather"], "site": {**sc["site"], "date": day["date"]},
                          "it_load": {**sc["it_load"], "utilization": u}} for day, u in zip(days, utils)])
    dm = day_matrix(res)
    design = max(res, key=lambda r: max(x["load_kw"]["total_cooling"] for x in r["hourly"]))
    cp = sc["cooling_plant"]
    by_m = {e["month"]: e for e in (schedule or [])}
    tot = defaultdict(float)
    groups = defaultdict(list)
    for i, m in enumerate(months):
        groups[m].append(i)
    for m, idx in groups.items():
        e = by_m.get(m)
        c = copy.deepcopy(cfg)
        if e:
            c["crah"]["airflow_ratio"], c["supply_temp_c"] = e["airflow_ratio"], e["supply_temp_c"]
            cs, cr = e["chw_supply_c"], e["chw_return_c"]
        else:
            cs, cr = cp["chw_supply_c"], cp["chw_return_c"]
        fan = analyze(sc, c)["metrics"]["crah_fan_kw"]
        nets = networks(sc, c, design, cr - cs)
        sub = type(dm)(*[getattr(dm, f)[idx] if f != "dates" else [dm.dates[j] for j in idx]
                         for f in dm.__dataclass_fields__])
        tot[m] = float(energy_matrix(sub, sc, c, fan, cs, cr, nets)["total"].sum())
    ms = sorted(tot)
    return {"monthly_kwh": [round(tot[m], 0) for m in ms], "months": ms, "annual_kwh": round(sum(tot.values()), 0),
            "uses_schedule": bool(schedule)}


def crah_text(sc, per_side):
    """CRAH 대수 문구 — 다중 존이면 전산실마다 같은 대수"""
    n = sum(1 for z in sc.get("zones") or [] if z["kind"] == "data_hall")
    return f"CRAH 전산실 {n}곳 x {per_side * 2}대" if n > 1 else f"CRAH {per_side * 2}대"
