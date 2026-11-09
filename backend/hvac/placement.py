"""
증설 랙 배치 추천 (Phase 15) - 새 랙을 어느 빈 자리에 둘지 기류 모델로 고른다 (결정적).

후보 자리: CAD '빈 랙 자리'(racks.slots) + 기존 열의 양 끝 연장 자리(건물 안쪽 7 m 여유, 기존 랙과 겹치지 않게)
가정: CRAH 는 그대로(1대 정격 풍량 고정 - 증설로 풍량이 늘지 않음), 새 랙은 기존과 같은 24시간 가동률 모양,
      IT 설계부하 = 기존 + 새 랙 설계 kW 합
순서: 설계 kW 큰 랙부터 탐욕 배치 → 목적 (흡입 상한 초과 랙 수, 최대 흡입온도, 평균 흡입온도) 사전식 최소
개선: 배치된 새 랙을 하나씩 남은 자리로 옮겨 보며 좋아지면 이동 (최대 max_moves 회)
계산량: 후보 자리는 최대 max_slots 개로 고르게 추림
"""
import copy

from .airflow import analyze, building_dims, crah_positions, crah_unit_rated, rack_items, rack_positions, rack_weights


def to_items(sc, cfg):
    """격자 배치 → 개별 랙 items (설계 kW = 현재 배분 비율 x IT 설계부하). 이미 items 면 설계 kW 만 채움"""
    ew, ns, _ = building_dims(sc)
    racks, rows, per = rack_positions(cfg, ew, ns)
    util = sc["it_load"]["utilization"]
    w = rack_weights(cfg, racks, max(range(24), key=lambda k: util[k]))
    tot = sum(w) or 1.0
    dk = sc["it_load"]["design_kw"]
    existing = rack_items(cfg)
    rlf = cfg["racks"].get("row_liquid_fraction")
    items = []
    for k, (j, i, x, z) in enumerate(racks):
        it = dict(existing[k][4]) if existing else {"id": f"R{j + 1:02d}-{i + 1:03d}", "x_m": round(x, 3),
                                                     "z_m": round(z, 3), "row": j}
        if it.get("design_kw") is None:
            it["design_kw"] = round(dk * w[k] / tot, 3)
        if not existing and rlf and j < len(rlf) and rlf[j] is not None:
            it["liquid_fraction"] = rlf[j]
        items.append(it)
    c = copy.deepcopy(cfg)
    c["racks"].update(items=items, rows=max(rows, 1), per_row=max(per, 1), row_density=None, row_liquid_fraction=None)
    return c


def candidate_slots(sc, cfg, max_slots=150):
    ew, ns, _ = building_dims(sc)
    g = cfg["racks"]
    items = g.get("items") or []
    w = g["width_m"]
    occupied = [(it["x_m"], it["z_m"]) for it in items]
    free = lambda x, z: all(abs(x - ox) >= w * 0.9 or abs(z - oz) >= g["depth_m"] * 0.9 for ox, oz in occupied)
    out = [(round(x, 3), round(z, 3), None) for x, z in (g.get("slots") or []) if free(x, z)]
    rows = {}
    for it in items:
        rows.setdefault(it["row"], []).append(it)
    lim = ew / 2 - 7
    for j, rs in sorted(rows.items()):
        z = sum(r["z_m"] for r in rs) / len(rs)
        xs = sorted(r["x_m"] for r in rs)
        for side, x0, step in (("e", xs[-1], w), ("w", xs[0], -w)):
            x = x0 + step
            while abs(x) <= lim:
                if free(x, z):
                    out.append((round(x, 3), round(z, 3), j))
                x += step
    if len(out) > max_slots:
        k = len(out) / max_slots
        out = [out[int(i * k)] for i in range(max_slots)]
    return out


def _nearest_row(items, z):
    return min(items, key=lambda it: abs(it["z_m"] - z))["row"]


def _score(m):
    return (m["racks_over_limit"], m["max_inlet_c"], m["mean_inlet_c"])


def place(sc, cfg, new_racks, *, max_slots=150, max_moves=200, progress=None):
    """new_racks: [{count, design_kw, liquid_fraction, label}] → 배치 결과"""
    base_cfg = to_items(sc, cfg)
    ew, ns, _ = building_dims(sc)
    unit = crah_unit_rated(sc, base_cfg, len(crah_positions(base_cfg, ew, ns)))
    base_cfg["crah"]["unit_airflow_m3_s"] = base_cfg["crah"].get("unit_airflow_m3_s") or round(unit, 4)
    before = analyze(sc, base_cfg)["metrics"]
    slots = candidate_slots(sc, base_cfg, max_slots)
    queue = []
    for g in new_racks:
        for _ in range(int(g["count"])):
            queue.append({"design_kw": float(g["design_kw"]), "liquid_fraction": g.get("liquid_fraction") or 0.0,
                          "label": g.get("label") or ""})
    queue.sort(key=lambda r: -r["design_kw"] * (1 - r["liquid_fraction"]))
    if len(queue) > len(slots):
        raise ValueError(f"새 랙 {len(queue)}대가 후보 자리 {len(slots)}곳보다 많습니다 — CAD 에 '빈 랙 자리'를 그리거나 대수를 줄이세요")
    util = sc["it_load"]["utilization"]
    umax = max(util) or 1.0
    shape = [u / umax for u in util]
    # 기존 랙이 모두 프로파일을 가지면 새 랙도 프로파일을 준다 (배분 규칙이 바뀌지 않도록)
    has_prof = all(it.get("kw_profile") for it in base_cfg["racks"]["items"])

    def state(placed):
        c = copy.deepcopy(base_cfg)
        add = []
        for n, (r, (x, z, row)) in enumerate(placed):
            it = {"id": f"N{n + 1:03d}", "x_m": x, "z_m": z,
                  "row": row if row is not None else _nearest_row(base_cfg["racks"]["items"], z),
                  "design_kw": r["design_kw"], "liquid_fraction": r["liquid_fraction"]}
            if has_prof:
                it["kw_profile"] = [round(r["design_kw"] * s * umax, 3) for s in shape]
            add.append(it)
        items = c["racks"]["items"] + add
        if any(it.get("liquid_fraction") for it in items):
            for it in items:
                it.setdefault("liquid_fraction", 0.0)
        c["racks"]["items"] = items
        c["racks"]["rows"] = max(c["racks"]["rows"], max(it["row"] for it in items) + 1)
        s = {**sc, "it_load": {**sc["it_load"], "design_kw": sc["it_load"]["design_kw"] + sum(r["design_kw"] for r, _ in placed)}}
        return s, c, add

    def metric(placed):
        s, c, _ = state(placed)
        return analyze(s, c)["metrics"]

    placed, free = [], list(slots)
    n_eval = 0
    for k, r in enumerate(queue):
        if progress:
            progress(0.05 + 0.75 * k / len(queue), f"새 랙 {k + 1}/{len(queue)} 자리 찾는 중")
        best = None
        for si, sl in enumerate(free):
            m = metric(placed + [(r, sl)])
            n_eval += 1
            if best is None or _score(m) < best[0]:
                best = (_score(m), si)
        placed.append((r, free.pop(best[1])))
    moves = 0
    cur = _score(metric(placed))
    improved = True
    while improved and moves < max_moves:
        improved = False
        for k in range(len(placed)):
            for si, sl in enumerate(free[:40]):
                if moves >= max_moves:
                    break
                trial = placed[:k] + [(placed[k][0], sl)] + placed[k + 1:]
                m = metric(trial)
                moves += 1
                n_eval += 1
                if _score(m) < cur:
                    free[si] = placed[k][1]
                    placed, cur, improved = trial, _score(m), True
        if progress:
            progress(0.8 + 0.15 * min(moves / max_moves, 1), "배치 개선")
    s, c, add = state(placed)
    res = analyze(s, c)
    after = res["metrics"]
    by_id = {r["id"]: r for r in res["racks"]}
    air = {}    # 흡입온도가 왜 그런지 보여 주는 기류 값 (서버 요구 풍량 vs 타일 급기, 재순환 비율)
    for it in add:
        r = by_id.get(it["id"])
        it["inlet_c"] = r["inlet_c"] if r else None
        if r:
            air[it["id"]] = {k: r[k] for k in ("required_m3_s", "supplied_m3_s", "recirculation")}
    limit = cfg["inlet_limit_c"]
    from .advice import inlet_issue
    issue = inlet_issue(s, c, res["hour"], label="증설 후") if after["max_inlet_c"] > before["max_inlet_c"] + 0.05 else None
    if issue is None and after["max_inlet_c"] > before["max_inlet_c"] + 0.05:
        from .advice import _cause
        issue = {"level": "info", "code": "inlet_rise",
                 "title": f"증설로 최대 흡입이 {before['max_inlet_c']:.1f} → {after['max_inlet_c']:.1f}℃ 로 오릅니다 (상한 이내)",
                 "cause": _cause(s, c, res)[0], "fixes": []}
    n_dlc = sum(1 for it in add if it["liquid_fraction"] > 0)
    rows_used = sorted({it["row"] + 1 for it in add})
    summary = (f"새 랙 {len(add)}대(DLC {n_dlc}대) 배치 후 최대 흡입 {after['max_inlet_c']:.1f}℃ "
               f"(여유 {limit - after['max_inlet_c']:.1f}℃), 상한 초과 랙 {after['racks_over_limit']}개. "
               f"사용한 열: {', '.join(map(str, rows_used[:8]))}{' 등' if len(rows_used) > 8 else ''}")
    if after["racks_over_limit"]:
        summary += (". 증설 후 흡입 상한을 넘습니다 — 지금 운전점(급기 "
                    f"{cfg['supply_temp_c']:g}℃, 풍량 {cfg['crah']['airflow_ratio'] * 100:.0f}%)의 여유가 작으면 "
                    "'운전 최적화'를 증설 후 시나리오로 다시 실행하거나 랙 대수·kW 를 줄여 보세요")
    return {"items": add, "before": before, "after": after, "summary": summary, "issue": issue, "air": air, "evaluations": n_eval,
            "slots_considered": len(slots), "added_design_kw": round(sum(it["design_kw"] for it in add), 2),
            "unit_airflow_m3_s": base_cfg["crah"]["unit_airflow_m3_s"],
            "notes": ["CRAH 대수·1대 정격 풍량은 그대로 (증설로 풍량이 늘지 않음)",
                      "새 랙은 기존과 같은 24시간 가동률 모양으로 운전한다고 가정",
                      f"후보 자리 {len(slots)}곳 (CAD 빈 자리 + 기존 열 끝 연장), 기류 계산 {n_eval}회"]}
