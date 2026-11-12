// Dev-server side of the habitat simulator (plans/habitat-simulator.md),
// loaded by vite.config.js's mockMediaPlugin in mock mode only.
//
// Renders the simulated habitat as the streams the frontend loads:
//  - a camera named after a box ("B3"): a top-down view of that box with its
//    gate openings and the rats in it, daylight colour or IR grey from the
//    light schedule;
//  - a microphone module named after a column ("B"): a scrolling
//    spectrogram per AudioMoth (B1-B4), with the simulated calls.
// Frames are PNGs (encoded here with node:zlib, no dependencies), served as
// multipart/x-mixed-replace like a module's MJPEG /video_feed.

import zlib from "node:zlib";
import { createWorld, ratPosition, callAt, gateInBox, hash01 } from "../src/mock/habitatSim/model.js";

// ── PNG ─────────────────────────────────────────────────────────────────────

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

function encodePng(img) {
  const { w, h, px } = img;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 3 + 1)] = 0;
    px.copy(raw, y * (w * 3 + 1) + 1, y * w * 3, (y + 1) * w * 3);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw, { level: 1 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ── A tiny rasteriser ───────────────────────────────────────────────────────

const image = (w, h) => ({ w, h, px: Buffer.alloc(w * h * 3) });

function set(img, x, y, c, a = 1) {
  if (x < 0 || y < 0 || x >= img.w || y >= img.h) return;
  const i = (y * img.w + x) * 3;
  if (a >= 1) { img.px[i] = c[0]; img.px[i + 1] = c[1]; img.px[i + 2] = c[2]; return; }
  img.px[i] += (c[0] - img.px[i]) * a;
  img.px[i + 1] += (c[1] - img.px[i + 1]) * a;
  img.px[i + 2] += (c[2] - img.px[i + 2]) * a;
}

function rect(img, x0, y0, w, h, c, a = 1) {
  for (let y = Math.max(0, Math.round(y0)); y < Math.min(img.h, Math.round(y0 + h)); y++) {
    for (let x = Math.max(0, Math.round(x0)); x < Math.min(img.w, Math.round(x0 + w)); x++) set(img, x, y, c, a);
  }
}

// Filled ellipse centred (cx, cy), radii (rx, ry), rotated by ang; soft edge.
function ellipse(img, cx, cy, rx, ry, ang, c, a = 1) {
  const r = Math.max(rx, ry) + 1;
  const ca = Math.cos(ang);
  const sa = Math.sin(ang);
  for (let y = Math.floor(cy - r); y <= Math.ceil(cy + r); y++) {
    for (let x = Math.floor(cx - r); x <= Math.ceil(cx + r); x++) {
      const dx = x - cx;
      const dy = y - cy;
      const u = (dx * ca + dy * sa) / rx;
      const v = (-dx * sa + dy * ca) / ry;
      const d = Math.sqrt(u * u + v * v);
      if (d < 1) set(img, x, y, c, a * Math.min(1, (1 - d) * Math.min(rx, ry)));
    }
  }
}

function line(img, x0, y0, x1, y1, c, width = 1, a = 1) {
  const n = Math.ceil(Math.hypot(x1 - x0, y1 - y0)) + 1;
  for (let i = 0; i <= n; i++) {
    const p = i / n;
    ellipse(img, x0 + (x1 - x0) * p, y0 + (y1 - y0) * p, width / 2 + 0.5, width / 2 + 0.5, 0, c, a);
  }
}

// 5×7 bitmap font, enough for labels and times.
const GLYPHS = {
  "0": [14, 17, 19, 21, 25, 17, 14], "1": [4, 12, 4, 4, 4, 4, 14], "2": [14, 17, 1, 2, 4, 8, 31],
  "3": [31, 2, 4, 2, 1, 17, 14], "4": [2, 6, 10, 18, 31, 2, 2], "5": [31, 16, 30, 1, 1, 17, 14],
  "6": [6, 8, 16, 30, 17, 17, 14], "7": [31, 1, 2, 4, 8, 8, 8], "8": [14, 17, 17, 14, 17, 17, 14],
  "9": [14, 17, 17, 15, 1, 2, 12], ":": [0, 12, 12, 0, 12, 12, 0], ".": [0, 0, 0, 0, 0, 12, 12],
  "-": [0, 0, 0, 31, 0, 0, 0], " ": [0, 0, 0, 0, 0, 0, 0], "/": [1, 1, 2, 4, 8, 16, 16],
  A: [14, 17, 17, 31, 17, 17, 17], B: [30, 17, 17, 30, 17, 17, 30], C: [14, 17, 16, 16, 16, 17, 14],
  D: [28, 18, 17, 17, 17, 18, 28], E: [31, 16, 16, 30, 16, 16, 31], F: [31, 16, 16, 30, 16, 16, 16],
  G: [14, 17, 16, 23, 17, 17, 15], H: [17, 17, 17, 31, 17, 17, 17], I: [14, 4, 4, 4, 4, 4, 14],
  K: [17, 18, 20, 24, 20, 18, 17], L: [16, 16, 16, 16, 16, 16, 31], M: [17, 27, 21, 21, 17, 17, 17],
  N: [17, 17, 25, 21, 19, 17, 17], O: [14, 17, 17, 17, 17, 17, 14], P: [30, 17, 17, 30, 16, 16, 16],
  R: [30, 17, 17, 30, 20, 18, 17], S: [15, 16, 16, 14, 1, 1, 30], T: [31, 4, 4, 4, 4, 4, 4],
  U: [17, 17, 17, 17, 17, 17, 14], V: [17, 17, 17, 17, 17, 10, 4], W: [17, 17, 17, 21, 21, 21, 10],
  Y: [17, 17, 10, 4, 4, 4, 4], Z: [31, 1, 2, 4, 8, 16, 31], k: [16, 16, 18, 20, 24, 20, 18],
  z: [0, 0, 31, 2, 4, 8, 31],
};

function text(img, x, y, s, c, scale = 2, bg = null) {
  if (bg) rect(img, x - 3, y - 3, s.length * 6 * scale + 4, 7 * scale + 6, bg, 0.6);
  [...s].forEach((ch, k) => {
    const g = GLYPHS[ch] ?? GLYPHS[ch.toUpperCase()] ?? GLYPHS[" "];
    g.forEach((row, ry) => {
      for (let rx = 0; rx < 5; rx++) {
        if (row & (16 >> rx)) rect(img, x + (k * 6 + rx) * scale, y + ry * scale, scale, scale, c);
      }
    });
  });
}

const mix = (a, b, p) => [a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p, a[2] + (b[2] - a[2]) * p];
const hhmmss = (t) => new Date(t * 1000).toTimeString().slice(0, 8);

// ── Camera frames ───────────────────────────────────────────────────────────

const CAM_W = 320;
const CAM_H = 240;
const ARENA = 216;                       // square box floor, centred
const AX = (CAM_W - ARENA) / 2;
const AY = (CAM_H - ARENA) / 2;

// Per box: a fixed floor texture (bedding speckle), so frames are cheap.
const floorCache = new Map();
function floorTexture(box) {
  if (floorCache.has(box)) return floorCache.get(box);
  const n = new Float32Array(ARENA * ARENA);
  const bi = box.charCodeAt(0) * 31 + Number(box.slice(1));
  for (let i = 0; i < n.length; i++) n[i] = hash01(bi, i) * 0.6 + hash01(bi, i >> 3, 7) * 0.4;
  floorCache.set(box, n);
  return n;
}

// Daylight colours fade to IR grey (the cameras see by IR in the dark).
function palette(light) {
  const pal = (day, ir) => mix(ir, day, light);
  return {
    outside: pal([38, 36, 34], [22, 22, 22]),
    floorA: pal([196, 178, 140], [112, 112, 112]),
    floorB: pal([160, 140, 104], [84, 84, 84]),
    wall: pal([90, 96, 104], [52, 52, 52]),
    hole: pal([24, 22, 20], [14, 14, 14]),
    body: pal([236, 232, 222], [214, 214, 214]),
    hood: pal([58, 50, 46], [70, 70, 70]),
    tail: pal([212, 160, 150], [150, 150, 150]),
  };
}

// The box without rats, cached per (box, light step): most of a frame.
const backgroundCache = new Map();
function background(world, box, light) {
  const step = Math.round(light * 40);
  const key = `${box}:${step}`;
  if (backgroundCache.has(key)) return backgroundCache.get(key);
  if (backgroundCache.size > 400) backgroundCache.clear();
  const img = drawBackground(world, box, palette(step / 40));
  backgroundCache.set(key, img);
  return img;
}

function cameraFrame(world, box, t) {
  const light = world.light(t);
  const { body, hood, tail } = palette(light);
  const bg = background(world, box, light);
  const img = { w: bg.w, h: bg.h, px: Buffer.from(bg.px) };
  drawRats(img, world, box, t, body, hood, tail);
  // Text inside the box area: the dashboard tiles crop the frame's sides.
  const white = [240, 240, 240];
  text(img, AX + 6, AY + 6, box, white, 2, [0, 0, 0]);
  text(img, AX + ARENA - 6 - 8 * 6, AY + ARENA - 6 - 7, hhmmss(t), white, 1, [0, 0, 0]);
  if (light < 0.5) text(img, AX + ARENA - 6 - 2 * 12, AY + 6, "IR", white, 2, [0, 0, 0]);
  return img;
}

function drawBackground(world, box, { outside, floorA, floorB, wall, hole }) {
  const img = image(CAM_W, CAM_H);
  rect(img, 0, 0, CAM_W, CAM_H, outside);
  const tex = floorTexture(box);
  for (let y = 0; y < ARENA; y++) {
    for (let x = 0; x < ARENA; x++) set(img, AX + x, AY + y, mix(floorB, floorA, tex[y * ARENA + x]));
  }
  // Walls, with an opening at every gate on a wall of this box.
  const B = 6;
  rect(img, AX - B, AY - B, ARENA + 2 * B, B, wall);
  rect(img, AX - B, AY + ARENA, ARENA + 2 * B, B, wall);
  rect(img, AX - B, AY, B, ARENA, wall);
  rect(img, AX + ARENA, AY, B, ARENA, wall);
  const closed = new Set(world.params.rfid?.closed_gates ?? []);
  for (const g of world.layout.gates) {
    if (!g.fitted || !world.wallById[g.wall].boxes.includes(box)) continue;
    const p = gateInBox(world, g.id, box);
    const gx = AX + p.x * ARENA;
    const gy = AY + p.y * ARENA;
    const vertical = p.x === 0 || p.x === 1;
    const [w, h] = vertical ? [B + 2, 18] : [18, B + 2];
    const x0 = gx - w / 2 + (vertical ? (p.x ? 1 : -1) * 2 : 0);
    const y0 = gy - h / 2 + (vertical ? 0 : (p.y ? 1 : -1) * 2);
    rect(img, x0, y0, w, h, closed.has(g.id) ? mix(wall, [200, 200, 200], 0.25) : hole);
  }
  return img;
}

// The rats in this box (including one walking out through a gate).
function drawRats(img, world, box, t, body, hood, tail) {
  world.rats.forEach((r) => {
    const pos = ratPosition(world, r.index, t);
    if (pos.box !== box) return;
    const cx = AX + pos.x * ARENA;
    const cy = AY + pos.y * ARENA;
    const h = pos.heading;
    const ux = Math.cos(h);
    const uy = Math.sin(h);
    if (pos.resting) {
      ellipse(img, cx, cy, 15, 13, h, body);
      ellipse(img, cx + ux * 8, cy + uy * 8, 6, 5, h, hood);
      return;
    }
    const wag = Math.sin(t * 5 + r.index) * 0.4;
    line(img, cx - ux * 14, cy - uy * 14,
      cx - ux * 34 + Math.cos(h + Math.PI / 2) * wag * 10, cy - uy * 34 + Math.sin(h + Math.PI / 2) * wag * 10, tail, 2);
    ellipse(img, cx, cy, 18, 9, h, body);
    ellipse(img, cx + ux * 15, cy + uy * 15, 8, 6, h, hood);
  });
}

// ── Microphone spectrograms ─────────────────────────────────────────────────

const SPEC_W = 320;
const BAND_H = 64;                       // one AudioMoth: 0-96 kHz
const BAND_GAP = 4;
const SPEC_WINDOW_S = 3;
const NYQ = 96000;

// inferno-ish ramp
const RAMP = [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]];
function ramp(v) {
  const x = Math.min(1, Math.max(0, v)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  return mix(RAMP[i], RAMP[i + 1], x - i);
}

// Intensity 0..1 of one spectrogram pixel: noise floor (more at low
// frequency), broadband rustle while rats move, and the call trace.
function specColumn(world, box, t, occ, moving, out) {
  const call = callAt(world, box, t, occ);
  const bi = box.charCodeAt(0) * 31 + Number(box.slice(1));
  const tick = Math.floor(t * 200);
  let fCall = null;
  if (call) {
    const p = t / 0.25 - call.slot;
    if (call.kind === "22k") fCall = call.f0;
    else if (p < 0.6) fCall = call.f0 + call.sweep * p + 2500 * Math.sin(p * 40);
  }
  for (let y = 0; y < BAND_H; y++) {
    const f = (1 - (y + 0.5) / BAND_H) * NYQ;
    let v = 0.12 + 0.18 * Math.exp(-f / 9000) + hash01(bi, tick, y) * 0.12;
    if (moving && hash01(bi, tick >> 2, 99) < 0.3) v += 0.18 * Math.exp(-f / 30000);
    if (fCall != null) {
      const d = Math.abs(f - fCall) / (NYQ / BAND_H);
      if (d < 1.6) v += 0.75 * (1 - d / 1.6);
    }
    out[y] = v;
  }
}

// A scrolling image: new columns are drawn at the right edge and the rest
// shifted left, so each frame only computes the time since the last one.
class Spectrogram {
  constructor(world, column) {
    this.world = world;
    this.boxes = world.layout.boxes.filter((b) => b.id[0] === column).map((b) => b.id);
    this.h = this.boxes.length * (BAND_H + BAND_GAP) - BAND_GAP;
    this.img = image(SPEC_W, this.h);
    rect(this.img, 0, 0, SPEC_W, this.h, [10, 10, 14]);
    this.col = new Float32Array(BAND_H);
    this.tLast = null;
  }

  frame(t) {
    const dtCol = SPEC_WINDOW_S / SPEC_W;
    if (this.tLast == null || t - this.tLast > SPEC_WINDOW_S) this.tLast = t - SPEC_WINDOW_S;
    const n = Math.floor((t - this.tLast) / dtCol);
    if (n > 0) {
      const occ = this.world.occupancy(t);
      const moving = Object.fromEntries(this.boxes.map((b) => [b, this.world.rats.some((r) => {
        const p = ratPosition(this.world, r.index, t);
        return p.box === b && !p.resting;
      })]));
      const { px, w } = this.img;
      for (let y = 0; y < this.h; y++) px.copy(px, y * w * 3, (y * w + n) * 3, (y + 1) * w * 3);
      for (let k = 0; k < n; k++) {
        this.tLast += dtCol;
        const x = SPEC_W - n + k;
        this.boxes.forEach((b, bi) => {
          specColumn(this.world, b, this.tLast, occ, moving[b], this.col);
          const y0 = bi * (BAND_H + BAND_GAP);
          for (let y = 0; y < BAND_H; y++) set(this.img, x, y0 + y, ramp(this.col[y]));
        });
      }
    }
    const out = { w: this.img.w, h: this.img.h, px: Buffer.from(this.img.px) };
    this.boxes.forEach((b, bi) => text(out, 5, bi * (BAND_H + BAND_GAP) + 4, b, [240, 240, 240], 1, [0, 0, 0]));
    return out;
  }
}

// ── Serving ─────────────────────────────────────────────────────────────────

const CAM_FPS = 5;
const SPEC_FPS = 8;

export function createHabitatMedia(params) {
  const world = createWorld(params);
  const boxes = new Set(world.layout.boxes.map((b) => b.id));
  const columns = new Set(world.layout.boxes.map((b) => b.id[0]));
  const now = () => Date.now() / 1000;

  // A fresh spectrogram per connection (each keeps its own scrolling buffer).
  function renderer(label) {
    if (boxes.has(label)) return { fps: CAM_FPS, frame: (t) => cameraFrame(world.advance(t), label, t) };
    if (columns.has(label)) {
      const spec = new Spectrogram(world, label);
      return { fps: SPEC_FPS, frame: (t) => spec.frame((world.advance(t), t)) };
    }
    return null;
  }

  // Handles a /mock-media request for a simulated label; false otherwise.
  return function handle(req, res, url) {
    const label = url.searchParams.get("label") || "";
    const r = renderer(label);
    if (!r) return false;
    if (!url.pathname.startsWith("/stream")) {
      // A still: for a spectrogram, fill its window first.
      const png = encodePng(r.frame(now()));
      res.writeHead(200, { "Content-Type": "image/png", "Cache-Control": "no-store", "Content-Length": png.length });
      res.end(png);
      return true;
    }
    res.writeHead(200, {
      "Content-Type": "multipart/x-mixed-replace; boundary=frame",
      "Cache-Control": "no-store",
      Connection: "close",
    });
    let closed = false;
    const send = () => {
      if (closed) return;
      try {
        const png = encodePng(r.frame(now()));
        res.write(`--frame\r\nContent-Type: image/png\r\nContent-Length: ${png.length}\r\n\r\n`);
        res.write(png);
        res.write("\r\n");
      } catch (err) {
        console.error("[mock-media]", err);
      }
    };
    send();
    const iv = setInterval(send, 1000 / r.fps);
    const stop = () => { closed = true; clearInterval(iv); };
    req.on("close", stop);
    res.on("close", stop);
    return true;
  };
}
