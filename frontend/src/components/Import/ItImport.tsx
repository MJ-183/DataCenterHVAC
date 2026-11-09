import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ItApplyResult, ItDatasetSummary, ItKind, ItOverride, ItTable, RoleOption, Scenario } from "../../types/api";
import { type UploadFile, api, fileB64 } from "../../api/client";
import { fmt, hh } from "../../lib/format";

const KIND_LABEL: Record<ItKind, string> = {
  long: "세로형 (시각·랙·값)", wide: "가로형 (랙이 열)", total: "합계 (랙 없음)", inventory: "랙 목록 (설계 kW)",
  unknown: "판정 불가", ignore: "사용 안 함",
};
const UNITS = ["W", "kW", "MW", "Wh", "kWh", "MWh", "%", "fraction"];
const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void }

function TableCard({ t, roles, ov, onChange }: {
  t: ItTable; roles: RoleOption[]; ov: ItOverride | undefined; onChange: (o: ItOverride | undefined) => void;
}) {
  const setRole = (col: string, role: string) => {
    // 이 표의 매핑 전체를 사용자 지정으로 고정 (화면의 현재 판정 + 바꾼 열)
    const m: Record<string, string> = {};
    for (const p of t.profiles) {
      const r = p.name === col ? role : p.role;
      if (r !== "ignore" && r !== "wide" && !(r in m)) m[r] = p.name;
    }
    if (role === "ignore") m.ignore = col;
    onChange({ ...ov, mapping: m });
  };
  return (
    <div className="card" style={{ marginTop: 10, background: "var(--surface-2)" }}>
      <div className="row">
        <b>{t.label}</b>
        <span className="muted">{t.rows_n.toLocaleString()}행 · 헤더 {t.header_row >= 0 ? `${t.header_row + 1}번째 줄` : "없음"}
          {t.encoding ? ` · ${t.encoding}` : ""}</span>
        <span className="spacer" />
        <label className="muted">형태
          <select value={ov?.kind ?? t.kind} onChange={(e) => onChange({ ...ov, kind: e.target.value as ItKind })} style={{ marginLeft: 4 }}>
            {(Object.keys(KIND_LABEL) as ItKind[]).filter((k) => k !== "unknown" || t.kind === "unknown")
              .map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select>
        </label>
        {t.kind !== "inventory" && <label className="muted">단위
          <select value={ov?.unit ?? t.unit ?? ""} onChange={(e) => onChange({ ...ov, unit: e.target.value || undefined })} style={{ marginLeft: 4 }}>
            <option value="">자동</option>
            {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </label>}
        {ov && <button className="btn small" onClick={() => onChange(undefined)}>자동으로 되돌리기</button>}
      </div>
      {t.unit_basis && <div className="muted" style={{ marginTop: 4 }}>단위 판정: {t.unit_basis}</div>}
      {t.warnings.map((w, i) => <div key={i} className="banner warn">⚠ {w}</div>)}
      <div className="table-scroll" style={{ maxHeight: 240, marginTop: 6 }}>
        <table className="data">
          <thead><tr><th>열</th><th>예시 값</th><th>숫자 비율</th><th>역할</th><th>근거</th></tr></thead>
          <tbody>
            {t.profiles.map((p) => (
              <tr key={p.name}>
                <td>{p.name}{p.unit && <span className="tag">{p.unit}</span>}</td>
                <td className="muted" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>{p.samples.slice(0, 3).join(" | ")}</td>
                <td>{fmt(p.num_frac * 100, 0)}%</td>
                <td>{p.role === "wide"
                  ? <span>랙 값 열 (가로형)</span>
                  : <select value={p.role} onChange={(e) => setRole(p.name, e.target.value)}>
                    {roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>}</td>
                <td className="muted">{p.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const DAYTYPE = [["weekday", "평일", "var(--series-1)"], ["sat", "토", "var(--series-2)"], ["sun", "일", "var(--series-3)"]] as const;

/** 기간 데이터셋 요약: 일별 IT 전력량 달력 + 평일·토·일 24시간 */
function DatasetCard({ ds }: { ds: ItDatasetSummary }) {
  const days = Object.entries(ds.daily_kwh).sort();
  const vals = days.map(([, v]) => v);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const first = new Date(days[0][0] + "T00:00:00");
  const lead = (first.getDay() + 6) % 7;     // 월요일 시작
  const cells = [...Array(lead).fill(null), ...days];
  const prof = Array.from({ length: 24 }, (_, h) => ({ h, ...Object.fromEntries(DAYTYPE.map(([k]) => [k, ds.daytype_kw[k]?.[h]])) }));
  return (
    <div style={{ marginTop: 10 }}>
      <h3 className="sub-h">기간 데이터셋 저장됨 (연간·내일 예측에 사용)</h3>
      <div className="muted">{ds.sentence}</div>
      <div className="cal" title="일별 IT 전력량 (진할수록 큼)">
        {cells.map((c, i) => c ? <span key={i} className="cal-cell" title={`${c[0]} · ${fmt(c[1], 0)} kWh`}
          style={{ background: `color-mix(in srgb, var(--accent) ${Math.round(15 + 85 * (hi > lo ? (c[1] - lo) / (hi - lo) : 1))}%, var(--surface-2))` }} />
          : <span key={i} />)}
      </div>
      <div className="legend" style={{ marginTop: 6 }}>{DAYTYPE.filter(([k]) => ds.daytype_kw[k]).map(([k, l, c]) =>
        <span key={k}><span className="sw" style={{ background: c }} />{l}</span>)}</div>
      <div className="chart-box" style={{ height: 160 }}>
        <ResponsiveContainer>
          <LineChart data={prof} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="h" {...axis} tickFormatter={(h: number) => hh(h)} />
            <YAxis {...axis} width={50} />
            <Tooltip formatter={(v, n) => [`${fmt(Number(v), 0)} kW`, DAYTYPE.find(([k]) => k === n)?.[1] ?? String(n)]} labelFormatter={(h) => hh(Number(h))} />
            {DAYTYPE.filter(([k]) => ds.daytype_kw[k]).map(([k, , c]) =>
              <Line key={k} dataKey={k} stroke={c} dot={false} strokeWidth={2} isAnimationActive={false} />)}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function ItImport({ scenario, busy, onApply }: Props) {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [tables, setTables] = useState<ItTable[] | null>(null);
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [ov, setOv] = useState<Record<string, ItOverride>>({});
  const [day, setDay] = useState("peak");
  const [date, setDate] = useState("");
  const [result, setResult] = useState<ItApplyResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [work, setWork] = useState(false);

  const inspect = async (fs: UploadFile[], o: Record<string, ItOverride>) => {
    setErr(null); setResult(null); setWork(true);
    try {
      const r = await api.itInspect(fs, o);
      setTables(r.tables); setRoles(r.roles);
    } catch (e) { setErr((e as Error).message); setTables(null); }
    setWork(false);
  };

  const onFiles = async (list: FileList | null) => {
    if (!list?.length) return;
    const fs = await Promise.all([...list].map(async (f) => ({ name: f.name, data_b64: await fileB64(f) })));
    setFiles(fs); setOv({});
    inspect(fs, {});
  };

  const change = (label: string, o: ItOverride | undefined) => {
    const next = { ...ov };
    if (o) next[label] = o; else delete next[label];
    setOv(next);
    inspect(files, next);
  };

  const dayVal = day === "date" ? date : day;
  const apply = async () => {
    setErr(null); setWork(true);
    try { setResult(await api.itApply(files, ov, dayVal, scenario)); }
    catch (e) { setErr((e as Error).message); setResult(null); }
    setWork(false);
  };

  const rep = result?.report;
  const items = scenario.cooling_layout?.racks.items;
  return (
    <div className="card">
      <div className="card-head">
        <h2>② IT 부하 데이터</h2>
        <span className="hint">CSV·TSV·엑셀(.xlsx) 여러 개 가능 · 컬럼은 헤더 이름과 값 모양으로 자동 매칭 · 랙 목록 + 계측 시계열을 함께 올려도 됨</span>
      </div>
      <div className="row">
        <label className="btn">파일 업로드<input type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xlsm" hidden
          onChange={(e) => onFiles(e.target.files)} /></label>
        {files.length > 0 && <span className="muted">{files.map((f) => f.name).join(", ")}</span>}
        {work && <span className="muted">처리 중…</span>}
      </div>
      <div className="muted" style={{ marginTop: 6 }}>
        현재 배치: {items ? `도면 랙 ${items.length}개 (id 로 매칭)` : "기본 격자 (도면을 먼저 가져오면 랙 id 로 매칭, 아니면 id 순서대로 격자에 배정)"}
      </div>
      {err && <div className="banner crit">{err}</div>}

      {tables?.map((t) => <TableCard key={t.label} t={t} roles={roles} ov={ov[t.label]} onChange={(o) => change(t.label, o)} />)}

      {tables && (
        <div className="row" style={{ marginTop: 12 }}>
          <label className="muted">하루 선택
            <select value={day} onChange={(e) => setDay(e.target.value)} style={{ marginLeft: 4 }}>
              <option value="peak">설계일 (IT 합계 최대인 날)</option>
              <option value="mean">시각별 평균</option>
              <option value="date">날짜 지정</option>
            </select>
          </label>
          {day === "date" && <input type="date" className="text" style={{ width: 160 }} value={date} onChange={(e) => setDate(e.target.value)} />}
          <button className="btn primary" disabled={work || (day === "date" && !date)} onClick={apply}>프로파일 만들기</button>
        </div>
      )}

      {rep && result && (
        <div style={{ marginTop: 14 }}>
          {rep.warnings.length > 0 && <div className="banner warn">{rep.warnings.map((w) => `⚠ ${w}`).join("\n")}</div>}
          <div className="import-grid">
            <div>
              <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
                <div className="kpi"><div className="label">IT 설계부하</div><div className="value">{fmt(rep.design_kw, 0)}<span className="unit">kW</span></div></div>
                <div className="kpi"><div className="label">선택일 최대 IT</div><div className="value">{fmt(rep.peak_kw, 0)}<span className="unit">kW</span></div>
                  <div className="detail">{rep.day ?? "시 기준"}{rep.period.length ? ` (기간 ${rep.period.join(" ~ ")})` : ""}</div></div>
                <div className="kpi"><div className="label">랙 매칭</div>
                  <div className="value" style={rep.racks.matched < rep.racks.data ? { color: "var(--critical)" } : undefined}>
                    {rep.racks.matched}<span className="unit">/ 데이터 {rep.racks.data} · 배치 {rep.racks.layout}</span></div></div>
              </div>
              {rep.racks.unmatched_data_n > 0 && <div className="banner warn">
                배치에 없는 데이터 id {rep.racks.unmatched_data_n}개: {rep.racks.unmatched_data.slice(0, 12).join(", ")}{rep.racks.unmatched_data_n > 12 ? " …" : ""}
              </div>}
              <ul className="notes">{rep.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
              {result.dataset && <DatasetCard ds={result.dataset} />}
            </div>
            <div>
              {rep.total_profile_kw && <>
                <h3 className="sub-h">IT 합계 프로파일 [kW]</h3>
                <div className="chart-box">
                  <ResponsiveContainer>
                    <LineChart data={rep.total_profile_kw.map((v, h) => ({ h, v }))} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                      <CartesianGrid stroke="var(--grid)" vertical={false} />
                      <XAxis dataKey="h" {...axis} tickFormatter={(h: number) => hh(h)} />
                      <YAxis {...axis} width={50} />
                      <Tooltip formatter={(v) => [`${fmt(Number(v), 1)} kW`, "IT"]} labelFormatter={(h) => hh(Number(h))} />
                      <Line dataKey="v" stroke="var(--series-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </>}
            </div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={busy} onClick={() => onApply(result.scenario)}>시나리오에 적용 · 재계산</button>
            <span className="muted">IT 설계부하·시간별 가동률과 랙별 24시간 전력이 바뀝니다 (기류 모델 랙 발열 배분에 사용)</span>
          </div>
        </div>
      )}
    </div>
  );
}
