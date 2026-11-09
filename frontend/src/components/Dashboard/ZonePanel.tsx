import type { EvaluateResult, Scenario, SimulationResult, Zone, ZoneKind } from "../../types/api";
import { fmt } from "../../lib/format";
import { Card } from "../../lib/ui";

export const KIND_LABEL: Record<ZoneKind, string> = { data_hall: "전산실", support: "전기·기계실", office: "사무·관제" };
const KIND_COLOR: Record<ZoneKind, string> = { data_hall: "var(--series-1)", support: "var(--series-4)", office: "var(--series-3)" };
const KINDS: ZoneKind[] = ["data_hall", "support", "office"];

interface Props {
  scenario: Scenario; result: SimulationResult; cooling: EvaluateResult | null;
  zone: string | null; onZone: (id: string) => void;
}

/** 다중 존(적층 건물): 단면 입면도 + 존별 결과 표. 전산실을 누르면 3D·냉각 구조 상세가 그 전산실로 바뀜 */
export function ZonePanel({ scenario, result, cooling, zone, onZone }: Props) {
  const zones = scenario.zones ?? [];
  const res = new Map((result.zones ?? []).map((z) => [z.id, z]));
  const hall = new Map((cooling?.zones ?? []).map((z) => [z.id, z]));
  const sel = cooling?.zone ?? zone;
  const limit = (scenario.cooling_layout?.inlet_limit_c ?? 27);

  // 입면도 좌표: x = 동서 위치 (존 중심 오프셋 ± 폭/2), 층은 아래에서 위로
  const W = 720, FH = 62, PAD = 8;
  const left = Math.min(...zones.map((z) => (z.geometry.x_m ?? 0) - z.geometry.ew_m / 2));
  const right = Math.max(...zones.map((z) => (z.geometry.x_m ?? 0) + z.geometry.ew_m / 2));
  const floors = Math.max(...zones.map((z) => z.floor)) + 1;
  const sx = (W - 2 * PAD) / Math.max(right - left, 1);
  const H = floors * FH + 2 * PAD + 16;
  const box = (z: Zone) => ({
    x: PAD + ((z.geometry.x_m ?? 0) - z.geometry.ew_m / 2 - left) * sx,
    y: PAD + (floors - 1 - z.floor) * FH, w: z.geometry.ew_m * sx, h: FH - 4,
  });

  return (
    <Card title="존별 결과 (적층 구조)" hint="존마다 단일 존 계산 후 합산 · 설비·운전점은 건물 공통 · 전산실을 누르면 3D 가 그 전산실로 바뀝니다">
      <div className="legend" style={{ marginBottom: 6 }}>
        {KINDS.filter((k) => zones.some((z) => z.kind === k)).map((k) =>
          <span key={k}><span className="sw" style={{ background: KIND_COLOR[k] }} />{KIND_LABEL[k]}</span>)}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W, display: "block" }} role="img"
        aria-label="건물 단면: 층별 존 배치와 피크 냉방부하">
        {zones.map((z) => {
          const b = box(z), r = res.get(z.id), m = hall.get(z.id);
          const isHall = z.kind === "data_hall";
          const on = isHall && sel === z.id;
          return (
            <g key={z.id} onClick={() => isHall && onZone(z.id)} style={{ cursor: isHall ? "pointer" : "default" }}>
              <title>{`${z.name} (${z.floor + 1}층, ${KIND_LABEL[z.kind]}) · 피크 냉방 ${fmt(r?.peak_cooling_kw ?? 0, 0)} kW`
                + (m ? ` · 최대 흡입 ${fmt(m.max_inlet_c, 1)}℃ (초과 ${m.racks_over_limit}개)` : "")}</title>
              <rect x={b.x + 1} y={b.y} width={Math.max(b.w - 2, 1)} height={b.h} rx={4}
                fill={KIND_COLOR[z.kind]} fillOpacity={on ? 0.32 : 0.16}
                stroke={KIND_COLOR[z.kind]} strokeWidth={on ? 2.5 : 1} />
              <text x={b.x + 8} y={b.y + 19} fontSize={13} fontWeight={600} fill="var(--text-primary)">{z.name}</text>
              <text x={b.x + 8} y={b.y + 37} fontSize={11.5} fill="var(--text-secondary)">
                {z.it_design_kw ? `IT ${fmt(z.it_design_kw / 1000, 0)} MW · ` : ""}피크 {fmt((r?.peak_cooling_kw ?? 0) / 1000, 1)} MW
              </text>
              {m && <text x={b.x + 8} y={b.y + 52} fontSize={11.5}
                fill={m.racks_over_limit ? "var(--critical)" : "var(--text-secondary)"}>
                최대 흡입 {fmt(m.max_inlet_c, 1)}℃{m.racks_over_limit ? ` ⚠ 초과 ${m.racks_over_limit}개` : ""}{on ? " · 3D 표시 중" : ""}
              </text>}
            </g>
          );
        })}
        <line x1={PAD} x2={W - PAD} y1={PAD + floors * FH - 2} y2={PAD + floors * FH - 2} stroke="var(--border)" strokeWidth={2} />
        <text x={PAD} y={H - 4} fontSize={11} fill="var(--text-muted)">서 ← 지반 (1층 바닥 단열 가정) → 동</text>
      </svg>
      <div className="table-scroll" style={{ marginTop: 10 }}>
        <table className="data">
          <thead><tr>
            <th>존</th><th>층</th><th>종류</th><th>설정 ℃</th><th>IT 설계 kW</th><th>일 냉방부하 MWh</th><th>일 냉방 전력 MWh</th>
            <th>피크 냉방 kW</th><th>존간 전도 kW</th><th>최대 흡입 ℃</th><th>초과 랙</th>
          </tr></thead>
          <tbody>
            {zones.map((z) => {
              const r = res.get(z.id), m = hall.get(z.id);
              return (
                <tr key={z.id} className={sel === z.id ? "active" : ""}>
                  <td>{z.kind === "data_hall" ? <button className="link" onClick={() => onZone(z.id)}>{z.name}</button> : z.name}</td>
                  <td>{z.floor + 1}</td><td>{KIND_LABEL[z.kind]}</td>
                  <td>{fmt((z.indoor ?? scenario.indoor).temp_c, 1)}</td><td>{fmt(z.it_design_kw ?? 0, 0)}</td>
                  <td>{fmt((r?.daily_total.cooling_kwh ?? 0) / 1000, 1)}</td><td>{fmt((r?.daily_total.cooling_power_kwh ?? 0) / 1000, 1)}</td>
                  <td>{fmt(r?.peak_cooling_kw ?? 0, 0)}</td><td>{fmt(r?.interzone_kw ?? 0, 1)}</td>
                  <td className={m && m.max_inlet_c > limit ? "bad" : ""}>{m ? fmt(m.max_inlet_c, 1) : "–"}</td>
                  <td>{m ? `${m.racks_over_limit}/${m.rack_count}` : "–"}</td>
                </tr>
              );
            })}
            <tr className="total">
              <td>건물 합계</td><td /><td /><td /><td>{fmt(scenario.it_load.design_kw, 0)}</td>
              <td>{fmt(result.daily_total.cooling_kwh / 1000, 1)}</td><td>{fmt(result.daily_total.cooling_power_kwh / 1000, 1)}</td>
              <td colSpan={4} className="muted">일 PUE {result.daily_total.pue?.toFixed(3) ?? "–"} · 존간 전도 합 0 (주고받는 열 상쇄)</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="muted" style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.6 }}>
        존간 전도 = Σ U·A·(이웃 존 온도 − 이 존 온도), + 는 유입. 랙 흡입·CRAH 는 전산실마다 기류 모델로 해석하고, 제약은 모든 전산실에 걸립니다
        (최적화·브리핑의 권장 운전점도 모든 전산실을 만족). 비 전산 존의 공조 팬은 엔진 비율 계수로 계산합니다.
      </div>
    </Card>
  );
}
