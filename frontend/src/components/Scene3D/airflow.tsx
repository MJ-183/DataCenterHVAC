import { useEffect, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import { BufferAttribute, BufferGeometry, Color } from "three";
import type { AnalyzeResult, CoolingLayout } from "../../types/api";
import { airColor } from "./colors";
import { roomGeom } from "./roomGeom";
import type { Vec3 } from "./parts";

/**
 * 기류 입자 (표시용 개념도, CFD 아님).
 * 주 경로: CRAH 하부 → 이중마루 → 콜드아일 유공타일 → 랙 전면(흡입온도) → 랙 후면(배기온도) → 핫아일 → 천장 → CRAH 상부(환기온도)
 * 재순환 경로: 핫아일 → 랙 상부 → 콜드아일 → 랙 전면. 입자 수 ∝ 랙 재순환 비율
 * 입자 색 = 각 지점 공기온도 (기류 모델 계산값), 속도 ∝ CRAH 풍량 배율
 */
interface Path { pts: Vec3[]; temps: number[]; cum: number[]; len: number }

const SAMPLE_COLS = 8;
const PER_PATH = 7;
const BASE_SPEED = 3.0; // m/s (표시용)

function mkPath(pts: Vec3[], temps: number[]): Path {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    const [a, b] = [pts[i - 1], pts[i]];
    cum.push(cum[i - 1] + Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]));
  }
  return { pts, temps, cum, len: cum[cum.length - 1] };
}

function at(p: Path, s: number, out: Float32Array, o: number): number {
  let i = 1;
  while (i < p.cum.length - 1 && p.cum[i] < s) i++;
  const seg = p.cum[i] - p.cum[i - 1] || 1;
  const f = Math.min(Math.max((s - p.cum[i - 1]) / seg, 0), 1);
  const [a, b] = [p.pts[i - 1], p.pts[i]];
  out[o] = a[0] + (b[0] - a[0]) * f;
  out[o + 1] = a[1] + (b[1] - a[1]) * f;
  out[o + 2] = a[2] + (b[2] - a[2]) * f;
  return p.temps[i - 1] + (p.temps[i] - p.temps[i - 1]) * f;
}

export function AirflowParticles({ af, layout, inlets, rackH, crahH, ceilingH }: {
  af: AnalyzeResult; layout: CoolingLayout; inlets: number[]; rackH: number; crahH: number; ceilingH: number;
}) {
  const { rows, pitch } = useMemo(() => roomGeom(af, layout), [af, layout]);
  const tSup = layout.supply_temp_c;
  const tRet = af.metrics.crah_return_c;
  const dT = layout.server_delta_t_k;
  const ratio = layout.crah.airflow_ratio;

  // 경로 기하는 배치가 바뀔 때만, 온도는 시각마다(inlets) 바뀌므로 temps 만 따로 갱신
  const paths = useMemo(() => {
    const out: { path: Path; rack: number; kind: "main" | "recirc"; n: number }[] = [];
    const perRow = af.room.per_row;
    const step = Math.max(1, Math.floor(perRow / SAMPLE_COLS));
    const d = layout.racks.depth_m;
    af.racks.forEach((r, i) => {
      if ((r.col + Math.floor(step / 2)) % step !== 0) return;
      const row = rows[r.row];
      const s = row.face;
      const zCold = r.z_m + (s * pitch) / 2, zHot = r.z_m - (s * pitch) / 2;
      const zFront = r.z_m + (s * d) / 2, zBack = r.z_m - (s * d) / 2;
      let best = af.crah[0], bd = Infinity;
      for (const c of af.crah) {
        const dd = Math.hypot(c.x_m - r.x_m, c.z_m - r.z_m);
        if (dd < bd) { bd = dd; best = c; }
      }
      const cx = best.x_m, cz = best.z_m;
      const yRet = Math.min(ceilingH - 0.8, rackH + 2.2);   // 환기 경로 높이 (천장 플레넘 대신 랙 상부 공간)
      const main: Vec3[] = [[cx, crahH * 0.4, cz], [cx, -0.25, cz], [r.x_m, -0.25, zCold], [r.x_m, 0.3, zCold],
        [r.x_m, 1.2, zCold], [r.x_m, 1.2, zFront], [r.x_m, 1.2, zBack], [r.x_m, 1.4, zHot],
        [r.x_m, yRet, zHot], [cx, yRet, cz], [cx, crahH + 0.2, cz]];
      out.push({ path: mkPath(main, new Array(main.length).fill(tSup)), rack: i, kind: "main", n: PER_PATH });
      const nRec = Math.min(Math.round(r.recirculation * 25), 6);
      if (nRec > 0) {
        const rec: Vec3[] = [[r.x_m, 1.6, zHot], [r.x_m, rackH + 0.35, zBack], [r.x_m, rackH + 0.35, zFront],
          [r.x_m, 1.6, zCold - s * 0.3], [r.x_m, 1.2, zFront]];
        out.push({ path: mkPath(rec, new Array(rec.length).fill(tSup)), rack: i, kind: "recirc", n: nRec });
      }
    });
    return out;
  }, [af, layout, rows, pitch, rackH, crahH, ceilingH, tSup]);

  // 시각별 온도 반영 (경로 객체의 온도만 교체 - 입자 위치·위상은 유지)
  useEffect(() => {
    for (const p of paths) {
      const tin = inlets[p.rack] ?? af.racks[p.rack].inlet_c;
      const tout = tin + dT;
      p.path.temps = p.kind === "main"
        ? [tSup, tSup, tSup, tSup, tSup, tin, tout, tout, tout, tRet, tRet]
        : [tout, tout, (tin + tout) / 2, tin, tin];
    }
  }, [paths, inlets, af, dT, tSup, tRet]);

  const total = paths.reduce((s, p) => s + p.n, 0);
  const state = useMemo(() => {
    const phase = new Float32Array(total);
    let k = 0;
    for (const p of paths) for (let j = 0; j < p.n; j++) phase[k++] = (j + Math.random() * 0.5) / p.n;
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array(total * 3), 3));
    g.setAttribute("color", new BufferAttribute(new Float32Array(total * 3), 3));
    return { phase, g };
  }, [paths, total]);

  const tmp = useMemo(() => new Color(), []);
  useFrame((_, dtRaw) => {
    const dt = Math.min(dtRaw, 0.1);
    const pos = state.g.attributes.position.array as Float32Array;
    const col = state.g.attributes.color.array as Float32Array;
    let k = 0;
    for (const p of paths) {
      const speed = (BASE_SPEED * ratio * (p.kind === "recirc" ? 0.5 : 1)) / p.path.len;
      for (let j = 0; j < p.n; j++, k++) {
        state.phase[k] = (state.phase[k] + speed * dt) % 1;
        const t = at(p.path, state.phase[k] * p.path.len, pos, k * 3);
        airColor(t, tmp);
        col[k * 3] = tmp.r;
        col[k * 3 + 1] = tmp.g;
        col[k * 3 + 2] = tmp.b;
      }
    }
    state.g.attributes.position.needsUpdate = true;
    state.g.attributes.color.needsUpdate = true;
  });

  return (
    <points geometry={state.g} frustumCulled={false}>
      <pointsMaterial size={0.6} vertexColors transparent opacity={0.9} depthWrite={false} sizeAttenuation />
    </points>
  );
}
