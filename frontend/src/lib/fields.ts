import type { Scenario } from "../types/api";

/** 입력 패널에 노출하는 주요 필드. assumed=true 는 근거 자료 없이 둔 "가정값" */
export interface FieldDef { path: string; label: string; unit: string; step: number; assumed?: boolean }

export const MAIN_FIELDS: FieldDef[] = [
  { path: "it_load.design_kw", label: "IT 설계부하", unit: "kW", step: 50 },
  { path: "cooling_plant.chw_supply_c", label: "냉수 공급온도", unit: "℃", step: 0.5 },
  { path: "cooling_plant.chw_return_c", label: "냉수 환수온도", unit: "℃", step: 0.5 },
  { path: "cooling_plant.cw_supply_c", label: "냉각수 공급온도", unit: "℃", step: 0.5 },
  { path: "cooling_plant.cw_return_c", label: "냉각수 환수온도", unit: "℃", step: 0.5 },
  { path: "cooling_plant.tower_cycles_of_concentration", label: "냉각탑 농축배수 COC", unit: "-", step: 0.5 },
  { path: "cooling_plant.chiller_cop_rated", label: "칠러 정격 COP", unit: "-", step: 0.1, assumed: true },
  { path: "cooling_plant.design_wet_bulb_c", label: "COP 기준 습구온도", unit: "℃", step: 0.5, assumed: true },
  { path: "cooling_plant.cop_wb_sensitivity", label: "습구 1℃당 COP 보정률", unit: "1/K", step: 0.005, assumed: true },
  { path: "cooling_plant.crah_fan_heat_ratio", label: "CRAH 팬 발열 비율", unit: "-", step: 0.01, assumed: true },
  { path: "cooling_plant.tower_drift_ratio", label: "비산 손실 비율", unit: "-", step: 0.0001, assumed: true },
  { path: "internal.ups_loss_ratio", label: "UPS 손실 비율", unit: "-", step: 0.01, assumed: true },
  { path: "indoor.temp_c", label: "실내 설정온도", unit: "℃", step: 0.5 },
  { path: "indoor.rh_pct", label: "실내 설정 상대습도", unit: "%", step: 5 },
  { path: "ventilation.outdoor_air_m3_h", label: "외기 도입량", unit: "m³/h", step: 500 },
  { path: "internal.lighting_w_m2", label: "조명 밀도", unit: "W/m²", step: 1 },
];

/** 냉각 방식(Phase 3.5) 계수. 전부 근거 자료 없이 둔 가정값 (backend engine.PLANT_DEFAULTS) */
export const PLANT_FIELDS: FieldDef[] = [
  { path: "cooling_plant.liquid_fraction", label: "DLC 액체 회수율 (IT 발열 대비)", unit: "-", step: 0.05, assumed: true },
  { path: "cooling_plant.immersion_capture", label: "액침 액체 회수율 (IT 발열 대비)", unit: "-", step: 0.01, assumed: true },
  { path: "cooling_plant.tcs_supply_c", label: "TCS 공급온도", unit: "℃", step: 0.5, assumed: true },
  { path: "cooling_plant.tcs_return_c", label: "TCS 환수온도", unit: "℃", step: 0.5, assumed: true },
  { path: "cooling_plant.cdu_approach_k", label: "CDU 접근온도", unit: "K", step: 0.5, assumed: true },
  { path: "cooling_plant.tower_approach_k", label: "냉각탑 접근온도 (출구 - 습구)", unit: "K", step: 0.5, assumed: true },
  { path: "cooling_plant.hx_approach_k", label: "프리쿨링 열교환기 접근온도", unit: "K", step: 0.5, assumed: true },
  { path: "cooling_plant.cop_supply_sensitivity", label: "공급수온 1℃당 COP 보정률", unit: "1/K", step: 0.005, assumed: true },
  { path: "cooling_plant.chw_pump_kw_per_kw", label: "냉수 펌프 계수", unit: "kW/kW", step: 0.001, assumed: true },
  { path: "cooling_plant.cw_pump_kw_per_kw", label: "냉각수 펌프 계수", unit: "kW/kW", step: 0.001, assumed: true },
  { path: "cooling_plant.liquid_pump_kw_per_kw", label: "TCS·FWS 펌프 계수", unit: "kW/kW", step: 0.001, assumed: true },
  { path: "cooling_plant.tower_fan_kw_per_kw", label: "냉각탑 팬 계수", unit: "kW/kW", step: 0.001, assumed: true },
];

export const ARCH_LABEL: Record<string, string> = {
  air_chw: "공랭 (CRAH + 칠러)", hybrid_dlc: "하이브리드 DLC (냉각판 + 공랭)", immersion: "액침 냉각",
};
export const MODE_LABEL: Record<string, string> = { chiller: "칠러", partial: "부분 프리쿨링", free: "프리쿨링 (칠러 OFF)" };

const EXTRA_LABELS: Record<string, string> = {
  "it_load.utilization": "IT 가동률 배율",
  "weather.dry_bulb_c": "외기 건구온도",
  "weather.rh_pct": "외기 상대습도",
  "weather.ghi_w_m2": "수평면 일사량",
  "envelope.roof.u_value": "지붕 열관류율",
  "envelope.roof.area_m2": "지붕 면적",
  "envelope.roof.absorptance": "지붕 일사흡수율",
  "internal.people": "재실 인원",
  "internal.floor_area_m2": "바닥 면적",
  "site.latitude": "위도",
};
const LIST_LABELS: Record<string, string> = { walls: "외벽", windows: "창" };
const LEAF_LABELS: Record<string, string> = {
  u_value: "열관류율", area_m2: "면적", absorptance: "일사흡수율", shgc: "SHGC",
};

/** 'envelope.walls.2.u_value' -> '외벽[S] 열관류율', 'it_load.utilization.14' -> 'IT 가동률 배율 14시' */
export function labelFor(path: string, sc?: Scenario): string {
  const main = [...MAIN_FIELDS, ...PLANT_FIELDS].find((f) => f.path === path)?.label ?? EXTRA_LABELS[path];
  if (main) return main;
  const parts = path.split(".");
  const last = parts[parts.length - 1];
  if (/^\d+$/.test(last)) {
    const arr = parts.slice(0, -1).join(".");
    const base = MAIN_FIELDS.find((f) => f.path === arr)?.label ?? EXTRA_LABELS[arr];
    if (base) return `${base} ${last}시`;
  }
  if (parts[0] === "envelope" && LIST_LABELS[parts[1]] && /^\d+$/.test(parts[2])) {
    const item = (sc?.envelope as unknown as Record<string, { orientation: string }[]>)?.[parts[1]]?.[+parts[2]];
    return `${LIST_LABELS[parts[1]]}[${item?.orientation ?? parts[2]}] ${LEAF_LABELS[parts[3]] ?? parts[3]}`;
  }
  return path;
}

export function getPath(obj: unknown, path: string): number {
  return path.split(".").reduce<unknown>((o, k) => (o as Record<string, unknown>)?.[k], obj) as number;
}

export function setPath<T>(obj: T, path: string, value: number): T {
  const copy = structuredClone(obj);
  const keys = path.split(".");
  let o = copy as Record<string, unknown>;
  for (const k of keys.slice(0, -1)) o = o[k] as Record<string, unknown>;
  o[keys[keys.length - 1]] = value;
  return copy;
}

/** 다중 존(Phase 18) 전산실 수 — 단일 존이면 1 */
export const hallCount = (sc: Scenario) => (sc.zones ?? []).filter((z) => z.kind === "data_hall").length || 1;
/** CRAH 대수 문구 (운전점은 전산실 공통이라 다중 존이면 전산실마다 같은 대수) */
export const crahText = (sc: Scenario, perSide: number) =>
  hallCount(sc) > 1 ? `CRAH 전산실 ${hallCount(sc)}곳 × ${perSide * 2}대` : `CRAH ${perSide * 2}대`;
