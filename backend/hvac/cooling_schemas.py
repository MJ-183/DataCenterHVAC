"""
냉각 구조(기류·배관·최적화) API 요청/응답 스키마. 입력 설정 CoolingLayout 은 schemas.py 에 있다
(Scenario.cooling_layout 으로 시나리오와 함께 저장되도록).
출력 모델은 hvac.airflow / hvac.plant / hvac.optimize 의 반환 dict 구조를 그대로 옮긴 것 (extra=forbid).
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import CRAH_PER_SIDE_MAX, CoolingLayout, Scenario


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- 요청 ----------
class AnalyzeRequest(_M):
    scenario: Scenario
    layout: CoolingLayout | None = Field(default=None, description="없으면 scenario.cooling_layout, 그것도 없으면 기본값")
    hour: int | None = Field(default=None, ge=0, le=23, description="분석 시각. 없으면 IT 부하 최대 시각")


class SearchSpace(_M):
    containment: list[Literal["open", "cold_aisle", "hot_aisle"]] | None = Field(
        default=None, description="허용 컨테인먼트 (공사 가능 범위). 없으면 3종 모두")
    crah_per_side: list[Annotated[int, Field(ge=1, le=CRAH_PER_SIDE_MAX)]] | None = Field(default=None, description="없으면 현재 값만")
    airflow_ratio: list[Annotated[float, Field(gt=0, le=3)]] | None = None
    tile_balancing: list[Annotated[float, Field(ge=0, le=1)]] | None = None
    chw_delta_t: list[Annotated[float, Field(gt=0, le=20)]] | None = None
    topology: list[Literal["radial", "dual", "ring"]] | None = None
    supply_temp_min_c: float | None = None
    supply_temp_max_c: float | None = None
    chw_supply_min_c: float | None = None
    top_n: int | None = Field(default=None, ge=1, le=20)


class OptimizeRequest(_M):
    scenario: Scenario
    space: SearchSpace = SearchSpace()


class ApplyRequest(_M):
    scenario: Scenario
    params: "CandidateParams"


# ---------- 기류 출력 ----------
class RackResult(_M):
    id: str | None = Field(default=None, description="CAD 랙 id (격자 배치면 없음)")
    row: int
    col: int
    x_m: float
    z_m: float
    kw: float
    required_m3_s: float
    supplied_m3_s: float
    recirculation: float
    inlet_c: float
    outlet_c: float


class CrahResult(_M):
    x_m: float
    z_m: float
    airflow_m3_s: float


class Room(_M):
    ew_m: float
    ns_m: float
    rows: int
    per_row: int


class Metrics(_M):
    max_inlet_c: float
    mean_inlet_c: float
    racks_over_limit: int
    rack_count: int
    supply_ratio: float
    bypass_fraction: float
    shi: float
    rhi: float
    crah_return_c: float
    crah_heat_kw: float
    crah_fan_kw: float


class AnalyzeResult(_M):
    hour: int
    it_kw: float
    air_fraction: float = Field(description="IT 발열 중 공기(CRAH)로 처리되는 비율 = 1 - 액체 회수율")
    room: Room
    racks: list[RackResult]
    crah: list[CrahResult]
    metrics: Metrics


# ---------- 연성 평가 / 최적화 출력 ----------
class CandidateParams(_M):
    containment: Literal["open", "cold_aisle", "hot_aisle"]
    crah_per_side: int
    airflow_ratio: float
    tile_balancing: float
    supply_temp_c: float
    chw_supply_c: float
    chw_return_c: float
    topology: Literal["radial", "dual", "ring"]


class PowerBreakdown(_M):
    chiller: float = Field(description="공기측(냉수) 칠러")
    crah_fan: float
    chw_pump: float
    cw_pump: float
    liquid: float = Field(description="액체 루프 보조 칠러 + TCS/FWS 펌프 (hybrid_dlc, immersion, 엔진 결과)")
    total: float


class Candidate(_M):
    params: CandidateParams
    max_inlet_c: float
    racks_over_limit: int
    crah_return_c: float
    shi: float
    peak_kw: PowerBreakdown
    cop_peak: float
    daily_kwh: PowerBreakdown
    chw_head_kpa: float
    feasible: bool
    violations: list[str]


class PlantHour(_M):
    hour: int
    it_kw: float
    cooling_kw: float
    cop: float
    free_fraction: float = Field(description="공기측 냉수 프리쿨링 비율 [-]")
    mode: Literal["chiller", "partial", "free"]
    chiller_kw: float
    crah_fan_kw: float
    chw_pump_kw: float
    cw_pump_kw: float
    liquid_kw: float
    total_kw: float
    chw_flow_m3_h: float
    cw_flow_m3_h: float
    max_inlet_c: float
    racks_over_limit: int
    crah_return_c: float


class PipeSegment(_M):
    kind: Literal["chw_supply", "chw_return", "cw_supply", "cw_return"]
    points: list[Annotated[list[float], Field(min_length=3, max_length=3)]]
    dn: int = Field(description="호칭경 [mm]")
    design_flow_m3_h: float
    length_m: float


class Piping(_M):
    topology: Literal["radial", "dual", "ring"]
    chw_head_kpa: float
    chw_friction_kpa: float
    chw_critical_m: float
    chw_design_flow_m3_h: float
    cw_design_flow_m3_h: float
    segments: list[PipeSegment]


class Box(_M):
    pos: list[float]
    size: list[float]


class BuildingDims(_M):
    ew_m: float
    ns_m: float
    h_m: float


class Geometry(_M):
    building: BuildingDims
    chiller: Box
    tower: Box


class Fix(_M):
    target: str = Field(description="바꿀 설정 위치 키 (layout.supply, rack.types, opt.day 등)")
    label: str
    value: float | str | None = Field(default=None, description="권장값 (설정 칸에 미리 채움)")
    detail: str
    effect: str | None = Field(default=None, description="권장값으로 다시 계산한 예상 결과")
    solves: bool | None = Field(default=None, description="이 방법만으로 해결되는지 (None = 판단 불가)")


class Issue(_M):
    level: Literal["crit", "warn", "info"]
    code: str
    title: str
    cause: str
    fixes: list[Fix]


class HallMetrics(_M):
    id: str
    name: str
    floor: int
    it_design_kw: float
    max_inlet_c: float
    mean_inlet_c: float
    racks_over_limit: int
    rack_count: int
    supply_ratio: float
    bypass_fraction: float
    shi: float
    rhi: float
    crah_return_c: float
    crah_heat_kw: float
    crah_fan_kw: float


class UnitPlan(_M):
    unit_kw: float
    design_kw: float = Field(description="설계 부하 = 시나리오 하루 중 최대 [kW]")
    duty: int
    standby: int
    total: int
    redundancy: str
    running: list[int] = Field(description="시간별 운전 대수 (참고값)")


# ---------- 다중 존 건물 전체 보기 (hvac/building_view.py, 건물 좌표: 원점 = 건물 중심, x=동, y=위, z=남) ----------
class BFloor(_M):
    floor: int
    elevation_m: float = Field(description="층 바닥 높이 (층고 누적) [m]")
    height_m: float


class BZone(_M):
    id: str
    name: str
    kind: Literal["data_hall", "support", "office"]
    floor: int
    x_m: float
    z_m: float
    ew_m: float
    ns_m: float
    height_m: float
    elevation_m: float


class BRack(_M):
    id: str = Field(description="CAD 랙 id, 격자 배치면 F{층}-R{열:02}-{번:03} (도면 생성기와 같은 규칙)")
    row: int
    col: int
    x: float
    y: float = Field(description="층 바닥 높이 [m]")
    z: float
    kw: float = Field(description="피크 시각 IT 전력 [kW]")
    inlet_c: float = Field(description="피크 시각 흡입온도 [℃]")


class BCrah(_M):
    x: float
    y: float
    z: float
    airflow: float = Field(description="풍량 [m3/s]")


class BHall(_M):
    zone: str
    floor: int
    racks: list[BRack]
    rack_inlet_c: list[list[float]] = Field(description="[시각 0~23][랙] 흡입온도 [℃]")
    crah: list[BCrah]


class BUnit(_M):
    pos: list[float] = Field(description="중심 [x, y, z]")
    size: list[float]
    standby: bool = Field(description="예비기 (N+k 의 k, 배관 운전 유량 0)")


class BCdu(_M):
    floor: int
    zone: str | None
    pos: list[float]
    size: list[float]


class BPlant(_M):
    chiller: list[BUnit]
    tower: list[BUnit]
    cdu: list[BCdu]
    source: dict[Literal["chiller", "tower", "cdu"], Literal["cad", "auto"]]


class BPipeSegment(PipeSegment):
    kind: Literal["chw_supply", "chw_return", "cw_supply", "cw_return", "fws_supply", "fws_return"]
    part: Literal["chiller_link", "yard_header", "feed", "riser", "floor_branch", "hall", "tower_link", "cw_header",
                  "fws_feed", "fws_riser", "fws_floor", "cdu_link"]
    zone: str | None = None
    floor: int | None = None


class BInlet(_M):
    zone: str
    floor: int
    point: list[float]


class BRiser(_M):
    x_m: float
    z_m: float
    base_m: float
    top_m: float = Field(description="입상관 최상단 = 가장 높은 전산실 인입점 높이 [m]")


class BPiping(_M):
    segments: list[BPipeSegment]
    chw_head_kpa: float
    chw_friction_kpa: float
    chw_critical_m: float
    critical_zone: str = Field(description="양정을 정한 (최원단) 전산실")
    riser_friction_kpa: float = Field(description="최원단 경로의 입상관 마찰 (공급+환수) [kPa]")
    cw_yard_dynamic_kpa: float = Field(
        description="참고값: 야드 헤더 기준 냉각수 마찰 x 2 + 응축기 [kPa]. 펌프 전력 계산은 기존 단일 배관 모델 그대로")
    chw_design_flow_m3_h: float
    cw_design_flow_m3_h: float
    fws_design_flow_m3_h: float | None
    inlets: list[BInlet]
    riser: BRiser


class BuildingView(_M):
    floors: list[BFloor]
    footprint: list[list[float]]
    zones: list[BZone]
    halls: list[BHall]
    plant: BPlant
    piping: BPiping
    notes: list[str]


class EvaluateResult(_M):
    issues: list[Issue] = []
    peak_hour: int
    airflow: AnalyzeResult
    rack_inlet_c: list[list[float]] = Field(description="[시각 0~23][랙] 흡입온도 [℃]")
    hourly: Annotated[list[PlantHour], Field(min_length=24, max_length=24)]
    summary: Candidate
    piping: Piping
    geometry: Geometry
    warnings: list[str] = []
    zone: str | None = Field(default=None, description="다중 존: 3D·랙·배관 표시에 쓴 전산실 존 id")
    zones: list[HallMetrics] | None = Field(default=None, description="다중 존: 전산실별 피크 시각 기류 지표")
    units: dict[Literal["chiller", "tower", "cdu"], UnitPlan] | None = Field(
        default=None, description="설비 대수 산정 (cooling_layout.plant 의 *_unit_kw 가 있을 때, 표시용)")
    building_view: BuildingView | None = Field(
        default=None, description="다중 존: 건물 좌표계 층·존·랙·CRAH·설비·배관 (건물 전체 3D)")


class OptimizeResult(_M):
    peak_hour: int
    airflow_evaluations: int
    evaluated: int
    baseline: Candidate
    candidates: list[Candidate]


ApplyRequest.model_rebuild()
