import { useEffect, useState } from "react";
import type { Architecture, Realism, Scenario } from "../../types/api";
import { ARCH_LABEL, MAIN_FIELDS, PLANT_FIELDS, getPath, setPath, type FieldDef } from "../../lib/fields";

interface Props {
  scenario: Scenario;
  busy: boolean;
  onApply: (sc: Scenario) => void;
  onReset: () => void;
}

// 현실성 R1 계수 (기본값 = backend realism.REALISM_DEFAULTS, 모두 가정값)
type RealismNum = Exclude<keyof Realism, "control">;
const REALISM_FIELDS: [RealismNum, string, string, number, number][] = [
  ["fc_hysteresis_k", "프리쿨링 전환 이력 폭", "K", 1.0, 0.1], ["chiller_min_on_h", "칠러 최소 운전 시간", "h", 2, 0.5],
  ["chiller_min_off_h", "칠러 최소 정지 시간", "h", 2, 0.5], ["chiller_min_plr", "칠러 최소 부하율", "-", 0.2, 0.05],
];
const mean = (a: number[]) => a.reduce((s, x) => s + x, 0) / a.length;
const ALL_FIELDS = [...MAIN_FIELDS, ...PLANT_FIELDS];

function Field({ f, value, onChange }: { f: FieldDef; value: string; onChange: (v: string) => void }) {
  return (
    <div className="field">
      <label>{f.label} [{f.unit}]{f.assumed && <span className="tag">가정값</span>}</label>
      <input type="number" step={f.step} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function InputPanel({ scenario, busy, onApply, onReset }: Props) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [utilMean, setUtilMean] = useState("");
  const [arch, setArch] = useState<Architecture>(scenario.cooling_plant.architecture);
  const [freeCooling, setFreeCooling] = useState(scenario.cooling_plant.free_cooling);
  const [real, setReal] = useState<Realism>(scenario.cooling_plant.realism ?? {});

  // 시나리오가 바뀌면(불러오기/자연어 적용 등) 입력칸을 다시 채움
  useEffect(() => {
    setDraft(Object.fromEntries(ALL_FIELDS.map((f) => [f.path, String(getPath(scenario, f.path))])));
    setUtilMean(mean(scenario.it_load.utilization).toFixed(3));
    setArch(scenario.cooling_plant.architecture);
    setFreeCooling(scenario.cooling_plant.free_cooling);
    setReal(scenario.cooling_plant.realism ?? {});
  }, [scenario]);

  const parsed = ALL_FIELDS.map((f) => [f, Number(draft[f.path])] as const);
  const invalid = parsed.some(([f, v]) => !draft[f.path]?.trim() || !Number.isFinite(v))
    || !(Number(utilMean) > 0);

  const apply = () => {
    let sc = scenario;
    for (const [f, v] of parsed) sc = setPath(sc, f.path, v);
    sc = structuredClone(sc);
    sc.cooling_plant.architecture = arch;
    sc.cooling_plant.free_cooling = freeCooling;
    sc.cooling_plant.realism = Object.keys(real).length ? real : null;
    // 평균 가동률 변경: 시간별 프로파일 모양은 유지하고 비율로 스케일
    // (표시값은 소수 3자리로 반올림되므로, 사용자가 값을 바꾼 경우에만 스케일)
    const cur = mean(scenario.it_load.utilization);
    const k = Number(utilMean) / cur;
    if (utilMean !== cur.toFixed(3)) {
      sc.it_load.utilization = scenario.it_load.utilization.map((u) => +(u * k).toFixed(4));
    }
    onApply(sc);
  };
  const set = (path: string) => (v: string) => setDraft({ ...draft, [path]: v });

  return (
    <div className="card">
      <div className="card-head">
        <h2>주요 입력값 수정 → 재계산</h2>
        <span className="hint"><span className="tag">가정값</span> = 근거 자료 없이 둔 값 (실제 설비 자료로 교체 필요)</span>
      </div>
      <div className="form-grid">
        <div className="field">
          <label>평균 IT 가동률 배율 <span className="muted">(시간별 프로파일 비율 유지)</span></label>
          <input type="number" step={0.01} value={utilMean} onChange={(e) => setUtilMean(e.target.value)} />
        </div>
        {MAIN_FIELDS.map((f) => <Field key={f.path} f={f} value={draft[f.path] ?? ""} onChange={set(f.path)} />)}
      </div>

      <h3 style={{ margin: "16px 0 8px" }}>냉각 방식 · 프리쿨링 · 보조동력</h3>
      <div className="form-grid">
        <div className="field">
          <label>냉각 방식</label>
          <select value={arch} onChange={(e) => setArch(e.target.value as Architecture)}>
            {Object.entries(ARCH_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="field">
          <label>프리쿨링 (수측 이코노마이저)</label>
          <label className="muted" style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center", paddingTop: 6 }}>
            <input type="checkbox" checked={freeCooling} onChange={(e) => setFreeCooling(e.target.checked)} />
            냉각탑 수온이 충분히 낮으면 칠러 OFF / 부분 예냉
          </label>
        </div>
        {PLANT_FIELDS.map((f) => <Field key={f.path} f={f} value={draft[f.path] ?? ""} onChange={set(f.path)} />)}
      </div>

      <h3 style={{ margin: "16px 0 8px" }}>운전 현실성 모델 <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>
        (끄면 기존 단순 모델과 같은 결과)</span></h3>
      <div className="form-grid">
        <div className="field">
          <label>R1 제어 이력</label>
          <label className="muted" style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center", paddingTop: 6 }}>
            <input type="checkbox" checked={!!real.control} onChange={(e) => setReal({ ...real, control: e.target.checked })} />
            프리쿨링 전환 이력 · 칠러 최소 운전/정지 시간
          </label>
        </div>
        {real.control && REALISM_FIELDS.map(([k, label, unit, def, step]) => (
          <div className="field" key={k}>
            <label>{label} [{unit}]<span className="tag">가정값</span></label>
            <input type="number" step={step} value={real[k] ?? def}
              onChange={(e) => setReal({ ...real, [k]: Number(e.target.value) })} />
          </div>))}
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={busy || invalid} onClick={apply}>{busy ? "계산 중…" : "재계산"}</button>
        <button className="btn" disabled={busy} onClick={onReset}>샘플로 초기화</button>
        <span className="muted">범위 검증(ΔT&gt;0, COC&gt;1 등)과 모든 계산은 백엔드 엔진이 수행</span>
      </div>
    </div>
  );
}
