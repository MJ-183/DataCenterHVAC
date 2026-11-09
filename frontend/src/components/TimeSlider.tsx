import { hhmm } from "../lib/format";
import { useSmoothMinute, type Clock } from "../hooks/usePlayback";

interface Props {
  clock: Clock;       // 하루 중 분 (연속)
  stepMin: number;
  playing: boolean;
  speedMs: number;    // 1시간 재생에 걸리는 시간 [ms]
  onMinute: (m: number) => void;
  onPlaying: (p: boolean) => void;
  onSpeed: (ms: number) => void;
}

export function TimeSlider({ clock, stepMin, playing, speedMs, onMinute, onPlaying, onSpeed }: Props) {
  const minute = Math.floor(useSmoothMinute(clock));
  return (
    <div className="slider-bar">
      <div className="slider">
        <button className="btn primary" onClick={() => onPlaying(!playing)} aria-label={playing ? "일시정지" : "재생"}>
          {playing ? "❚❚ 일시정지" : "▶ 재생"}
        </button>
        <button className="btn small" onClick={() => onMinute(minute - 60)} aria-label="1시간 전">−1h</button>
        <span className="clock">{hhmm(minute / 60)}</span>
        <button className="btn small" onClick={() => onMinute(minute + 60)} aria-label="1시간 후">+1h</button>
        <div style={{ flex: 1, minWidth: 120 }}>
          <input
            type="range" min={0} max={24 * 60 - 1} step={1} value={minute} style={{ width: "100%" }}
            onChange={(e) => onMinute(Number(e.target.value))} aria-label="시각"
          />
          <div className="ticks">{[0, 3, 6, 9, 12, 15, 18, 21, 24].map((t) => <span key={t}>{t}</span>)}</div>
        </div>
        <span className="muted" title="정시 사이 기상·가동률은 선형 보간(가정)">{stepMin}분 간격</span>
        <select value={speedMs} onChange={(e) => onSpeed(Number(e.target.value))} className="btn small" aria-label="재생 속도">
          <option value={3000}>느리게 (1시간/3초)</option>
          <option value={1200}>보통 (1시간/1.2초)</option>
          <option value={400}>빠르게 (1시간/0.4초)</option>
        </select>
      </div>
    </div>
  );
}
