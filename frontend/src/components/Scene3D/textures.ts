import { CanvasTexture, RepeatWrapping, SRGBColorSpace, type Texture } from "three";

/** 절차적 텍스처 (외부 이미지 파일 없이 캔버스로 생성). 모듈 단위로 한 번만 만든다 */
function make(w: number, h: number, draw: (g: CanvasRenderingContext2D) => void, repeat = false): Texture {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  draw(c.getContext("2d")!);
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  t.anisotropy = 4;
  if (repeat) t.wrapS = t.wrapT = RepeatWrapping;
  return t;
}

const cache = new Map<string, Texture>();
const once = (key: string, f: () => Texture) => {
  if (!cache.has(key)) cache.set(key, f());
  return cache.get(key)!;
};

/** 랙 전면: 42U 서버(1U/2U/4U), 베젤, 상태 LED, 타공 도어 느낌. 흰색 계열이라 instanceColor 로 틴트 가능 */
export const rackFrontTexture = () => once("rack", () => make(128, 512, (g) => {
  g.fillStyle = "#2a2e35";
  g.fillRect(0, 0, 128, 512);
  const u = 512 / 44;
  let y = u;
  let seed = 7;
  const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  while (y < 512 - u * 2) {
    const r = rnd();
    const units = r < 0.55 ? 1 : r < 0.85 ? 2 : r < 0.95 ? 4 : 0;
    if (units === 0) {               // 블랭크 패널
      g.fillStyle = "#3a3f47";
      g.fillRect(8, y, 112, u - 1);
      y += u;
      continue;
    }
    const hgt = units * u - 1.5;
    g.fillStyle = units === 4 ? "#c9ccd1" : "#dfe2e6";
    g.fillRect(8, y, 112, hgt);
    g.fillStyle = "#9aa0a8";
    for (let x = 14; x < 96; x += 5) g.fillRect(x, y + 2, 2.5, hgt - 4);   // 통풍 슬롯
    g.fillStyle = rnd() < 0.9 ? "#3ddc84" : "#ffb020";
    g.fillRect(104, y + hgt / 2 - 1.5, 4, 3);
    g.fillStyle = "#4aa3ff";
    g.fillRect(111, y + hgt / 2 - 1.5, 3, 3);
    y += units * u;
  }
  g.strokeStyle = "#16181c";
  g.lineWidth = 6;
  g.strokeRect(3, 3, 122, 506);
}));

/** 이중마루 타일 (600 mm). repeat 로 바닥 전체에 깔림 */
export const floorTileTexture = () => once("floor", () => make(64, 64, (g) => {
  g.fillStyle = "#d9dcdf";
  g.fillRect(0, 0, 64, 64);
  g.strokeStyle = "#a9adb2";
  g.lineWidth = 2;
  g.strokeRect(1, 1, 62, 62);
}, true));

/** 유공 타일 (콜드아일 급기) */
export const perfTileTexture = () => once("perf", () => make(64, 64, (g) => {
  g.fillStyle = "#c4d4e4";
  g.fillRect(0, 0, 64, 64);
  g.fillStyle = "#5d7086";
  for (let x = 6; x < 64; x += 6) for (let y = 6; y < 64; y += 6) g.fillRect(x - 1.2, y - 1.2, 2.4, 2.4);
  g.strokeStyle = "#8a99aa";
  g.lineWidth = 2;
  g.strokeRect(1, 1, 62, 62);
}, true));

/** 냉각탑 루버 (가로 슬랫) */
export const louverTexture = () => once("louver", () => make(64, 64, (g) => {
  g.fillStyle = "#8d98a4";
  g.fillRect(0, 0, 64, 64);
  for (let y = 0; y < 64; y += 8) {
    g.fillStyle = "#5e6873";
    g.fillRect(0, y, 64, 3);
  }
}, true));

/** CRAH 전면 그릴 */
export const grilleTexture = () => once("grille", () => make(64, 64, (g) => {
  g.fillStyle = "#e4e8ec";
  g.fillRect(0, 0, 64, 64);
  g.fillStyle = "#9aa3ad";
  for (let y = 4; y < 60; y += 4) g.fillRect(4, y, 56, 1.6);
  g.fillStyle = "#2f6fd6";
  g.fillRect(44, 4, 14, 6);
}, true));

/** 콘크리트 외벽 (약한 패널 줄눈) */
export const concreteTexture = () => once("concrete", () => make(128, 128, (g) => {
  g.fillStyle = "#cfccc5";
  g.fillRect(0, 0, 128, 128);
  let seed = 3;
  const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  for (let i = 0; i < 900; i++) {
    g.fillStyle = `rgba(90,85,78,${rnd() * 0.08})`;
    g.fillRect(rnd() * 128, rnd() * 128, 2, 2);
  }
  g.strokeStyle = "#b3afa6";
  g.lineWidth = 1.5;
  g.strokeRect(0.5, 0.5, 127, 127);
}, true));
