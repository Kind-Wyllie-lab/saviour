// Habitat simulator model (plans/habitat-simulator.md): tagged rats moving
// between the habitat's boxes through the gates, as a pure function of
// (parameters, seed, time). No browser or Node APIs, so the browser mock
// (gate reads, AudioMoth levels) and the dev server (camera and microphone
// streams, vite.config.js) each build the same world and agree without
// talking to each other.
//
// Each "sim day" starts at schedule.day_start (local time, mid light phase)
// with every rat resting in its nest; from there it's event-driven: a rat
// stays in a box for a drawn dwell time, then crosses one gate of a passable
// wall into a neighbouring box. Events are processed strictly in time order
// from one seeded RNG, so how far ahead either side has computed doesn't
// change the result.

// ── Small deterministic helpers ─────────────────────────────────────────────

export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Stateless hash of a few integers to [0, 1), for per-slot randomness
// (calls, wander phases) that must not depend on evaluation order.
export function hash01(...ints) {
  let h = 0x811c9dc5;
  for (const v of ints) {
    h = Math.imul(h ^ (v | 0), 0x01000193);
    h ^= h >>> 13;
    h = Math.imul(h, 0x5bd1e995);
    h ^= h >>> 15;
  }
  return (h >>> 0) / 4294967296;
}

export function parseHHMM(s) {
  const [h, m] = String(s).split(":").map(Number);
  return h * 60 + m;
}

// ── Layout (the gate map's: gate_map.py / gateMapMock.js) ───────────────────

const boxId = (c, r) => `${String.fromCharCode(65 + c)}${r + 1}`;

export function generateLayout(cols = 4, rows = 4, perWall = 4) {
  const boxes = [];
  const walls = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      boxes.push({ id: boxId(c, r), col: c, row: r });
      if (c + 1 < cols) walls.push({ id: boxId(c, r) + boxId(c + 1, r), boxes: [boxId(c, r), boxId(c + 1, r)], orientation: "vertical" });
      if (r + 1 < rows) walls.push({ id: boxId(c, r) + boxId(c, r + 1), boxes: [boxId(c, r), boxId(c, r + 1)], orientation: "horizontal" });
    }
  }
  const gates = walls.flatMap((w) => Array.from({ length: perWall }, (_, i) => (
    { id: `${w.id}_${"abcdefgh"[i]}`, wall: w.id, index: i, fitted: true })));
  return { version: 1, cols, rows, gates_per_wall: perWall, boxes, walls, gates };
}

// The habitat layout from the parameters (walls listed in unfitted_walls get
// no gates).
export function simLayout(params) {
  const l = params.layout;
  const layout = generateLayout(l.cols, l.rows, l.gates_per_wall);
  const unfitted = new Set(l.unfitted_walls ?? []);
  for (const g of layout.gates) if (unfitted.has(g.wall)) g.fitted = false;
  return layout;
}

// ── Light schedule ──────────────────────────────────────────────────────────

const DAY_MIN = 1440;
const mod = (a, n) => ((a % n) + n) % n;

// Visible light 0..1 at a local minute of the day (cosine ramps).
export function lightAt(minute, schedule) {
  const on = parseHHMM(schedule.lights_on);
  const off = parseHHMM(schedule.lights_off);
  const up = schedule.sunrise_minutes ?? 0;
  const down = schedule.sunset_minutes ?? 0;
  const sinceOn = mod(minute - on, DAY_MIN);
  const dayLen = mod(off - on, DAY_MIN);
  const ease = (p) => 0.5 - 0.5 * Math.cos(Math.PI * Math.min(1, Math.max(0, p)));
  if (sinceOn < dayLen) return up > 0 && sinceOn < up ? ease(sinceOn / up) : 1;
  const sinceOff = sinceOn - dayLen;
  return down > 0 && sinceOff < down ? 1 - ease(sinceOff / down) : 0;
}

export function localMinute(tSec) {
  const d = new Date(tSec * 1000);
  return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60 + d.getMilliseconds() / 60000;
}

// Epoch seconds of the latest local `day_start` at or before tSec.
export function dayStartBefore(tSec, schedule) {
  const d = new Date(tSec * 1000);
  const m = parseHHMM(schedule.day_start ?? "13:00");
  const start = new Date(d.getFullYear(), d.getMonth(), d.getDate(), Math.floor(m / 60), m % 60, 0, 0);
  if (start.getTime() > d.getTime()) start.setDate(start.getDate() - 1);
  return start.getTime() / 1000;
}

// ── Geometry inside a box (0..1 both ways, x right, y down) ─────────────────

// Where a gate sits on the edge of `box` (gatePoint's spacing: (i+1)/(n+1)).
export function gateInBox(world, gateId, box) {
  const g = world.gateById[gateId];
  const w = world.wallById[g.wall];
  const t = (g.index + 1) / (world.layout.gates_per_wall + 1);
  const first = w.boxes[0] === box;
  if (w.orientation === "vertical") return { x: first ? 1 : 0, y: t };
  return { x: t, y: first ? 1 : 0 };
}

// ── The world ───────────────────────────────────────────────────────────────

export function createWorld(params) {
  const layout = simLayout(params);
  const gateById = Object.fromEntries(layout.gates.map((g) => [g.id, g]));
  const wallById = Object.fromEntries(layout.walls.map((w) => [w.id, w]));
  const closed = new Set(params.rfid?.closed_gates ?? []);

  // Every fitted gate's unit on the bus ("the wiring"), in layout order.
  const trueUnit = {};
  layout.gates.filter((g) => g.fitted).forEach((g, i) => { trueUnit[g.id] = i + 1; });

  // box -> [{ to, wall, gates }] over walls with an open fitted gate.
  const neighbours = Object.fromEntries(layout.boxes.map((b) => [b.id, []]));
  for (const w of layout.walls) {
    const gates = layout.gates.filter((g) => g.wall === w.id && g.fitted && !closed.has(g.id)).map((g) => g.id);
    if (!gates.length) continue;
    const [a, b] = w.boxes;
    neighbours[a].push({ to: b, wall: w.id, gates });
    neighbours[b].push({ to: a, wall: w.id, gates });
  }

  const rp = params.rats;
  const rats = Array.from({ length: rp.count }, (_, i) => ({
    index: i,
    tag: (rp.tag_prefix + (0x20 + i).toString(16).toUpperCase().padStart(2, "0")).slice(-10),
    nest: rp.nests?.[i] ?? layout.boxes[i % layout.boxes.length].id,
  }));

  const sched = params.schedule;
  const beh = params.behaviour;
  const world = {
    params, layout, gateById, wallById, trueUnit, neighbours, rats,
    day: null,        // { start, rng, t, stays: [[...per rat]], cur: [...], crossings: [] }
  };

  const lightAtT = (t) => lightAt(localMinute(t), sched);

  function startDay(start) {
    const dayIndex = Math.round(start / 86400);
    const rng = mulberry32((params.seed ^ Math.imul(dayIndex, 2654435761)) >>> 0);
    const cur = rats.map((r) => ({ box: r.nest, from: start, until: 0, enter: null, exit: null, rest: true }));
    world.day = { start, rng, t: start, cur, stays: rats.map(() => []), crossings: [] };
    // Initial dwell: resting in the nest for most of the light phase.
    cur.forEach((s) => { s.until = start + drawDwell(start, rng) * (0.3 + rng()); });
  }

  function drawDwell(t, rng) {
    const light = lightAtT(t);
    const mean = beh.dwell_mean_s.night + (beh.dwell_mean_s.day - beh.dwell_mean_s.night) * light;
    return Math.max(beh.dwell_min_s, -Math.log(1 - rng()) * mean);
  }

  function countIn(box, except) {
    let n = 0;
    world.day.cur.forEach((s, i) => { if (i !== except && s.box === box) n += 1; });
    return n;
  }

  // Rat i's stay ends at time t: stay put (nest by day) or cross into a
  // neighbour.
  function step(i) {
    const d = world.day;
    const s = d.cur[i];
    const t = s.until;
    const rng = d.rng;
    const light = lightAtT(t);
    const rat = rats[i];
    if (s.box === rat.nest && rng() < 0.75 * light) {
      s.until = t + drawDwell(t, rng);
      s.rest = s.until - s.from > 600;
      return;
    }
    const opts = neighbours[s.box];
    if (!opts.length) { s.until = t + drawDwell(t, rng); return; }
    const weights = opts.map((o) => 1 + beh.social_weight * countIn(o.to, i)
      + (o.to === rat.nest ? beh.nest_weight_day * light : 0));
    let pick = rng() * weights.reduce((a, b) => a + b, 0);
    let k = 0;
    while (k < opts.length - 1 && pick >= weights[k]) { pick -= weights[k]; k += 1; }
    const o = opts[k];
    const gate = o.gates[Math.floor(rng() * o.gates.length)];
    const reads = params.rfid?.reads_per_crossing ?? [1, 1];
    const nReads = reads[0] + Math.floor(rng() * (reads[1] - reads[0] + 1));
    s.exit = gate;
    d.stays[i].push(s);
    const at = t + beh.transit_s / 2;
    d.crossings.push({ t: at, rat: i, tag: rat.tag, gate, unit: trueUnit[gate], from: s.box, to: o.to,
      reads: Array.from({ length: nReads }, (_, j) => at + j * (params.rfid?.read_spacing_s ?? 0.25)) });
    const from = t + beh.transit_s;
    const until = from + drawDwell(from, rng);
    d.cur[i] = { box: o.to, from, until, enter: gate, exit: null, rest: until - from > 600 };
  }

  // Run the world forward to time t (seconds since the epoch).
  world.advance = (t) => {
    const start = dayStartBefore(t, sched);
    if (!world.day || world.day.start !== start) {
      if (world.day && world.day.start > start) return world;   // never run backwards across a day
      startDay(start);
    }
    const d = world.day;
    for (;;) {
      let next = -1;
      for (let i = 0; i < d.cur.length; i++) {
        if (d.cur[i].until <= t && (next < 0 || d.cur[i].until < d.cur[next].until)) next = i;
      }
      if (next < 0) break;
      step(next);
    }
    d.t = Math.max(d.t, t);
    return world;
  };

  // Where each rat is at t (t no later than the last advance, same day).
  world.stayAt = (i, t) => {
    const d = world.day;
    const s = d.cur[i];
    if (t >= s.from) return s;
    const list = d.stays[i];
    for (let k = list.length - 1; k >= 0; k--) if (t >= list[k].from) return list[k];
    return list[0] ?? s;
  };

  world.occupancy = (t) => {
    const out = Object.fromEntries(layout.boxes.map((b) => [b.id, 0]));
    rats.forEach((r) => { out[world.stayAt(r.index, t).box] += 1; });
    return out;
  };

  // Crossings with a read in (t0, t1].
  world.crossingsBetween = (t0, t1) => world.day.crossings.filter((c) => c.reads.some((x) => x > t0 && x <= t1));

  world.light = lightAtT;

  return world;
}

// ── Rat position inside its box (for camera frames) ─────────────────────────

const WALK_S = 2.5;

function lerp(a, b, p) { return { x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p }; }
const smooth = (q) => { const p = Math.min(1, Math.max(0, q)); return p * p * (3 - 2 * p); };

// Rat i's point in its stay s at t, and how settled (0..1) it is.
function placeInStay(world, i, t, s) {
  const seed = Math.floor(s.from * 10) + i * 7919;
  // Resting spot: a corner-ish place per (rat, stay).
  const rest = {
    x: 0.2 + 0.6 * Math.round(hash01(seed, 1)) + (hash01(seed, 2) - 0.5) * 0.14,
    y: 0.2 + 0.6 * Math.round(hash01(seed, 3)) + (hash01(seed, 4) - 0.5) * 0.14,
  };
  // Wandering: a smooth loop around the box.
  const f1 = 0.05 + hash01(seed, 5) * 0.06;
  const f2 = 0.04 + hash01(seed, 6) * 0.07;
  const p1 = hash01(seed, 7) * 6.283;
  const p2 = hash01(seed, 8) * 6.283;
  const wander = {
    x: 0.5 + 0.3 * Math.sin(t * f1 * 6.283 + p1) + 0.06 * Math.sin(t * 0.7 + p2),
    y: 0.5 + 0.3 * Math.sin(t * f2 * 6.283 + p2) + 0.06 * Math.cos(t * 0.9 + p1),
  };
  const settle = s.rest ? Math.min(1, Math.max(0, (t - s.from - 8) / 12)) : 0;
  const unsettle = s.rest ? Math.min(1, Math.max(0, (s.until - t - 8) / 12)) : 0;
  const k = smooth(Math.min(settle, unsettle));
  let pos = lerp(wander, rest, k);
  if (s.enter && t - s.from < WALK_S) {
    pos = lerp(gateInBox(world, s.enter, s.box), pos, smooth((t - s.from) / WALK_S));
  }
  const left = s.until - t;
  if (s.exit && left < WALK_S) {
    pos = lerp(pos, gateInBox(world, s.exit, s.box), smooth(1 - left / WALK_S));
  }
  return { pos, k, seed };
}

// { box, x, y, heading, resting } for rat i at t.
export function ratPosition(world, i, t) {
  const s = world.stayAt(i, t);
  const { pos, k, seed } = placeInStay(world, i, t, s);
  // Heading from a short look-back.
  const back = t - 0.25 >= s.from ? placeInStay(world, i, t - 0.25, s).pos : null;
  const moved = back && Math.abs(pos.x - back.x) + Math.abs(pos.y - back.y) > 1e-4;
  const heading = moved ? Math.atan2(pos.y - back.y, pos.x - back.x) : hash01(seed, 9) * 6.283;
  return { box: s.box, x: pos.x, y: pos.y, heading, resting: k > 0.95 };
}

// ── Calls (USVs) ────────────────────────────────────────────────────────────

export const CALL_SLOT_S = 0.25;

// The call in `box` during the 250 ms slot holding t, or null:
// { kind: "50k" | "22k", slot, f0 (Hz), sweep (Hz over the slot) }.
// 50 kHz calls are likely with two or more rats in the box; a 22 kHz alarm
// call holds for several slots.
export function callAt(world, box, t, occ) {
  const p = world.params.audio.call_prob_per_slot;
  const slot = Math.floor(t / CALL_SLOT_S);
  const bi = box.charCodeAt(0) * 31 + Number(box.slice(1));
  const n = occ[box] ?? 0;
  if (!n) return null;
  // Alarm calls: a run of 6 slots starting where a slot rolls an alarm.
  for (let k = 0; k < 6; k++) {
    if (hash01(world.params.seed, bi, slot - k, 22) < p.alarm) {
      return { kind: "22k", slot, f0: 22000 + hash01(bi, slot - k, 3) * 3000, sweep: 0 };
    }
  }
  const prob = n >= 2 ? p.social : p.alone;
  if (hash01(world.params.seed, bi, slot, 50) < prob) {
    const f0 = 48000 + hash01(bi, slot, 1) * 25000;
    return { kind: "50k", slot, f0, sweep: (hash01(bi, slot, 2) - 0.5) * 24000 };
  }
  return null;
}
