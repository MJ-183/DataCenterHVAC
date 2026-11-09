import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BmsDataset, BmsInspect, CalResult, Scenario } from "../../types/api";
import { type UploadFile, api, fileB64 } from "../../api/client";
import { fmt } from "../../lib/format";
import { Fold, JobBar, Lead, useJob, usePersist } from "../../lib/ui";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };
const SERIES = [["measured", "실측", "var(--text-primary)"], ["before", "보정 전", "var(--series-2)"],
  ["after", "보정 후", "var(--series-1)"]] as const;
const VERDICT = { pass: ["info", "✓ 보정 기준 충족"], partial: ["warn", "△ 일부 지표만 기준 충족"], fail: ["crit", "✕ 기준 미달"] } as const;

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void }

/** BMS 실측 보정: 올리기 → 열 확인 → 보정(작업) → 결과 확인·적용 */
export function BmsCalibration({ scenario, busy, onApply }: Props) {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [ins, setIns] = usePersist<BmsInspect | null>("bms:ins", null);
  const [ov, setOv] = useState<Record<string, Record<string, string>>>({});
  const [ds, setDs] = usePersist<BmsDataset | null>("bms:ds", null);
  const [err, setErr] = useState<string | null>(null);
  const job = useJob<CalResult>("calibrate");
  const res = job.result;
  const calibrated = Object.entries(scenario.provenance ?? {}).filter(([, p]) => p.source === "bms_calibrated").map(([k]) => k);

  const pick = async (fl: FileList | null) => {
    if (!fl?.length) return;
    setErr(null); setDs(null);
    try {
      const up = await Promise.all([...fl].map(async (f) => ({ name: f.name, data_b64: await fileB64(f) })));
      setFiles(up);
      setOv({});
      setIns(await api.bmsInspect(up, {}));
    } catch (e) { setErr((e as Error).message); }
  };
  const run = async () => {
    setErr(null);
    try {
      const d = await api.bmsDataset(files, ov);
      setDs(d);
      await job.start("calibrate", { scenario, dataset_id: d.id });
    } catch (e) { setErr((e as Error).message); }
  };
  const apply = async () => {
    if (!res) return;
    try { onApply(await api.calibrationApply(scenario, res.run_id)); } catch (e) { setErr((e as Error).message); }
  };
  const undo = async () => {
    try { onApply(await api.revert(scenario, calibrated)); } catch (e) { setErr((e as Error).message); }
  };
  const setRole = (label: string, role: string, col: string) => {
    const t = ins?.tables.find((x) => x.label === label);
    if (!t) return;
    const m = { ...(ov[label] ?? t.mapping) };
    for (const k of Object.keys(m)) if (m[k] === col) delete m[k];
    if (role) m[role] = col;
    setOv({ ...ov, [label]: m });
  };

  return (
    <div>
      <div className="row">
        <input type="file" multiple accept=".csv,.tsv,.txt,.xlsx" onChange={(e) => pick(e.target.files)} />
        {calibrated.length > 0 && <>
          <span className="badge ok">보정 적용 중 ({calibrated.length}개 계수)</span>
          <button className="btn small" disabled={busy} onClick={undo}>보정 되돌리기</button>
        </>}
      </div>
      <div className="muted" style={{ marginTop: 4 }}>BMS 내보내기 파일(여러 날, 시간 단위). 외기 온습도 열이 없으면 그 기간의 Open-Meteo 과거 기상을 자동으로 받습니다.</div>
      {err && <div className="banner crit">{err}</div>}

      {ins && <>
        <h3 className="sub-h">보정할 수 있는 항목</h3>
        <div className="chips">
          {ins.stages.map((s) => <span key={s.stage} className={`chip ${s.possible ? "on" : ""}`}
            title={s.possible ? "" : `필요한 열: ${s.needs.join(", ")}`}>{s.possible ? "✓" : "–"} {s.label}</span>)}
        </div>
        <Fold title="열 매칭 확인·수정">
          {ins.tables.map((t) => {
            const m = ov[t.label] ?? t.mapping;
            const roleOf = (col: string) => Object.entries(m).find(([, c]) => c === col)?.[0] ?? "";
            return (
              <div key={t.label} className="table-scroll" style={{ maxHeight: 220, marginBottom: 8 }}>
                <div className="muted">{t.label} · {t.n_rows}행 {t.time_ok ? "" : "· ⚠ 시각 열을 지정하세요"}</div>
                <table className="data"><thead><tr><th>열</th><th>예시 값</th><th>역할</th></tr></thead>
                  <tbody>{t.header.map((h) => (
                    <tr key={h}><td>{h}</td><td className="muted">{t.samples[h]?.join(", ")}</td>
                      <td><select value={roleOf(h)} onChange={(e) => setRole(t.label, e.target.value, h)}>
                        <option value="">사용 안 함</option>
                        {t.roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select></td></tr>
                  ))}</tbody></table>
              </div>
            );
          })}
        </Fold>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={job.running || busy || !ins.stages.some((s) => s.possible)} onClick={run}>보정 실행</button>
          {ds && <span className="muted">{ds.period.join(" ~ ")} · {ds.n_days}일</span>}
        </div>
        <JobBar job={job.job} running={job.running} onCancel={job.cancel} />
        {job.error && <div className="banner crit">{job.error}</div>}
      </>}

      {res && <CalView res={res} busy={busy} onApply={apply} />}
    </div>
  );
}

function CalView({ res, busy, onApply }: { res: CalResult; busy: boolean; onApply: () => void }) {
  const [cls, msg] = VERDICT[res.verdict];
  const label = (m: string) => ({ total_cooling_kw: "총 냉방부하", chiller_kw: "칠러 전력", chiller_cop: "칠러 COP",
    crah_fan_kw: "CRAH 팬", pump_kw: "펌프", rack_inlet_max_c: "랙 흡입 최대", makeup_m3_h: "보급수" } as Record<string, string>)[m] ?? m;
  const s = res.series;
  const data = s ? s.measured.map((v, i) => ({ i, measured: v, before: s.before[i], after: s.after[i] })) : [];
  const improved = res.metrics.filter((m) => m.before?.cv_rmse_pct != null && m.after?.cv_rmse_pct != null);
  return (
    <div style={{ marginTop: 14 }}>
      <div className={`banner ${cls}`}>{msg} · {res.criteria}{res.short ? " · 검증 기간 부족(14일 미만)" : ""}</div>
      <Lead>
        계수 {res.params.length}개를 보정했습니다 ({res.train_days}일로 보정, {res.valid_days}일로 검증).
        {improved.length > 0 && ` ${improved.map((m) => `${label(m.metric)} 오차 ${fmt(m.before!.cv_rmse_pct, 1)}% → ${fmt(m.after!.cv_rmse_pct, 1)}%`).join(", ")}.`}
      </Lead>
      <table className="data">
        <thead><tr><th>계수</th><th>보정 전</th><th>보정 후</th><th>근거 지표</th><th /></tr></thead>
        <tbody>{res.params.map((p) => (
          <tr key={p.path}><td>{p.label}</td><td>{fmt(p.before, 3)}</td><td><b>{fmt(p.after, 3)}</b></td>
            <td>{p.metrics.map(label).join(", ")}</td>
            <td>{p.at_bound && <span style={{ color: "var(--critical)" }} title={`범위 ${p.bounds.join(" ~ ")}`}>범위 한계 — 데이터·모델 확인</span>}</td></tr>
        ))}</tbody>
      </table>
      {s && <>
        <h3 className="sub-h">검증 기간 {label(s.metric)} ({s.dates[0]}부터 {s.dates.length}일)</h3>
        <div className="legend">{SERIES.map(([, l, c]) => <span key={l}><span className="sw" style={{ background: c }} />{l}</span>)}</div>
        <div className="chart-box" style={{ height: 200 }}>
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="i" {...axis} tickFormatter={(i: number) => (i % 24 === 0 ? (s.dates[i / 24] ?? "").slice(5) : "")} interval={0} />
              <YAxis {...axis} width={50} />
              <Tooltip formatter={(v, n) => [fmt(Number(v), 1), SERIES.find(([k]) => k === n)?.[1] ?? String(n)]}
                labelFormatter={(i) => `${s.dates[Math.floor(Number(i) / 24)]} ${Number(i) % 24}시`} />
              {SERIES.map(([k, , c]) => <Line key={k} dataKey={k} stroke={c} dot={false} strokeWidth={k === "measured" ? 1.5 : 2}
                strokeDasharray={k === "before" ? "4 3" : undefined} isAnimationActive={false} connectNulls />)}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </>}
      <Fold title="지표별 오차 · 건너뛴 단계 · 메모">
        <table className="data"><thead><tr><th>지표</th><th>CV(RMSE) 전→후 %</th><th>NMBE 전→후 %</th><th>판정</th></tr></thead>
          <tbody>{res.metrics.map((m) => (
            <tr key={m.metric}><td>{label(m.metric)}</td>
              <td>{fmt(m.before?.cv_rmse_pct, 1)} → {fmt(m.after?.cv_rmse_pct, 1)}</td>
              <td>{fmt(m.before?.nmbe_pct, 1)} → {fmt(m.after?.nmbe_pct, 1)}</td>
              <td>{m.after?.pass == null ? "–" : m.after.pass ? <span className="ok">충족</span> : "미달"}</td></tr>
          ))}</tbody></table>
        <ul className="notes">
          {res.skipped.map((x) => <li key={x.stage}>건너뜀 · {x.label}: {x.reason}</li>)}
          {res.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      </Fold>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={busy || !res.params.length} onClick={onApply}>보정값 적용 · 재계산</button>
        <span className="muted">계수만 바뀌고 운전점·구조는 그대로입니다. 언제든 되돌릴 수 있습니다.</span>
      </div>
    </div>
  );
}
