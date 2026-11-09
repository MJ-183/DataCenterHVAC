import { useEffect, useState, useSyncExternalStore } from "react";

const DAY_MIN = 24 * 60;

/** 연속 재생 시계 (분, 실수). 매 프레임 바뀌므로 React 상태가 아닌 외부 저장소 — 구독한 컴포넌트만 다시 그림 */
export interface Clock {
  get: () => number;
  set: (m: number) => void;
  subscribe: (fn: () => void) => () => void;
}

function createClock(initial: number): Clock {
  let value = initial;
  const subs = new Set<() => void>();
  return {
    get: () => value,
    set: (m) => { value = ((m % DAY_MIN) + DAY_MIN) % DAY_MIN; subs.forEach((f) => f()); },
    subscribe: (fn) => { subs.add(fn); return () => subs.delete(fn); },
  };
}

/** 매 프레임 갱신되는 연속 시각 [분] (3D·시각 막대처럼 부드럽게 움직여야 하는 곳) */
export const useSmoothMinute = (clock: Clock) => useSyncExternalStore(clock.subscribe, clock.get);

/**
 * 하루 중 시각(분) 재생. playing 이면 requestAnimationFrame 으로 msPerHour 속도로 연속 진행, 24:00 다음은 00:00.
 * minute 는 계산 간격(stepMin)으로 내림한 값 — 차트·KPI 는 이 값으로만 다시 그림. 연속 값은 clock 구독.
 * 시작 시각은 URL ?hour=0~23 (공유·재현용), 없으면 12:00.
 */
export function usePlayback(stepMin: number, msPerHour = 900) {
  const [clock] = useState(() => {
    const raw = new URLSearchParams(window.location.search).get("hour");
    const q = Number(raw);
    return createClock(raw !== null && Number.isInteger(q) && q >= 0 && q <= 23 ? q * 60 : 12 * 60);
  });
  const [playing, setPlaying] = useState(false);
  const minute = useSyncExternalStore(clock.subscribe, () => Math.floor(clock.get() / stepMin) * stepMin);

  useEffect(() => {
    if (!playing) return;
    let id = 0, last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(now - last, 100);   // 탭 전환 등으로 멈췄다 돌아와도 한 번에 건너뛰지 않음
      last = now;
      clock.set(clock.get() + (dt * 60) / msPerHour);
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [playing, msPerHour, clock]);

  /** 임의 시각 설정 (음수·24시 이상은 하루 안으로 순환). 계산 간격 사이 값은 보간으로 표시 */
  const setTime = (m: number) => clock.set(m);

  return { minute, setMinute: setTime, playing, setPlaying, clock, index: Math.floor(minute / stepMin) };
}
