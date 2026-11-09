"""
자동 튜닝 (Phase 18) - 주의·경고(제약 위반·충돌)가 있으면 사용자가 정한 '중요한 파라미터' 순서대로 바꿔 해결한다.

사용자 설정 (화면 '최적화 → 자동 튜닝'):
- 파라미터 목록과 우선순위(위가 먼저), 켜기/끄기, 허용 범위. 공사가 필요한 항목(컨테인먼트·CRAH 대수)은 기본 꺼짐
- 목표 흡입온도 여유 [K] (기본 1 = 운전 현황·브리핑의 '여유 작음' 주의 기준과 같음)
- 목표: min_change(가장 적게 바꾸기) | min_energy(경고 없는 범위에서 냉방 전력 최소)

방법 (우선순위 단계 탐색, 결정적):
  1단계 = 1순위 파라미터만, 2단계 = 1~2순위, ... 처음으로 해법이 나온 단계에서 멈춘다
  → 덜 중요한(뒤 순위) 파라미터는 앞 순위만으로 해결이 안 될 때만 건드린다.
  각 단계: 기류 파라미터(타일 밸런싱·풍량 배율·컨테인먼트·CRAH 대수)는 격자 전수 → 기류 해석(피크 IT 시각, 다중 존은 모든 전산실)
           온도 파라미터는 해석적으로: 흡입 상승폭(최대 흡입 - 급기)과 CRAH 환기 - 급기는 급기온도와 무관하므로
             급기 ≤ 흡입 상한 - 목표 여유 - 상승폭, 냉수 공급 ≤ 급기 - 코일 접근온도, 냉수 환수 ≤ CRAH 환기 - 코일 환수 접근온도
           min_change 는 현재값에서 가장 가까운 허용값, min_energy 는 전력에 유리한 쪽 끝(급기·냉수 높게)과 ΔT 전수
  제약: 하루(운전 현황 날짜) 피크 시각 흡입 여유 ≥ 목표, 코일 공급·환수 접근온도. 에너지 = 연성 모델 일 냉방 전력량
  변경량 점수 = Σ (|변화| / 허용 범위 폭) x (1 + 우선순위 index) — 뒤 순위일수록 비싸게
결과는 다시 계산해서(advice.evaluate_issues) 남는 주의·경고까지 보여준다. 적용은 사용자가 누를 때만 (되돌리기 가능)
"""
import copy
import itertools
import math

from .advice import evaluate_issues
from .airflow import analyze, building_dims, crah_positions, crah_unit_rated
from .engine import simulate
from .optimize import apply_candidate
from .plant import energy, networks, peak_hour

CONT_ORDER = ["open", "cold_aisle", "hot_aisle"]
CONT_LABEL = {"open": "개방형", "cold_aisle": "콜드아일", "hot_aisle": "핫아일"}
# 기본 범위·간격은 가정값 (일반 데이터센터 운전 범위). 화면에서 바꿀 수 있음
PARAMS = {
    "tile_balancing": {"label": "타일 밸런싱", "unit": "", "min": 0.0, "max": 1.0, "step": 0.1, "group": "airflow",
                       "construction": False, "help": "고밀도 랙 앞 타일 개구율 ↑ (팬 전력 그대로)"},
    "airflow_ratio": {"label": "CRAH 풍량 배율", "unit": "", "min": 0.3, "max": 1.5, "step": 0.05, "group": "airflow",
                      "construction": False, "help": "VFD 운전 풍량 (팬 전력 ∝ 풍량³)"},
    "supply_temp_c": {"label": "CRAH 급기온도", "unit": "℃", "min": 16.0, "max": 25.0, "step": 0.5, "group": "temp",
                      "construction": False, "help": "낮추면 흡입이 같은 폭만큼 내려감 (칠러 전력 ↑)"},
    "chw_supply_c": {"label": "냉수 공급온도", "unit": "℃", "min": 5.0, "max": 20.0, "step": 0.5, "group": "temp",
                     "construction": False, "help": "코일 공급 접근온도 충돌 해소 (낮추면 COP ↓)"},
    "chw_delta_t": {"label": "냉수 ΔT", "unit": "K", "min": 4.0, "max": 12.0, "step": 1.0, "group": "temp",
                    "construction": False, "help": "코일 환수 접근온도 충돌 해소 (작게 하면 펌프 ↑)"},
    "containment": {"label": "컨테인먼트", "unit": "", "group": "airflow", "construction": True,
                    "help": "콜드·핫아일 차폐 (공사)"},
    "crah_per_side": {"label": "CRAH 대수 (남·북 각)", "unit": "대", "min": None, "max": None, "step": 1, "group": "airflow",
                      "construction": True, "help": "같은 크기 장비 추가 (공사·투자)"},
}
DEFAULT_ORDER = ["tile_balancing", "airflow_ratio", "supply_temp_c", "chw_supply_c", "chw_delta_t", "containment", "crah_per_side"]
CRAH_ADD_MAX = 4


def defaults(cfg):
    """화면 기본 설정 (우선순위 순서, 공사 항목은 꺼짐)"""
    out = []
    for k in DEFAULT_ORDER:
        p = PARAMS[k]
        e = {"key": k, "enabled": not p["construction"], "label": p["label"], "unit": p["unit"], "help": p["help"],
             "construction": p["construction"]}
        if k == "crah_per_side":
            e.update(min=cfg["crah"]["per_side"], max=cfg["crah"]["per_side"] + CRAH_ADD_MAX, step=1)
        elif k == "containment":
            e["choices"] = CONT_ORDER
        else:
            e.update(min=p["min"], max=p["max"], step=p["step"])
        out.append(e)
    return out


def current(sc, cfg):
    cp = sc["cooling_plant"]
    return {"tile_balancing": cfg["crah"]["tile_balancing"], "airflow_ratio": cfg["crah"]["airflow_ratio"],
            "supply_temp_c": cfg["supply_temp_c"], "chw_supply_c": cp["chw_supply_c"],
            "chw_delta_t": round(cp["chw_return_c"] - cp["chw_supply_c"], 3), "containment": cfg["containment"],
            "crah_per_side": cfg["crah"]["per_side"]}


def _grid(lo, hi, step):
    n = int(math.floor((hi - lo) / step + 1e-9))
    return [round(lo + i * step, 4) for i in range(n + 1)]


def _floor_to(x, lo, step):
    return round(lo + math.floor((x - lo) / step + 1e-9) * step, 4)


def problems(sc, cfg, metrics, margin):
    """현재 상태의 위반 목록 (자동 튜닝 대상) — 흡입 여유, 코일 공급·환수 접근온도"""
    cp, out = sc["cooling_plant"], []
    lim = cfg["inlet_limit_c"]
    if metrics["max_inlet_c"] > lim - margin + 1e-9:
        out.append(f"랙 흡입 여유 {lim - metrics['max_inlet_c']:.1f}K < 목표 {margin:g}K"
                   + (f" (초과 랙 {metrics['racks_over_limit']}개)" if metrics["racks_over_limit"] else ""))
    if cfg["supply_temp_c"] < cp["chw_supply_c"] + cfg["coil_approach_k"] - 1e-9:
        out.append(f"급기 {cfg['supply_temp_c']:g}℃ < 냉수 공급 {cp['chw_supply_c']:g}℃ + 코일 접근 {cfg['coil_approach_k']:g}K")
    if cp["chw_return_c"] > metrics["crah_return_c"] - cfg["coil_return_approach_k"] + 1e-9:
        out.append(f"냉수 환수 {cp['chw_return_c']:g}℃ > CRAH 환기 {metrics['crah_return_c']:.1f}℃ - {cfg['coil_return_approach_k']:g}K")
    return out


class _Ctx:
    def __init__(self, sc, cfg, margin):
        self.sc, self.cfg, self.margin = sc, cfg, margin
        self.res = simulate(sc, 60)
        self.ph = peak_hour(sc)
        self.unit = crah_unit_rated(sc, cfg, len(crah_positions(cfg, *building_dims(sc)[:2])))
        self.air, self.nets, self.evals = {}, {}, 0

    def layout(self, a):
        a["crah_per_side"] = int(round(a["crah_per_side"]))   # 화면 설정은 실수로 올 수 있음
        c = copy.deepcopy(self.cfg)
        c["containment"] = a["containment"]
        c["crah"].update(tile_balancing=a["tile_balancing"], airflow_ratio=a["airflow_ratio"])
        if a["crah_per_side"] != self.cfg["crah"]["per_side"]:
            c["crah"].update(per_side=a["crah_per_side"], positions=None, unit_airflow_m3_s=self.unit)
        return c

    def metrics(self, a):
        a["crah_per_side"] = int(round(a["crah_per_side"]))
        k = tuple(a[x] for x in ("containment", "crah_per_side", "tile_balancing", "airflow_ratio"))
        if k not in self.air:
            self.air[k] = analyze(self.sc, self.layout(a), self.ph)["metrics"]
            self.evals += 1
        return self.air[k]

    def kwh(self, c, fan, chws, chwr):
        dt = round(chwr - chws, 3)
        k = (c["crah"]["per_side"], c["piping"]["topology"], dt)
        if k not in self.nets:
            self.nets[k] = networks(self.sc, c, self.res, dt)
        s = {**self.sc, "cooling_plant": {**self.sc["cooling_plant"], "chw_supply_c": chws, "chw_return_c": chwr}}
        return energy(s, c, self.res, fan, chws, chwr, self.nets[k])[1]["total"]


def _values(key, setting, cur):
    if key == "containment":
        ch = set(setting.get("choices") or CONT_ORDER) | {cur}
        return [x for x in CONT_ORDER if x in ch]
    lo, hi, st = setting["min"], setting["max"], setting["step"]
    vals = _grid(lo, hi, st)
    if cur not in vals:   # 현재값은 범위 밖이어도 후보 (바꾸지 않는 선택지가 항상 있도록)
        vals.append(cur)
    return sorted(vals)


def _change(a, cur, rank, settings):
    """변경량 점수 (뒤 순위일수록 가중) + 바뀐 키"""
    score, keys = 0.0, []
    for k, v in a.items():
        if v == cur[k]:
            continue
        keys.append(k)
        w = 1 + rank.get(k, len(rank))
        if k == "containment":
            d = abs(CONT_ORDER.index(v) - CONT_ORDER.index(cur[k])) / 2
        else:
            s = settings.get(k) or {}
            span = max((s.get("max") or v) - (s.get("min") or v), 1e-9) if s else 1.0
            d = abs(v - cur[k]) / span
        score += w * (d + 0.01)   # 바꾸는 것 자체에도 작은 비용 (같은 효과면 덜 건드림)
    return score, keys


def _search(ctx, allowed, settings, rank, objective, progress=None, frac=(0.0, 1.0)):
    sc, cfg = ctx.sc, ctx.cfg
    cur = current(sc, cfg)
    lim, app, rapp, margin = cfg["inlet_limit_c"], cfg["coil_approach_k"], cfg["coil_return_approach_k"], ctx.margin
    air_keys = ["containment", "crah_per_side", "tile_balancing", "airflow_ratio"]
    grids = [(_values(k, settings[k], cur[k]) if k in allowed else [cur[k]]) for k in air_keys]
    combos = list(itertools.product(*grids))
    best = []
    for i, combo in enumerate(combos):
        if progress and i % 20 == 0:
            progress(frac[0] + (frac[1] - frac[0]) * i / len(combos), f"{len(allowed)}개 파라미터 탐색 ({i}/{len(combos)})")
        a = dict(zip(air_keys, combo))
        m = ctx.metrics(a)
        rise = m["max_inlet_c"] - cfg["supply_temp_c"]
        ret_rise = m["crah_return_c"] - cfg["supply_temp_c"]
        s_max = lim - margin - rise
        # 급기
        if "supply_temp_c" in allowed:
            st = settings["supply_temp_c"]
            if s_max < st["min"] - 1e-9:
                continue
            top = min(_floor_to(s_max, st["min"], st["step"]), st["max"])
            sups = [top] if objective == "min_energy" else [min(cur["supply_temp_c"], top)]
        else:
            if cur["supply_temp_c"] > s_max + 1e-9:
                continue
            sups = [cur["supply_temp_c"]]
        for s in sups:
            ret_lim = s + ret_rise - rapp
            if "chw_delta_t" in allowed:
                dset = settings["chw_delta_t"]
                dts = _values("chw_delta_t", dset, cur["chw_delta_t"])
                if objective == "min_change":
                    dts = sorted(dts, key=lambda d: abs(d - cur["chw_delta_t"]))
            else:
                dts = [cur["chw_delta_t"]]
            for dt in dts:
                ub = min(s - app, ret_lim - dt)
                if "chw_supply_c" in allowed:
                    cs_set = settings["chw_supply_c"]
                    if ub < cs_set["min"] - 1e-9:
                        continue
                    top = min(_floor_to(ub, cs_set["min"], cs_set["step"]), cs_set["max"])
                    cs = top if objective == "min_energy" else min(cur["chw_supply_c"], top)
                else:
                    cs = cur["chw_supply_c"]
                    if cs > ub + 1e-9:
                        continue
                cr = round(cs + dt, 3)
                full = {**a, "supply_temp_c": s, "chw_supply_c": cs, "chw_delta_t": dt}
                score, keys = _change(full, cur, rank, settings)
                c = ctx.layout(a)
                c["supply_temp_c"] = s
                kwh = ctx.kwh(c, m["crah_fan_kw"], cs, cr)
                best.append({"values": full, "chw_return_c": cr, "change_score": round(score, 4), "changed": keys,
                             "kwh": round(kwh, 1), "max_inlet_c": round(rise + s, 2), "fan_kw": m["crah_fan_kw"],
                             "crah_return_c": round(s + ret_rise, 2)})
                if objective == "min_change":
                    break   # 현재에 가장 가까운 ΔT 하나면 충분
    key = (lambda x: (x["kwh"], x["change_score"])) if objective == "min_energy" else (lambda x: (x["change_score"], x["kwh"]))
    best.sort(key=key)
    return best


def influence(sc, cfg, margin=1.0, settings=None):
    """파라미터별 한 단계 변화의 효과 (도움이 되는 방향): 흡입 여유·환수 여유·일 냉방 전력 변화 → '영향도 순서' 추천"""
    ctx = _Ctx(sc, cfg, margin)
    st = {s["key"]: s for s in (settings or defaults(cfg))}
    cur = current(sc, cfg)
    m0 = ctx.metrics(cur)
    cp = sc["cooling_plant"]
    base_kwh = ctx.kwh(ctx.layout(cur) | {"supply_temp_c": cur["supply_temp_c"]}, m0["crah_fan_kw"], cp["chw_supply_c"], cp["chw_return_c"])
    out = []
    for k in DEFAULT_ORDER:
        a, s, cs, dt = dict(cur), cur["supply_temp_c"], cur["chw_supply_c"], cur["chw_delta_t"]
        step = (st.get(k) or {}).get("step") or PARAMS[k].get("step") or 1
        if k == "containment":
            i = CONT_ORDER.index(cur[k])
            if i == len(CONT_ORDER) - 1:
                out.append({"key": k, "label": PARAMS[k]["label"], "note": "이미 핫아일"})
                continue
            a[k] = CONT_ORDER[i + 1]
        elif k in ("tile_balancing", "airflow_ratio", "crah_per_side"):
            a[k] = round(cur[k] + step, 4)
        elif k == "supply_temp_c":
            s = cur[k] - step
        elif k == "chw_supply_c":
            cs = cur[k] - step
        elif k == "chw_delta_t":
            dt = cur[k] - step
        m = ctx.metrics(a)
        rise = m["max_inlet_c"] - cfg["supply_temp_c"]
        c = ctx.layout(a)
        c["supply_temp_c"] = s
        kwh = ctx.kwh(c, m["crah_fan_kw"], cs, cs + dt)
        ret_slack = (s + m["crah_return_c"] - cfg["supply_temp_c"]) - cfg["coil_return_approach_k"] - (cs + dt)
        ret_slack0 = m0["crah_return_c"] - cfg["coil_return_approach_k"] - cp["chw_return_c"]
        sup_slack = s - cs - cfg["coil_approach_k"]
        sup_slack0 = cfg["supply_temp_c"] - cp["chw_supply_c"] - cfg["coil_approach_k"]
        out.append({"key": k, "label": PARAMS[k]["label"],
                    "step": f"{CONT_LABEL[a[k]]}" if k == "containment" else
                    f"{'-' if k in ('supply_temp_c', 'chw_supply_c', 'chw_delta_t') else '+'}{step:g}{PARAMS[k]['unit']}",
                    "inlet_margin_k": round((m0["max_inlet_c"] - (rise + s)), 2),
                    "return_slack_k": round(ret_slack - ret_slack0, 2), "supply_slack_k": round(sup_slack - sup_slack0, 2),
                    "kwh": round(kwh - base_kwh, 1)})
    lim = cfg["inlet_limit_c"]
    codes = [c for c, bad in (("inlet", m0["max_inlet_c"] > lim - margin + 1e-9),
                              ("coil_supply", cfg["supply_temp_c"] < cp["chw_supply_c"] + cfg["coil_approach_k"] - 1e-9),
                              ("coil_return", cp["chw_return_c"] > m0["crah_return_c"] - cfg["coil_return_approach_k"] + 1e-9)) if bad]
    return {"base_kwh": round(base_kwh, 1), "items": out, "problems": codes}


def run(sc, cfg, settings=None, margin=1.0, objective="min_change", progress=None):
    """settings: [{key, enabled, min, max, step, choices}] (우선순위 순서). 반환: 상태·단계·변경·전후 비교·남은 주의"""
    settings = settings or defaults(cfg)
    ctx = _Ctx(sc, cfg, margin)
    cur = current(sc, cfg)
    m0 = ctx.metrics(cur)
    cp = sc["cooling_plant"]
    before_kwh = ctx.kwh({**ctx.layout(cur), "supply_temp_c": cur["supply_temp_c"]}, m0["crah_fan_kw"],
                         cp["chw_supply_c"], cp["chw_return_c"])
    probs = problems(sc, cfg, m0, margin)
    issues0 = evaluate_issues(sc, cfg, ctx.ph, [])
    before = {"max_inlet_c": m0["max_inlet_c"], "inlet_margin_c": round(cfg["inlet_limit_c"] - m0["max_inlet_c"], 2),
              "racks_over_limit": m0["racks_over_limit"], "daily_kwh": round(before_kwh, 1), "problems": probs,
              "issues": [{"level": i["level"], "title": i["title"]} for i in issues0]}
    method = ("우선순위 단계 탐색: 1순위 파라미터만으로 먼저 풀고, 안 되면 다음 순위를 하나씩 더함. 기류 파라미터는 격자 전수(기류 모델), "
              "온도는 해석적 역산(흡입 상승폭은 급기와 무관). 목표: " +
              ("변경량 최소 (뒤 순위일수록 비싸게)" if objective == "min_change" else "경고 없는 범위에서 냉방 전력 최소") +
              f". 제약: 피크 {ctx.ph}시 흡입 여유 ≥ {margin:g}K (다중 존은 모든 전산실), 코일 공급·환수 접근온도")
    base = {"before": before, "method": method, "peak_hour": ctx.ph, "margin_c": margin, "objective": objective}
    if not probs:
        return {**base, "status": "ok", "message": "주의·경고가 없어 바꿀 것이 없습니다 (목표 여유 기준)", "stages": []}
    st = {s["key"]: s for s in settings}
    order = [s["key"] for s in settings if s.get("enabled")]
    if not order:
        return {**base, "status": "unsolved", "message": "켜진 파라미터가 없습니다 — 바꿔도 되는 파라미터를 하나 이상 켜세요", "stages": []}
    rank = {k: i for i, k in enumerate(order)}
    stages = []
    for k in range(1, len(order) + 1):
        allowed = order[:k]
        cands = _search(ctx, set(allowed), st, rank, objective, progress,
                        ((k - 1) / len(order) * 0.9, k / len(order) * 0.9))
        stages.append({"params": allowed, "feasible": len(cands)})
        if cands:
            return {**base, **_result(sc, cfg, ctx, cands[0], cands[1:4], cur, margin, before), "stage": k, "stages": stages,
                    "status": "solved", "evaluations": ctx.evals,
                    "message": (f"{', '.join(PARAMS[x]['label'] for x in allowed)}"
                                f"{'만으로' if k == 1 else '까지 써서'} 해결 ({k}단계)")}
    return {**base, "status": "unsolved", "stages": stages, "evaluations": ctx.evals,
            "message": "켜진 파라미터와 범위로는 해결하지 못했습니다 — 범위를 넓히거나 공사 항목(컨테인먼트·CRAH 대수)을 켜 보세요"}


def _params(sc, cfg, v, chwr):
    return {"containment": v["containment"], "crah_per_side": int(round(v["crah_per_side"])), "airflow_ratio": v["airflow_ratio"],
            "tile_balancing": v["tile_balancing"], "supply_temp_c": v["supply_temp_c"], "chw_supply_c": v["chw_supply_c"],
            "chw_return_c": chwr, "topology": cfg["piping"]["topology"]}


def _result(sc, cfg, ctx, best, alts, cur, margin, before):
    p = _params(sc, cfg, best["values"], best["chw_return_c"])
    sc2, c2 = apply_candidate(sc, cfg, p)
    issues = evaluate_issues(sc2, c2, ctx.ph, [])
    changes = []
    for k in DEFAULT_ORDER:
        a, b = cur[k], best["values"][k]
        if a != b:
            changes.append({"key": k, "label": PARAMS[k]["label"], "unit": PARAMS[k]["unit"],
                            "from": CONT_LABEL.get(a, a) if k == "containment" else a,
                            "to": CONT_LABEL.get(b, b) if k == "containment" else b,
                            "construction": PARAMS[k]["construction"]})
    after = {"max_inlet_c": best["max_inlet_c"], "inlet_margin_c": round(cfg["inlet_limit_c"] - best["max_inlet_c"], 2),
             "racks_over_limit": 0, "daily_kwh": best["kwh"], "problems": [],
             "issues": [{"level": i["level"], "title": i["title"]} for i in issues]}
    return {"params": p, "changes": changes, "after": after,
            "saving_kwh": round(before["daily_kwh"] - best["kwh"], 1),
            "alternatives": [{"params": _params(sc, cfg, x["values"], x["chw_return_c"]), "kwh": x["kwh"],
                              "changed": [PARAMS[k]["label"] for k in x["changed"]]} for x in alts]}
