// Built-in spectrogram palettes and call-band presets. The data is
// src/shared/spectrogram_palettes.json, the same file the Python renderers
// (src/shared/spectrogram_palette.py) read, so the swatches here always
// match what the monitor and post-processing draw.
import doc from "@saviour-shared/spectrogram_palettes.json";

export const PALETTES = doc.palettes;            // [{ name, label, stops }]
export const DEFAULT_PALETTE = doc.default;
export const BAND_PRESETS = doc.band_presets;    // [{ id, label, lo_hz, hi_hz, palette }]
export const MAX_BANDS = 4;                      // spectrogram_palette.MAX_BANDS

const BY_NAME = Object.fromEntries(PALETTES.map((p) => [p.name, p]));
// Old palette names (e.g. "alarm_blue") still resolve to the colour they drew.
const ALIASES = doc.aliases ?? {};

export function paletteByName(name) {
  const key = String(name ?? "").toLowerCase();
  return BY_NAME[key] ?? BY_NAME[ALIASES[key]] ?? BY_NAME[DEFAULT_PALETTE];
}

// CSS gradient for a stop list, left (quiet) to right (loud).
export function gradientCss(stops) {
  return `linear-gradient(to right, ${stops
    .map(([pos, colour]) => `${colour} ${(pos * 100).toFixed(1)}%`)
    .join(", ")})`;
}

// Problems with a band list, as human-readable strings; mirrors
// spectrogram_palette.parse_bands so the user sees why the monitor would
// ignore their bands before saving.
export function bandProblems(bands) {
  const enabled = (bands ?? []).filter((b) => b.enabled !== false);
  const problems = [];
  if (enabled.length > MAX_BANDS) problems.push(`At most ${MAX_BANDS} bands can be on`);
  for (const b of enabled) {
    if (!(Number(b.lo_hz) >= 0 && Number(b.lo_hz) < Number(b.hi_hz))) {
      problems.push(`${b.label || "A band"}: low must be below high`);
    }
  }
  const sorted = [...enabled].sort((a, b) => a.lo_hz - b.lo_hz);
  for (let i = 1; i < sorted.length; i++) {
    if (Number(sorted[i].lo_hz) < Number(sorted[i - 1].hi_hz)) {
      problems.push(
        `${sorted[i - 1].label || "Band"} and ${sorted[i].label || "band"} overlap`,
      );
    }
  }
  return problems;
}

// 256-entry RGB lookup table (Uint8ClampedArray, r,g,b per entry) for a stop
// list -- the same linear interpolation as spectrogram_palette.lut().
const LUT_CACHE = new Map();
export function lutRgb(stops) {
  const key = JSON.stringify(stops);
  if (LUT_CACHE.has(key)) return LUT_CACHE.get(key);
  const pts = stops.map(([pos, hex]) => [
    pos * 255,
    parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16),
  ]);
  const lut = new Uint8ClampedArray(256 * 3);
  let j = 0;
  for (let i = 0; i < 256; i++) {
    while (j < pts.length - 2 && i > pts[j + 1][0]) j++;
    const [x0, ...c0] = pts[j];
    const [x1, ...c1] = pts[j + 1];
    const t = x1 > x0 ? Math.min(1, Math.max(0, (i - x0) / (x1 - x0))) : 0;
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = Math.round(c0[c] + (c1[c] - c0[c]) * t);
  }
  LUT_CACHE.set(key, lut);
  return lut;
}
