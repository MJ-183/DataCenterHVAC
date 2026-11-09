// backend/hvac/schemas.py 와 1:1 대응. 백엔드 스키마가 바뀌면 이 파일을 함께 수정할 것.

export type Orientation = "N" | "E" | "S" | "W";

// ---------- 입력 ----------
export interface Site {
  name: string;
  latitude: number;
  longitude: number;
  asos_station_id?: number | null;
  asos_solar_station_id?: number | null;
  asos_solar?: "auto" | "cloud" | null;
  weather_year?: number | null;
  std_meridian: number;
  date: string; // YYYY-MM-DD
}
export interface Weather {
  dry_bulb_c: number[];
  rh_pct: number[];
  ghi_w_m2: number[];
}
export interface Indoor {
  temp_c: number;
  rh_pct: number;
}
export interface Wall {
  orientation: Orientation;
  area_m2: number;
  u_value: number;
  absorptance: number;
}
export interface Roof {
  area_m2: number;
  u_value: number;
  absorptance: number;
}
export interface Window {
  orientation: Orientation;
  area_m2: number;
  u_value: number;
  shgc: number;
}
export interface Envelope {
  walls: Wall[];
  roof: Roof;
  windows: Window[];
}
export interface ITLoad {
  design_kw: number;
  utilization: number[];
  dataset_id?: string | null;
}
export interface Internal {
  floor_area_m2: number;
  lighting_w_m2: number;
  people: number;
  people_kw_each: number;
  ups_loss_ratio: number;
  misc_kw?: number | null;
}
export interface Ventilation {
  outdoor_air_m3_h: number;
}
/** 냉각 방식 (Phase 3.5) */
export type Architecture = "air_chw" | "hybrid_dlc" | "immersion";
export type PlantMode = "chiller" | "partial" | "free";
export interface CoolingPlant {
  chw_supply_c: number;
  chw_return_c: number;
  cw_supply_c: number;
  cw_return_c: number;
  chiller_cop_rated: number;
  design_wet_bulb_c: number;
  cop_wb_sensitivity: number;
  crah_fan_heat_ratio: number;
  tower_cycles_of_concentration: number;
  tower_drift_ratio: number;
  // Phase 3.5 - 백엔드가 기본값을 채워 돌려줌 (engine.PLANT_DEFAULTS, 모두 가정값)
  architecture: Architecture;
  liquid_fraction: number;
  immersion_capture: number;
  tcs_supply_c: number;
  tcs_return_c: number;
  cdu_approach_k: number;
  free_cooling: boolean;
  tower_approach_k: number;
  hx_approach_k: number;
  cop_supply_sensitivity: number;
  cop_ref_supply_c: number;
  chw_pump_kw_per_kw: number;
  cw_pump_kw_per_kw: number;
  liquid_pump_kw_per_kw: number;
  tower_fan_kw_per_kw: number;
  realism?: Realism | null;
}
/** 운전 현실성 모델 (Phase 16, backend hvac/realism.py). 없거나 끄면 단순 모델 */
export interface Realism {
  control?: boolean; fc_hysteresis_k?: number; chiller_min_on_h?: number; chiller_min_off_h?: number;
  stage_min_interval_h?: number; chiller_min_plr?: number;
}
export type ModeReason = "threshold" | "hysteresis_hold" | "min_on" | "min_off" | "staging";
// 냉각 구조 (선택 입력, 모든 필드 기본값 있음 - 백엔드 CoolingLayout)
export type Containment = "open" | "cold_aisle" | "hot_aisle";
export type Topology = "radial" | "dual" | "ring";
export interface RackItem {
  id: string;
  x_m: number;
  z_m: number;
  row: number;
  design_kw?: number | null;
  kw_profile?: number[] | null;
  liquid_fraction?: number | null;
}
export interface RackGrid {
  rows: number;
  per_row: number;
  width_m: number;
  depth_m: number;
  aisle_m: number;
  row_density?: number[] | null;
  items?: RackItem[] | null;
  row_liquid_fraction?: (number | null)[] | null;
  slots?: [number, number][] | null;
}
export interface BuildingGeometry {
  source: string;
  ew_m: number;
  ns_m: number;
  height_m: number;
  rotation_deg: number;
  footprint?: [number, number][] | null;
}
export interface CrahConfig {
  per_side: number;
  positions?: [number, number][] | null;
  airflow_ratio: number;
  design_margin: number;
  fan_kw_per_m3_s: number;
  throw_length_m: number;
  tile_balancing: number;
  /** CRAH 1대 정격 풍량 [m3/s]. 없으면 공기측 설계 풍량 / 대수 */
  unit_airflow_m3_s?: number | null;
}
export interface PlantPlacement {
  gap_east_m: number;
  chiller_size_m: [number, number, number];
  chiller_z_m: number;
  tower_size_m: [number, number, number];
  tower_z_m: number;
  /** 설비 1대 용량 [kW] (대수 표시용, 계산에는 안 씀) */
  chiller_unit_kw?: number | null;
  tower_unit_kw?: number | null;
  cdu_unit_kw?: number | null;
  standby_units?: number;
  /** 다중 존 건물 전체 보기: 건물 좌표 [[x, z], ...] (CAD). 없으면 자동 배치 */
  chiller_positions?: [number, number][] | null;
  tower_positions?: [number, number][] | null;
  cdu_positions?: { floor: number; x_m: number; z_m: number }[] | null;
}
export interface PipingConfig {
  topology: Topology;
  max_velocity_m_s: number;
  fitting_factor: number;
  pump_efficiency: number;
  evaporator_dp_kpa: number;
  coil_dp_kpa: number;
  condenser_dp_kpa: number;
  tower_static_kpa: number;
}
export interface CoolingLayout {
  racks: RackGrid;
  crah: CrahConfig;
  plant: PlantPlacement;
  piping: PipingConfig;
  containment: Containment;
  supply_temp_c: number;
  server_delta_t_k: number;
  inlet_limit_c: number;
  coil_approach_k: number;
  coil_return_approach_k: number;
  recirculation_scale?: number;
  operating_schedule?: ScheduleEntry[] | null;
}
export interface Scenario {
  _note?: string | null;
  site: Site;
  weather: Weather;
  indoor: Indoor;
  envelope: Envelope;
  it_load: ITLoad;
  internal: Internal;
  ventilation: Ventilation;
  cooling_plant: CoolingPlant;
  cooling_layout?: CoolingLayout | null;
  building?: BuildingGeometry | null;
  provenance?: Record<string, Provenance> | null;
  economics?: Economics | null;
  /** 다중 존 (적층 건물, Phase 18). 있으면 엔진은 존별 계산 후 합산 */
  zones?: Zone[] | null;
}

export type ZoneKind = "data_hall" | "support" | "office";
export interface Zone {
  id: string;
  name: string;
  floor: number; // 0 = 1층
  kind: ZoneKind;
  geometry: { ew_m: number; ns_m: number; height_m: number; x_m?: number; z_m?: number };
  envelope: Envelope;
  it_design_kw?: number;
  indoor?: Indoor | null;
  floor_area_m2: number;
  lighting_w_m2?: number | null;
  people?: number;
  misc_kw?: number | null;
  outdoor_air_m3_h?: number;
  architecture?: Architecture | null;
  liquid_fraction?: number | null;
  racks?: RackGrid | null;
  crah_positions?: [number, number][] | null;
  partitions?: { zone: string; area_m2: number; u_value: number; kind?: "slab" | "wall" }[];
}
export interface ZoneResult {
  id: string; name: string; floor: number; kind: ZoneKind; architecture: Architecture;
  interzone_kw: number; daily_total: DailyTotal;
  it_kw: number[]; cooling_kw: number[]; cooling_power_kw: number[]; peak_cooling_kw: number;
}
export interface HallMetrics {
  id: string; name: string; floor: number; it_design_kw: number; max_inlet_c: number; mean_inlet_c: number;
  racks_over_limit: number; rack_count: number; crah_return_c: number; crah_fan_kw: number; shi: number;
}

// ---------- 출력 ----------
export interface Sun {
  altitude_deg: number;
  azimuth_deg: number; // 남=0, 서=+, 동=-
}
export interface SurfaceSolar {
  N: number;
  E: number;
  S: number;
  W: number;
  roof: number;
}
export interface LoadKw {
  it: number;
  ups_loss: number;
  lighting: number;
  people: number;
  wall_conduction: number;
  roof_conduction: number;
  window_conduction: number;
  window_solar: number;
  ventilation: number;
  crah_fan: number;
  total_cooling: number;
}
/** 한 계산 스텝 (step_min 간격). 이름은 백엔드 호환성 유지용 */
export interface HourResult {
  hour: number;
  minute: number;
  t_h: number; // hour + minute/60
  sun: Sun;
  surface_solar_w_m2: SurfaceSolar;
  weather: { dry_bulb_c: number; rh_pct: number; wet_bulb_c: number; ghi_w_m2: number };
  load_kw: LoadKw;
  chilled_water: { flow_m3_h: number; supply_c: number; return_c: number };
  chiller: { cop: number; power_kw: number };
  condenser_water: { heat_rejection_kw: number; flow_m3_h: number; supply_c: number; return_c: number };
  makeup_water_m3_h: { evaporation: number; drift: number; blowdown: number; total: number };
  plant: HourPlant;
}
export interface LoopState {
  load_kw: number;
  free_fraction: number;
  chiller_load_kw: number;
  mode: PlantMode;
  chiller_on?: boolean; reason?: ModeReason; supply_overshoot_k?: number;   // 현실성 R1
}
export interface HourPlant {
  mode: PlantMode;
  mode_reason?: ModeReason;
  tower_water_c: number;
  free_cooling_water_c: number;
  air_loop: LoopState;
  liquid_loop: (LoopState & {
    chiller_cop: number; tcs_supply_c: number; tcs_return_c: number;
    fws_supply_c: number; fws_return_c: number; fws_flow_m3_h: number;
  }) | null;
  power_kw: {
    chiller: number; crah_fan: number; chw_pump: number; cw_pump: number; liquid_pump: number;
    tower_fan: number; cooling_total: number; facility: number;
  };
  pue: number | null;
  wue_l_per_kwh: number | null;
}
export interface DailyTotal {
  cooling_kwh: number;
  chiller_kwh: number;
  chw_m3: number;
  cw_m3: number;
  makeup_m3: number;
  it_kwh: number;
  cooling_power_kwh: number;
  facility_kwh: number;
  pue: number | null;
  wue_l_per_kwh: number | null;
  free_cooling_hours: number;
  partial_free_cooling_hours: number;
}
export interface SimulationResult {
  site: string;
  date: string;
  step_min: number;
  architecture: Architecture;
  hourly: HourResult[];
  daily_total: DailyTotal;
  control?: { chiller_switches: Record<string, number>; notes: string[] };
  zones?: ZoneResult[] | null;
}
export interface ArchitectureRun {
  architecture: Architecture;
  daily_total: DailyTotal;
  hourly: { t_h: number; mode: PlantMode; pue: number | null; facility_kw: number; cooling_power_kw: number; chiller_kw: number }[];
}
export interface ArchitectureComparison {
  step_min: number;
  current: Architecture;
  architectures: ArchitectureRun[];
}

// ---------- 냉각 구조 (api/cooling.py, hvac/cooling_schemas.py) ----------
export interface RackResult {
  id?: string | null;
  row: number;
  col: number;
  x_m: number;
  z_m: number;
  kw: number;
  required_m3_s: number;
  supplied_m3_s: number;
  recirculation: number;
  inlet_c: number;
  outlet_c: number;
}
export interface AirflowMetrics {
  max_inlet_c: number;
  mean_inlet_c: number;
  racks_over_limit: number;
  rack_count: number;
  supply_ratio: number;
  bypass_fraction: number;
  shi: number;
  rhi: number;
  crah_return_c: number;
  crah_heat_kw: number;
  crah_fan_kw: number;
}
export interface AnalyzeResult {
  hour: number;
  it_kw: number;
  air_fraction: number;
  room: { ew_m: number; ns_m: number; rows: number; per_row: number };
  racks: RackResult[];
  crah: { x_m: number; z_m: number; airflow_m3_s: number }[];
  metrics: AirflowMetrics;
}
export interface CandidateParams {
  containment: Containment;
  crah_per_side: number;
  airflow_ratio: number;
  tile_balancing: number;
  supply_temp_c: number;
  chw_supply_c: number;
  chw_return_c: number;
  topology: Topology;
}
export interface PowerBreakdown {
  chiller: number;
  crah_fan: number;
  chw_pump: number;
  cw_pump: number;
  liquid: number;
  total: number;
}
export interface Candidate {
  params: CandidateParams;
  max_inlet_c: number;
  racks_over_limit: number;
  crah_return_c: number;
  shi: number;
  peak_kw: PowerBreakdown;
  cop_peak: number;
  daily_kwh: PowerBreakdown;
  chw_head_kpa: number;
  feasible: boolean;
  violations: string[];
}
export interface PlantHour {
  hour: number;
  it_kw: number;
  cooling_kw: number;
  cop: number;
  free_fraction: number;
  mode: PlantMode;
  chiller_kw: number;
  crah_fan_kw: number;
  chw_pump_kw: number;
  cw_pump_kw: number;
  liquid_kw: number;
  total_kw: number;
  chw_flow_m3_h: number;
  cw_flow_m3_h: number;
  max_inlet_c: number;
  racks_over_limit: number;
  crah_return_c: number;
}
export type PipeKind = "chw_supply" | "chw_return" | "cw_supply" | "cw_return" | "fws_supply" | "fws_return";
export interface PipeSegment {
  kind: PipeKind;
  points: [number, number, number][];
  dn: number;
  design_flow_m3_h: number;
  length_m: number;
}
export interface Box3 { pos: [number, number, number]; size: [number, number, number] }
// ---------- 주의·경고 → 해결 방법 (backend hvac/advice.py) ----------
export interface Fix {
  target: string; label: string; value: number | string | null; detail: string; effect: string | null; solves: boolean | null;
}
export interface Issue { level: "crit" | "warn" | "info"; code: string; title: string; cause: string; fixes: Fix[] }

export interface EvaluateResult {
  issues?: Issue[];
  warnings?: string[];
  peak_hour: number;
  airflow: AnalyzeResult;
  rack_inlet_c: number[][]; // [시각 0~23][랙]
  hourly: PlantHour[];
  summary: Candidate;
  piping: {
    topology: Topology;
    chw_head_kpa: number;
    chw_friction_kpa: number;
    chw_critical_m: number;
    chw_design_flow_m3_h: number;
    cw_design_flow_m3_h: number;
    segments: PipeSegment[];
  };
  geometry: { building: { ew_m: number; ns_m: number; h_m: number }; chiller: Box3; tower: Box3 };
  /** 설비 대수 산정 (cooling_layout.plant 의 *_unit_kw 가 있을 때) */
  units?: Partial<Record<"chiller" | "tower" | "cdu", UnitPlan>> | null;
  /** 다중 존: 3D·랙·배관 표시에 쓴 전산실 존 id, 전산실별 피크 지표 */
  zone?: string;
  zones?: HallMetrics[];
  /** 다중 존: 건물 좌표계 층·존·랙·CRAH·설비·배관 (건물 전체 3D) */
  building_view?: BuildingView | null;
}
// ---------- 건물 전체 보기 (backend hvac/building_view.py, 원점 = 건물 중심, x=동, y=위, z=남) ----------
export interface BFloor { floor: number; elevation_m: number; height_m: number }
export interface BZone {
  id: string; name: string; kind: ZoneKind; floor: number;
  x_m: number; z_m: number; ew_m: number; ns_m: number; height_m: number; elevation_m: number;
}
export interface BRack { id: string; row: number; col: number; x: number; y: number; z: number; kw: number; inlet_c: number }
export interface BHall {
  zone: string; floor: number; racks: BRack[];
  rack_inlet_c: number[][]; // [시각 0~23][랙]
  crah: { x: number; y: number; z: number; airflow: number }[];
}
export interface BUnit extends Box3 { standby: boolean }
export interface BCdu extends Box3 { floor: number; zone: string | null }
export type BPipePart = "chiller_link" | "yard_header" | "feed" | "riser" | "floor_branch" | "hall" | "tower_link"
  | "cw_header" | "fws_feed" | "fws_riser" | "fws_floor" | "cdu_link";
export interface BPipeSegment extends PipeSegment { part: BPipePart; zone: string | null; floor: number | null }
export interface BuildingView {
  floors: BFloor[];
  footprint: [number, number][];
  zones: BZone[];
  halls: BHall[];
  plant: {
    chiller: BUnit[]; tower: BUnit[]; cdu: BCdu[];
    source: Partial<Record<"chiller" | "tower" | "cdu", "cad" | "auto">>;
  };
  piping: {
    segments: BPipeSegment[];
    chw_head_kpa: number; chw_friction_kpa: number; chw_critical_m: number; critical_zone: string;
    riser_friction_kpa: number; cw_yard_dynamic_kpa: number;
    chw_design_flow_m3_h: number; cw_design_flow_m3_h: number; fws_design_flow_m3_h: number | null;
    inlets: { zone: string; floor: number; point: [number, number, number] }[];
    riser: { x_m: number; z_m: number; base_m: number; top_m: number };
  };
  notes: string[];
}
export interface UnitPlan {
  unit_kw: number; design_kw: number; duty: number; standby: number; total: number; redundancy: string;
  running: number[];
}
export interface SearchSpace {
  containment?: Containment[];
  crah_per_side?: number[];
  airflow_ratio?: number[];
  tile_balancing?: number[];
  chw_delta_t?: number[];
  topology?: Topology[];
  supply_temp_min_c?: number;
  supply_temp_max_c?: number;
  chw_supply_min_c?: number;
  top_n?: number;
}
export interface OptimizeResult {
  peak_hour: number;
  airflow_evaluations: number;
  evaluated: number;
  baseline: Candidate;
  candidates: Candidate[];
}

// ---------- AI (api/ai.py) ----------
export interface AiStatus {
  enabled: boolean;
  model: string | null;
  reason: string | null;
}
export interface ExplainResponse {
  text: string;
  model: string;
  facts: Record<string, unknown>;
  unverified_numbers: string[];
}
export interface AppliedChange {
  path: string;
  old: number;
  new: number;
  reason: string;
}
export interface ParseResponse {
  summary: string;
  unsupported: string[];
  changes: AppliedChange[];
  scenario: Scenario;
}

// ---------- 시나리오 저장 / BMS (api/store.py) ----------
export interface SavedInfo {
  name: string;
  saved_at: string;
  note: string | null;
}
export interface BmsPoint {
  hour: number;
  measured: number;
  calculated: number;
  error: number;
}
export interface BmsStats {
  n: number;
  mae: number;
  rmse: number;
  nmbe_pct: number | null;
  cv_rmse_pct: number | null;
  max_abs_error: number;
  max_abs_error_hour: number;
}
export interface BmsMetric {
  key: string;
  label: string;
  unit: string;
  points: BmsPoint[];
  stats: BmsStats | null;
}

// ---------- CAD·IT 가져오기 / 예측 (api/imports.py) ----------
export interface RoleOption { value: string; label: string }
export interface CadLayerInfo { name: string; entities: Record<string, number>; suggested_role: string; floor?: string | null }
export interface CadBlockInfo {
  name: string; count: number; layers: Record<string, number>; attribute_tags: string[]; suggested_role: string;
  floors?: Record<string, number>;   // 층 라벨(F2) → 개수
}
export interface CadInspect {
  cad_id: string;
  file_name: string;
  dxf_version: string;
  units: { insunits: number; scale_m: number; basis: string };
  extents_m: [number, number, number, number];
  layers: CadLayerInfo[];
  blocks: CadBlockInfo[];
  preview: { layers: Record<string, { pts: [number, number][]; closed: boolean }[]>; truncated: boolean };
  skipped: Record<string, number>;
  audit_errors: number;
  roles: RoleOption[];
  floors?: { floor: number; label: string; layers: number }[];   // 레이어 접두어로 읽은 층
}
export interface CadZoneOverride { name?: string; kind?: ZoneKind; it_design_kw?: number }
export interface CadOptions {
  north_deg: number; height_m: number; window_height_m: number; unit_scale_m?: number | null;
  // 다중 층 (Phase 18 연장)
  floor_heights_m?: Record<number, number> | null; rack_kw?: number | null;
  zone_overrides?: Record<string, CadZoneOverride> | null;
}
export interface CadEquip { x: number; z: number; w: number; d: number }
export interface CadPreview {
  footprint: [number, number][];
  walls: [number, number][][];
  windows: [number, number][][];
  racks: { id: string; x: number; z: number; row: number }[];
  rack_size: [number, number];
  crah: [number, number][];
  chiller: CadEquip | null;
  tower: CadEquip | null;
  zones?: { id: string; name: string; kind: ZoneKind; poly: [number, number][]; x: number; z: number }[];
  floors?: CadFloorPreview[];
}
export interface CadFloorPreview extends CadPreview { floor: number; label: string; height_m: number; outline: string }
export interface CadZoneReport {
  id: string; name: string; floor: number; floor_label: string; kind: ZoneKind; kind_source: string; boundary: string;
  area_m2: number; it_kw: number; it_source: string; racks: number; crah: number; ext_wall_m: Record<string, number>;
  walls: Record<string, number>; windows: Record<string, number>; roof_m2: number; inherited: boolean; notes: string[];
}
export interface CadReport {
  notes: string[];
  warnings: string[];
  counts: {
    racks: number; rows: number; crah: number; windows: number; zones?: number; floors?: number;
    cdu?: number; chillers?: number; towers?: number;
  };
  building: { ew_m: number; ns_m: number; height_m: number; floor_area_m2: number; rotation_deg: number };
  envelope: { walls: Record<string, number>; windows: Record<string, number>; roof_m2: number };
  // 다중 층
  floors?: { floor: number; label: string; height_m: number; outline: string; zones: string[] }[];
  zones?: CadZoneReport[];
  partitions?: { a: string; b: string; kind: "slab" | "wall"; area_m2: number; u_value: number; u_source: string }[];
  it_design_kw?: number; rack_kw?: number; rack_kw_source?: string;
}
export interface CadBuildResult { scenario: Scenario; report: CadReport; preview: CadPreview }

export type ItKind = "long" | "wide" | "total" | "inventory" | "unknown" | "ignore";
export interface ItColumn {
  name: string; n: number; num_frac: number; unit: string | null; samples: string[];
  min?: number; max?: number; median?: number; role: string; reason: string;
}
export interface ItTable {
  label: string;
  header_row: number;
  columns: string[];
  kind: ItKind;
  mapping: Record<string, string>;
  wide_columns: string[];
  unit: string | null;
  unit_basis: string;
  design_kw_unit: string | null;
  profiles: ItColumn[];
  warnings: string[];
  rows_n: number;
  encoding?: string | null;
}
export interface ItOverride { mapping?: Record<string, string>; kind?: ItKind; unit?: string }
export interface ItReport {
  notes: string[];
  warnings: string[];
  period: string[];
  day: string | null;
  racks: {
    data: number; matched: number; layout: number; unmatched_data: string[]; unmatched_data_n: number;
    unmatched_layout_n: number; grid_assigned: boolean;
  };
  design_kw: number | null;
  peak_kw: number | null;
  total_profile_kw: number[] | null;
}
export interface ItApplyResult { scenario: Scenario; report: ItReport; tables: ItTable[]; dataset?: ItDatasetSummary | null }


// ---------- Phase 9: 연간 시뮬레이션 ----------
export interface AnnualPeakDay {
  date: string; peak_cooling_kw: number; hour: number; multiplier: number; max_dry_bulb_c: number; max_wet_bulb_c: number;
}
export interface AnnualSums {
  it_kwh: number; cooling_kwh: number; cooling_power_kwh: number; chiller_kwh: number; crah_fan_kwh: number;
  pump_kwh: number; tower_fan_kwh: number; facility_kwh: number; makeup_m3: number;
  free_h: number; partial_h: number; chiller_h: number;
}
export interface AnnualAgg extends AnnualSums {
  pue: number | null; wue_l_per_kwh: number | null; peak_day: AnnualPeakDay; n_days: number;
}
export interface AnnualMonth extends AnnualAgg { month: number }
export interface AnnualDay extends AnnualSums {
  date: string; month: number; multiplier: number; pue: number | null;
  peak_cooling_kw: number; peak_hour: number; max_dry_bulb_c: number; max_wet_bulb_c: number;
}
export interface AnnualArch {
  architecture: Architecture; annual: AnnualAgg; monthly: AnnualMonth[]; daily: AnnualDay[];
  heatmap: { months: number[]; free: number[][]; partial: number[][] };
}
export interface AnnualCompareRow {
  architecture: Architecture; cooling_power_kwh: number; chiller_kwh: number; facility_kwh: number; pue: number | null;
  makeup_m3: number; wue_l_per_kwh: number | null; free_h: number; partial_h: number; chiller_h: number; peak_cooling_kw: number;
}
export interface AnnualResult {
  site: string; year: number | null; n_days: number; current: Architecture;
  base_multiplier: number; growth_pct_per_year: number; weather_meta: Record<string, unknown>; weather_file: string;
  comparison: AnnualCompareRow[]; architectures: AnnualArch[];
  peak_days: Record<string, { weather: Weather; multiplier: number; utilization: number[] }>; notes: string[]; cached: boolean;
  it_source: "scenario" | "dataset"; uses_schedule: boolean;
  coupled: { monthly_kwh: number[]; months: number[]; annual_kwh: number; uses_schedule: boolean } | null;
}
export interface AnnualWeatherFile { file: string; latitude: number; longitude: number; year: number; n_days: number; source: string }
export interface AnnualRequest {
  source?: "kma_asos" | "open_meteo"; station_id?: number | null; solar_station_id?: number | null; solar?: "auto" | "cloud";
  weather_file?: string | null; latitude?: number | null; longitude?: number | null; year?: number | null;
  base_multiplier: number; growth_pct_per_year: number;
  it_source?: "scenario" | "dataset"; coupled?: boolean;
}

// ---------- Phase 10: 출처·작업·상태 ----------
export type ProvSource = "assumed" | "user" | "cad" | "it_data" | "bms_calibrated" | "optimizer" | "schedule" | "weather_api"
  | "published" | "real_calibrated";
export interface Provenance {
  source: ProvSource; ref?: string | null; at?: string | null; previous?: unknown; absent_before?: boolean | null; note?: string | null;
}
export interface TrustItem { path: string; label: string; source: ProvSource }
export interface ScenarioStatus {
  guide: { cad: boolean; it: boolean; it_dataset: boolean; bms: boolean; improve: boolean; invest: boolean };
  trust: { measured: number; assumed: number; decided: number; measured_pct: number; items: TrustItem[] };
}
export interface Job<T = unknown> {
  id: string; kind: string; status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number; stage: string; result: T | null; error: string | null;
}

// ---------- Phase 11: IT 데이터셋 ----------
export interface ItDatasetSummary {
  id: string; period: [string, string]; n_days: number; months: number[]; coverage: number;
  weekend_ratio: number | null; daily_kwh: Record<string, number>;
  daytype_kw: Partial<Record<"weekday" | "sat" | "sun", number[]>>; sentence: string;
}

// ---------- Phase 12: BMS 보정 ----------
export interface BmsTable {
  label: string; header: string[]; n_rows: number; mapping: Record<string, string>;
  samples: Record<string, string[]>; metrics: string[]; time_ok: boolean; roles: RoleOption[];
}
export interface BmsStage { stage: string; label: string; possible: boolean; needs: string[] }
export interface BmsInspect { tables: BmsTable[]; stages: BmsStage[]; has_weather: boolean }
export interface BmsDataset {
  id: string; period: [string, string]; n_days: number; metrics: string[]; metric_labels: Record<string, string>;
  has_weather: boolean; notes: string[]; stages: BmsStage[];
}
export interface CalStat { n: number; rmse: number; cv_rmse_pct: number | null; nmbe_pct: number | null; pass: boolean | null }
export interface CalParam {
  path: string; label: string; stage: string; stage_label: string; before: number; after: number;
  bounds: [number, number]; at_bound: boolean; metrics: string[];
}
export interface CalResult {
  run_id: string; params: CalParam[]; skipped: { stage: string; label: string; reason: string }[];
  metrics: { metric: string; before: CalStat | null; after: CalStat | null }[];
  verdict: "pass" | "partial" | "fail"; short: boolean; n_days: number; train_days: number; valid_days: number;
  series: { metric: string; dates: string[]; measured: (number | null)[]; before: (number | null)[]; after: (number | null)[] } | null;
  notes: string[]; criteria: string;
}

// ---------- Phase 13: 연간 최적화 ----------
export interface ScheduleEntry { month: number; supply_temp_c: number; chw_supply_c: number; chw_return_c: number; airflow_ratio: number }
export interface Structure { containment: Containment; topology: Topology; crah_per_side: number }
export interface StructurePlan {
  structure: Structure; is_current: boolean; monthly_kwh: number; fixed_kwh: number | null;
  schedule: (ScheduleEntry & { kwh: number; inlet_margin_c: number })[]; monthly: number[];
}
export interface AnnualOptResult {
  months: number[]; current: { structure: Structure; annual_kwh: number; monthly: number[]; violations: number[] };
  structures: StructurePlan[]; best: StructurePlan; same_structure: StructurePlan | null;
  check: { max_rel_diff: number; ok: boolean }; summary: string; notes: string[]; year: number | null; n_days: number;
}

// ---------- Phase 14: 내일 운전 ----------
export interface Setpoint { supply_temp_c: number; chw_supply_c: number; chw_return_c: number; airflow_ratio: number }
interface OutlookBase {
  date: string; weekday: string; title: string; bullets: string[];
  alerts: { level: "info" | "warn" | "crit"; text: string }[]; issues?: Issue[]; notes: string[];
  weather_source: string; weather_note: string | null; current_label: string;
  saving_kwh: number; peak_cooling_kw: number; makeup_m3: number; weather: Weather; utilization: number[];
  power_note?: string | null;
}
type SetpointResult<K extends string> = Setpoint & Record<K, number> & { max_inlet_c: number; inlet_margin_c: number };
/** 내일 브리핑 (기상청 단기예보) */
export interface OutlookDay extends OutlookBase {
  period: "day";
  current: SetpointResult<"daily_kwh">; recommended: SetpointResult<"daily_kwh"> | null;
  hourly: { hour: number; dry_bulb_c: number; wet_bulb_c: number; rh_pct: number; it_kw: number; cooling_kw: number;
    current_kw: number; recommended_kw: number; mode: PlantMode; current_mode: PlantMode;
    current_chiller_kw: number; recommended_chiller_kw: number; current_fan_kw: number; recommended_fan_kw: number }[];
  precip?: { pop_pct: (number | null)[]; pty: (string | null)[]; sky: (string | null)[]; pcp: (string | null)[] } | null;
}
/** 다음 달 브리핑 (지역 관측소의 지난 연도 같은 달 관측) */
export interface OutlookMonth extends OutlookBase {
  period: "month"; year: number; month: number; observed_year: number;
  current: SetpointResult<"monthly_kwh">; recommended: SetpointResult<"monthly_kwh"> | null;
  daily: { date: string; obs_date: string; max_dry_bulb_c: number; min_dry_bulb_c: number; max_wet_bulb_c: number;
    peak_cooling_kw: number; current_kwh: number; recommended_kwh: number; current_free_h: number; current_partial_h: number;
    free_h: number; partial_h: number }[];
}
export type Outlook = OutlookDay | OutlookMonth;

// ---------- Phase 15: 랙 혼합·배치 ----------
export interface NewRacks { count: number; design_kw: number; liquid_fraction: number; label?: string }
export interface PlacedRack extends RackItem { inlet_c: number | null }
export interface PlacementResult {
  items: PlacedRack[]; before: { max_inlet_c: number; racks_over_limit: number; mean_inlet_c: number };
  after: { max_inlet_c: number; racks_over_limit: number; mean_inlet_c: number }; summary: string; evaluations: number;
  issue?: Issue | null; air?: Record<string, { required_m3_s: number; supplied_m3_s: number; recirculation: number }>;
  slots_considered: number; added_design_kw: number; unit_airflow_m3_s: number; notes: string[];
}

// ---------- Phase 16: 투자 검토 ----------
export interface Measure { id: string; enabled?: boolean; capex_krw?: number | null; om_krw_per_year?: number; life_years?: number }
export interface Economics {
  electricity_krw_per_kwh: number; water_krw_per_m3: number; co2_kg_per_kwh: number; discount_rate: number;
  years: number; energy_escalation: number; it_growth_pct_per_year: number; measures: Measure[];
}
export interface CatalogItem {
  id: string; label: string; description: string; unit: string; unit_cost_krw: number; quantity: number;
  example_capex_krw: number; applicable: boolean;
}
export interface InvestRow {
  key: string; ids: string[]; label: string; package: boolean; capex_krw: number; demo_capex: boolean;
  annual_kwh_year1: number; npv_krw: number; irr: number | null; payback_years: number | null;
  discounted_payback_years: number | null; year1_saving_kwh: number; year1_saving_krw: number; year1_co2_t: number;
  cumulative_krw: number[]; sum_of_singles_kwh?: number;
}
export interface InvestResult {
  baseline: { annual_kwh: number; water_m3: number; cost_krw: number }; results: InvestRow[];
  sensitivity: { measure: string; base_npv_krw: number;
    items: { factor: string; low: string; high: string; npv_low_krw: number; npv_high_krw: number }[] };
  notes: string[]; demo: boolean; summary: string;
}

// ---------- Phase 18: 자동 튜닝 ----------
export type TuneKey = "tile_balancing" | "airflow_ratio" | "supply_temp_c" | "chw_supply_c" | "chw_delta_t" | "containment" | "crah_per_side";
export interface TuneSetting {
  key: TuneKey; enabled: boolean; min?: number | null; max?: number | null; step?: number | null;
  choices?: Containment[] | null;
  label?: string; unit?: string; help?: string; construction?: boolean;   // 화면 표시용 (요청에서는 무시)
}
export interface TuneParams {
  containment: Containment; crah_per_side: number; airflow_ratio: number; tile_balancing: number;
  supply_temp_c: number; chw_supply_c: number; chw_return_c: number; topology: Topology;
}
export interface TuneState {
  max_inlet_c: number; inlet_margin_c: number; racks_over_limit: number; daily_kwh: number;
  problems: string[]; issues: { level: string; title: string }[];
}
export interface TuneResult {
  status: "ok" | "solved" | "unsolved"; message: string; method: string; peak_hour: number; margin_c: number;
  objective: "min_change" | "min_energy"; before: TuneState; after?: TuneState; stage?: number;
  stages: { params: TuneKey[]; feasible: number }[]; evaluations?: number;
  params?: TuneParams; saving_kwh?: number;
  changes?: { key: TuneKey; label: string; unit: string; from: number | string; to: number | string; construction: boolean }[];
  alternatives?: { params: TuneParams; kwh: number; changed: string[] }[];
}
export interface TuneInfluence {
  base_kwh: number;
  problems: ("inlet" | "coil_supply" | "coil_return")[];
  items: { key: TuneKey; label: string; step?: string; note?: string; inlet_margin_k?: number; return_slack_k?: number;
    supply_slack_k?: number; kwh?: number }[];
}

// ---------- Phase 19: 실측 비교 (/real/*) ----------
export type RunKey = "A" | "B" | "C";
export interface RealMapping {
  timestamp?: string | null; it: string; items: Record<string, string[]>; labels: Record<string, string>;
  definitions?: Record<string, string>; lighting?: string | null; pue_column?: string | null; pue_terms: string[];
  ups_loss: number; dry_bulb?: string | null; rh?: string | null; temp_unit?: "C" | "F";
}
export interface RealPeriods { cal: [string, string]; test?: [string, string] | null }
export interface RealPresetInfo {
  id: string; label: string; description: string; periods: RealPeriods; available: boolean; sources: { label: string }[];
}
export interface RealColumn { name: string; role: string | null; missing_pct: number; min?: number; max?: number; mean?: number }
export interface RealInspect {
  timestamp: string; range: [string, string]; hours: number; rows: number; bad_rows: number; columns: RealColumn[];
  mapping: RealMapping; dataset_id?: string; name?: string;
}
export interface RealPresetDetail extends RealInspect {
  id: string; label: string; description: string; periods: RealPeriods; period_notes: Record<string, string>;
  weather: { epw_label: string | null; site_label: string | null; site_available: boolean };
}
export interface PairCorr { measured: number | null; sim: number | null }
export interface RealItemMetric {
  label: string; sim_keys: string[]; measured_mean_kw: number; sim_mean_kw: number; ratio_sim_over_meas: number | null;
  nmbe_pct: number | null; cv_rmse_pct: number | null; g14_pass: boolean | null; corr_hourly: number | null;
  corr_with_it: PairCorr; corr_with_wet_bulb: PairCorr;
}
export interface RealRun {
  label: string; period: [string, string]; weather_days: Record<string, number>; coefficients?: Record<string, number>;
  hours: number; it_mean_kw: number; pue: { measured: number; sim: number; diff: number };
  chiller_hours: { sim: number; measured: number | null }; chiller_mean_kw: number;
  items: Record<string, RealItemMetric>; wue_l_per_kwh: { sim: number; measured: number | null };
}
export type RealRow = Record<string, number | string>;
export interface RealHourly {
  start: string | null; offset_h: number[]; it_kw: number[]; sim_it_kw: number[]; wet_bulb_c: number[];
  meas: Record<string, number[]>; sim: Record<string, number[]>;
}
export interface CalStep {
  step: number; title: string; bullets?: string[]; rows?: Record<string, string | number | boolean | null>[]; text?: string;
  pue?: Record<string, number>; g14_pass?: boolean;
}
export interface RealInputRow {
  path: string | null; label: string; value: unknown; basis: "fact" | "assumed" | "user"; ref: string | null;
  note: string | null; editable: boolean;
}
export interface G14Pair { nmbe_pct: number | null; cv_rmse_pct: number | null }
export interface StructureRow {
  label: string; coef: { fixed_kw: number; per_it_kw: number; per_wb_k_above0: number; per_k_below0_dry: number };
  fixed_share_of_mean_cal: number; engine_calibrated_test?: G14Pair; affine_test?: G14Pair;
}
export interface Sensitivity {
  source: string; hours_total: number; loop: string; elevation_m: number | null; note: string;
  rows: { label: string; wet_bulb: "sea" | "site"; tower_k: number; hx_k: number; cdu_k: number; hours: number }[];
  max_wet_bulb_sea_c: number | null; max_wet_bulb_alt_c?: number | null; site_pressure_kpa?: number;
  wet_bulb_bias_when_t_gt_20c_mean_k?: number | null;
}
export interface RealCompare {
  compare_id: string; created: string; cached: boolean; preset: string | null; label: string;
  dataset: { id: string | null; name: string; hash: string; range: [string, string] };
  mapping: RealMapping; periods: RealPeriods; period_notes: Record<string, string>; inputs: RealInputRow[];
  facility: { chiller_count?: number; chiller_note?: string } | null; reference: Record<string, unknown> | null;
  sources: { label: string }[];
  weather: { epw: string | null; site: boolean; site_columns: string[] | null; label: string | null;
    days: Partial<Record<RunKey, Record<string, number>>> };
  pue_check: { hours: number; median_abs_diff: number | null; ok: boolean; warning: string | null } | null;
  sign_note: string; g14: { nmbe_pct: number; cv_rmse_pct: number };
  runs: Partial<Record<RunKey, RealRun>> & { A: RealRun; B: RealRun };
  monthly: Partial<Record<RunKey, RealRow[]>>; daily: Partial<Record<RunKey, RealRow[]>>;
  hourly: Partial<Record<RunKey, RealHourly>>;
  calibration: { coefficients: Record<string, number>; ratios: Record<string, number>; steps: CalStep[] };
  structure: Record<string, StructureRow>; sensitivity: Sensitivity; elapsed_s: number;
}
export interface RealCause {
  rank: number; title: string; affected: string[]; direction: string; evidence: string; basis: "data" | "hypothesis";
  how_to_verify: string; model_fix: string;
}
export interface RealDiagnosis {
  compare_id: string; model: string; generated_at: string; unverified_numbers: string[]; summary: string;
  causes: RealCause[]; not_explained: string; cached: boolean;
}
export interface RealCompareRequest {
  preset?: string | null; dataset_id?: string | null; mapping?: RealMapping | null; periods?: RealPeriods | null;
  scenario?: Scenario | null; scenario_overrides?: Record<string, unknown>;
}
