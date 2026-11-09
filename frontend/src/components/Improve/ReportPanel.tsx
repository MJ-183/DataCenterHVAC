import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import type { AnnualOptResult, CoolingLayout, EvaluateResult, InvestResult, Scenario, ScenarioStatus, ScheduleEntry, StructurePlan } from "../../types/api";
import { ARCH_LABEL, crahText } from "../../lib/fields";
import { fmt } from "../../lib/format";
import { Card, krw, useJob, usePersist } from "../../lib/ui";
import { type AnnualState, wxLabel } from "../Annual/AnnualPanel";
import { regionText, useStations } from "../Import/RegionPanel";

const MARGIN_WARN_C = 1;   // 흡입온도 여유 주의 기준 (backend outlook.MARGIN_WARN_C)
const W = 660;   // A4 본문 폭 [px] — 인쇄에서 ResponsiveContainer 가 깨지지 않도록 고정
const axis = { stroke: "#77766f", fontSize: 11, tickLine: false };
const CONT = { open: "개방형", cold_aisle: "콜드아일", hot_aisle: "핫아일" } as const;
const TOPO = { radial: "단일 헤더", dual: "2계통", ring: "루프" } as const;
const SOURCE: Record<string, string> = {
  assumed: "가정값", user: "직접 입력", cad: "도면", it_data: "IT 데이터", bms_calibrated: "BMS 보정",
  optimizer: "최적화", schedule: "월별 스케줄", weather_api: "기상 API", published: "공개 자료", real_calibrated: "실측 비교 보정",
};

/** 결과에 영향을 주는 입력만 — 운전점·구조는 최적화를 적용하면 바뀌는 값이라 제외. 다르면 그 결과는 지금 시나리오의 것이 아님 */
const conditions = (sc: Scenario, defaults: CoolingLayout) => JSON.stringify({
  site: [sc.site.asos_station_id, sc.site.asos_solar_station_id, sc.site.asos_solar, sc.site.weather_year],
  it: [sc.it_load.design_kw, sc.it_load.dataset_id, sc.it_load.utilization], envelope: sc.envelope, indoor: sc.indoor,
  plant: [sc.cooling_plant.architecture, sc.cooling_plant.liquid_fraction, sc.cooling_plant.immersion_capture,
    sc.cooling_plant.chiller_cop_rated, sc.cooling_plant.free_cooling, sc.cooling_plant.realism],
  // 냉각 구조가 비어 있던 시나리오는 적용 시 기본값이 채워지므로, 기본값으로 맞추고 키 순서·빈 값 차이를 없애 비교
  racks: Object.entries({ ...defaults.racks, ...sc.cooling_layout?.racks }).filter(([, v]) => v != null).sort(([a], [b]) => a.localeCompare(b)),
});
type State = "ok" | "missing" | "stale";

const planName = (p: StructurePlan) => (p.is_current ? "지금 구조 + 월별 운전" : "구조 변경 + 월별 운전");
const structText = (s: StructurePlan["structure"], sc: Scenario) => `${CONT[s.containment]} · ${TOPO[s.topology]} · ${crahText(sc, s.crah_per_side)}`;

function Check({ label, state, need, onGo }: { label: string; state: State; need: boolean; onGo: () => void }) {
  const text = state === "ok" ? "완료" : state === "stale" ? "조건이 바뀜 — 다시 계산 필요" : need ? "미실행" : "미실행 (선택, 보고서에서 제외)";
  return (
    <li className="row" style={{ gap: 8 }}>
      <span style={{ color: state === "ok" ? "var(--good)" : need ? "var(--critical)" : "var(--text-muted)", width: 14 }}>{state === "ok" ? "✓" : need ? "✕" : "–"}</span>
      <span style={{ width: 110 }}>{label}</span><span className="muted">{text}</span>
      {state !== "ok" && <button className="btn small" onClick={onGo}>{state === "stale" ? "다시 계산하러 가기" : "실행하러 가기"}</button>}
    </li>
  );
}

interface Props {
  scenario: Scenario; annual: AnnualState | null; cooling: EvaluateResult | null; status: ScenarioStatus | null; demo: boolean;
  coolDefault: CoolingLayout; onYear: () => void; onOpt: () => void; onInvest: () => void;
}

/** 최종 보고서: 연간 예측 + 연간 최적화(+투자 검토) 가 지금 조건으로 끝나면 A4 로 출력 (브라우저 인쇄 → PDF 저장) */
export function ReportPanel({ scenario, annual, cooling, status, demo, coolDefault, onYear, onOpt, onInvest }: Props) {
  const opt = useJob<AnnualOptResult>("annual_optimize");
  const inv = useJob<InvestResult>("invest");
  const [pick] = usePersist<"same" | "best">("aopt:pick", "same");
  const stations = useStations();
  const today = new Date().toISOString().slice(0, 10);

  // 피크일을 열어 본 상태면 그 날의 기상·가동률이 아니라 원래 기준 시나리오로 비교
  const base = annual && annual.opened === scenario ? annual.base : scenario;
  const now = conditions(base, coolDefault);
  const stateOf = (has: boolean, sc?: Scenario): State => (!has ? "missing" : sc && conditions(sc, coolDefault) === now ? "ok" : "stale");
  const stYear = stateOf(!!annual, annual?.base);
  const stOpt = stateOf(!!opt.result, (opt.request as { scenario?: Scenario } | null)?.scenario);
  const stInv = stateOf(!!inv.result, (inv.request as { scenario?: Scenario } | null)?.scenario);
  const ready = stYear === "ok" && stOpt === "ok";

  const print = () => {
    const old = document.title;
    const region = scenario.site.name.replace(/\s+/g, "_");
    document.title = `냉방운전보고서_${region}_${today}`;     // PDF 기본 파일 이름
    window.addEventListener("afterprint", () => { document.title = old; }, { once: true });
    window.print();
  };

  const check = (
    <Card title="최종 보고서" hint="연간 예측과 연간 기준 운전 최적화가 지금 조건으로 끝나면 출력할 수 있습니다"
      right={<button className="btn primary" disabled={!ready} onClick={print}>인쇄 / PDF 저장</button>}>
      <ul className="report-check">
        <Check label="연간 예측" state={stYear} need onGo={onYear} />
        <Check label="연간 최적화" state={stOpt} need onGo={onOpt} />
        <Check label="투자 검토" state={stInv} need={false} onGo={onInvest} />
      </ul>
      {ready && <div className="muted" style={{ marginTop: 6, fontSize: 12.5 }}>
        인쇄 창에서 대상을 'PDF로 저장'으로 고르세요. 파일 이름이 자동으로 채워집니다. '머리글과 바닥글'을 끄면 브라우저 주소가 인쇄되지 않습니다.</div>}
    </Card>
  );
  if (!ready || !annual || !opt.result) return <div className="no-print">{check}</div>;
  return (
    <>
      <div className="no-print">{check}</div>
      <Report scenario={base} annual={annual} r={opt.result} plan={pick === "best" ? opt.result.best : opt.result.same_structure ?? opt.result.best}
        invest={stInv === "ok" ? inv.result : null} cooling={cooling} status={status} demo={demo} date={today} region={regionText(base.site, stations)}
        applied={scenario.cooling_layout?.operating_schedule ?? null} />
    </>
  );
}

function Report({ scenario: sc, annual, r, plan, invest, cooling, status, demo, date, region, applied }: {
  scenario: Scenario; annual: AnnualState; r: AnnualOptResult; plan: StructurePlan; invest: InvestResult | null;
  cooling: EvaluateResult | null; status: ScenarioStatus | null; demo: boolean; date: string; region: string;
  applied: ScheduleEntry[] | null;
}) {
  const res = annual.res;
  const arch = res.architectures.find((a) => a.architecture === res.current) ?? res.architectures[0];
  const save = r.current.annual_kwh - plan.monthly_kwh;
  const pct = r.current.annual_kwh > 0 ? (save / r.current.annual_kwh) * 100 : 0;
  const isApplied = !!applied && plan.schedule.every((e) => applied.some((a) =>
    a.month === e.month && a.supply_temp_c === e.supply_temp_c && a.airflow_ratio === e.airflow_ratio));
  const badMonths = r.current.violations;   // 흡입 상한을 넘는 달 (월 번호 목록)
  const minMargin = Math.min(...plan.schedule.map((e) => e.inlet_margin_c));
  const data = useMemo(() => r.months.map((m, i) => ({ name: `${m}월`, 현재: r.current.monthly[i] / 1000, 최적안: plan.monthly[i] / 1000 })), [r, plan]);
  const yearly = arch.monthly.map((m) => ({ name: `${m.month}월`, 칠러: m.chiller_kwh / 1000, "CRAH 팬": m.crah_fan_kwh / 1000, 펌프: m.pump_kwh / 1000, "냉각탑 팬": m.tower_fan_kwh / 1000 }));
  const top = invest?.results.slice(0, 3) ?? [];
  const COLORS = ["#2a78d6", "#eb6834", "#1baf7a"];
  const years = top[0]?.cumulative_krw.length ?? 0;
  const flows = Array.from({ length: years }, (_, y) => ({ y, ...Object.fromEntries(top.map((x) => [x.label, x.cumulative_krw[y] / 1e8])) }));
  const items = status?.trust.items ?? [];
  const assumed = items.filter((i) => i.source === "assumed");
  const known = items.filter((i) => i.source !== "assumed");
  const issues = cooling?.issues ?? [];

  return (
    <article className="report">
      <header className="report-head">
        <div className="report-kicker">데이터센터 냉방 운전 최적화 보고서</div>
        <h1>{sc.site.name}</h1>
        <div className="report-meta">작성일 {date} · {region}</div>
      </header>

      {demo && <p className="report-warn">이 보고서에는 도면·IT·BMS 실측이 반영되지 않았습니다. 예시 설비값으로 계산한 시연 결과이므로 실제 설비의 성능 보증이나 투자 판단 근거로 쓰면 안 됩니다.</p>}

      <section>
        <h2>1. 요약</h2>
        <p className="report-lead">{planName(plan)} 안을 적용하면 연간 냉방 설비 전력이
          {" "}{fmt(r.current.annual_kwh / 1000, 0)} MWh에서 {fmt(plan.monthly_kwh / 1000, 0)} MWh로
          {" "}{fmt(Math.abs(save) / 1000, 0)} MWh({fmt(Math.abs(pct), 1)}%) {save >= 0 ? "줄어듭니다" : "늘어납니다"}.
          {plan.is_current ? " 공사 없이 월별 운전점만 바꾸는 안입니다." : ` 구조 공사(${structText(plan.structure, sc)})가 필요한 안입니다.`}
          {invest ? ` ${invest.summary}` : ""}</p>
        <div className="report-kpis">
          <div><span>현재 운전</span><b>{fmt(r.current.annual_kwh / 1000, 0)} MWh/년</b></div>
          <div><span>최적안</span><b>{fmt(plan.monthly_kwh / 1000, 0)} MWh/년</b></div>
          <div><span>연간 {save >= 0 ? "절감" : "증가"}</span><b>{fmt(Math.abs(save) / 1000, 0)} MWh ({fmt(Math.abs(pct), 1)}%)</b></div>
          <div><span>연 PUE ({ARCH_LABEL[res.current]}, 현재 운전)</span><b>{fmt(arch.annual.pue, 3)}</b></div>
        </div>
        <p className="report-note">이 표의 전력량은 연성 모델 값입니다 (칠러 + CRAH 팬 + 냉수·냉각수 펌프 + 액체 루프, 냉각탑 팬 제외). 3장의 연간 값은 냉각탑 팬까지 포함한 엔진 모델이라 값이 다릅니다.
          {" "}{isApplied ? "이 안은 현재 시나리오에 적용되어 있습니다." : "이 안은 아직 시나리오에 적용되지 않았습니다."}</p>
        {badMonths.length > 0 && <p className="report-warn">현재 운전은 {badMonths.map((m) => `${m}월`).join(", ")}에 랙 흡입온도 상한을 넘습니다. 최적안의 전력이 더 크더라도 안전 쪽으로 조정한 결과일 수 있습니다.</p>}
      </section>

      <section>
        <h2>2. 분석 조건</h2>
        <table className="report-table"><tbody>
          <tr><th>사이트</th><td>{sc.site.name} (위도 {sc.site.latitude}, 경도 {sc.site.longitude})</td></tr>
          <tr><th>기상</th><td>{wxLabel(res)}</td></tr>
          <tr><th>IT 부하</th><td>설계 {fmt(sc.it_load.design_kw, 0)} kW · {res.it_source === "dataset" ? "IT 데이터셋" : "시나리오 24시간 가동률"} · 일별 배율 {res.base_multiplier}, 연 증설 {res.growth_pct_per_year}%</td></tr>
          <tr><th>냉각 방식</th><td>{ARCH_LABEL[res.current]} · 프리쿨링 {sc.cooling_plant.free_cooling ? "사용" : "미사용"} · 칠러 정격 COP {sc.cooling_plant.chiller_cop_rated}</td></tr>
          <tr><th>현재 구조</th><td>{structText(r.current.structure, sc)}</td></tr>
          <tr><th>계산 모델</th><td>{sc.cooling_plant.realism?.control ? "제어 이력(R1) 반영" : "단순 모델 (제어 이력·칠러 대수·부분부하 곡선 미반영)"}</td></tr>
        </tbody></table>
      </section>

      <section>
        <h2>3. 연간 예측 ({res.year}년 {res.n_days}일)</h2>
        <table className="report-table num"><thead><tr><th>냉각 방식</th><th>냉방 전력 MWh</th><th>칠러 MWh</th><th>설비 전체 MWh</th><th>PUE</th><th>보급수 m³</th><th>프리쿨링 h</th><th>칠러 h</th></tr></thead>
          <tbody>{res.comparison.map((c) => <tr key={c.architecture} className={c.architecture === res.current ? "on" : ""}>
            <td>{ARCH_LABEL[c.architecture]}{c.architecture === res.current ? " (현재)" : ""}</td><td>{fmt(c.cooling_power_kwh / 1000, 0)}</td><td>{fmt(c.chiller_kwh / 1000, 0)}</td>
            <td>{fmt(c.facility_kwh / 1000, 0)}</td><td>{fmt(c.pue, 3)}</td><td>{fmt(c.makeup_m3, 0)}</td><td>{fmt(c.free_h, 0)}</td><td>{fmt(c.chiller_h, 0)}</td></tr>)}</tbody></table>
        <h3>월별 냉방 설비 전력 [MWh] — {ARCH_LABEL[res.current]}</h3>
        <BarChart width={W} height={210} data={yearly} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#e6e5e0" vertical={false} /><XAxis dataKey="name" {...axis} /><YAxis {...axis} width={44} />
          <Tooltip /><Legend wrapperStyle={{ fontSize: 11 }} />
          {([["칠러", "#2a78d6"], ["CRAH 팬", "#eb6834"], ["펌프", "#1baf7a"], ["냉각탑 팬", "#eda100"]] as const).map(([k, c]) =>
            <Bar key={k} dataKey={k} stackId="p" fill={c} isAnimationActive={false} />)}
        </BarChart>
      </section>

      <section>
        <h2>4. 운전 최적화 결과</h2>
        <p>{r.summary}</p>
        <h3>월별 냉방 전력 [MWh] — 현재 운전 vs {planName(plan)}</h3>
        <BarChart width={W} height={200} data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#e6e5e0" vertical={false} /><XAxis dataKey="name" {...axis} /><YAxis {...axis} width={44} />
          <Tooltip /><Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="현재" fill="#eb6834" isAnimationActive={false} /><Bar dataKey="최적안" fill="#2a78d6" isAnimationActive={false} />
        </BarChart>
        {minMargin < MARGIN_WARN_C && <p className="report-warn">이 스케줄의 랙 흡입온도 여유는 최소 {fmt(minMargin, 2)}℃입니다. 상한은 지키지만 브리핑 권장 기준({MARGIN_WARN_C}℃)보다 작아, 적용하면 '여유 작음' 주의가 뜰 수 있습니다.</p>}
        <h3>월별 운전 스케줄 ({structText(plan.structure, sc)})</h3>
        <table className="report-table num"><thead><tr><th>월</th><th>급기 ℃</th><th>냉수 공급/환수 ℃</th><th>풍량</th><th>냉방 전력 kWh</th><th>흡입 여유 ℃</th></tr></thead>
          <tbody>{plan.schedule.map((e) => <tr key={e.month}><td>{e.month}월</td><td>{fmt(e.supply_temp_c, 1)}</td>
            <td>{fmt(e.chw_supply_c, 1)} / {fmt(e.chw_return_c, 1)}</td><td>{fmt(e.airflow_ratio * 100, 0)}%</td><td>{fmt(e.kwh, 0)}</td><td>{fmt(e.inlet_margin_c, 2)}</td></tr>)}</tbody></table>
        <p className="report-note">계산 검증: 벡터 계산과 스칼라 재계산의 차이 최대 {fmt(r.check.max_rel_diff * 100, 3)}% {r.check.ok ? "(허용 범위)" : "(허용 범위 초과 — 결과 확인 필요)"}</p>
      </section>

      <section>
        <h2>5. 현재 설정 점검</h2>
        {cooling && <p>현재 시나리오 하루({sc.site.date}) 기준 랙 최대 흡입온도 {fmt(cooling.summary.max_inlet_c, 1)}℃, 상한 초과 랙 {cooling.summary.racks_over_limit}개.</p>}
        {!issues.length ? <p>주의·경고 항목이 없습니다.</p> : issues.map((i) => <div key={i.code + i.title} className="report-issue">
          <b>[{i.level === "crit" ? "경고" : i.level === "warn" ? "주의" : "참고"}] {i.title}</b><p>{i.cause}</p>
          {i.fixes.length > 0 && <ul>{i.fixes.map((f) => <li key={f.target + f.label}>{f.label}{f.effect ? ` — ${f.effect}` : ""}</li>)}</ul>}</div>)}
      </section>

      {invest && <section>
        <h2>6. 투자 검토</h2>
        {invest.demo && <p className="report-warn">투자비는 예시 단가로 계산한 값입니다. 실제 견적으로 바꾸기 전에는 참고용입니다.</p>}
        <table className="report-table num"><thead><tr><th>개선안</th><th>투자비</th><th>연 절감 MWh</th><th>연 절감액</th><th>단순 회수</th><th>NPV</th><th>IRR</th></tr></thead>
          <tbody>{invest.results.map((x) => <tr key={x.key}><td>{x.label}{x.demo_capex ? " (예시)" : ""}</td><td>{krw(x.capex_krw)}</td><td>{fmt(x.year1_saving_kwh / 1000, 0)}</td><td>{krw(x.year1_saving_krw)}</td>
            <td>{x.payback_years == null ? "회수 불가" : `${fmt(x.payback_years, 1)}년`}</td><td>{krw(x.npv_krw)}</td><td>{x.irr == null ? "–" : `${fmt(x.irr * 100, 0)}%`}</td></tr>)}</tbody></table>
        {top.length > 0 && <><h3>누적 현금흐름 [억 원] (상위 {top.length}개)</h3>
          <LineChart width={W} height={200} data={flows} margin={{ top: 6, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="#e6e5e0" vertical={false} /><XAxis dataKey="y" {...axis} tickFormatter={(y: number) => `${y}년`} /><YAxis {...axis} width={48} />
            <ReferenceLine y={0} stroke="#77766f" /><Legend wrapperStyle={{ fontSize: 11 }} />
            {top.map((x, i) => <Line key={x.key} dataKey={x.label} stroke={COLORS[i]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
          </LineChart></>}
      </section>}

      <section>
        <h2>{invest ? 7 : 6}. 데이터 근거와 한계</h2>
        {status && <p>주요 입력 {items.length}개 중 측정·도면·기상 자료 {status.trust.measured}개, 사용자 결정 {status.trust.decided}개, 가정값 {status.trust.assumed}개.</p>}
        {known.length > 0 && <><h3>근거가 있는 입력</h3><p className="report-list">{known.map((i) => `${i.label}(${SOURCE[i.source] ?? i.source})`).join(" · ")}</p></>}
        {assumed.length > 0 && <><h3>가정값 (실제 설비 자료로 교체 필요)</h3><p className="report-list">{assumed.map((i) => i.label).join(" · ")}</p></>}
        <h3>계산 참고 사항</h3>
        <ul className="report-notes">{[...res.notes, ...r.notes, ...(invest?.notes ?? [])].map((n) => <li key={n}>{n}</li>)}</ul>
        <p className="report-note">실측과 대조하지 않은 값은 실제 전력량이 아니라 조건 간 상대 비교로 해석해야 합니다.</p>
      </section>
    </article>
  );
}
