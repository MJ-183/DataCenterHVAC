"""
주의·경고 → 원인 설명 + 해결 방법(바꿀 설정·권장값·예상 결과).

- 해결 방법마다 target(화면의 설정 위치 키)과 권장값을 준다. 프론트는 그 설정으로 바로 이동해 값을 채워 둔다 (적용은 사용자)
- 예상 결과는 기류 모델(airflow.analyze)을 실제로 다시 돌려 얻은 값 (추정 문구가 아님)
- 흡입온도 상승폭(최대 흡입 - 급기)은 급기온도와 무관하다 (모델 식 Tin = Tsup + r*dT/(1-r)) → 급기 권장값은 역산

target 키 (frontend/src/lib/issues.ts 와 같아야 함):
  layout.<supply|chws|chwr|ratio|tile|per_side|limit|approach|containment>  냉각 구조 설정
  rack.types  랙·냉각 방식 화면 / opt.day  운전 최적화(하루)
"""
import copy
import math

from .airflow import CONTAINMENT, END_FACTOR, analyze, building_dims, crah_positions, crah_unit_rated

from .outlook import MARGIN_WARN_C   # 흡입온도 여유 주의 기준 (권장 운전점 탐색과 같은 값)

CONT_LABEL = {"open": "개방형", "cold_aisle": "콜드아일", "hot_aisle": "핫아일"}


def _fix(target, label, detail, value=None, effect=None, solves=None):
    return {"target": target, "label": label, "value": value, "detail": detail, "effect": effect, "solves": solves}


def _floor_half(x):
    return math.floor(x * 2 + 1e-9) / 2


def _metrics(sc, cfg, hour, mod):
    c = copy.deepcopy(cfg)
    mod(c)
    return analyze(sc, c, hour)["metrics"]


def _effect(m, limit, fan0=None):
    s = f"최대 흡입 {m['max_inlet_c']:.1f}℃ (여유 {limit - m['max_inlet_c']:.1f}℃), 초과 랙 {m['racks_over_limit']}개"
    if fan0 is not None and abs(m["crah_fan_kw"] - fan0) >= 0.5:
        s += f", CRAH 팬 {m['crah_fan_kw'] - fan0:+.0f} kW"
    return s


def _cause(sc, cfg, af):
    """가장 뜨거운 랙의 흡입온도가 왜 높은지 (급기 부족 재순환 vs 열 끝 재순환)"""
    racks, m = af["racks"], af["metrics"]
    hot = max(racks, key=lambda r: r["inlet_c"])
    t_sup, dt = cfg["supply_temp_c"], cfg["server_delta_t_k"]
    r_base = CONTAINMENT[cfg["containment"]][0] * cfg.get("recirculation_scale", 1.0)
    beta = cfg["crah"]["tile_balancing"]
    mean_kw = sum(r["kw"] for r in racks) / len(racks)
    name = hot["id"] or f"{hot['row'] + 1}열 {hot['col'] + 1}번째"
    starved = hot["required_m3_s"] > 0 and 1 - hot["supplied_m3_s"] / hot["required_m3_s"] > r_base * END_FACTOR[0] - 1e-6
    dens = hot["kw"] / max(mean_kw, 1e-9)
    lines = [f"가장 뜨거운 랙은 {name} ({hot['kw']:.1f} kW"
             + (f", 평균 랙 {mean_kw:.1f} kW의 {dens:.1f}배" if dens > 1.3 else "")
             + f")로, 흡입 {hot['inlet_c']:.1f}℃ = 급기 {t_sup:g}℃ + 재순환 상승 {hot['inlet_c'] - t_sup:.1f}℃ 입니다."]
    if starved:
        if m["supply_ratio"] < 1.0:
            why = (f"CRAH 총 풍량이 서버 요구 풍량의 {m['supply_ratio']:.2f}배로 모자라고(풍량 배율 "
                   f"{cfg['crah']['airflow_ratio']:g}), 바이패스 {m['bypass_fraction'] * 100:.0f}% 를 빼면 랙에 닿는 공기는 더 적습니다.")
        elif dens > 1.3:
            why = (f"타일 급기는 'CRAH 거리 기준 {(1 - beta) * 100:.0f}% + 랙 수요 비례 {beta * 100:.0f}%'(타일 밸런싱 {beta:g})로 "
                   f"나뉘어, 발열이 큰 랙도 주변 랙과 비슷한 양만 받습니다.")
        else:
            why = (f"이 자리는 CRAH 에서 멀어(도달 거리 {cfg['crah']['throw_length_m']:g} m 감쇠) 거리 기준으로 나뉘는 타일 급기 "
                   f"{(1 - beta) * 100:.0f}% 몫이 적습니다.")
        lines.append(
            f"서버 팬이 {hot['required_m3_s']:.2f} m³/s 의 공기를 빨아들여야 하는데 바닥 타일에서 받는 급기는 "
            f"{hot['supplied_m3_s']:.2f} m³/s 뿐입니다. {why} "
            f"모자란 {hot['recirculation'] * 100:.0f}%는 랙 뒤쪽 고온 배기(흡입+{dt:g}K)가 앞으로 돌아 들어와 채워지므로 "
            f"흡입온도가 오릅니다 (재순환 비율 r → 상승폭 r×ΔT/(1−r)).")
    else:
        short = max(1 - hot["supplied_m3_s"] / hot["required_m3_s"], 0.0) if hot["required_m3_s"] > 0 else 0.0
        lines.append(
            (f"타일 급기 부족분({short * 100:.0f}%)보다 " if short > 0.005 else "타일 급기는 충분하지만 ")
            + f"열 끝 랙이라 배기가 옆으로 돌아 들어오는 재순환이 더 큽니다: {CONT_LABEL[cfg['containment']]} 기본 "
            f"{r_base * 100:.0f}% × 끝 가중 {END_FACTOR[0]:g} = {hot['recirculation'] * 100:.0f}%. "
            f"이 재순환은 풍량·타일 배분으로는 줄지 않고 컨테인먼트나 급기온도로만 줄일 수 있습니다.")
    lines.append(f"전체 급기/요구 풍량 비는 {m['supply_ratio']:.2f}, 바이패스 {m['bypass_fraction'] * 100:.0f}% 입니다"
                 + (" — 총 풍량은 남는데 배분이 맞지 않는 상태입니다." if m["supply_ratio"] > 1.1 and starved else "."))
    return " ".join(lines), hot, starved


def inlet_fixes(sc, cfg, hour, target_margin=0.0):
    """랙 최대 흡입온도를 (상한 - target_margin) 이하로 낮추는 설정별 방법. 각 방법은 다시 계산한 결과를 붙인다"""
    limit = cfg["inlet_limit_c"]
    goal = limit - target_margin
    base = analyze(sc, cfg, hour)
    m0 = base["metrics"]
    fan0 = m0["crah_fan_kw"]
    cp = sc["cooling_plant"]
    t_sup = cfg["supply_temp_c"]
    rise = m0["max_inlet_c"] - t_sup
    fixes = []
    ok = lambda m: m["max_inlet_c"] <= goal + 1e-9

    # 1) 급기온도 낮추기 (상승폭은 급기와 무관 → 역산)
    t_new = _floor_half(goal - rise)
    if t_new < t_sup:
        need_chw = t_new - cfg["coil_approach_k"]
        coil = (f" 단, 코일 접근온도 {cfg['coil_approach_k']:g}K 때문에 냉수 공급온도도 {need_chw:g}℃ 이하로 "
                f"낮춰야 합니다(현재 {cp['chw_supply_c']:g}℃) — 칠러 COP 가 떨어져 전력이 늘어납니다."
                if need_chw < cp["chw_supply_c"] else "")
        if need_chw < 6:
            coil += " 냉수 6℃ 미만은 일반 데이터센터 칠러 운전 범위를 벗어나므로 현실적인 해결책이 아닙니다 — 다른 방법을 우선하세요."
        fixes.append(_fix("layout.supply", "CRAH 급기온도 낮추기", value=t_new, solves=True,
                          effect=f"최대 흡입 {rise + t_new:.1f}℃ (여유 {limit - rise - t_new:.1f}℃)",
                          detail=f"급기온도를 {t_sup:g} → {t_new:g}℃ 로 낮추면 모든 랙 흡입이 같은 폭만큼 내려갑니다. "
                                 f"가장 빠른 조치지만 재순환 자체는 그대로라 에너지 효율은 나빠집니다.{coil}"))
    # 2) 풍량 배율 올리기
    r0 = cfg["crah"]["airflow_ratio"]
    for k in range(1, 31):
        ratio = round(r0 + 0.05 * k, 2)
        if ratio > 1.5:
            break
        m = _metrics(sc, cfg, hour, lambda c: c["crah"].__setitem__("airflow_ratio", ratio))
        if ok(m):
            fixes.append(_fix("layout.ratio", "CRAH 풍량 배율 올리기", value=ratio, solves=True,
                              effect=_effect(m, limit, fan0),
                              detail=f"풍량 배율을 {r0:g} → {ratio:g} 로 올리면 모든 타일 급기가 늘어 공기가 모자란 랙의 "
                                     f"재순환이 줄어듭니다. 팬 전력은 풍량의 3승으로 늘어납니다 (VFD 상사법칙)."))
            break
    # 3) 타일 밸런싱 (고밀도 랙 앞 타일 개구율 조정)
    b0 = cfg["crah"]["tile_balancing"]
    for k in range(1, 11):
        b = round(b0 + 0.1 * k, 2)
        if b > 1.0 + 1e-9:
            break
        m = _metrics(sc, cfg, hour, lambda c: c["crah"].__setitem__("tile_balancing", b))
        if ok(m) or (b >= 1.0 - 1e-9 and m["max_inlet_c"] < m0["max_inlet_c"] - 0.1):
            fixes.append(_fix("layout.tile", "타일 밸런싱 올리기 (고밀도 랙 앞 타일 개구율 ↑)", value=b, solves=ok(m),
                              effect=_effect(m, limit),
                              detail=f"타일 밸런싱 {b0:g} → {b:g}: 발열이 큰 랙 앞에 개구율 높은 타일(그레이팅)을 두고 "
                                     f"저밀도 구역 타일은 줄여, 총 풍량은 그대로 두고 필요한 곳으로 급기를 돌립니다. "
                                     f"팬 전력이 늘지 않아 에너지 측면에서 가장 유리한 방법입니다."))
            break
    # 4) 컨테인먼트
    order = ["open", "cold_aisle", "hot_aisle"]
    for cont in order[order.index(cfg["containment"]) + 1:]:
        m = _metrics(sc, cfg, hour, lambda c: c.__setitem__("containment", cont))
        if m["max_inlet_c"] < m0["max_inlet_c"] - 0.05:
            fixes.append(_fix("layout.containment", f"{CONT_LABEL[cont]} 컨테인먼트 설치", value=cont, solves=ok(m),
                              effect=_effect(m, limit),
                              detail=f"{CONT_LABEL[cfg['containment']]} → {CONT_LABEL[cont]}: 냉기·열기 통로를 막아 배기 재순환과 "
                                     f"바이패스를 줄입니다(공사 필요). 급기 부족이 원인인 랙은 컨테인먼트만으로는 다 해결되지 않을 수 있어 "
                                     f"타일 밸런싱과 함께 쓰는 것이 일반적입니다."))
            if ok(m):
                break
    # 5) CRAH 대수 (좌표 지정 배치가 아닐 때만)
    if not cfg["crah"].get("positions"):
        ew, ns, _ = building_dims(sc)
        unit = crah_unit_rated(sc, cfg, len(crah_positions(cfg, ew, ns)))
        n0 = cfg["crah"]["per_side"]
        for n in range(n0 + 1, n0 + 4):
            m = _metrics(sc, cfg, hour, lambda c: (c["crah"].__setitem__("per_side", n),
                                                   c["crah"].__setitem__("unit_airflow_m3_s", round(unit, 4))))
            if ok(m):
                fixes.append(_fix("layout.per_side", "CRAH 증설", value=n, solves=True, effect=_effect(m, limit, fan0),
                                  detail=f"남·북 각 {n0} → {n}대 (같은 크기 장비 추가): 총 풍량과 랙 주변 급기 도달 범위가 함께 늘어납니다. "
                                         f"투자비가 들고 팬 전력이 늘어납니다."))
                break
    # 6) 액체 냉각 전환 (고밀도 랙)
    racks = base["racks"]
    mean_kw = sum(r["kw"] for r in racks) / len(racks)
    hot_n = sum(1 for r in racks if r["inlet_c"] > goal)
    if hot_n and max(r["kw"] for r in racks if r["inlet_c"] > goal) > 1.5 * mean_kw:
        fixes.append(_fix("rack.types", "고밀도 랙을 DLC(액체 냉각)로", solves=None,
                          detail="흡입이 높은 랙이 평균보다 발열이 훨씬 큽니다. 그 랙(또는 열)을 DLC 냉각판(발열의 약 75%를 냉수로 회수)으로 "
                                 "바꾸면 공기로 나가는 열과 필요한 풍량이 크게 줄어 재순환이 사라집니다. '랙·냉각 방식'에서 열별로 바꿀 수 있습니다."))
    # 7) 흡입 상한 (ASHRAE 권장 범위 상한 27℃ 미만일 때만)
    if limit < 27.0 and m0["max_inlet_c"] <= 27.0 - target_margin:
        fixes.append(_fix("layout.limit", "흡입온도 상한 재검토", value=27.0, solves=True,
                          detail=f"현재 상한 {limit:g}℃ 는 ASHRAE TC9.9 권장 범위 상한(27℃)보다 엄격합니다. 서버 제조사 허용 범위를 확인해 "
                                 f"27℃ 로 두면 지금 운전점 그대로 만족합니다. 근거 없이 올리지는 마세요."))
    fixes.append(_fix("opt.day", "운전 최적화 실행", solves=None,
                      detail="급기온도·풍량·타일·컨테인먼트·냉수 온도를 함께 바꿔 보며 흡입 상한을 지키는 최소 전력 조합을 찾습니다. "
                             "위 방법 중 어느 것을 쓸지 정하기 어렵다면 먼저 실행해 보세요."))
    # 해결되는 방법 먼저 → 일부 개선 → 판단 불가(화면 이동형). 같은 그룹 안에서는 에너지 측면 유리한 순
    rank = {True: 0, False: 1, None: 2}
    pref = ["layout.tile", "layout.containment", "rack.types", "layout.ratio", "layout.per_side", "layout.supply",
            "layout.limit", "opt.day"]
    return sorted(fixes, key=lambda f: (rank[f["solves"]], pref.index(f["target"])))


def inlet_issue(sc, cfg, hour=None, *, label="현재 운전점", where="피크"):
    """흡입 상한 초과(crit) 또는 여유 부족(warn). 문제가 없으면 None"""
    af = analyze(sc, cfg, hour)
    m, limit = af["metrics"], cfg["inlet_limit_c"]
    margin = limit - m["max_inlet_c"]
    if margin >= MARGIN_WARN_C:
        return None
    cause, _, _ = _cause(sc, cfg, af)
    crit = m["racks_over_limit"] > 0
    title = (f"{label}: {where} {af['hour']}시 랙 흡입온도가 상한 {limit:g}℃ 를 넘습니다 "
             f"(최대 {m['max_inlet_c']:.1f}℃, 초과 {m['racks_over_limit']}/{m['rack_count']}개)" if crit else
             f"{label}: {where} {af['hour']}시 랙 흡입온도 여유가 {margin:.1f}℃ 로 작습니다 (최대 {m['max_inlet_c']:.1f}℃ / 상한 {limit:g}℃)")
    return {"level": "crit" if crit else "warn", "code": "inlet", "title": title, "cause": cause,
            "fixes": inlet_fixes(sc, cfg, af["hour"], 0.0 if crit else MARGIN_WARN_C)}


def coil_supply_issue(sc, cfg):
    cp = sc["cooling_plant"]
    t, cs, app = cfg["supply_temp_c"], cp["chw_supply_c"], cfg["coil_approach_k"]
    if t >= cs + app - 1e-9:
        return None
    fixes = [_fix("layout.chws", "냉수 공급온도 낮추기", value=_floor_half(t - app), solves=True,
                  detail=f"냉수 공급을 {cs:g} → {_floor_half(t - app):g}℃ 로 낮추면 코일이 급기 {t:g}℃ 를 만들 수 있습니다. "
                         f"냉수온도가 1℃ 내려갈 때마다 칠러 COP 가 떨어져 전력이 늘어납니다.")]
    ph = max(range(24), key=lambda k: sc["it_load"]["utilization"][k])
    m = _metrics(sc, cfg, ph, lambda c: c.__setitem__("supply_temp_c", cs + app))
    fixes.append(_fix("layout.supply", "CRAH 급기온도 올리기", value=round(cs + app, 1), solves=m["racks_over_limit"] == 0,
                      effect=_effect(m, cfg["inlet_limit_c"]),
                      detail=f"급기를 {t:g} → {cs + app:g}℃ 로 올리면 지금 냉수로 만들 수 있는 온도가 됩니다. "
                             f"랙 흡입도 같은 폭만큼 오르니 예상 결과의 흡입 여유를 확인하세요."))
    fixes.append(_fix("layout.approach", "코일 접근온도 확인 (가정값)", value=round(t - cs, 1), solves=None,
                      detail=f"코일 접근온도 {app:g}K 는 장비 자료 없이 둔 가정값입니다. CRAH 코일 선정표의 '급기 - 냉수 입구' 온도차가 "
                             f"더 작다면(예: {t - cs:g}K) 그 값으로 바꾸면 위반이 사라집니다. 근거 없이 바꾸지는 마세요."))
    return {"level": "crit", "code": "coil_supply",
            "title": f"급기온도 {t:g}℃ 가 냉수 공급 {cs:g}℃ + 코일 접근온도 {app:g}K = {cs + app:g}℃ 보다 낮습니다",
            "cause": f"냉수 코일은 냉수보다 최소 {app:g}K 높은 공기까지만 만들 수 있습니다. 지금 설정은 코일이 물리적으로 낼 수 없는 "
                     f"급기온도를 요구하므로 실제로는 급기가 {cs + app:g}℃ 이상으로 나가고, 계산된 흡입온도·전력이 맞지 않게 됩니다.",
            "fixes": fixes}


def coil_return_issue(sc, cfg, hour=None):
    cp = sc["cooling_plant"]
    af = analyze(sc, cfg, hour)
    t_ret, cr, ra = af["metrics"]["crah_return_c"], cp["chw_return_c"], cfg["coil_return_approach_k"]
    if cr <= t_ret - ra + 1e-9:
        return None
    new_cr = _floor_half(t_ret - ra)
    fixes = [_fix("layout.chwr", "냉수 환수온도 낮추기", value=new_cr, solves=new_cr > cp["chw_supply_c"],
                  detail=f"냉수 환수를 {cr:g} → {new_cr:g}℃ 로 낮춥니다(냉수 ΔT {cr - cp['chw_supply_c']:g} → "
                         f"{new_cr - cp['chw_supply_c']:g}K). 같은 열량을 옮기려면 냉수 유량과 펌프 전력이 늘어납니다.")]
    r0 = cfg["crah"]["airflow_ratio"]
    for k in range(1, 15):
        ratio = round(r0 - 0.05 * k, 2)
        if ratio < 0.3:
            break
        m = _metrics(sc, cfg, af["hour"], lambda c: c["crah"].__setitem__("airflow_ratio", ratio))
        if cr <= m["crah_return_c"] - ra + 1e-9:
            fixes.append(_fix("layout.ratio", "CRAH 풍량 배율 낮추기", value=ratio, solves=m["racks_over_limit"] == 0,
                              effect=f"CRAH 환기 {m['crah_return_c']:.1f}℃, " + _effect(m, cfg["inlet_limit_c"], af["metrics"]["crah_fan_kw"]),
                              detail=f"풍량 배율 {r0:g} → {ratio:g}: 남는 급기가 랙을 거치지 않고 돌아오는 바이패스가 줄어 "
                                     f"CRAH 환기온도가 오르고, 팬 전력도 줄어듭니다. 흡입 여유가 줄어드니 예상 결과를 확인하세요."))
            break
    return {"level": "crit", "code": "coil_return",
            "title": f"냉수 환수 {cr:g}℃ 가 CRAH 환기 {t_ret:.1f}℃ − {ra:g}K = {t_ret - ra:.1f}℃ 보다 높습니다",
            "cause": f"냉수는 코일을 지나며 환기 공기보다 최소 {ra:g}K 낮은 온도까지만 데워집니다. 바이패스 "
                     f"{af['metrics']['bypass_fraction'] * 100:.0f}% 로 찬 급기가 섞여 환기가 {t_ret:.1f}℃ 에 그쳐, 설정한 냉수 환수온도까지 "
                     f"물을 데울 수 없습니다 → 실제 냉수 ΔT 가 작아지고 유량이 부족해집니다.",
            "fixes": fixes}


def mixed_issue(text):
    return {"level": "warn", "code": "mixed", "title": text,
            "cause": "기류 계산은 랙별 냉각 방식(공랭/DLC/액침)을 직접 쓰지만, 부하·칠러 계산(엔진)은 시나리오 전체의 냉각 방식 하나만 씁니다. "
                     "둘이 다르면 냉방 전력과 액체 루프 부하가 랙 구성과 맞지 않게 계산됩니다.",
            "fixes": [_fix("rack.types", "랙·냉각 방식에서 다시 적용", solves=True,
                           detail="'최적화 → 랙·냉각 방식'에서 '냉각 방식 적용 · 재계산'을 누르면 엔진 냉각 방식이 hybrid_dlc 와 랙 발열 가중 평균 "
                                  "액체 회수율로 자동 맞춰집니다.")]}


def evaluate_issues(sc, cfg, peak_h, warnings):
    out = [i for i in (inlet_issue(sc, cfg, peak_h), coil_supply_issue(sc, cfg), coil_return_issue(sc, cfg, peak_h)) if i]
    out += [mixed_issue(w) for w in warnings]
    return out
