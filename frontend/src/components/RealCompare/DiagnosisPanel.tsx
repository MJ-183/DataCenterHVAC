import { useEffect, useState } from "react";
import type { RealCompare, RealDiagnosis } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { usePersist } from "../../lib/ui";
import { BasisBadge, pct } from "./common";

/** OpenAI 오차 원인 진단 — AI 는 계산하지 않음. 아래에 서버 계산 근거(구조 검증·칠러 민감도)를 함께 보여 대조 */
export function DiagnosisPanel({ result }: { result: RealCompare }) {
  const [status, setStatus] = useState<{ enabled: boolean; model: string | null; reason: string | null } | null>(null);
  const [diag, setDiag] = usePersist<RealDiagnosis | null>("real:diag", null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.aiStatus().then(setStatus).catch(() => setStatus({ enabled: false, model: null, reason: "AI 상태 확인 실패" })); }, []);
  const d = diag?.compare_id === result.compare_id ? diag : null;
  const run = async () => {
    setBusy(true); setErr(null);
    try { setDiag(await api.realDiagnose(result.compare_id)); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  return (
    <div>
      <div className="row">
        <button className="btn primary" disabled={!status?.enabled || busy} onClick={run}>
          {busy ? "분석 중…" : "OpenAI로 오차 원인 분석"}</button>
        {status && !status.enabled && <span className="muted">사용할 수 없음: {status.reason}</span>}
        {status?.enabled && !d && <span className="muted">서버가 계산한 근거 묶음(지표·월별 표·보정 단계·구조 검증·칠러 민감도)만 보내고, 응답 숫자를 근거와 대조합니다.</span>}
      </div>
      {err && <div className="banner crit">{err}</div>}

      {d && <>
        {d.unverified_numbers.length > 0
          ? <div className="banner warn">⚠ 근거 묶음에 없는 숫자: {d.unverified_numbers.join(", ")} — 이 숫자는 AI 가 만든 값일 수 있습니다</div>
          : <div className="banner info">✓ 응답의 숫자가 모두 근거 묶음에 있습니다</div>}
        <p className="lead">{d.summary}</p>
        <div className="cause-list">{d.causes.map((c) => (
          <div key={c.rank} className="cause">
            <div className="cause-head"><span className="step-no">{c.rank}</span><b>{c.title}</b>
              <BasisBadge basis={c.basis} title={c.basis === "data" ? "근거 묶음으로 확인됨" : "추가 확인 필요"} /></div>
            <dl>
              <dt>영향 항목</dt><dd>{c.affected.join(", ")}</dd>
              <dt>방향</dt><dd>{c.direction}</dd>
              <dt>근거</dt><dd>{c.evidence}</dd>
              <dt>확인 방법</dt><dd>{c.how_to_verify}</dd>
              <dt>모델 수정</dt><dd>{c.model_fix}</dd>
            </dl>
          </div>))}</div>
        <div className="muted"><b>설명되지 않은 오차:</b> {d.not_explained}</div>
        <div className="muted" style={{ marginTop: 4 }}>모델 {d.model} · 생성 {d.generated_at.replace("T", " ")}{d.cached ? " (캐시)" : ""}</div>
      </>}

      <h3 className="sub-h" style={{ marginTop: 14 }}>서버 계산 근거 ① 구조 검증 — 실측 = 고정분 + a·IT + b·max(습구, 0) + c·max(−건구, 0)</h3>
      <div className="table-scroll" style={{ maxHeight: "none" }}>
        <table className="data">
          <thead><tr><th>항목</th><th>고정분 kW</th><th>IT 1 kW 당</th><th>습구 1 K 당</th><th>영하 1 K 당</th><th>고정분 비율</th>
            <th>검증: 엔진 비례 보정 NMBE / CV</th><th>검증: 고정분 회귀 NMBE / CV</th></tr></thead>
          <tbody>{Object.entries(result.structure).map(([k, s]) => (
            <tr key={k}><td>{s.label}</td><td>{fmt(s.coef.fixed_kw, 2)}</td><td>{fmt(s.coef.per_it_kw, 5)}</td>
              <td>{fmt(s.coef.per_wb_k_above0, 3)}</td><td>{fmt(s.coef.per_k_below0_dry, 3)}</td>
              <td>{fmt(s.fixed_share_of_mean_cal * 100, 0)}%</td>
              <td>{s.engine_calibrated_test ? `${pct(s.engine_calibrated_test.nmbe_pct)} / ${fmt(s.engine_calibrated_test.cv_rmse_pct, 1)}%` : "–"}</td>
              <td>{s.affine_test ? `${pct(s.affine_test.nmbe_pct)} / ${fmt(s.affine_test.cv_rmse_pct, 1)}%` : "–"}</td></tr>))}</tbody>
        </table>
      </div>
      <div className="muted">보정 기간에 맞추고 검증 기간에 적용. 분석용이며 엔진에는 넣지 않았습니다 (엔진 수식 변경은 별도 승인).</div>

      <h3 className="sub-h" style={{ marginTop: 14 }}>서버 계산 근거 ② 칠러 필요 시간 민감도 ({result.sensitivity.source}, {result.sensitivity.hours_total.toLocaleString()} h)</h3>
      <table className="data" style={{ maxWidth: 720 }}>
        <thead><tr><th>조건</th><th>습구</th><th>칠러 필요 시간</th></tr></thead>
        <tbody>{result.sensitivity.rows.map((r) => (
          <tr key={r.label}><td>{r.label}</td><td>{r.wet_bulb === "site" ? "사이트 기압" : "해수면"}</td><td>{r.hours.toLocaleString()} h</td></tr>))}</tbody>
      </table>
      <div className="muted">
        {result.sensitivity.site_pressure_kpa != null && <>사이트 기압 {fmt(result.sensitivity.site_pressure_kpa, 1)} kPa
          (고도 {fmt(result.sensitivity.elevation_m, 0)} m) · 건구 20 ℃ 이상에서 해수면 습구가 평균 {fmt(result.sensitivity.wet_bulb_bias_when_t_gt_20c_mean_k, 2)} K 높음 ·
          최고 습구 해수면 {fmt(result.sensitivity.max_wet_bulb_sea_c, 2)} ℃ / 사이트 {fmt(result.sensitivity.max_wet_bulb_alt_c, 2)} ℃ · </>}
        {result.sensitivity.note}{result.facility?.chiller_note ? ` · ${result.facility.chiller_note}` : ""}
      </div>
    </div>
  );
}
