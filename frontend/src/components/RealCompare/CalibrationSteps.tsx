import { useState } from "react";
import type { CalStep, RealCompare, Scenario } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { G14Badge } from "./common";

// 표 열 이름 (백엔드 calibration_steps 의 키 그대로)
const COL: Record<string, string> = {
  label: "항목", measured_mean_kw: "실측 평균 kW", sim_A_mean_kw: "시뮬 A 평균 kW", sim_B_mean_kw: "시뮬 B 평균 kW",
  sim_mean_kw: "시뮬 평균 kW", ratio: "비율 r", formula: "계산식", coefficient: "계수", A: "A (기본값)", B: "B (보정값)",
  residual_kw: "B 잔차 kW", nmbe_pct: "NMBE %", cv_rmse_pct: "CV(RMSE) %", g14_pass: "G14", diff_A_kw: "A − 실측 kW",
  pue_contrib_A: "PUE 기여 A", diff_B_kw: "B − 실측 kW", pue_contrib_B: "PUE 기여 B",
};
const HIDE = new Set(["item", "calibrated"]);
const DIGITS: Record<string, number> = { ratio: 3, pue_contrib_A: 4, pue_contrib_B: 4, nmbe_pct: 1, cv_rmse_pct: 1 };
const SIGNED = new Set(["residual_kw", "diff_A_kw", "diff_B_kw", "pue_contrib_A", "pue_contrib_B", "nmbe_pct"]);

function cell(k: string, v: string | number | boolean | null | undefined) {
  if (k === "g14_pass") return <G14Badge pass={v as boolean | null} />;
  if (v == null) return k === "ratio" ? "보정 안 함" : "–";
  if (typeof v !== "number") return String(v);
  if (k === "A" || k === "B") return String(v);
  const s = fmt(v, DIGITS[k] ?? 2);
  return SIGNED.has(k) && v > 0 ? `+${s}` : s;
}

function StepCard({ s }: { s: CalStep }) {
  const keys = s.rows?.length ? Object.keys(s.rows[0]).filter((k) => !HIDE.has(k)) : [];
  return (
    <div className="step-card">
      <div className="step-title"><span className="step-no">{s.step}</span>{s.title}</div>
      {s.bullets && <ul>{s.bullets.map((b) => <li key={b}>{b}</li>)}</ul>}
      {s.rows && s.rows.length > 0 && <div className="table-scroll" style={{ maxHeight: "none" }}>
        <table className="data"><thead><tr>{keys.map((k) => <th key={k}>{COL[k] ?? k}</th>)}</tr></thead>
          <tbody>{s.rows.map((r, i) => <tr key={i}>{keys.map((k) =>
            <td key={k} style={k === "formula" ? { textAlign: "left", whiteSpace: "normal", minWidth: 260 } : undefined}>{cell(k, r[k])}</td>)}</tr>)}</tbody>
        </table></div>}
      {s.text && <p className="step-text">{s.text}</p>}
      {s.step === 5 && <G14Badge pass={s.g14_pass} />}
    </div>
  );
}

/** 시뮬 A → B 보정 단계 0~5 (백엔드 문장·표 그대로) + B 계수 시나리오 적용·되돌리기 */
export function CalibrationSteps({ result, scenario, busy, onApply }: {
  result: RealCompare; scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void;
}) {
  const [err, setErr] = useState<string | null>(null);
  const applied = Object.entries(scenario.provenance ?? {}).filter(([, p]) => p.source === "real_calibrated").map(([k]) => k);
  const coefs = result.calibration.coefficients;
  const apply = async () => {
    setErr(null);
    try { onApply(await api.realApply(scenario, result.compare_id)); } catch (e) { setErr((e as Error).message); }
  };
  const undo = async () => {
    setErr(null);
    try { onApply(await api.revert(scenario, applied)); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div>
      <div className="steps-grid">{result.calibration.steps.map((s) => <StepCard key={s.step} s={s} />)}</div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={busy || !Object.keys(coefs).length} onClick={apply}>B 계수를 시나리오에 적용</button>
        {applied.length > 0 && <>
          <span className="badge ok">실측 비교 보정 적용 중 ({applied.length}개 계수)</span>
          <button className="btn small" disabled={busy} onClick={undo}>보정 되돌리기</button>
        </>}
        <span className="muted">지금 화면의 시나리오에 {Object.entries(coefs).map(([k, v]) => `${k} = ${v}`).join(", ")} 만 씁니다.
          출처는 '실측 비교 보정(real:{result.preset ?? "dataset"})'으로 기록되고, 상단 ↶ 되돌리기로도 취소할 수 있습니다.</span>
      </div>
      {err && <div className="banner crit">{err}</div>}
    </div>
  );
}
