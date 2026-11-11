// The DMX lighting schedule maths, mirrored from
// src/modules/variants/dmx_lighting/schedule.py so the config card can
// preview the 24 h curve and catch patch errors before saving. The module
// validates again on save (and is the authority).

export const DAY_MIN = 24 * 60;
export const CURVES = ["perceptual", "cosine", "linear"];
export const IR_MODES = ["constant", "night_only", "off"];
export const ROLES = ["visible", "ir"];

export function parseHHMM(v) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(v ?? "").trim());
  if (!m) return null;
  const h = Number(m[1]), mm = Number(m[2]);
  return h <= 23 && mm <= 59 ? h * 60 + mm : null;
}

const mod = (a, n) => ((a % n) + n) % n;
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const lstar = (y) => { y = clamp01(y); return y > 0.008856 ? 116 * Math.cbrt(y) - 16 : 903.3 * y; };
const lstarInv = (l) => { l = Math.min(100, Math.max(0, l)); return l > 8 ? ((l + 16) / 116) ** 3 : l / 903.3; };
const cosEase = (x) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(x));

export function rampLevel(lo, hi, p, curve) {
  p = clamp01(p);
  if (curve === "linear") return lo + (hi - lo) * p;
  if (curve === "cosine") return lo + (hi - lo) * cosEase(p);
  const a = lstar(lo / 100), b = lstar(hi / 100);
  return 100 * lstarInv(a + (b - a) * cosEase(p));
}

// {phase, progress} at wall-clock minute m.
export function phaseAt(m, on, off, rise, fall) {
  const dayLen = mod(off - on, DAY_MIN);
  const sinceOn = mod(m - on, DAY_MIN);
  if (sinceOn < dayLen) {
    if (rise > 0 && sinceOn < rise) return { phase: "sunrise", progress: sinceOn / rise };
    return { phase: "day", progress: 1 };
  }
  const sinceOff = mod(m - off, DAY_MIN);
  if (fall > 0 && sinceOff < fall) return { phase: "sunset", progress: 1 - sinceOff / fall };
  return { phase: "night", progress: 0 };
}

export function levelsAt(m, cfg) {
  const s = cfg.schedule ?? {};
  const on = parseHHMM(s.lights_on), off = parseHHMM(s.lights_off);
  const { phase, progress } = phaseAt(m, on, off, Number(s.sunrise_minutes) || 0,
    Number(s.sunset_minutes) || 0);
  const curve = s.curve ?? "perceptual";
  const visible = rampLevel(Number(s.night_level) || 0, Number(s.day_level ?? 100), progress, curve);
  const ir = cfg.ir ?? {};
  const irLevel = Number(ir.level ?? 100);
  const irOut = ir.mode === "off" ? 0
    : ir.mode === "night_only" ? rampLevel(0, irLevel, 1 - progress, curve) : irLevel;
  return { phase, progress, visible, ir: irOut };
}

export function dayCurve(cfg, stepMin = 2) {
  const out = [];
  for (let m = 0; m <= DAY_MIN; m += stepMin) out.push({ minute: m, ...levelsAt(m % DAY_MIN, cfg) });
  return out;
}

const isNum = (v, lo, hi) => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
const isInt = (v) => Number.isInteger(v);

// Returns { general: [..], rows: {index: [..]} } so the patch table can put
// errors on their row.
export function validate(cfg) {
  const general = [];
  const rows = {};
  const rowErr = (i, msg) => { (rows[i] ||= []).push(msg); };
  const s = cfg?.schedule ?? {};
  const on = parseHHMM(s.lights_on), off = parseHHMM(s.lights_off);
  if (on == null) general.push("Lights on must be HH:MM");
  if (off == null) general.push("Lights off must be HH:MM");
  const rise = s.sunrise_minutes, fall = s.sunset_minutes;
  if (!isNum(rise, 0, 720)) general.push("Sunrise must be 0-720 minutes");
  if (!isNum(fall, 0, 720)) general.push("Sunset must be 0-720 minutes");
  if (on != null && off != null) {
    const dayLen = mod(off - on, DAY_MIN);
    if (dayLen === 0) general.push("Lights on and lights off can't be the same time");
    else {
      if (isNum(rise, 0, 720) && rise > dayLen) general.push(`Sunrise (${rise} min) is longer than the light period (${dayLen} min)`);
      if (isNum(fall, 0, 720) && fall > DAY_MIN - dayLen) general.push(`Sunset (${fall} min) is longer than the dark period (${DAY_MIN - dayLen} min)`);
    }
  }
  if (!isNum(s.day_level, 0, 100)) general.push("Day level must be 0-100 %");
  if (!isNum(s.night_level, 0, 100)) general.push("Night level must be 0-100 %");
  if (!isNum(cfg?.ir?.level, 0, 100)) general.push("IR level must be 0-100 %");
  const bits = cfg?.channel_bits === 16 ? 16 : 8;
  const width = bits === 16 ? 2 : 1;
  const used = new Map();
  const seen = new Set();
  (cfg?.compartments ?? []).forEach((r, i) => {
    const box = String(r.box ?? "").trim();
    if (!box) rowErr(i, "Box name is empty");
    else if (seen.has(box)) rowErr(i, `Box ${box} is listed twice`);
    seen.add(box);
    if (!isNum(r.trim ?? 100, 0, 100)) rowErr(i, "Trim must be 0-100 %");
    for (const role of ROLES) {
      const a = r[role];
      if (a === null || a === undefined || a === "") continue;
      if (!isInt(a)) { rowErr(i, `${role}: address must be a whole number`); continue; }
      if (a < 1 || a + width - 1 > 512) { rowErr(i, `${role}: address ${a} is outside 1-512${width === 2 ? ` (16-bit uses ${a} and ${a + 1})` : ""}`); continue; }
      for (let ch = a; ch < a + width; ch++) {
        if (used.has(ch)) { rowErr(i, `${role}: channel ${ch} is already used by ${used.get(ch)}`); break; }
        used.set(ch, `${box || `row ${i + 1}`} ${role}`);
      }
    }
  });
  const count = general.length + Object.values(rows).reduce((a, r) => a + r.length, 0);
  return { general, rows, count };
}

export function channelMap(compartments, bits) {
  const width = bits === 16 ? 2 : 1;
  const out = [];
  for (const r of compartments ?? []) {
    for (const role of ROLES) {
      const a = r[role];
      if (!isInt(a)) continue;
      const parts = width === 2 ? ["coarse", "fine"] : ["level"];
      parts.forEach((part, k) => out.push({ channel: a + k, box: r.box, role, part }));
    }
  }
  return out.sort((x, y) => x.channel - y.channel);
}

// Next free start address for a new row's visible and IR strips.
export function nextAddresses(compartments, bits) {
  const width = bits === 16 ? 2 : 1;
  const used = new Set(channelMap(compartments, bits).map((c) => c.channel));
  const free = [];
  for (let a = 1; a + width - 1 <= 512 && free.length < 2; a += width) {
    let ok = true;
    for (let k = 0; k < width; k++) if (used.has(a + k)) ok = false;
    if (ok) free.push(a);
  }
  return free;
}

export const fmtMinute = (m) => `${String(Math.floor(mod(m, DAY_MIN) / 60)).padStart(2, "0")}:${String(Math.round(mod(m, DAY_MIN) % 60)).padStart(2, "0")}`;

export const PHASE_LABEL = {
  day: "Day", night: "Night", sunrise: "Sunrise", sunset: "Sunset",
  override: "Manual override", blackout: "Blackout", test: "Strip test",
  fault: "Fault", waiting_for_clock: "Waiting for clock", config_error: "Config error",
  starting: "Starting",
};
