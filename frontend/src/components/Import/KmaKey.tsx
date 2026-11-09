import { useEffect, useState } from "react";
import { api, type KmaKeyStatus } from "../../api/client";
import { Card } from "../../lib/ui";

/** 기상청(공공데이터포털) 인증키 입력 → backend/.env 저장 → 서비스별 호출 확인. 저장 즉시 예보·관측 수집에 쓰임 */
export function KmaKey({ onChange }: { onChange?: (configured: boolean) => void }) {
  const [st, setStRaw] = useState<KmaKeyStatus | null>(null);
  const [key, setKey] = useState("");
  const [work, setWork] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const setSt = (s: KmaKeyStatus) => { setStRaw(s); onChange?.(s.configured); };

  useEffect(() => { api.kmaKey().then(setSt).catch((e) => setErr((e as Error).message)); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (f: () => Promise<KmaKeyStatus>) => {
    setErr(null); setWork(true);
    try { setSt(await f()); setKey(""); } catch (e) { setErr((e as Error).message); }
    setWork(false);
  };

  return (
    <Card title="기상청 API 인증키" hint="공공데이터포털 일반 인증키 — 입력하면 아래 지역 선택과 연간 예측이 기상청 관측(기온·습도·일사)을 받아 계산합니다 (서버 재시작 불필요)"
      right={st?.configured ? <span className="badge ok">설정됨 {st.masked}</span> : <span className="badge">미설정</span>}>
      <div className="row">
        <input className="text" type="password" autoComplete="off" style={{ flex: 1, minWidth: 260 }}
          placeholder={st?.configured ? "새 키로 바꾸려면 입력" : "Encoding 키 또는 Decoding 키를 그대로 붙여넣기"}
          value={key} onChange={(e) => setKey(e.target.value)} />
        <button className="btn primary" disabled={work || key.trim().length < 10} onClick={() => run(() => api.kmaKeySave(key.trim()))}>
          {work ? "확인 중…" : "저장 · 확인"}</button>
        {st?.configured && <button className="btn" disabled={work} onClick={() => run(api.kmaKeyTest)}>다시 확인</button>}
        {st?.configured && <button className="btn" disabled={work} onClick={() => run(api.kmaKeyClear)}>삭제</button>}
      </div>
      {err && <div className="banner crit">{err}</div>}
      {st?.tests && <ul className="notes" style={{ marginTop: 8 }}>
        {st.tests.map((t) => <li key={t.service}>
          <span style={{ color: t.ok ? "var(--good)" : "var(--critical)" }}>{t.ok ? "✓" : "✕"}</span> {t.name} — {t.message}</li>)}
      </ul>}
      <div className="muted" style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.6 }}>
        키는 backend/.env 의 KMA_SERVICE_KEY 에 저장되며 브라우저로 다시 보내지 않습니다 (끝 4자리만 표시). VS Code 에서 .env 를 직접 고쳐도 바로 반영됩니다.
        공공데이터포털은 서비스마다 활용신청이 따로 필요합니다: {st?.services.map((s) => s.name).join(", ")}. 승인 직후에는 키 등록 반영까지 시간이 걸릴 수 있습니다.
      </div>
    </Card>
  );
}
