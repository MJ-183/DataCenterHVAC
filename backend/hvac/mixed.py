"""
랙별 냉각 방식 혼합 (Phase 15) - 랙 φ(액체 회수 비율)와 엔진 전역값을 맞춘다.

- 기류 모델은 랙별 φ 를 직접 쓴다 (airflow.rack_phis)
- 엔진은 단일 존이라 전역값 하나만 쓴다 → 랙 φ 가 있으면 architecture=hybrid_dlc, liquid_fraction=발열 가중 평균 φ
- sync() 는 랙 편집·배치 적용 때만 호출. evaluate 는 불일치를 경고만 한다 (자동으로 바꾸지 않음)
"""
from .airflow import air_fraction, building_dims, rack_phis, rack_positions
from .provenance import set_value
from .schemas import CoolingLayout


def effective_phi(sc, cfg=None):
    """랙 φ 가 있으면 발열 가중 평균 φ, 없으면 None"""
    cfg = cfg or CoolingLayout.model_validate(sc.get("cooling_layout") or {}).model_dump()
    ew, ns, _ = building_dims(sc)
    racks = rack_positions(cfg, ew, ns)[0]
    if not racks or rack_phis(sc, cfg, racks) is None:
        return None
    return round(1.0 - air_fraction(sc, cfg, racks), 4)


def sync(sc):
    """랙 φ → 엔진 전역 냉각 방식 동기화 (출처 기록). 랙 φ 가 없으면 그대로"""
    phi = effective_phi(sc)
    if phi is None:
        return sc
    cp = sc["cooling_plant"]
    if cp.get("architecture") != "hybrid_dlc":
        set_value(sc, "cooling_plant.architecture", "hybrid_dlc", "user", note="랙별 혼합 냉각에서 자동 설정")
    if abs((cp.get("liquid_fraction") or 0) - phi) > 1e-4:
        set_value(sc, "cooling_plant.liquid_fraction", phi, "user", note="랙별 혼합 냉각의 발열 가중 평균")
    return sc


def warning(sc, cfg):
    phi = effective_phi(sc, cfg)
    if phi is None:
        return None
    cp = sc["cooling_plant"]
    if cp.get("architecture") != "hybrid_dlc" or abs(cp.get("liquid_fraction", 0.75) - phi) > 0.01:
        return (f"랙별 냉각 방식(유효 액체 회수율 {phi:.2f})과 냉각 방식 설정이 다릅니다 — "
                "'랙 편집'에서 적용하면 부하 계산도 맞춰집니다")
    return None
