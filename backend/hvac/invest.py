"""
투자비·회수기간 (Phase 16) - 개선안마다 연간 전력·물 비용을 계산하고 현금흐름(NPV·IRR·회수기간)을 낸다.

- 기준안: 현재 구조 + 현재 운전점 (연성 모델 + 엔진 냉각탑 팬, 엔진 보급수)
- 개선안: 시나리오를 바꾼 뒤 그 구조에서 월별 최적 운전(annual_opt)까지 포함해 평가 → 운전 최적화 효과가 함께 들어간다
  ('운전 최적화' 개선안 = 구조 그대로 월별 운전만)
- 패키지: 여러 개선안을 함께 적용해 다시 계산 (단순 합 아님, 개별 합과의 차이를 표시)
- IT 증설: 1년차·중간·마지막 해 3점만 계산하고 사이 연도는 선형 보간 (가정)
- 전기요금 단일 단가 x 연 상승률, 물 요금 단일 단가. 기본요금(kW)·계절·시간대 요금은 반영하지 않음
- 투자비 예시 단가(EXAMPLE_COSTS)는 시연용이다. 결과에 'demo' 표시를 붙이고 화면에 크게 안내한다
"""
import copy

from .annual_opt import optimize_year
from .engine import simulate
from .schemas import CRAH_PER_SIDE_MAX

EXAMPLE_COSTS = {   # 시연용 예시 단가 - 실제 견적으로 바꿔야 함
    "schedule": ("식", 30_000_000, "제어 로직 설정·시운전"),
    "cold_aisle": ("랙", 1_200_000, "콜드아일 컨테인먼트 (도어·지붕 패널)"),
    "hot_aisle": ("랙", 1_500_000, "핫아일 컨테인먼트 (덕트·천장 리턴)"),
    "crah_add": ("대", 60_000_000, "CRAH 증설 (남·북 각 1대)"),
    "piping_dual": ("식", 120_000_000, "냉수 배관 남·북 2계통 분리"),
    "piping_ring": ("식", 180_000_000, "냉수 배관 루프(환상) 구성"),
    "free_cooling": ("kW", 60_000, "프리쿨링 판형 열교환기 (설계 냉방 kW 당)"),
    "dlc": ("kW", 900_000, "DLC 냉각판·CDU 전환 (IT 설계 kW 당)"),
    "immersion": ("kW", 1_400_000, "액침 냉각 전환 (IT 설계 kW 당)"),
}
LABEL = {"schedule": "운전 최적화 (월별 스케줄)", "cold_aisle": "콜드아일 컨테인먼트", "hot_aisle": "핫아일 컨테인먼트",
         "crah_add": "CRAH 2대 증설", "piping_dual": "냉수 배관 2계통", "piping_ring": "냉수 배관 루프",
         "free_cooling": "프리쿨링 열교환기 설치", "dlc": "DLC 전환 (전체 랙)", "immersion": "액침 냉각 전환"}


def _n_racks(cfg, sc=None):
    """랙 수 (다중 존이면 전산실 존 랙 합)"""
    if sc is not None and sc.get("zones"):
        from .multizone import halls
        return sum(_n_racks(s["cooling_layout"]) for _, s in halls(sc, cfg))
    g = cfg["racks"]
    return len(g["items"]) if g.get("items") else g["rows"] * g["per_row"]


def catalog(sc, cfg):
    """적용 가능한 개선안, 수량, 예시 투자비"""
    cp = sc["cooling_plant"]
    arch = cp.get("architecture", "air_chw")
    peak_cool = max(x["load_kw"]["total_cooling"] for x in simulate(sc, 60)["hourly"])
    n_halls = sum(1 for z in sc.get("zones") or [] if z["kind"] == "data_hall") or 1
    qty = {"schedule": 1, "cold_aisle": _n_racks(cfg, sc), "hot_aisle": _n_racks(cfg, sc), "crah_add": 2 * n_halls,
           "piping_dual": 1, "piping_ring": 1, "free_cooling": round(peak_cool),
           "dlc": round(sc["it_load"]["design_kw"]), "immersion": round(sc["it_load"]["design_kw"])}
    ok = {"schedule": True, "cold_aisle": cfg["containment"] != "cold_aisle", "hot_aisle": cfg["containment"] != "hot_aisle",
          "crah_add": cfg["crah"]["per_side"] < CRAH_PER_SIDE_MAX, "piping_dual": cfg["piping"]["topology"] != "dual",
          "piping_ring": cfg["piping"]["topology"] != "ring", "free_cooling": not cp.get("free_cooling", True),
          "dlc": arch == "air_chw", "immersion": arch != "immersion"}
    out = []
    for k, (unit, price, desc) in EXAMPLE_COSTS.items():
        out.append({"id": k, "label": LABEL[k], "description": desc, "unit": unit, "unit_cost_krw": price,
                    "quantity": qty[k], "example_capex_krw": price * qty[k], "applicable": ok[k]})
    return out


def _apply(sc, cfg, ids):
    """개선안 → (시나리오, 구조)"""
    s, c = copy.deepcopy(sc), copy.deepcopy(cfg)
    st = {"containment": [c["containment"]], "topology": [c["piping"]["topology"]], "crah_per_side": None}
    for k in ids:
        if k in ("cold_aisle", "hot_aisle"):
            st["containment"] = [k]
        elif k == "crah_add":
            st["crah_per_side"] = [c["crah"]["per_side"] + 1]
        elif k == "piping_dual":
            st["topology"] = ["dual"]
        elif k == "piping_ring":
            st["topology"] = ["ring"]
        elif k == "free_cooling":
            s["cooling_plant"]["free_cooling"] = True
        elif k == "dlc":
            s["cooling_plant"]["architecture"] = "hybrid_dlc"
        elif k == "immersion":
            s["cooling_plant"]["architecture"] = "immersion"
    return s, c, st


def _annual(sc, cfg, annual, st, growth_mult, it_dataset, optimize_operation):
    r = optimize_year(sc, cfg, annual, it_dataset=it_dataset, base_multiplier=growth_mult,
                      containment=tuple(st["containment"]), topology=tuple(st["topology"]),
                      crah_per_side=st["crah_per_side"])
    kwh = r["best"]["monthly_kwh"] if optimize_operation else r["current"]["annual_kwh"]
    return {"kwh": kwh + r["engine"]["tower_fan_kwh"], "water_m3": r["engine"]["makeup_m3"],
            "it_kwh": r["engine"]["it_kwh"], "schedule": r["best"]["schedule"] if optimize_operation else None}


def _irr(flows):
    def npv(r):
        return sum(f / (1 + r) ** t for t, f in enumerate(flows))
    lo, hi = -0.99, 50.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


def cashflow(base_pts, var_pts, capex, om, life, eco):
    """연도별 현금흐름 → 지표. *_pts: {year: {"kwh", "water_m3"}} (3점) → 선형 보간"""
    N = eco["years"]
    ys = sorted(base_pts)

    def interp(pts, y, key):
        if y <= ys[0]:
            return pts[ys[0]][key]
        for a, b in zip(ys, ys[1:]):
            if y <= b:
                return pts[a][key] + (pts[b][key] - pts[a][key]) * (y - a) / (b - a)
        return pts[ys[-1]][key]
    flows, rows = [-capex], []
    for y in range(1, N + 1):
        price = eco["electricity_krw_per_kwh"] * (1 + eco["energy_escalation"]) ** (y - 1)
        dk = interp(base_pts, y, "kwh") - interp(var_pts, y, "kwh")
        dw = interp(base_pts, y, "water_m3") - interp(var_pts, y, "water_m3")
        f = dk * price + dw * eco["water_krw_per_m3"] - om
        if y % life == 0 and y < N:
            f -= capex
        flows.append(f)
        rows.append({"year": y, "saving_kwh": round(dk, 0), "saving_water_m3": round(dw, 1), "cashflow_krw": round(f, 0)})
    r = eco["discount_rate"]
    disc = [f / (1 + r) ** t for t, f in enumerate(flows)]
    npv = sum(disc)
    cum, payback, dpay, cum_d = 0.0, None, None, 0.0
    cum_list = []
    for t, (f, d) in enumerate(zip(flows, disc)):
        prev, prev_d = cum, cum_d
        cum += f
        cum_d += d
        cum_list.append(round(cum, 0))
        if t and payback is None and cum >= 0 and f > 0:
            payback = round(t - 1 + (-prev) / f, 2)
        if t and dpay is None and cum_d >= 0 and d > 0:
            dpay = round(t - 1 + (-prev_d) / d, 2)
    first = rows[0] if rows else {"saving_kwh": 0, "saving_water_m3": 0}
    return {"npv_krw": round(npv, 0), "irr": _irr(flows) if capex > 0 else None, "payback_years": payback,
            "discounted_payback_years": dpay, "year1_saving_kwh": first["saving_kwh"],
            "year1_saving_krw": rows[0]["cashflow_krw"] if rows else 0,
            "year1_co2_t": round(first["saving_kwh"] * eco["co2_kg_per_kwh"] / 1000, 1),
            "cumulative_krw": cum_list, "years": rows}


def run(sc, cfg, annual, eco, measures, packages=(), it_dataset=None, progress=None):
    """measures: [{id, capex_krw|None, om_krw_per_year, life_years, enabled}], packages: [[id, ...], ...]"""
    cat = {m["id"]: m for m in catalog(sc, cfg)}
    sel = [m for m in measures if m.get("enabled", True) and m["id"] in cat]
    unknown = [m["id"] for m in measures if m["id"] not in cat]
    if not sel:
        raise ValueError("평가할 개선안을 하나 이상 고르세요")
    g = eco["it_growth_pct_per_year"] / 100
    N = eco["years"]
    years = sorted({1, max(1, (N + 1) // 2), N})
    mult = {y: max(0.05, 1 + g * (y - 1)) for y in years}
    variants = [([m["id"]], m["id"]) for m in sel] + [(list(p), "+".join(p)) for p in packages if p]
    steps = (1 + len(variants)) * len(years)
    done = [0]

    def tick(label):
        done[0] += 1
        if progress:
            progress(done[0] / (steps + 1), label)
    base_pts, s0, c0, st0 = {}, *_apply(sc, cfg, [])
    for y in years:
        base_pts[y] = _annual(s0, c0, annual, st0, mult[y], it_dataset, optimize_operation=False)
        tick(f"기준안 {y}년차")
    by_id = {m["id"]: m for m in sel}
    results = []
    for ids, key in variants:
        s, c, st = _apply(sc, cfg, ids)
        pts = {}
        for y in years:
            pts[y] = _annual(s, c, annual, st, mult[y], it_dataset, optimize_operation=True)
            tick(f"{' + '.join(LABEL.get(i, i) for i in ids)} {y}년차")
        capex, om, demo = 0.0, 0.0, False
        for i in ids:
            m = by_id.get(i) or {"id": i}
            if m.get("capex_krw") is None:
                capex += cat[i]["example_capex_krw"]
                demo = True
            else:
                capex += m["capex_krw"]
            om += m.get("om_krw_per_year", 0.0) or 0.0
        life = min((by_id.get(i) or {}).get("life_years", 15) or 15 for i in ids)
        cf = cashflow(base_pts, pts, capex, om, life, eco)
        results.append({"key": key, "ids": ids, "label": " + ".join(LABEL.get(i, i) for i in ids),
                        "package": len(ids) > 1, "capex_krw": round(capex, 0), "demo_capex": demo,
                        "annual_kwh_year1": round(pts[years[0]]["kwh"], 0), **cf})
    singles = {r["ids"][0]: r for r in results if not r["package"]}
    for r in results:
        if r["package"]:
            r["sum_of_singles_kwh"] = round(sum(singles[i]["year1_saving_kwh"] for i in r["ids"] if i in singles), 0)
    results.sort(key=lambda r: -r["npv_krw"])
    best = results[0]
    sens = _sensitivity(base_pts, eco, best, by_id, years)
    b1 = base_pts[years[0]]
    unit_price_default = eco.get("defaults_used", False)
    notes = ["예시 데이터(가상 단가·요금)를 이용해 시연 계산한 결과입니다 — 실제 견적·요금으로 바꿔 다시 계산하세요"
             if any(r["demo_capex"] for r in results) or unit_price_default else
             "입력한 투자비·요금 기준 결과입니다",
             "개선안 효과에는 그 구조에서의 월별 최적 운전이 포함됩니다 (기준안은 현재 운전점)",
             "전기요금은 단일 단가 x 연 상승률, 기본요금(kW)·계절·시간대 요금은 반영하지 않음",
             f"IT 증설 연 {eco['it_growth_pct_per_year']:g}%: {', '.join(f'{y}년차' for y in years)} 계산 후 사이 연도 선형 보간"]
    if unknown:
        notes.append(f"알 수 없는 개선안은 제외: {', '.join(unknown)}")
    return {"baseline": {"annual_kwh": round(b1["kwh"], 0), "water_m3": b1["water_m3"],
                         "cost_krw": round(b1["kwh"] * eco["electricity_krw_per_kwh"] + b1["water_m3"] * eco["water_krw_per_m3"], 0)},
            "results": results, "sensitivity": sens, "notes": notes, "demo": any(r["demo_capex"] for r in results),
            "summary": (f"가장 유리한 안은 '{best['label']}' — 투자 {best['capex_krw'] / 1e8:,.1f}억 원, "
                        f"연 {best['year1_saving_kwh'] / 1000:,.0f} MWh 절감, "
                        + (f"단순 회수 {best['payback_years']:.1f}년, " if best["payback_years"] is not None else "회수 불가, ")
                        + f"{N}년 NPV {best['npv_krw'] / 1e8:,.1f}억 원")}


def _sensitivity(base_pts, eco, best, by_id, years):
    """최우선안의 NPV: 전기요금 ±20%, 투자비 ±30% (에너지 재계산 없이 현금흐름만)"""
    # 최우선안의 연도별 kWh·물은 cashflow 의 years 에서 역산 (재계산 없이)
    yr = {r["year"]: r for r in best["years"]}
    var = {y: {"kwh": base_pts[y]["kwh"] - yr[y]["saving_kwh"], "water_m3": base_pts[y]["water_m3"] - yr[y]["saving_water_m3"]}
           for y in years}
    om = sum((by_id.get(i) or {}).get("om_krw_per_year", 0) or 0 for i in best["ids"])
    life = min((by_id.get(i) or {}).get("life_years", 15) or 15 for i in best["ids"])
    out = []
    for label, k, lo, hi in (("전기요금", "electricity_krw_per_kwh", 0.8, 1.2), ("투자비", "capex", 0.7, 1.3)):
        vals = []
        for f in (lo, hi):
            e2 = dict(eco)
            cap = best["capex_krw"]
            if k == "capex":
                cap *= f
            else:
                e2[k] = eco[k] * f
            vals.append(cashflow(base_pts, var, cap, om, life, e2)["npv_krw"])
        out.append({"factor": label, "low": f"-{round((1 - lo) * 100)}%", "high": f"+{round((hi - 1) * 100)}%",
                    "npv_low_krw": round(vals[0], 0), "npv_high_krw": round(vals[1], 0)})
    return {"measure": best["label"], "base_npv_krw": best["npv_krw"], "items": out}
