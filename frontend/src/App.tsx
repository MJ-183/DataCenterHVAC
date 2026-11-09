import { useCallback, useEffect, useRef, useState } from "react";
import type { CoolingLayout, EvaluateResult, Fix, SavedInfo, Scenario, ScenarioStatus, SimulationResult } from "./types/api";
import { API_BASE, STEP_MIN, api } from "./api/client";
import { usePlayback } from "./hooks/usePlayback";
import { TimeSlider } from "./components/TimeSlider";
import { KpiCards } from "./components/Dashboard/KpiCards";
import { Charts } from "./components/Dashboard/Charts";
import { InputPanel } from "./components/Dashboard/InputPanel";
import { Scene3D } from "./components/Scene3D/Scene3D";
import { LayoutEditor, Optimizer, Summary, type LayoutFocus } from "./components/Cooling/CoolingPanel";
import { AiSummary } from "./components/Dashboard/AiSummary";
import { ZonePanel } from "./components/Dashboard/ZonePanel";
import { GoTo, TARGET_LABEL, fixValue, flash } from "./lib/issues";
import { CadImport } from "./components/Import/CadImport";
import { ItImport } from "./components/Import/ItImport";
import { KmaKey } from "./components/Import/KmaKey";
import { RegionPanel, restoreRegion } from "./components/Import/RegionPanel";
import { BmsCalibration } from "./components/Calibration/BmsCalibration";
import { AnnualPanel, type AnnualState } from "./components/Annual/AnnualPanel";
import { OutlookPanel } from "./components/Outlook/OutlookPanel";
import { AnnualOptPanel } from "./components/Improve/AnnualOptPanel";
import { RackPanel } from "./components/Improve/RackPanel";
import { InvestPanel } from "./components/Improve/InvestPanel";
import { ReportPanel } from "./components/Improve/ReportPanel";
import { AutoTuneBanner, AutoTunePanel } from "./components/Improve/AutoTunePanel";
import { RealComparePage } from "./components/RealCompare/RealComparePage";
import { ARCH_LABEL } from "./lib/fields";
import { Card, DemoNote, Fold, usePersist } from "./lib/ui";

const PRESET_KEY = "preset:last";
const savedPreset = () => { try { return localStorage.getItem(PRESET_KEY) ?? "haenam"; } catch { return "haenam"; } };

const PAGES = [["data", "데이터 입력"], ["now", "운전 현황"], ["year", "연간 예측"], ["forecast", "브리핑"], ["improve", "최적화"], ["real", "실측 비교"]] as const;
type Page = (typeof PAGES)[number][0];

export default function App() {
  const [sample, setSample] = useState<Scenario | null>(null);
  const [presets, setPresets] = useState<{ id: string; label: string }[]>([]);
  const [preset, setPreset] = useState(savedPreset);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [history, setHistory] = useState<Scenario[]>([]);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [coolDefault, setCoolDefault] = useState<CoolingLayout | null>(null);
  const [cooling, setCooling] = useState<EvaluateResult | null>(null);
  const [coolError, setCoolError] = useState<string | null>(null);
  const [status, setStatus] = useState<ScenarioStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [speed, setSpeed] = useState(1200);
  // 시작 화면은 URL ?page= (공유·재현용), 없으면 운전 현황
  const [page, setPage] = useState<Page>(() => {
    const q = new URLSearchParams(window.location.search).get("page");
    return PAGES.some(([k]) => k === q) ? (q as Page) : "now";
  });
  const [sub, setSub] = usePersist<"opt" | "tune" | "rack" | "invest" | "report">("ui:sub", "opt");
  const [optMode, setOptMode] = usePersist<"day" | "year">("ui:opt", "year");
  const [annual, setAnnual] = useState<AnnualState | null>(null);
  const [focus, setFocus] = useState<LayoutFocus | null>(null);
  const [kmaOk, setKmaOk] = useState(false);
  const [layoutFold, setLayoutFold] = useState(0);
  const [zone, setZone] = useState<string | null>(null);   // 다중 존: 3D·냉각 구조 상세로 볼 전산실
  const zoneRef = useRef<string | null>(null);
  const { minute, setMinute, playing, setPlaying, clock } = usePlayback(STEP_MIN, speed);

  /** 해결 방법 → 바꿀 설정 위치로 이동 (화면·접힌 구역 열기, 권장값 채우기, 스크롤·강조) */
  const goTo = useCallback((f: Fix) => {
    const [area, key] = f.target.split(".");
    if (area === "layout") {
      setPage("now");
      setLayoutFold((n) => n + 1);
      setFocus({ key, value: f.value, label: `${TARGET_LABEL[f.target] ?? key} → ${fixValue(f)}`, n: Date.now() });
    } else if (area === "rack") {
      setPage("improve"); setSub("rack");
    } else if (area === "data") {
      setPage("data");
    } else if (area === "opt") {
      setPage("improve"); setSub("opt"); setOptMode(key === "year" ? "year" : "day");
    }
    window.setTimeout(() => flash(`set-${area}-${key}`), 150);
  }, [setSub, setOptMode]);

  /** 모든 계산은 백엔드: 부하 + 냉각 구조 + 상태(가이드·신뢰도). 실패하면 이전 결과 유지 */
  const run = useCallback(async (sc: Scenario, keepHistory = true) => {
    setBusy(true);
    setError(null);
    try {
      const z = sc.zones?.some((x) => x.id === zoneRef.current && x.kind === "data_hall") ? zoneRef.current : null;
      zoneRef.current = z; setZone(z);
      const [r, c, st] = await Promise.all([api.simulate(sc), api.coolingEvaluate(sc, z).catch((e: Error) => e),
        api.status(sc).catch(() => null)]);
      setScenario((prev) => {
        if (prev && keepHistory) setHistory((hs) => [...hs.slice(-19), prev]);
        return sc;
      });
      setResult(r);
      setStatus(st);
      if (c instanceof Error) { setCooling(null); setCoolError(c.message); } else { setCooling(c); setCoolError(null); }
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }, []);
  const apply = useCallback((sc: Scenario) => { run(sc); }, [run]);
  /** 시나리오 선택: 그 시나리오의 지역·기상으로 시작 (마지막 지역 선택 기억은 지움). 되돌리기 가능 */
  const pickPreset = async (id: string) => {
    try {
      const sc = await api.preset(id);
      setPreset(id); setSample(sc); setAnnual(null);
      try { localStorage.setItem(PRESET_KEY, id); localStorage.removeItem("region:last"); } catch { /* 이번 화면에는 적용됨 */ }
      run(sc);
    } catch (e) { setError((e as Error).message); }
  };
  /** 다중 존: 3D 로 볼 전산실 바꾸기 (냉각 구조 평가만 다시) */
  const pickZone = async (id: string) => {
    if (!scenario) return;
    zoneRef.current = id; setZone(id);
    try { setCooling(await api.coolingEvaluate(scenario, id)); setCoolError(null); } catch (e) { setCoolError((e as Error).message); }
  };
  const undo = () => {
    const prev = history[history.length - 1];
    if (!prev) return;
    setHistory(history.slice(0, -1));
    run(prev, false);
  };

  useEffect(() => {
    api.presets().then(setPresets).catch(() => undefined);
    Promise.all([api.preset(savedPreset()).catch(() => api.sample()), api.coolingDefault()])
      .then(async ([s, d]) => { setSample(s); setCoolDefault(d); return run(await restoreRegion(s), false); })
      .catch((e) => setError((e as Error).message));
    api.kmaKey().then((k) => setKmaOk(k.configured)).catch(() => undefined);
  }, [run]);

  if (!scenario || !result || !coolDefault) {
    return (
      <div className="app">
        <div className="header"><h1>데이터센터 냉방 시뮬레이터</h1></div>
        {error ? <div className="banner crit">{error}</div> : <div className="banner info">불러오는 중… ({API_BASE})</div>}
      </div>
    );
  }

  const index = Math.min(Math.floor(minute / result.step_min), result.hourly.length - 1);
  const h = result.hourly[index];
  const coolLayout = scenario.cooling_layout ?? coolDefault;
  // 다중 존: 3D 는 선택한 전산실 하나 (치수 = 존 평면, 랙 격자 = 존 랙)
  const hallZone = scenario.zones?.find((z) => z.id === (cooling?.zone ?? zone)) ?? scenario.zones?.find((z) => z.kind === "data_hall");
  const scene = hallZone ? { ...scenario, building: { source: `zone:${hallZone.id}`, ew_m: hallZone.geometry.ew_m,
    ns_m: hallZone.geometry.ns_m, height_m: hallZone.geometry.height_m, rotation_deg: 0 } } : scenario;
  const sceneLayout = hallZone?.racks ? { ...coolLayout, racks: hallZone.racks } : coolLayout;
  const g = status?.guide;
  const demo = !g?.cad && !g?.it && !g?.bms;
  const steps: { label: string; done: boolean; optional?: boolean; go: Page }[] = [
    { label: "지역", done: !!scenario.site.asos_station_id, go: "data" }, { label: "도면", done: !!g?.cad, go: "data" }, { label: "IT 데이터", done: !!g?.it, go: "data" },
    { label: "BMS 보정", done: !!g?.bms, optional: true, go: "data" }, { label: "연간 분석", done: !!annual, go: "year" },
    { label: "개선안", done: !!g?.improve, go: "improve" }, { label: "투자 검토", done: false, go: "improve" },
  ];
  const next = steps.find((s) => !s.done && !s.optional);

  return (
    <GoTo.Provider value={goTo}>
    <div className="app">
      <header className="header">
        <h1>데이터센터 냉방 시뮬레이터</h1>
        <span className="sub">{result.site} · {result.date} · {ARCH_LABEL[result.architecture]} · 일 PUE {result.daily_total.pue?.toFixed(3) ?? "–"}</span>
        <span className="spacer" />
        {busy && <span className="muted">계산 중…</span>}
        {presets.length > 0 && <select className="preset" value={preset} disabled={busy} title="시나리오 선택"
          onChange={(e) => pickPreset(e.target.value)}>
          {presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>}
        <button className="btn small" disabled={!history.length || busy} onClick={undo} title="직전 적용 되돌리기">↶ 되돌리기</button>
        <ScenarioMenu scenario={scenario} onLoad={apply} onReset={() => sample && apply(sample)} />
      </header>

      <nav className="steps">
        {steps.map((s, i) => (
          <button key={s.label} className={`step ${s.done ? "done" : ""} ${s === next ? "next" : ""}`} onClick={() => setPage(s.go)}>
            <span className="dot">{s.done ? "✓" : i + 1}</span>{s.label}{s.optional ? " (선택)" : ""}
          </button>
        ))}
      </nav>

      {demo && <DemoNote>지금은 <b>예시 데이터</b>(가상 기상·IT 부하·설비 값)로 시연 계산한 결과입니다. '데이터 입력'에서 실제 도면·IT·BMS 데이터를 올리면 그 값으로 다시 계산됩니다.</DemoNote>}
      {scenario._note && <div className="banner info">ⓘ {scenario._note}</div>}
      {error && <div className="banner crit">계산 실패 (이전 결과 유지): {error}</div>}

      <nav className="pages">
        {PAGES.map(([k, l]) => <button key={k} className={`page ${page === k ? "on" : ""}`} onClick={() => setPage(k)}>{l}</button>)}
      </nav>

      {page === "data" && <div className="stack">
        <KmaKey onChange={setKmaOk} />
        <RegionPanel scenario={scenario} busy={busy} keyOk={kmaOk} onApply={apply} />
        <CadImport scenario={scenario} busy={busy} onApply={apply} />
        <ItImport scenario={scenario} busy={busy} onApply={apply} />
        <Card title="③ BMS 실측 보정 (선택)" hint="모델 계수(칠러 COP·팬·펌프·재순환 등)를 실측에 맞춥니다">
          <BmsCalibration scenario={scenario} busy={busy} onApply={apply} />
        </Card>
      </div>}

      {page === "now" && <div className="stack">
        {coolLayout.operating_schedule && <div className="banner info">ⓘ 월별 운전 스케줄 사용 중 — 아래 값은 {Number(result.date.slice(5, 7))}월 운전점 기준</div>}
        <TimeSlider clock={clock} stepMin={result.step_min} playing={playing} speedMs={speed}
          onMinute={setMinute} onPlaying={setPlaying} onSpeed={setSpeed} />
        <div className="top-grid">
          <Scene3D result={result} scenario={scene} clock={clock} cooling={cooling} coolingLayout={sceneLayout} />
          <KpiCards h={h} architecture={result.architecture} />
        </div>
        {scenario.zones && <ZonePanel scenario={scenario} result={result} cooling={cooling} zone={zone} onZone={pickZone} />}
        <Charts result={result} t={h.t_h} onTime={(t) => setMinute(t * 60)} />
        {coolError && <div className="banner crit">냉각 구조 평가 실패: {coolError}</div>}
        {cooling && <Summary cooling={cooling} />}
        <AutoTuneBanner scenario={scenario} cooling={cooling} busy={busy} onApply={apply}
          onSettings={() => { setPage("improve"); setSub("tune"); }} />
        <AiSummary result={result} />
        <Fold title="입력값 직접 수정 (기상·외피·IT·설비)">
          {scenario.zones && <div className="banner info">ⓘ 다중 존 시나리오: 외피·면적·조명·인원·외기량은 존별 값(zones)을 씁니다 — 여기서 바꾼 건물 외피·내부 발열 값은 계산에 쓰이지 않고, 기상·IT 가동률·설비 값만 반영됩니다</div>}
          <InputPanel scenario={scenario} busy={busy} onApply={apply} onReset={() => sample && apply(sample)} />
        </Fold>
        <Fold title="냉각 구조 설정 (컨테인먼트·CRAH·배관·운전점)" force={layoutFold}>
          <LayoutEditor scenario={scenario} layout={coolLayout} cooling={cooling} busy={busy} onApply={apply}
            focus={focus} onFocusDone={() => setFocus(null)} />
        </Fold>
      </div>}

      {page === "year" && <div className="stack">
        <AnnualPanel scenario={scenario} busy={busy} state={annual} onState={setAnnual} keyOk={kmaOk}
          onOpenDay={(sc) => { apply(sc); setPage("now"); }} />
      </div>}

      {page === "forecast" && <div className="stack">
        <OutlookPanel scenario={scenario} busy={busy} onApply={apply} />
      </div>}

      {page === "improve" && <div className="stack">
        {sub === "report" && <ReportPanel scenario={scenario} annual={annual} cooling={cooling} status={status} demo={demo} coolDefault={coolDefault}
          onYear={() => setPage("year")} onOpt={() => { setSub("opt"); setOptMode("year"); }} onInvest={() => setSub("invest")} />}
        <div className="seg big">
          {([["opt", "운전 최적화"], ["tune", "자동 튜닝"], ["rack", "랙·냉각 방식"], ["invest", "투자 검토"], ["report", "보고서"]] as const).map(([k, l]) =>
            <button key={k} className={sub === k ? "on" : ""} onClick={() => setSub(k)}>{l}</button>)}
        </div>
        {sub === "opt" && <Card title="운전 최적화" hint="목적: 냉방 설비 전력 최소 · 제약: 랙 흡입온도 상한, 코일 접근온도 (연성 모델)"
          right={<div className="seg">{([["year", "연간 기준"], ["day", "하루 기준"]] as const).map(([k, l]) =>
            <button key={k} className={optMode === k ? "on" : ""} onClick={() => setOptMode(k)}>{l}</button>)}</div>}>
          {optMode === "year" ? <AnnualOptPanel scenario={scenario} busy={busy} onApply={apply} onReport={() => setSub("report")} />
            : <Optimizer scenario={scenario} busy={busy} onApply={apply} />}
        </Card>}
        {sub === "tune" && <Card title="자동 튜닝" hint="주의·경고가 나면 중요한 파라미터 순서대로 바꿔 해결 · 설정은 이 브라우저에 저장">
          <AutoTunePanel scenario={scenario} busy={busy} onApply={apply} />
        </Card>}
        {sub === "rack" && <Card title="랙별 냉각 방식 · 증설 배치" hint="CRAH 는 그대로 두고 기류 모델로 평가">
          <RackPanel scenario={scenario} layout={coolLayout} cooling={cooling} busy={busy} onApply={apply} />
        </Card>}
        {sub === "invest" && <Card title="투자 검토" hint="개선안별 투자비·연 절감·회수기간·NPV (연간 기상 기준)">
          <InvestPanel scenario={scenario} busy={busy} />
        </Card>}
      </div>}

      {page === "real" && <RealComparePage scenario={scenario} busy={busy} onApply={apply} />}
    </div>
    </GoTo.Provider>
  );
}

/** 시나리오 저장·불러오기·처음으로 */
function ScenarioMenu({ scenario, onLoad, onReset }: { scenario: Scenario; onLoad: (sc: Scenario) => void; onReset: () => void }) {
  const [list, setList] = useState<SavedInfo[]>([]);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = () => api.listScenarios().then(setList).catch(() => setList([]));
  return (
    <details className="menu" onToggle={(e) => (e.target as HTMLDetailsElement).open && refresh()}>
      <summary className="btn small">시나리오 ▾</summary>
      <div className="menu-pop">
        <div className="row">
          <input className="text" style={{ width: 160 }} placeholder="저장할 이름" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn small primary" disabled={!name.trim()} onClick={() =>
            api.saveScenario(name.trim(), scenario).then(() => { setMsg(`'${name.trim()}' 저장됨`); refresh(); })
              .catch((e) => setMsg((e as Error).message))}>저장</button>
        </div>
        {msg && <div className="muted">{msg}</div>}
        {list.length > 0 && <div className="muted" style={{ marginTop: 6 }}>불러오기</div>}
        {list.map((s) => <button key={s.name} className="menu-item" onClick={() =>
          api.getScenario(s.name).then(onLoad).catch((e) => setMsg((e as Error).message))}>{s.name}<span className="muted"> {s.saved_at.slice(0, 16)}</span></button>)}
        <button className="menu-item" onClick={onReset}>처음 상태(선택한 시나리오)로</button>
      </div>
    </details>
  );
}
