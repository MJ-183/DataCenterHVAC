import { createContext, useContext } from "react";
import type { Fix, Issue } from "../types/api";

/** 해결 방법 → 설정 위치 이동 (App 이 제공). target 키는 backend hvac/advice.py 와 같음 */
export const GoTo = createContext<(f: Fix) => void>(() => undefined);

export const TARGET_LABEL: Record<string, string> = {
  "layout.supply": "CRAH 급기온도", "layout.chws": "냉수 공급온도", "layout.chwr": "냉수 환수온도",
  "layout.ratio": "CRAH 풍량 배율", "layout.tile": "타일 밸런싱", "layout.per_side": "CRAH 대수",
  "layout.limit": "흡입온도 상한", "layout.approach": "코일 접근온도", "layout.containment": "컨테인먼트",
  "rack.types": "랙·냉각 방식", "opt.day": "운전 최적화",
};
const UNIT: Record<string, string> = {
  "layout.supply": "℃", "layout.chws": "℃", "layout.chwr": "℃", "layout.limit": "℃", "layout.approach": "K",
  "layout.per_side": "대 (남·북 각)",
};
const VALUE_LABEL: Record<string, string> = { open: "개방형", cold_aisle: "콜드아일", hot_aisle: "핫아일" };
const ICON = { crit: "✕", warn: "⚠", info: "ⓘ" } as const;

export const fixValue = (f: Fix) =>
  f.value == null ? "" : typeof f.value === "string" ? VALUE_LABEL[f.value] ?? f.value : `${f.value}${UNIT[f.target] ?? ""}`;

/** 화면 요소로 스크롤하고 잠깐 강조 */
export function flash(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
  window.setTimeout(() => el.classList.remove("flash"), 2600);
  el.querySelector<HTMLElement>("input, select")?.focus({ preventScroll: true });
}

/** 문제 → 원인 → 해결 방법(오른쪽에 바꿀 설정 바로가기). local 로 특정 target 의 동작을 화면 안 동작으로 바꿀 수 있음 */
export function IssueBox({ issue, local, note }: {
  issue: Issue; local?: Record<string, { label: string; run: () => void }>; note?: string;
}) {
  const goTo = useContext(GoTo);
  return (
    <div className={`issue ${issue.level}`}>
      <div className="issue-title">{ICON[issue.level]} {issue.title}</div>
      <div className="issue-cause"><b>원인</b> {issue.cause}</div>
      {issue.fixes.length > 0 && <>
        <div className="issue-sub">해결 방법 <span className="muted">— 예상 결과는 기류 모델로 다시 계산한 값 · 바로가기는 권장값을 입력칸에 채워 두기만 하고, 적용은 직접 누릅니다{note ? ` · ${note}` : ""}</span></div>
        <ol className="fixes">
          {issue.fixes.map((f, i) => {
            const loc = local?.[f.target];
            return (
              <li key={i} className="fix">
                <div className="fix-body">
                  <div className="fix-label">
                    <span className="fix-n">{i + 1}.</span>{f.label}{f.value != null && <b> → {fixValue(f)}</b>}
                    {f.solves === true && <span className="badge ok">해결</span>}
                    {f.solves === false && <span className="badge">일부 개선</span>}
                  </div>
                  <div className="fix-detail">{f.detail}</div>
                  {f.effect && <div className="fix-effect">예상: {f.effect}</div>}
                </div>
                <button className="btn small fix-go" onClick={() => (loc ? loc.run() : goTo(f))}>
                  {loc ? loc.label : `${TARGET_LABEL[f.target] ?? "설정"} 바로가기`} →
                </button>
              </li>
            );
          })}
        </ol>
      </>}
    </div>
  );
}

export function IssueList({ issues }: { issues?: Issue[] | null }) {
  if (!issues?.length) return null;
  return <>{issues.map((x) => <IssueBox key={x.code + x.title} issue={x} />)}</>;
}
