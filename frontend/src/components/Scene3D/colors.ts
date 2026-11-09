import { Color } from "three";

// 순차(단일 색상, 밝음→어두움) 램프. 일사 = 주황 계열, IT 가동률/랙 전력 = 자홍/적색 계열
const SOLAR = ["#f4efe6", "#f8c9a6", "#ef8a4f", "#d4561f", "#8f3207"];
const UTIL = ["#e9eef6", "#9ec5f4", "#e87ba4", "#d03b3b", "#7f1d1d"];
// 온도 램프 (차가움 → 뜨거움). 랙 흡입온도·기류 입자 공용
const TEMP = ["#1d4ed8", "#38bdf8", "#a7f3d0", "#fde047", "#f97316", "#b91c1c"];

function ramp(stops: string[], t: number): Color {
  const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  return new Color(stops[i]).lerp(new Color(stops[i + 1]), x - i);
}

export const SOLAR_MAX = 1000; // W/m2 - 히트맵 상한 (고정 스케일, 시각 간 비교 가능)
export const solarColor = (w_m2: number) => ramp(SOLAR, w_m2 / SOLAR_MAX);
export const UTIL_MIN = 0.5;   // 가동률 색상 스케일 0.5 ~ 1.0
export const utilColor = (u: number) => ramp(UTIL, (u - UTIL_MIN) / (1 - UTIL_MIN));

/** 랙 흡입온도 고정 스케일 [℃] (ASHRAE 권장 18~27 이 가운데 오도록) */
export const INLET_MIN = 15;
export const INLET_MAX = 32;
export const inletColor = (t: number) => ramp(TEMP, (t - INLET_MIN) / (INLET_MAX - INLET_MIN));
/** 기류 입자 온도 스케일 [℃] (배기 공기까지 포함) */
export const AIR_MIN = 15;
export const AIR_MAX = 45;
export const airColor = (t: number, out = new Color()) =>
  out.copy(ramp(TEMP, (t - AIR_MIN) / (AIR_MAX - AIR_MIN)));
/** 랙 전력 [kW] 스케일 0 ~ KW_MAX */
export const KW_MAX = 15;
export const kwColor = (kw: number) => ramp(UTIL, kw / KW_MAX);

export const cssGradient = (which: "solar" | "util" | "temp") =>
  `linear-gradient(90deg, ${(which === "solar" ? SOLAR : which === "util" ? UTIL : TEMP).join(", ")})`;

export const PIPE = {
  chwSupply: "#2f7fe0",
  chwReturn: "#8ec2ff",
  cwSupply: "#f2a33a",
  cwReturn: "#e2483b",
  fwsSupply: "#1f9d55",
  fwsReturn: "#8fdcae",
};
export const PIPE_COLOR: Record<string, string> = {
  chw_supply: PIPE.chwSupply, chw_return: PIPE.chwReturn, cw_supply: PIPE.cwSupply, cw_return: PIPE.cwReturn,
  fws_supply: PIPE.fwsSupply, fws_return: PIPE.fwsReturn,
};
