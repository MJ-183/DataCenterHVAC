"""
입력(시나리오) / 출력(시뮬레이션 결과) pydantic 스키마.

- 프론트엔드 타입(frontend/src/types)은 이 파일을 기준으로 맞춘다.
- 출력 모델은 hvac.engine.simulate() 의 반환 dict 구조를 그대로 옮긴 것이다.
  (extra="forbid" 이므로 엔진에 키가 추가/삭제되면 tests/test_api.py 가 실패한다)
- 입력 검증은 계산이 불가능한 값(예: ΔT<=0, COC<=1, 24개가 아닌 시간 배열)만 막는다.
  수식은 engine.py 에 있고 여기서 바꾸지 않는다.
"""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .engine import PLANT_DEFAULTS as PD
from .engine import STEP_MINUTES
from .realism import REALISM_DEFAULTS as RD

HOURS = 24
# CRAH 남·북 각 대수 상한. 2 MW 샘플은 5대 → 30 이면 충분했지만, 40 MW(공기측 ~830 m3/s)는 1대 15 m3/s 로 28대/측,
# 공랭 전량(air_chw)이면 100대/측을 넘으므로 120 으로 둔다 (기본값 5 는 그대로)
CRAH_PER_SIDE_MAX = 120

Orientation = Literal["N", "E", "S", "W"]
Architecture = Literal["air_chw", "hybrid_dlc", "immersion"]
Mode = Literal["chiller", "partial", "free"]
Hourly = Annotated[list[float], Field(min_length=HOURS, max_length=HOURS)]
NonNeg = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- 입력 ----------
class Site(_In):
    name: str
    latitude: float = Field(ge=-90, le=90, description="위도 [deg]")
    longitude: float = Field(ge=-180, le=180, description="경도 [deg, 동경 +]")
    std_meridian: float = Field(ge=-180, le=180, description="표준시 자오선 [deg], KST=135")
    date: date
    asos_station_id: int | None = Field(default=None, description="연간 기상에 쓸 기상청 ASOS 관측소 (가까운 유인관서)")
    asos_solar_station_id: int | None = Field(
        default=None, description="그 관측소에 일사 관측이 없을 때 일사를 빌려 쓸 관측소 (없으면 전운량으로 추정)")
    asos_solar: Literal["auto", "cloud"] | None = Field(
        default=None, description="auto(기본) = 관측 일사 우선, cloud = 전운량 추정만")
    weather_year: int | None = Field(default=None, ge=1940, le=2100,
                                     description="연간 분석에 쓸 관측 연도 (비우면 작년)")
    elevation_m: float | None = Field(default=None, ge=-500, le=9000,
                                      description="해발 고도 [m] (Phase 19 실측 비교의 고도 습구 민감도 분석에만 사용, 엔진은 쓰지 않음)")


class Weather(_In):
    dry_bulb_c: Hourly = Field(description="시간별 외기 건구온도 [℃], 24개")
    rh_pct: Annotated[list[Annotated[float, Field(ge=0, le=100)]],
                      Field(min_length=HOURS, max_length=HOURS)] = Field(description="시간별 상대습도 [%], 24개")
    ghi_w_m2: Annotated[list[NonNeg], Field(min_length=HOURS, max_length=HOURS)] = Field(
        description="시간별 수평면 전일사량 [W/m2], 24개")


class Indoor(_In):
    temp_c: float = Field(description="실내 설정온도 [℃]")
    rh_pct: float = Field(ge=0, le=100, description="실내 설정 상대습도 [%]")


class Wall(_In):
    orientation: Orientation
    area_m2: NonNeg
    u_value: NonNeg = Field(description="열관류율 [W/m2.K]")
    absorptance: Fraction = Field(description="외표면 일사 흡수율 [-]")


class Roof(_In):
    area_m2: NonNeg
    u_value: NonNeg
    absorptance: Fraction


class Window(_In):
    orientation: Orientation
    area_m2: NonNeg
    u_value: NonNeg
    shgc: Fraction = Field(description="일사열취득계수 [-]")


class Envelope(_In):
    walls: list[Wall]
    roof: Roof
    windows: list[Window]


class ITLoad(_In):
    design_kw: NonNeg = Field(description="IT 설계부하 [kW]")
    utilization: Annotated[list[NonNeg], Field(min_length=HOURS, max_length=HOURS)] = Field(
        description="시간별 가동률 배율 [-], 24개 (IT 부하 = design_kw x 배율)")
    # Phase 11
    dataset_id: str | None = Field(default=None, max_length=32,
                                   description="IT 데이터셋 id (기간 전체 계측, 연간·내일 예측의 IT 소스). 24시간 계산에는 영향 없음")


class Internal(_In):
    floor_area_m2: NonNeg
    lighting_w_m2: NonNeg
    people: NonNeg
    people_kw_each: NonNeg
    ups_loss_ratio: Fraction = Field(description="UPS 손실 비율 (IT 부하 대비) [-]")
    # Phase 12
    misc_kw: float | None = Field(default=None, ge=-100000, le=100000,
                                  description="기타 발열 [kW] (BMS 보정값, 모델에 없는 발열·누설). 없으면 0")


class Ventilation(_In):
    outdoor_air_m3_h: NonNeg


class Realism(_In):
    """운전 현실성 모델 (Phase 16, hvac/realism.py). 단계별로 켜고 끔. 모두 끄면 기존 단순 모델과 같음. 기본값은 가정값"""
    control: bool = Field(default=False, description="R1 제어 이력: 프리쿨링 전환 이력·칠러 최소 운전/정지 시간")
    fc_hysteresis_k: Annotated[float, Field(ge=0, le=5)] = Field(default=RD["fc_hysteresis_k"],
                                                                description="프리쿨링 전환 이력 폭 [K] (가정값)")
    chiller_min_on_h: Annotated[float, Field(ge=0, le=12)] = Field(default=RD["chiller_min_on_h"],
                                                                  description="칠러 최소 운전 시간 [h] (가정값)")
    chiller_min_off_h: Annotated[float, Field(ge=0, le=12)] = Field(default=RD["chiller_min_off_h"],
                                                                   description="칠러 최소 정지 시간 [h] (가정값)")
    stage_min_interval_h: Annotated[float, Field(ge=0, le=12)] = Field(default=RD["stage_min_interval_h"],
                                                                      description="칠러 대수 변경 최소 간격 [h] (가정값, R2)")
    chiller_min_plr: Annotated[float, Field(ge=0, le=0.9)] = Field(default=RD["chiller_min_plr"],
                                                                  description="칠러 최소 부하율 [-] (가정값)")


class CoolingPlant(_In):
    chw_supply_c: float = Field(description="냉수 공급온도 [℃]")
    chw_return_c: float = Field(description="냉수 환수온도 [℃]")
    cw_supply_c: float = Field(description="냉각수 공급온도(냉각탑 출구) [℃]")
    cw_return_c: float = Field(description="냉각수 환수온도(냉각탑 입구) [℃]")
    chiller_cop_rated: float = Field(gt=0, description="칠러 정격 COP [-]")
    design_wet_bulb_c: float = Field(description="COP 보정 기준 습구온도 [℃]")
    cop_wb_sensitivity: float = Field(description="습구 1℃당 COP 보정률 [-/K] (가정값)")
    crah_fan_heat_ratio: NonNeg = Field(description="CRAH 팬 발열 비율 [-] (가정값)")
    tower_cycles_of_concentration: float = Field(gt=1, description="냉각탑 농축배수 COC [-]")
    tower_drift_ratio: Fraction = Field(description="비산 손실 비율 (냉각수 유량 대비) [-]")
    # ---- Phase 3.5 냉각 방식 / 프리쿨링 / 보조동력 (기본값은 engine.PLANT_DEFAULTS, 모두 가정값) ----
    architecture: Architecture = Field(default=PD["architecture"],
                                       description="냉각 방식: air_chw=공랭(CRAH+칠러), hybrid_dlc=직접액체냉각 혼합, immersion=액침")
    liquid_fraction: Fraction = Field(default=PD["liquid_fraction"], description="DLC 냉각판 회수 IT 발열 비율 [-] (가정값)")
    immersion_capture: Fraction = Field(default=PD["immersion_capture"], description="액침 냉각액 회수 IT 발열 비율 [-] (가정값)")
    tcs_supply_c: float = Field(default=PD["tcs_supply_c"], description="TCS(서버측 액체 루프) 공급온도 [℃] (가정값)")
    tcs_return_c: float = Field(default=PD["tcs_return_c"], description="TCS 환수온도 [℃] (가정값)")
    cdu_approach_k: NonNeg = Field(default=PD["cdu_approach_k"], description="CDU 열교환기 접근온도 [K] (가정값)")
    free_cooling: bool = Field(default=PD["free_cooling"], description="수측 이코노마이저(프리쿨링) 사용")
    tower_approach_k: NonNeg = Field(default=PD["tower_approach_k"], description="냉각탑 출구 수온 - 외기 습구 [K] (가정값)")
    hx_approach_k: NonNeg = Field(default=PD["hx_approach_k"], description="프리쿨링 판형 열교환기 접근온도 [K] (가정값)")
    cop_supply_sensitivity: Annotated[float, Field(ge=0, le=0.1)] = Field(
        default=PD["cop_supply_sensitivity"], description="공급수온 1℃ 상승당 칠러 COP 증가율 [-/K] (가정값)")
    cop_ref_supply_c: float = Field(default=PD["cop_ref_supply_c"], description="정격 COP 기준 공급수온 [℃] (가정값)")
    chw_pump_kw_per_kw: NonNeg = Field(default=PD["chw_pump_kw_per_kw"], description="냉수 펌프 kW / 공기측 냉방 kW (가정값)")
    cw_pump_kw_per_kw: NonNeg = Field(default=PD["cw_pump_kw_per_kw"], description="냉각수 펌프 kW / 방열 kW (가정값)")
    liquid_pump_kw_per_kw: NonNeg = Field(default=PD["liquid_pump_kw_per_kw"],
                                          description="TCS+FWS 펌프 kW / 액체측 냉방 kW (가정값)")
    tower_fan_kw_per_kw: NonNeg = Field(default=PD["tower_fan_kw_per_kw"], description="냉각탑 팬 kW / 방열 kW (가정값)")
    realism: Realism | None = Field(default=None, description="운전 현실성 모델 (없으면 단순 모델)")

    @model_validator(mode="after")
    def _check_delta_t(self):
        if self.chw_return_c <= self.chw_supply_c:
            raise ValueError("chw_return_c 는 chw_supply_c 보다 커야 합니다 (냉수 ΔT > 0)")
        if self.cw_return_c <= self.cw_supply_c:
            raise ValueError("cw_return_c 는 cw_supply_c 보다 커야 합니다 (냉각수 ΔT > 0)")
        if self.tcs_return_c <= self.tcs_supply_c:
            raise ValueError("tcs_return_c 는 tcs_supply_c 보다 커야 합니다 (TCS ΔT > 0)")
        return self


# ---------- 냉각 구조 (기류·배관 모델 hvac/airflow.py, hvac/piping.py, hvac/plant.py 용, 선택 입력) ----------
# 기본값은 모두 가정값. 엔진 simulate() 는 이 값을 읽지 않는다 (기존 결과 불변).
PosNum = Annotated[float, Field(gt=0)]


class RackItem(_In):
    """CAD 에서 가져온 개별 랙 (좌표계: 원점=건물 중심, x=동, z=남. 열은 x 방향으로 정렬된 상태)"""
    id: str = Field(min_length=1, max_length=64)
    x_m: float
    z_m: float
    row: int = Field(ge=0, le=59, description="열 번호 (z 오름차순)")
    design_kw: NonNeg | None = Field(default=None, description="랙 설계(정격) 전력 [kW]")
    kw_profile: Annotated[list[NonNeg], Field(min_length=HOURS, max_length=HOURS)] | None = Field(
        default=None, description="랙 시간별 IT 전력 [kW], 24개 (IT 데이터 가져오기 결과)")
    # Phase 15
    liquid_fraction: Fraction | None = Field(
        default=None, description="이 랙 발열 중 액체(DLC)로 회수하는 비율 [-]. 랙별 지정이 하나라도 있으면 미지정 랙은 공랭(0)")


class RackGrid(_In):
    rows: int = Field(default=8, ge=1, le=60)
    per_row: int = Field(default=40, ge=1, le=200)
    width_m: PosNum = 0.6
    depth_m: PosNum = 1.2
    aisle_m: PosNum = 2.4
    row_density: list[Annotated[float, Field(ge=0)]] | None = Field(
        default=None, description="열별 상대 전력밀도 [-], 길이=rows. 없으면 균일")
    items: Annotated[list[RackItem], Field(min_length=1, max_length=12000)] | None = Field(
        default=None, description="개별 랙 좌표 (CAD). 있으면 rows x per_row 격자 대신 사용")
    # Phase 15
    row_liquid_fraction: list[Fraction | None] | None = Field(
        default=None, description="격자 배치의 열별 액체 회수 비율 [-], 길이=rows (None=공랭)")
    slots: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, description="빈 랙 자리 [[x_m, z_m], ...] (CAD 'slot' 역할, 증설 배치 후보)")

    @model_validator(mode="after")
    def _len(self):
        if self.row_density is not None and (len(self.row_density) != self.rows or sum(self.row_density) <= 0):
            raise ValueError("row_density 길이는 rows 와 같아야 하고 합이 0보다 커야 합니다")
        if self.row_liquid_fraction is not None and len(self.row_liquid_fraction) != self.rows:
            raise ValueError("row_liquid_fraction 길이는 rows 와 같아야 합니다")
        if self.items is not None:
            if max(r.row for r in self.items) >= self.rows:
                raise ValueError("items 의 row 는 rows 보다 작아야 합니다")
            if len({r.id for r in self.items}) != len(self.items):
                raise ValueError("items 의 랙 id 가 중복되었습니다")
        return self


class BuildingGeometry(_In):
    """CAD 외곽선에서 얻은 건물 치수. 있으면 외피 면적 역산(building_dims) 대신 사용"""
    source: str = Field(default="cad", description="출처 (파일 이름 등)")
    ew_m: PosNum = Field(description="동서 길이 [m] (3D·기류 좌표계)")
    ns_m: PosNum = Field(description="남북 길이 [m]")
    height_m: PosNum = Field(description="층고 [m]")
    rotation_deg: float = Field(default=0.0, description="랙 열을 x 축에 맞추려고 도면을 돌린 각도 [deg] (표시용)")
    footprint: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, description="외곽선 [[x_m, z_m], ...] (3D 좌표계)")


class CrahConfig(_In):
    per_side: int = Field(default=5, ge=1, le=CRAH_PER_SIDE_MAX, description="남·북 벽에 각각 놓는 CRAH 수 (positions 가 없을 때)")
    positions: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, description="CRAH 좌표 [[x_m, z_m], ...] (원점=건물 중심, x=동, z=남). 지정하면 per_side 무시")
    airflow_ratio: float = Field(default=1.0, gt=0, le=3, description="설계 풍량 대비 운전 풍량 배율 (VFD)")
    design_margin: float = Field(default=1.2, ge=1, le=3, description="설계 풍량 여유율 [-] (가정값)")
    fan_kw_per_m3_s: float = Field(default=0.35, gt=0, description="정격 풍량당 팬 전력 [kW per m3/s] (가정값)")
    throw_length_m: PosNum = Field(default=18.0, description="CRAH 급기 도달 거리 감쇠 길이 [m] (가정값)")
    tile_balancing: float = Field(default=0.3, ge=0, le=1, description="0=거리 기반 배분, 1=수요 비례 배분(타일 개구율 조정)")
    unit_airflow_m3_s: PosNum | None = Field(
        default=None, description="CRAH 1대 정격 풍량 [m3/s]. 없으면 공기측 설계 풍량 / 대수 (대수를 바꾸면 1대 크기도 바뀜)")


class CduPosition(_In):
    floor: int = Field(ge=0, le=200)
    x_m: float
    z_m: float


class PlantPlacement(_In):
    gap_east_m: float = Field(default=12.0, ge=2, description="건물 동측 벽 ~ 칠러 간격 [m]")
    chiller_size_m: Annotated[list[PosNum], Field(min_length=3, max_length=3)] = [8.0, 3.0, 3.0]
    chiller_z_m: float = -6.0
    tower_size_m: Annotated[list[PosNum], Field(min_length=3, max_length=3)] = [7.0, 5.0, 7.0]
    tower_z_m: float = 8.0
    # 설비 대수 산정 (hvac/plant_units.py, 표시용). 없으면 단일 설비로 표시
    chiller_unit_kw: PosNum | None = Field(default=None, description="칠러 1대 냉방용량 [kW] (대수 표시용, 계산에는 안 씀)")
    tower_unit_kw: PosNum | None = Field(default=None, description="냉각탑 1대 방열용량 [kW] (대수 표시용)")
    cdu_unit_kw: PosNum | None = Field(default=None, description="CDU 1대 용량 [kW] (대수 표시용)")
    standby_units: int = Field(default=1, ge=0, le=4, description="설비별 예비 대수 (N+k)")
    # 다중 존 건물 전체 보기 (hvac/building_view.py) — 건물 좌표계 (원점 = 건물 중심, x=동, z=남). CAD 에서 읽음
    chiller_positions: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, description="칠러 중심 [[x_m, z_m], ...] (건물 좌표). 없으면 건물 외곽 동측 + gap_east_m 에 대수만큼 배열")
    tower_positions: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, description="냉각탑 중심 [[x_m, z_m], ...] (건물 좌표). 없으면 칠러 열 동측에 배열")
    cdu_positions: list[CduPosition] | None = Field(
        default=None, description="CDU 위치 (건물 좌표, 층별). 없으면 전산실과 같은 층 지원 존에 배분")


class PipingConfig(_In):
    topology: Literal["radial", "dual", "ring"] = Field(
        default="radial", description="냉수 분배: radial=단일 헤더 직렬, dual=남·북 2계통, ring=루프(환상)")
    max_velocity_m_s: float = Field(default=2.0, gt=0.3, le=4, description="관경 선정 최대 유속 [m/s]")
    fitting_factor: float = Field(default=1.5, ge=1, le=3, description="이음쇠·밸브 상당길이 배율 [-] (가정값)")
    pump_efficiency: float = Field(default=0.65, gt=0.1, le=0.95, description="펌프+모터 종합효율 [-] (가정값)")
    evaporator_dp_kpa: NonNeg = Field(default=50.0, description="칠러 증발기 압력손실 (설계유량) [kPa] (가정값)")
    coil_dp_kpa: NonNeg = Field(default=80.0, description="CRAH 코일+제어밸브 압력손실 (설계유량) [kPa] (가정값)")
    condenser_dp_kpa: NonNeg = Field(default=60.0, description="칠러 응축기 압력손실 (설계유량) [kPa] (가정값)")
    tower_static_kpa: NonNeg = Field(default=50.0, description="냉각탑 정수두+노즐 (유량 무관) [kPa] (가정값)")


class ScheduleEntry(_In):
    """월별 운전점 (Phase 13 연간 최적화 결과)"""
    month: int = Field(ge=1, le=12)
    supply_temp_c: float
    chw_supply_c: float
    chw_return_c: float
    airflow_ratio: float = Field(gt=0, le=3)

    @model_validator(mode="after")
    def _dt(self):
        if self.chw_return_c <= self.chw_supply_c:
            raise ValueError("냉수 환수온도는 공급온도보다 높아야 합니다")
        return self


class CoolingLayout(_In):
    racks: RackGrid = RackGrid()
    crah: CrahConfig = CrahConfig()
    plant: PlantPlacement = PlantPlacement()
    piping: PipingConfig = PipingConfig()
    containment: Literal["open", "cold_aisle", "hot_aisle"] = "open"
    supply_temp_c: float = Field(default=18.0, description="CRAH 급기온도 [℃]")
    server_delta_t_k: PosNum = Field(default=12.0, description="서버 흡입-배기 온도차 [K] (가정값)")
    inlet_limit_c: float = Field(default=27.0, description="랙 흡입온도 허용 상한 [℃] (ASHRAE 권장범위 상한)")
    coil_approach_k: NonNeg = Field(default=6.0, description="급기온도 - 냉수 공급온도 최소차 [K] (가정값)")
    coil_return_approach_k: NonNeg = Field(default=2.0, description="CRAH 환기온도 - 냉수 환수온도 최소차 [K] (가정값)")
    # Phase 12 / 13
    recirculation_scale: PosNum = Field(default=1.0, le=5, description="컨테인먼트 재순환 기본비 배율 [-] (BMS 보정값, 기본 1)")
    operating_schedule: list[ScheduleEntry] | None = Field(
        default=None, description="월별 운전점 (연간 최적화). 연간·내일 예측에 사용, 24시간 화면은 현재 운전점")

    @model_validator(mode="after")
    def _sched(self):
        if self.operating_schedule is not None:
            ms = [e.month for e in self.operating_schedule]
            if len(set(ms)) != len(ms):
                raise ValueError("operating_schedule 의 월이 중복되었습니다")
        return self
    # 칠러 COP 의 공급수온 보정은 cooling_plant.cop_supply_sensitivity / cop_ref_supply_c (엔진과 공용)


# ---------- Phase 10: 데이터 출처 ----------
ProvSource = Literal["assumed", "user", "cad", "it_data", "bms_calibrated", "optimizer", "schedule", "weather_api",
                     "published", "real_calibrated"]   # Phase 19: 공개 자료 사실 / 실측 비교 비례 보정


class Provenance(_In):
    source: ProvSource
    ref: str | None = Field(default=None, max_length=200, description="데이터셋 id, 파일 이름, 보정 실행 id 등")
    at: datetime | None = None
    previous: float | str | bool | list | dict | None = Field(default=None, description="되돌리기용 직전 값")
    absent_before: bool | None = Field(default=None, description="적용 전에는 값이 없었음 (되돌리면 필드 삭제)")
    note: str | None = Field(default=None, max_length=300)


# ---------- Phase 16: 투자 검토 ----------
class Measure(_In):
    id: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    capex_krw: NonNeg | None = Field(default=None, description="투자비 [원]. 없으면 예시 단가 x 수량 (시연용)")
    om_krw_per_year: float = Field(default=0.0, description="연간 유지비 변화 [원/년] (+ 증가)")
    life_years: int = Field(default=15, ge=1, le=40)


class Economics(_In):
    electricity_krw_per_kwh: PosNum = Field(default=150.0, description="전기요금 [원/kWh] (예시값)")
    water_krw_per_m3: NonNeg = Field(default=2000.0, description="상·하수도 요금 [원/m3] (예시값)")
    co2_kg_per_kwh: NonNeg = Field(default=0.45, description="전력 탄소배출계수 [kgCO2/kWh] (예시값)")
    discount_rate: Annotated[float, Field(ge=0, le=0.3)] = 0.05
    years: int = Field(default=15, ge=1, le=40)
    energy_escalation: Annotated[float, Field(ge=-0.1, le=0.2)] = Field(default=0.0, description="전기요금 연 상승률 [-]")
    it_growth_pct_per_year: Annotated[float, Field(ge=-50, le=100)] = 0.0
    measures: list[Measure] = []


# ---------- Phase 18: 다중 존 (적층 건물) ----------
ZoneKind = Literal["data_hall", "support", "office"]


class ZonePartition(_In):
    """다른 존과 맞닿은 내부 경계 (층간 슬래브·내벽). 같은 쌍은 한 번만 적는다"""
    zone: str = Field(description="맞닿은 존 id")
    area_m2: NonNeg
    u_value: NonNeg = Field(description="내부 경계 열관류율 [W/m2.K] (가정값)")
    kind: Literal["slab", "wall"] = "wall"


class ZoneGeometry(_In):
    """존 평면 치수 (기류 모델·3D). 원점 = 건물 중심, x=동, z=남"""
    ew_m: PosNum
    ns_m: PosNum
    height_m: PosNum = Field(description="층고 [m]")
    x_m: float = Field(default=0.0, description="존 중심의 건물 중심 대비 동쪽 오프셋 [m] (3D 표시)")
    z_m: float = Field(default=0.0, description="존 중심의 남쪽 오프셋 [m] (3D 표시)")


class Zone(_In):
    """적층 건물의 존 하나. 엔진은 존마다 단일 존 계산을 하고 합산 (hvac/multizone.py).
    기상·사이트·냉각 설비(cooling_plant)·운전점은 건물 공통, IT 시간 모양은 건물 it_load.utilization 공통"""
    id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=40)
    floor: int = Field(ge=0, le=50, description="층 (0 = 1층)")
    kind: ZoneKind = Field(description="data_hall = 전산실(랙·CRAH 기류 모델), support = 전기·기계실, office = 사무·관제")
    geometry: ZoneGeometry
    envelope: Envelope = Field(description="이 존의 외기 접촉면만 (내부 경계는 partitions)")
    it_design_kw: NonNeg = Field(default=0.0, description="존 IT 설계부하 [kW] (합 = it_load.design_kw)")
    indoor: Indoor | None = Field(default=None, description="없으면 건물 indoor")
    floor_area_m2: PosNum
    lighting_w_m2: NonNeg | None = Field(default=None, description="없으면 건물 internal 값")
    people: NonNeg = 0.0
    misc_kw: float | None = Field(default=None, ge=-100000, le=100000, description="기타 발열 [kW] (가정값, 장비 발열 등)")
    outdoor_air_m3_h: NonNeg = 0.0
    architecture: Architecture | None = Field(default=None, description="없으면 건물 냉각 방식")
    liquid_fraction: Fraction | None = None
    racks: RackGrid | None = Field(default=None, description="전산실 랙 격자 (없으면 cooling_layout.racks)")
    crah_positions: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, description="전산실 CRAH 좌표 [[x_m, z_m], ...] (존 좌표계, CAD). 없으면 공통 운전점의 per_side 배치")
    partitions: list[ZonePartition] = []


class Scenario(_In):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    note: str | None = Field(default=None, alias="_note", description="시나리오 설명/데이터 출처 메모")
    site: Site
    weather: Weather
    indoor: Indoor
    envelope: Envelope
    it_load: ITLoad
    internal: Internal
    ventilation: Ventilation
    cooling_plant: CoolingPlant
    cooling_layout: CoolingLayout | None = Field(
        default=None, description="전산실 배치·기류·배관 설정 (선택). 없으면 기본값. 부하 엔진에는 영향 없음")
    building: BuildingGeometry | None = Field(
        default=None, description="CAD 에서 가져온 건물 치수 (선택). 없으면 외피 면적에서 역산")
    provenance: dict[str, Provenance] | None = Field(
        default=None, description="값 출처 (키 = 'cooling_plant.chiller_cop_rated' 같은 경로). 없으면 가정값/사용자 입력")
    economics: Economics | None = Field(default=None, description="투자 검토 조건 (선택)")
    zones: Annotated[list[Zone], Field(min_length=1, max_length=40)] | None = Field(
        default=None, description="다중 존 (적층 건물). 있으면 엔진은 존별로 계산해 합산하고, 건물 envelope·internal 의 "
                                  "면적·조명·인원·외기량은 쓰지 않음 (표시용)")

    @model_validator(mode="after")
    def _zones(self):
        if not self.zones:
            return self
        ids = [z.id for z in self.zones]
        if len(set(ids)) != len(ids):
            raise ValueError("zones 의 id 가 중복되었습니다")
        for z in self.zones:
            for p in z.partitions:
                if p.zone not in ids or p.zone == z.id:
                    raise ValueError(f"존 {z.id} 의 partitions 가 없는 존({p.zone})이나 자기 자신을 가리킵니다")
        if not any(z.kind == "data_hall" for z in self.zones):
            raise ValueError("zones 에 전산실(data_hall)이 하나 이상 있어야 합니다")
        it = sum(z.it_design_kw for z in self.zones)
        if abs(it - self.it_load.design_kw) > max(1e-3 * self.it_load.design_kw, 1e-6):
            raise ValueError(f"존 IT 설계부하 합({it:g} kW)이 it_load.design_kw({self.it_load.design_kw:g} kW)와 다릅니다")
        return self

    def to_engine_dict(self) -> dict:
        """engine.simulate() 에 넘길 dict (date 는 'YYYY-MM-DD' 문자열)"""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


# ---------- 출력 ----------
class HourWeather(_Out):
    dry_bulb_c: float
    rh_pct: float
    wet_bulb_c: float
    ghi_w_m2: float


class LoadKw(_Out):
    it: float
    ups_loss: float
    lighting: float
    people: float
    wall_conduction: float
    roof_conduction: float
    window_conduction: float
    window_solar: float
    ventilation: float
    crah_fan: float
    total_cooling: float


class ChilledWater(_Out):
    flow_m3_h: float
    supply_c: float
    return_c: float


class Chiller(_Out):
    cop: float
    power_kw: float


class CondenserWater(_Out):
    heat_rejection_kw: float
    flow_m3_h: float
    supply_c: float
    return_c: float


class MakeupWater(_Out):
    evaporation: float
    drift: float
    blowdown: float
    total: float


class Sun(_Out):
    altitude_deg: float = Field(description="태양 고도 [deg], 음수면 지평선 아래")
    azimuth_deg: float = Field(description="태양 방위각 [deg], 남=0, 서=+, 동=-")


class SurfaceSolar(_Out):
    N: float
    E: float
    S: float
    W: float
    roof: float = Field(description="지붕(수평면) = GHI")


Reason = Literal["threshold", "hysteresis_hold", "min_on", "min_off", "staging"]


class AirLoop(_Out):
    load_kw: float = Field(description="공기측(CRAH->냉수) 냉방량, CRAH 팬 발열 포함")
    free_fraction: float = Field(description="프리쿨링 처리 비율 [-]")
    chiller_load_kw: float
    mode: Mode
    chiller_on: bool | None = Field(default=None, description="현실성 R1: 칠러 운전 상태")
    reason: Reason | None = Field(default=None, description="현실성 R1: 이 스텝 상태의 이유")
    supply_overshoot_k: float | None = Field(default=None, description="현실성 R1: 칠러 정지 중 냉수 공급온도 상승 [K]")


class LiquidLoop(_Out):
    load_kw: float = Field(description="액체측(TCS->CDU->FWS) 냉방량")
    free_fraction: float
    chiller_load_kw: float
    mode: Mode
    chiller_cop: float = Field(description="FWS 공급온도로 보정한 보조 칠러 COP")
    tcs_supply_c: float
    tcs_return_c: float
    fws_supply_c: float
    fws_return_c: float
    fws_flow_m3_h: float
    chiller_on: bool | None = Field(default=None, description="현실성 R1: 칠러 운전 상태")
    reason: Reason | None = Field(default=None, description="현실성 R1: 이 스텝 상태의 이유")
    supply_overshoot_k: float | None = Field(default=None, description="현실성 R1: 칠러 정지 중 냉수 공급온도 상승 [K]")


class PlantPower(_Out):
    chiller: float
    crah_fan: float
    chw_pump: float
    cw_pump: float
    liquid_pump: float
    tower_fan: float
    cooling_total: float = Field(description="냉방 설비 합계 = 칠러+CRAH 팬+펌프+냉각탑 팬")
    facility: float = Field(description="설비 전체 = IT + UPS 손실 + 조명 + 냉방 설비")


class HourPlant(_Out):
    mode: Mode = Field(description="chiller=칠러 단독, partial=부분 프리쿨링, free=칠러 OFF")
    mode_reason: Reason | None = Field(
        default=None, description="현실성 R1: threshold=전환점 판정, hysteresis_hold=이력 구간 유지, "
                                  "min_on/min_off=최소 운전/정지 시간 유지, staging=대수 변경 간격")
    tower_water_c: float = Field(description="냉각탑 출구 수온 = 습구 + 접근온도 [℃]")
    free_cooling_water_c: float = Field(description="판형 열교환기 통과 후 프리쿨링 수온 [℃]")
    air_loop: AirLoop
    liquid_loop: LiquidLoop | None = Field(description="air_chw 는 없음")
    power_kw: PlantPower
    pue: float | None
    wue_l_per_kwh: float | None


class HourResult(_Out):
    """한 계산 스텝의 결과 (step_min=60 이면 정시 24개, 5 이면 5분 간격 288개)"""
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    t_h: float = Field(ge=0, lt=24, description="시각 [h] = hour + minute/60")
    sun: Sun
    surface_solar_w_m2: SurfaceSolar = Field(description="면별 입사 일사량 [W/m2]")
    weather: HourWeather
    load_kw: LoadKw
    chilled_water: ChilledWater
    chiller: Chiller
    condenser_water: CondenserWater
    makeup_water_m3_h: MakeupWater
    plant: HourPlant


class DailyTotal(_Out):
    cooling_kwh: float
    chiller_kwh: float
    chw_m3: float
    cw_m3: float
    makeup_m3: float
    it_kwh: float
    cooling_power_kwh: float
    facility_kwh: float
    pue: float | None
    wue_l_per_kwh: float | None
    free_cooling_hours: float
    partial_free_cooling_hours: float


class SimulationResult(_Out):
    site: str
    date: str
    step_min: int = Field(description="계산 간격 [분]")
    architecture: Architecture
    hourly: Annotated[list[HourResult], Field(min_length=HOURS, max_length=24 * 60)] = Field(
        description="스텝별 결과 (이름은 호환성 유지용, step_min 간격)")
    daily_total: DailyTotal
    control: dict | None = Field(default=None, description="현실성 R1: 칠러 전환 횟수·마지막 상태·초기화 설명")
    zones: list["ZoneResult"] | None = Field(default=None, description="다중 존 시나리오의 존별 결과 (건물 합계는 위 값)")

    @model_validator(mode="after")
    def _check_steps(self):
        if self.step_min not in STEP_MINUTES or len(self.hourly) != 24 * 60 // self.step_min:
            raise ValueError("step_min 과 hourly 길이가 맞지 않습니다")
        return self


class ZoneResult(_Out):
    id: str
    name: str
    floor: int
    kind: str
    architecture: Architecture
    interzone_kw: float = Field(description="다른 존에서 들어오는 전도열 [kW] (+ = 유입)")
    daily_total: DailyTotal
    it_kw: list[float]
    cooling_kw: list[float] = Field(description="스텝별 냉방부하 [kW]")
    cooling_power_kw: list[float] = Field(description="스텝별 냉방 설비 전력 [kW]")
    peak_cooling_kw: float


class CompareHour(_Out):
    t_h: float
    mode: Mode
    pue: float | None
    facility_kw: float
    cooling_power_kw: float
    chiller_kw: float


class ArchitectureRun(_Out):
    architecture: Architecture
    daily_total: DailyTotal
    hourly: list[CompareHour]


class ArchitectureComparison(_Out):
    """같은 날씨·IT 부하에서 냉각 방식별 결과 (engine.compare_architectures)"""
    step_min: int
    current: Architecture
    architectures: list[ArchitectureRun]
SimulationResult.model_rebuild()
