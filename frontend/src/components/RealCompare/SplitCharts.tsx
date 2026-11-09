import { useMemo, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import type { RealCompare, RealHourly, RunKey } from "../../types/api";
import { fmt } from "../../lib/format";
import { RUN_COLOR, itemLabel, itemsOf, runsOf, signed } from "./common";

type Unit = "month" | "day" | "week";
interface Pt { x: number | string; label: string; meas: number | null; sim: number | null }

const axis = { stroke: "var(--text-muted)", fontSize: 10.5, tickLine: false };
const MAX_SCATTER = 1500;
const digitsOf = (item: string) => (item === "pue" ? 4 : 2);
const unitOf = (item: string) => (item === "pue" ? "" : " kW");

/** 한 칸의 점: 월·일 평균 또는 임의 1주 시간값 */
function points(c: RealCompare, k: RunKey, item: string, unit: Unit, week: number): Pt[] {
  if (unit !== "week") {
    const rows = (unit === "month" ? c.monthly : c.daily)[k] ?? [];
    return rows.map((r) => ({ x: String(r.t), label: String(r.t), meas: Number(r[`meas_${item}`]), sim: Number(r[`sim_${item}`]) }));
  }
  const h = c.hourly[k];
  if (!h?.start) return [];
  const t0 = new Date(h.start.replace(" ", "T")).getTime();
  const lo = week * 7 * 24, out: Pt[] = [];
  h.offset_h.forEach((o, i) => {
    if (o < lo || o >= lo + 168) return;
    const d = new Date(t0 + o * 3600e3);
    out.push({ x: o - lo, label: `${d.toISOString().slice(0, 10)} ${String(d.getUTCHours()).padStart(2, "0")}시`,
      meas: h.meas[item]?.[i] ?? null, sim: h.sim[item]?.[i] ?? null });
  });
  return out;
}

function CellTip({ active, payload, runLabel, item }: {
  active?: boolean; payload?: readonly { payload?: unknown }[]; runLabel: string; item: string;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload as Pt;
  const d = digitsOf(item), u = unitOf(item);
  const diff = p.meas != null && p.sim != null ? p.sim - p.meas : null;
  return (
    <div className="tip">
      <div className="t">{p.label}</div>
      <div className="r"><span>실측</span><span>{fmt(p.meas, d)}{u}</span></div>
      <div className="r"><span>{runLabel}</span><span>{fmt(p.sim, d)}{u}</span></div>
      <div className="r" style={{ borderTop: "1px solid var(--grid)", marginTop: 3, paddingTop: 3 }}>
        <span>차이 (시뮬 − 실측)</span><span>{signed(diff, d)}{u}</span></div>
    </div>
  );
}

function Cell({ c, k, item, data, domain, unit }: {
  c: RealCompare; k: RunKey; item: string; data: Pt[]; domain: [number, number]; unit: Unit;
}) {
  const run = c.runs[k]!;
  const diff = item === "pue" ? `PUE 차 ${signed(run.pue.diff, 4)}`
    : `평균 차 ${signed(((run.items[item].ratio_sim_over_meas ?? 1) - 1) * 100, 1)}%`;
  return (
    <div className="split-cell">
      <div className="split-head">
        <span><b>{run.label}</b> <span className="muted">{run.period.join(" ~ ")}</span></span>
        <span className="split-diff">{diff}</span>
      </div>
      <div className="legend">
        <span><span className="sw" style={{ background: RUN_COLOR.meas }} />실측</span>
        <span><span className="sw" style={{ background: RUN_COLOR[k] }} />{run.label}</span>
      </div>
      <div style={{ height: 150 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            {unit === "week"
              ? <XAxis dataKey="x" type="number" domain={[0, 168]} ticks={[0, 24, 48, 72, 96, 120, 144, 168]}
                tickFormatter={(v) => `${v / 24}일`} {...axis} />
              : <XAxis dataKey="x" {...axis} minTickGap={18} tickFormatter={(v: string) => (unit === "month" ? v.slice(2) : v.slice(5))} />}
            <YAxis domain={domain} width={44} {...axis} tickFormatter={(v) => fmt(v, item === "pue" ? 3 : 0)} allowDataOverflow />
            <Tooltip content={(p) => <CellTip {...p} runLabel={run.label} item={item} />} />
            <Line dataKey="meas" stroke={RUN_COLOR.meas} dot={false} strokeWidth={1.4} isAnimationActive={false} connectNulls={false} />
            <Line dataKey="sim" stroke={RUN_COLOR[k]} dot={false} strokeWidth={1.6} isAnimationActive={false} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** 소분할 격자: 행 = 항목(+ PUE), 열 = 실행. 칸마다 실측 + 해당 실행 두 선, 범례도 칸마다. 같은 행은 y축 공유 */
export function SplitCharts({ result }: { result: RealCompare }) {
  const [unit, setUnit] = useState<Unit>("month");
  const [week, setWeek] = useState(0);
  const runs = runsOf(result);
  const rows = [...itemsOf(result), "pue"];
  const weeks = Math.max(1, Math.min(...runs.map((k) => Math.floor((result.daily[k]?.length ?? 7) / 7))));

  const grid = useMemo(() => rows.map((item) => {
    const cells = runs.map((k) => ({ k, data: points(result, k, item, unit, Math.min(week, weeks - 1)) }));
    const vals = cells.flatMap((x) => x.data.flatMap((p) => [p.meas, p.sim])).filter((v): v is number => v != null && Number.isFinite(v));
    const lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
    const pad = (hi - lo) * 0.06 || (item === "pue" ? 0.005 : 1);
    const domain: [number, number] = [item === "pue" ? lo - pad : Math.max(0, lo - pad), hi + pad];
    return { item, cells, domain };
  }), [result, unit, week, weeks]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="row">
        <span className="seg">{([["month", "월 평균"], ["day", "일 평균"], ["week", "1주 시간값"]] as const).map(([u, l]) =>
          <button key={u} className={unit === u ? "on" : ""} onClick={() => setUnit(u)}>{l}</button>)}</span>
        {unit === "week" && <label>주 선택 (각 기간 시작부터) <input type="range" min={0} max={weeks - 1} value={Math.min(week, weeks - 1)}
          onChange={(e) => setWeek(Number(e.target.value))} aria-label="주 선택" /> {Math.min(week, weeks - 1) + 1}주째</label>}
        <span className="muted">같은 행은 y축 범위 공유 · 이중 축 없음 · 머리줄 차이 = 기간 전체 평균 기준</span>
      </div>
      <div className="split-grid" style={{ gridTemplateColumns: `90px repeat(${runs.length}, minmax(0, 1fr))` }}>
        {grid.map((g) => <div className="split-row" key={g.item}>
          <div className="split-label">{itemLabel(result, g.item)}</div>
          {g.cells.map((x) => <Cell key={x.k} c={result} k={x.k} item={g.item} data={x.data} domain={g.domain} unit={unit} />)}
        </div>)}
      </div>
    </div>
  );
}

// ---------- 산점도 ----------
function thin<T>(a: T[]): T[] {
  if (a.length <= MAX_SCATTER) return a;
  const step = a.length / MAX_SCATTER;
  return Array.from({ length: MAX_SCATTER }, (_, i) => a[Math.floor(i * step)]);
}

function ScatterTip({ active, payload, xl, yl }: { active?: boolean; payload?: readonly { payload?: unknown }[]; xl: string; yl: string }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload as { x: number; y: number };
  return <div className="tip"><div className="r"><span>{xl}</span><span>{fmt(p.x, 2)} kW</span></div>
    <div className="r"><span>{yl}</span><span>{fmt(p.y, 2)} kW</span></div></div>;
}

function ScatterCell({ title, pts, color, domX, domY, xl, yl, oneToOne }: {
  title: string; pts: { x: number; y: number }[]; color: string; domX: [number, number]; domY: [number, number];
  xl: string; yl: string; oneToOne?: boolean;
}) {
  return (
    <div className="split-cell">
      <div className="split-head"><b>{title}</b><span className="muted">n = {pts.length.toLocaleString()}</span></div>
      <div style={{ height: 200 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 4, right: 8, bottom: 14, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" />
            <XAxis dataKey="x" type="number" domain={domX} {...axis} tickFormatter={(v) => fmt(v, 0)}
              label={{ value: xl, position: "insideBottom", offset: -8, fontSize: 10.5, fill: "var(--text-muted)" }} />
            <YAxis dataKey="y" type="number" domain={domY} width={44} {...axis} tickFormatter={(v) => fmt(v, 0)} />
            {oneToOne && <ReferenceLine segment={[{ x: domX[0], y: domX[0] }, { x: domX[1], y: domX[1] }]} stroke="var(--text-muted)"
              strokeDasharray="4 3" ifOverflow="hidden" />}
            <Tooltip content={(p) => <ScatterTip {...p} xl={xl} yl={yl} />} />
            <Scatter data={pts} fill={color} fillOpacity={0.35} shape="circle" isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const span = (vals: number[]): [number, number] => {
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.05 || 1;
  return [Math.max(0, Math.floor(lo - pad)), Math.ceil(hi + pad)];
};

/** 항목별 실측 vs 시뮬 시간값 + 1:1 선, IT vs 기계 전력 (실측 / 시뮬 나란히 — 고정분 구조) */
export function ScatterCharts({ result }: { result: RealCompare }) {
  const runs = runsOf(result);
  const [k, setK] = useState<RunKey>("B");
  const h: RealHourly | undefined = result.hourly[k];
  const items = itemsOf(result);
  if (!h) return null;
  const run = result.runs[k]!;
  const pair = (item: string) => thin(h.meas[item].map((m, i) => ({ x: m, y: h.sim[item][i] })));
  const itMeas = thin(h.it_kw.map((x, i) => ({ x, y: h.meas.mech_total[i] })));
  const itSim = thin(h.sim_it_kw.map((x, i) => ({ x, y: h.sim.mech_total[i] })));
  const domIt = span([...itMeas, ...itSim].map((p) => p.x));
  const domMech = span([...itMeas, ...itSim].map((p) => p.y));
  return (
    <div>
      <div className="row">
        <span>실행</span>
        <span className="seg">{runs.map((r) => <button key={r} className={k === r ? "on" : ""} onClick={() => setK(r)}>
          {result.runs[r]!.label}</button>)}</span>
        <span className="muted">{run.period.join(" ~ ")} · 점이 많으면 고르게 {MAX_SCATTER.toLocaleString()}개만 표시</span>
      </div>
      <h3 className="sub-h">항목별 실측 vs 시뮬 (시간값, 점선 = 1:1)</h3>
      <div className="scatter-grid">
        {items.map((it) => {
          const p = pair(it);
          const d = span(p.flatMap((q) => [q.x, q.y]));
          return <ScatterCell key={it} title={itemLabel(result, it)} pts={p} color={RUN_COLOR[k]} domX={d} domY={d}
            xl="실측 kW" yl={`${run.label} kW`} oneToOne />;
        })}
      </div>
      <h3 className="sub-h">IT 전력 vs 기계 전력 합계 (같은 축) — 실측은 IT 가 늘어도 덜 늘면 고정분이 큰 구조</h3>
      <div className="scatter-grid two">
        <ScatterCell title="실측" pts={itMeas} color={RUN_COLOR.meas} domX={domIt} domY={domMech} xl="IT kW" yl="기계 합계 kW" />
        <ScatterCell title={run.label} pts={itSim} color={RUN_COLOR[k]} domX={domIt} domY={domMech} xl="IT kW" yl="기계 합계 kW" />
      </div>
    </div>
  );
}
