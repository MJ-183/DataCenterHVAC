import type {
  AnnualRequest, AnnualResult, BmsDataset, BmsInspect, CadBuildResult, CadInspect, CadOptions,
  CandidateParams, CatalogItem, CoolingLayout, Economics, EvaluateResult, ItApplyResult, ItOverride,
  ItTable, Job, OptimizeResult, Outlook, RackItem, RealCompare, RealCompareRequest, RealDiagnosis, RealInspect, RealPresetDetail,
  RealPresetInfo, RoleOption, SavedInfo, Scenario, ScenarioStatus, TuneInfluence, TuneParams, TuneSetting,
  ScheduleEntry, SearchSpace, Setpoint, SimulationResult, Structure,
} from "../types/api";

export interface KmaKeyStatus {
  configured: boolean; masked: string | null; services: { service: string; name: string }[];
  tests: { service: string; name: string; ok: boolean; message: string }[] | null;
}

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
/** 화면용 계산 간격 [분]. 정시 사이 기상·가동률은 백엔드에서 선형 보간(가정) */
export const STEP_MIN = 5;

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail?: unknown) {
    super(message);
  }
}

/** FastAPI 오류 detail(문자열 또는 검증 오류 배열)을 사람이 읽을 문장으로 */
function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: (string | number)[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? ""}`)
      .join("\n");
  }
  return "알 수 없는 오류";
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(`백엔드(${API_BASE})에 연결할 수 없습니다. uvicorn 서버가 실행 중인지 확인하세요.`, 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(describe(body?.detail ?? res.statusText), res.status, body?.detail);
  return body as T;
}

const post = <T>(path: string, data: unknown) => req<T>(path, { method: "POST", body: JSON.stringify(data) });
const enc = encodeURIComponent;

/** 업로드 파일 → base64 (DXF·CSV·XLSX 를 JSON 본문으로 보냄) */
export async function fileB64(f: File): Promise<string> {
  const buf = new Uint8Array(await f.arrayBuffer());
  let s = "";
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}
export interface UploadFile { name: string; data_b64: string }

/** 데이터 입력의 지역 선택 → 사이트 위치·관측소·연도 (연간·연간 최적화·투자·브리핑이 모두 따름) */
export interface RegionPick {
  station_id: number; solar_station_id: number | null; solar: "auto" | "cloud"; year: number;
  design_day: "hottest_wet_bulb" | "hottest_dry_bulb" | "typical_wet_bulb" | "keep";
}
export interface AsosStation { id: number; name: string; office: string; latitude: number; longitude: number; altitude_m: number }

export const api = {
  health: () => req<{ status: string }>("/health"),
  sample: () => req<Scenario>("/scenario/sample"),
  presets: () => req<{ id: string; label: string }[]>("/scenario/presets"),
  preset: (id: string) => req<Scenario>(`/scenario/preset/${enc(id)}`),
  simulate: (sc: Scenario, stepMin = STEP_MIN) => post<SimulationResult>(`/simulate?step_min=${stepMin}`, sc),

  listScenarios: () => req<SavedInfo[]>("/scenarios"),
  getScenario: (name: string) => req<Scenario>(`/scenarios/${enc(name)}`),
  saveScenario: (name: string, sc: Scenario) =>
    req<SavedInfo>(`/scenarios/${enc(name)}`, { method: "PUT", body: JSON.stringify(sc) }),

  coolingDefault: () => req<CoolingLayout>("/cooling/layout/default"),
  coolingEvaluate: (sc: Scenario, zone?: string | null) =>
    post<EvaluateResult>(`/cooling/evaluate${zone ? `?zone=${enc(zone)}` : ""}`, sc),
  coolingOptimize: (sc: Scenario, space: SearchSpace) => post<OptimizeResult>("/cooling/optimize", { scenario: sc, space }),
  coolingApply: (sc: Scenario, params: CandidateParams) => post<Scenario>("/cooling/apply", { scenario: sc, params }),

  cadInspect: (file_name: string, data_b64: string) => post<CadInspect>("/cad/inspect", { file_name, data_b64 }),
  cadBuild: (cad_id: string, roles: Record<string, string>, block_roles: Record<string, string>, options: CadOptions,
    scenario: Scenario) => post<CadBuildResult>("/cad/build", { cad_id, roles, block_roles, options, scenario }),
  itInspect: (files: UploadFile[], overrides: Record<string, ItOverride>) =>
    post<{ tables: ItTable[]; roles: RoleOption[] }>("/itload/inspect", { files, overrides }),
  itApply: (files: UploadFile[], overrides: Record<string, ItOverride>, day: string, scenario: Scenario, save_dataset = true) =>
    post<ItApplyResult>("/itload/apply", { files, overrides, day, scenario, save_dataset }),

  annualStations: () => req<{ key_configured: boolean; stations: AsosStation[] }>("/annual/stations"),
  annualRun: (scenario: Scenario, opts: AnnualRequest) => post<AnnualResult>("/annual/run", { scenario, ...opts }),

  // Phase 10
  status: (sc: Scenario) => post<ScenarioStatus>("/scenario/status", sc),
  revert: (scenario: Scenario, paths: string[]) => post<Scenario>("/scenario/revert", { scenario, paths }),
  startJob: (kind: string, body: unknown) => post<{ job_id: string }>(`/jobs/${kind}`, body),
  autotuneDefaults: (sc: Scenario) => post<{ settings: TuneSetting[]; margin_c: number; objective: string }>("/autotune/defaults", sc),
  autotuneInfluence: (body: unknown) => post<TuneInfluence>("/autotune/influence", body),
  autotuneApply: (sc: Scenario, params: TuneParams) => post<Scenario>("/autotune/apply", { scenario: sc, params }),
  job: <T>(id: string) => req<Job<T>>(`/jobs/${id}`),
  cancelJob: (id: string) => req<{ ok: boolean }>(`/jobs/${id}`, { method: "DELETE" }),

  // Phase 12
  bmsInspect: (files: UploadFile[], overrides: Record<string, Record<string, string>>) =>
    post<BmsInspect>("/bms/inspect", { files, overrides }),
  bmsDataset: (files: UploadFile[], overrides: Record<string, Record<string, string>>) =>
    post<BmsDataset>("/bms/dataset", { files, overrides }),
  calibrationApply: (scenario: Scenario, run_id: string, paths?: string[]) =>
    post<Scenario>("/calibration/apply", { scenario, run_id, paths }),

  // Phase 13
  scheduleApply: (scenario: Scenario, structure: Structure, schedule: ScheduleEntry[]) =>
    post<Scenario>("/annual/schedule/apply", { scenario, structure, schedule }),

  // Phase 14
  outlook: (scenario: Scenario, period: "day" | "month", capacity_kw: number | null) =>
    post<Outlook>("/outlook", { scenario, period, capacity_kw }),
  outlookApply: (scenario: Scenario, setpoint: Setpoint, month?: number) =>
    post<Scenario>("/outlook/apply", { scenario, setpoint, month }),

  // Phase 15
  racksApply: (scenario: Scenario, body: { rows?: Record<number, number | null>; add_items?: RackItem[];
    unit_airflow_m3_s?: number }) => post<Scenario>("/cooling/racks/apply", { scenario, ...body }),

  // Phase 16
  investCatalog: (scenario: Scenario) =>
    post<{ measures: CatalogItem[]; economics: Economics; economics_is_default: boolean; note: string }>(
      "/invest/catalog", { scenario }),

  // Phase 19 실측 비교
  realPresets: () => req<RealPresetInfo[]>("/real/presets"),
  realPreset: (id: string) => req<RealPresetDetail>(`/real/preset/${enc(id)}`),
  realInspect: (file_name: string, data_b64: string) => post<RealInspect>("/real/inspect", { file_name, data_b64 }),
  realCompare: (body: RealCompareRequest) => post<RealCompare>("/real/compare", body),
  realDiagnose: (compare_id: string) => post<RealDiagnosis>("/real/diagnose", { compare_id }),
  realApply: (scenario: Scenario, compare_id: string) => post<Scenario>("/real/apply", { scenario, compare_id }),

  // 기상청 인증키 (backend/.env 의 KMA_SERVICE_KEY 에 저장, 키는 다시 내려오지 않음 — 끝 4자리만)
  kmaKey: () => req<KmaKeyStatus>("/weather/kma-key"),
  kmaKeySave: (key: string) => req<KmaKeyStatus>("/weather/kma-key", { method: "PUT", body: JSON.stringify({ key }) }),
  kmaKeyTest: () => post<KmaKeyStatus>("/weather/kma-key/test", {}),
  kmaKeyClear: () => req<KmaKeyStatus>("/weather/kma-key", { method: "DELETE" }),
  region: (scenario: Scenario, pick: RegionPick) => post<Scenario>("/weather/region", { scenario, ...pick }),

  // AI 요약 (backend/.env 의 OPENAI_API_KEY·OPENAI_MODEL, 키는 프론트로 오지 않음)
  aiStatus: () => req<{ enabled: boolean; model: string | null; reason: string | null }>("/ai/status"),
  explain: (result: SimulationResult) =>
    post<{ text: string; model: string; unverified_numbers: string[] }>("/explain", result),
};
