// Mock-mode stand-in for the habitat controller's gate-mapping service
// (variants/habitat/gate_map_service.py), so the Gates page can be clicked
// through with `VITE_VARIANT=habitat npm run dev:mock`. A rough simulation,
// not a port: a mapping run registers a probe a moment after it starts, and
// each gate is "read" by its unit on the simulated bus shortly after it's
// prompted (sometimes two units, sometimes a unit that's already mapped, to
// show the checks). The layout, the bus wiring and the live reads come from
// the habitat simulator (habitatSim/), so reads follow the simulated rats.

import { params, simWorld, recentCall } from "./habitatSim/index";
import { BOXES, COLUMNS, RFID_ID, STRAY_MOTH, micId, mothSerial } from "./habitatSim/fleet";

const SETTLE_S = 1.5;
const PROBE = "000807DC1F";
const MODULE = RFID_ID;

const now = () => Date.now() / 1000;

function initialState() {
  const world = simWorld();
  const layout = JSON.parse(JSON.stringify(world.layout));
  // Every fitted gate mapped to its unit on the simulated bus, except a few
  // left unmapped (their reads show as "from unmapped units"), one closed,
  // one faulty (habitat_sim.json rfid).
  const rp = params.rfid ?? {};
  const unmapped = new Set(rp.unmapped_gates ?? []);
  const closed = new Set(rp.closed_gates ?? []);
  const faulty = new Set(rp.faulty_gates ?? []);
  const entries = {};
  const blank = { module_id: null, unit: null, unit_serial: null, mapped_at: now() - 86400, run_id: "mock0" };
  for (const gate of layout.gates.filter((x) => x.fitted)) {
    if (unmapped.has(gate.id)) continue;
    if (closed.has(gate.id)) entries[gate.id] = { gate: gate.id, status: "closed", ...blank };
    else if (faulty.has(gate.id)) entries[gate.id] = { gate: gate.id, status: "faulty", ...blank };
    else {
      const unit = world.trueUnit[gate.id];
      entries[gate.id] = { gate: gate.id, status: "mapped", module_id: MODULE, unit,
        unit_serial: `SN${String(unit).padStart(6, "0")}`, mapped_at: now() - 86400, run_id: "mock0" };
    }
  }
  return {
    layout,
    entries,
    updated_at: now() - 86400,
    run: null,
    walkthrough: false,
    bus: null,
    bus_scanned_at: null,
    hits: [],
    changes: [],
    nextUnit: Math.max(...Object.values(simWorld().trueUnit)) + 1,
    timers: [],
  };
}

let g = null;

function mappedUnits(entries) {
  const out = {};
  for (const [gate, e] of Object.entries(entries)) {
    if (e?.status === "mapped") (out[`${e.module_id}:${e.unit}`] ??= []).push(gate);
  }
  return out;
}

function runEntries(run) {
  const out = { ...run.base };
  for (const [gate, e] of Object.entries(run.results)) {
    if (e === null) delete out[gate]; else out[gate] = e;
  }
  return out;
}

function review(entries) {
  const byUnit = mappedUnits(entries);
  const bus = g.bus ?? [];
  const busKeys = new Set(bus.map((b) => b.unit));
  return {
    scanned: !!g.bus,
    shared_units: Object.entries(byUnit).filter(([, gs]) => gs.length > 1).map(([unit, gates]) => ({ unit, gates })),
    on_bus_not_mapped: g.bus ? [...busKeys].filter((k) => !byUnit[k]).sort() : [],
    mapped_not_on_bus: g.bus ? Object.entries(byUnit).filter(([k]) => !busKeys.has(k)).map(([unit, gates]) => ({ unit, gates })) : [],
    serial_changed: [],
  };
}

function issue(run) {
  const c = run.capture;
  if (run.phase !== "gate" || !c.settled || !c.first) return null;
  const units = Object.keys(c.counts);
  if (units.length > 1) return { kind: "multiple", units: units.sort((a, b) => c.counts[b] - c.counts[a]) };
  const others = (mappedUnits(runEntries(run))[c.first] ?? []).filter((x) => x !== run.scope[run.index]);
  return others.length ? { kind: "already_mapped", unit: c.first, gates: others } : null;
}

function diff(before, after) {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort()
    .filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .map((k) => ({ gate: k, before: before[k] ?? null, after: after[k] ?? null }));
}

function view() {
  const run = g.run;
  let runView = null;
  if (run) {
    runView = {
      run_id: run.run_id, operator: run.operator, scope: run.scope, started_at: run.started_at,
      phase: run.phase, index: run.index, probe_tag: run.probe_tag, prompt_ts: run.prompt_ts,
      results: run.results, gate: run.phase === "gate" ? run.scope[run.index] : null,
      capture: run.capture, issue: issue(run), total: run.scope.length, settle_s: SETTLE_S,
    };
    if (run.phase === "review") {
      runView.review = review(runEntries(run));
      runView.changes = diff(run.base, runEntries(run));
    }
  }
  const fitted = new Set(g.layout.gates.filter((x) => x.fitted).map((x) => x.id));
  return {
    layout: g.layout,
    map: { entries: g.entries, updated_at: g.updated_at },
    orphaned: Object.keys(g.entries).filter((k) => !fitted.has(k)).sort(),
    check: review(g.entries),
    run: runView,
    walkthrough: g.walkthrough,
    bus: g.bus,
    bus_scanned_at: g.bus_scanned_at,
    rfid_modules: [MODULE],
    hits: g.hits,
    changes: g.changes,
    settle_s: SETTLE_S,
  };
}

function later(sock, ms, fn) {
  g.timers.push(setTimeout(() => { fn(); sock._fire("gate_map_state", view()); }, ms));
}

function clearTimers() {
  g.timers.forEach(clearTimeout);
  g.timers = [];
}

function hit(sock, unit, tag = PROBE) {
  const h = { module_id: MODULE, unit, tag, ts: now(),
    gates: mappedUnits(g.entries)[`${MODULE}:${unit}`] ?? [], probe: tag === g.run?.probe_tag };
  g.hits = [h, ...g.hits].slice(0, 20);
  sock._fire("gate_map_hit", h);
}

// Simulate the probe passing the current gate.
function simulatePass(sock) {
  const run = g.run;
  if (!run || run.phase !== "gate") return;
  later(sock, 1500 + Math.random() * 1500, () => {
    if (g.run !== run || run.phase !== "gate") return;
    const roll = Math.random();
    const units = [];
    if (roll < 0.12 && Object.keys(g.entries).length) {
      const any = Object.values(g.entries).find((e) => e.status === "mapped");
      units.push(any.unit);                    // already mapped elsewhere
    } else {
      // The gate's own unit on the simulated bus (a new one if the
      // layout was edited to add gates the simulator doesn't know).
      units.push(simWorld().trueUnit[run.scope[run.index]] ?? g.nextUnit++);
      if (roll > 0.88) units.push(units[0] + 100);   // a neighbour also reads it
    }
    run.capture = { first: `${MODULE}:${units[0]}`, first_ts: now(), counts: {}, settled: false };
    for (const u of units) {
      run.capture.counts[`${MODULE}:${u}`] = u === units[0] ? 3 : 1;
      hit(sock, u);
    }
    later(sock, SETTLE_S * 1000, () => { if (g.run === run) run.capture.settled = true; });
  });
}

function prompt(run) {
  run.capture = { first: null, first_ts: null, counts: {}, settled: false };
  run.prompt_ts = now();
}

// The dashboard's live map, from the simulated rats: every crossing is 1-3
// reads of the rat's tag on that gate's unit (none from a faulty gate), and
// AudioMoth levels for every box: room noise, more per rat in the box, and a
// burst on a call (habitatSim/model.js callAt, the same calls the dev
// server draws on the Monitor spectrograms).
let liveTimer = null;
const LIVE_TICK_MS = 250;
function startLive(sock) {
  if (liveTimer) return;
  const ap = params.audio;
  const faulty = new Set(params.rfid?.faulty_gates ?? []);
  const base = Object.fromEntries(BOXES.map((b, i) => [b, ap.noise_db[0] + ((i * 7) % 10) / 10 * (ap.noise_db[1] - ap.noise_db[0])]));
  let last = Date.now() / 1000;
  let tick = 0;
  liveTimer = setInterval(() => {
    tick++;
    g ??= initialState();
    const world = simWorld();
    const t = Date.now() / 1000;
    for (const c of world.crossingsBetween(last, t)) {
      if (faulty.has(c.gate)) continue;
      for (const r of c.reads) if (r > last && r <= t) hit(sock, c.unit, c.tag);
    }
    last = t;
    if (tick % 2) return;
    const occ = world.occupancy(t);
    for (const col of COLUMNS) {
      const levels = BOXES.filter((b) => b[0] === col).map((b) => {
        const call = recentCall(world, b, t, occ, 0.5);
        const burst = call ? ap.call_db * (0.7 + Math.random() * 0.3) : 0;
        const level = Math.min(-1, base[b] + ap.per_rat_db * occ[b] + Math.random() * 4 + burst);
        return { serial: mothSerial(b), label: b, level_db: level,
          peak_db: Math.min(0, level + 6 + Math.random() * 5), clip_pct: 0, clipping: false };
      });
      if (col === STRAY_MOTH.module) {
        levels.push({ serial: STRAY_MOTH.serial, label: STRAY_MOTH.serial, level_db: -70, peak_db: -62,
          clip_pct: 0, clipping: false });
      }
      sock._fire("audio_levels", { module_id: micId(col), levels });
    }
  }, LIVE_TICK_MS);
}

export const gateMapRoutes = {
  gate_map_live() { startLive(this); },
  get_gate_map() {
    g ??= initialState();
    this._fire("gate_map_state", view());
  },
  gate_map_keepalive() {},
  gate_map_scan() {
    g ??= initialState();
    later(this, 800, () => {
      const units = Object.values(g.entries).filter((e) => e.status === "mapped").map((e) => e.unit);
      g.bus = [...units.slice(1), 77].map((u) => ({ unit: `${MODULE}:${u}`, module_id: MODULE, address: u, serial: `SN${String(u).padStart(6, "0")}`, firmware: "800" }));
      g.bus_scanned_at = now();
    });
  },
  gate_map_walkthrough(p) {
    g.walkthrough = !!p?.enabled;
    if (g.walkthrough) {
      g.hits = [];
      const tick = () => {
        if (!g.walkthrough) return;
        const mapped = Object.values(g.entries).filter((e) => e.status === "mapped");
        const pick = Math.random() < 0.8 && mapped.length ? mapped[Math.floor(Math.random() * mapped.length)].unit : 99;
        hit(this, pick, "1122334455");
        g.timers.push(setTimeout(tick, 2500));
      };
      g.timers.push(setTimeout(tick, 1200));
    }
    this._fire("gate_map_state", view());
  },
  gate_map_save_layout(p) {
    if (g.run) { this._fire("gate_map_error", { error: "Finish or discard the mapping run before changing the layout" }); return; }
    g.layout = p.layout;
    this._fire("gate_map_state", view());
  },
  gate_map_start_run(p) {
    if (g.run) { this._fire("gate_map_error", { error: "A mapping run is already in progress" }); return; }
    if (!p?.operator?.trim()) { this._fire("gate_map_error", { error: "Enter your name or initials first" }); return; }
    const fitted = g.layout.gates.filter((x) => x.fitted).map((x) => x.id);
    const scope = p.scope === "all" ? fitted
      : p.scope?.wall ? fitted.filter((x) => x.startsWith(`${p.scope.wall}_`))
        : fitted.filter((x) => p.scope.gates.includes(x));
    g.run = { run_id: Math.random().toString(16).slice(2, 14), operator: p.operator.trim(), scope,
      started_at: now(), base: { ...g.entries }, phase: "probe", index: 0, probe_tag: null,
      results: {}, capture: null };
    prompt(g.run);
    gateMapRoutes.gate_map_scan.call(this);
    this._fire("gate_map_state", view());
    later(this, 1500, () => {
      g.run.probe_tag = PROBE;
      g.run.phase = "gate";
      prompt(g.run);
      hit(this, 5);
      simulatePass(this);
    });
  },
  gate_map_action(p) {
    const run = g.run;
    if (!run) return;
    clearTimers();
    const gate = run.scope[run.index];
    const entry = (status, key) => {
      const [module_id, unit] = key ? [key.slice(0, key.lastIndexOf(":")), Number(key.slice(key.lastIndexOf(":") + 1))] : [null, null];
      return { gate, status, module_id, unit, unit_serial: key ? `SN${String(unit).padStart(6, "0")}` : null, mapped_at: now(), run_id: run.run_id };
    };
    const iss = issue(run);
    switch (p.action) {
      case "back":
        if (run.phase === "review") { run.phase = "gate"; run.index = run.scope.length - 1; }
        else if (run.index > 0) run.index -= 1;
        prompt(run); simulatePass(this);
        this._fire("gate_map_state", view());
        return;
      case "retry":
        prompt(run); simulatePass(this);
        this._fire("gate_map_state", view());
        return;
      case "confirm": {
        if (!run.capture.settled) { this._fire("gate_map_error", { error: "No antenna has read the probe at this gate yet" }); return; }
        let key = run.capture.first;
        if (iss?.kind === "multiple") {
          if (!p.unit) { this._fire("gate_map_error", { error: "Several units read the probe: pick one" }); return; }
          key = p.unit;
        }
        if (iss?.kind === "already_mapped") {
          if (p.resolve === "move") for (const other of iss.gates) run.results[other] = null;
          else if (p.resolve !== "share") { this._fire("gate_map_error", { error: "Move it or share it" }); return; }
        }
        run.results[gate] = entry("mapped", key);
        break;
      }
      case "skip_closed": run.results[gate] = entry("closed"); break;
      case "skip_not_fitted": run.results[gate] = entry("not_fitted"); break;
      case "faulty": run.results[gate] = entry("faulty"); break;
      default: return;
    }
    run.index += 1;
    if (run.index >= run.scope.length) run.phase = "review";
    prompt(run);
    simulatePass(this);
    this._fire("gate_map_state", view());
  },
  gate_map_save_run() {
    const run = g.run;
    if (!run || run.phase !== "review") return;
    const after = runEntries(run);
    g.changes = [...diff(g.entries, after).map((c) => ({ ...c, ts: now(), run_id: run.run_id, operator: run.operator, reason: "mapping_run" })), ...g.changes].slice(0, 20);
    g.entries = after;
    g.updated_at = now();
    g.run = null;
    clearTimers();
    this._fire("gate_map_state", view());
  },
  gate_map_discard_run() {
    g.run = null;
    clearTimers();
    this._fire("gate_map_state", view());
  },
};
