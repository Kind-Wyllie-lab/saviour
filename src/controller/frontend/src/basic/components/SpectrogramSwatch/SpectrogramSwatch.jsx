// Miniature example spectrograms drawn in the browser through a palette's
// lookup table, so every palette (built-in or custom) and every band setup
// previews the way the monitor will draw it, not just as a gradient.
import { useEffect, useRef } from "react";
import { lutRgb, paletteByName } from "/src/basic/utils/spectrogramPalettes";

// Small deterministic PRNG so the noise floor looks the same every render.
function mulberry32(seed) {
  let a = seed;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Intensity field (0-255) indexed [row * w + col], row 0 = top (high
// frequency). `ridges(tNorm) -> [{ f: 0-1 from the bottom, amp, width }]`.
function field(w, h, ridges, floor = 0.06) {
  const rand = mulberry32(7);
  const out = new Float32Array(w * h);
  let max = 0;
  for (let col = 0; col < w; col++) {
    const rs = ridges(col / (w - 1));
    for (let row = 0; row < h; row++) {
      const f = 1 - (row + 0.5) / h;
      let v = floor * rand();
      for (const { f: f0, amp, width } of rs) {
        v += amp * Math.exp(-((f - f0) ** 2) / (2 * width * width));
      }
      out[row * w + col] = v;
      if (v > max) max = v;
    }
  }
  const img = new Uint8ClampedArray(w * h);
  for (let i = 0; i < out.length; i++) img[i] = Math.round(255 * (out[i] / max) ** 0.72);
  return img;
}

const FIELD_CACHE = new Map();

// The generic swatch scene: a few sweeping ridges over a noise floor (the
// same scene the old committed swatch PNGs showed).
function swatchField(w, h) {
  const key = `swatch:${w}x${h}`;
  if (!FIELD_CACHE.has(key)) {
    FIELD_CACHE.set(key, field(w, h, (t) => [
      { f: 0.18 + 0.44 * t, amp: 1.0, width: 0.02 },
      { f: 0.55 - 0.27 * t, amp: 0.75, width: 0.026 },
      { f: 0.8 + 0.08 * t, amp: 0.55, width: 0.018 },
      { f: 0.35 + 0.05 * t, amp: 0.45, width: 0.05 },
    ]));
  }
  return FIELD_CACHE.get(key);
}

// A rat-call scene over [loHz, hiHz]: a long flat 22 kHz call, rising
// 50-65 kHz chirps and a short 40 kHz noise burst, so band colouring shows
// what it does to calls inside and outside the bands.
function callField(w, h, loHz, hiHz) {
  const key = `calls:${w}x${h}:${loHz}:${hiHz}`;
  if (!FIELD_CACHE.has(key)) {
    const span = hiHz - loHz;
    const norm = (hz) => (hz - loHz) / span;
    const width = 700 / span;
    FIELD_CACHE.set(key, field(w, h, (t) => {
      const rs = [];
      if (t > 0.08 && t < 0.5) rs.push({ f: norm(22500), amp: 0.9, width });
      const phase = (t * 5) % 1;
      if (phase < 0.25) rs.push({ f: norm(50000 + 60000 * phase), amp: 1.0, width });
      if (t > 0.62 && t < 0.72) rs.push({ f: norm(40000), amp: 0.7, width: width * 1.5 });
      return rs;
    }, 0.12));
  }
  return FIELD_CACHE.get(key);
}

function useCanvas(draw, deps) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (canvas) draw(canvas.getContext("2d"), canvas.width, canvas.height);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller supplies deps
  }, deps);
  return ref;
}

function paint(ctx, w, h, grey, rowLut) {
  const img = ctx.createImageData(w, h);
  for (let row = 0; row < h; row++) {
    const lut = rowLut(row);
    for (let col = 0; col < w; col++) {
      const v = grey[row * w + col] * 3;
      const o = (row * w + col) * 4;
      img.data[o] = lut[v]; img.data[o + 1] = lut[v + 1]; img.data[o + 2] = lut[v + 2];
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}

/** A palette drawn as a miniature spectrogram. */
export function PaletteSwatch({ stops, width = 100, height = 28, className }) {
  const ref = useCanvas((ctx, w, h) => {
    const lut = lutRgb(stops);
    paint(ctx, w, h, swatchField(w, h), () => lut);
  }, [JSON.stringify(stops), width, height]);
  return <canvas ref={ref} width={width} height={height} className={className} />;
}

/**
 * A miniature of the banded view: example rat calls over [loHz, hiHz], each
 * enabled band in its palette, grey elsewhere -- what the monitor will show.
 */
export function BandPreview({ bands, loHz, hiHz, width = 320, height = 72, className }) {
  const enabled = (bands ?? []).filter((b) => b.enabled !== false);
  const ref = useCanvas((ctx, w, h) => {
    const grey = lutRgb(paletteByName("grayscale").stops);
    if (!(hiHz > loHz)) { paint(ctx, w, h, new Uint8ClampedArray(w * h), () => grey); return; }
    const rowLuts = Array.from({ length: h }, (_, row) => {
      const hz = hiHz - ((row + 0.5) / h) * (hiHz - loHz);
      const band = enabled.find((b) => hz >= Number(b.lo_hz) && hz < Number(b.hi_hz));
      return band ? lutRgb(band.stops ?? paletteByName(band.palette).stops) : grey;
    });
    paint(ctx, w, h, callField(w, h, loHz, hiHz), (row) => rowLuts[row]);
  }, [JSON.stringify(enabled), loHz, hiHz, width, height]);
  return <canvas ref={ref} width={width} height={height} className={className} />;
}
