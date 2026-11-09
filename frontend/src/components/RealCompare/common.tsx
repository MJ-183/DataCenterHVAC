import type { RealCompare, RunKey } from "../../types/api";
import { fmt } from "../../lib/format";

/** 실행별 고정 색 (순서가 바뀌어도 유지). 실측은 다크 모드에서 배경과 겹치지 않도록 CSS 변수(--rc-meas)로 */
export const RUN_COLOR: Record<RunKey | "meas", string> = {
  meas: "var(--rc-meas)", A: "#9aa5b1", B: "#1f6fd1", C: "#d9822b",
};
export const RUN_KEYS: RunKey[] = ["A", "B", "C"];
export const SIM_KEY_LABEL: Record<string, string> = {
  tower_fan: "냉각탑 팬", chw_pump: "냉수 펌프", cw_pump: "냉각수 펌프", liquid_pump: "액체 루프 펌프",
  crah_fan: "CRAH 팬", chiller: "칠러",
};
export const SIM_KEYS = Object.keys(SIM_KEY_LABEL);

export const runsOf = (c: RealCompare) => RUN_KEYS.filter((k) => c.runs[k]);
/** 비교 항목 (매핑 항목 + 기계 합계) */
export const itemsOf = (c: RealCompare) => [...Object.keys(c.mapping.items), "mech_total"];
export const itemLabel = (c: RealCompare, item: string) =>
  item === "pue" ? "PUE" : c.runs.A.items[item]?.label ?? c.mapping.labels[item] ?? item;

export const pct = (v: number | null | undefined, digits = 1) => (v == null ? "–" : `${v > 0 ? "+" : ""}${fmt(v, digits)}%`);
export const signed = (v: number | null | undefined, digits = 2) => (v == null ? "–" : `${v > 0 ? "+" : ""}${fmt(v, digits)}`);

/** G14 판정 — 색만으로 구분하지 않도록 아이콘 + 글자 */
export function G14Badge({ pass }: { pass: boolean | null | undefined }) {
  if (pass == null) return <span className="muted">–</span>;
  return <span className={`g14 ${pass ? "pass" : "fail"}`}>{pass ? "✓ G14 통과" : "✕ G14 미통과"}</span>;
}

export function BasisBadge({ basis, title }: { basis: "fact" | "assumed" | "user" | "data" | "hypothesis"; title?: string }) {
  const label = { fact: "사실", assumed: "가정", user: "사용자 수정", data: "근거 있음", hypothesis: "가설" }[basis];
  return <span className={`basis ${basis}`} title={title}>{label}</span>;
}
