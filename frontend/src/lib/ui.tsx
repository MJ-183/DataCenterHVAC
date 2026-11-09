import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Job } from "../types/api";
import { api } from "../api/client";

/** 화면을 옮겨도 남는 상태 (모듈 메모리). 새로고침하면 초기화 */
const STORE = new Map<string, unknown>();
export function usePersist<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => (STORE.has(key) ? (STORE.get(key) as T) : init));
  const set = useCallback((x: T) => { STORE.set(key, x); setV(x); }, [key]);
  return [v, set];
}

/** 오래 걸리는 계산(작업 API): 진행률 조회, 취소, 화면 이동 후 이어 보기 */
export function useJob<T>(key: string) {
  const [jobId, setJobId] = usePersist<string | null>(`job:${key}`, null);
  const [job, setJob] = usePersist<Job<T> | null>(`jobv:${key}`, null);
  const [request, setRequest] = usePersist<unknown>(`jobreq:${key}`, null);   // 결과를 만든 입력 (보고서의 조건 일치 확인)
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    const tick = async () => {
      try {
        const j = await api.job<T>(jobId);
        if (!alive) return;
        setJob(j);
        if (j.status === "queued" || j.status === "running") timer.current = window.setTimeout(tick, 500);
        else setJobId(null);
      } catch (e) {
        if (alive) { setErr((e as Error).message); setJobId(null); }
      }
    };
    tick();
    return () => { alive = false; if (timer.current) window.clearTimeout(timer.current); };
  }, [jobId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const start = async (kind: string, body: unknown) => {
    setErr(null);
    try {
      const { job_id } = await api.startJob(kind, body);
      setJob({ id: job_id, kind, status: "queued", progress: 0, stage: "시작", result: null, error: null });
      setRequest(body);
      setJobId(job_id);
    } catch (e) { setErr((e as Error).message); }
  };
  const cancel = () => { if (jobId) api.cancelJob(jobId).catch(() => undefined); };
  const running = !!jobId;
  return { job, start, cancel, running, request, error: err ?? (job?.status === "error" ? job.error : null),
    result: job?.status === "done" ? (job.result as T) : null };
}

export function JobBar({ job, running, onCancel }: { job: Job | null; running: boolean; onCancel: () => void }) {
  if (!running || !job) return null;
  return (
    <div className="jobbar">
      <div className="jobbar-track"><div className="jobbar-fill" style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
      <span>{job.stage || "계산 중"} · {Math.round(job.progress * 100)}%</span>
      <button className="btn small" onClick={onCancel}>취소</button>
    </div>
  );
}

/** 접이식 구역 (고급 설정·상세 표). force 값이 바뀌면 펼침 (해결 방법 바로가기) */
export function Fold({ title, children, open = false, force }: { title: ReactNode; children: ReactNode; open?: boolean; force?: number }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (force && ref.current) ref.current.open = true; }, [force]);
  return (
    <details ref={ref} className="fold" open={open}>
      <summary>{title}</summary>
      <div className="fold-body">{children}</div>
    </details>
  );
}

/** 결과 맨 위 요약 문장 */
export function Lead({ children }: { children: ReactNode }) {
  return <p className="lead">{children}</p>;
}

export function DemoNote({ children }: { children?: ReactNode }) {
  return <div className="banner demo">ⓘ {children ?? "예시 데이터를 이용해 시연 계산한 결과입니다. 실제 값으로 바꾸면 다시 계산됩니다."}</div>;
}

export function Card({ title, hint, right, children }: { title: ReactNode; hint?: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head"><h2>{title}</h2>{hint && <span className="hint">{hint}</span>}<span className="spacer" />{right}</div>
      {children}
    </section>
  );
}

export const krw = (v: number | null | undefined) =>
  v == null ? "–" : Math.abs(v) >= 1e8 ? `${(v / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억 원`
    : `${Math.round(v / 1e4).toLocaleString("ko-KR")}만 원`;
