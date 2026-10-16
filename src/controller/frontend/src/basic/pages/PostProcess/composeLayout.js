// Pure helpers for the compose layout editor. The layout document's shape
// and limits mirror src/controller/compose_layout.py; the backend
// validates everything again, so these only keep the editor honest.

export const CANVAS_PRESETS = [
  { label: "1920 × 1080 (16:9)", width: 1920, height: 1080 },
  { label: "1280 × 720 (16:9)", width: 1280, height: 720 },
  { label: "1080 × 1080 (square)", width: 1080, height: 1080 },
  { label: "1080 × 1920 (portrait)", width: 1080, height: 1920 },
];

export const LIMITS = {
  minCanvas: 64, maxCanvasW: 3840, maxCanvasH: 4320, minTile: 8, maxTiles: 16,
  maxAudioTracks: 8, windowMin: 0.5, windowMax: 600,
};

export const TEMPLATES = [
  { value: "auto", label: "Auto" },
  { value: "side", label: "Side by side" },
  { value: "stack", label: "Stacked" },
  { value: "grid", label: "Grid" },
];

export const TEMPLATE_AUDIO = [
  { value: "none", label: "Cameras only" },
  { value: "strip", label: "Spectrogram strip over the video" },
  { value: "panel", label: "Spectrogram panels below the video" },
  { value: "track", label: "Audio tracks, no spectrogram" },
];

export const SPEC_COLORS = [
  "intensity", "rainbow", "magma", "viridis", "plasma", "cividis",
  "fire", "fiery", "fruit", "moreland", "nebulae", "terrain",
  "cool", "green", "channel",
];

export const DEFAULT_SPECTROGRAM = {
  color: "intensity", fmin_hz: 0, fmax_hz: 96000, fscale: "lin", ascale: "log",
  gain: 2.5, window_s: null, playhead: 0.5, label: false,
};

export const DEFAULT_CAMERA = { fit: "contain", crop: null, rotate: 0, label: true };

const even = (v) => Math.max(2, Math.round(v / 2) * 2);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Group the flat session file list into { dateDir: { cameras, mics } }.
 *  A camera is a folder with a video + a *_timestamps.csv; a mic a folder
 *  with a .flac/.wav + a *_timestamps.txt (what compose.py looks for). */
export function streamsByDate(files) {
  const has = { vid: new Set(), csv: new Set(), aud: new Set(), txt: new Set() };
  for (const f of files || []) {
    const parts = f.path.split("/");
    if (parts.length < 3) continue;
    const key = `${parts[0]}/${parts[1]}`;
    if (/\.(ts|mp4)$/i.test(f.name)) has.vid.add(key);
    if (/_timestamps\.csv$/i.test(f.name)) has.csv.add(key);
    if (/\.(flac|wav)$/i.test(f.name)) has.aud.add(key);
    if (/_timestamps\.txt$/i.test(f.name)) has.txt.add(key);
  }
  const byDate = {};
  const bucket = (d) => (byDate[d] ||= { cameras: [], mics: [] });
  for (const key of has.vid) {
    if (!has.csv.has(key)) continue;
    const [d, m] = key.split("/");
    bucket(d).cameras.push(m);
  }
  for (const key of has.aud) {
    if (!has.txt.has(key)) continue;
    const [d, m] = key.split("/");
    bucket(d).mics.push(m);
  }
  for (const d of Object.keys(byDate)) {
    byDate[d].cameras.sort();
    byDate[d].mics.sort();
  }
  return byDate;
}

export function newTileId(doc, prefix) {
  const used = new Set(doc.tiles.map((t) => t.id));
  for (let i = 1; ; i += 1) {
    if (!used.has(`${prefix}${i}`)) return `${prefix}${i}`;
  }
}

/** Clamp a rect inside the canvas, whole pixels, at least minTile. */
export function clampRect([x, y, w, h], canvas) {
  const W = canvas.width;
  const H = canvas.height;
  const nw = clamp(Math.round(w), LIMITS.minTile, W);
  const nh = clamp(Math.round(h), LIMITS.minTile, H);
  return [clamp(Math.round(x), 0, W - nw), clamp(Math.round(y), 0, H - nh), nw, nh];
}

/** The displayed aspect of a camera after its crop and rotation. */
export function cameraAspect(cam, options = {}) {
  if (!cam?.width || !cam?.height) return 16 / 9;
  const crop = options.crop || [0, 0, 1, 1];
  let w = cam.width * crop[2];
  let h = cam.height * crop[3];
  if (options.rotate === 90 || options.rotate === 270) [w, h] = [h, w];
  return w / h;
}

/** A new camera tile: half the canvas wide at the camera's aspect,
 *  cascaded so successive adds don't land exactly on top of each other. */
export function newCameraTile(doc, source, cam) {
  const { width: W, height: H } = doc.canvas;
  const aspect = cameraAspect(cam);
  let w = Math.round(W / 2);
  let h = Math.round(w / aspect);
  if (h > H) { h = H; w = Math.round(h * aspect); }
  const off = (doc.tiles.length % 6) * 32;   // clampRect keeps it on the canvas
  return {
    id: newTileId(doc, "c"), kind: "camera", source,
    rect: clampRect([off, off, w, h], doc.canvas),
    z: 0, options: { ...DEFAULT_CAMERA },
  };
}

/** A new spectrogram tile: full width, a fifth of the height, at the
 *  bottom, above the cameras. */
export function newSpectrogramTile(doc, source, defaults = {}) {
  const { width: W, height: H } = doc.canvas;
  const h = Math.max(LIMITS.minTile, even(H * 0.2));
  return {
    id: newTileId(doc, "a"), kind: "spectrogram", source,
    rect: clampRect([0, H - h, W, h], doc.canvas),
    z: 1, options: { ...DEFAULT_SPECTROGRAM, ...defaults },
  };
}

/** `doc` scaled uniformly to fit a new canvas size and centred on it
 *  (mirrors compose_layout.fit_to_canvas). */
export function fitToCanvas(doc, width, height) {
  const W = even(width);
  const H = even(height);
  const s = Math.min(W / doc.canvas.width, H / doc.canvas.height);
  const ox = (W - doc.canvas.width * s) / 2;
  const oy = (H - doc.canvas.height * s) / 2;
  const canvas = { ...doc.canvas, width: W, height: H };
  return {
    ...doc,
    canvas,
    tiles: doc.tiles.map((t) => {
      const [x, y, w, h] = t.rect;
      return { ...t, rect: clampRect([ox + x * s, oy + y * s, w * s, h * s], canvas) };
    }),
  };
}

/** Sources the layout uses that this day doesn't have. */
export function missingSources(doc, cameraNames, micIds) {
  const cams = new Set(cameraNames);
  const mics = new Set(micIds);
  const out = [];
  for (const t of doc?.tiles || []) {
    const ok = t.kind === "camera" ? cams.has(t.source) : mics.has(t.source);
    if (!ok && !out.includes(t.source)) out.push(t.source);
  }
  for (const m of doc?.audio_tracks || []) {
    if (!mics.has(m) && !out.includes(m)) out.push(m);
  }
  return out;
}

/** Seconds of audio an "auto" spectrogram window shows: one strip pixel
 *  per tile pixel at the render's scroll speed (audio_align
 *  _strip_pixels_per_second). */
export function autoWindowSeconds(tileW, durationS, pps = 120, maxStrip = 48000) {
  if (!durationS) return null;
  const total = Math.max(durationS, 0.1);
  const strip = Math.max(2, Math.floor(Math.min(Math.ceil(total * pps), maxStrip) / 2) * 2);
  return tileW / (strip / total);
}

// ── snapping ────────────────────────────────────────────────────────────

/** Snap a moving/resizing rect's edges to the canvas edges and centre
 *  lines and to other tiles' edges, within `tol` output px. `edges` says
 *  which edges move ("l", "r", "t", "b"; all four for a move). Returns
 *  { rect, guides: [{ axis: "x"|"y", pos }] }. */
export function snapRect(rect, others, canvas, tol, edges) {
  const [x, y, w, h] = rect;
  const xs = [0, canvas.width / 2, canvas.width];
  const ys = [0, canvas.height / 2, canvas.height];
  for (const [ox, oy, ow, oh] of others) {
    xs.push(ox, ox + ow, ox + ow / 2);
    ys.push(oy, oy + oh, oy + oh / 2);
  }
  const best = (vals, targets) => {
    let pick = null;
    for (const v of vals) {
      for (const tgt of targets) {
        const d = tgt - v.pos;
        if (Math.abs(d) <= tol && (!pick || Math.abs(d) < Math.abs(pick.d))) {
          pick = { d, pos: tgt };
        }
      }
    }
    return pick;
  };
  const guides = [];
  let [nx, ny, nw, nh] = [x, y, w, h];
  const move = edges.length === 4;
  if (move) {
    const sx = best([{ pos: x }, { pos: x + w / 2 }, { pos: x + w }], xs);
    if (sx) { nx += sx.d; guides.push({ axis: "x", pos: sx.pos }); }
    const sy = best([{ pos: y }, { pos: y + h / 2 }, { pos: y + h }], ys);
    if (sy) { ny += sy.d; guides.push({ axis: "y", pos: sy.pos }); }
    return { rect: [nx, ny, nw, nh], guides };
  }
  if (edges.includes("l")) {
    const s = best([{ pos: x }], xs);
    if (s) { nx += s.d; nw -= s.d; guides.push({ axis: "x", pos: s.pos }); }
  }
  if (edges.includes("r")) {
    const s = best([{ pos: x + w }], xs);
    if (s) { nw += s.d; guides.push({ axis: "x", pos: s.pos }); }
  }
  if (edges.includes("t")) {
    const s = best([{ pos: y }], ys);
    if (s) { ny += s.d; nh -= s.d; guides.push({ axis: "y", pos: s.pos }); }
  }
  if (edges.includes("b")) {
    const s = best([{ pos: y + h }], ys);
    if (s) { nh += s.d; guides.push({ axis: "y", pos: s.pos }); }
  }
  return { rect: [nx, ny, nw, nh], guides };
}

/** Resize `start` by dragging handle `edges` by (dx, dy) output px;
 *  `aspect` (w/h) locks the ratio when given. */
export function resizeRect(start, edges, dx, dy, aspect) {
  let [x, y, w, h] = start;
  if (edges.includes("l")) { x += dx; w -= dx; }
  if (edges.includes("r")) { w += dx; }
  if (edges.includes("t")) { y += dy; h -= dy; }
  if (edges.includes("b")) { h += dy; }
  w = Math.max(LIMITS.minTile, w);
  h = Math.max(LIMITS.minTile, h);
  if (aspect) {
    const horiz = edges.includes("l") || edges.includes("r");
    const vert = edges.includes("t") || edges.includes("b");
    if (horiz && (!vert || Math.abs(dx) >= Math.abs(dy))) {
      const nh = w / aspect;
      if (edges.includes("t")) y += h - nh;
      h = nh;
    } else {
      const nw = h * aspect;
      if (edges.includes("l")) x += w - nw;
      w = nw;
    }
  }
  return [x, y, w, h];
}

// ── timeline ─────────────────────────────────────────────────────────────

export const SPEEDS = [0.5, 1, 2, 4, 8];

// Times here are epoch milliseconds (Numbers stay exact at ms; wall-clock
// ns don't fit a double). The layout document carries ns as strings.
export const nsToMs = (ns) => (ns == null ? null : Math.round(Number(ns) / 1e6));
export const msToNs = (ms) => (ms == null ? null : `${Math.round(ms)}000000`);

const pad = (n, w = 2) => String(Math.floor(n)).padStart(w, "0");

/** "1:02:03.4" (or "2:03.4") for a span of ms. */
export function formatSpan(ms) {
  const neg = ms < 0;
  let s = Math.abs(ms) / 1000;
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  const sec = `${pad(s)}.${Math.floor((s % 1) * 10)}`;
  return `${neg ? "-" : ""}${h ? `${h}:${pad(m)}` : m}:${sec}`;
}

/** Parse "h:mm:ss(.s)", "m:ss" or seconds into ms; null if it isn't one. */
export function parseSpan(text) {
  const parts = String(text).trim().split(":").map(Number);
  if (!parts.length || parts.some((p) => !Number.isFinite(p) || p < 0) || parts.length > 3) {
    return null;
  }
  return parts.reduce((acc, p) => acc * 60 + p, 0) * 1000;
}
