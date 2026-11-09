"""
값 출처(provenance) 기록·되돌리기·신뢰도 (Phase 10).

- 시나리오의 provenance = {경로: {source, ref, at, previous, note}}. 경로는 'cooling_plant.chiller_cop_rated' 처럼 점 구분
- set_value(): 값을 쓰면서 직전 값을 previous 에 남긴다 (되돌리기 = revert)
- 기록이 없는 필드: 스키마 설명에 '가정값' 이 있으면 assumed, 아니면 user (입력값)
- trust(): 주요 입력 중 측정·도면·보정 기반 비율 (화면 '데이터 신뢰도')
"""
import copy
from datetime import datetime

MEASURED = ("cad", "it_data", "bms_calibrated", "weather_api", "real_calibrated")
# 신뢰도 계산 대상 (경로, 화면 이름). 가정값 필드는 assumed_fields() 로 자동 추가
CORE = [("weather", "기상"), ("it_load.utilization", "IT 부하 24시간"), ("envelope", "외피 면적"),
        ("cooling_layout.racks", "랙 배치·발열")]
_UNSET = object()


def get_path(sc, path, default=None):
    cur = sc
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _set_path(sc, path, value):
    keys = path.split(".")
    cur = sc
    for k in keys[:-1]:
        if cur.get(k) is None:
            cur[k] = {}
        cur = cur[k]
    if value is _UNSET:
        cur.pop(keys[-1], None)
    else:
        cur[keys[-1]] = value


def mark(sc, path, source, ref=None, note=None, previous=_UNSET):
    """출처 기록. previous 를 주면 되돌리기 값으로 저장 (같은 경로에 기존 기록이 있으면 가장 처음 값을 유지)"""
    prov = sc.get("provenance") or {}
    old = prov.get(path)
    e = {"source": source, "at": datetime.now().isoformat(timespec="seconds")}
    if ref:
        e["ref"] = ref
    if note:
        e["note"] = note
    if old and ("previous" in old or old.get("absent_before")):
        for k in ("previous", "absent_before"):
            if k in old:
                e[k] = old[k]
    elif previous is not _UNSET:
        if previous is None:
            e["absent_before"] = True
        else:
            e["previous"] = previous
    prov[path] = e
    sc["provenance"] = prov
    return sc


def set_value(sc, path, value, source, ref=None, note=None):
    old = copy.deepcopy(get_path(sc, path))
    _set_path(sc, path, value)
    return mark(sc, path, source, ref, note, old)


def revert(sc, paths):
    """previous 값으로 되돌리고 기록 삭제"""
    sc = copy.deepcopy(sc)
    prov = sc.get("provenance") or {}
    done = []
    for p in sorted(paths, key=lambda x: -x.count(".")):   # 깊은 경로 먼저 (부모가 나중에 지워지도록)
        e = prov.pop(p, None)
        if e is None:
            continue
        if e.get("absent_before"):
            _set_path(sc, p, _UNSET)
        elif "previous" in e:
            _set_path(sc, p, e["previous"])
        done.append(p)
    if prov:
        sc["provenance"] = prov
    else:
        sc.pop("provenance", None)
    return sc, done


def assumed_fields():
    """스키마 설명에 '가정값' 이 들어간 입력 필드 경로 → 짧은 이름"""
    from .schemas import Scenario
    out = {}

    def walk(model, prefix):
        for name, f in model.model_fields.items():
            ann = f.annotation
            sub = next((a for a in getattr(ann, "__args__", (ann,)) if isinstance(a, type) and hasattr(a, "model_fields")), None)
            path = f"{prefix}{f.alias or name}"
            if sub is not None and name not in ("provenance", "economics"):
                walk(sub, path + ".")
            elif f.description and "가정값" in f.description:
                out[path] = f.description.split(" [")[0].split(" (")[0]
    walk(Scenario, "")
    return out


def trust(sc):
    prov = sc.get("provenance") or {}
    items = []
    for path, label in CORE:
        src = prov.get(path, {}).get("source")
        racks = (sc.get("cooling_layout") or {}).get("racks") or {}
        if path == "cooling_layout.racks" and not src:
            src = "it_data" if any(r.get("kw_profile") for r in racks.get("items") or []) else \
                "cad" if racks.get("items") else None
        if path == "envelope" and not src and (sc.get("building") or {}).get("source"):
            src = "cad"
        items.append({"path": path, "label": label, "source": src or "assumed"})
    for path, label in assumed_fields().items():
        items.append({"path": path, "label": label, "source": prov.get(path, {}).get("source", "assumed")})
    measured = sum(1 for i in items if i["source"] in MEASURED)
    assumed = sum(1 for i in items if i["source"] == "assumed")
    return {"measured": measured, "assumed": assumed, "decided": len(items) - measured - assumed,
            "measured_pct": round(100 * measured / len(items)), "items": items}


def guide(sc):
    """시작 가이드 단계 상태 (연간 분석 여부는 화면 상태라 프론트에서 판단)"""
    prov = sc.get("provenance") or {}
    racks = (sc.get("cooling_layout") or {}).get("racks") or {}
    srcs = {e.get("source") for e in prov.values()}
    return {
        "cad": bool((sc.get("building") or {}).get("source")),
        "it": any(r.get("kw_profile") for r in racks.get("items") or []) or bool(sc["it_load"].get("dataset_id")),
        "it_dataset": bool(sc["it_load"].get("dataset_id")),
        "bms": "bms_calibrated" in srcs,
        "improve": bool(srcs & {"optimizer", "schedule"}),
        "invest": bool(sc.get("economics")),
    }
