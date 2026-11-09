import { useEffect, useState } from "react";
import type { RealCompare, RealCompareRequest, RealInspect, RealMapping, RealPeriods, RealPresetDetail, RealPresetInfo } from "../../types/api";
import { api, fileB64 } from "../../api/client";
import { fmt } from "../../lib/format";
import { Fold, usePersist } from "../../lib/ui";
import { SIM_KEYS, SIM_KEY_LABEL } from "./common";

type Role = "" | "it" | "item" | "lighting" | "pue" | "pue_term" | "dry_bulb" | "rh";
const ROLE_LABEL: Record<Role, string> = {
  "": "사용 안 함", it: "IT 전력", item: "비교 항목", lighting: "조명·콘센트", pue: "원본 PUE (확인용)",
  pue_term: "PUE 분자에만 포함", dry_bulb: "외기 건구", rh: "외기 상대습도",
};
const PUMP = ["chw_pump", "cw_pump", "liquid_pump"];

function roleOf(m: RealMapping, col: string): Role {
  if (m.it === col) return "it";
  if (col in m.items) return "item";
  if (m.lighting === col) return "lighting";
  if (m.pue_column === col) return "pue";
  if (m.dry_bulb === col) return "dry_bulb";
  if (m.rh === col) return "rh";
  return m.pue_terms.includes(col) ? "pue_term" : "";
}

/** 열 역할 변경 → 매핑 (한 역할에 열 하나: IT·조명·PUE·외기) */
function setRole(m: RealMapping, col: string, role: Role): RealMapping {
  const n: RealMapping = { ...m, items: { ...m.items }, labels: { ...m.labels }, pue_terms: m.pue_terms.filter((c) => c !== col) };
  delete n.items[col];
  for (const k of ["lighting", "pue_column", "dry_bulb", "rh"] as const) if (n[k] === col) n[k] = null;
  if (n.it === col) n.it = "";
  if (role === "it") n.it = col;
  if (role === "item") {
    n.items[col] = /pump|펌프/i.test(col) ? PUMP : /hvac|fan|팬|공조/i.test(col) ? ["crah_fan"] : ["tower_fan"];
    n.labels[col] = n.labels[col] ?? col;
    n.pue_terms.push(col);
  }
  if (role === "lighting") { n.lighting = col; n.pue_terms.push(col); }
  if (role === "pue") n.pue_column = col;
  if (role === "pue_term") n.pue_terms.push(col);
  if (role === "dry_bulb") n.dry_bulb = col;
  if (role === "rh") n.rh = col;
  return n;
}

interface Props {
  presets: RealPresetInfo[]; busy: boolean; result: RealCompare | null;
  onRun: (req: RealCompareRequest) => void;
}

/** 실측 데이터 선택(프리셋·CSV) → 항목 매핑 → 기간 → 실행 */
export function DatasetPanel({ presets, busy, result, onRun }: Props) {
  const [src, setSrc] = usePersist<"preset" | "upload">("real:src", "preset");
  const [pid, setPid] = usePersist<string>("real:pid", "esif");
  const [detail, setDetail] = usePersist<RealPresetDetail | RealInspect | null>("real:detail", null);
  const [mapping, setMapping] = usePersist<RealMapping | null>("real:mapping", null);
  const [periods, setPeriods] = usePersist<RealPeriods | null>("real:periods", null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (src !== "preset" || !pid || (detail && "id" in detail && detail.id === pid)) return;
    setLoading(true); setErr(null);
    api.realPreset(pid).then((d) => { setDetail(d); setMapping(d.mapping); setPeriods(d.periods); })
      .catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  }, [src, pid]);   // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setLoading(true); setErr(null);
    try {
      const d = await api.realInspect(f.name, await fileB64(f));
      const d0 = d.range[0].slice(0, 10), d1 = d.range[1].slice(0, 10);
      setDetail(d); setMapping(d.mapping); setPeriods({ cal: [d0, d1], test: null });
    } catch (e) { setErr((e as Error).message); }
    setLoading(false);
  };

  const preset = presets.find((p) => p.id === pid);
  const isPreset = src === "preset" && detail && "id" in detail;
  const ready = !!(detail && mapping && periods && mapping.it && Object.keys(mapping.items).length);
  const run = () => {
    if (!ready || !detail || !mapping || !periods) return;
    onRun(isPreset ? { preset: pid, mapping, periods } : { preset: null, dataset_id: detail.dataset_id, mapping, periods });
  };
  const setP = (k: "cal" | "test", i: 0 | 1, v: string) => {
    if (!periods) return;
    const cur = (periods[k] ?? periods.cal).slice() as [string, string];
    cur[i] = v;
    setPeriods({ ...periods, [k]: cur });
  };
  const notes = isPreset ? (detail as RealPresetDetail).period_notes : {};
  const wx = isPreset ? (detail as RealPresetDetail).weather : null;

  return (
    <div>
      <div className="row">
        <span className="seg">
          <button className={src === "preset" ? "on" : ""} onClick={() => { setSrc("preset"); setDetail(null); }}>프리셋</button>
          <button className={src === "upload" ? "on" : ""} onClick={() => { setSrc("upload"); setDetail(null); setMapping(null); }}>CSV 올리기</button>
        </span>
        {src === "preset" ? <select value={pid} onChange={(e) => { setPid(e.target.value); setDetail(null); }} aria-label="실측 프리셋">
          {presets.map((p) => <option key={p.id} value={p.id} disabled={!p.available}>{p.label}{p.available ? "" : " (데이터 없음)"}</option>)}
        </select> : <input type="file" accept=".csv,.tsv,.txt,.xlsx" onChange={(e) => upload(e.target.files?.[0])} />}
        {loading && <span className="muted">불러오는 중…</span>}
      </div>
      {src === "preset" && preset && <div className="muted" style={{ marginTop: 6 }}>{preset.description}</div>}
      {src === "upload" && <div className="muted" style={{ marginTop: 6 }}>
        시간 단위 표: 일시(현지 표준시), IT 전력 kW(필수), 계측 항목 열, (선택) 외기 건구·상대습도. 기준 시나리오는 지금 화면의 시나리오입니다.
        외기 열이 없으면 기상 자료가 없어 실행할 수 없습니다.</div>}
      {err && <div className="banner crit">{err}</div>}

      {detail && mapping && periods && <>
        <div className="muted" style={{ marginTop: 8 }}>
          데이터 {detail.range[0]} ~ {detail.range[1]} · {detail.hours.toLocaleString()} h · 일시 열 '{detail.timestamp}'
          {detail.bad_rows ? ` · 일시를 읽지 못한 행 ${detail.bad_rows}개 제외` : ""}
        </div>

        <h3 className="sub-h">기간</h3>
        <div className="row">
          <label>보정 <input type="date" value={periods.cal[0]} onChange={(e) => setP("cal", 0, e.target.value)} /> ~{" "}
            <input type="date" value={periods.cal[1]} onChange={(e) => setP("cal", 1, e.target.value)} /></label>
          {notes.cal && <span className="muted">{notes.cal}</span>}
        </div>
        <div className="row" style={{ marginTop: 4 }}>
          <label><input type="checkbox" checked={!!periods.test} onChange={(e) => setPeriods({ ...periods, test: e.target.checked ? periods.cal : null })} /> 검증</label>
          {periods.test && <>
            <input type="date" value={periods.test[0]} onChange={(e) => setP("test", 0, e.target.value)} /> ~{" "}
            <input type="date" value={periods.test[1]} onChange={(e) => setP("test", 1, e.target.value)} />
            {notes.test && <span className="muted">{notes.test}</span>}
          </>}
        </div>

        <h3 className="sub-h">기상</h3>
        <div className="muted">
          {mapping.dry_bulb && mapping.rh ? `데이터의 외기 열 (${mapping.dry_bulb}, ${mapping.rh}) 사용${isPreset ? " · 일사는 TMY3" : " · 일사 0 (가정)"}`
            : wx ? (wx.site_available ? `현장 외기: ${wx.site_label}` : `현장 외기 파일 없음 → ${wx.epw_label}`) : "외기 열을 지정하세요"}
          {result && <> · 실행 결과: {Object.entries(result.weather.days).map(([k, d]) =>
            `${k} ${Object.entries(d ?? {}).map(([s, n]) => `${s === "site" ? "현장" : s.startsWith("tmy3") ? s.replace("tmy3", "TMY3") : s} ${n}일`).join(", ")}`).join(" / ")}</>}
        </div>

        <Fold title={`항목 매핑 (비교 ${Object.keys(mapping.items).length}개 · PUE 분자 ${mapping.pue_terms.length}개)`} open={!isPreset}>
          <div className="table-scroll" style={{ maxHeight: 340 }}>
            <table className="data">
              <thead><tr><th>실측 열</th><th>결측</th><th>평균</th><th>역할</th><th>시뮬 키 (비교 항목)</th><th>이름</th><th>PUE 항목</th></tr></thead>
              <tbody>{detail.columns.map((c) => {
                const r = roleOf(mapping, c.name);
                const keys = mapping.items[c.name];
                return (
                  <tr key={c.name}>
                    <td>{c.name}</td><td>{fmt(c.missing_pct, 1)}%</td><td>{fmt(c.mean, 2)}</td>
                    <td><select value={r} onChange={(e) => setMapping(setRole(mapping, c.name, e.target.value as Role))}>
                      {(Object.keys(ROLE_LABEL) as Role[]).map((k) => <option key={k} value={k}>{ROLE_LABEL[k]}</option>)}
                    </select></td>
                    <td style={{ textAlign: "left" }}>{keys && SIM_KEYS.map((k) => (
                      <label key={k} style={{ marginRight: 8 }}><input type="checkbox" checked={keys.includes(k)} onChange={(e) => {
                        const next = e.target.checked ? [...keys, k] : keys.filter((x) => x !== k);
                        if (next.length) setMapping({ ...mapping, items: { ...mapping.items, [c.name]: next } });
                      }} />{SIM_KEY_LABEL[k]}</label>))}</td>
                    <td>{keys && <input className="text" style={{ width: 110 }} value={mapping.labels[c.name] ?? ""}
                      onChange={(e) => setMapping({ ...mapping, labels: { ...mapping.labels, [c.name]: e.target.value } })} />}</td>
                    <td><input type="checkbox" aria-label={`${c.name} PUE 항목`} disabled={r === "it" || r === "pue" || r === "dry_bulb" || r === "rh"}
                      checked={r === "it" || mapping.pue_terms.includes(c.name)} onChange={(e) => setMapping({ ...mapping,
                        pue_terms: e.target.checked ? [...mapping.pue_terms, c.name] : mapping.pue_terms.filter((x) => x !== c.name) })} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="row" style={{ marginTop: 6 }}>
            <label>UPS 손실 비율 (실측 PUE 에 더함){" "}
              <input type="number" step="0.01" min={0} max={0.5} style={{ width: 70 }} value={mapping.ups_loss}
                onChange={(e) => setMapping({ ...mapping, ups_loss: Number(e.target.value) || 0 })} /></label>
            {mapping.dry_bulb && <label>외기 온도 단위 <select value={mapping.temp_unit ?? "C"}
              onChange={(e) => setMapping({ ...mapping, temp_unit: e.target.value as "C" | "F" })}><option value="C">℃</option><option value="F">℉</option></select></label>}
          </div>
          <div className="muted">실측 PUE = (IT + 체크한 항목 + UPS 손실) ÷ IT. 시뮬 PUE = (IT + UPS 손실 + 조명 + 냉방 설비 전체) ÷ IT.
            {mapping.definitions && Object.entries(mapping.definitions).map(([k, v]) => <div key={k}>· {k}: {v}</div>)}</div>
        </Fold>

        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={!ready || busy} onClick={run}>{busy ? "계산 중…" : "실측 비교 실행"}</button>
          {!mapping.it && <span className="muted">IT 전력 열을 지정하세요</span>}
          {busy && <span className="muted">1년 × 3회 계산은 수 초 걸립니다 (같은 조건은 캐시)</span>}
        </div>
      </>}
    </div>
  );
}
