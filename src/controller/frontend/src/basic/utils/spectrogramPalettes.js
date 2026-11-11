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

export function paletteByName(name) {
  return BY_NAME[String(name ?? "").toLowerCase()] ?? BY_NAME[DEFAULT_PALETTE];
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
