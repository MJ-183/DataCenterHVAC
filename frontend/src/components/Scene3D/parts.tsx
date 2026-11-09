import { useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import {
  BufferAttribute, BufferGeometry, CurvePath, InstancedMesh, LineCurve3, Object3D, Vector3,
} from "three";

export type Vec3 = [number, number, number];
export type Click = (e: ThreeEvent<MouseEvent>) => void;

export const hoverCursor = {
  onPointerOver: (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); document.body.style.cursor = "pointer"; },
  onPointerOut: () => { document.body.style.cursor = "auto"; },
};

/** 배관 표시에서 흐름 점 속도 = 실제 관내 유속 x PIPE_SPEED_SCALE (눈에 보이도록 과장) */
export const PIPE_SPEED_SCALE = 2;

/**
 * 배관: 실제 관경(+보온) 굵기의 튜브 + 흐름 방향으로 움직이는 점.
 * radius [m] = 호칭경/2 (+보온), speed [m/s] = 관내 유속 (운전 유량 기준)
 */
export function Pipe({ points, color, radius, speed, insulated = false, onClick }: {
  points: Vec3[]; color: string; radius: number; speed: number; insulated?: boolean; onClick: Click;
}) {
  const curve = useMemo(() => {
    const c = new CurvePath<Vector3>();
    for (let i = 0; i < points.length - 1; i++) c.add(new LineCurve3(new Vector3(...points[i]), new Vector3(...points[i + 1])));
    return c;
  }, [points]);
  const length = useMemo(() => Math.max(curve.getLength(), 1e-3), [curve]);
  // 구간별 원통: 원통 기본축(Y)을 X축 90° 회전으로 Z에 맞추고, 그룹을 진행 방향으로 lookAt
  const segments = useMemo(() => points.slice(0, -1).map((p, i) => {
    const a = new Vector3(...p), b = new Vector3(...points[i + 1]);
    const o = new Object3D();
    o.lookAt(b.clone().sub(a));
    return { mid: a.clone().add(b).multiplyScalar(0.5), len: a.distanceTo(b), quat: o.quaternion.clone() };
  }), [points]);
  const n = Math.max(3, Math.ceil(length / 1.6));
  const dots = useRef<InstancedMesh>(null);
  const phase = useRef(Math.random());
  const tmp = useMemo(() => new Object3D(), []);

  useFrame((_, dt) => {
    if (!dots.current) return;
    phase.current = (phase.current + (speed * PIPE_SPEED_SCALE * Math.min(dt, 0.1)) / length) % 1;
    for (let i = 0; i < n; i++) {
      tmp.position.copy(curve.getPointAt((i / n + phase.current) % 1));
      tmp.updateMatrix();
      dots.current.setMatrixAt(i, tmp.matrix);
    }
    dots.current.instanceMatrix.needsUpdate = true;
  });

  return (
    <group>
      {segments.map((sg, i) => (
        <group key={i} position={sg.mid} quaternion={sg.quat}>
          <mesh rotation={[Math.PI / 2, 0, 0]} onClick={onClick} castShadow {...hoverCursor}>
            <cylinderGeometry args={[radius, radius, sg.len + radius * 2, 16]} />
            <meshStandardMaterial color={color} roughness={insulated ? 0.85 : 0.35} metalness={insulated ? 0 : 0.4}
              transparent opacity={0.8} />
          </mesh>
        </group>
      ))}
      <instancedMesh ref={dots} args={[undefined, undefined, n]} key={n} onClick={onClick}>
        <sphereGeometry args={[radius * 0.55, 8, 6]} />
        <meshBasicMaterial color="#ffffff" />
      </instancedMesh>
    </group>
  );
}

/** 냉각탑 수증기: 표시 입자 수 = 증발량 × STEAM_PER_M3H (상한 STEAM_MAX) */
export const STEAM_PER_M3H = 100;
export const STEAM_MAX = 800;

const STEAM_LIFE = 4; // s

export function Steam({ origin, spread, evapM3h }: { origin: Vec3; spread: number; evapM3h: number }) {
  const count = Math.min(Math.round(evapM3h * STEAM_PER_M3H), STEAM_MAX);
  const state = useMemo(() => {
    const pos = new Float32Array(STEAM_MAX * 3);
    const vel = new Float32Array(STEAM_MAX * 3);
    const age = new Float32Array(STEAM_MAX);
    const spawn = (i: number, a: number) => {
      vel[i * 3] = (Math.random() - 0.5) * 0.6;
      vel[i * 3 + 1] = 1.6 + Math.random() * 1.2;
      vel[i * 3 + 2] = (Math.random() - 0.5) * 0.6;
      pos[i * 3] = origin[0] + (Math.random() - 0.5) * spread + vel[i * 3] * a;
      pos[i * 3 + 1] = origin[1] + vel[i * 3 + 1] * a;
      pos[i * 3 + 2] = origin[2] + (Math.random() - 0.5) * spread + vel[i * 3 + 2] * a;
      age[i] = a;
    };
    for (let i = 0; i < STEAM_MAX; i++) spawn(i, Math.random() * STEAM_LIFE); // 처음부터 높이가 분산되도록
    return { pos, vel, age, spawn };
  }, [origin, spread]);
  const geom = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(state.pos, 3));
    return g;
  }, [state]);

  useFrame((_, dtRaw) => {
    const dt = Math.min(dtRaw, 0.1);
    const { pos, vel, age, spawn } = state;
    for (let i = 0; i < count; i++) {
      age[i] += dt;
      if (age[i] >= STEAM_LIFE) { spawn(i, 0); continue; }
      for (let k = 0; k < 3; k++) pos[i * 3 + k] += vel[i * 3 + k] * dt;
    }
    geom.setDrawRange(0, count);
    geom.attributes.position.needsUpdate = true;
  });

  return (
    <points geometry={geom} frustumCulled={false}>
      <pointsMaterial color="#f2f5f8" size={0.7} transparent opacity={0.45} depthWrite={false} sizeAttenuation />
    </points>
  );
}
