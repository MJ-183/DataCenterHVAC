import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AnnualArch, AnnualResult, Architecture, Scenario } from "../../types/api";
import { api } from "../../api/client";
import { RegionNote } from "../Import/RegionPanel";
import { ARCH_LABEL } from "../../lib/fields";
import { fmt } from "../../lib/format";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };
const ARCH_COLOR: Record<Architecture, string> = {
  air_chw: "var(--series-1)", hybrid_dlc: "var(--series-2)", immersion: "var(--series-3)",
};
// 냉방 전력 구성 (적층 막대). 순서 = 범주색 슬롯 순서
const POWER_PARTS = [
  { key: "chiller_kwh", label: "칠러", color: "var(--series-1)" },
  { key: "crah_fan_kwh", label: "CRAH 팬", color: "var(--series-2)" },
  { key: "pump_kwh", label: "펌프 (냉수·냉각수·액체)", color: "var(--series-3)" },
  { key: "tower_fan_kwh", label: "냉각탑 팬", color: "var(--series-4)" },
] as const;

/** App 에 보관 (탭을 옮겨도 결과 유지). base = 연간 계산에 쓴 시나리오, opened = 피크일로 연 시나리오 */
export interface AnnualState { res: AnnualResult; base: Scenario; opened: Scenario | null }

interface Props {
  scenario: Scenario; busy: boolean; state: AnnualState | null;
  onState: (s: AnnualState) => void; onOpenDay: (sc: Scenario) => void; keyOk: boolean;
}

/** 결과에 쓴 기상 출처 한 줄 */
export const wxLabel = (r: AnnualResult) => {
  const m = r.weather_meta as { source?: string; source_detail?: string; year?: number };
  return m.source === "kma_asos" ? `${m.year ?? r.year ?? ""}년 ${m.source_detail ?? "기상청 ASOS"}`
    : `${m.source_detail ?? "Open-Meteo 재분석 격자값"} (관측소 실측 아님)`;
};

function Legend({ items }: { items: readonly { label: string; color: string }[] }) {
  return <div className="legend">{items.map((s) => <span key={s.label}><span className="sw" style={{ background: s.color }} />{s.label}</span>)}</div>;
}

/** 월 x 시 히트맵: 그 달 중 h시가 해당 모드였던 날의 비율 (단색 명도 단계) */
function Heatmap({ a, mode }: { a: AnnualArch; mode: "free" | "partial" }) {
  const [hover, setHover] = useState<{ m: number; h: number } | null>(null);
  const rows = a.heatmap[mode];
  return (
    <div className="heat-wrap">
      <div className="heat" style={{ gridTemplateColumns: `34px repeat(24, minmax(0, 1fr))` }}>
        <span />
        {Array.from({ length: 24 }, (_, h) => <span key={h} className="heat-x">{h % 3 === 0 ? h : ""}</span>)}
        {a.heatmap.months.map((mo, i) => (
          <div key={mo} style={{ display: "contents" }}>
            <span className="heat-y">{mo}월</span>
            {rows[i].map((v, h) => (
              <span key={h} className="heat-cell" onMouseEnter={() => setHover({ m: i, h })} onMouseLeave={() => setHover(null)}
                style={{ background: v > 0 ? `color-mix(in srgb, var(--accent) ${Math.round(12 + 88 * v)}%, var(--surface-2))` : "var(--surface-2)" }}
                aria-label={`${mo}월 ${h}시 ${Math.round(v * 100)}%`} />
            ))}
          </div>
        ))}
      </div>
      <div className="row" style={{ justifyContent: "space-between", fontSize: 12, marginTop: 4 }}>
        <span className="muted">
          {hover ? <>{a.heatmap.months[hover.m]}월 {hover.h}시 · 프리쿨링 {fmt(a.heatmap.free[hover.m][hover.h] * 100, 0)}% ·
            부분 {fmt(a.heatmap.partial[hover.m][hover.h] * 100, 0)}% 의 날</> : "칸에 마우스를 올리면 비율 표시"}</span>
        <span className="heat-scale"><span>0%</span><span className="bar" /><span>100%</span></span>
      </div>
    </div>
  );
}

export function AnnualPanel({ scenario, busy, state, onState, onOpenDay, keyOk }: Props) {
  const [base, setBase] = useState("1.0");
  const [growth, setGrowth] = useState("0");
  const [arch, setArch] = useState<Architecture | null>(null);
  const [heatMode, setHeatMode] = useState<"free" | "partial">("free");
  const [itSrc, setItSrc] = useState<"scenario" | "dataset">(scenario.it_load.dataset_id ? "dataset" : "scenario");
  const [coupled, setCoupled] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [work, setWork] = useState(false);

  const baseV = Number(base), growthV = Number(growth);
  const bad = !(baseV > 0 && baseV <= 5) || !(growthV >= -90 && growthV <= 500) || Number.isNaN(growthV);
  // 피크일을 열어 바뀐 시나리오(기상·배율·방식)로 다시 돌리지 않도록, 그 경우엔 원래 기준 시나리오를 쓴다
  const source = state && state.opened === scenario ? state.base : scenario;

  const run = async () => {
    if (bad) return;
    setErr(null); setWork(true);
    try {
      const it_source = source.it_load.dataset_id ? itSrc : "scenario";
      const res = await api.annualRun(source, { base_multiplier: baseV, growth_pct_per_year: growthV, it_source, coupled });
      onState({ res, base: source, opened: null });
      setArch(null);
    } catch (e) { setErr((e as Error).message); }
    setWork(false);
  };

  const r = state?.res;
  const a = r ? (r.architectures.find((x) => x.architecture === (arch ?? r.current)) ?? r.architectures[0]) : null;

  const openDay = (date: string, monthLabel: string) => {
    if (!state || !a) return;
    const pd = state.res.peak_days[date];
    if (!pd) return;
    const sc: Scenario = JSON.parse(JSON.stringify(state.base));
    sc.weather = pd.weather;
    sc.site.date = date;
    sc.it_load.utilization = pd.utilization ?? sc.it_load.utilization.map((u) => u * pd.multiplier);
    sc.cooling_plant.architecture = a.architecture;
    const e = sc.cooling_layout?.operating_schedule?.find((x) => x.month === Number(date.slice(5, 7)));
    if (e && sc.cooling_layout) {   // 월별 스케줄이 있으면 그 달 운전점으로
      sc.cooling_layout.supply_temp_c = e.supply_temp_c;
      sc.cooling_layout.crah.airflow_ratio = e.airflow_ratio;
      sc.cooling_plant.chw_supply_c = e.chw_supply_c;
      sc.cooling_plant.chw_return_c = e.chw_return_c;
    }
    sc._note = `연간 시뮬레이션 ${state.res.year ?? ""}년 ${monthLabel} 피크일 ${date} · ${ARCH_LABEL[a.architecture]} · ` +
      `기상: ${wxLabel(state.res)} · IT 가동률 x${pd.multiplier} (가정). 그 외 값은 가정값.`;
    onState({ ...state, opened: sc });
    onOpenDay(sc);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const monthData = a?.monthly.map((m) => ({
    name: `${m.month}월`, month: m.month, date: m.peak_day.date,
    ...Object.fromEntries(POWER_PARTS.map((p) => [p.key, m[p.key] / 1000])),
    total: m.cooling_power_kwh / 1000, makeup: m.makeup_m3,
  })) ?? [];
  const pueData = r?.architectures[0].monthly.map((m, i) => ({
    name: `${m.month}월`, ...Object.fromEntries(r.architectures.map((x) => [x.architecture, x.monthly[i]?.pue])),
  })) ?? [];
  const dailyData = a?.daily.map((d) => ({ date: d.date, v: d.cooling_power_kwh / 1000, pk: d.peak_cooling_kw })) ?? [];
  const clickMonth = (st: unknown) => {
    const p = (st as { activePayload?: { payload: { date: string; name: string } }[] } | null)?.activePayload?.[0]?.payload;
    if (p) openDay(p.date, p.name);
  };

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>연간 시뮬레이션 (계절별 냉방부하)</h2>
          <span className="hint">365일 각각 24시간 엔진 계산 x 냉각 방식 3종 · 그날 기상 + IT 가동률(현재 24시간 모양 x 일별 배율)</span>
        </div>
        <div className="form-grid">
          <RegionNote scenario={scenario} />
          <div className="field"><label>일별 IT 배율 기본값</label>
            <input type="number" step={0.05} value={base} onChange={(e) => setBase(e.target.value)} /></div>
          <div className="field"><label>연간 증설률 [%/년] (선형)</label>
            <input type="number" step={1} value={growth} onChange={(e) => setGrowth(e.target.value)} /></div>
          {source.it_load.dataset_id && <div className="field"><label>IT 부하</label>
            <select value={itSrc} onChange={(e) => setItSrc(e.target.value as "scenario" | "dataset")}>
              <option value="dataset">IT 데이터셋 (월·요일 패턴)</option><option value="scenario">시나리오 24시간 모양</option></select></div>}
          <div className="field"><label>냉각 구조 반영</label>
            <label className="muted" style={{ fontSize: 13 }}><input type="checkbox" checked={coupled} onChange={(e) => setCoupled(e.target.checked)} /> 연성 모델(기류·배관) 연간 전력도 계산</label></div>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={work || busy || bad || !keyOk} onClick={run}>
            {work ? "기상청 자료 받는 중·계산 중… (처음 받는 연도는 30초~1분)" : "기상 받아서 연간 계산"}</button>
          {!keyOk && <span className="muted">데이터 입력 → 기상청 API 인증키를 먼저 입력하세요</span>}
          {source !== scenario && <span className="muted">피크일 보기 전의 기준 시나리오로 계산합니다</span>}
          {r?.cached && <span className="muted">캐시된 결과</span>}
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          {r ? <>기상: {wxLabel(r)}</> : "기상청 ASOS 시간자료(관측소 실측)의 기온·습도·일사로 365일을 계산합니다. 한 번 받은 연도는 저장해 두고 다시 받지 않습니다."}
          {" "}나머지 가정은 결과 아래 목록에 있습니다.</div>
        {err && <div className="banner crit">{err}</div>}
      </div>

      {r && a && <>
        <div className="card">
          <div className="card-head">
            <h2>냉각 방식별 연간 비교 · {r.year}년 {r.n_days}일</h2>
            <span className="hint">행을 누르면 아래 월별 화면이 그 방식으로 바뀝니다 · 현재 시나리오 방식: {ARCH_LABEL[r.current]}</span>
          </div>
          <div className="table-scroll">
            <table className="data">
              <thead><tr><th>냉각 방식</th><th>냉방 전력 MWh</th><th>칠러 MWh</th><th>설비 전체 MWh</th><th>PUE</th>
                <th>보급수 m³</th><th>WUE L/kWh</th><th>프리쿨링 h</th><th>부분 h</th><th>칠러 h</th><th>피크 냉방 kW</th></tr></thead>
              <tbody>{r.comparison.map((c) => (
                <tr key={c.architecture} onClick={() => setArch(c.architecture)} style={{ cursor: "pointer",
                  fontWeight: c.architecture === a.architecture ? 650 : undefined }}>
                  <td><span className="sw-inline" style={{ background: ARCH_COLOR[c.architecture] }} />{ARCH_LABEL[c.architecture]}
                    {c.architecture === r.current ? " (현재)" : ""}</td>
                  <td>{fmt(c.cooling_power_kwh / 1000, 0)}</td><td>{fmt(c.chiller_kwh / 1000, 0)}</td>
                  <td>{fmt(c.facility_kwh / 1000, 0)}</td><td>{fmt(c.pue, 3)}</td><td>{fmt(c.makeup_m3, 0)}</td>
                  <td>{fmt(c.wue_l_per_kwh, 2)}</td><td>{fmt(c.free_h, 0)}</td><td>{fmt(c.partial_h, 0)}</td>
                  <td>{fmt(c.chiller_h, 0)}</td><td>{fmt(c.peak_cooling_kw, 0)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", marginTop: 12 }}>
            <div className="kpi"><div className="label">연 냉방부하 ({ARCH_LABEL[a.architecture]})</div>
              <div className="value">{fmt(a.annual.cooling_kwh / 1000, 0)}<span className="unit">MWh</span></div></div>
            <div className="kpi"><div className="label">연 PUE</div><div className="value">{fmt(a.annual.pue, 3)}</div></div>
            {r.coupled && <div className="kpi"><div className="label">연성 모델 냉방 전력 (현재 방식{r.coupled.uses_schedule ? "·월 스케줄" : ""})</div>
              <div className="value">{fmt(r.coupled.annual_kwh / 1000, 0)}<span className="unit">MWh</span></div>
              <div className="detail">기류 팬·배관 펌프 반영, 냉각탑 팬 제외</div></div>}
            <div className="kpi"><div className="label">연 보급수</div>
              <div className="value">{fmt(a.annual.makeup_m3, 0)}<span className="unit">m³</span></div></div>
            <div className="kpi"><div className="label">연 최대 피크일</div>
              <div className="value">{fmt(a.annual.peak_day.peak_cooling_kw, 0)}<span className="unit">kW</span></div>
              <div className="detail">{a.annual.peak_day.date} {a.annual.peak_day.hour}시 · 건구 {fmt(a.annual.peak_day.max_dry_bulb_c, 1)}℃
                · 습구 {fmt(a.annual.peak_day.max_wet_bulb_c, 1)}℃</div></div>
          </div>
          <ul className="notes">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>

        <div className="card">
          <div className="card-head">
            <h2>월별 · {ARCH_LABEL[a.architecture]}</h2>
            <span className="hint">막대를 누르면 그 달 피크일(최대 냉방부하 날)을 위 24시간 화면으로 엽니다</span>
          </div>
          <div className="chart-grid">
            <div>
              <h3 className="sub-h">월별 냉방 전력 [MWh]</h3>
              <Legend items={POWER_PARTS} />
              <div className="chart-box">
                <ResponsiveContainer>
                  <BarChart data={monthData} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} onClick={clickMonth} style={{ cursor: "pointer" }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="name" {...axis} /><YAxis {...axis} width={48} />
                    <Tooltip cursor={{ fill: "var(--surface-2)" }} formatter={(v, n) => [`${fmt(Number(v), 1)} MWh`,
                      POWER_PARTS.find((p) => p.key === n)?.label ?? String(n)]}
                      labelFormatter={(l, p) => `${l} · 합계 ${fmt(Number(p?.[0]?.payload?.total), 1)} MWh · 클릭: 피크일 열기`} />
                    {POWER_PARTS.map((p, i) => <Bar key={p.key} dataKey={p.key} stackId="p" fill={p.color} stroke="var(--surface-1)"
                      strokeWidth={1} radius={i === POWER_PARTS.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />)}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div>
              <h3 className="sub-h">월별 냉각탑 보급수 [m³]</h3>
              <div className="legend">&nbsp;</div>
              <div className="chart-box">
                <ResponsiveContainer>
                  <BarChart data={monthData} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} onClick={clickMonth} style={{ cursor: "pointer" }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="name" {...axis} /><YAxis {...axis} width={48} />
                    <Tooltip cursor={{ fill: "var(--surface-2)" }} formatter={(v) => [`${fmt(Number(v), 0)} m³`, "보급수"]} />
                    <Bar dataKey="makeup" fill="var(--series-1)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div>
              <h3 className="sub-h">월별 PUE (냉각 방식별)</h3>
              <Legend items={r.architectures.map((x) => ({ label: ARCH_LABEL[x.architecture], color: ARCH_COLOR[x.architecture] }))} />
              <div className="chart-box">
                <ResponsiveContainer>
                  <LineChart data={pueData} margin={{ top: 6, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="name" {...axis} /><YAxis {...axis} width={48} domain={["auto", "auto"]} tickFormatter={(v: number) => v.toFixed(2)} />
                    <Tooltip formatter={(v, n) => [fmt(Number(v), 3), ARCH_LABEL[String(n)] ?? String(n)]} />
                    {r.architectures.map((x) => <Line key={x.architecture} dataKey={x.architecture} stroke={ARCH_COLOR[x.architecture]}
                      strokeWidth={x.architecture === a.architecture ? 2.5 : 1.5} dot={{ r: 3 }} isAnimationActive={false} />)}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div>
              <h3 className="sub-h">일별 냉방 전력 [MWh/일]</h3>
              <div className="legend">&nbsp;</div>
              <div className="chart-box">
                <ResponsiveContainer>
                  <LineChart data={dailyData} margin={{ top: 6, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="date" {...axis} tickFormatter={(d: string) => d.endsWith("-01") ? `${Number(d.slice(5, 7))}월` : ""}
                      interval={0} minTickGap={0} />
                    <YAxis {...axis} width={48} />
                    <Tooltip formatter={(v) => [`${fmt(Number(v), 2)} MWh`, "냉방 전력"]}
                      labelFormatter={(d, p) => `${d} · 피크 냉방 ${fmt(Number(p?.[0]?.payload?.pk), 0)} kW`} />
                    <Line dataKey="v" stroke="var(--series-1)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          <div className="row" style={{ marginTop: 14, gap: 8 }}>
            <h3 className="sub-h" style={{ margin: 0 }}>프리쿨링 시간 분포 (월 x 시)</h3>
            <select value={heatMode} onChange={(e) => setHeatMode(e.target.value as "free" | "partial")}>
              <option value="free">프리쿨링 (칠러 OFF) 인 날의 비율</option>
              <option value="partial">부분 프리쿨링 인 날의 비율</option>
            </select>
          </div>
          <Heatmap a={a} mode={heatMode} />

          <div className="table-scroll" style={{ marginTop: 12 }}>
            <table className="data">
              <thead><tr><th>월</th><th>냉방부하 MWh</th><th>냉방 전력 MWh</th><th>칠러 MWh</th><th>PUE</th><th>보급수 m³</th>
                <th>프리 h</th><th>부분 h</th><th>칠러 h</th><th>피크일</th><th>피크 kW</th><th /></tr></thead>
              <tbody>{a.monthly.map((m) => (
                <tr key={m.month}>
                  <td>{m.month}월</td><td>{fmt(m.cooling_kwh / 1000, 0)}</td><td>{fmt(m.cooling_power_kwh / 1000, 1)}</td>
                  <td>{fmt(m.chiller_kwh / 1000, 1)}</td><td>{fmt(m.pue, 3)}</td><td>{fmt(m.makeup_m3, 0)}</td>
                  <td>{fmt(m.free_h, 0)}</td><td>{fmt(m.partial_h, 0)}</td><td>{fmt(m.chiller_h, 0)}</td>
                  <td>{m.peak_day.date.slice(5)} {m.peak_day.hour}시</td><td>{fmt(m.peak_day.peak_cooling_kw, 0)}</td>
                  <td><button className="btn small" disabled={busy} onClick={() => openDay(m.peak_day.date, `${m.month}월`)}>24시간 보기</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      </>}
    </>
  );
}
