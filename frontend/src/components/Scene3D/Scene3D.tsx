import { Component, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { Edges, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { Color, Mesh, MeshStandardMaterial } from "three";
import type {
  BPipeSegment, BuildingView, CoolingLayout, EvaluateResult, HourResult, Orientation, PipeKind, PlantHour, Scenario,
  SimulationResult,
} from "../../types/api";
import { fmt, hhmm } from "../../lib/format";
import { useSmoothMinute, type Clock } from "../../hooks/usePlayback";
import { LAYOUT, buildingDims, type Layout, type Role } from "./layout";
import {
  AIR_MAX, AIR_MIN, INLET_MAX, INLET_MIN, KW_MAX, PIPE, SOLAR_MAX, cssGradient, solarColor, utilColor,
} from "./colors";
import { PIPE_SPEED_SCALE, STEAM_PER_M3H, Steam, type Vec3 } from "./parts";
import { Building, Ground, wallGeoms, type EnvMode } from "./building";
import { CableTrays, CeilingLights, ContainmentPanels, Crahs, RaisedFloor, Racks, type RackMode } from "./room";
import { Chiller, CoolingTower, PipeNetwork, PipeRacks, Pump } from "./plant";
import { AirflowParticles } from "./airflow";
import {
  BuildingPipes, BuildingShell, Cdus, HallInBuilding, PART_LABEL, YardPlant, ZONE_COLOR, ZoneLabels, buildingDimsOf,
  type BSel, type FloorVis,
} from "./buildingView";

type Sel =
  | { kind: "wall"; o: Orientation } | { kind: "roof" } | { kind: "rack"; i: number } | { kind: "crah"; i: number }
  | { kind: "chiller" } | { kind: "tower" } | { kind: "pipe"; i: number } | { kind: "pump"; which: "chw" | "cw" } | BSel;

const ORIENT_LABEL: Record<Orientation, string> = { N: "북측", E: "동측", S: "남측", W: "서측" };
const PIPE_LABEL: Record<PipeKind, string> = {
  chw_supply: "냉수 공급", chw_return: "냉수 환수", cw_supply: "냉각수 공급", cw_return: "냉각수 환수",
  fws_supply: "FWS 공급", fws_return: "FWS 환수",
};
const CONT_LABEL = { open: "개방형", cold_aisle: "콜드아일 컨테인먼트", hot_aisle: "핫아일 컨테인먼트" };
const TOPO_LABEL = { radial: "단일 헤더(직렬)", dual: "남·북 2계통", ring: "루프(환상)" };

/** 태양 방향 단위벡터. 좌표계: x=동, y=위, z=남. 엔진 방위각은 남=0, 서=+ */
function sunDir(h: HourResult): Vec3 {
  const alt = (h.sun.altitude_deg * Math.PI) / 180, az = (h.sun.azimuth_deg * Math.PI) / 180;
  return [-Math.sin(az) * Math.cos(alt), Math.sin(alt), Math.cos(az) * Math.cos(alt)];
}

/** 시간별(24개) 값 사이 선형 보간 (23시 이후는 0시로 순환 - 엔진 보간 규칙과 동일) */
function lerpHour(rows: PlantHour[], t: number): PlantHour {
  const i = Math.floor(t) % 24, f = t - Math.floor(t);
  const a = rows[i], b = rows[(i + 1) % 24];
  const out = { ...a };   // 정수·문자열(시각, 초과 랙 수, 모드)은 앞 시각 값 유지
  for (const k of Object.keys(a) as (keyof PlantHour)[]) {
    const x = a[k], y = b[k];
    if (k !== "hour" && k !== "racks_over_limit" && typeof x === "number" && typeof y === "number") {
      (out[k] as number) = x + (y - x) * f;
    }
  }
  return out;
}

/** 숫자 필드만 재귀 선형 보간 (문자열·불리언·null 은 앞 값). 방위각은 ±180° 경계를 짧은 쪽으로 */
function lerpDeep<T>(a: T, b: T, f: number, key = ""): T {
  if (typeof a === "number" && typeof b === "number") {
    if (key === "azimuth_deg") {
      const d = ((b - a + 540) % 360) - 180;
      return ((((a + d * f) + 540) % 360) - 180) as T;
    }
    return (a + (b - a) * f) as T;
  }
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a)) {
    const out = { ...a } as Record<string, unknown>;
    for (const k of Object.keys(out)) out[k] = lerpDeep((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k], f, k);
    return out as T;
  }
  return a;
}

/** 계산 간격(stepMin) 결과 사이를 보간한 임의 시각(분)의 값. 마지막 간격 다음은 0시로 순환 */
function lerpResult(rows: HourResult[], minute: number, stepMin: number): HourResult {
  const x = minute / stepMin, i = Math.min(Math.floor(x), rows.length - 1), f = x - i;
  const a = rows[i], b = rows[(i + 1) % rows.length];
  if (f <= 0) return a;
  const out = lerpDeep(a, b, f);
  // 시각은 순환과 무관하게 연속 (23:55 → 24:00 보간이 0시로 되돌아가지 않도록)
  return { ...out, hour: a.hour, minute: a.minute, t_h: minute / 60 };
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="r"><span>{k}</span><span>{v}</span></div>;
}

const floorName = (f: number) => `${f + 1}층`;

/** 건물 전체 보기 선택 항목 (존·층 표시) */
function BTooltipBody({ sel, h, bv, cooling, cl, binlets, ph, ratio }: {
  sel: BSel; h: HourResult; bv: BuildingView; cooling: EvaluateResult; cl: CoolingLayout;
  binlets: number[][]; ph: PlantHour | null; ratio: (k: PipeKind) => number;
}) {
  const t = hhmm(h.t_h);
  const zoneName = (id: string | null) => bv.zones.find((z) => z.id === id)?.name ?? "–";
  const hr = Math.floor(h.t_h) % 24;
  switch (sel.kind) {
    case "brack": {
      const hall = bv.halls[sel.h], r = hall.racks[sel.i], tin = binlets[sel.h]?.[sel.i] ?? r.inlet_c;
      return <>
        <div className="t">랙 {r.id} · {t}</div>
        <Row k="존 · 층" v={`${zoneName(hall.zone)} · ${floorName(hall.floor)}`} />
        <Row k="열 · 번호" v={`${r.row + 1}열 ${r.col + 1}번`} />
        <Row k="흡입 / 배기 온도" v={`${fmt(tin, 1)} / ${fmt(tin + cl.server_delta_t_k, 1)} ℃`} />
        <Row k="허용 상한" v={`${fmt(cl.inlet_limit_c, 1)} ℃`} />
        <Row k={`IT 전력 (피크 ${cooling.peak_hour}시)`} v={`${fmt(r.kw, 1)} kW`} />
        <Row k="건물 좌표 (x, 바닥, z)" v={`${fmt(r.x, 1)}, ${fmt(r.y, 1)}, ${fmt(r.z, 1)} m`} />
      </>;
    }
    case "bcrah": {
      const hall = bv.halls[sel.h], c = hall.crah[sel.i];
      return <>
        <div className="t">CRAH #{sel.i + 1} · {t}</div>
        <Row k="존 · 층" v={`${zoneName(hall.zone)} · ${floorName(hall.floor)}`} />
        <Row k="풍량" v={`${fmt(c.airflow, 2)} m³/s (배율 ${fmt(cl.crah.airflow_ratio, 2)})`} />
        <Row k="급기 온도" v={`${fmt(cl.supply_temp_c, 1)} ℃`} />
        <Row k="이 전산실 CRAH" v={`${hall.crah.length}대`} />
      </>;
    }
    case "bunit": {
      const u = bv.plant[sel.which][sel.i], plan = cooling.units?.[sel.which];
      const run = plan?.running[hr];
      const name = sel.which === "chiller" ? "칠러" : "냉각탑";
      const now = sel.which === "chiller" ? (ph ? ph.cooling_kw : h.load_kw.total_cooling) : h.condenser_water.heat_rejection_kw;
      return <>
        <div className="t">{name} #{sel.i + 1}/{bv.plant[sel.which].length} · {t}</div>
        <Row k="구분" v={u.standby ? "예비기 (N+k)" : "운전 대상"} />
        {plan && <Row k="1대 용량" v={`${fmt(plan.unit_kw)} kW`} />}
        {plan && run != null && <Row k="지금 운전 대수" v={`${run} / ${plan.total}대 (${plan.redundancy})`} />}
        <Row k={sel.which === "chiller" ? "냉동부하 (전체)" : "방열량 (전체)"} v={`${fmt(now)} kW`} />
        {plan && run ? <Row k="1대당 부하율" v={`${fmt((now / run / plan.unit_kw) * 100, 0)} %`} /> : null}
        <Row k="위치 출처" v={bv.plant.source[sel.which] === "cad" ? "도면" : "자동 배치 (가정)"} />
      </>;
    }
    case "bcdu": {
      const c = bv.plant.cdu[sel.i], plan = cooling.units?.cdu;
      const liq = h.plant.liquid_loop;
      return <>
        <div className="t">CDU #{sel.i + 1}/{bv.plant.cdu.length} · {t}</div>
        <Row k="존 · 층" v={`${zoneName(c.zone)} · ${floorName(c.floor)}`} />
        {plan && <Row k="1대 용량 / 설계 부하" v={`${fmt(plan.unit_kw)} / ${fmt(plan.design_kw)} kW`} />}
        {plan && <Row k="지금 운전 대수" v={`${plan.running[hr]} / ${plan.total}대 (${plan.redundancy})`} />}
        {liq && <Row k="액체 루프 회수 (전체)" v={`${fmt(liq.load_kw)} kW`} />}
        {liq && <Row k="FWS 공급 / 환수" v={`${fmt(liq.fws_supply_c, 1)} / ${fmt(liq.fws_return_c, 1)} ℃`} />}
        <Row k="위치 출처" v={bv.plant.source.cdu === "cad" ? "도면" : "자동 배치 (가정)"} />
      </>;
    }
    case "bpipe": {
      const s: BPipeSegment = bv.piping.segments[sel.i];
      const q = s.design_flow_m3_h * ratio(s.kind), d = s.dn / 1000;
      return <>
        <div className="t">{PIPE_LABEL[s.kind]} · {PART_LABEL[s.part]} · {t}</div>
        {(s.zone || s.floor != null) && <Row k="존 · 층" v={`${s.zone ? zoneName(s.zone) : "–"} · ${s.floor != null ? floorName(s.floor) : "–"}`} />}
        <Row k="호칭경" v={`DN${s.dn}`} />
        <Row k="구간 길이" v={`${fmt(s.length_m, 1)} m`} />
        <Row k="설계 / 현재 유량" v={`${fmt(s.design_flow_m3_h)} / ${fmt(q)} m³/h`} />
        <Row k="현재 유속" v={`${fmt(q / 3600 / ((Math.PI * d * d) / 4), 2)} m/s`} />
        {s.kind.startsWith("chw") && <Row k="냉수 양정 (최원단)" v={`${fmt(bv.piping.chw_head_kpa, 1)} kPa · 입상관 마찰 ${fmt(bv.piping.riser_friction_kpa, 2)} kPa`} />}
      </>;
    }
  }
}

function TooltipBody({ sel, h, sc, cooling, cl, inlets, ph }: {
  sel: Sel; h: HourResult; sc: Scenario; cooling: EvaluateResult | null; cl: CoolingLayout;
  inlets: number[]; ph: PlantHour | null;
}) {
  const L = h.load_kw;
  const t = hhmm(h.t_h);
  switch (sel.kind) {
    case "wall": {
      const walls = sc.envelope.walls.filter((w) => w.orientation === sel.o);
      const area = walls.reduce((s, w) => s + w.area_m2, 0);
      return <>
        <div className="t">{ORIENT_LABEL[sel.o]} 외벽 · {t}</div>
        <Row k="입사 일사량" v={`${fmt(h.surface_solar_w_m2[sel.o], 1)} W/m²`} />
        <Row k="면적 (입력)" v={`${fmt(area, 0)} m²`} />
        {walls[0] && <Row k="U / 흡수율 (입력)" v={`${walls[0].u_value} / ${walls[0].absorptance}`} />}
        <Row k="외벽 관류 (4면 합계)" v={`${fmt(L.wall_conduction, 2)} kW`} />
      </>;
    }
    case "roof":
      return <>
        <div className="t">지붕 · {t}</div>
        <Row k="입사 일사량 (GHI)" v={`${fmt(h.surface_solar_w_m2.roof, 0)} W/m²`} />
        <Row k="지붕 관류" v={`${fmt(L.roof_conduction, 2)} kW`} />
      </>;
    case "rack": {
      const af = cooling!.airflow, r = af.racks[sel.i];
      const kwNow = af.it_kw > 0 ? r.kw * (L.it / af.it_kw) : 0;
      return <>
        <div className="t">랙 {r.id ? `${r.id} (${r.row + 1}열 ${r.col + 1}번)` : `${r.row + 1}열 ${r.col + 1}번`} · {t}</div>
        <Row k="IT 전력 (현재)" v={`${fmt(kwNow, 2)} kW`} />
        <Row k="흡입 / 배기 온도" v={`${fmt(inlets[sel.i], 1)} / ${fmt(inlets[sel.i] + cl.server_delta_t_k, 1)} ℃`} />
        <Row k="허용 상한" v={`${fmt(cl.inlet_limit_c, 1)} ℃`} />
        <Row k={`재순환 비율 (피크 ${af.hour}시)`} v={fmt(r.recirculation, 3)} />
        <Row k="급기 / 요구 풍량 (피크)" v={`${fmt(r.supplied_m3_s, 3)} / ${fmt(r.required_m3_s, 3)} m³/s`} />
      </>;
    }
    case "crah": {
      const c = cooling!.airflow.crah[sel.i];
      return <>
        <div className="t">CRAH #{sel.i + 1} · {t}</div>
        <Row k="풍량" v={`${fmt(c.airflow_m3_s, 2)} m³/s (배율 ${fmt(cl.crah.airflow_ratio, 2)})`} />
        <Row k="급기 온도" v={`${fmt(cl.supply_temp_c, 1)} ℃`} />
        {ph && <Row k="환기 온도" v={`${fmt(ph.crah_return_c, 1)} ℃`} />}
        {ph && <Row k="팬 전력 (전체)" v={`${fmt(ph.crah_fan_kw, 1)} kW`} />}
        <Row k="대수" v={`${cooling!.airflow.crah.length}대`} />
      </>;
    }
    case "chiller":
      return <>
        <div className="t">칠러 (1대 등가) · {t}</div>
        {ph ? <>
          <Row k="냉동부하 (연성 모델)" v={`${fmt(ph.cooling_kw)} kW`} />
          <Row k="전력" v={`${fmt(ph.chiller_kw)} kW`} />
          <Row k="COP (냉수 온도 보정)" v={fmt(ph.cop, 2)} />
        </> : <>
          <Row k="냉동능력 (=부하)" v={`${fmt(L.total_cooling)} kW`} />
          <Row k="전력" v={`${fmt(h.chiller.power_kw)} kW`} />
          <Row k="COP" v={fmt(h.chiller.cop, 2)} />
        </>}
        <Row k="냉수 공급 / 환수" v={`${fmt(sc.cooling_plant.chw_supply_c)} / ${fmt(sc.cooling_plant.chw_return_c)} ℃`} />
        <Row k="외기 습구" v={`${fmt(h.weather.wet_bulb_c, 2)} ℃`} />
      </>;
    case "tower": {
      const M = h.makeup_water_m3_h;
      return <>
        <div className="t">냉각탑 · {t}</div>
        <Row k="방열량" v={`${fmt(h.condenser_water.heat_rejection_kw)} kW`} />
        <Row k="냉각수 유량" v={`${fmt(h.condenser_water.flow_m3_h)} m³/h`} />
        <Row k="증발" v={`${fmt(M.evaporation, 3)} m³/h`} />
        <Row k="블로다운" v={`${fmt(M.blowdown, 3)} m³/h`} />
        <Row k="보급수 합계" v={`${fmt(M.total, 3)} m³/h`} />
      </>;
    }
    default:
      return null;
    case "pipe": {
      const s = cooling!.piping.segments[sel.i];
      const chw = s.kind.startsWith("chw");
      if (!PIPE_LABEL[s.kind]) return null;
      const ratio = ph ? (chw ? ph.chw_flow_m3_h / cooling!.piping.chw_design_flow_m3_h
        : ph.cw_flow_m3_h / cooling!.piping.cw_design_flow_m3_h) : 1;
      const q = s.design_flow_m3_h * ratio, d = s.dn / 1000;
      return <>
        <div className="t">{PIPE_LABEL[s.kind]} 배관 · {t}</div>
        <Row k="호칭경" v={`DN${s.dn}`} />
        <Row k="구간 길이" v={`${fmt(s.length_m, 1)} m`} />
        <Row k="설계 / 현재 유량" v={`${fmt(s.design_flow_m3_h)} / ${fmt(q)} m³/h`} />
        <Row k="현재 유속" v={`${fmt(q / 3600 / ((Math.PI * d * d) / 4), 2)} m/s`} />
        {chw && <Row k="냉수 배관 방식" v={TOPO_LABEL[cooling!.piping.topology]} />}
      </>;
    }
    case "pump":
      return <>
        <div className="t">{sel.which === "chw" ? "냉수" : "냉각수"} 펌프 · {t}</div>
        {ph && <Row k="유량" v={`${fmt(sel.which === "chw" ? ph.chw_flow_m3_h : ph.cw_flow_m3_h)} m³/h`} />}
        {ph && <Row k="전력" v={`${fmt(sel.which === "chw" ? ph.chw_pump_kw : ph.cw_pump_kw, 1)} kW`} />}
        {sel.which === "chw" && cooling && <Row k="설계 양정" v={`${fmt(cooling.piping.chw_head_kpa, 0)} kPa`} />}
      </>;
  }
}

/** Phase 5: 실제 도면 GLB. bindings(메시 이름 접두어 → 역할)로 히트맵 색과 클릭을 연결 */
function GlbModel({ layout, colorFor, onSelect }: {
  layout: NonNullable<Layout["model"]>; colorFor: (r: Role) => Color | null; onSelect: (r: Role) => void;
}) {
  const { scene } = useGLTF(layout.url);
  const roleOf = (name: string) =>
    Object.entries(layout.bindings).find(([prefix]) => name.startsWith(prefix))?.[1] ?? null;
  useEffect(() => {
    scene.traverse((o) => {
      const m = o as Mesh;
      if (!m.isMesh) return;
      m.castShadow = m.receiveShadow = true;
      const role = roleOf(m.name);
      const c = role && colorFor(role);
      if (c) {
        if (!m.userData.hvacCloned) {   // 원본 GLB 재질을 공유하지 않도록 한 번만 복제
          m.material = (m.material as MeshStandardMaterial).clone();
          m.userData.hvacCloned = true;
        }
        (m.material as MeshStandardMaterial).color.copy(c);
      }
    });
  });
  return (
    <primitive object={scene} scale={layout.scale ?? 1}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        const role = roleOf(e.object.name);
        if (role) { e.stopPropagation(); onSelect(role); }
      }} />
  );
}

/** 다중 존: 지금 보는 전산실 기준으로 같은 층·위층 존을 반투명 상자로 (적층 구조 표시).
 *  지금 보는 전산실을 지면에 그리므로 아래층은 그리지 않음 — 층 구성은 '존별 결과' 단면도 */
function ZoneGhosts({ sc }: { sc: Scenario }) {
  const src = sc.building?.source ?? "";
  const z0 = src.startsWith("zone:") ? sc.zones?.find((z) => z.id === src.slice(5)) : undefined;
  if (!sc.zones || !z0) return null;
  const g0 = z0.geometry;
  return <>{sc.zones.filter((z) => z.id !== z0.id && z.floor >= z0.floor).map((z) => {
    const g = z.geometry;
    const pos: Vec3 = [(g.x_m ?? 0) - (g0.x_m ?? 0), (z.floor - z0.floor) * g0.height_m + g.height_m / 2, (g.z_m ?? 0) - (g0.z_m ?? 0)];
    return (
      <group key={z.id}>
        <mesh position={pos}>
          <boxGeometry args={[g.ew_m - 0.4, g.height_m - 0.2, g.ns_m - 0.4]} />
          <meshStandardMaterial color={ZONE_COLOR[z.kind]} transparent opacity={0.06} depthWrite={false} />
          <Edges color={ZONE_COLOR[z.kind]} />
        </mesh>
        <Html position={pos} center style={{ pointerEvents: "none" }}>
          <span style={{ color: "#fff", fontSize: 12, fontWeight: 600, textShadow: "0 0 4px #000", whiteSpace: "nowrap" }}>
            {z.name}</span>
        </Html>
      </group>
    );
  })}</>;
}

class GlbBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { err: boolean }> {
  state = { err: false };
  static getDerivedStateFromError() { return { err: true }; }
  render() { return this.state.err ? this.props.fallback : this.props.children; }
}

export function Scene3D({ result, scenario, clock, cooling, coolingLayout, layout = LAYOUT }: {
  result: SimulationResult; scenario: Scenario; clock: Clock;
  cooling: EvaluateResult | null; coolingLayout: CoolingLayout; layout?: Layout;
}) {
  const minute = useSmoothMinute(clock);
  const h = useMemo(() => lerpResult(result.hourly, minute, result.step_min), [result, minute]);
  const fb = useMemo(() => buildingDims(scenario, layout), [scenario, layout]);
  const geo = cooling?.geometry;
  const ew = geo?.building.ew_m ?? fb.ew, ns = geo?.building.ns_m ?? fb.ns, bh = geo?.building.h_m ?? fb.h;
  const cl = coolingLayout;
  const rackH = layout.visual.rack_height_m;
  const crahSize = layout.visual.crah_size_m;

  const [sel, setSel] = useState<Sel | null>(null);
  const [env, setEnv] = useState<EnvMode>("cutaway");
  const [rackMode, setRackMode] = useState<RackMode>("inlet");
  const [showAir, setShowAir] = useState(true);
  const [showPipes, setShowPipes] = useState(true);
  const [showLegend, setShowLegend] = useState(true);
  const [hq, setHq] = useState(true);
  // 다중 존: '건물 전체'(기본) / '전산실 상세'(기존 화면). 층 선택 시 다른 층 = 반투명 또는 숨김, 단면 = 선택 층 위 숨김
  const bv = cooling?.building_view ?? null;
  const [view, setView] = useState<"building" | "hall">("building");
  const [floorSel, setFloorSel] = useState<number | null>(null);
  const [others, setOthers] = useState<"ghost" | "hidden">("ghost");
  const [section, setSection] = useState(false);
  const bmode = !!bv && view === "building";
  useEffect(() => setSel(null), [cooling, view]);     // 배치·보기가 바뀌면 선택 해제 (인덱스 무효)

  // 시각 보간: 랙 흡입온도 / 연성 모델 시간값
  const t = h.t_h;
  const hr = Math.floor(t) % 24, frac = t - Math.floor(t);
  const inlets = useMemo(() => {
    if (!cooling) return [];
    const a = cooling.rack_inlet_c[hr], b = cooling.rack_inlet_c[(hr + 1) % 24];
    return a.map((x, i) => x + (b[i] - x) * frac);
  }, [cooling, hr, frac]);
  const ph = useMemo(() => (cooling ? lerpHour(cooling.hourly, t) : null), [cooling, t]);
  const binlets = useMemo(() => (bv ? bv.halls.map((hl) => {
    const a = hl.rack_inlet_c[hr], b = hl.rack_inlet_c[(hr + 1) % 24];
    return a.map((x, i) => x + (b[i] - x) * frac);
  }) : []), [bv, hr, frac]);
  const allInlets = bmode ? binlets.flat() : inlets;
  const maxInletNow = allInlets.length ? Math.max(...allInlets) : NaN;
  const overNow = allInlets.filter((x) => x > cl.inlet_limit_c).length;

  const dir = sunDir(h);
  const alt = h.sun.altitude_deg;
  const day = Math.max(0, Math.sin((alt * Math.PI) / 180));
  const sunI = alt > 0 ? 0.4 + 2.2 * Math.min(h.weather.ghi_w_m2 / 900, 1) : 0;
  const sky = new Color("#0b1220").lerp(new Color("#8fb8de"), Math.min(1, day * 1.6));
  const util = scenario.it_load.design_kw > 0 ? h.load_kw.it / scenario.it_load.design_kw : 0;
  const bd = useMemo(() => (bv ? buildingDimsOf(bv) : null), [bv]);
  const vis = useCallback((f: number): FloorVis => {
    if (floorSel == null || f === floorSel) return "show";
    if (section && f > floorSel) return "hidden";
    return others === "hidden" ? "hidden" : "ghost";
  }, [floorSel, section, others]);
  const cutY = useMemo(() => {
    const f = bv?.floors.find((x) => x.floor === floorSel);
    return section && f ? f.elevation_m + f.height_m - 0.05 : Infinity;
  }, [bv, floorSel, section]);
  const maxRej = useMemo(() => Math.max(...result.hourly.map((x) => x.condenser_water.heat_rejection_kw), 1), [result]);
  const plantX = geo ? geo.chiller.pos[0] : ew / 2 + 16;
  const walls = wallGeoms(ew, ns, bh);
  const chwRatio = cooling && ph ? ph.chw_flow_m3_h / cooling.piping.chw_design_flow_m3_h : 1;
  const cwRatio = cooling && ph ? ph.cw_flow_m3_h / cooling.piping.cw_design_flow_m3_h : 1;
  const fwsNow = h.plant.liquid_loop?.fws_flow_m3_h ?? 0;
  const fwsRatio = bv?.piping.fws_design_flow_m3_h ? fwsNow / bv.piping.fws_design_flow_m3_h : 0;
  const ratio = useCallback((k: PipeKind) => (k.startsWith("chw") ? chwRatio : k.startsWith("cw") ? cwRatio : fwsRatio),
    [chwRatio, cwRatio, fwsRatio]);
  const pipeHidden = useCallback((s: BPipeSegment) => s.floor != null && vis(s.floor) === "hidden", [vis]);

  // 펌프 위치: 냉수 펌프는 칠러 서측 트렁크 옆, 냉각수 펌프는 냉각탑 수조 → 칠러 공급관 옆
  const pumps = geo && {
    chw: [geo.chiller.pos[0] - geo.chiller.size[0] / 2 - 2.6, 0, geo.chiller.pos[2] - 1.4] as Vec3,
    cw: [geo.tower.pos[0] - geo.tower.size[0] / 2 - 0.4,
      0, (geo.tower.pos[2] - geo.tower.size[2] / 2 + geo.chiller.pos[2] + geo.chiller.size[2] / 2) / 2] as Vec3,
  };

  const selPos: Vec3 | null = !sel ? null : (() => {
    switch (sel.kind) {
      case "wall": return walls.find((w) => w.o === sel.o)!.label;
      case "roof": return [0, bh + 1, 0];
      case "rack": { const r = cooling!.airflow.racks[sel.i]; return [r.x_m, rackH + 0.5, r.z_m]; }
      case "crah": { const c = cooling!.airflow.crah[sel.i]; return [c.x_m, crahSize[1] + 0.6, c.z_m]; }
      case "chiller": return geo ? [geo.chiller.pos[0], geo.chiller.size[1] + 1, geo.chiller.pos[2]] : [plantX, 4, 0];
      case "tower": return geo ? [geo.tower.pos[0], geo.tower.size[1] + 2, geo.tower.pos[2]] : [plantX, 6, 0];
      case "pipe": { const p = cooling!.piping.segments[sel.i].points; return p[Math.floor(p.length / 2)] as Vec3; }
      case "pump": return pumps ? [pumps[sel.which][0], 1.5, pumps[sel.which][2]] : [plantX, 2, 0];
      case "brack": { const r = bv!.halls[sel.h].racks[sel.i]; return [r.x, r.y + rackH + 0.5, r.z]; }
      case "bcrah": { const c = bv!.halls[sel.h].crah[sel.i]; return [c.x, c.y + crahSize[1] + 0.6, c.z]; }
      case "bunit": { const u = bv!.plant[sel.which][sel.i]; return [u.pos[0], u.size[1] + 1.5, u.pos[2]]; }
      case "bcdu": { const c = bv!.plant.cdu[sel.i]; return [c.pos[0], c.pos[1] + c.size[1] / 2 + 0.5, c.pos[2]]; }
      case "bpipe": { const p = bv!.piping.segments[sel.i].points; return p[Math.floor(p.length / 2)] as Vec3; }
    }
  })();

  const roleColor = (r: Role): Color | null =>
    r.startsWith("wall:") ? solarColor(h.surface_solar_w_m2[r.slice(5) as Orientation])
      : r === "roof" ? solarColor(h.surface_solar_w_m2.roof) : r === "racks" ? utilColor(util) : null;
  const roleSel = (r: Role): Sel | null =>
    r.startsWith("wall:") ? { kind: "wall", o: r.slice(5) as Orientation }
      : r === "roof" ? { kind: "roof" } : r === "chiller" ? { kind: "chiller" } : r === "tower" ? { kind: "tower" } : null;

  const building = (
    <Building ew={ew} ns={ns} h={bh} scenario={scenario} hour={h} mode={env}
      onWall={(o) => setSel({ kind: "wall", o })} onRoof={() => setSel({ kind: "roof" })} />
  );

  return (
    <div className="scene-wrap">
      <Canvas shadows="percentage" dpr={hq ? [1, 2] : 1} key={bmode ? "b" : "h"}
        camera={{ position: bd && bmode ? [bd.plantX * 1.1, bd.top + bd.ew * 0.75, bd.ns * 1.9] : [ew * 1.05, ew * 0.8, ns * 1.5],
          fov: 42, near: 0.3, far: 2000 }}
        onPointerMissed={() => setSel(null)}>
        <color attach="background" args={[sky]} />
        <fog attach="fog" args={[sky, 180, 700]} />
        <hemisphereLight args={["#cfe0ff", "#3a3a30", 0.25 + 0.55 * day]} />
        <ambientLight intensity={0.22} />
        <directionalLight position={[dir[0] * 150, dir[1] * 150, dir[2] * 150]} intensity={sunI} castShadow
          shadow-mapSize={hq ? [4096, 4096] : [1024, 1024]} shadow-camera-left={-110} shadow-camera-right={110}
          shadow-camera-top={110} shadow-camera-bottom={-110} shadow-camera-far={400} shadow-bias={-0.0004} />
        {alt > -3 && (
          <mesh position={[dir[0] * 160, dir[1] * 160, dir[2] * 160]}>
            <sphereGeometry args={[4, 16, 12]} />
            <meshBasicMaterial color={alt > 0 ? "#ffe59a" : "#ff9a5a"} />
          </mesh>
        )}

        {bmode && bv && bd ? <Ground ew={bd.ew} ns={bd.ns} plantX={bd.plantX} /> : <Ground ew={ew} ns={ns} plantX={plantX} />}
        {bmode && bv && cooling && <>
          <BuildingShell bv={bv} scenario={scenario} hour={h} env={env} vis={vis}
            onWall={(o) => setSel({ kind: "wall", o })} onRoof={() => setSel({ kind: "roof" })} />
          {bv.halls.map((hl, i) => {
            const v = vis(hl.floor);
            const zr = scenario.zones?.find((z) => z.id === hl.zone)?.racks;
            return v === "hidden" ? null : (
              <HallInBuilding key={hl.zone} bv={bv} h={i} layout={zr ? { ...cl, racks: zr } : cl} inlets={binlets[i] ?? []}
                mode={rackMode} rackH={rackH} crahSize={crahSize} ghost={v === "ghost"}
                onRack={(j) => setSel({ kind: "brack", h: i, i: j })} onCrah={(j) => setSel({ kind: "bcrah", h: i, i: j })} />
            );
          })}
          <Cdus bv={bv} vis={vis} onCdu={(i) => setSel({ kind: "bcdu", i })} />
          <YardPlant bv={bv} running={{ chiller: cooling.units?.chiller?.running[hr] ?? bv.plant.chiller.length,
            tower: cooling.units?.tower?.running[hr] ?? bv.plant.tower.length }}
            towerLoad={h.condenser_water.heat_rejection_kw / maxRej} evapM3h={h.makeup_water_m3_h.evaporation}
            onUnit={(which, i) => setSel({ kind: "bunit", which, i })} />
          {showPipes && <BuildingPipes segments={bv.piping.segments} ratio={ratio} cutY={cutY} hidden={pipeHidden}
            onSegment={(i) => setSel({ kind: "bpipe", i })} />}
          <ZoneLabels bv={bv} vis={vis} />
        </>}
        {!bmode && <>
        <ZoneGhosts sc={scenario} />
        {layout.model ? (
          <GlbBoundary fallback={building}>
            <Suspense fallback={building}>
              <GlbModel layout={layout.model} colorFor={roleColor}
                onSelect={(r) => { const s = roleSel(r); if (s) setSel(s); }} />
            </Suspense>
          </GlbBoundary>
        ) : building}

        {cooling && <>
          <RaisedFloor ew={ew} ns={ns} af={cooling.airflow} layout={cl} />
          <Racks af={cooling.airflow} layout={cl} inlets={inlets} mode={rackMode} height={rackH}
            onRack={(i) => setSel({ kind: "rack", i })} />
          <ContainmentPanels af={cooling.airflow} layout={cl} kind={cl.containment} rackH={rackH} ceilingH={bh} />
          <CableTrays af={cooling.airflow} layout={cl} rackH={rackH} />
          <Crahs af={cooling.airflow} size={crahSize} onCrah={(i) => setSel({ kind: "crah", i })} />
          {(env === "cutaway" || env === "hidden") && <CeilingLights af={cooling.airflow} layout={cl} ceilingH={bh} />}
          {showAir && <AirflowParticles af={cooling.airflow} layout={cl} inlets={inlets} rackH={rackH}
            crahH={crahSize[1]} ceilingH={bh} />}
          <Chiller box={cooling.geometry.chiller} onClick={() => setSel({ kind: "chiller" })} />
          <CoolingTower box={cooling.geometry.tower} load={h.condenser_water.heat_rejection_kw / maxRej}
            onClick={() => setSel({ kind: "tower" })} />
          <Steam origin={[cooling.geometry.tower.pos[0], cooling.geometry.tower.size[1] + 1.4, cooling.geometry.tower.pos[2]]}
            spread={cooling.geometry.tower.size[0] * 0.55} evapM3h={h.makeup_water_m3_h.evaporation} />
          {pumps && <>
            <Pump pos={pumps.chw} color={PIPE.chwSupply} onClick={() => setSel({ kind: "pump", which: "chw" })} />
            <Pump pos={pumps.cw} color={PIPE.cwSupply} onClick={() => setSel({ kind: "pump", which: "cw" })} />
          </>}
          {showPipes && <>
            <PipeNetwork segments={cooling.piping.segments} chwRatio={chwRatio} cwRatio={cwRatio}
              onSegment={(i) => (e) => { e.stopPropagation(); setSel({ kind: "pipe", i }); }} />
            <PipeRacks segments={cooling.piping.segments} />
          </>}
        </>}
        </>}

        {!bmode && walls.map((w) => (
          <Html key={w.o} position={w.label} center style={{ pointerEvents: "none" }}>
            <span style={{ color: "#fff", fontSize: 12, fontWeight: 700, textShadow: "0 0 4px #000" }}>{w.o}</span>
          </Html>
        ))}
        {sel && selPos && (
          <Html position={selPos} style={{ pointerEvents: "none", transform: "translate(12px, -50%)" }} zIndexRange={[30, 0]}>
            <div className="tooltip3d">
              {bv && cooling && sel.kind.startsWith("b")
                ? <BTooltipBody sel={sel as BSel} h={h} bv={bv} cooling={cooling} cl={cl} binlets={binlets} ph={ph} ratio={ratio} />
                : <TooltipBody sel={sel} h={h} sc={scenario} cooling={cooling} cl={cl} inlets={inlets} ph={ph} />}
            </div>
          </Html>
        )}
        <OrbitControls target={bd && bmode ? [(bd.x0 + bd.plantX) / 2 * 0.6, bd.top * 0.35, 0] : [plantX / 4, 2, 0]}
          maxPolarAngle={Math.PI / 2.05} minDistance={6} maxDistance={600} />
      </Canvas>

      <div className="scene-controls">
        {bv && <label>보기{" "}
          <select value={view} onChange={(e) => setView(e.target.value as "building" | "hall")}>
            <option value="building">건물 전체</option>
            <option value="hall">전산실 상세</option>
          </select>
        </label>}
        {bmode && bv && <>
          <span className="seg">
            <button className={floorSel == null ? "on" : ""} onClick={() => setFloorSel(null)}>전체</button>
            {bv.floors.map((f) => (
              <button key={f.floor} className={floorSel === f.floor ? "on" : ""} onClick={() => setFloorSel(f.floor)}>
                {f.floor + 1}F</button>
            ))}
          </span>
          <label>다른 층{" "}
            <select value={others} disabled={floorSel == null} onChange={(e) => setOthers(e.target.value as "ghost" | "hidden")}>
              <option value="ghost">반투명</option>
              <option value="hidden">숨김</option>
            </select>
          </label>
          <label title="선택 층 위를 숨김"><input type="checkbox" checked={section} disabled={floorSel == null}
            onChange={(e) => setSection(e.target.checked)} /> 단면</label>
        </>}
        <label>외피{" "}
          <select value={env} onChange={(e) => setEnv(e.target.value as EnvMode)}>
            <option value="cutaway">단면 (실내 보기)</option>
            <option value="exterior">실제 외관</option>
            <option value="solar">일사 히트맵</option>
            <option value="hidden">숨김</option>
          </select>
        </label>
        <label>랙 색{" "}
          <select value={rackMode} onChange={(e) => setRackMode(e.target.value as RackMode)}>
            <option value="inlet">흡입온도</option>
            <option value="kw">IT 전력</option>
            <option value="real">실제</option>
          </select>
        </label>
        {!bmode && <label><input type="checkbox" checked={showAir} onChange={(e) => setShowAir(e.target.checked)} /> 기류</label>}
        <label><input type="checkbox" checked={showPipes} onChange={(e) => setShowPipes(e.target.checked)} /> 배관</label>
        <label><input type="checkbox" checked={hq} onChange={(e) => setHq(e.target.checked)} /> 고품질</label>
        <label><input type="checkbox" checked={showLegend} onChange={(e) => setShowLegend(e.target.checked)} /> 범례</label>
      </div>
      <div className="scene-overlay">
        <div className="box">
          <div>{hhmm(h.t_h)} · 태양 고도 {fmt(alt)}° · 방위 {fmt(h.sun.azimuth_deg)}° (남=0, 서=+)</div>
          {bmode && bv && cooling && <div>
            건물 {bv.floors.length}층 · 전산실 {bv.halls.length}개 · 랙 {bv.halls.reduce((s, x) => s + x.racks.length, 0)}개 ·
            칠러 {bv.plant.chiller.length}대 · 냉각탑 {bv.plant.tower.length}대{bv.plant.cdu.length ? ` · CDU ${bv.plant.cdu.length}대` : ""}
            {" "}· 냉수 양정 {fmt(bv.piping.chw_head_kpa, 0)} kPa (입상관 마찰 {fmt(bv.piping.riser_friction_kpa, 1)} kPa)
          </div>}
          {cooling && ph ? <div>
            랙 최대 흡입 <b style={{ color: maxInletNow > cl.inlet_limit_c ? "#ff8a80" : "#a7f3d0" }}>{fmt(maxInletNow, 1)}℃</b>
            {" "}(상한 {fmt(cl.inlet_limit_c, 0)}℃, 초과 {overNow}개) · 냉방 전력 {fmt(ph.total_kw, 0)} kW
          </div> : <div style={{ color: "#ffb4a8" }}>냉각 구조 평가 결과 없음 (랙·설비·배관 미표시)</div>}
          <div style={{ color: "#b9bec8" }}>객체를 클릭하면 수치 표시 · 드래그로 회전 · 휠로 확대</div>
        </div>
        {showLegend && <>
          <div className="box">
            {rackMode === "inlet" && <>
              <div>랙 색 = 흡입온도 [℃]</div>
              <div className="gradient" style={{ background: cssGradient("temp") }} />
              <div className="scale-labels"><span>{INLET_MIN}</span><span>{(INLET_MIN + INLET_MAX) / 2}</span><span>{INLET_MAX}</span></div>
            </>}
            {rackMode === "kw" && <>
              <div>랙 색 = IT 전력 (피크 시각) [kW/랙]</div>
              <div className="gradient" style={{ background: cssGradient("util") }} />
              <div className="scale-labels"><span>0</span><span>{KW_MAX / 2}</span><span>{KW_MAX}+</span></div>
            </>}
            {showAir && !bmode && <>
              <div style={{ marginTop: 4 }}>기류 입자 색 = 공기온도 [℃]</div>
              <div className="gradient" style={{ background: cssGradient("temp") }} />
              <div className="scale-labels"><span>{AIR_MIN}</span><span>{(AIR_MIN + AIR_MAX) / 2}</span><span>{AIR_MAX}</span></div>
            </>}
            {env === "solar" && <>
              <div style={{ marginTop: 4 }}>외피 입사 일사량 [W/m²]</div>
              <div className="gradient" style={{ background: cssGradient("solar") }} />
              <div className="scale-labels"><span>0</span><span>{SOLAR_MAX / 2}</span><span>{SOLAR_MAX}+</span></div>
            </>}
          </div>
          <div className="box">
            <div><span style={{ color: PIPE.chwSupply }}>■</span> 냉수 공급 <span style={{ color: PIPE.chwReturn }}>■</span> 냉수 환수{" "}
              <span style={{ color: PIPE.cwSupply }}>■</span> 냉각수 공급 <span style={{ color: PIPE.cwReturn }}>■</span> 냉각수 환수
              {bmode && bv?.piping.fws_design_flow_m3_h ? <>{" "}<span style={{ color: PIPE.fwsSupply }}>■</span> FWS 공급{" "}
                <span style={{ color: PIPE.fwsReturn }}>■</span> FWS 환수</> : null}</div>
            {bmode && bv && <div style={{ color: "#b9bec8" }}>
              <span style={{ color: ZONE_COLOR.data_hall }}>■</span> 전산실 <span style={{ color: ZONE_COLOR.support }}>■</span> 지원{" "}
              <span style={{ color: ZONE_COLOR.office }}>■</span> 사무 (바닥 존 경계) · 칠러 위 점: 초록 운전 · 주황 대기 · 회색 예비
            </div>}
            <div style={{ color: "#b9bec8" }}>
              배관 굵기 = 선정 호칭경(냉수관 보온 50 mm 포함) · 흐름 점 속도 = 관내 유속 × {PIPE_SPEED_SCALE} ·
              수증기 입자 수 = 증발량 × {STEAM_PER_M3H}
            </div>
            {cooling && !bmode && <div style={{ color: "#b9bec8" }}>
              {CONT_LABEL[cl.containment]} · 냉수 {TOPO_LABEL[cooling.piping.topology]} · 랙 {cooling.airflow.metrics.rack_count}개 ·
              CRAH {cooling.airflow.crah.length}대
            </div>}
            {bmode && bv && bd ? <div style={{ color: "#b9bec8" }}>
              절차적 모델 · 건물 {fmt(bd.ew, 0)}×{fmt(bd.ns, 0)} m, 높이 {fmt(bd.top, 0)} m (층고 누적) · 설비·헤더·입상관 위치는
              {bv.plant.source.chiller === "cad" ? " 도면" : " 자동 배치(가정)"} · 랙 색은 각 전산실 기류 모델 결과
            </div> : <div style={{ color: "#b9bec8" }}>
              절차적 모델(실제 도면 아님) · 건물 {fmt(ew, 0)}×{fmt(ns, 0)}×{fmt(bh, 0)} m
              {scenario.building?.source?.startsWith("zone:") ? ` (${scenario.zones?.find((z) => z.id === scenario.building!.source!.slice(5))?.name ?? "존"} 평면, 다중 존)`
                : fb.derived ? " (외피 면적에서 역산)" : " (기본값)"} · 기류 입자는 저차원 모델 개념도(CFD 아님)
            </div>}
          </div>
        </>}
      </div>
    </div>
  );
}
