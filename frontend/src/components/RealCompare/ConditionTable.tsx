import { useState } from "react";
import type { RealCompare } from "../../types/api";
import { BasisBadge } from "./common";

const show = (v: unknown) => {
  if (typeof v === "number") return v.toLocaleString("ko-KR", { maximumFractionDigits: 4 });
  if (typeof v === "boolean") return v ? "예" : "아니오";
  if (v && typeof v === "object") return "(묶음 값 — 비고 참고)";
  return String(v ?? "–");
};

/** 시나리오 입력: 사실(출처) / 가정 / 사용자 수정. 숫자 가정값은 고쳐서 다시 실행 */
export function ConditionTable({ result, busy, overrides, onRerun }: {
  result: RealCompare; busy: boolean; overrides: Record<string, unknown>;
  onRerun: (overrides: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const edits = Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim() !== "" && Number.isFinite(Number(v)))
    .map(([k, v]) => [k, Number(v)]));
  const changed = Object.keys(edits).length > 0;
  return (
    <div>
      <div className="table-scroll" style={{ maxHeight: 420 }}>
        <table className="data">
          <thead><tr><th>입력</th><th>값</th><th>구분</th><th style={{ textAlign: "left" }}>출처 · 근거</th></tr></thead>
          <tbody>{result.inputs.map((r, i) => {
            const numeric = r.editable && r.path && typeof r.value === "number";
            return (
              <tr key={r.path ?? `n${i}`}>
                <td>{r.label}{r.path && <div className="muted">{r.path}</div>}</td>
                <td>{numeric ? <input className="text" style={{ width: 100, textAlign: "right" }} aria-label={`${r.label} 값`}
                  value={draft[r.path!] ?? String(r.value)} onChange={(e) => setDraft({ ...draft, [r.path!]: e.target.value })} />
                  : show(r.value)}</td>
                <td><BasisBadge basis={r.basis} /></td>
                <td style={{ textAlign: "left", whiteSpace: "normal" }}>
                  {r.ref && <span className="ref">{r.ref}</span>}{r.ref && r.note ? " · " : ""}{r.note}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn primary" disabled={busy || !changed} onClick={() => { onRerun({ ...overrides, ...edits }); setDraft({}); }}>
          수정한 가정값으로 다시 실행</button>
        {Object.keys(overrides).length > 0 && <button className="btn small" disabled={busy} onClick={() => { setDraft({}); onRerun({}); }}>
          가정값 원래대로</button>}
        <span className="muted">사실 입력은 공개 자료 값이라 여기서 바꾸지 않습니다. 수정값은 출처 '사용자 수정'으로 기록됩니다.</span>
      </div>
      {result.sources.length > 0 && <div className="muted" style={{ marginTop: 8 }}>
        출처: {result.sources.map((s) => s.label).join(" · ")}</div>}
    </div>
  );
}
