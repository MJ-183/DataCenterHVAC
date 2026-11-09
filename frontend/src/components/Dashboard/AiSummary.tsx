import { useEffect, useState } from "react";
import type { SimulationResult } from "../../types/api";
import { api } from "../../api/client";
import { Card } from "../../lib/ui";

type Status = { enabled: boolean; model: string | null; reason: string | null };

/** AI 요약: 지금 결과를 OpenAI 로 해설 (키는 backend/.env, 저장하면 서버 재시작 없이 반영) */
export function AiSummary({ result }: { result: SimulationResult }) {
  const [st, setSt] = useState<Status | null>(null);
  const [out, setOut] = useState<{ text: string; model: string; unverified_numbers: string[]; of: SimulationResult } | null>(null);
  const [work, setWork] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const check = () => api.aiStatus().then(setSt).catch((e) => setErr((e as Error).message));
  useEffect(() => { check(); }, []);

  const run = async () => {
    setErr(null); setWork(true);
    try { setOut({ ...(await api.explain(result)), of: result }); } catch (e) { setErr((e as Error).message); }
    setWork(false);
  };
  const stale = out && out.of !== result;

  return (
    <Card title="AI 요약" hint={st?.enabled ? `OpenAI · ${st.model}` : "OpenAI (선택 기능)"}
      right={st?.enabled
        ? <button className="btn small primary" disabled={work} onClick={run}>{work ? "요약 중…" : out ? "다시 요약" : "지금 결과 요약"}</button>
        : <button className="btn small" onClick={check}>설정 다시 확인</button>}>
      {st && !st.enabled && <div className="banner info">
        AI 요약이 꺼져 있습니다 ({st.reason}). VS Code 에서 <b>backend/.env</b> 를 열어 <code>OPENAI_API_KEY=</code> 와
        <code> OPENAI_MODEL=</code> 에 값을 넣고 저장한 뒤 '설정 다시 확인'을 누르세요 (서버 재시작 불필요, 키는 브라우저로 전달되지 않음).
      </div>}
      {err && <div className="banner crit">{err}</div>}
      {stale && <div className="banner warn">⚠ 입력이 바뀌어 아래 요약은 이전 결과 기준입니다 — '다시 요약'을 누르세요</div>}
      {out && <>
        <div className="ai-text">{out.text}</div>
        {out.unverified_numbers.length > 0 && <div className="banner warn">
          ⚠ 결과 데이터와 맞지 않는 숫자: {out.unverified_numbers.join(", ")} — 이 부분은 믿지 마세요</div>}
      </>}
      {st?.enabled && !out && !work && <div className="muted">버튼을 누르면 지금 하루 결과(부하·칠러·냉수·보급수)를 코드로 계산한 지표만 인용해 요약합니다.</div>}
    </Card>
  );
}
