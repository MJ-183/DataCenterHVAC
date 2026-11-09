"""
설비 대수 산정 (표시·배치용) — 칠러·냉각탑·CDU 를 1대 용량으로 나눠 운전 대수 + 예비(N+k) 를 정한다.

엔진 수식은 바꾸지 않는다: 칠러 COP 는 대수·부분부하와 무관하게 전체 부하에 적용 (engine.chiller_cop).
여기서 정한 대수는 3D 배치, 화면 표시, 시간별 운전 대수 참고값으로만 쓴다.

설계 용량 (시나리오 하루 중 최대값 — 시나리오 날짜가 여름 설계일이어야 설계 용량이 됨):
  칠러   = 공기 루프 부하(프리쿨링 없다고 가정) + 액체 루프 보조 칠러 부하
  냉각탑 = 응축(방열) 열량
  CDU    = 액체 루프(DLC/액침) 회수 열량
"""
import math

KINDS = (("chiller", "chiller_unit_kw"), ("tower", "tower_unit_kw"), ("cdu", "cdu_unit_kw"))


def hourly_loads(engine_res):
    """시간별 설비 부하 [kW] {chiller, tower, cdu}"""
    out = {"chiller": [], "tower": [], "cdu": []}
    for x in engine_res["hourly"]:
        p = x["plant"]
        liq = p.get("liquid_loop")
        out["chiller"].append(p["air_loop"]["load_kw"] + (liq["chiller_load_kw"] if liq else 0.0))
        out["tower"].append(x["condenser_water"]["heat_rejection_kw"])
        out["cdu"].append(liq["load_kw"] if liq else 0.0)
    return out


def size_units(cfg, engine_res):
    """cfg.plant 의 *_unit_kw 가 있는 설비만 대수 산정. 하나도 없으면 None (기존 단일 설비 표시)"""
    p = cfg["plant"]
    if not any(p.get(k) for _, k in KINDS):
        return None
    k_sb = p.get("standby_units", 1)
    loads = hourly_loads(engine_res)
    out = {}
    for kind, key in KINDS:
        unit = p.get(key)
        design = max(loads[kind]) if loads[kind] else 0.0
        if not unit or design <= 0:
            continue
        duty = max(math.ceil(design / unit - 1e-9), 1)
        out[kind] = {"unit_kw": unit, "design_kw": round(design, 1), "duty": duty, "standby": k_sb,
                     "total": duty + k_sb, "redundancy": f"N+{k_sb}" if k_sb else "N",
                     "running": [max(math.ceil(q / unit - 1e-9), 0) for q in loads[kind]]}
    return out or None
