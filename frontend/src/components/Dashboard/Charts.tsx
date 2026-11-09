import { useMemo, useState, type ReactNode } from "react";
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { ModeReason, SimulationResult } from "../../types/api";
import { LOAD_GROUPS, fmt, groupLoads, hhmm } from "../../lib/format";
import { MODE_LABEL } from "../../lib/fields";

/** 현실성 R1: 운전 상태를 정한 이유 (칠러 전력 툴팁) */
export const REASON_LABEL: Record<ModeReason, string> = {
  threshold: "전환점 판정", hysteresis_hold: "이력 구간 — 직전 상태 유지", min_on: "최소 운전 시간 — 칠러 최소 부하 유지",
  min_off: "최소 정지 시간 — 재기동 대기 (판형만, 공급온도 상승)", staging: "대수 변경 간격 대기",
};

export interface Series { key: string; label: string; color: string; digits?: number }
type Row = Record<string, number>;

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };
const grid = <CartesianGrid stroke="var(--grid)" vertical={false} />;
// 연속 시간축 (x = 시각[h], 0~24)
const timeAxis = (
  <XAxis dataKey="t" type="number" domain={[0, 24]} ticks={[0, 3, 6, 9, 12, 15, 18, 21, 24]} {...axis} />
);

function Legend({ series }: { series: Series[] }) {
  return (
    <div className="legend">
      {series.map((s) => <span key={s.key}><span className="sw" style={{ background: s.color }} />{s.label}</span>)}
    </div>
  );
}

function makeTip(series: Series[], unit: string, total?: (row: Row) => number, extra?: (row: Row) => ReactNode) {
  return function Tip({ active, payload }: { active?: boolean; payload?: readonly { payload?: unknown }[] }) {
    if (!active || !payload?.length) return null;
    const row = payload[0].payload as Row;
    return (
      <div className="tip">
        <div className="t">{hhmm(row.t)}</div>
        {[...series].reverse().map((s) => (
          <div className="r" key={s.key}>
            <span><span className="sw" style={{ background: s.color, display: "inline-block", width: 8, height: 8, borderRadius: 2, marginRight: 5 }} />{s.label}</span>
            <span>{fmt(row[s.key], s.digits ?? 1)} {unit}</span>
          </div>
        ))}
        {total && <div className="r" style={{ borderTop: "1px solid var(--grid)", marginTop: 3, paddingTop: 3 }}>
          <span>합계</span><span>{fmt(total(row), series[0].digits ?? 1)} {unit}</span></div>}
        {extra?.(row)}
      </div>
    );
  };
}

/** 차트 카드: 범례 + 차트/표 전환 (색 대비가 낮은 계열이 있어 표 보기를 항상 제공). 표는 정시 값만 */
export function ChartCard({ title, unit, hint, series, rows, t, children }: {
  title: string; unit: string; hint?: string; series: Series[]; rows: Row[]; t: number; children: ReactNode;
}) {
  const [table, setTable] = useState(false);
  const onHour = rows.filter((r) => r.minute === 0);
  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2><span className="hint">{unit}{hint ? ` · ${hint}` : ""}</span>
        <span className="spacer" />
        <button className="btn small" onClick={() => setTable(!table)}>{table ? "차트" : "표(정시)"}</button>
      </div>
      {series.length > 1 && <Legend series={series} />}
      {table ? (
        <div className="table-scroll" style={{ height: 240 }}>
          <table className="data">
            <thead><tr><th>시각</th>{series.map((s) => <th key={s.key}>{s.label}</th>)}</tr></thead>
            <tbody>
              {onHour.map((r) => (
                <tr key={r.t} className={Math.floor(t) === r.hour ? "active" : ""}>
                  <td>{hhmm(r.t)}</td>{series.map((s) => <td key={s.key}>{fmt(r[s.key], s.digits ?? 1)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="chart-box">{children}</div>}
    </div>
  );
}

type ClickState = { activeLabel?: string | number } | null;
const clickTime = (onTime: (t_h: number) => void) => (s: ClickState) => {
  const v = Number(s?.activeLabel);
  if (s?.activeLabel != null && !Number.isNaN(v)) onTime(v);
};

/** 0부터 시작하는 고른 눈금 (약 4칸, 간격 1·2·2.5·5 x 10^n) */
const zeroTicks = (max: number) => {
  const rough = Math.max(max, 10) / 4;
  const p = 10 ** Math.floor(Math.log10(rough));
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * p >= rough) ?? 10) * p;
  const n = Math.ceil(Math.max(max, 10) / step);
  return Array.from({ length: n + 1 }, (_, i) => i * step);
};

const timeLine = (t: number) => (
  <ReferenceLine x={t} stroke="var(--text-primary)" strokeDasharray="3 3" strokeWidth={1.5} ifOverflow="extendDomain" />
);

export function Charts({ result, t, onTime }: { result: SimulationResult; t: number; onTime: (t_h: number) => void }) {
  const rows = useMemo(() => result.hourly.map((x, i) => ({
    i,
    t: x.t_h,
    hour: x.hour,
    minute: x.minute,
    ...groupLoads(x.load_kw),
    total: x.load_kw.total_cooling,
    chw: x.chilled_water.flow_m3_h,
    cw: x.condenser_water.flow_m3_h,
    evap: x.makeup_water_m3_h.evaporation,
    blow: x.makeup_water_m3_h.blowdown,
    drift: x.makeup_water_m3_h.drift,
    makeup: x.makeup_water_m3_h.total,
    power: x.chiller.power_kw,
  })) as Row[], [result]);

  const loadSeries: Series[] = LOAD_GROUPS.map((g) => ({ key: g.key, label: g.label, color: g.color }));
  const flowSeries: Series[] = [
    { key: "chw", label: "냉수", color: "var(--series-1)" },
    { key: "cw", label: "냉각수", color: "var(--series-2)" },
  ];
  const mkSeries: Series[] = [
    { key: "evap", label: "증발", color: "var(--series-1)", digits: 3 },
    { key: "blow", label: "블로다운", color: "var(--series-2)", digits: 3 },
    { key: "drift", label: "비산", color: "var(--series-3)", digits: 4 },
  ];
  const pwSeries: Series[] = [{ key: "power", label: "칠러 전력", color: "var(--series-1)" }];
  const pwTicks = zeroTicks(Math.max(...rows.map((r) => r.power)));
  const common = {
    data: rows, margin: { top: 8, right: 12, left: 0, bottom: 0 },
    onClick: clickTime(onTime), style: { cursor: "pointer" },
  };
  const stacked = (series: Series[]) => series.map((s) => (
    <Area key={s.key} dataKey={s.key} stackId="1" type="linear" fill={s.color} fillOpacity={0.9}
      stroke="var(--surface-1)" strokeWidth={1} isAnimationActive={false} />
  ));
  const lines = (series: Series[]) => series.map((s) => (
    <Line key={s.key} dataKey={s.key} type="linear" stroke={s.color} strokeWidth={2} dot={false}
      activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} isAnimationActive={false} />
  ));
  const card = { rows, t };
  const hint = `${result.step_min}분 간격`;

  return (
    <div className="chart-grid">
      <ChartCard title="냉방부하 구성" unit="kW" hint={`누적 · ${hint}`} series={loadSeries} {...card}>
        <ResponsiveContainer>
          <AreaChart {...common}>
            {grid}{timeAxis}<YAxis {...axis} width={52} />
            <Tooltip content={makeTip(loadSeries, "kW", (r) => r.total)} />
            {stacked(loadSeries)}
            {timeLine(t)}
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard title="냉수 / 냉각수 유량" unit="m³/h" hint={hint} series={flowSeries} {...card}>
        <ResponsiveContainer>
          <LineChart {...common}>
            {grid}{timeAxis}<YAxis {...axis} width={44} domain={["auto", "auto"]} />
            <Tooltip content={makeTip(flowSeries, "m³/h")} />
            {lines(flowSeries)}
            {timeLine(t)}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard title="보급수 구성" unit="m³/h" hint={`증발 + 블로다운 + 비산 · ${hint}`} series={mkSeries} {...card}>
        <ResponsiveContainer>
          <AreaChart {...common}>
            {grid}{timeAxis}<YAxis {...axis} width={40} />
            <Tooltip content={makeTip(mkSeries, "m³/h", (r) => r.makeup)} />
            {stacked(mkSeries)}
            {timeLine(t)}
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard title="칠러 전력" unit="kW" series={pwSeries} {...card}
        hint={result.control ? `제어 이력 반영 · 칠러 켜짐/꺼짐 ${Object.values(result.control.chiller_switches).reduce((a, b) => a + b, 0)}회`
          : "COP는 외기 습구온도·공급수온 선형 보정(가정 모델), 프리쿨링 시 감소"}>
        <ResponsiveContainer>
          <LineChart {...common}>
            {/* 0부터: 자동 축(최소~최대)은 ±10% 변동도 바닥~꼭대기로 과장해 부하 차트와 다르게 보임 */}
            {grid}{timeAxis}<YAxis {...axis} width={44} domain={[0, pwTicks[pwTicks.length - 1]]} ticks={pwTicks} />
            <Tooltip content={makeTip(pwSeries, "kW", undefined, (r) => {
              const p = result.hourly[r.i]?.plant;
              if (!p) return null;
              const over = p.air_loop.supply_overshoot_k;
              return <div className="muted" style={{ marginTop: 3, fontSize: 11.5 }}>
                {MODE_LABEL[p.mode]}{p.mode_reason ? ` · ${REASON_LABEL[p.mode_reason]}` : ""}
                {over ? ` · 냉수 +${fmt(over, 1)}K` : ""}</div>;
            })} />
            {lines(pwSeries)}
            {timeLine(t)}
          </LineChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  );
}
