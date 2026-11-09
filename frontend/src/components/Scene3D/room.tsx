import { useLayoutEffect, useMemo, useRef } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { Color, DoubleSide, InstancedMesh, Object3D } from "three";
import type { AnalyzeResult, Containment, CoolingLayout } from "../../types/api";
import { inletColor, kwColor } from "./colors";
import { hoverCursor } from "./parts";
import { roomGeom } from "./roomGeom";
import { floorTileTexture, grilleTexture, perfTileTexture, rackFrontTexture } from "./textures";

export type RackMode = "real" | "inlet" | "kw";

const TILE = 0.6;
/** 건물 전체 보기에서 선택하지 않은 층의 랙·CRAH 불투명도 */
export const GHOST_OPACITY = 0.12;

/** 이중마루: 일반 타일 + 콜드아일 유공 타일 */
export function RaisedFloor({ ew, ns, af, layout }: { ew: number; ns: number; af: AnalyzeResult; layout: CoolingLayout }) {
  const { aisles } = useMemo(() => roomGeom(af, layout), [af, layout]);
  const floor = useMemo(() => {
    const t = floorTileTexture().clone();
    t.repeat.set(ew / TILE, ns / TILE);
    t.needsUpdate = true;
    return t;
  }, [ew, ns]);
  const perf = useMemo(() => perfTileTexture(), []);
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]} receiveShadow>
        <planeGeometry args={[ew - 0.6, ns - 0.6]} />
        <meshStandardMaterial map={floor} roughness={0.8} />
      </mesh>
      {aisles.filter((a) => a.kind === "cold").map((a, i) => {
        const w = layout.racks.aisle_m - 0.6, l = a.x1 - a.x0;
        const t = perf.clone();
        t.repeat.set(l / TILE, w / TILE);
        t.needsUpdate = true;
        return (
          <mesh key={i} rotation={[-Math.PI / 2, 0, 0]} position={[(a.x0 + a.x1) / 2, 0.02, a.z]} receiveShadow>
            <planeGeometry args={[l, w]} />
            <meshStandardMaterial map={t} roughness={0.7} />
          </mesh>
        );
      })}
    </group>
  );
}

/** 랙: 본체(금속 프레임) + 전면 서버 패널(텍스처) 인스턴싱. 색상 모드에 따라 인스턴스 틴트 */
export function Racks({ af, layout, inlets, mode, height, onRack, ghost = false }: {
  af: AnalyzeResult; layout: CoolingLayout; inlets: number[]; mode: RackMode; height: number;
  onRack: (i: number) => void; ghost?: boolean;
}) {
  const body = useRef<InstancedMesh>(null);
  const front = useRef<InstancedMesh>(null);
  const g = layout.racks;
  const n = af.racks.length;
  const w = g.width_m * 0.94, d = g.depth_m;
  const tex = useMemo(() => rackFrontTexture(), []);

  useLayoutEffect(() => {
    const o = new Object3D();
    af.racks.forEach((r, i) => {
      const face = r.row % 2 === 0 ? 1 : -1;
      o.rotation.set(0, face === 1 ? 0 : Math.PI, 0);
      o.position.set(r.x_m, height / 2, r.z_m);
      o.updateMatrix();
      body.current!.setMatrixAt(i, o.matrix);
      o.position.set(r.x_m, height / 2, r.z_m + face * (d / 2 + 0.006));
      o.updateMatrix();
      front.current!.setMatrixAt(i, o.matrix);
    });
    body.current!.instanceMatrix.needsUpdate = true;
    front.current!.instanceMatrix.needsUpdate = true;
  }, [af, height, d]);

  useLayoutEffect(() => {
    const dark = new Color("#2b2f36"), white = new Color("#ffffff");
    af.racks.forEach((r, i) => {
      const c = mode === "inlet" ? inletColor(inlets[i] ?? r.inlet_c) : mode === "kw" ? kwColor(r.kw) : null;
      body.current!.setColorAt(i, c ? dark.clone().lerp(c, 0.45) : dark);
      front.current!.setColorAt(i, c ? white.clone().lerp(c, 0.75) : white);
    });
    body.current!.instanceColor!.needsUpdate = true;
    front.current!.instanceColor!.needsUpdate = true;
  }, [af, inlets, mode]);

  const pick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (e.instanceId != null) onRack(e.instanceId);
  };
  return (
    <group>
      <instancedMesh ref={body} args={[undefined, undefined, n]} key={`b${n}`} castShadow receiveShadow
        onClick={pick} {...hoverCursor}>
        <boxGeometry args={[w, height, d]} />
        <meshStandardMaterial key={String(ghost)} color="#ffffff" metalness={0.55} roughness={0.45}
          transparent={ghost} opacity={ghost ? GHOST_OPACITY : 1} depthWrite={!ghost} />
      </instancedMesh>
      <instancedMesh ref={front} args={[undefined, undefined, n]} key={`f${n}`} onClick={pick} {...hoverCursor}>
        <planeGeometry args={[w * 0.96, height * 0.97]} />
        <meshStandardMaterial key={String(ghost)} map={tex} roughness={0.5} metalness={0.2} emissiveMap={tex} emissive="#222222"
          transparent={ghost} opacity={ghost ? GHOST_OPACITY : 1} depthWrite={!ghost} />
      </instancedMesh>
    </group>
  );
}

/** 컨테인먼트: 콜드/핫아일 상부 패널 + 양 끝 도어 (반투명). 핫아일은 천장 플레넘까지 차폐 */
export function ContainmentPanels({ af, layout, kind, rackH, ceilingH }: {
  af: AnalyzeResult; layout: CoolingLayout; kind: Containment; rackH: number; ceilingH: number;
}) {
  const { aisles } = useMemo(() => roomGeom(af, layout), [af, layout]);
  if (kind === "open") return null;
  const target = kind === "cold_aisle" ? "cold" : "hot";
  const w = layout.racks.aisle_m + 0.1;
  return (
    <group>
      {aisles.filter((a) => a.kind === target).map((a, i) => {
        const l = a.x1 - a.x0, cx = (a.x0 + a.x1) / 2;
        // 핫아일: 랙 상부 차폐(침니) - 천장 플레넘 연결부는 생략하고 랙 위 1.2 m 까지만 표시
        const top = kind === "hot_aisle" ? Math.min(ceilingH - 0.4, rackH + 1.2) : rackH + 0.05;
        const panelH = top;
        return (
          <group key={i}>
            <mesh position={[cx, top, a.z]}>
              <boxGeometry args={[l, 0.04, w]} />
              <meshStandardMaterial color={kind === "cold_aisle" ? "#9cc8ff" : "#ffb59c"} transparent opacity={0.35}
                side={DoubleSide} depthWrite={false} />
            </mesh>
            {kind === "hot_aisle" && [-1, 1].map((s) => (    // 랙 상부 ~ 천장 측면 차폐
              <mesh key={s} position={[cx, (rackH + top) / 2, a.z + (s * w) / 2]}>
                <boxGeometry args={[l, top - rackH, 0.03]} />
                <meshStandardMaterial color="#ffb59c" transparent opacity={0.25} depthWrite={false} side={DoubleSide} />
              </mesh>
            ))}
            {[a.x0 - 0.05, a.x1 + 0.05].map((x, k) => (     // 끝단 도어 (프레임 + 유리)
              <group key={k} position={[x, panelH / 2, a.z]}>
                <mesh>
                  <boxGeometry args={[0.05, panelH, w]} />
                  <meshStandardMaterial color="#d6e6f5" transparent opacity={0.3} depthWrite={false} />
                </mesh>
                <mesh position={[0, 0, 0]}>
                  <boxGeometry args={[0.07, panelH, 0.06]} />
                  <meshStandardMaterial color="#6b7480" metalness={0.6} roughness={0.4} />
                </mesh>
              </group>
            ))}
          </group>
        );
      })}
    </group>
  );
}

/** 랙 열 상부 케이블 트레이 + 열 끝 PDU */
export function CableTrays({ af, layout, rackH }: { af: AnalyzeResult; layout: CoolingLayout; rackH: number }) {
  const { rows } = useMemo(() => roomGeom(af, layout), [af, layout]);
  return (
    <group>
      {rows.map((r, j) => (
        <group key={j}>
          <mesh position={[(r.x0 + r.x1) / 2, rackH + 0.35, r.z]} castShadow>
            <boxGeometry args={[r.x1 - r.x0, 0.08, 0.45]} />
            <meshStandardMaterial color="#c8a630" metalness={0.5} roughness={0.5} />
          </mesh>
          <mesh position={[(r.x0 + r.x1) / 2, rackH + 0.6, r.z - r.face * 0.2]}>
            <boxGeometry args={[r.x1 - r.x0, 0.06, 0.3]} />
            <meshStandardMaterial color="#8d949c" metalness={0.6} roughness={0.4} />
          </mesh>
          <mesh position={[r.x0 - 0.5, rackH * 0.45, r.z]} castShadow>
            <boxGeometry args={[0.7, rackH * 0.9, layout.racks.depth_m]} />
            <meshStandardMaterial color="#3d4450" metalness={0.4} roughness={0.5} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

/** CRAH: 하부 급기(이중마루), 상부 환기 그릴. 실내 쪽을 향함 */
export function Crahs({ af, size, onCrah, ghost = false }: {
  af: AnalyzeResult; size: [number, number, number]; onCrah: (i: number) => void; ghost?: boolean;
}) {
  const grille = useMemo(() => grilleTexture(), []);
  const [w, h, d] = size;
  return (
    <group>
      {af.crah.map((c, i) => {
        const face = c.z_m < 0 ? 1 : -1;
        return (
          <group key={i} position={[c.x_m, 0, c.z_m]} rotation={[0, face === 1 ? 0 : Math.PI, 0]}
            onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onCrah(i); }} {...hoverCursor}>
            <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
              <boxGeometry args={[w, h, d]} />
              <meshStandardMaterial key={String(ghost)} color="#e9ecef" roughness={0.55} metalness={0.15}
                transparent={ghost} opacity={ghost ? GHOST_OPACITY : 1} depthWrite={!ghost} />
            </mesh>
            {!ghost && <>
              <mesh position={[0, h / 2, d / 2 + 0.005]}>
                <planeGeometry args={[w * 0.92, h * 0.9]} />
                <meshStandardMaterial map={grille} roughness={0.6} />
              </mesh>
              <mesh position={[0, h + 0.15, 0]}>
                <boxGeometry args={[w * 0.9, 0.3, d * 0.85]} />
                <meshStandardMaterial color="#9aa3ad" roughness={0.5} />
              </mesh>
            </>}
          </group>
        );
      })}
    </group>
  );
}

/** 천장 조명 (통로 따라 선형 LED). 단면 보기에서 실내 분위기용 */
export function CeilingLights({ af, layout, ceilingH }: { af: AnalyzeResult; layout: CoolingLayout; ceilingH: number }) {
  const { aisles } = useMemo(() => roomGeom(af, layout), [af, layout]);
  return (
    <group>
      {aisles.map((a, i) => (
        <mesh key={i} position={[(a.x0 + a.x1) / 2, ceilingH - 0.15, a.z]}>
          <boxGeometry args={[a.x1 - a.x0, 0.05, 0.12]} />
          <meshStandardMaterial color="#ffffff" emissive="#fdf6e3" emissiveIntensity={1.2} />
        </mesh>
      ))}
    </group>
  );
}
