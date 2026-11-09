import type { LoadKw } from "../types/api";

export const fmt = (v: number | null | undefined, digits = 1) =>
  v == null || Number.isNaN(v)
    ? "–"
    : v.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
/** 실수 시각[h] 또는 하루 중 분 -> "HH:MM" */
export const hhmm = (t_h: number) => {
  const m = Math.round(t_h * 60);
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};

// 부하 구성 그룹 (backend/hvac/insights.py LOAD_GROUPS 와 동일 구성). 순서 = 범주색 슬롯 순서
export const LOAD_GROUPS: { key: string; label: string; color: string; parts: (keyof LoadKw)[] }[] = [
  { key: "it", label: "IT", color: "var(--series-1)", parts: ["it"] },
  { key: "ups", label: "UPS 손실", color: "var(--series-2)", parts: ["ups_loss"] },
  { key: "env", label: "외피(관류+일사)", color: "var(--series-3)",
    parts: ["wall_conduction", "roof_conduction", "window_conduction", "window_solar"] },
  { key: "vent", label: "외기 도입", color: "var(--series-4)", parts: ["ventilation"] },
  { key: "int", label: "조명·인원", color: "var(--series-5)", parts: ["lighting", "people"] },
  { key: "fan", label: "CRAH 팬", color: "var(--series-6)", parts: ["crah_fan"] },
];

export const groupLoads = (l: LoadKw) =>
  Object.fromEntries(LOAD_GROUPS.map((g) => [g.key, +g.parts.reduce((s, k) => s + l[k], 0).toFixed(2)]));
