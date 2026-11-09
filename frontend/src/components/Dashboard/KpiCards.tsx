import type { HourResult } from "../../types/api";
import { fmt } from "../../lib/format";
import { ARCH_LABEL, MODE_LABEL } from "../../lib/fields";

function Kpi({ label, value, unit, digits = 1, detail }: {
  label: string; value: number; unit: string; digits?: number; detail?: string;
}) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{fmt(value, digits)}<span className="unit">{unit}</span></div>
      {detail && <div className="detail">{detail}</div>}
    </div>
  );
}

export function KpiCards({ h, architecture }: { h: HourResult; architecture: string }) {
  const L = h.load_kw, M = h.makeup_water_m3_h, P = h.plant;
  // 전체 냉방 열량 중 프리쿨링(냉각탑 직접)으로 처리한 비율 = Σ(루프 부하 x 루프 프리쿨링 비율) / Σ 루프 부하
  const loops = [P.air_loop, ...(P.liquid_loop ? [P.liquid_loop] : [])];
  const heat = loops.reduce((s, l) => s + l.load_kw, 0);
  const freeShare = heat > 0 ? loops.reduce((s, l) => s + l.load_kw * l.free_fraction, 0) / heat : 0;
  return (
    <div className="kpis">
      <Kpi label="PUE (현재 시각)" value={P.pue ?? NaN} unit="" digits={3}
        detail={`${ARCH_LABEL[architecture]} · 설비 ${fmt(P.power_kw.facility, 0)} kW`} />
      <Kpi label={`냉각 모드: ${MODE_LABEL[P.mode]}`} value={freeShare * 100} unit="% 열량 프리쿨링" digits={0}
        detail={`공기 루프 ${fmt(P.air_loop.free_fraction * 100, 0)}%`
          + (P.liquid_loop ? ` · 액체 루프 ${fmt(P.liquid_loop.free_fraction * 100, 0)}%` : "")
          + ` · 냉각탑 출구 ${fmt(P.tower_water_c, 1)}℃ · WUE ${fmt(P.wue_l_per_kwh, 2)} L/kWh`} />
      <Kpi label="총 냉방부하" value={L.total_cooling} unit="kW" detail={`IT ${fmt(L.it)} kW 포함`} />
      <Kpi label="칠러 전력" value={h.chiller.power_kw} unit="kW" detail={`COP ${fmt(h.chiller.cop, 2)}`} />
      <Kpi label="냉수 유량" value={h.chilled_water.flow_m3_h} unit="m³/h"
        detail={`${fmt(h.chilled_water.supply_c)} → ${fmt(h.chilled_water.return_c)} ℃`} />
      <Kpi label="냉각수 유량" value={h.condenser_water.flow_m3_h} unit="m³/h"
        detail={`방열 ${fmt(h.condenser_water.heat_rejection_kw)} kW · ${fmt(h.condenser_water.supply_c)} → ${fmt(h.condenser_water.return_c)} ℃`} />
      <Kpi label="보급수" value={M.total} unit="m³/h" digits={2}
        detail={`증발 ${fmt(M.evaporation, 2)} · 블로다운 ${fmt(M.blowdown, 2)} · 비산 ${fmt(M.drift, 3)}`} />
      <Kpi label="외기 건구 / 습구" value={h.weather.dry_bulb_c} unit="℃"
        detail={`습구 ${fmt(h.weather.wet_bulb_c, 2)} ℃ · RH ${fmt(h.weather.rh_pct, 0)}%`} />
      <Kpi label="수평면 일사량" value={h.weather.ghi_w_m2} unit="W/m²" digits={0}
        detail={`태양 고도 ${fmt(h.sun.altitude_deg)}° · 방위 ${fmt(h.sun.azimuth_deg)}°`} />
      <Kpi label="외피 부하 (관류+일사)" unit="kW"
        value={L.wall_conduction + L.roof_conduction + L.window_conduction + L.window_solar}
        detail={`외기 도입 ${fmt(L.ventilation)} kW`} />
    </div>
  );
}
