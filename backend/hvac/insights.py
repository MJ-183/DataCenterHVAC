"""
결과 요약 지표 계산과 해설 문장 수치 검증 (Phase 4.5 AI 해설용).

LLM 이 직접 계산하지 않도록, 해설에 필요한 파생값(피크, 비중, 최소/최대)은 여기서 코드로 계산해 전달한다.
"""
import re

LOAD_GROUPS = {
    "IT": ["it"],
    "UPS 손실": ["ups_loss"],
    "외피(벽·지붕·창 관류+창 일사)": ["wall_conduction", "roof_conduction", "window_conduction", "window_solar"],
    "외기 도입": ["ventilation"],
    "조명·인원": ["lighting", "people"],
    "CRAH 팬": ["crah_fan"],
}


def hourly_only(result: dict) -> dict:
    """정시 스텝만 남긴 결과 (LLM 에 보낼 원본 데이터 크기를 줄이기 위함)"""
    return {**result, "hourly": [x for x in result["hourly"] if x.get("minute", 0) == 0]}


def compute_facts(result: dict) -> dict:
    h = result["hourly"]
    dt_h = result.get("step_min", 60) / 60.0
    load = lambda x: x["load_kw"]["total_cooling"]
    cop = lambda x: x["chiller"]["cop"]
    pw = lambda x: x["chiller"]["power_kw"]
    mk = lambda x: x["makeup_water_m3_h"]["total"]
    tdb = lambda x: x["weather"]["dry_bulb_c"]
    twb = lambda x: x["weather"]["wet_bulb_c"]

    total_kwh = sum(load(x) for x in h) * dt_h
    group_kwh = {g: round(sum(sum(x["load_kw"][k] for k in ks) for x in h) * dt_h, 1) for g, ks in LOAD_GROUPS.items()}
    shares = {g: round(v / total_kwh * 100, 1) if total_kwh else 0.0 for g, v in group_kwh.items()}
    mk_parts = {k: round(sum(x["makeup_water_m3_h"][k] for x in h) * dt_h, 2)
                for k in ("evaporation", "drift", "blowdown")}

    def peak(fn, kind=max):
        x = kind(h, key=fn)
        return {"time": f"{x['hour']:02d}:{x.get('minute', 0):02d}", "value": fn(x)}

    return {
        "site": result["site"], "date": result["date"],
        "daily_total": result["daily_total"],
        "step_min": result.get("step_min", 60),
        "cooling_load_kw": {"peak": peak(load), "min": peak(load, min),
                            "mean": round(sum(load(x) for x in h) / len(h), 1)},
        "load_group_daily_kwh": group_kwh,
        "load_group_share_pct": shares,
        "chiller_cop": {"max": peak(cop), "min": peak(cop, min)},
        "chiller_power_kw": {"peak": peak(pw), "min": peak(pw, min)},
        "outdoor_dry_bulb_c": {"max": peak(tdb), "min": peak(tdb, min)},
        "outdoor_wet_bulb_c": {"max": peak(twb), "min": peak(twb, min)},
        "makeup_water_m3_h": {"peak": peak(mk), "min": peak(mk, min)},
        "makeup_daily_m3_by_component": mk_parts,
        "cooling_architecture": result.get("architecture", "air_chw"),
        "pue_daily": result["daily_total"].get("pue"),
        "wue_l_per_kwh_daily": result["daily_total"].get("wue_l_per_kwh"),
        "free_cooling_hours": result["daily_total"].get("free_cooling_hours"),
        "partial_free_cooling_hours": result["daily_total"].get("partial_free_cooling_hours"),
        "pue_note": "PUE = (IT + UPS 손실 + 조명 + 냉방 설비)/IT. 펌프·냉각탑 팬 계수는 가정값, 변압기 등 기타 손실 미반영",
    }


_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")


def _leaves(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield abs(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _leaves(v)
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):
            yield float(m.group().replace(",", ""))


def unverified_numbers(text: str, *sources) -> list[str]:
    """text 속 숫자 중 sources(결과/요약 지표)에 반올림 기준으로 대응되는 값이 없는 것"""
    allowed = set()
    for s in sources:
        allowed.update(_leaves(s))
    allowed.update(range(0, 25))  # 시각(0~24시), 서수 등
    bad = []
    for m in _NUM.finditer(text):
        tok = m.group()
        x = float(tok.replace(",", ""))
        dec = len(tok.split(".")[1]) if "." in tok else 0
        tol = 0.5 * 10 ** -dec + 1e-9
        if not any(abs(x - a) <= tol for a in allowed):
            bad.append(tok)
    return bad
