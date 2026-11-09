import { useEffect, useState } from "react";
import type { RealCompare, RealCompareRequest, RealPresetInfo, Scenario } from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { Card, Fold, Lead, usePersist } from "../../lib/ui";
import { CalibrationSteps } from "./CalibrationSteps";
import { ConditionTable } from "./ConditionTable";
import { DatasetPanel } from "./DatasetPanel";
import { DiagnosisPanel } from "./DiagnosisPanel";
import { ScatterCharts, SplitCharts } from "./SplitCharts";
import { SummaryTable } from "./SummaryTable";
import { itemsOf, pct, signed } from "./common";

/** 결과 맨 위 요약 — 계산값만 넣은 템플릿 문장 */
function summary(c: RealCompare): string {
  const A = c.runs.A, B = c.runs.B, C = c.runs.C;
  const ratios = Object.keys(c.mapping.items).map((k) => A.items[k].ratio_sim_over_meas ?? 1);
  const mt = (r: typeof A) => r.items.mech_total;
  let s = `공개 사실과 기본 계수(A)로 PUE 는 실측 ${fmt(A.pue.measured, 4)} 대비 ${signed(A.pue.diff, 4)} 차이지만, `
    + `항목별 평균은 실측의 ${fmt(Math.min(...ratios), 2)} ~ ${fmt(Math.max(...ratios), 2)}배입니다. `
    + `계수 비례 보정(B) 후 기계 합계는 NMBE ${pct(mt(B).nmbe_pct)}, CV ${fmt(mt(B).cv_rmse_pct, 1)}%`
    + ` (${mt(B).g14_pass ? "G14 통과" : "G14 미통과"}, 같은 기간에 맞춘 결과)`;
  if (C) s += `, 같은 계수를 검증 기간(C)에 적용하면 NMBE ${pct(mt(C).nmbe_pct)}, CV ${fmt(mt(C).cv_rmse_pct, 1)}% (${mt(C).g14_pass ? "G14 통과" : "G14 미통과"})`;
  s += `. 칠러 시간은 시뮬 ${A.chiller_hours.sim} h`;
  if (A.chiller_hours.measured != null) s += ` / 실측 ${A.chiller_hours.measured} h`;
  return s + ".";
}

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void }

export function RealComparePage({ scenario, busy, onApply }: Props) {
  const [presets, setPresets] = useState<RealPresetInfo[]>([]);
  const [result, setResult] = usePersist<RealCompare | null>("real:result", null);
  const [req, setReq] = usePersist<RealCompareRequest | null>("real:req", null);
  const [overrides, setOverrides] = usePersist<Record<string, unknown>>("real:over", {});
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.realPresets().then(setPresets).catch((e) => setErr((e as Error).message)); }, []);
  // URL ?real=<프리셋 id> 이면 프리셋 기본 매핑·기간으로 바로 실행 (공유·재현용)
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("real");
    if (q && !result) run({ preset: q }, {});
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (r: RealCompareRequest, over: Record<string, unknown>) => {
    setRunning(true); setErr(null);
    try {
      const res = await api.realCompare({ ...r, scenario: r.preset ? null : scenario, scenario_overrides: over });
      setResult(res); setReq(r); setOverrides(over);
    } catch (e) { setErr((e as Error).message); }
    setRunning(false);
  };

  return (
    <div className="stack">
      <Card title="실측 비교 — 데이터 · 항목 매핑 · 기간" hint="실제 데이터센터 장기 실측(시간 단위)을 같은 조건의 시뮬레이션과 비교 · 엔진 수식은 그대로">
        <DatasetPanel presets={presets} busy={running} result={result} onRun={(r) => run(r, req?.preset === r.preset ? overrides : {})} />
        {err && <div className="banner crit">{err}</div>}
      </Card>

      {result && <>
        <Card title={`결과 요약 — ${result.label}`} hint={`A 기본 계수 · B 보정 기간에 계수 비례 보정 · C B 계수를 검증 기간에 적용${result.cached ? " · 캐시된 결과" : ` · 계산 ${result.elapsed_s}초`}`}>
          <Lead>{summary(result)}</Lead>
          <SummaryTable result={result} />
          <Fold title={`시뮬레이션 조건 (사실 ${result.inputs.filter((r) => r.basis === "fact").length}개 · 가정 ${result.inputs.filter((r) => r.basis === "assumed").length}개${Object.keys(overrides).length ? ` · 사용자 수정 ${Object.keys(overrides).length}개` : ""})`}>
            <ConditionTable result={result} busy={running} overrides={overrides} onRerun={(o) => req && run(req, o)} />
          </Fold>
        </Card>

        <Card title="항목 × 실행 비교" hint={`행 = ${itemsOf(result).length + 1}개 항목, 열 = 실행. 칸마다 실측 + 해당 실행 두 선`}>
          <SplitCharts result={result} />
        </Card>

        <Card title="산점도" hint="시간값 · 실행 선택">
          <ScatterCharts result={result} />
        </Card>

        <Card title="시뮬 A → B 보정 단계" hint="무엇을 바꾸고 두는지, 비율, 계수, 재실행, PUE 기여, 검증 기간의 한계">
          <CalibrationSteps result={result} scenario={scenario} busy={busy || running} onApply={onApply} />
        </Card>

        <Card title="오차 원인 진단 (OpenAI)" hint="AI 는 계산하지 않음 · 근거 있음 / 가설 구분 · 숫자 대조">
          <DiagnosisPanel result={result} />
        </Card>
      </>}
    </div>
  );
}
