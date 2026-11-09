import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AnnualOptResult, Containment, Scenario, StructurePlan } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { Fold, JobBar, Lead, useJob, usePersist } from "../../lib/ui";
import { RegionNote } from "../Import/RegionPanel";
import { crahText } from "../../lib/fields";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };
const CONT: [Containment, string][] = [["open", "개방형"], ["cold_aisle", "콜드아일"], ["hot_aisle", "핫아일"]];
const TOPO = { radial: "단일 헤더", dual: "2계통", ring: "루프" } as const;
const contL = (c: Containment) => CONT.find(([k]) => k === c)![1];

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void; onReport?: () => void }

/** 연간 기준 운전 최적화: 구조는 고정, 운전점은 월마다 (변경 폭 제한) */
export function AnnualOptPanel({ scenario, busy, onApply, onReport }: Props) {
  const [conts, setConts] = useState<Containment[]>(["open", "cold_aisle", "hot_aisle"]);
  const [step, setStep] = useState("2");
  const [useDs, setUseDs] = useState(true);
  const [pick, setPick] = usePersist<"same" | "best">("aopt:pick", "same");
  const [err, setErr] = useState<string | null>(null);
  const job = useJob<AnnualOptResult>("annual_optimize");
  const r = job.result;
  const hasDs = !!scenario.it_load.dataset_id;

  const run = () => job.start("annual_optimize", {
    scenario, containment: conts, max_step_c: Number(step) || 0,
    it_source: hasDs && useDs ? "dataset" : "scenario",
  });
  const plan: StructurePlan | null = r ? (pick === "same" ? r.same_structure ?? r.best : r.best) : null;
  const apply = async () => {
    if (!plan) return;
    try {
      onApply(await api.scheduleApply(scenario, plan.structure,
        plan.schedule.map(({ month, supply_temp_c, chw_supply_c, chw_return_c, airflow_ratio }) =>
          ({ month, supply_temp_c, chw_supply_c, chw_return_c, airflow_ratio }))));
    } catch (e) { setErr((e as Error).message); }
  };
  const data = r?.months.map((m, i) => ({ name: `${m}월`, cur: r.current.monthly[i] / 1000, opt: (plan?.monthly[i] ?? 0) / 1000 })) ?? [];

  return (
    <div>
      <div className="form-grid">
        <RegionNote scenario={scenario} />
        <div className="field"><label>인접 월 급기온도 변경 폭 상한 [℃]</label>
          <input type="number" step={0.5} value={step} onChange={(e) => setStep(e.target.value)} /></div>
        {hasDs && <div className="field"><label>IT 부하</label>
          <select value={useDs ? "ds" : "sc"} onChange={(e) => setUseDs(e.target.value === "ds")}>
            <option value="ds">IT 데이터셋 (월·요일 패턴)</option><option value="sc">시나리오 24시간 모양</option></select></div>}
      </div>
      <Fold title="고급: 검토할 컨테인먼트">
        <div className="row">{CONT.map(([k, l]) => <label key={k} className="muted" style={{ fontSize: 13 }}>
          <input type="checkbox" checked={conts.includes(k)} onChange={() => setConts(conts.includes(k) ? conts.filter((x) => x !== k) : [...conts, k])} /> {l}
        </label>)}</div>
      </Fold>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={job.running || busy || !conts.length} onClick={run}>연간 최적화 실행</button>
        <span className="muted">365일 × 구조 후보 × 운전점 (보통 수 초)</span>
      </div>
      <JobBar job={job.job} running={job.running} onCancel={job.cancel} />
      {(job.error || err) && <div className="banner crit">{job.error || err}</div>}

      {r && plan && <div style={{ marginTop: 12 }}>
        <Lead>{r.summary}</Lead>
        <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>
          <div className="kpi"><div className="label">현재 운전 (연)</div><div className="value">{fmt(r.current.annual_kwh / 1000, 0)}<span className="unit">MWh</span></div></div>
          {r.same_structure && <div className={`kpi pick ${pick === "same" ? "on" : ""}`} onClick={() => setPick("same")}>
            <div className="label">지금 구조 + 월별 운전</div><div className="value">{fmt(r.same_structure.monthly_kwh / 1000, 0)}<span className="unit">MWh</span></div>
            <div className="detail">공사 없음</div></div>}
          {!r.best.is_current && <div className={`kpi pick ${pick === "best" ? "on" : ""}`} onClick={() => setPick("best")}>
            <div className="label">구조 변경 + 월별 운전</div><div className="value">{fmt(r.best.monthly_kwh / 1000, 0)}<span className="unit">MWh</span></div>
            <div className="detail">{contL(r.best.structure.containment)} · {TOPO[r.best.structure.topology]} · {crahText(scenario, r.best.structure.crah_per_side)}</div></div>}
        </div>
        <h3 className="sub-h">월별 냉방 전력 [MWh] — 현재 운전 vs 선택한 안</h3>
        <div className="legend"><span><span className="sw" style={{ background: "var(--series-2)" }} />현재 운전</span>
          <span><span className="sw" style={{ background: "var(--series-1)" }} />선택한 안</span></div>
        <div className="chart-box" style={{ height: 200 }}>
          <ResponsiveContainer>
            <BarChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="name" {...axis} /><YAxis {...axis} width={44} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} formatter={(v, n) => [`${fmt(Number(v), 1)} MWh`, n === "cur" ? "현재 운전" : "선택한 안"]} />
              <Bar dataKey="cur" fill="var(--series-2)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="opt" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <Fold title="월별 운전 스케줄 보기">
          <div className="table-scroll"><table className="data">
            <thead><tr><th>월</th><th>급기 ℃</th><th>냉수 공급/환수 ℃</th><th>풍량</th><th>냉방 전력 kWh</th><th>흡입 여유 ℃</th></tr></thead>
            <tbody>{plan.schedule.map((e) => (
              <tr key={e.month}><td>{e.month}월</td><td>{fmt(e.supply_temp_c, 1)}</td>
                <td>{fmt(e.chw_supply_c, 1)} / {fmt(e.chw_return_c, 1)}</td><td>{fmt(e.airflow_ratio * 100, 0)}%</td>
                <td>{fmt(e.kwh, 0)}</td><td>{fmt(e.inlet_margin_c, 2)}</td></tr>
            ))}</tbody></table></div>
          <ul className="notes">{r.notes.map((n) => <li key={n}>{n}</li>)}
            <li>검증: 벡터 계산과 스칼라 재계산 차이 최대 {fmt(r.check.max_rel_diff * 100, 3)}% {r.check.ok ? "✓" : "⚠"}</li></ul>
        </Fold>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={busy} onClick={apply}>이 스케줄 적용</button>
          {onReport && <button className="btn" onClick={onReport}>보고서 보기</button>}
          <span className="muted">구조와 12개월 운전점이 저장되고, 현재 운전점은 시나리오 날짜가 속한 달 값이 됩니다. 되돌리기 가능</span>
        </div>
      </div>}
    </div>
  );
}
