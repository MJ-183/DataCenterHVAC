import { useLayoutEffect, useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Html } from "@react-three/drei";
import {
  Color, CylinderGeometry, DoubleSide, ExtrudeGeometry, InstancedMesh, Object3D, Quaternion, Shape, Vector3,
} from "three";
import type {
  AnalyzeResult, BPipePart, BPipeSegment, BuildingView, CoolingLayout, HourResult, Orientation, PipeKind, Scenario,
} from "../../types/api";
import { PIPE_COLOR, solarColor } from "./colors";
import { PIPE_SPEED_SCALE, Steam, hoverCursor, type Vec3 } from "./parts";
import { Chiller, CoolingTower } from "./plant";
import { CableTrays, ContainmentPanels, Crahs, RaisedFloor, Racks, type RackMode } from "./room";
import { concreteTexture } from "./textures";
import type { EnvMode } from "./building";

/**
 * 다중 존 '건물 전체' 3D (백엔드 /cooling/evaluate 의 building_view, 건물 좌표: 원점 = 건물 중심, x=동, y=위, z=남).
 * 층 슬래브·외벽(층 구분선)·존별 창·지붕/파라펫, 층마다 전산실 랙(인스턴싱)·CRAH·컨테인먼트, 지원 존 CDU,
 * 건물 좌표 배관(입상관 포함, 인스턴싱 원통 + 흐름 점), 야드 칠러·냉각탑 N대.
 * 층 표시: show(선택 층·전체) / ghost(반투명) / hidden(숨김·단면 위층)
 */
export type FloorVis = "show" | "ghost" | "hidden";
export type BSel =
  | { kind: "brack"; h: number; i: number } | { kind: "bcrah"; h: number; i: number }
  | { kind: "bunit"; which: "chiller" | "tower"; i: number } | { kind: "bcdu"; i: number } | { kind: "bpipe"; i: number };

export const ZONE_COLOR = { data_hall: "#2a78d6", support: "#eda100", office: "#1baf7a" } as const;
export const PART_LABEL: Record<BPipePart, string> = {
  chiller_link: "칠러 연결관", yard_header: "야드 헤더", feed: "건물 인입관", riser: "입상관", floor_branch: "층 분기관",
  hall: "전산실 분기", tower_link: "냉각탑 연결관", cw_header: "냉각수 헤더", fws_feed: "FWS 인입관",
  fws_riser: "FWS 입상관", fws_floor: "FWS 층 분기", cdu_link: "CDU 연결관",
};
const WALL_T = 0.3;
const SLAB_T = 0.3;
const WIN_H = 1.6;
const DOTS_MAX_PER_SEG = 48;

const bboxOf = (pts: [number, number][]) => {
  const xs = pts.map((p) => p[0]), zs = pts.map((p) => p[1]);
  return { x0: Math.min(...xs), x1: Math.max(...xs), z0: Math.min(...zs), z1: Math.max(...zs) };
};

export function buildingDimsOf(bv: BuildingView) {
  const b = bboxOf(bv.footprint);
  const top = bv.floors.reduce((s, f) => Math.max(s, f.elevation_m + f.height_m), 0);
  const plantX = Math.max(b.x1, ...bv.plant.chiller.map((u) => u.pos[0]), ...bv.plant.tower.map((u) => u.pos[0]));
  return { ...b, ew: b.x1 - b.x0, ns: b.z1 - b.z0, top, plantX };
}

/** 외곽선 변 → 외벽 (바깥 법선 방위) */
function footprintEdges(fp: [number, number][]) {
  const cx = fp.reduce((s, p) => s + p[0], 0) / fp.length, cz = fp.reduce((s, p) => s + p[1], 0) / fp.length;
  return fp.map((p, k) => {
    const q = fp[(k + 1) % fp.length];
    const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
    let nx = (q[1] - p[1]) / (len || 1), nz = -(q[0] - p[0]) / (len || 1);
    const mx = (p[0] + q[0]) / 2, mz = (p[1] + q[1]) / 2;
    if ((mx - cx) * nx + (mz - cz) * nz < 0) { nx = -nx; nz = -nz; }
    const o: Orientation = Math.abs(nx) > Math.abs(nz) ? (nx > 0 ? "E" : "W") : (nz > 0 ? "S" : "N");
    return { mx, mz, len, rotY: -Math.atan2(q[1] - p[1], q[0] - p[0]), nx, nz, o };
  }).filter((e) => e.len > 0.05);
}

function Slab({ fp, y, thick, color, opacity, onClick }: {
  fp: [number, number][]; y: number; thick: number; color: string; opacity: number; onClick?: (e: ThreeEvent<MouseEvent>) => void;
}) {
  const geo = useMemo(() => {
    const s = new Shape();
    fp.forEach(([x, z], i) => (i ? s.lineTo(x, z) : s.moveTo(x, z)));
    s.closePath();
    return new ExtrudeGeometry(s, { depth: thick, bevelEnabled: false });
  }, [fp, thick]);
  // 도형 평면(x, z) → 회전 +90° (x 축): (x, y=z, 0) → (x, 0, z), 돌출은 -y (아래로)
  return (
    <mesh geometry={geo} rotation={[Math.PI / 2, 0, 0]} position={[0, y, 0]} receiveShadow castShadow={opacity >= 1}
      onClick={onClick} {...(onClick ? hoverCursor : {})}>
      <meshStandardMaterial key={opacity} color={color} roughness={0.9} transparent={opacity < 1} opacity={opacity}
        depthWrite={opacity >= 1} side={DoubleSide} />
    </mesh>
  );
}

/** 층 슬래브·외벽·층 구분선·존 경계·창·지붕 */
export function BuildingShell({ bv, scenario, hour, env, vis, onWall, onRoof }: {
  bv: BuildingView; scenario: Scenario; hour: HourResult; env: EnvMode; vis: (f: number) => FloorVis;
  onWall: (o: Orientation) => void; onRoof: () => void;
}) {
  const edges = useMemo(() => footprintEdges(bv.footprint), [bv]);
  const tex = useMemo(() => {
    const t = concreteTexture().clone();
    t.repeat.set(12, 1.5);
    t.needsUpdate = true;
    return t;
  }, []);
  const dims = buildingDimsOf(bv);
  const click = (f: () => void) => (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); f(); };
  const topVisible = bv.floors.every((f) => vis(f.floor) === "show");
  const zoneEnv = (id: string) => scenario.zones?.find((z) => z.id === id)?.envelope;
  return (
    <group>
      {bv.floors.map((f) => {
        const v = vis(f.floor);
        if (v === "hidden") return null;
        const ghost = v === "ghost";
        const wallOpacity = ghost ? 0.05 : env === "exterior" ? 1 : env === "solar" ? 0.45 : 0.14;
        return (
          <group key={f.floor}>
            <Slab fp={bv.footprint} y={f.elevation_m} thick={SLAB_T} color="#b9b7b0" opacity={ghost ? 0.18 : 1} />
            {!ghost && bv.zones.filter((z) => z.floor === f.floor).map((z) => {   // 존 경계 (바닥 위 선)
              const y = f.elevation_m + 0.04, x0 = z.x_m - z.ew_m / 2, x1 = z.x_m + z.ew_m / 2;
              const z0 = z.z_m - z.ns_m / 2, z1 = z.z_m + z.ns_m / 2;
              const pts = new Float32Array([x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z1]);
              return (
                <lineLoop key={z.id}>
                  <bufferGeometry><bufferAttribute attach="attributes-position" args={[pts, 3]} /></bufferGeometry>
                  <lineBasicMaterial color={ZONE_COLOR[z.kind]} />
                </lineLoop>
              );
            })}
            {env !== "hidden" && edges.map((e, k) => (
              <group key={k}>
                <mesh position={[e.mx, f.elevation_m + f.height_m / 2, e.mz]} rotation={[0, e.rotY, 0]}
                  castShadow={wallOpacity >= 1} receiveShadow onClick={click(() => onWall(e.o))} {...hoverCursor}>
                  <boxGeometry args={[e.len, f.height_m, WALL_T]} />
                  {env === "exterior" && !ghost
                    ? <meshStandardMaterial key="tex" map={tex} roughness={0.92} />
                    : <meshStandardMaterial key={`f${wallOpacity}`} color={env === "solar" ? solarColor(hour.surface_solar_w_m2[e.o]) : "#d4d8dc"}
                      transparent opacity={wallOpacity} depthWrite={false} />}
                </mesh>
                {/* 층 구분선 (슬래브 끝) */}
                <mesh position={[e.mx + e.nx * 0.2, f.elevation_m + f.height_m - 0.15, e.mz + e.nz * 0.2]} rotation={[0, e.rotY, 0]}>
                  <boxGeometry args={[e.len + 0.4, 0.3, 0.12]} />
                  <meshStandardMaterial key={String(ghost)} color="#6f747a" transparent={ghost} opacity={ghost ? 0.2 : 1} />
                </mesh>
              </group>
            ))}
            {!ghost && env !== "hidden" && bv.zones.filter((z) => z.floor === f.floor).flatMap((z) =>
              (zoneEnv(z.id)?.windows ?? []).filter((w) => w.area_m2 > 0).map((w) => {   // 창 (존 외벽 가운데, 높이 1.6 m 띠)
                const horiz = w.orientation === "N" || w.orientation === "S";
                const len = horiz ? z.ew_m : z.ns_m;
                const width = Math.min(w.area_m2 / WIN_H, len * 0.8);
                const sgn = w.orientation === "S" || w.orientation === "E" ? 1 : -1;
                const off = WALL_T / 2 + 0.02;
                const pos: Vec3 = horiz
                  ? [z.x_m, f.elevation_m + 2.4, z.z_m + sgn * (z.ns_m / 2 + off)]
                  : [z.x_m + sgn * (z.ew_m / 2 + off), f.elevation_m + 2.4, z.z_m];
                return (
                  <mesh key={`${z.id}${w.orientation}`} position={pos} rotation={[0, horiz ? 0 : Math.PI / 2, 0]}>
                    <planeGeometry args={[width, WIN_H]} />
                    <meshStandardMaterial color="#29425c" metalness={0.8} roughness={0.12} side={DoubleSide} />
                  </mesh>
                );
              }))}
          </group>
        );
      })}
      {topVisible && (env === "exterior" || env === "solar") && (
        <group>
          <Slab fp={bv.footprint} y={dims.top + SLAB_T} thick={SLAB_T} opacity={env === "solar" ? 0.5 : 1}
            color={env === "solar" ? `#${solarColor(hour.surface_solar_w_m2.roof).getHexString()}` : "#a9a8a2"} onClick={click(onRoof)} />
          {env === "exterior" && edges.map((e, k) => (   // 파라펫
            <mesh key={k} position={[e.mx, dims.top + SLAB_T + 0.55, e.mz]} rotation={[0, e.rotY, 0]} castShadow>
              <boxGeometry args={[e.len, 0.8, WALL_T]} />
              <meshStandardMaterial map={tex} roughness={0.92} />
            </mesh>
          ))}
        </group>
      )}
    </group>
  );
}

/** 전산실 하나 (존 좌표 → group 이동). 기존 랙·CRAH·컨테인먼트·케이블 트레이 컴포넌트 재사용 */
export function HallInBuilding({ bv, h, layout, inlets, mode, rackH, crahSize, ghost, onRack, onCrah }: {
  bv: BuildingView; h: number; layout: CoolingLayout; inlets: number[]; mode: RackMode; rackH: number;
  crahSize: [number, number, number]; ghost: boolean; onRack: (i: number) => void; onCrah: (i: number) => void;
}) {
  const hall = bv.halls[h];
  const z = bv.zones.find((x) => x.id === hall.zone)!;
  const af = useMemo(() => ({
    racks: hall.racks.map((r) => ({ id: r.id, row: r.row, col: r.col, x_m: r.x - z.x_m, z_m: r.z - z.z_m, kw: r.kw,
      inlet_c: r.inlet_c })),
    crah: hall.crah.map((c) => ({ x_m: c.x - z.x_m, z_m: c.z - z.z_m, airflow_m3_s: c.airflow })),
  }) as unknown as AnalyzeResult, [hall, z]);
  return (
    <group position={[z.x_m, z.elevation_m, z.z_m]}>
      {!ghost && <RaisedFloor ew={z.ew_m} ns={z.ns_m} af={af} layout={layout} />}
      <Racks af={af} layout={layout} inlets={inlets} mode={mode} height={rackH} onRack={onRack} ghost={ghost} />
      {!ghost && <ContainmentPanels af={af} layout={layout} kind={layout.containment} rackH={rackH} ceilingH={z.height_m} />}
      {!ghost && <CableTrays af={af} layout={layout} rackH={rackH} />}
      <Crahs af={af} size={crahSize} onCrah={onCrah} ghost={ghost} />
    </group>
  );
}

export function Cdus({ bv, vis, onCdu }: { bv: BuildingView; vis: (f: number) => FloorVis; onCdu: (i: number) => void }) {
  return (
    <group>
      {bv.plant.cdu.map((c, i) => {
        const v = vis(c.floor);
        if (v === "hidden") return null;
        const ghost = v === "ghost";
        return (
          <group key={i} position={c.pos} onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onCdu(i); }} {...hoverCursor}>
            <mesh castShadow={!ghost}>
              <boxGeometry args={c.size} />
              <meshStandardMaterial key={String(ghost)} color="#c9d3de" metalness={0.3} roughness={0.5} transparent={ghost}
                opacity={ghost ? 0.15 : 1} depthWrite={!ghost} />
            </mesh>
            {!ghost && <mesh position={[0, c.size[1] * 0.3, c.size[2] / 2 + 0.01]}>
              <planeGeometry args={[c.size[0] * 0.8, c.size[1] * 0.25]} />
              <meshStandardMaterial color="#1f9d55" emissive="#0d4d29" />
            </mesh>}
          </group>
        );
      })}
    </group>
  );
}

/** 야드 칠러·냉각탑 N대 (운전 대수 = units.running[시각], 예비기는 회색 표시) */
export function YardPlant({ bv, running, towerLoad, evapM3h, onUnit }: {
  bv: BuildingView; running: { chiller: number; tower: number }; towerLoad: number; evapM3h: number;
  onUnit: (which: "chiller" | "tower", i: number) => void;
}) {
  const on = (which: "chiller" | "tower") => {
    // 운전 순서: 예비가 아닌 기기 중 앞에서부터 running 대
    const out = new Set<number>();
    bv.plant[which].forEach((u, i) => { if (!u.standby && out.size < running[which]) out.add(i); });
    return out;
  };
  const chOn = on("chiller"), twOn = on("tower");
  return (
    <group>
      {bv.plant.chiller.map((u, i) => (
        <group key={`c${i}`}>
          <Chiller box={u} onClick={() => onUnit("chiller", i)} />
          <mesh position={[u.pos[0], u.size[1] + 0.9, u.pos[2]]}>
            <sphereGeometry args={[0.28, 12, 8]} />
            <meshBasicMaterial color={chOn.has(i) ? "#34d399" : u.standby ? "#9ca3af" : "#f59e0b"} />
          </mesh>
        </group>
      ))}
      {bv.plant.tower.map((u, i) => (
        <group key={`t${i}`}>
          <CoolingTower box={u} load={twOn.has(i) ? towerLoad : 0} onClick={() => onUnit("tower", i)} />
          {twOn.has(i) && <Steam origin={[u.pos[0], u.size[1] + 1.4, u.pos[2]]} spread={u.size[0] * 0.55}
            evapM3h={evapM3h / Math.max(twOn.size, 1)} />}
        </group>
      ))}
    </group>
  );
}

interface Piece { a: Vector3; b: Vector3; seg: number; r: number }
interface DotPath { seg: number; pieces: Piece[]; cum: number[]; len: number; n: number; off: number; r: number; speed: number; phase: number }

/** 건물 배관: 모든 구간을 원통 인스턴싱 하나 + 흐름 점 인스턴싱 하나로 (그리기 2회). cutY 위는 숨김(수직관은 잘라 표시) */
export function BuildingPipes({ segments, ratio, cutY, hidden, onSegment }: {
  segments: BPipeSegment[]; ratio: (k: PipeKind) => number; cutY: number;
  hidden: (s: BPipeSegment) => boolean; onSegment: (i: number) => void;
}) {
  const { pieces, paths, nDots } = useMemo(() => {
    const pieces: Piece[] = [], paths: DotPath[] = [];
    let nDots = 0;
    segments.forEach((s, i) => {
      if (hidden(s)) return;
      const ins = s.kind.startsWith("chw") || s.kind.startsWith("fws");
      const r = s.dn / 2000 + (ins ? 0.05 : 0);
      const mine: Piece[] = [];
      for (let k = 0; k < s.points.length - 1; k++) {
        const a = new Vector3(...s.points[k]), b = new Vector3(...s.points[k + 1]);
        if (a.y >= cutY && b.y >= cutY) continue;
        if (a.y > cutY || b.y > cutY) {           // 수직관만 잘라 표시, 걸친 수평관은 생략
          if (Math.abs(a.x - b.x) > 1e-6 || Math.abs(a.z - b.z) > 1e-6) continue;
          if (a.y > cutY) a.y = cutY; else b.y = cutY;
        }
        if (a.distanceTo(b) < 1e-3) continue;
        mine.push({ a, b, seg: i, r });
      }
      if (!mine.length) return;
      pieces.push(...mine);
      const cum = [0];
      mine.forEach((p) => cum.push(cum[cum.length - 1] + p.a.distanceTo(p.b)));
      const len = cum[cum.length - 1];
      const d = s.dn / 1000;
      const speed = (s.design_flow_m3_h * ratio(s.kind)) / 3600 / ((Math.PI * d * d) / 4);
      const n = speed > 1e-6 ? Math.min(DOTS_MAX_PER_SEG, Math.max(2, Math.ceil(len / 1.6))) : 0;
      paths.push({ seg: i, pieces: mine, cum, len, n, off: nDots, r, speed, phase: Math.random() });
      nDots += n;
    });
    return { pieces, paths, nDots };
  }, [segments, cutY, hidden, ratio]);

  const tubes = useRef<InstancedMesh>(null);
  const dots = useRef<InstancedMesh>(null);
  const geo = useMemo(() => new CylinderGeometry(1, 1, 1, 12), []);
  useLayoutEffect(() => {
    const o = new Object3D(), up = new Vector3(0, 1, 0), c = new Color();
    pieces.forEach((p, k) => {
      const dir = p.b.clone().sub(p.a);
      const len = dir.length();
      o.position.copy(p.a).add(p.b).multiplyScalar(0.5);
      o.quaternion.copy(new Quaternion().setFromUnitVectors(up, dir.normalize()));
      o.scale.set(p.r, len + p.r * 2, p.r);
      o.updateMatrix();
      tubes.current!.setMatrixAt(k, o.matrix);
      tubes.current!.setColorAt(k, c.set(PIPE_COLOR[segments[p.seg].kind]));
    });
    tubes.current!.instanceMatrix.needsUpdate = true;
    if (tubes.current!.instanceColor) tubes.current!.instanceColor.needsUpdate = true;
  }, [pieces, segments]);

  const tmp = useMemo(() => new Object3D(), []);
  useFrame((_, dt) => {
    if (!dots.current) return;
    const step = Math.min(dt, 0.1);
    for (const p of paths) {
      if (!p.n) continue;
      p.phase = (p.phase + (p.speed * PIPE_SPEED_SCALE * step) / p.len) % 1;
      for (let i = 0; i < p.n; i++) {
        const s = (((i / p.n + p.phase) % 1) * p.len);
        let k = 0;
        while (k < p.pieces.length - 1 && p.cum[k + 1] < s) k++;
        const pc = p.pieces[k], f = (s - p.cum[k]) / Math.max(p.cum[k + 1] - p.cum[k], 1e-6);
        tmp.position.lerpVectors(pc.a, pc.b, f);
        tmp.scale.setScalar(p.r * 0.55);
        tmp.updateMatrix();
        dots.current.setMatrixAt(p.off + i, tmp.matrix);
      }
    }
    dots.current.instanceMatrix.needsUpdate = true;
  });

  const pick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (e.instanceId != null) onSegment(pieces[e.instanceId].seg);
  };
  return (
    <group>
      <instancedMesh ref={tubes} args={[geo, undefined, Math.max(pieces.length, 1)]} key={`t${pieces.length}`}
        castShadow onClick={pick} {...hoverCursor}>
        <meshStandardMaterial roughness={0.6} metalness={0.2} transparent opacity={0.85} />
      </instancedMesh>
      <instancedMesh args={[undefined, undefined, Math.max(nDots, 1)]} ref={dots} key={`d${nDots}`} frustumCulled={false}>
        <sphereGeometry args={[1, 8, 6]} />
        <meshBasicMaterial color="#ffffff" />
      </instancedMesh>
    </group>
  );
}

/** 존 이름표 (보이는 층만) */
export function ZoneLabels({ bv, vis }: { bv: BuildingView; vis: (f: number) => FloorVis }) {
  return <>{bv.zones.filter((z) => vis(z.floor) === "show").map((z) => (
    <Html key={z.id} position={[z.x_m, z.elevation_m + z.height_m - 0.6, z.z_m]} center style={{ pointerEvents: "none" }}>
      <span style={{ color: "#fff", fontSize: 11, fontWeight: 600, textShadow: "0 0 4px #000", whiteSpace: "nowrap" }}>
        {z.name}</span>
    </Html>
  ))}</>;
}
