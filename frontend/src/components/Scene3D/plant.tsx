import { useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import type { Group } from "three";
import type { Box3, PipeSegment } from "../../types/api";
import { PIPE_COLOR } from "./colors";
import { Pipe, hoverCursor, type Click, type Vec3 } from "./parts";
import { louverTexture } from "./textures";

const click = (f: () => void) => (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); f(); };

/** 수냉식 칠러: 받침대 + 증발기·응축기 쉘(원통) + 압축기 + 제어반 */
export function Chiller({ box, onClick }: { box: Box3; onClick: () => void }) {
  const [x, , z] = box.pos;
  const [w, h, d] = box.size;
  const r = Math.min(h, d) * 0.22;
  return (
    <group position={[x, 0, z]} onClick={click(onClick)} {...hoverCursor}>
      <mesh position={[0, 0.15, 0]} receiveShadow>
        <boxGeometry args={[w + 1, 0.3, d + 1]} />
        <meshStandardMaterial color="#9b9a95" roughness={0.9} />
      </mesh>
      {[-1, 1].map((s) => (       // 증발기(냉수) / 응축기(냉각수) 쉘
        <mesh key={s} position={[0, 0.3 + r + 0.2, s * d * 0.22]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[r, r, w * 0.9, 24]} />
          <meshStandardMaterial color={s < 0 ? "#3d6fb4" : "#5b7a99"} metalness={0.5} roughness={0.4} />
        </mesh>
      ))}
      <mesh position={[-w * 0.1, 0.3 + 2 * r + 0.55, 0]} castShadow>
        <boxGeometry args={[w * 0.45, h * 0.35, d * 0.55]} />
        <meshStandardMaterial color="#4c5a6b" metalness={0.5} roughness={0.45} />
      </mesh>
      <mesh position={[w * 0.32, 0.3 + 2 * r + 0.4, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[r * 0.7, r * 0.7, w * 0.25, 20]} />
        <meshStandardMaterial color="#2f3a46" metalness={0.6} roughness={0.35} />
      </mesh>
      <mesh position={[w / 2 + 0.2, h * 0.45, 0]} castShadow>
        <boxGeometry args={[0.4, h * 0.9, d * 0.6]} />
        <meshStandardMaterial color="#d7dadf" roughness={0.5} />
      </mesh>
    </group>
  );
}

/** 냉각탑: 루버 외장 + 팬 데크 + 팬 스택 + 회전 팬 (회전 속도 ∝ 방열량/설계 방열량) */
export function CoolingTower({ box, load, onClick }: { box: Box3; load: number; onClick: () => void }) {
  const [x, , z] = box.pos;
  const [w, h, d] = box.size;
  const fan = useRef<Group>(null);
  const tex = useMemo(() => {
    const t = louverTexture().clone();
    t.repeat.set(w / 1.2, h / 1.2);
    t.needsUpdate = true;
    return t;
  }, [w, h]);
  useFrame((_, dt) => {
    if (fan.current) fan.current.rotation.y += Math.min(dt, 0.1) * 6 * Math.max(load, 0.05);
  });
  const stackR = Math.min(w, d) * 0.36;
  return (
    <group position={[x, 0, z]} onClick={click(onClick)} {...hoverCursor}>
      <mesh position={[0, 0.4, 0]} receiveShadow>
        <boxGeometry args={[w + 0.6, 0.8, d + 0.6]} />
        <meshStandardMaterial color="#6f7a85" roughness={0.8} />
      </mesh>
      <mesh position={[0, h / 2 + 0.2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, h - 0.4, d]} />
        <meshStandardMaterial map={tex} roughness={0.75} metalness={0.2} />
      </mesh>
      <mesh position={[0, h + 0.05, 0]} castShadow>
        <boxGeometry args={[w + 0.2, 0.1, d + 0.2]} />
        <meshStandardMaterial color="#5a646e" />
      </mesh>
      <mesh position={[0, h + 0.7, 0]} castShadow>
        <cylinderGeometry args={[stackR, stackR * 1.08, 1.3, 32, 1, true]} />
        <meshStandardMaterial color="#59636e" side={2} metalness={0.3} roughness={0.5} />
      </mesh>
      <group ref={fan} position={[0, h + 0.9, 0]}>
        {[0, 1, 2, 3, 4, 5].map((k) => (
          <mesh key={k} rotation={[0.25, (k * Math.PI) / 3, 0]} position={[0, 0, 0]}>
            <boxGeometry args={[stackR * 1.8, 0.04, 0.35]} />
            <meshStandardMaterial color="#2b3138" />
          </mesh>
        ))}
        <mesh>
          <cylinderGeometry args={[0.25, 0.25, 0.3, 16]} />
          <meshStandardMaterial color="#1f2328" />
        </mesh>
      </group>
    </group>
  );
}

/** 원심 펌프 + 모터 (받침대 위) */
export function Pump({ pos, color, onClick }: { pos: Vec3; color: string; onClick: () => void }) {
  return (
    <group position={pos} onClick={click(onClick)} {...hoverCursor}>
      <mesh position={[0, 0.15, 0]} receiveShadow>
        <boxGeometry args={[1.8, 0.3, 0.9]} />
        <meshStandardMaterial color="#8f8e88" roughness={0.9} />
      </mesh>
      <mesh position={[-0.4, 0.65, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[0.38, 0.38, 0.35, 24]} />
        <meshStandardMaterial color={color} metalness={0.4} roughness={0.45} />
      </mesh>
      <mesh position={[0.35, 0.6, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.28, 0.28, 0.8, 20]} />
        <meshStandardMaterial color="#3a4a5c" metalness={0.5} roughness={0.4} />
      </mesh>
    </group>
  );
}

/**
 * 배관망: 백엔드가 선정한 호칭경(DN)을 실제 굵기로 표시. 냉수관은 보온 두께 50 mm 포함.
 * 흐름 점 속도는 운전 유량 기준 관내 유속 (flowRatio = 현재 유량 / 설계 유량)
 */
export function PipeNetwork({ segments, chwRatio, cwRatio, onSegment }: {
  segments: PipeSegment[]; chwRatio: number; cwRatio: number; onSegment: (i: number) => Click;
}) {
  return (
    <group>
      {segments.map((s, i) => {
        const chw = s.kind.startsWith("chw");
        const d = s.dn / 1000;
        const q = (s.design_flow_m3_h * (chw ? chwRatio : cwRatio)) / 3600;
        const v = q / ((Math.PI * d * d) / 4);
        return (
          <Pipe key={i} points={s.points as Vec3[]} color={PIPE_COLOR[s.kind]} radius={d / 2 + (chw ? 0.05 : 0)}
            speed={v} insulated={chw} onClick={onSegment(i)} />
        );
      })}
    </group>
  );
}

/** 배관 지지대 (트렁크 구간 일정 간격 기둥) */
export function PipeRacks({ segments }: { segments: PipeSegment[] }) {
  const posts = useMemo(() => {
    const out: Vec3[] = [];
    for (const s of segments) {
      if (!s.kind.endsWith("supply")) continue;
      for (let k = 0; k < s.points.length - 1; k++) {
        const [a, b] = [s.points[k], s.points[k + 1]];
        if (Math.abs(a[1] - b[1]) > 0.1 || a[1] < 0.8) continue;
        const L = Math.hypot(b[0] - a[0], b[2] - a[2]);
        for (let t = 3; t < L; t += 6) {
          const f = t / L;
          out.push([a[0] + (b[0] - a[0]) * f, a[1], a[2] + (b[2] - a[2]) * f]);
        }
      }
    }
    return out;
  }, [segments]);
  return (
    <group>
      {posts.map((p, i) => (
        <mesh key={i} position={[p[0], (p[1] + 1.2) / 2, p[2]]}>
          <boxGeometry args={[0.08, p[1] + 1.2, 0.08]} />
          <meshStandardMaterial color="#7a838c" metalness={0.5} roughness={0.5} />
        </mesh>
      ))}
    </group>
  );
}
