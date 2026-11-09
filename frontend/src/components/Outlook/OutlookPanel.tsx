import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Outlook, OutlookDay, OutlookMonth, PlantMode, Scenario } from "../../types/api";
import { api } from "../../api/client";
import { MODE_LABEL } from "../../lib/fields";
import { fmt, hh } from "../../lib/format";
import { Card, usePersist } from "../../lib/ui";
import { IssueList } from "../../lib/issues";

const axis = { stroke: "var(--text-muted)", fontSize: 11, tickLine: false };
const MODE_COLOR = { free: "var(--series-3)", partial: "var(--series-4)", chiller: "var(--series-2)" } as const;
type Period = "day" | "month";

// 0~23시 (category 축 자동 간격은 01·03시처럼 홀수 시각부터 찍혀 0시가 빠져 보임)
const hourAxis = <XAxis dataKey="hour" type="number" domain={[0, 23]} ticks={[0, 3, 6, 9, 12, 15, 18, 21]}
  {...axis} tickFormatter={(h: number) => hh(h)} />;

/** 시간별 운전 모드 띠 (차트 x 축과 같은 폭 정렬은 하지 않음 — 24칸 균등) */
const modeBar = (modes: PlantMode[], label: string) => (
  <div className="row" style={{ gap: 8, marginTop: 4, alignItems: "center" }}>
    <span className="muted" style={{ fontSize: 12, width: 92, flexShrink: 0 }}>{label}</span>
    <div className="modebar" style={{ flex: 1, marginTop: 0 }}>
      {modes.map((m, k) => <span key={k} style={{ background: MODE_COLOR[m] }} title={`${hh(k)} ${MODE_LABEL[m]}`} />)}
    </div>
  </div>
);

const Legend2 = ({ a, b }: { a: string; b: string }) => (
  <div className="legend"><span><span className="sw" style={{ background: "var(--series-2)" }} />{a}</span>
    <span><span className="sw" style={{ background: "var(--series-1)" }} />{b}</span></div>
);

/** 툴팁: 두 운전점의 합계 = 칠러 + CRAH 팬 + 펌프, 운전 모드, 습구온도 */
function PowerTip({ o, active, payload }: { o: OutlookDay; active?: boolean; payload?: readonly { payload?: unknown }[] }) {
  if (!active || !payload?.length) return null;
  const h = payload[0].payload as OutlookDay["hourly"][number];
  const row = (label: string, color: string, tot: number, ch: number, fan: number, mode: PlantMode) => (
    <div className="r"><span><span className="sw" style={{ background: color, display: "inline-block", width: 8, height: 8, borderRadius: 2, marginRight: 5 }} />
      {label} · {MODE_LABEL[mode]}</span>
      <span>{fmt(tot, 0)} kW <span className="muted">(칠러 {fmt(ch, 0)} · 팬 {fmt(fan, 0)} · 펌프 {fmt(tot - ch - fan, 0)})</span></span></div>
  );
  return (
    <div className="tip">
      <div className="t">{hh(h.hour)} · 습구 {fmt(h.wet_bulb_c, 1)}℃</div>
      {row(o.current_label, "var(--series-2)", h.current_kw, h.current_chiller_kw, h.current_fan_kw, h.current_mode)}
      {row("권장", "var(--series-1)", h.recommended_kw, h.recommended_chiller_kw, h.recommended_fan_kw, h.mode)}
    </div>
  );
}

/** 예보 기온 한 줄 해석: 최저·최고 시각, 밤에 오르는 경우 습도 변화 (습구가 칠러 가동을 좌우) */
function weatherNote(hs: OutlookDay["hourly"]) {
  const lo = hs.reduce((a, b) => (b.dry_bulb_c < a.dry_bulb_c ? b : a));
  const hi = hs.reduce((a, b) => (b.dry_bulb_c > a.dry_bulb_c ? b : a));
  const wb = hs.reduce((a, b) => (b.wet_bulb_c > a.wet_bulb_c ? b : a));
  let s = `기온 최저 ${fmt(lo.dry_bulb_c, 1)}℃(${hh(lo.hour)}) · 최고 ${fmt(hi.dry_bulb_c, 1)}℃(${hh(hi.hour)})`;
  if (hi.hour >= 18 || hi.hour <= 5) {
    const rh0 = Math.min(...hs.map((h) => h.rh_pct)), rh1 = hs[hi.hour].rh_pct;
    s += ` — 낮이 아니라 ${hh(hi.hour)}에 가장 따뜻한 것은 예보 원본 그대로입니다`
      + (rh1 - rh0 >= 20 ? ` (습도 ${fmt(rh0, 0)}→${fmt(rh1, 0)}%, 따뜻하고 습한 공기 유입 — 비 예보일 수 있음)` : "");
  }
  return `${s}. 습구온도 최고 ${fmt(wb.wet_bulb_c, 1)}℃(${hh(wb.hour)}) — 습구가 높을수록 냉각탑 물이 덜 식어 프리쿨링이 줄고 칠러가 켜집니다.`;
}

/** 강수형태·강수확률 띠 (기상청 단기예보 원본) */
function PrecipStrip({ p }: { p: NonNullable<OutlookDay["precip"]> }) {
  const rain = p.pty.some((x) => x && x !== "없음");
  return (
    <div style={{ marginTop: 8 }}>
      <div className="muted" style={{ fontSize: 12 }}>기상청 단기예보 — 하늘·강수형태·강수확률
        {rain ? "" : " (강수 없음)"}</div>
      <div className="modebar" style={{ marginTop: 4 }}>
        {p.pop_pct.map((v, k) => {
          const wet = p.pty[k] && p.pty[k] !== "없음";
          return <span key={k} title={`${hh(k)} ${p.sky[k] ?? ""} · 강수 ${p.pty[k] ?? "-"} ${p.pcp[k] ?? ""} · 확률 ${v ?? "-"}%`}
            style={{ background: `color-mix(in srgb, ${wet ? "var(--series-1)" : "var(--text-muted)"} ${Math.round(10 + 0.9 * (v ?? 0))}%, var(--surface-2))` }} />;
        })}
      </div>
      <div className="legend" style={{ marginTop: 2 }}>칸 진하기 = 강수확률 · 파란 칸 = 비·눈·소나기 예보 (마우스를 올리면 시각별 값)</div>
    </div>
  );
}

const METHOD = (scope: string) => <div className="muted" style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.6 }}>
  <b>권장값을 찾는 방법</b> — 제약 격자 전수 탐색: 지금 냉각 구조는 그대로 두고 CRAH 풍량 배율 13단계(30~150%) × 냉수 ΔT 5단계(5~10K)
  = 65개 조합을 모두 계산합니다. 조합마다 급기온도는 '흡입 상한 − 여유 1℃'에서 역산하고(0.5℃ 단위), 냉수 공급 = 급기 − 코일 접근온도로 정합니다.
  {scope}의 피크와 '운전 현황' 하루 피크 모두에서 흡입 여유 1℃ 이상, 코일 환수 조건을 만족하는 조합 가운데 {scope} 냉방 전력량
  (칠러 + CRAH 팬 + 펌프, 연성 모델)이 가장 작은 것을 고르므로, 적용 후 주의·경고가 뜨지 않습니다.
</div>;

function DayCharts({ o }: { o: OutlookDay }) {
  return (
    <div className="chart-grid">
      <div>
        <h3 className="sub-h">냉방 설비 전력 [kW] — {o.current_label} vs 권장</h3>
        <Legend2 a={o.current_label} b="권장 운전점" />
        <div className="chart-box" style={{ height: 200 }}>
          <ResponsiveContainer>
            <LineChart data={o.hourly} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              {hourAxis}
              <YAxis {...axis} width={50} />
              <Tooltip content={<PowerTip o={o} />} />
              <Line dataKey="current_kw" stroke="var(--series-2)" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line dataKey="recommended_kw" stroke="var(--series-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {modeBar(o.hourly.map((h) => h.current_mode), o.current_label)}
        {modeBar(o.hourly.map((h) => h.mode), "권장 운전점")}
        {o.power_note && <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6, margin: "6px 0 0" }}>
          <b>곡선 해석</b> — {o.power_note}</p>}
      </div>
      <div>
        <h3 className="sub-h">예보 기온 [℃]</h3>
        <div className="legend"><span><span className="sw" style={{ background: "var(--series-1)" }} />건구</span>
          <span><span className="sw" style={{ background: "var(--series-3)" }} />습구</span></div>
        <div className="chart-box" style={{ height: 200 }}>
          <ResponsiveContainer>
            <LineChart data={o.hourly} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              {hourAxis}
              <YAxis {...axis} width={40} domain={["auto", "auto"]} />
              <Tooltip formatter={(v, n) => [`${fmt(Number(v), 1)} ℃`, n === "dry_bulb_c" ? "건구" : "습구"]}
                labelFormatter={(h) => `${hh(Number(h))} · 습도 ${fmt(o.hourly[Number(h)]?.rh_pct, 0)}%`} />
              <Line dataKey="dry_bulb_c" stroke="var(--series-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line dataKey="wet_bulb_c" stroke="var(--series-3)" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {o.precip && <PrecipStrip p={o.precip} />}
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6, margin: "6px 0 0" }}>{weatherNote(o.hourly)}</p>
      </div>
    </div>
  );
}

function MonthCharts({ o }: { o: OutlookMonth }) {
  const data = o.daily.map((d) => ({ ...d, day: Number(d.date.slice(8)), cur: d.current_kwh / 1000, rec: d.recommended_kwh / 1000,
    chiller_h: 24 - d.free_h - d.partial_h }));
  const dayAxis = <XAxis dataKey="day" {...axis} interval={2} tickFormatter={(d: number) => `${d}일`} />;
  return (
    <div className="chart-grid">
      <div>
        <h3 className="sub-h">일별 냉방 설비 전력량 [MWh] — {o.current_label} vs 권장</h3>
        <Legend2 a={o.current_label} b="권장 운전점" />
        <div className="chart-box" style={{ height: 200 }}>
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              {dayAxis}
              <YAxis {...axis} width={50} domain={["auto", "auto"]} />
              <Tooltip formatter={(v, n) => [`${fmt(Number(v), 2)} MWh`, n === "cur" ? o.current_label : "권장"]}
                labelFormatter={(d) => `${o.month}/${d} (기상: ${data[Number(d) - 1]?.obs_date} 관측)`} />
              <Line dataKey="cur" stroke="var(--series-2)" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line dataKey="rec" stroke="var(--series-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <h3 className="sub-h" style={{ marginTop: 10 }}>권장 운전점의 일별 운전 모드 [시간]</h3>
        <div className="chart-box" style={{ height: 150 }}>
          <ResponsiveContainer>
            <BarChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              {dayAxis}
              <YAxis {...axis} width={30} domain={[0, 24]} ticks={[0, 12, 24]} />
              <Tooltip formatter={(v, n) => [`${v} 시간`, MODE_LABEL[(n === "free_h" ? "free" : n === "partial_h" ? "partial" : "chiller") as PlantMode]]}
                labelFormatter={(d) => `${o.month}/${d}`} />
              <Bar dataKey="free_h" stackId="m" fill={MODE_COLOR.free} isAnimationActive={false} />
              <Bar dataKey="partial_h" stackId="m" fill={MODE_COLOR.partial} isAnimationActive={false} />
              <Bar dataKey="chiller_h" stackId="m" fill={MODE_COLOR.chiller} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        {o.power_note && <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6, margin: "6px 0 0" }}>
          <b>해석</b> — {o.power_note}</p>}
      </div>
      <div>
        <h3 className="sub-h">일별 기온 [℃] — {o.observed_year}년 {o.month}월 관측</h3>
        <div className="legend"><span><span className="sw" style={{ background: "var(--series-1)" }} />최고 건구</span>
          <span><span className="sw" style={{ background: "var(--series-4)" }} />최저 건구</span>
          <span><span className="sw" style={{ background: "var(--series-3)" }} />최고 습구</span></div>
        <div className="chart-box" style={{ height: 200 }}>
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              {dayAxis}
              <YAxis {...axis} width={40} domain={["auto", "auto"]} />
              <Tooltip formatter={(v, n) => [`${fmt(Number(v), 1)} ℃`,
                n === "max_dry_bulb_c" ? "최고 건구" : n === "min_dry_bulb_c" ? "최저 건구" : "최고 습구"]}
                labelFormatter={(d) => `${o.month}/${d} (${data[Number(d) - 1]?.obs_date} 관측)`} />
              <Line dataKey="max_dry_bulb_c" stroke="var(--series-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line dataKey="min_dry_bulb_c" stroke="var(--series-4)" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line dataKey="max_wet_bulb_c" stroke="var(--series-3)" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6, margin: "6px 0 0" }}>
          다음 달은 기상 예보가 없어 지역 관측소의 {o.observed_year}년 {o.month}월 시간별 관측(기상청 ASOS)을 같은 날짜에 대응시켜
          한 달 {o.daily.length}일을 모두 계산했습니다. 습구온도가 높은 날일수록 프리쿨링 시간이 줄고 칠러 전력이 늘어납니다.</p>
        <div className="kpis" style={{ gridTemplateColumns: "repeat(2, 1fr)", marginTop: 8 }}>
          <div className="kpi"><div className="label">한 달 전력량 ({o.current_label})</div>
            <div className="value">{fmt(o.current.monthly_kwh / 1000, 1)}<span className="unit">MWh</span></div></div>
          {o.recommended && <div className="kpi"><div className="label">권장 운전점</div>
            <div className="value">{fmt(o.recommended.monthly_kwh / 1000, 1)}<span className="unit">MWh</span></div>
            <div className="detail">{o.saving_kwh >= 0 ? "절감" : "증가"} {fmt(Math.abs(o.saving_kwh) / 1000, 1)} MWh</div></div>}
        </div>
      </div>
    </div>
  );
}

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void }

/** 운전 브리핑: 내일(기상청 단기예보) / 다음 달(지역 관측소의 지난 연도 같은 달 관측) → 부하·프리쿨링·권장 운전점·주의사항 */
export function OutlookPanel({ scenario, busy, onApply }: Props) {
  const [period, setPeriod] = usePersist<Period>("outlook:period", "day");
  const [cap, setCap] = usePersist("outlook:cap", "");
  const [res, setRes] = usePersist<Partial<Record<Period, Outlook>>>("outlook:res2", {});
  const [err, setErr] = useState<string | null>(null);
  const [work, setWork] = useState(false);
  const capV = cap.trim() ? Number(cap) : null;
  const o = res[period];

  const run = async () => {
    setErr(null); setWork(true);
    try {
      const r = await api.outlook(scenario, period, capV && capV > 0 ? capV : null);
      setRes({ ...res, [period]: r });
    } catch (e) { setErr((e as Error).message); }
    setWork(false);
  };
  useEffect(() => { run(); }, [scenario, period]);   // eslint-disable-line react-hooks/exhaustive-deps

  const view = async () => {
    if (!o) return;
    const sc: Scenario = JSON.parse(JSON.stringify(scenario));
    sc.weather = o.weather;
    sc.site.date = o.date;
    sc.it_load.utilization = o.utilization;
    sc._note = o.period === "day"
      ? `${o.date} 운전 예측 — 기상: ${o.weather_note ?? o.title.split(" · ").pop()} (실제와 다를 수 있음)`
      : `${o.year}년 ${o.month}월 브리핑의 가장 습한 날 ${o.date} — 기상: ${o.observed_year}년 같은 날 기상청 ASOS 관측 (예보 아님)`;
    onApply(sc);
  };
  const applyRec = async () => {
    if (!o?.recommended) return;
    const { supply_temp_c, chw_supply_c, chw_return_c, airflow_ratio } = o.recommended;
    try { onApply(await api.outlookApply(scenario, { supply_temp_c, chw_supply_c, chw_return_c, airflow_ratio }, Number(o.date.slice(5, 7)))); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <Card title={period === "day" ? "내일 운전 브리핑" : "다음 달 운전 브리핑"}
      hint={period === "day" ? "기상청 단기예보 (사이트 위치의 5 km 격자)" : "지역 관측소의 지난 연도 같은 달 관측으로 한 달 전체 계산"}
      right={<div className="seg">{([["day", "내일"], ["month", "다음 달"]] as const).map(([k, l]) =>
        <button key={k} className={period === k ? "on" : ""} onClick={() => setPeriod(k)}>{l}</button>)}</div>}>
      {err && <div className="banner crit">{err}</div>}
      {work && !o && <div className="muted">{period === "day" ? "예보를 받아 계산 중…" : "한 달을 계산 중… (처음 받는 관측 연도는 30초~1분)"}</div>}
      {o && <>
        {o.weather_note && <div className={`banner ${o.period === "month" ? "info" : "warn"}`}>{o.period === "month" ? "ⓘ" : "⚠"} {o.weather_note}</div>}
        <div className="brief">
          <div className="brief-title">{o.title}</div>
          <ul>{o.bullets.map((b) => <li key={b}>{b}</li>)}</ul>
          {o.alerts.map((a) => <div key={a.text} className={`alert ${a.level}`}>{a.level === "crit" ? "✕" : a.level === "warn" ? "⚠" : "ⓘ"} {a.text}</div>)}
          <IssueList issues={o.issues} />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={busy} onClick={view}>
              {o.period === "day" ? "이 날을 '운전 현황'에서 보기" : `가장 습한 날(${o.month}/${Number(o.date.slice(8))})을 '운전 현황'에서 보기`}</button>
            {o.recommended && <button className="btn primary" disabled={busy} onClick={applyRec}>권장 운전점 적용</button>}
            {work && <span className="muted">다시 계산 중…</span>}
          </div>
          {o.recommended && METHOD(o.period === "day" ? "내일 하루" : "다음 달 한 달")}
        </div>
        {o.period === "day" ? <DayCharts o={o} /> : <MonthCharts o={o} />}
        {o.period === "day" && <div className="legend" style={{ marginTop: 4 }}>운전 모드 띠: {(["free", "partial", "chiller"] as const).map((m) =>
          <span key={m}><span className="sw" style={{ background: MODE_COLOR[m] }} />{MODE_LABEL[m]}</span>)}</div>}
        {o.period === "month" && <div className="legend" style={{ marginTop: 4 }}>운전 모드: {(["free", "partial", "chiller"] as const).map((m) =>
          <span key={m}><span className="sw" style={{ background: MODE_COLOR[m] }} />{MODE_LABEL[m]}</span>)}</div>}
        <div className="row" style={{ marginTop: 8 }}>
          <div className="field" style={{ width: 220 }}><label>냉방 설비 용량 [kW] (선택, 용량 경고용)</label>
            <input type="number" value={cap} onChange={(e) => setCap(e.target.value)} onBlur={run} placeholder="비우면 확인 안 함" /></div>
        </div>
        <ul className="notes">{o.notes.map((n) => <li key={n}>{n}</li>)}
          <li>권장 운전점: 현재 구조 그대로, 랙 흡입온도 여유 1℃ 이상·코일 조건을 지키는 최소 전력 운전점 (격자 전수 탐색, 연성 모델).
            월별 스케줄이 있으면 적용 시 그 달 스케줄 값도 함께 바뀝니다</li></ul>
      </>}
    </Card>
  );
}
