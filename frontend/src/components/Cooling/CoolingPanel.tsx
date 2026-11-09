import { useEffect, useState } from "react";
import type {
  Candidate, Containment, CoolingLayout, EvaluateResult, OptimizeResult, Scenario, Topology, UnitPlan,
} from "../../types/api";
import { api } from "../../api/client";
import { fmt } from "../../lib/format";
import { MODE_LABEL } from "../../lib/fields";
import { IssueList } from "../../lib/issues";

const CONT: { v: Containment; label: string }[] = [
  { v: "open", label: "개방형" }, { v: "cold_aisle", label: "콜드아일" }, { v: "hot_aisle", label: "핫아일" },
];
const TOPO: { v: Topology; label: string }[] = [
  { v: "radial", label: "단일 헤더" }, { v: "dual", label: "남·북 2계통" }, { v: "ring", label: "루프" },
];
const contLabel = (v: Containment) => CONT.find((c) => c.v === v)!.label;
const topoLabel = (v: Topology) => TOPO.find((c) => c.v === v)!.label;

interface Props {
  scenario: Scenario;
  layout: CoolingLayout;                 // 실제 적용 중인 레이아웃 (시나리오 값 또는 기본값)
  cooling: EvaluateResult | null;
  error: string | null;
  busy: boolean;
  onApply: (sc: Scenario) => void;
}

/** 숫자 입력 필드 정의: [경로 라벨, 단위, step, 가정값 여부, getter, setter] */
type NumField = {
  key: string; label: string; unit: string; step: number; assumed?: boolean;
  get: (l: CoolingLayout, sc: Scenario) => number;
  set: (l: CoolingLayout, sc: Scenario, v: number) => void;
};
const FIELDS: NumField[] = [
  { key: "supply", label: "CRAH 급기온도", unit: "℃", step: 0.5, get: (l) => l.supply_temp_c, set: (l, _, v) => { l.supply_temp_c = v; } },
  { key: "chws", label: "냉수 공급온도", unit: "℃", step: 0.5, get: (_, s) => s.cooling_plant.chw_supply_c, set: (_, s, v) => { s.cooling_plant.chw_supply_c = v; } },
  { key: "chwr", label: "냉수 환수온도", unit: "℃", step: 0.5, get: (_, s) => s.cooling_plant.chw_return_c, set: (_, s, v) => { s.cooling_plant.chw_return_c = v; } },
  { key: "ratio", label: "CRAH 풍량 배율 (설계 대비)", unit: "-", step: 0.05, get: (l) => l.crah.airflow_ratio, set: (l, _, v) => { l.crah.airflow_ratio = v; } },
  { key: "tile", label: "타일 밸런싱 (0=거리, 1=수요 비례)", unit: "-", step: 0.25, get: (l) => l.crah.tile_balancing, set: (l, _, v) => { l.crah.tile_balancing = v; } },
  { key: "per_side", label: "CRAH 대수 (남·북 각)", unit: "대", step: 1, get: (l) => l.crah.per_side, set: (l, _, v) => { l.crah.per_side = Math.round(v); l.crah.positions = null; } },
  { key: "rows", label: "랙 열 수", unit: "열", step: 1, get: (l) => l.racks.rows, set: (l, _, v) => { l.racks.rows = Math.round(v); } },
  { key: "per_row", label: "열당 랙 수", unit: "개", step: 1, get: (l) => l.racks.per_row, set: (l, _, v) => { l.racks.per_row = Math.round(v); } },
  { key: "limit", label: "랙 흡입온도 상한", unit: "℃", step: 0.5, get: (l) => l.inlet_limit_c, set: (l, _, v) => { l.inlet_limit_c = v; } },
  { key: "srv_dt", label: "서버 흡입-배기 온도차", unit: "K", step: 0.5, assumed: true, get: (l) => l.server_delta_t_k, set: (l, _, v) => { l.server_delta_t_k = v; } },
  { key: "vmax", label: "배관 최대 유속 (관경 선정)", unit: "m/s", step: 0.1, get: (l) => l.piping.max_velocity_m_s, set: (l, _, v) => { l.piping.max_velocity_m_s = v; } },
  { key: "approach", label: "코일 접근온도 (급기 - 냉수)", unit: "K", step: 0.5, assumed: true, get: (l) => l.coil_approach_k, set: (l, _, v) => { l.coil_approach_k = v; } },
];

function Kpi({ label, value, unit, detail, bad }: { label: string; value: string; unit?: string; detail?: string; bad?: boolean }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value" style={bad ? { color: "var(--critical)" } : undefined}>{value}{unit && <span className="unit">{unit}</span>}</div>
      {detail && <div className="detail">{detail}</div>}
    </div>
  );
}

const pct = (a: number, b: number) => (b > 0 ? ((b - a) / b) * 100 : 0);

/** 해결 방법 바로가기로 들어온 권장값 (key = FIELDS.key 또는 containment) */
export interface LayoutFocus { key: string; value: number | string | null; label: string; n: number }

export function LayoutEditor({ scenario, layout, cooling, busy, onApply, focus, onFocusDone }:
  Pick<Props, "scenario" | "layout" | "cooling" | "busy" | "onApply"> & { focus?: LayoutFocus | null; onFocusDone?: () => void }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [cont, setCont] = useState<Containment>(layout.containment);
  const [topo, setTopo] = useState<Topology>(layout.piping.topology);
  const [density, setDensity] = useState("");
  const [prefill, setPrefill] = useState<string | null>(null);

  useEffect(() => {
    setDraft(Object.fromEntries(FIELDS.map((f) => [f.key, String(f.get(layout, scenario))])));
    setCont(layout.containment);
    setTopo(layout.piping.topology);
    setDensity(layout.racks.row_density?.join(", ") ?? "");
    setPrefill(null);
  }, [layout, scenario]);

  useEffect(() => {
    if (!focus) return;
    if (focus.value != null) {
      if (focus.key === "containment") setCont(focus.value as Containment);
      else setDraft((d) => ({ ...d, [focus.key]: String(focus.value) }));
      setPrefill(focus.label);
    }
    onFocusDone?.();
  }, [focus?.n]);   // eslint-disable-line react-hooks/exhaustive-deps

  // CAD 랙(items)이 있으면 격자 설정(열 수·열당 랙 수·열 밀도)은 쓰이지 않으므로 숨김
  const cadRacks = layout.racks.items?.length ?? 0;
  const fields = cadRacks ? FIELDS.filter((f) => f.key !== "rows" && f.key !== "per_row") : FIELDS;
  const nums = fields.map((f) => [f, Number(draft[f.key])] as const);
  const densArr = !cadRacks && density.trim() ? density.split(/[,\s]+/).filter(Boolean).map(Number) : null;
  const rowsVal = Number(draft.rows);
  const densBad = densArr !== null && (densArr.length !== rowsVal || densArr.some((x) => !(x >= 0)));
  const invalid = nums.some(([f, v]) => !draft[f.key]?.trim() || !Number.isFinite(v)) || densBad;

  const apply = () => {
    const l = structuredClone(layout);
    const sc = structuredClone(scenario);
    for (const [f, v] of nums) f.set(l, sc, v);
    // CRAH 대수를 바꾸면 1대 크기는 현재 장비로 고정 (대수만큼 총풍량·팬 전력이 변함). 정격 = 현재 운전 풍량 / 풍량 배율
    const unit = cooling?.airflow.crah[0]?.airflow_m3_s;
    if (l.crah.per_side !== layout.crah.per_side && !layout.crah.unit_airflow_m3_s && unit)
      l.crah.unit_airflow_m3_s = Math.round((unit / layout.crah.airflow_ratio) * 1000) / 1000;
    l.containment = cont;
    l.piping.topology = topo;
    if (!cadRacks) l.racks.row_density = densArr;
    sc.cooling_layout = l;
    onApply(sc);
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>냉각 구조 설정 → 재계산</h2>
        <span className="hint">시나리오에 함께 저장됨 (cooling_layout) · <span className="tag">가정값</span> = 근거 자료 없이 둔 값</span>
      </div>
      <div className="form-grid">
        <div className="field" id="set-layout-containment">
          <label>컨테인먼트</label>
          <select value={cont} onChange={(e) => setCont(e.target.value as Containment)}>
            {CONT.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}
          </select>
        </div>
        <div className="field">
          <label>냉수 배관 방식</label>
          <select value={topo} onChange={(e) => setTopo(e.target.value as Topology)}>
            {TOPO.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}
          </select>
        </div>
        {fields.map((f) => (
          <div className="field" key={f.key} id={`set-layout-${f.key}`}>
            <label>{f.label} [{f.unit}]{f.assumed && <span className="tag">가정값</span>}</label>
            <input type="number" step={f.step} value={draft[f.key] ?? ""}
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
          </div>
        ))}
        {cadRacks > 0 ? <div className="field" style={{ gridColumn: "span 2" }}>
          <label>랙 배치</label>
          <div className="muted">CAD 도면 랙 {cadRacks}개 사용 중 (랙별 발열 = IT 데이터 프로파일 또는 설계 kW 비례). 배치는 'CAD·IT 가져오기' 탭에서 변경</div>
        </div> : <div className="field" style={{ gridColumn: "span 2" }}>
          <label>열별 상대 전력밀도 (쉼표 구분, 열 수만큼 · 비우면 균일)</label>
          <input className="text" value={density} placeholder="예: 1, 1, 1, 1, 1.5, 1.5, 2, 2"
            onChange={(e) => setDensity(e.target.value)} />
          {densBad && <span className="muted" style={{ color: "var(--critical)" }}>값 {densArr?.length}개 - 랙 열 수({rowsVal})와 같아야 합니다</span>}
        </div>}
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={busy || invalid} onClick={apply}>{busy ? "계산 중…" : "적용 · 재계산"}</button>
        {prefill ? <span className="prefill-note">권장값을 입력해 두었습니다 ({prefill}) — '적용 · 재계산'을 눌러야 반영됩니다</span>
          : <span className="muted">범위 검증과 모든 계산(기류·배관·칠러)은 백엔드에서 수행</span>}
      </div>
    </div>
  );
}

const UNIT_LABEL: Record<string, string> = { chiller: "칠러", tower: "냉각탑", cdu: "CDU" };

export function Summary({ cooling }: { cooling: EvaluateResult }) {
  const s = cooling.summary, m = cooling.airflow.metrics, p = cooling.piping;
  return (
    <div className="card">
      <div className="card-head">
        <h2>현재 냉각 구조 평가</h2>
        <span className="hint">피크 IT 시각 {cooling.peak_hour}시 기준 · 일 전력량은 24시간 합계</span>
      </div>
      {cooling.issues ? <IssueList issues={cooling.issues} />
        : !s.feasible && <div className="banner crit">제약 위반:{"\n"}{s.violations.map((v) => `· ${v}`).join("\n")}</div>}
      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))" }}>
        <Kpi label="랙 최대 흡입온도 (피크)" value={fmt(s.max_inlet_c, 1)} unit="℃" bad={s.racks_over_limit > 0}
          detail={`평균 ${fmt(m.mean_inlet_c, 1)}℃ · 초과 ${s.racks_over_limit}/${m.rack_count}개`} />
        <Kpi label="냉방 전력 (피크)" value={fmt(s.peak_kw.total, 0)} unit="kW"
          detail={`칠러 ${fmt(s.peak_kw.chiller, 0)} · 팬 ${fmt(s.peak_kw.crah_fan, 0)} · 펌프 ${fmt(s.peak_kw.chw_pump + s.peak_kw.cw_pump, 0)}`
            + (s.peak_kw.liquid ? ` · 액체 루프 ${fmt(s.peak_kw.liquid, 0)}` : "")} />
        <Kpi label="일 냉방 전력량" value={fmt(s.daily_kwh.total, 0)} unit="kWh"
          detail={`칠러 ${fmt(s.daily_kwh.chiller, 0)} · 팬 ${fmt(s.daily_kwh.crah_fan, 0)} · 펌프 ${fmt(s.daily_kwh.chw_pump + s.daily_kwh.cw_pump, 0)}`
            + (s.daily_kwh.liquid ? ` · 액체 루프 ${fmt(s.daily_kwh.liquid, 0)}` : "")} />
        <Kpi label="칠러 COP (피크)" value={fmt(s.cop_peak, 2)}
          detail={`냉수 ${fmt(s.params.chw_supply_c, 1)} → ${fmt(s.params.chw_return_c, 1)} ℃`} />
      </div>
      {!cooling.issues && cooling.warnings?.map((w) => <div key={w} className="banner warn">⚠ {w}</div>)}
      {cooling.units && <div className="muted" style={{ marginTop: 8 }}>
        설비 대수 (오늘 최대 부하 기준, 표시용): {(Object.entries(cooling.units) as [string, UnitPlan][]).map(([k, u]) =>
          `${UNIT_LABEL[k]} ${u.duty}+${u.standby}대 (1대 ${fmt(u.unit_kw, 0)} kW, 최대 ${fmt(u.design_kw, 0)} kW)`).join(" · ")}
      </div>}
      <details style={{ marginTop: 10 }}>
        <summary className="muted" style={{ cursor: "pointer" }}>상세: 기류 지표·배관·시간별 값</summary>
        <div className="muted" style={{ margin: "6px 0" }}>
          SHI {fmt(s.shi, 3)} · RHI {fmt(m.rhi, 3)} · 바이패스 {fmt(m.bypass_fraction * 100, 0)}% · CRAH 환기 {fmt(s.crah_return_c, 1)}℃ ·
          급기/요구 풍량 {fmt(m.supply_ratio, 2)} · 냉수 양정 {fmt(p.chw_head_kpa, 0)} kPa (최원단 {fmt(p.chw_critical_m, 0)} m) ·
          냉수 설계 유량 {fmt(p.chw_design_flow_m3_h, 0)} m³/h ({topoLabel(p.topology)})
        </div>
        <div className="table-scroll" style={{ marginTop: 6 }}>
          <table className="data">
            <thead><tr>
              <th>시각</th><th>IT kW</th><th>냉방 kW</th><th>COP</th><th>칠러 kW</th><th>CRAH 팬</th>
              <th>냉수 펌프</th><th>냉각수 펌프</th><th>액체 루프</th><th>모드</th><th>합계 kW</th><th>최대 흡입 ℃</th><th>초과 랙</th>
            </tr></thead>
            <tbody>
              {cooling.hourly.map((x) => (
                <tr key={x.hour} className={x.hour === cooling.peak_hour ? "active" : ""}>
                  <td>{String(x.hour).padStart(2, "0")}:00</td><td>{fmt(x.it_kw, 0)}</td><td>{fmt(x.cooling_kw, 0)}</td>
                  <td>{fmt(x.cop, 2)}</td><td>{fmt(x.chiller_kw, 0)}</td><td>{fmt(x.crah_fan_kw, 1)}</td>
                  <td>{fmt(x.chw_pump_kw, 1)}</td><td>{fmt(x.cw_pump_kw, 1)}</td><td>{fmt(x.liquid_kw, 1)}</td>
                  <td>{MODE_LABEL[x.mode]}</td><td>{fmt(x.total_kw, 0)}</td>
                  <td>{fmt(x.max_inlet_c, 1)}</td><td>{x.racks_over_limit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function CandidateRow({ c, base, label, onApply, busy }: {
  c: Candidate; base: Candidate; label: string; onApply?: () => void; busy: boolean;
}) {
  const p = c.params;
  const save = pct(c.daily_kwh.total, base.daily_kwh.total);
  return (
    <tr>
      <td>{label}</td>
      <td>{contLabel(p.containment)}</td>
      <td>{p.crah_per_side}×2</td>
      <td>{fmt(p.airflow_ratio, 2)}</td>
      <td>{fmt(p.tile_balancing, 2)}</td>
      <td>{fmt(p.supply_temp_c, 1)}</td>
      <td>{fmt(p.chw_supply_c, 1)} / {fmt(p.chw_return_c, 1)}</td>
      <td>{topoLabel(p.topology)}</td>
      <td style={c.racks_over_limit ? { color: "var(--critical)" } : undefined}>{fmt(c.max_inlet_c, 1)}</td>
      <td>{fmt(c.cop_peak, 2)}</td>
      <td>{fmt(c.peak_kw.total, 0)}</td>
      <td>{fmt(c.daily_kwh.total, 0)}</td>
      <td>{onApply ? <b className={save > 0 ? "ok" : ""}>{fmt(save, 1)}%</b> : "-"}</td>
      <td>
        {onApply ? <button className="btn small" disabled={busy} onClick={onApply}>적용</button>
          : c.feasible ? <span className="muted">현재안</span> : <span style={{ color: "var(--critical)" }} title={c.violations.join("\n")}>제약 위반</span>}
      </td>
    </tr>
  );
}

export function Optimizer({ scenario, busy, onApply }: Pick<Props, "scenario" | "busy" | "onApply">) {
  const [conts, setConts] = useState<Containment[]>(["open", "cold_aisle", "hot_aisle"]);
  const [topos, setTopos] = useState<Topology[]>(["radial", "dual", "ring"]);
  const [tmax, setTmax] = useState("25");
  const [crahOpts, setCrahOpts] = useState("");
  const [res, setRes] = useState<OptimizeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => setRes(null), [scenario]);   // 입력이 바뀌면 이전 결과는 무효

  const toggle = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const crahList = crahOpts.trim() ? crahOpts.split(/[,\s]+/).filter(Boolean).map(Number) : undefined;
  const invalid = !conts.length || !topos.length || !(Number(tmax) > 0)
    || (crahList?.some((x) => !Number.isInteger(x) || x < 1) ?? false);

  const run = async () => {
    setRunning(true);
    setErr(null);
    try {
      setRes(await api.coolingOptimize(scenario, {
        containment: conts, topology: topos, supply_temp_max_c: Number(tmax), crah_per_side: crahList,
      }));
    } catch (e) {
      setErr((e as Error).message);
    }
    setRunning(false);
  };
  const apply = async (c: Candidate) => {
    try {
      onApply(await api.coolingApply(scenario, c.params));
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <div className="card" id="set-opt-day">
      <div className="card-head">
        <h2>냉각 유로·운전점 최적화</h2>
        <span className="hint">목적: 일 냉방 전력량(칠러+CRAH 팬+냉수·냉각수 펌프) 최소 · 제약: 랙 흡입온도 ≤ 상한, 코일 접근온도</span>
      </div>
      <div className="form-grid">
        <div className="field">
          <label>허용 컨테인먼트 (공사 가능 범위)</label>
          <div className="row">
            {CONT.map((c) => (
              <label key={c.v} className="muted" style={{ fontSize: 13 }}>
                <input type="checkbox" checked={conts.includes(c.v)} onChange={() => setConts(toggle(conts, c.v))} /> {c.label}
              </label>
            ))}
          </div>
        </div>
        <div className="field">
          <label>허용 냉수 배관 방식</label>
          <div className="row">
            {TOPO.map((c) => (
              <label key={c.v} className="muted" style={{ fontSize: 13 }}>
                <input type="checkbox" checked={topos.includes(c.v)} onChange={() => setTopos(toggle(topos, c.v))} /> {c.label}
              </label>
            ))}
          </div>
        </div>
        <div className="field">
          <label>급기온도 상한 [℃]</label>
          <input type="number" step={0.5} value={tmax} onChange={(e) => setTmax(e.target.value)} />
        </div>
        <div className="field">
          <label>CRAH 대수 후보 (남·북 각, 쉼표 · 비우면 현재 값)</label>
          <input className="text" value={crahOpts} placeholder="예: 4, 5, 6" onChange={(e) => setCrahOpts(e.target.value)} />
        </div>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={running || busy || invalid} onClick={run}>
          {running ? "탐색 중…" : "최적화 실행"}</button>
        <span className="muted">풍량 배율 0.3~1.5, 타일 밸런싱 0~1, 냉수 ΔT 5~10K 격자 전수 탐색 · 급기온도는 흡입 상한에서 역산</span>
      </div>
      {err && <div className="banner crit">{err}</div>}
      {res && <>
        <div className="muted" style={{ margin: "10px 0 4px" }}>
          기류 해석 {res.airflow_evaluations}회 · 에너지 평가 {res.evaluated}개 조합 · 피크 {res.peak_hour}시 기준 제약 확인
          {res.candidates.length === 0 && " · 제약을 만족하는 조합 없음 (허용 범위를 넓혀 보세요)"}
        </div>
        <div className="table-scroll" style={{ maxHeight: "none" }}>
          <table className="data">
            <thead><tr>
              <th>구분</th><th>컨테인먼트</th><th>CRAH</th><th>풍량 배율</th><th>타일</th><th>급기 ℃</th>
              <th>냉수 공급/환수 ℃</th><th>배관</th><th>최대 흡입 ℃</th><th>COP</th><th>피크 kW</th><th>일 kWh</th>
              <th>절감</th><th />
            </tr></thead>
            <tbody>
              <CandidateRow c={res.baseline} base={res.baseline} label="현재" busy={busy} />
              {res.candidates.map((c, i) => (
                <CandidateRow key={i} c={c} base={res.baseline} label={`후보 ${i + 1}`} busy={busy} onApply={() => apply(c)} />
              ))}
            </tbody>
          </table>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          모델(가정값) 기준 상대 비교 · 컨테인먼트·배관 공사비, 냉각탑 팬 전력, 습도 제어는 목적함수에 없음 ·
          '적용'하면 냉수 온도와 냉각 구조가 시나리오에 반영되고 부하 엔진이 다시 계산됨
        </div>
      </>}
    </div>
  );
}
