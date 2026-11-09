import { useState } from "react";
import type { CoolingLayout, EvaluateResult, NewRacks, PlacementResult, Scenario } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { Fold, JobBar, Lead, useJob } from "../../lib/ui";
import { IssueBox, flash } from "../../lib/issues";

const TYPES = [["air", "공랭", 0], ["dlc", "DLC 냉각판", 0.75], ["imm", "액침", 0.95]] as const;
type TypeKey = (typeof TYPES)[number][0];
const phiOf = (k: TypeKey) => TYPES.find(([t]) => t === k)![2];
const typeOf = (phi: number | null | undefined): TypeKey => (!phi ? "air" : phi >= 0.9 ? "imm" : "dlc");

interface Props {
  scenario: Scenario; layout: CoolingLayout; cooling: EvaluateResult | null; busy: boolean; onApply: (sc: Scenario) => void;
}

/** 평면도: 기존 랙(흡입온도 색), 새 랙(강조), CRAH */
function Plan({ cooling, added, limit }: { cooling: EvaluateResult; added?: PlacementResult["items"]; limit: number }) {
  const g = cooling.geometry.building;
  const W = g.ew_m, H = g.ns_m;
  const racks = cooling.airflow.racks;
  const col = (t: number) => (t > limit ? "var(--critical)" : t > limit - 2 ? "var(--series-4)" : "var(--text-muted)");
  return (
    <svg className="plan" viewBox={`${-W / 2 - 1} ${-H / 2 - 1} ${W + 2} ${H + 2}`} preserveAspectRatio="xMidYMid meet">
      <rect x={-W / 2} y={-H / 2} width={W} height={H} fill="none" stroke="var(--border)" strokeWidth={0.3} />
      {racks.map((r, i) => <rect key={i} x={r.x_m - 0.3} y={r.z_m - 0.6} width={0.6} height={1.2} fill={col(r.inlet_c)} opacity={0.75} />)}
      {cooling.airflow.crah.map((c, i) => <rect key={`c${i}`} x={c.x_m - 1} y={c.z_m - 0.6} width={2} height={1.2} fill="var(--series-1)" />)}
      {added?.map((r) => <rect key={r.id} x={r.x_m - 0.35} y={r.z_m - 0.7} width={0.7} height={1.4}
        fill={r.liquid_fraction ? "var(--series-3)" : "var(--series-5)"} stroke="var(--text-primary)" strokeWidth={0.12} />)}
    </svg>
  );
}

export function RackPanel({ scenario, layout, cooling, busy, onApply }: Props) {
  const items = layout.racks.items ?? [];
  const nRows = items.length ? Math.max(...items.map((r) => r.row)) + 1 : layout.racks.rows;
  const rowType = (j: number): TypeKey | "mixed" => {
    if (items.length) {
      const ts = new Set(items.filter((r) => r.row === j).map((r) => typeOf(r.liquid_fraction)));
      return ts.size === 1 ? [...ts][0] : "mixed";
    }
    return typeOf(layout.racks.row_liquid_fraction?.[j]);
  };
  const [rows, setRows] = useState<Record<number, TypeKey>>({});
  const [groups, setGroups] = useState<NewRacks[]>([{ count: 8, design_kw: 30, liquid_fraction: 0.75 }]);
  const [err, setErr] = useState<string | null>(null);
  const job = useJob<PlacementResult>("placement");
  const res = job.result;

  const applyRows = async () => {
    const body: Record<number, number | null> = {};
    for (const [j, t] of Object.entries(rows)) body[Number(j)] = t === "air" ? 0 : phiOf(t);
    try { onApply(await api.racksApply(scenario, { rows: body })); setRows({}); } catch (e) { setErr((e as Error).message); }
  };
  const applyPlace = async () => {
    if (!res) return;
    try {
      onApply(await api.racksApply(scenario, { add_items: res.items.map(({ inlet_c: _x, ...it }) => it),
        unit_airflow_m3_s: res.unit_airflow_m3_s }));
    } catch (e) { setErr((e as Error).message); }
  };
  const setG = (i: number, patch: Partial<NewRacks>) => setGroups(groups.map((g, k) => (k === i ? { ...g, ...patch } : g)));
  /** 해결 방법: 새 공랭 랙 묶음을 DLC 로 (다시 추천은 사용자가) */
  const toDlc = () => {
    setGroups(groups.map((g) => (g.liquid_fraction ? g : { ...g, liquid_fraction: phiOf("dlc") })));
    const i = groups.findIndex((g) => !g.liquid_fraction);
    window.setTimeout(() => flash(`rack-group-${Math.max(i, 0)}`), 50);
  };

  return (
    <div>
      <h3 className="sub-h" id="set-rack-types">열별 냉각 방식</h3>
      <div className="muted">열마다 공랭 / DLC / 액침을 고르면 기류 계산(랙별)과 부하 계산(가중 평균)이 함께 맞춰집니다.</div>
      <div className="rowtypes">
        {Array.from({ length: nRows }, (_, j) => {
          const cur = rowType(j);
          return (
            <label key={j} className="rowtype">{j + 1}열
              <select value={rows[j] ?? (cur === "mixed" ? "" : cur)} onChange={(e) => setRows({ ...rows, [j]: e.target.value as TypeKey })}>
                {cur === "mixed" && <option value="">혼합</option>}
                {TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
          );
        })}
      </div>
      <button className="btn primary" style={{ marginTop: 8 }} disabled={busy || !Object.keys(rows).length} onClick={applyRows}>냉각 방식 적용 · 재계산</button>

      <h3 className="sub-h" style={{ marginTop: 18 }}>증설 랙 자리 추천</h3>
      {groups.map((g, i) => (
        <div key={i} className="row" style={{ marginBottom: 6 }} id={`rack-group-${i}`}>
          <input className="text" style={{ width: 70 }} type="number" min={1} value={g.count} onChange={(e) => setG(i, { count: Number(e.target.value) })} /> 대 ×
          <input className="text" style={{ width: 80 }} type="number" min={1} value={g.design_kw} onChange={(e) => setG(i, { design_kw: Number(e.target.value) })} /> kW
          <select value={typeOf(g.liquid_fraction)} onChange={(e) => setG(i, { liquid_fraction: phiOf(e.target.value as TypeKey) })}>
            {TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          {groups.length > 1 && <button className="btn small" onClick={() => setGroups(groups.filter((_, k) => k !== i))}>삭제</button>}
        </div>
      ))}
      <div className="row">
        <button className="btn small" onClick={() => setGroups([...groups, { count: 10, design_kw: 12, liquid_fraction: 0 }])}>+ 묶음 추가</button>
        <button className="btn primary" disabled={job.running || busy || groups.some((g) => !(g.count > 0 && g.design_kw > 0))}
          onClick={() => job.start("placement", { scenario, new_racks: groups })}>자리 추천</button>
      </div>
      <JobBar job={job.job} running={job.running} onCancel={job.cancel} />
      {(job.error || err) && <div className="banner crit">{job.error || err}</div>}

      <div className="import-grid">
        <div>
          {res && <>
            <Lead>{res.summary}</Lead>
            <div className="kpis" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
              <div className="kpi"><div className="label">최대 흡입온도 (전 → 후)</div>
                <div className="value">{fmt(res.before.max_inlet_c, 1)} → {fmt(res.after.max_inlet_c, 1)}<span className="unit">℃</span></div></div>
              <div className="kpi"><div className="label">추가 IT 설계부하</div><div className="value">{fmt(res.added_design_kw, 0)}<span className="unit">kW</span></div></div>
            </div>
            {res.issue && <IssueBox issue={res.issue} note="설정을 바꾼 뒤 '자리 추천'을 다시 실행하세요"
              local={{ "rack.types": { label: "새 공랭 랙 → DLC 로 바꾸기", run: toDlc } }} />}
            <Fold title="새 랙 목록·기류 근거·가정">
              <div className="table-scroll"><table className="data"><thead><tr><th>id</th><th>열</th><th>x, z [m]</th><th>kW</th><th>방식</th>
                <th title="서버 팬이 빨아들여야 하는 공기량">요구 풍량 m³/s</th><th title="바닥 타일에서 받는 급기">타일 급기 m³/s</th>
                <th title="모자란 공기를 랙 배기(고온)가 채우는 비율">재순환</th><th>흡입 ℃</th></tr></thead>
                <tbody>{res.items.map((r) => {
                  const a = res.air?.[r.id];
                  return <tr key={r.id}><td>{r.id}</td><td>{r.row + 1}</td><td>{fmt(r.x_m, 1)}, {fmt(r.z_m, 1)}</td>
                    <td>{fmt(r.design_kw, 0)}</td><td>{TYPES.find(([k]) => k === typeOf(r.liquid_fraction))![1]}</td>
                    <td>{a ? fmt(a.required_m3_s, 2) : "–"}</td>
                    <td style={a && a.supplied_m3_s < a.required_m3_s ? { color: "var(--critical)" } : undefined}>{a ? fmt(a.supplied_m3_s, 2) : "–"}</td>
                    <td>{a ? `${fmt(a.recirculation * 100, 0)}%` : "–"}</td>
                    <td style={r.inlet_c != null && r.inlet_c > layout.inlet_limit_c ? { color: "var(--critical)" } : undefined}>{fmt(r.inlet_c, 1)}</td></tr>;
                })}</tbody></table></div>
              <div className="muted" style={{ marginTop: 4 }}>흡입 = 급기 + 재순환 × 서버 ΔT / (1 − 재순환). 타일 급기가 요구 풍량보다 적으면 모자란 만큼 랙 배기가 다시 빨려 들어갑니다.</div>
              <ul className="notes">{res.notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </Fold>
            <button className="btn primary" style={{ marginTop: 8 }} disabled={busy} onClick={applyPlace}>이 배치 적용 · 재계산</button>
          </>}
        </div>
        <div>
          {cooling && <Plan cooling={cooling} added={res?.items} limit={layout.inlet_limit_c} />}
          <div className="legend"><span><span className="sw" style={{ background: "var(--text-muted)" }} />기존 랙</span>
            <span><span className="sw" style={{ background: "var(--series-4)" }} />흡입 여유 2℃ 미만</span>
            <span><span className="sw" style={{ background: "var(--series-1)" }} />CRAH</span>
            <span><span className="sw" style={{ background: "var(--series-3)" }} />새 랙 (액체)</span>
            <span><span className="sw" style={{ background: "var(--series-5)" }} />새 랙 (공랭)</span></div>
        </div>
      </div>
    </div>
  );
}
