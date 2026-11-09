import { useMemo, useState } from "react";
import type { CadBuildResult, CadInspect, CadOptions, CadPreview, CadZoneOverride, Scenario, ZoneKind } from "../../types/api";
import { api, fileB64 } from "../../api/client";
import { fmt } from "../../lib/format";

/** 역할별 미리보기 색 (범주색 토큰) */
const ROLE_COLOR: Record<string, string> = {
  outline: "var(--text-primary)", wall: "var(--text-secondary)", window: "var(--series-1)", rack: "var(--series-2)",
  crah: "var(--series-3)", chiller: "var(--series-4)", tower: "var(--series-5)", zone: "var(--series-6)", ignore: "var(--grid)",
};
const KIND_LABEL: Record<ZoneKind, string> = { data_hall: "전산실", support: "지원실 (전기·기계)", office: "사무·관제" };
const FLOOR_H_DEFAULT = "6";

interface Props { scenario: Scenario; busy: boolean; onApply: (sc: Scenario) => void }

function DrawingPreview({ insp, roles, show }: { insp: CadInspect; roles: Record<string, string>; show: (layer: string) => boolean }) {
  const [x0, y0, x1, y1] = insp.extents_m;
  const pad = Math.max(x1 - x0, y1 - y0) * 0.03 + 1;
  const vb = `${x0 - pad} ${-(y1 + pad)} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`;
  // 무시 레이어를 먼저 그려 역할 있는 선이 위에 보이도록
  const layers = Object.entries(insp.preview.layers).filter(([name]) => show(name)).sort(([a], [b]) =>
    Number((roles[a] ?? "ignore") !== "ignore") - Number((roles[b] ?? "ignore") !== "ignore"));
  return (
    <svg viewBox={vb} className="cad-svg" role="img" aria-label="DXF 도면 미리보기">
      {layers.map(([name, shapes]) => {
        const role = roles[name] ?? "ignore";
        return (
          <g key={name} stroke={ROLE_COLOR[role]} fill="none" strokeWidth={role === "ignore" ? 0.8 : 1.4}
            strokeDasharray={role === "zone" ? "4 3" : undefined}>
            {shapes.map((s, i) => {
              const pts = s.pts.map(([x, y]) => `${x},${-y}`).join(" ");
              return s.closed ? <polygon key={i} points={pts} vectorEffect="non-scaling-stroke" />
                : <polyline key={i} points={pts} vectorEffect="non-scaling-stroke" />;
            })}
          </g>
        );
      })}
    </svg>
  );
}

/** 3D·기류 좌표계(x=동, z=남) 결과 미리보기. 다중 층이면 한 층 + 존 경계 */
function ScenePreview({ p }: { p: CadPreview }) {
  const xs = p.footprint.map((q) => q[0]), zs = p.footprint.map((q) => q[1]);
  const extra = [p.chiller, p.tower].filter(Boolean) as { x: number; z: number; w: number; d: number }[];
  const minX = Math.min(...xs, ...extra.map((e) => e.x - e.w / 2)), maxX = Math.max(...xs, ...extra.map((e) => e.x + e.w / 2));
  const minZ = Math.min(...zs, ...extra.map((e) => e.z - e.d / 2)), maxZ = Math.max(...zs, ...extra.map((e) => e.z + e.d / 2));
  const pad = 3;
  const [w, d] = p.rack_size;
  const fs = Math.max((maxX - minX) / 45, 1);
  return (
    <svg viewBox={`${minX - pad} ${minZ - pad} ${maxX - minX + 2 * pad} ${maxZ - minZ + 2 * pad}`} className="cad-svg"
      role="img" aria-label="가져온 배치 미리보기">
      <polygon points={p.footprint.map((q) => q.join(",")).join(" ")} fill="var(--surface-2)" stroke="var(--text-primary)"
        strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      {p.zones?.map((z) => <polygon key={z.id} points={z.poly.map((q) => q.join(",")).join(" ")} fill="none"
        stroke={ROLE_COLOR.zone} strokeWidth={1.5} strokeDasharray="5 3" vectorEffect="non-scaling-stroke" />)}
      {p.windows.map((s, i) => <polyline key={i} points={s.map((q) => q.join(",")).join(" ")} stroke={ROLE_COLOR.window}
        strokeWidth={3} fill="none" vectorEffect="non-scaling-stroke" />)}
      {p.racks.map((r) => <rect key={r.id} x={r.x - w / 2} y={r.z - d / 2} width={w} height={d}
        fill={r.row % 2 ? "var(--series-2)" : "var(--series-4)"}><title>{`${r.id} · ${r.row + 1}열`}</title></rect>)}
      {p.crah.map(([x, z], i) => <rect key={i} x={x - 1.3} y={z - 0.5} width={2.6} height={1} fill={ROLE_COLOR.crah}>
        <title>{`CRAH #${i + 1}`}</title></rect>)}
      {extra.map((e, i) => <rect key={i} x={e.x - e.w / 2} y={e.z - e.d / 2} width={e.w} height={e.d} fill="none"
        stroke={i === 0 && p.chiller ? ROLE_COLOR.chiller : ROLE_COLOR.tower} strokeWidth={2} vectorEffect="non-scaling-stroke" />)}
      {p.zones?.map((z) => <text key={z.id} x={z.x} y={z.z} fontSize={fs * 1.2} textAnchor="middle" fill="var(--text-primary)"
        paintOrder="stroke" stroke="var(--surface-1)" strokeWidth={fs * 0.3}>{z.name}</text>)}
      <text x={minX} y={minZ - 0.8} fontSize={fs} fill="var(--text-muted)">N ↑ (3D 좌표계)</text>
    </svg>
  );
}

const oText = (m: Record<string, number>) => ["N", "E", "S", "W"].filter((o) => m[o]).map((o) => `${o} ${fmt(m[o], 0)}`).join(" · ") || "–";

export function CadImport({ scenario, busy, onApply }: Props) {
  const [insp, setInsp] = useState<CadInspect | null>(null);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [blockRoles, setBlockRoles] = useState<Record<string, string>>({});
  const [opts, setOpts] = useState({ north_deg: "0", height_m: String(scenario.building?.height_m ?? 10),
    window_height_m: "1.5", unit_scale_m: "", rack_kw: "" });
  const [floorH, setFloorH] = useState<Record<number, string>>({});
  const [tab, setTab] = useState("all");                     // 도면 미리보기·역할 표의 층
  const [resFloor, setResFloor] = useState(0);              // 결과 미리보기 층 (index)
  const [over, setOver] = useState<Record<string, CadZoneOverride>>({});
  const [dirty, setDirty] = useState(false);                // 존 표 수정 후 다시 해석 전
  const [result, setResult] = useState<CadBuildResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [work, setWork] = useState(false);
  const floors = insp?.floors ?? [];
  const multi = floors.length >= 2;

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setErr(null); setResult(null); setInsp(null); setOver({}); setDirty(false); setTab("all"); setResFloor(0);
    if (/\.dwg$/i.test(f.name)) {
      setErr("DWG 는 받지 않습니다. AutoCAD 에서 '다른 이름으로 저장 → DXF' 로 내보낸 파일을 올려 주세요.");
      return;
    }
    setWork(true);
    try {
      const r = await api.cadInspect(f.name, await fileB64(f));
      setInsp(r);
      setRoles(Object.fromEntries(r.layers.map((l) => [l.name, l.suggested_role])));
      // 블록은 이름으로 역할이 추정될 때만 지정, 아니면 레이어 따름. 다중 층이면 층마다 따로 ('블록@F2')
      const br: Record<string, string> = {};
      for (const b of r.blocks) {
        const v = b.suggested_role !== "ignore" ? b.suggested_role : "";
        const fl = Object.keys(b.floors ?? {});
        if ((r.floors?.length ?? 0) >= 2 && fl.length) {
          fl.forEach((l) => { br[`${b.name}@${l}`] = v; });
          if (b.count > fl.reduce((s, l) => s + (b.floors![l] ?? 0), 0)) br[b.name] = v;
        } else br[b.name] = v;
      }
      setBlockRoles(br);
      setFloorH(Object.fromEntries((r.floors ?? []).map((x) => [x.floor, FLOOR_H_DEFAULT])));
    } catch (e) { setErr((e as Error).message); }
    setWork(false);
  };

  const options: CadOptions | null = useMemo(() => {
    const n = Number(opts.north_deg), h = Number(opts.height_m), wh = Number(opts.window_height_m);
    const us = opts.unit_scale_m.trim() ? Number(opts.unit_scale_m) : null;
    const rk = opts.rack_kw.trim() ? Number(opts.rack_kw) : null;
    const fh = Object.fromEntries(Object.entries(floorH).map(([k, v]) => [Number(k), Number(v)]));
    if (![n, h, wh, ...Object.values(fh)].every(Number.isFinite) || (us !== null && !(us > 0)) || (rk !== null && !(rk > 0))
      || Object.values(fh).some((v) => !(v > 2))) return null;
    return { north_deg: n, height_m: h, window_height_m: wh, unit_scale_m: us,
      ...(multi ? { floor_heights_m: fh, rack_kw: rk, zone_overrides: Object.keys(over).length ? over : null } : {}) };
  }, [opts, floorH, over, multi]);

  const build = async () => {
    if (!insp || !options) return;
    setErr(null); setWork(true);
    try { setResult(await api.cadBuild(insp.cad_id, roles, blockRoles, options, scenario)); setDirty(false); }
    catch (e) { setErr((e as Error).message); setResult(null); }
    setWork(false);
  };
  const changed = () => { setResult(null); };

  const roleSel = (v: string, on: (v: string) => void, inherit = false) => (
    <select value={v} onChange={(e) => { on(e.target.value); changed(); }}>
      {inherit && <option value="">(레이어 따름)</option>}
      {insp!.roles.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
    </select>
  );
  const ents = (e: Record<string, number>) => Object.entries(e).map(([k, v]) => `${k} ${v}`).join(", ");
  const layerFloor = (name: string) => insp?.layers.find((l) => l.name === name)?.floor ?? null;
  const showLayer = (name: string) => tab === "all" || layerFloor(name) === tab || layerFloor(name) === null;
  // 블록 역할 행: 다중 층이면 (블록, 층)마다
  const blockRows = (insp?.blocks ?? []).flatMap((b) => {
    const fl = Object.entries(b.floors ?? {});
    if (!multi || !fl.length) return [{ key: b.name, b, floor: null as string | null, count: b.count }];
    const rows = fl.map(([l, n]) => ({ key: `${b.name}@${l}`, b, floor: l as string | null, count: n }));
    const common = b.count - fl.reduce((s, [, n]) => s + n, 0);
    return common > 0 ? [...rows, { key: b.name, b, floor: null, count: common }] : rows;
  }).filter((r) => tab === "all" || r.floor === tab || r.floor === null);

  const rep = result?.report;
  const zones = rep?.zones;
  const pv = result?.preview.floors?.[resFloor] ?? result?.preview;
  const setZ = (id: string, patch: CadZoneOverride) => { setOver({ ...over, [id]: { ...over[id], ...patch } }); setDirty(true); };

  return (
    <div className="card">
      <div className="card-head">
        <h2>① CAD 도면 (DXF)</h2>
        <span className="hint">외곽선·외벽 → 방위별 외피 면적, 랙·CRAH·설비 블록 → 전산실 배치 · 레이어 이름에 층 접두어(F2-, 2F-, L2-, 2층)가
          있으면 층·존별 다중 존 · DWG 는 DXF 로 저장 후 업로드</span>
      </div>
      <div className="row">
        <label className="btn">DXF 업로드<input type="file" accept=".dxf" hidden onChange={(e) => onFile(e.target.files?.[0])} /></label>
        {insp && <span className="muted">{insp.file_name} · {insp.dxf_version} · {insp.units.basis}
          {insp.audit_errors > 0 && ` · 자동 복구 ${insp.audit_errors}건`}
          {multi && ` · 층 ${floors.length}개 (${floors.map((f) => f.label).join(", ")})`}</span>}
        {work && <span className="muted">처리 중…</span>}
      </div>
      {err && <div className="banner crit">{err}</div>}

      {insp && (
        <>
          {multi && <div className="row" style={{ marginTop: 10 }}>
            <div className="seg" role="tablist" aria-label="층">
              {[["all", "전체"], ...floors.map((f) => [f.label, `${f.floor + 1}층 (${f.label})`])].map(([k, l]) =>
                <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
            </div>
            <span className="muted">층 탭은 그 층 레이어 + 공통 레이어(접두어 없음)만 보여 줍니다. 블록 역할은 그 블록이 놓인 층에만 적용</span>
          </div>}
          <div className="import-grid">
            <div>
              <h3 className="sub-h">레이어 역할 <span className="muted">(이름으로 자동 추정 · 직접 수정 가능)</span></h3>
              <div className="table-scroll" style={{ maxHeight: 300 }}>
                <table className="data">
                  <thead><tr><th>레이어</th>{multi && <th>층</th>}<th>객체</th><th>역할</th></tr></thead>
                  <tbody>
                    {insp.layers.filter((l) => showLayer(l.name)).map((l) => (
                      <tr key={l.name}>
                        <td><span className="sw-inline" style={{ background: ROLE_COLOR[roles[l.name] ?? "ignore"] }} />{l.name}</td>
                        {multi && <td className="muted">{l.floor ?? "공통"}</td>}
                        <td className="muted">{ents(l.entities)}</td>
                        <td>{roleSel(roles[l.name] ?? "ignore", (v) => setRoles({ ...roles, [l.name]: v }))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {blockRows.length > 0 && <>
                <h3 className="sub-h">블록 역할 <span className="muted">(레이어보다 우선{multi ? " · 층별" : ""})</span></h3>
                <div className="table-scroll" style={{ maxHeight: 200 }}>
                  <table className="data">
                    <thead><tr><th>블록</th>{multi && <th>층</th>}<th>개수</th><th>속성</th><th>역할</th></tr></thead>
                    <tbody>
                      {blockRows.map(({ key, b, floor, count }) => (
                        <tr key={key}>
                          <td>{b.name}</td>{multi && <td className="muted">{floor ?? "공통"}</td>}<td>{count}</td>
                          <td className="muted">{b.attribute_tags.join(", ") || "–"}</td>
                          <td>{roleSel(blockRoles[key] ?? "", (v) => setBlockRoles({ ...blockRoles, [key]: v }), true)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>}
            </div>
            <div>
              <h3 className="sub-h">도면 미리보기 <span className="muted">(색 = 역할{insp.preview.truncated ? " · 일부만 표시" : ""})</span></h3>
              <DrawingPreview insp={insp} roles={roles} show={showLayer} />
            </div>
          </div>

          <div className="form-grid" style={{ marginTop: 12 }}>
            <div className="field"><label>진북 각도 [deg] (도면 위쪽 기준 시계방향 +)</label>
              <input type="number" step={1} value={opts.north_deg} onChange={(e) => setOpts({ ...opts, north_deg: e.target.value })} /></div>
            {!multi && <div className="field"><label>층고 [m]</label>
              <input type="number" step={0.5} value={opts.height_m} onChange={(e) => setOpts({ ...opts, height_m: e.target.value })} /></div>}
            {multi && floors.map((f) => (
              <div className="field" key={f.floor}><label>{f.floor + 1}층 층고 [m]</label>
                <input type="number" step={0.5} value={floorH[f.floor] ?? FLOOR_H_DEFAULT}
                  onChange={(e) => { setFloorH({ ...floorH, [f.floor]: e.target.value }); changed(); }} /></div>
            ))}
            <div className="field"><label>창 높이 [m]<span className="tag">가정값</span></label>
              <input type="number" step={0.1} value={opts.window_height_m} onChange={(e) => setOpts({ ...opts, window_height_m: e.target.value })} /></div>
            {multi && <div className="field"><label>랙당 kW (KW 속성 없는 랙)<span className="tag">가정값</span></label>
              <input type="number" step={1} value={opts.rack_kw} placeholder="비우면 기존 밀도"
                onChange={(e) => setOpts({ ...opts, rack_kw: e.target.value })} /></div>}
            <div className="field"><label>도면 1 단위 = ? m (비우면 자동: {insp.units.scale_m})</label>
              <input type="number" step="any" value={opts.unit_scale_m} placeholder="예: 0.001 (mm)"
                onChange={(e) => setOpts({ ...opts, unit_scale_m: e.target.value })} /></div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={work || !options} onClick={build}>도면 해석</button>
            <span className="muted">외피 U값·SHGC 는 현재 시나리오 값을 방위별로 이어받습니다
              {multi && " · 존 경계 안 문자 'id | 종류' 로 존을 읽고, 같은 id 의 기존 존 설정(실내·발열·외기량)은 유지"}</span>
          </div>
        </>
      )}

      {result && rep && pv && (
        <div style={{ marginTop: 14 }}>
          {rep.warnings.length > 0 && <div className="banner warn">{rep.warnings.map((w) => `⚠ ${w}`).join("\n")}</div>}
          <div className="import-grid">
            <div>
              <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
                <div className="kpi"><div className="label">건물 (동서 x 남북 x {zones ? "높이" : "층고"})</div>
                  <div className="value" style={{ fontSize: 18 }}>{fmt(rep.building.ew_m, 1)} x {fmt(rep.building.ns_m, 1)} x {fmt(rep.building.height_m, 1)}<span className="unit">m</span></div></div>
                <div className="kpi"><div className="label">{zones ? "연면적 (존 합)" : "바닥·지붕 면적"}</div><div className="value">{fmt(rep.building.floor_area_m2, 0)}<span className="unit">m²</span></div></div>
                {zones && <div className="kpi"><div className="label">층 · 존</div><div className="value">{rep.counts.floors}<span className="unit">층 · {rep.counts.zones}존</span></div></div>}
                <div className="kpi"><div className="label">랙</div><div className="value">{rep.counts.racks}<span className="unit">개 · {rep.counts.rows}열</span></div></div>
                <div className="kpi"><div className="label">CRAH</div><div className="value">{rep.counts.crah}<span className="unit">대</span></div></div>
                {zones && <div className="kpi"><div className="label">IT 설계부하 (존 합)</div><div className="value">{fmt((rep.it_design_kw ?? 0) / 1000, 1)}<span className="unit">MW</span></div></div>}
              </div>
              <table className="data" style={{ marginTop: 10 }}>
                <thead><tr><th>방위</th><th>벽 m²{zones && " (존 합)"}</th><th>창 m²</th></tr></thead>
                <tbody>{["N", "E", "S", "W"].map((o) => (
                  <tr key={o}><td>{o}</td><td>{fmt(rep.envelope.walls[o] ?? 0, 1)}</td><td>{fmt(rep.envelope.windows[o] ?? 0, 1)}</td></tr>
                ))}</tbody>
              </table>
              <ul className="notes">{rep.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </div>
            <div>
              {result.preview.floors && <div className="seg" style={{ marginBottom: 6 }} role="tablist" aria-label="결과 층">
                {result.preview.floors.map((f, i) => <button key={f.label} className={resFloor === i ? "on" : ""}
                  onClick={() => setResFloor(i)}>{f.floor + 1}층</button>)}
              </div>}
              <h3 className="sub-h">가져온 배치 <span className="muted">(랙 색 = 짝/홀 열, 초록 = CRAH{zones && ", 점선 = 존 경계"})</span></h3>
              <ScenePreview p={pv} />
            </div>
          </div>

          {zones && <>
            <h3 className="sub-h" style={{ marginTop: 12 }}>존 <span className="muted">(이름·종류·IT 는 수정 가능 → '수정 반영' 으로 다시 해석)</span></h3>
            <div className="table-scroll">
              <table className="data cad-zones">
                <thead><tr><th>층</th><th>id</th><th>이름</th><th>종류</th><th>면적 m²</th><th>랙</th><th>CRAH</th><th>IT kW</th>
                  <th>외벽 m²</th><th>창 m²</th><th>지붕 m²</th><th>근거</th></tr></thead>
                <tbody>{zones.map((z) => (
                  <tr key={z.id}>
                    <td>{z.floor + 1}층</td>
                    <td className="mono">{z.id}</td>
                    <td><input value={over[z.id]?.name ?? z.name} maxLength={40}
                      onChange={(e) => setZ(z.id, { name: e.target.value || undefined })} /></td>
                    <td><select value={over[z.id]?.kind ?? z.kind} onChange={(e) => setZ(z.id, { kind: e.target.value as ZoneKind })}>
                      {(Object.keys(KIND_LABEL) as ZoneKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                    </select></td>
                    <td>{fmt(z.area_m2, 0)}</td><td>{z.racks || "–"}</td><td>{z.crah || "–"}</td>
                    <td><input className="num" type="number" min={0} step={100} value={over[z.id]?.it_design_kw ?? z.it_kw}
                      title={z.it_source}
                      onChange={(e) => setZ(z.id, { it_design_kw: e.target.value === "" ? undefined : Number(e.target.value) })} /></td>
                    <td className="muted">{oText(z.walls)}</td><td className="muted">{oText(z.windows)}</td>
                    <td>{z.roof_m2 ? fmt(z.roof_m2, 0) : "–"}</td>
                    <td><details><summary className="muted">{z.inherited ? "기존 존 설정 유지" : "새 존 (가정값)"}</summary>
                      <ul className="notes">{[`IT: ${z.it_source}`, ...z.notes].map((n, i) => <li key={i}>{n}</li>)}</ul></details></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {rep.partitions && rep.partitions.length > 0 && <p className="muted" style={{ marginTop: 6 }}>
              내부 경계: {rep.partitions.map((p) => `${p.a}–${p.b} ${p.kind === "slab" ? "슬래브" : "내벽"} ${fmt(p.area_m2, 0)} m² (U ${p.u_value}, ${p.u_source})`).join(" · ")}
            </p>}
          </>}

          <div className="row" style={{ marginTop: 10 }}>
            {dirty && <button className="btn" disabled={work || !options} onClick={build}>수정 반영 (다시 해석)</button>}
            <button className="btn primary" disabled={busy || dirty} onClick={() => onApply(result.scenario)}>
              {zones ? "다중 존 시나리오로 적용 · 재계산" : "시나리오에 적용 · 재계산"}</button>
            <span className="muted">{zones
              ? "존·외피·연면적·IT 설계부하·건물 치수·설비 위치가 바뀝니다 (기상·IT 시간 모양·운전점·설비 온도는 유지) · 되돌리기 가능"
              : "외피·바닥 면적·건물 치수·랙/CRAH/설비 배치가 바뀝니다 (기상·IT 부하·설비 온도는 유지)"}</span>
          </div>
        </div>
      )}
    </div>
  );
}
