import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CatalogItem, Economics, InvestResult, Measure, Scenario } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { DemoNote, Fold, JobBar, Lead, krw, useJob, usePersist } from "../../lib/ui";
import { RegionNote } from "../Import/RegionPanel";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const ECO_FIELDS: [keyof Economics, string, number][] = [
  ["electricity_krw_per_kwh", "전기요금 [원/kWh]", 1], ["water_krw_per_m3", "물 요금 [원/m³]", 10],
  ["discount_rate", "할인율 [-]", 0.01], ["years", "분석 기간 [년]", 1], ["energy_escalation", "전기요금 연 상승률 [-]", 0.01],
  ["it_growth_pct_per_year", "IT 증설률 [%/년]", 1], ["co2_kg_per_kwh", "탄소배출계수 [kgCO₂/kWh]", 0.01],
];

interface Props { scenario: Scenario; busy: boolean }

/** 투자 검토: 개선안별 투자비·연 절감·회수기간·NPV */
export function InvestPanel({ scenario, busy }: Props) {
  const [cat, setCat] = useState<CatalogItem[]>([]);
  const [eco, setEco] = usePersist<Economics | null>("inv:eco", null);
  const [sel, setSel] = usePersist<Record<string, { on: boolean; capex: string }>>("inv:sel", {});
  const [pkg, setPkg] = usePersist<boolean>("inv:pkg", true);
  const [err, setErr] = useState<string | null>(null);
  const job = useJob<InvestResult>("invest");
  const r = job.result;

  useEffect(() => {
    api.investCatalog(scenario).then((c) => {
      setCat(c.measures);
      if (!eco) setEco(c.economics);
      if (!Object.keys(sel).length) {
        const init: Record<string, { on: boolean; capex: string }> = {};
        for (const m of c.measures) init[m.id] = { on: m.applicable && ["schedule", "cold_aisle", "piping_dual"].includes(m.id), capex: "" };
        setSel(init);
      }
    }).catch((e) => setErr((e as Error).message));
  }, [scenario]);   // eslint-disable-line react-hooks/exhaustive-deps

  const chosen = cat.filter((m) => m.applicable && sel[m.id]?.on);
  const run = () => {
    if (!eco) return;
    const measures: Measure[] = chosen.map((m) => ({ id: m.id, capex_krw: sel[m.id]?.capex.trim() ? Number(sel[m.id].capex) : null }));
    const packages = pkg && chosen.length > 1 ? [chosen.map((m) => m.id)] : [];
    job.start("invest", { scenario, economics: eco, measures, packages,
      it_source: scenario.it_load.dataset_id ? "dataset" : "scenario" });
  };
  const top = r?.results.slice(0, 4) ?? [];
  const flows = r ? Array.from({ length: (eco?.years ?? 15) + 1 }, (_, y) => ({ y, ...Object.fromEntries(top.map((x) => [x.key, x.cumulative_krw[y] / 1e8])) })) : [];

  return (
    <div>
      <DemoNote>투자비 단가와 요금 기본값은 <b>예시 데이터</b>입니다. 이 화면의 결과는 예시 데이터를 이용해 시연 계산한 것이며, 실제 견적·요금을 넣으면 다시 계산됩니다.</DemoNote>
      <h3 className="sub-h">개선안 (예시 투자비는 수량 × 예시 단가)</h3>
      <div className="measures">
        {cat.map((m) => (
          <label key={m.id} className={`measure ${sel[m.id]?.on ? "on" : ""} ${m.applicable ? "" : "na"}`}>
            <div className="row" style={{ gap: 6 }}>
              <input type="checkbox" disabled={!m.applicable} checked={!!sel[m.id]?.on}
                onChange={(e) => setSel({ ...sel, [m.id]: { ...(sel[m.id] ?? { capex: "" }), on: e.target.checked } })} />
              <b>{m.label}</b>
            </div>
            <div className="muted">{m.applicable ? `${m.description} · ${fmt(m.quantity, 0)} ${m.unit}` : "이미 적용됨 / 해당 없음"}</div>
            {m.applicable && <input className="text" type="number" placeholder={`예시 ${krw(m.example_capex_krw)}`}
              value={sel[m.id]?.capex ?? ""} onChange={(e) => setSel({ ...sel, [m.id]: { ...(sel[m.id] ?? { on: true }), capex: e.target.value } })} />}
          </label>
        ))}
      </div>
      {eco && <Fold title="요금·조건 (예시값)">
        <div className="form-grid">
          {ECO_FIELDS.map(([k, l, st]) => <div key={k} className="field"><label>{l}</label>
            <input type="number" step={st} value={String(eco[k])} onChange={(e) => setEco({ ...eco, [k]: Number(e.target.value) })} /></div>)}
          <RegionNote scenario={scenario} />
        </div>
      </Fold>}
      <div className="row" style={{ marginTop: 10 }}>
        <label className="muted" style={{ fontSize: 13 }}><input type="checkbox" checked={pkg} onChange={(e) => setPkg(e.target.checked)} /> 고른 안을 함께 적용한 패키지도 계산</label>
        <button className="btn primary" disabled={job.running || busy || !chosen.length || !eco} onClick={run}>투자 검토 계산</button>
      </div>
      <JobBar job={job.job} running={job.running} onCancel={job.cancel} />
      {(job.error || err) && <div className="banner crit">{job.error || err}</div>}

      {r && <div style={{ marginTop: 12 }}>
        {r.demo && <DemoNote>아래 결과는 예시 데이터를 이용해 시연 계산한 것입니다 (예시 투자비 포함).</DemoNote>}
        <Lead>{r.summary}</Lead>
        <div className="table-scroll" style={{ maxHeight: "none" }}><table className="data">
          <thead><tr><th>개선안</th><th>투자비</th><th>연 절감 MWh</th><th>연 절감액</th><th>단순 회수</th><th>NPV</th><th>IRR</th><th>탄소 t/년</th></tr></thead>
          <tbody>{r.results.map((x) => (
            <tr key={x.key}><td>{x.label}{x.demo_capex ? <span className="tag">예시</span> : null}
              {x.package && x.sum_of_singles_kwh != null && <div className="muted">개별 합 {fmt(x.sum_of_singles_kwh / 1000, 0)} MWh → 함께 {fmt(x.year1_saving_kwh / 1000, 0)} MWh</div>}</td>
              <td>{krw(x.capex_krw)}</td><td>{fmt(x.year1_saving_kwh / 1000, 0)}</td><td>{krw(x.year1_saving_krw)}</td>
              <td>{x.payback_years == null ? "회수 불가" : `${fmt(x.payback_years, 1)}년`}</td>
              <td style={x.npv_krw < 0 ? { color: "var(--critical)" } : undefined}>{krw(x.npv_krw)}</td>
              <td>{x.irr == null ? "–" : `${fmt(x.irr * 100, 0)}%`}</td><td>{fmt(x.year1_co2_t, 0)}</td></tr>
          ))}</tbody></table></div>
        <h3 className="sub-h">누적 현금흐름 [억 원] (상위 {top.length}개)</h3>
        <div className="legend">{top.map((x, i) => <span key={x.key}><span className="sw" style={{ background: COLORS[i] }} />{x.label}</span>)}</div>
        <div className="chart-box" style={{ height: 220 }}>
          <ResponsiveContainer>
            <LineChart data={flows} margin={{ top: 6, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="y" {...axis} tickFormatter={(y: number) => `${y}년`} />
              <YAxis {...axis} width={48} />
              <ReferenceLine y={0} stroke="var(--text-muted)" />
              <Tooltip formatter={(v, n) => [`${fmt(Number(v), 2)} 억 원`, top.find((x) => x.key === n)?.label ?? String(n)]} labelFormatter={(y) => `${y}년차`} />
              {top.map((x, i) => <Line key={x.key} dataKey={x.key} stroke={COLORS[i]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <Fold title={`민감도 (${r.sensitivity.measure}) · 가정`}>
          <table className="data"><thead><tr><th>요인</th><th>낮을 때 NPV</th><th>기준 NPV</th><th>높을 때 NPV</th></tr></thead>
            <tbody>{r.sensitivity.items.map((s) => <tr key={s.factor}><td>{s.factor} ({s.low} / {s.high})</td>
              <td>{krw(s.npv_low_krw)}</td><td>{krw(r.sensitivity.base_npv_krw)}</td><td>{krw(s.npv_high_krw)}</td></tr>)}</tbody></table>
          <ul className="notes">{r.notes.map((n) => <li key={n}>{n}</li>)}
            <li>기준안 연간 냉방 설비 전력 {fmt(r.baseline.annual_kwh / 1000, 0)} MWh · 보급수 {fmt(r.baseline.water_m3, 0)} m³ · 비용 {krw(r.baseline.cost_krw)}</li></ul>
        </Fold>
      </div>}
    </div>
  );
}
