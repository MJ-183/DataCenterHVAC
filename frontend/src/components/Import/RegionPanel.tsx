import { useContext, useEffect, useState } from "react";
import type { Scenario, Site } from "../../types/api";
import { type AsosStation, type RegionPick, api } from "../../api/client";
import { GoTo } from "../../lib/issues";
import { Card, usePersist } from "../../lib/ui";

let STATIONS: AsosStation[] | null = null;   // 활용가이드 지점코드 표 + 관측지점정보 좌표 (한 번만 받음)

export function useStations(): AsosStation[] {
  const [list, setList] = useState<AsosStation[]>(STATIONS ?? []);
  useEffect(() => {
    if (!STATIONS) api.annualStations().then((r) => { STATIONS = r.stations; setList(r.stations); }).catch(() => undefined);
  }, []);
  return list;
}

const LAST_YEAR = new Date().getFullYear() - 1;
const SAVED = "region:last";

/** 마지막으로 적용한 지역을 브라우저에 기억 → 새로 열어도 예시(서울) 대신 그 지역으로 시작 */
export async function restoreRegion(sc: Scenario): Promise<Scenario> {
  let pick: RegionPick | null = null;
  try { pick = JSON.parse(localStorage.getItem(SAVED) ?? "null"); } catch { return sc; }
  if (!pick) return sc;
  try { return await api.region(sc, pick); } catch { /* 키 없음·오프라인 → 위치만 */ }
  try { return await api.region(sc, { ...pick, design_day: "keep" }); } catch { return sc; }
}
const stnName = (list: AsosStation[], id: number) => { const s = list.find((x) => x.id === id); return s ? `${s.name}(${id})` : `${id}`; };

/** 시나리오 site 의 지역 조건 한 줄 (연간 예측·최적화·투자 화면 표시용) */
export function regionText(site: Site, list: AsosStation[]): string {
  const stn = site.asos_station_id ?? 108;
  const solar = site.asos_solar === "cloud" ? "전운량 추정"
    : site.asos_solar_station_id ? `관측 없으면 ${stnName(list, site.asos_solar_station_id)} 빌림` : "관측 (없으면 전운량 추정)";
  return `기상청 ASOS ${stnName(list, stn)} · ${site.weather_year ?? LAST_YEAR}년 · 일사 ${solar}`;
}

/** 분석 화면의 기상 조건 (읽기 전용) + 데이터 입력의 지역 선택으로 이동 */
export function RegionNote({ scenario }: { scenario: Scenario }) {
  const list = useStations();
  const goTo = useContext(GoTo);
  return (
    <div className="field" style={{ gridColumn: "1 / -1" }}><label>기상 (지역 조건)</label>
      <div className="row">
        <span>{regionText(scenario.site, list)}</span>
        <button className="btn small" onClick={() => goTo({ target: "data.region", label: "", value: null, detail: "", effect: null, solves: null })}>
          데이터 입력에서 변경</button>
      </div></div>
  );
}

const DAY_OPT: [RegionPick["design_day"], string][] = [
  ["hottest_wet_bulb", "그 해 최고 습구온도일 (냉각탑·칠러 설계)"], ["hottest_dry_bulb", "그 해 최고 건구온도일"],
  ["typical_wet_bulb", "습구 중앙값 대표일"], ["keep", "기상은 그대로 두기 (위치만 변경)"],
];

interface Props { scenario: Scenario; busy: boolean; keyOk: boolean; onApply: (sc: Scenario) => void }

/** 지역 선택: 기상청 ASOS 관측소 → 사이트 위치·연간 기상·브리핑 예보 위치·운전 현황 하루 기상을 한 번에 맞춤 */
export function RegionPanel({ scenario, busy, keyOk, onApply }: Props) {
  const list = useStations();
  const site = scenario.site;
  const [stn, setStn] = useState(site.asos_station_id ?? 108);
  const [solar, setSolar] = useState(site.asos_solar === "cloud" ? "cloud" : site.asos_solar_station_id ? `stn:${site.asos_solar_station_id}` : "auto");
  const [year, setYear] = useState(site.weather_year ?? LAST_YEAR);
  const [day, setDay] = usePersist<RegionPick["design_day"]>("region:day", "hottest_wet_bulb");
  const [work, setWork] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {   // 시나리오를 불러오거나 되돌리면 그 값으로
    setStn(site.asos_station_id ?? 108);
    setSolar(site.asos_solar === "cloud" ? "cloud" : site.asos_solar_station_id ? `stn:${site.asos_solar_station_id}` : "auto");
    setYear(site.weather_year ?? LAST_YEAR);
  }, [site.asos_station_id, site.asos_solar_station_id, site.asos_solar, site.weather_year]);

  const s = list.find((x) => x.id === stn);
  const needKey = day !== "keep";
  const run = async () => {
    setErr(null); setWork(true);
    try {
      const pick: RegionPick = {
        station_id: stn, year, design_day: day, solar: solar === "cloud" ? "cloud" : "auto",
        solar_station_id: solar.startsWith("stn:") ? Number(solar.slice(4)) : null,
      };
      onApply(await api.region(scenario, pick));
      try { localStorage.setItem(SAVED, JSON.stringify(pick)); } catch { /* 저장 불가여도 이번 화면에는 적용됨 */ }
    } catch (e) { setErr((e as Error).message); }
    setWork(false);
  };

  return (
    <Card title="지역 · 기상 조건" hint="여기서 고른 관측소·연도를 운전 현황·AI 요약, 연간 예측, 브리핑(단기예보 격자), 최적화·투자 검토가 모두 따릅니다. 다음에 열 때도 이 지역으로 시작">
      <div id="set-data-region" className="form-grid">
        <div className="field"><label>지역 (기상청 ASOS 관측소)</label>
          <select value={stn} onChange={(e) => setStn(Number(e.target.value))}>
            {list.length ? list.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.id}) · {x.office}</option>)
              : <option value={stn}>{stn}</option>}
          </select></div>
        <div className="field"><label>관측 연도 (연간 분석)</label>
          <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {Array.from({ length: 10 }, (_, i) => LAST_YEAR - i).map((y) => <option key={y} value={y}>{y}년</option>)}
          </select></div>
        <div className="field"><label>일사</label>
          <select value={solar} onChange={(e) => setSolar(e.target.value)}>
            <option value="auto">이 관측소 일사 관측 (없으면 전운량으로 추정)</option>
            {list.filter((x) => x.id !== stn).map((x) =>
              <option key={x.id} value={`stn:${x.id}`}>관측 없으면 {x.name}({x.id}) 일사 빌려 쓰기</option>)}
            <option value="cloud">전운량으로 추정만</option>
          </select></div>
        <div className="field"><label>운전 현황 하루 기상</label>
          <select value={day} onChange={(e) => setDay(e.target.value as RegionPick["design_day"])}>
            {DAY_OPT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={work || busy || (needKey && !keyOk)} onClick={run}>
          {work ? "기상청 자료 받는 중… (처음 받는 관측소·연도는 30초~1분)" : "이 지역으로 적용"}</button>
        {needKey && !keyOk && <span className="muted">위의 기상청 API 인증키를 먼저 입력하세요</span>}
      </div>
      <div className="muted" style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.6 }}>
        {s && <>사이트 위치 = 관측소 좌표 {s.latitude}, {s.longitude} (해발 {s.altitude_m} m, 기상자료개방포털 관측지점정보) — 태양 위치와 브리핑 예보 위치에 쓰입니다. </>}
        현재 적용: {site.asos_station_id ? <b>{regionText(site, list)}</b> : <>지역 미선택 (예시 데이터: 위치 {site.latitude}, {site.longitude})</>}
      </div>
      {err && <div className="banner crit">{err}</div>}
    </Card>
  );
}
