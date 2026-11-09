import type { AnalyzeResult, CoolingLayout } from "../../types/api";

/**
 * 랙 열·통로 기하 (백엔드 hvac/airflow.py 의 랙 좌표를 그대로 사용).
 * 열 j 는 z 오름차순. 짝수 열은 전면이 +z, 홀수 열은 -z 를 향해
 * (2k, 2k+1) 사이가 콜드아일, (2k+1, 2k+2) 사이가 핫아일이 된다 (표시용 규칙).
 */
export interface RowGeom { z: number; face: 1 | -1; x0: number; x1: number }
export interface AisleGeom { z: number; x0: number; x1: number; kind: "cold" | "hot" }

export function roomGeom(af: AnalyzeResult, layout: CoolingLayout) {
  const g = layout.racks;
  const pitch = g.depth_m + g.aisle_m;
  const rows: RowGeom[] = [];
  for (const r of af.racks) {
    const row = rows[r.row] ?? (rows[r.row] = { z: r.z_m, face: r.row % 2 === 0 ? 1 : -1, x0: Infinity, x1: -Infinity });
    row.x0 = Math.min(row.x0, r.x_m - g.width_m / 2);
    row.x1 = Math.max(row.x1, r.x_m + g.width_m / 2);
  }
  const aisles: AisleGeom[] = [];
  for (let j = 0; j < rows.length - 1; j++) {
    aisles.push({ z: (rows[j].z + rows[j + 1].z) / 2, x0: Math.min(rows[j].x0, rows[j + 1].x0),
      x1: Math.max(rows[j].x1, rows[j + 1].x1), kind: j % 2 === 0 ? "cold" : "hot" });
  }
  return { rows, aisles, pitch };
}
