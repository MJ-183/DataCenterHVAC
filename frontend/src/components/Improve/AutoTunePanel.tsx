import { useEffect, useState } from "react";
import type { Containment, EvaluateResult, Scenario, TuneInfluence, TuneResult, TuneSetting } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { JobBar, Lead, useJob } from "../../lib/ui";

const SAVE = "autotune:v1";
const CONT: Record<Containment, string> = { open: "개방형", cold_aisle: "콜드아일", hot_aisle: "핫아일" };
const OBJ = { min_change: "가장 적게 바꾸기", min_energy: "냉방 전력 최소" } as const;
type Obj = keyof typeof OBJ;
interface Saved { settings: TuneSetting[]; margin_c: number; objective: Obj; auto: boolean }

function load(): Saved | null {
  try { return JSON.parse(localStorage.getItem(SAVE) ?? "null"); } catch { return null; }
}
function save(s: Saved) {
  try { localStorage.setItem(SAVE, JSON.stringify(s)); } catch { /* 저장 불가여도 이번 화면에는 적용됨 */ }
}

/** 자동 튜닝 설정 (브라우저에 기억). 저장된 것이 없으면 서버 기본값 (공사 항목 꺼짐) */
export function useTuneSettings(scenario: Scenario) {
  const [s, setS] = useState<Saved | null>(load);
  useEffect(() => {
    if (s) return;
    api.autotuneDefaults(scenario).then((d) => setS({ settings: d.settings, margin_c: d.margin_c, objective: "min_change", auto: false }))
      .catch(() => undefined);
  }, [s, scenario]);
  const set = (x: Saved) => { setS(x); save(x); };
  const reset = () => api.autotuneDefaults(scenario).then((d) => set({ settings: d.settings, margin_c: d.margin_c,
    objective: "min_change", auto: s?.auto ?? false }));
  return { saved: s, set, reset };
}

export const tuneBody = (scenario: Scenario, s: Saved) => ({
  scenario, margin_c: s.margin_c, objective: s.objective,
  settings: s.settings.map(({ key, enabled, min, max, step, choices }) => ({ key, enabled, min, max, step, choices })),
});
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void }

/** 최적화 → 자동 튜닝: 주의·경고가 나면 '중요한 파라미터' 순서대로 바꿔 해결 */
export function AutoTunePanel({ scenario, busy, onApply }: Props) {
  const { saved, set, reset } = useTuneSettings(scenario);
  const job = useJob<TuneResult>("autotune");
  const [inf, setInf] = useState<TuneInfluence | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (!saved) return <div className="muted">설정 불러오는 중…</div>;
  const st = saved.settings;
  const upd = (i: number, patch: Partial<TuneSetting>) => set({ ...saved, settings: st.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= st.length) return;
    const a = [...st];
    [a[i], a[j]] = [a[j], a[i]];
    set({ ...saved, settings: a });
  };
  const body = tuneBody(scenario, saved);
  const r = job.result;
  const fresh = r && same(job.request, body);
  const infBy = new Map((inf?.items ?? []).map((x) => [x.key, x]));

  const byInfluence = async () => {
    setMsg(null);
    try {
      const x = await api.autotuneInfluence(body);   // 매번 다시 (시나리오가 바뀌었을 수 있음)
      setInf(x);
      // 지금 있는 문제를 푸는 효과만 점수로 (문제가 없으면 흡입 여유 기준), 전력 증가는 감점
      const p = x.problems.length ? x.problems : ["inlet"];
      const score = (k: string) => { const e = x.items.find((y) => y.key === k);
        if (!e || e.note) return -99;
        const gain = (p.includes("inlet") ? e.inlet_margin_k ?? 0 : 0) + (p.includes("coil_return") ? e.return_slack_k ?? 0 : 0)
          + (p.includes("coil_supply") ? e.supply_slack_k ?? 0 : 0);
        return gain - (e.kwh ?? 0) / Math.max(x.base_kwh, 1) * 10; };
      const a = [...st].sort((p, q) => Number(!!p.construction) - Number(!!q.construction) || score(q.key) - score(p.key));
      set({ ...saved, settings: a });
      const what = { inlet: "랙 흡입 여유", coil_supply: "코일 공급 접근온도", coil_return: "코일 환수 접근온도" } as Record<string, string>;
      setMsg(`영향도 순으로 정렬했습니다 (공사 항목은 뒤로) — 기준: ${(x.problems.length ? x.problems : ["inlet"]).map((c) => what[c]).join(", ")}`
        + `${x.problems.length ? "" : " (지금은 주의·경고가 없어 흡입 여유 기준)"}. 한 단계 바꿨을 때 이 여유가 많이 늘고 전력이 덜 느는 파라미터가 위로 갑니다.`);
    } catch (e) { setMsg((e as Error).message); }
  };
  const apply = async () => {
    if (!r?.params) return;
    try { onApply(await api.autotuneApply(scenario, r.params)); setMsg("적용했습니다 — 위의 '되돌리기'로 원래대로 돌릴 수 있습니다."); }
    catch (e) { setMsg((e as Error).message); }
  };

  return (
    <div>
      <Lead>주의·경고(랙 흡입온도 여유 부족, 코일 접근온도 충돌)가 생기면 <b>아래 순서대로</b> 파라미터를 바꿔 해결합니다.
        1순위만으로 먼저 풀어 보고, 안 되면 다음 순위를 하나씩 더합니다 — 뒤 순위는 앞 순위로 해결이 안 될 때만 건드립니다.</Lead>
      <div className="table-scroll">
        <table className="data tune">
          <thead><tr><th>순위</th><th>사용</th><th>파라미터</th><th>허용 범위</th><th>간격</th><th>한 단계 효과 (흡입 여유 / 전력)</th><th /></tr></thead>
          <tbody>
            {st.map((x, i) => {
              const e = infBy.get(x.key);
              return (
                <tr key={x.key} className={x.enabled ? "" : "off"}>
                  <td>{x.enabled ? st.filter((y, j) => y.enabled && j <= i).length : "–"}</td>
                  <td><input type="checkbox" checked={x.enabled} onChange={(ev) => upd(i, { enabled: ev.target.checked })} /></td>
                  <td>{x.label}{x.construction && <span className="tag">공사</span>}<div className="muted">{x.help}</div></td>
                  <td>{x.key === "containment"
                    ? (["open", "cold_aisle", "hot_aisle"] as Containment[]).map((c) => (
                      <label key={c} style={{ marginRight: 8, whiteSpace: "nowrap" }}>
                        <input type="checkbox" checked={(x.choices ?? []).includes(c)}
                          onChange={(ev) => upd(i, { choices: ev.target.checked ? [...(x.choices ?? []), c] : (x.choices ?? []).filter((y) => y !== c) })} /> {CONT[c]}
                      </label>))
                    : <span className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                      <input className="text num" type="number" value={x.min ?? ""} step={x.step ?? 1} onChange={(ev) => upd(i, { min: Number(ev.target.value) })} />
                      ~<input className="text num" type="number" value={x.max ?? ""} step={x.step ?? 1} onChange={(ev) => upd(i, { max: Number(ev.target.value) })} />
                      <span className="muted">{x.unit}</span></span>}</td>
                  <td>{x.key === "containment" ? "–" :
                    <input className="text num" type="number" value={x.step ?? ""} min={0} step="any" onChange={(ev) => upd(i, { step: Number(ev.target.value) })} />}</td>
                  <td className="muted">{e ? (e.note ?? `${e.step}: 여유 ${fmt(e.inlet_margin_k ?? 0, 2)}K`
                    + ((e.return_slack_k ?? 0) ? ` · 환수 ${fmt(e.return_slack_k!, 2)}K` : "")
                    + ((e.supply_slack_k ?? 0) ? ` · 코일 ${fmt(e.supply_slack_k!, 2)}K` : "")
                    + ` · ${(e.kwh ?? 0) >= 0 ? "+" : ""}${fmt(e.kwh ?? 0, 0)} kWh/일`) : ""}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="btn small" disabled={i === 0} onClick={() => move(i, -1)} title="우선순위 올리기">↑</button>
                    <button className="btn small" disabled={i === st.length - 1} onClick={() => move(i, 1)} title="우선순위 내리기">↓</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="form-grid" style={{ marginTop: 10 }}>
        <div className="field"><label>목표 랙 흡입온도 여유 [K]</label>
          <input className="text" type="number" min={0} max={5} step={0.5} value={saved.margin_c}
            onChange={(e) => set({ ...saved, margin_c: Number(e.target.value) })} />
          <span className="muted">1K 이상이면 운전 현황·브리핑의 '여유 작음' 주의도 사라집니다</span></div>
        <div className="field"><label>해가 여러 개일 때</label>
          <div className="seg">{(Object.keys(OBJ) as Obj[]).map((k) =>
            <button key={k} className={saved.objective === k ? "on" : ""} onClick={() => set({ ...saved, objective: k })}>{OBJ[k]}</button>)}</div>
          <span className="muted" style={{ display: "block", marginTop: 4 }}>{saved.objective === "min_change" ? "현재 운전에서 가장 덜 바꾸는 해 (뒤 순위 변경은 더 비싸게 봄)" : "경고가 없는 해 중 하루 냉방 전력이 가장 작은 해"}</span></div>
        <div className="field"><label>운전 현황에서</label>
          <label style={{ fontWeight: 400 }}><input type="checkbox" checked={saved.auto} onChange={(e) => set({ ...saved, auto: e.target.checked })} />
            {" "}주의·경고가 생기면 자동으로 튜닝안 계산 (적용은 직접)</label></div>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={busy || job.running || !st.some((x) => x.enabled)} onClick={() => job.start("autotune", body)}>
          {job.running ? "튜닝 중…" : "자동 튜닝 실행"}</button>
        <button className="btn" onClick={byInfluence} disabled={busy}>영향도 순으로 정렬</button>
        <button className="btn" onClick={() => { setInf(null); reset(); }}>기본 설정으로</button>
      </div>
      <JobBar job={job.job} running={job.running} onCancel={job.cancel} />
      {job.error && <div className="banner crit">{job.error}</div>}
      {msg && <div className="banner info">{msg}</div>}
      {r && <TuneResultView r={r} fresh={!!fresh} busy={busy} onApply={apply} scenario={scenario} />}
    </div>
  );
}

export function TuneResultView({ r, fresh, busy, onApply, scenario, compact }: {
  r: TuneResult; fresh: boolean; busy: boolean; onApply: () => void; scenario: Scenario; compact?: boolean;
}) {
  const level = r.status === "solved" ? "info" : r.status === "ok" ? "info" : "warn";
  const a = r.after;
  return (
    <div style={{ marginTop: 12 }}>
      {!fresh && <div className="banner warn">조건(시나리오·설정)이 바뀌어 이 결과는 예전 계산입니다 — 다시 실행하세요</div>}
      <div className={`banner ${level}`}>{r.status === "solved" ? "✓ " : r.status === "ok" ? "ⓘ " : "⚠ "}{r.message}</div>
      {r.status === "solved" && a && <>
        <table className="data" style={{ marginTop: 8 }}>
          <thead><tr><th /><th>최대 흡입 ℃</th><th>여유 K</th><th>초과 랙</th><th>일 냉방 전력 kWh</th><th>주의·경고</th></tr></thead>
          <tbody>
            {([["지금", r.before], ["튜닝 후", a]] as const).map(([l, x]) => (
              <tr key={l}><td>{l}</td><td>{fmt(x.max_inlet_c, 1)}</td><td>{fmt(x.inlet_margin_c, 2)}</td><td>{x.racks_over_limit}</td>
                <td>{fmt(x.daily_kwh, 0)}</td><td>{x === r.before ? (x.problems.length ? x.problems.join(" · ") : "없음")
                  : (x.issues.length ? x.issues.map((i) => i.title).join(" · ") : "없음")}</td></tr>))}
          </tbody>
        </table>
        <h3 className="sub-h" style={{ marginTop: 10 }}>바꿀 값 ({r.changes?.length ?? 0}개)</h3>
        <ul className="tune-changes">
          {r.changes?.map((c) => <li key={c.key}><b>{c.label}</b> {typeof c.from === "number" ? fmt(c.from, 2) : c.from} → <b>{typeof c.to === "number" ? fmt(c.to, 2) : c.to}</b>{c.unit}
            {c.construction && <span className="tag">공사</span>}</li>)}
        </ul>
        <div className="muted">하루 냉방 전력 {(r.saving_kwh ?? 0) >= 0 ? "절감" : "증가"} {fmt(Math.abs(r.saving_kwh ?? 0), 0)} kWh
          {scenario.cooling_layout?.operating_schedule ? " · 적용하면 월별 운전 스케줄은 해제되고 이 운전점 하나로 운전합니다" : ""}</div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn primary" disabled={busy || !fresh} onClick={onApply}>이 설정 적용</button>
        </div>
      </>}
      {!compact && <details style={{ marginTop: 8 }}>
        <summary className="muted" style={{ cursor: "pointer" }}>방법·단계별 탐색 결과</summary>
        <div className="muted" style={{ margin: "6px 0", lineHeight: 1.6 }}>{r.method}</div>
        <ol className="muted">{r.stages.map((s, i) => <li key={i}>{s.params.length}개 파라미터까지: 해 {s.feasible}개{s.feasible ? " → 여기서 멈춤" : ""}</li>)}</ol>
        {!!r.alternatives?.length && <div className="muted">다른 해: {r.alternatives.map((x) => `${x.changed.join("·") || "변경 없음"} (${fmt(x.kwh, 0)} kWh)`).join(" / ")}</div>}
      </details>}
    </div>
  );
}

/** 운전 현황: 주의·경고가 있으면 자동 튜닝 제안 (설정에서 '자동 계산'을 켜면 바로 계산) */
export function AutoTuneBanner({ scenario, cooling, busy, onApply, onSettings }: {
  scenario: Scenario; cooling: EvaluateResult | null; busy: boolean; onApply: (sc: Scenario) => void; onSettings: () => void;
}) {
  const { saved } = useTuneSettings(scenario);
  const job = useJob<TuneResult>("autotune");
  const problem = !!cooling?.issues?.some((i) => i.code !== "mixed");
  const body = saved ? tuneBody(scenario, saved) : null;
  const fresh = !!job.result && !!body && same(job.request, body);
  useEffect(() => {
    if (saved?.auto && problem && body && !job.running && !fresh) job.start("autotune", body);
  }, [saved?.auto, problem, fresh, job.running]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!problem || !saved || !body) return null;
  const apply = async () => { if (job.result?.params) onApply(await api.autotuneApply(scenario, job.result.params)); };
  return (
    <div className="card">
      <div className="card-head"><h2>자동 튜닝</h2><span className="hint">주의·경고를 우선순위 파라미터로 해결 · 설정은 '최적화 → 자동 튜닝'</span>
        <span className="spacer" /><button className="btn small" onClick={onSettings}>설정</button></div>
      {!fresh && !job.running && <button className="btn primary" disabled={busy} onClick={() => job.start("autotune", body)}>
        자동 튜닝으로 해결안 찾기</button>}
      <JobBar job={job.job} running={job.running} onCancel={job.cancel} />
      {job.error && <div className="banner crit">{job.error}</div>}
      {fresh && job.result && <TuneResultView r={job.result} fresh busy={busy} onApply={apply} scenario={scenario} compact />}
    </div>
  );
}
