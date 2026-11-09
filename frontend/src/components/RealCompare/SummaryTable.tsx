import type { RealCompare, RealItemMetric, RealRun, RunKey } from "../../types/api";
import { fmt } from "../../lib/format";
import { G14Badge, RUN_COLOR, itemsOf, pct, runsOf, signed } from "./common";

function Sw({ k }: { k: RunKey | "meas" }) {
  return <span className="sw-inline" style={{ background: RUN_COLOR[k] }} />;
}

function ItemCell({ m }: { m: RealItemMetric }) {
  return (
    <td>
      <b>{fmt(m.sim_mean_kw, 2)}</b> kW
      <div className="muted">× {fmt(m.ratio_sim_over_meas, 3)} · NMBE {pct(m.nmbe_pct)} · CV {fmt(m.cv_rmse_pct, 1)}%</div>
      <G14Badge pass={m.g14_pass} />
    </td>
  );
}

/** 행: PUE · 항목별 평균 kW · 기계 합계 · 칠러 시간 · WUE / 열: 실측 · A · B · (검증 실측 · C) */
export function SummaryTable({ result }: { result: RealCompare }) {
  const runs = runsOf(result);
  const cols: { key: string; label: string; run: RealRun; sim: RunKey | null }[] = [
    { key: "mcal", label: "실측 (보정 기간)", run: result.runs.A, sim: null },
    ...runs.filter((k) => k !== "C").map((k) => ({ key: k, label: result.runs[k]!.label, run: result.runs[k]!, sim: k })),
    ...(result.runs.C ? [{ key: "mtest", label: "실측 (검증 기간)", run: result.runs.C, sim: null },
      { key: "C", label: result.runs.C.label, run: result.runs.C, sim: "C" as RunKey }] : []),
  ];
  const ref = result.reference ?? {};
  return (
    <div className="table-scroll" style={{ maxHeight: "none" }}>
      <table className="data summary">
        <thead>
          <tr><th />{cols.map((c) => <th key={c.key}><Sw k={c.sim ?? "meas"} />{c.label}
            <div className="muted">{c.run.period.join(" ~ ")} · IT {fmt(c.run.it_mean_kw, 1)} kW</div></th>)}</tr>
        </thead>
        <tbody>
          <tr><td>PUE (기간)</td>{cols.map((c) => c.sim ? (
            <td key={c.key}><b>{fmt(c.run.pue.sim, 4)}</b><div className="muted">차 {signed(c.run.pue.diff, 4)}</div></td>
          ) : <td key={c.key}><b>{fmt(c.run.pue.measured, 4)}</b></td>)}</tr>
          {itemsOf(result).map((it) => (
            <tr key={it} className={it === "mech_total" ? "total" : ""}>
              <td>{result.runs.A.items[it].label}{it === "mech_total" ? " (칠러 포함)" : ""} 평균</td>
              {cols.map((c) => c.sim ? <ItemCell key={c.key} m={c.run.items[it]} />
                : <td key={c.key}><b>{fmt(c.run.items[it].measured_mean_kw, 2)}</b> kW</td>)}
            </tr>
          ))}
          <tr><td>칠러 운전 시간</td>{cols.map((c) => <td key={c.key}>
            {c.sim ? <b>{c.run.chiller_hours.sim.toLocaleString()} h</b>
              : <b>{c.run.chiller_hours.measured == null ? "–" : `${c.run.chiller_hours.measured} h`}</b>}
            {!c.sim && result.facility?.chiller_note && <div className="muted">{result.facility.chiller_note}</div>}</td>)}</tr>
          <tr><td>WUE [L/kWh]</td>{cols.map((c) => <td key={c.key}>
            {c.sim ? <b>{fmt(c.run.wue_l_per_kwh.sim, 3)}</b> : c.run.wue_l_per_kwh.measured != null ? <b>{fmt(c.run.wue_l_per_kwh.measured, 3)}</b>
              : <span className="muted">{typeof ref.wue_first_year_l_per_kwh === "number"
                ? `실측 없음 · 공개값 ${ref.wue_first_year_l_per_kwh} (냉각탑만 ${ref.wue_cooling_towers_alone_l_per_kwh ?? "–"})` : "실측 없음"}</span>}
          </td>)}</tr>
        </tbody>
      </table>
      <div className="muted" style={{ marginTop: 6 }}>
        {result.sign_note}. 판정: 시간값 |NMBE| ≤ {result.g14.nmbe_pct}%, CV(RMSE) ≤ {result.g14.cv_rmse_pct}% · × = 시뮬 ÷ 실측 평균 ·
        실측이 있는 시간만 비교 (A {result.runs.A.hours.toLocaleString()} h{result.runs.C ? `, C ${result.runs.C.hours.toLocaleString()} h` : ""})
      </div>
      {result.pue_check && <div className={`banner ${result.pue_check.ok ? "info" : "warn"}`}>
        {result.pue_check.ok ? `✓ 매핑으로 다시 계산한 실측 PUE 가 원본 PUE 열과 일치 (차이 중앙값 ${result.pue_check.median_abs_diff}, ${result.pue_check.hours.toLocaleString()} h)`
          : `⚠ ${result.pue_check.warning}`}</div>}
    </div>
  );
}
