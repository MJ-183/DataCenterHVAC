import { useMemo } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import type { HourResult, Orientation, Scenario } from "../../types/api";
import { solarColor } from "./colors";
import { hoverCursor, type Vec3 } from "./parts";
import { concreteTexture } from "./textures";

/** 외피 표시: 단면(벽 하부만, 지붕 없음) / 실제 외관 / 일사 히트맵(반투명) / 숨김 */
export type EnvMode = "cutaway" | "exterior" | "solar" | "hidden";

const WALL_T = 0.3;
const CUT_H = 1.2;
const WIN_H = 1.6;

export interface WallGeom { o: Orientation; pos: Vec3; size: Vec3; label: Vec3; normal: Vec3; len: number }

export function wallGeoms(ew: number, ns: number, h: number): WallGeom[] {
  return [
    { o: "N", pos: [0, h / 2, -ns / 2], size: [ew, h, WALL_T], label: [0, h + 1.5, -ns / 2], normal: [0, 0, -1], len: ew },
    { o: "S", pos: [0, h / 2, ns / 2], size: [ew, h, WALL_T], label: [0, h + 1.5, ns / 2], normal: [0, 0, 1], len: ew },
    { o: "E", pos: [ew / 2, h / 2, 0], size: [WALL_T, h, ns], label: [ew / 2, h + 1.5, 0], normal: [1, 0, 0], len: ns },
    { o: "W", pos: [-ew / 2, h / 2, 0], size: [WALL_T, h, ns], label: [-ew / 2, h + 1.5, 0], normal: [-1, 0, 0], len: ns },
  ];
}

export function Building({ ew, ns, h, scenario, hour, mode, onWall, onRoof }: {
  ew: number; ns: number; h: number; scenario: Scenario; hour: HourResult; mode: EnvMode;
  onWall: (o: Orientation) => void; onRoof: () => void;
}) {
  const walls = useMemo(() => wallGeoms(ew, ns, h), [ew, ns, h]);
  const tex = useMemo(() => {
    const t = concreteTexture().clone();
    t.repeat.set(Math.max(ew, ns) / 6, h / 3);
    t.needsUpdate = true;
    return t;
  }, [ew, ns, h]);
  if (mode === "hidden") return null;
  const wallH = mode === "cutaway" ? CUT_H : h;
  const solar = mode === "solar";
  const click = (f: () => void) => (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); f(); };
  const winArea = (o: Orientation) =>
    scenario.envelope.windows.filter((w) => w.orientation === o).reduce((s, w) => s + w.area_m2, 0);

  return (
    <group>
      {walls.map((w) => {
        const size: Vec3 = [w.size[0], wallH, w.size[2]];
        const winW = Math.min(winArea(w.o) / WIN_H, w.len * 0.8);
        return (
          <group key={w.o}>
            <mesh position={[w.pos[0], wallH / 2, w.pos[2]]} castShadow receiveShadow
              onClick={click(() => onWall(w.o))} {...hoverCursor}>
              <boxGeometry args={size} />
              {solar
                ? <meshStandardMaterial color={solarColor(hour.surface_solar_w_m2[w.o])} transparent opacity={0.6} depthWrite={false} />
                : <meshStandardMaterial map={tex} roughness={0.92} />}
            </mesh>
            {mode === "exterior" && winW > 0.2 && (       // 창 (방위별 창 면적 = 폭 x 1.6 m 띠)
              <mesh position={[w.pos[0] + w.normal[0] * (WALL_T / 2 + 0.01), 2.4, w.pos[2] + w.normal[2] * (WALL_T / 2 + 0.01)]}
                rotation={[0, w.normal[0] !== 0 ? (w.normal[0] > 0 ? Math.PI / 2 : -Math.PI / 2) : (w.normal[2] > 0 ? 0 : Math.PI), 0]}>
                <planeGeometry args={[winW, WIN_H]} />
                <meshStandardMaterial color="#29425c" metalness={0.8} roughness={0.12} />
              </mesh>
            )}
          </group>
        );
      })}
      {(mode === "exterior" || solar) && (
        <group onClick={click(onRoof)} {...hoverCursor}>
          <mesh position={[0, h, 0]} castShadow receiveShadow>
            <boxGeometry args={[ew, WALL_T, ns]} />
            {solar
              ? <meshStandardMaterial color={solarColor(hour.surface_solar_w_m2.roof)} transparent opacity={0.4} depthWrite={false} />
              : <meshStandardMaterial color="#a9a8a2" roughness={0.95} />}
          </mesh>
          {!solar && walls.map((w) => (           // 파라펫
            <mesh key={w.o} position={[w.pos[0], h + 0.55, w.pos[2]]} castShadow>
              <boxGeometry args={[w.size[0], 0.8, w.size[2]]} />
              <meshStandardMaterial map={tex} roughness={0.92} />
            </mesh>
          ))}
          {!solar && [[-ew / 4, -ns / 5], [ew / 6, ns / 6]].map(([x, z], i) => (   // 옥상 설비 (공조기·배기팬)
            <mesh key={i} position={[x, h + 0.9, z]} castShadow>
              <boxGeometry args={[4, 1.5, 2.5]} />
              <meshStandardMaterial color="#b9bdc2" metalness={0.3} roughness={0.6} />
            </mesh>
          ))}
        </group>
      )}
    </group>
  );
}

/** 대지: 잔디 + 건물·설비 야드 아스팔트 + 진입로 */
export function Ground({ ew, ns, plantX }: { ew: number; ns: number; plantX: number }) {
  const padW = plantX + 14 + ew / 2;
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[plantX / 2, -0.02, 0]}>
        <planeGeometry args={[500, 500]} />
        <meshStandardMaterial color="#6d7f5c" roughness={1} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[(plantX + 14 - ew / 2) / 2, -0.01, 0]}>
        <planeGeometry args={[padW + 12, ns + 22]} />
        <meshStandardMaterial color="#5d6166" roughness={0.95} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[0, -0.005, ns / 2 + 30]}>
        <planeGeometry args={[8, 40]} />
        <meshStandardMaterial color="#4f5357" roughness={0.95} />
      </mesh>
    </group>
  );
}
