import defaultLayout from "../../data/layout.default.json";
import type { Orientation, Scenario } from "../../types/api";

/**
 * 3D 표시 설정.
 * - 랙·CRAH·칠러·냉각탑·배관 좌표는 백엔드 /cooling/evaluate 결과(geometry, airflow, piping)를 그대로 쓴다.
 * - model.url 에 GLB 경로를 넣으면 절차적 건물 외피 대신 GLB 를 로드하고,
 *   model.bindings 의 메시 이름 접두어 → 역할 매핑으로 히트맵/툴팁을 연결한다.
 *   예) { "url": "/models/dc.glb", "bindings": { "Wall_South": "wall:S", "Rack_": "racks", "Roof": "roof" } }
 */
export type Role = `wall:${Orientation}` | "roof" | "racks" | "crah" | "chiller" | "tower";

export interface Layout {
  _note: string;
  model: { url: string; bindings: Record<string, Role>; scale?: number } | null;
  building_fallback: { length_ew_m: number; length_ns_m: number; height_m: number };
  visual: { rack_height_m: number; crah_size_m: [number, number, number] };
}

export const LAYOUT = defaultLayout as Layout;

export interface Dims { ew: number; ns: number; h: number; derived: boolean }

/** 외피 면적에서 건물 치수 역산: A_NS = L_ew·H, A_EW = L_ns·H, A_roof = L_ew·L_ns (백엔드 airflow.building_dims 와 동일) */
export function buildingDims(sc: Scenario, layout: Layout = LAYOUT): Dims {
  if (sc.building) return { ew: sc.building.ew_m, ns: sc.building.ns_m, h: sc.building.height_m, derived: true };
  const area = (o: Orientation) =>
    sc.envelope.walls.filter((w) => w.orientation === o).reduce((s, w) => s + w.area_m2, 0);
  const ans = (area("N") + area("S")) / 2;
  const aew = (area("E") + area("W")) / 2;
  const roof = sc.envelope.roof.area_m2 || sc.internal.floor_area_m2;
  const fb = layout.building_fallback;
  if (ans > 0 && aew > 0 && roof > 0) {
    const h = Math.sqrt((ans * aew) / roof);
    if (h >= 3 && h <= 60) return { ew: ans / h, ns: aew / h, h, derived: true };
  }
  return { ew: fb.length_ew_m, ns: fb.length_ns_m, h: fb.height_m, derived: false };
}
