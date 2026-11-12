// Mock-mode stand-in for the habitat controller's gate-mapping service
// (variants/habitat/gate_map_service.py), so the Gates page can be clicked
// through with `VITE_VARIANT=habitat npm run dev:mock`. The mapping run
// follows MappingRun (gate_map.py): the first tag read becomes the probe,
// other tags are ignored, reads stop counting once a gate has settled, a
// confirmed unit takes its serial from the last bus scan, and the review
// runs the same checks as review_entries().
//
// The simulated bus is a list of antennas (reader units). By default it is
// the habitat simulator's wiring (habitatSim/), and the Gates page's
// "Simulated bus" panel (MockBusPanel.jsx) plays the probe by hand: click
// the antenna you put the tag on. You can paste your own antenna list,
// unplug an antenna (no reads, no scan answer) or swap its decoder (new
// serial). "Answer prompts automatically" brings back the old behaviour:
// each gate is read a moment after it's prompted, sometimes by two units or
// by a unit that's already mapped, to show the checks. Live reads on the
// dashboard follow the simulated rats either way.

import { params, simWorld, recentCall } from "./habitatSim/index";
import { BOXES, COLUMNS, RFID_ID, STRAY_MOTH, micId, mothSerial } from "./habitatSim/fleet";

const SETTLE_S = 1.5;
const PROBE = "000807DC1F";
const ANIMAL = "000807DC2A";   // a tag that isn't the probe (an animal)
const MODULE = RFID_ID;
const STORE_KEY = "saviour.mock.gateMapBus";
const MAX_ADDRESS = 255;

const now = () => Date.now() / 1000;
const serialFor = (address) => `SN${String(address).padStart(6, "0")}`;
const keyFor = (address) => `${MODULE}:${address}`;

// ── Antenna list ───────────────────────────────────────────────────────────

function simAntennas() {
  const addrs = [...new Set(Object.values(simWorld().trueUnit))].sort((a, b) => a - b);
  const spare = addrs.length ? addrs[addrs.length - 1] + 1 : 1;
  return [
    ...addrs.map((address) => ({ address, label: "", serial: serialFor(address), online: true })),
    // On the bus but at no gate, so the review has an unmapped unit to report.
    { address: spare, label: "spare", serial: serialFor(spare), online: true },
  ];
}

/** Parse a pasted antenna list: one per line, `address`, `address label`,
 *  `address, label, serial` or just `label` (an address is then given).
 *  Blank lines and `#` comments are skipped. Returns {antennas} or {error}. */
export function parseAntennaList(text) {
  const rows = [];
  const lines = String(text ?? "").split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/#.*/, "").trim();
    if (!line) continue;
    // Commas, tabs or semicolons separate the fields when there are any (so
    // a label may contain spaces); otherwise spaces do.
    const parts = (/[,\t;]/.test(line) ? line.split(/\s*[,\t;]\s*/) : line.split(/\s+/)).filter(Boolean);
    const num = /^\d+$/.test(parts[0]) ? Number(parts[0]) : null;
    if (num !== null && (num < 1 || num > MAX_ADDRESS)) {
      return { error: `Line ${i + 1}: address ${num} is outside 1-${MAX_ADDRESS}` };
    }
    rows.push({
      line: i + 1, address: num,
      label: (num === null ? parts.join(" ") : parts.slice(1, 2).join("")) || "",
      serial: num === null ? null : parts[2] ?? null,
    });
  }
  if (!rows.length) return { error: "The list is empty" };
  const used = new Map();
  for (const r of rows) {
    if (r.address === null) continue;
    if (used.has(r.address)) {
      return { error: `Address ${r.address} is listed twice (lines ${used.get(r.address)} and ${r.line})` };
    }
    used.set(r.address, r.line);
  }
  const labels = new Map();
  for (const r of rows) {
    if (!r.label) continue;
    const k = r.label.toLowerCase();
    if (labels.has(k)) {
      return { error: `"${r.label}" is listed twice (lines ${labels.get(k)} and ${r.line})` };
    }
    labels.set(k, r.line);
  }
  let next = 1;
  const antennas = rows.map((r) => {
    let address = r.address;
    if (address === null) {
      while (used.has(next)) next++;
      address = next;
      used.set(address, r.line);
    }
    if (address > MAX_ADDRESS) return null;
    return { address, label: r.label, serial: r.serial || serialFor(address), online: true };
  });
  if (antennas.includes(null)) return { error: `More than ${MAX_ADDRESS} antennas` };
  return { antennas: antennas.sort((a, b) => a.address - b.address) };
}

function loadStoredAntennas() {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    const list = raw ? JSON.parse(raw) : null;
    return Array.isArray(list) && list.length ? list : null;
  } catch {
    return null;
  }
}

function storeAntennas(list) {
  try {
    if (list) window.localStorage.setItem(STORE_KEY, JSON.stringify(list));
    else window.localStorage.removeItem(STORE_KEY);
  } catch {
    // Private window or blocked storage: the list lasts until a reload.
  }
}

// ── State ──────────────────────────────────────────────────────────────────

function simEntries(layout) {
  // Every fitted gate mapped to its unit on the simulated bus, except a few
  // left unmapped (their reads show as "from unmapped units"), one closed,
  // one faulty (habitat_sim.json rfid).
  const world = simWorld();
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
        unit_serial: serialFor(unit), mapped_at: now() - 86400, run_id: "mock0" };
    }
  }
  return entries;
}

function initialState() {
  const layout = JSON.parse(JSON.stringify(simWorld().layout));
  const stored = loadStoredAntennas();
  return {
    layout,
    // Your own antenna list starts with an empty map: the simulator's map
    // refers to the simulator's units.
    entries: stored ? {} : simEntries(layout),
    updated_at: stored ? null : now() - 86400,
    run: null,
    walkthrough: false,
    bus: null,
    bus_scanned_at: null,
    hits: [],
    changes: [],
    antennas: stored ?? simAntennas(),
    custom: !!stored,
    manual: true,
    tagMode: "probe",
    busError: null,
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

// Mirror of gate_map.review_entries().
function review(entries) {
  const byUnit = mappedUnits(entries);
  const shared = Object.entries(byUnit).filter(([, gs]) => gs.length > 1)
    .sort(([a], [b]) => a.localeCompare(b)).map(([unit, gates]) => ({ unit, gates }));
  if (!g.bus) {
    return { scanned: false, shared_units: shared, on_bus_not_mapped: [], mapped_not_on_bus: [], serial_changed: [] };
  }
  const bus = Object.fromEntries(g.bus.map((b) => [b.unit, b]));
  const serialChanged = [];
  for (const [gate, e] of Object.entries(entries).sort(([a], [b]) => a.localeCompare(b))) {
    if (e?.status !== "mapped") continue;
    const b = bus[`${e.module_id}:${e.unit}`];
    if (b && b.serial && e.unit_serial && b.serial !== e.unit_serial) {
      serialChanged.push({ gate, unit: b.unit, mapped_serial: e.unit_serial, bus_serial: b.serial });
    }
  }
  return {
    scanned: true,
    shared_units: shared,
    on_bus_not_mapped: Object.keys(bus).filter((k) => !byUnit[k]).sort(),
    mapped_not_on_bus: Object.entries(byUnit).filter(([k]) => !bus[k])
      .sort(([a], [b]) => a.localeCompare(b)).map(([unit, gates]) => ({ unit, gates })),
    serial_changed: serialChanged,
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

// What the Simulated bus panel shows: each antenna with the gates it is in
// the map as it stands (the run's preview while a run is open).
function busView() {
  const run = g.run;
  const byUnit = mappedUnits(run ? runEntries(run) : g.entries);
  const capture = run?.phase === "gate" ? run.capture : null;
  return {
    antennas: g.antennas.map((a) => ({ ...a, key: keyFor(a.address), gates: byUnit[keyFor(a.address)] ?? [],
      reads: capture?.counts[keyFor(a.address)] ?? 0 })),
    custom: g.custom,
    manual: g.manual,
    tag_mode: g.tagMode,
    probe_tag: PROBE,
    animal_tag: ANIMAL,
    run: run ? { phase: run.phase, gate: run.phase === "gate" ? run.scope[run.index] : null,
      probe_tag: run.probe_tag, settled: !!capture?.settled, first_ts: capture?.first_ts ?? null } : null,
    settle_s: SETTLE_S,
    error: g.busError,
  };
}

function emit(sock) {
  sock._fire("gate_map_state", view());
  sock._fire("mock_bus_state", busView());
}

function later(sock, ms, fn) {
  g.timers.push(setTimeout(() => { fn(); emit(sock); }, ms));
}

function clearTimers() {
  g.timers.forEach(clearTimeout);
  g.timers = [];
}

function hit(sock, unit, tag = PROBE) {
  const h = { module_id: MODULE, unit, tag, ts: now(),
    gates: mappedUnits(g.entries)[keyFor(unit)] ?? [], probe: tag === g.run?.probe_tag };
  g.hits = [h, ...g.hits].slice(0, 20);
  sock._fire("gate_map_hit", h);
}

// A tag read on one antenna, as MappingRun.on_read() takes it.
function busRead(sock, address, tag) {
  const run = g.run;
  if (run?.phase === "probe") {
    run.probe_tag = tag;
    run.phase = "gate";
    run.index = 0;
    prompt(run);
  } else if (run?.phase === "gate" && tag === run.probe_tag && !run.capture.settled) {
    const c = run.capture;
    const key = keyFor(address);
    if (!c.first) {
      c.first = key;
      c.first_ts = now();
      later(sock, SETTLE_S * 1000, () => { if (g.run === run && run.capture === c) c.settled = true; });
    }
    c.counts[key] = (c.counts[key] ?? 0) + 1;
  }
  hit(sock, address, tag);
}

// "Answer prompts automatically": the probe passes the current gate.
function simulatePass(sock) {
  const run = g.run;
  if (g.manual || !run || run.phase !== "gate") return;
  later(sock, 1500 + Math.random() * 1500, () => {
    if (g.run !== run || run.phase !== "gate") return;
    const online = g.antennas.filter((a) => a.online).map((a) => a.address);
    if (!online.length) return;
    const roll = Math.random();
    const units = [];
    const mapped = Object.values(runEntries(run)).filter((e) => e.status === "mapped");
    if (roll < 0.12 && mapped.length) {
      units.push(mapped[0].unit);              // already mapped elsewhere
    } else {
      // The gate's own unit on the simulated bus, or a random antenna for
      // a gate the simulator doesn't know (or your own list).
      const own = g.custom ? null : simWorld().trueUnit[run.scope[run.index]];
      units.push(own ?? online[Math.floor(Math.random() * online.length)]);
      const other = online.find((u) => u !== units[0]);
      if (roll > 0.88 && other) units.push(other);   // a neighbour also reads it
    }
    for (const u of units) busRead(sock, u, run.probe_tag);
    busRead(sock, units[0], run.probe_tag);
    busRead(sock, units[0], run.probe_tag);
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
    emit(this);
  },
  gate_map_keepalive() {},
  gate_map_scan() {
    g ??= initialState();
    later(this, 800, () => {
      g.bus = g.antennas.filter((a) => a.online).map((a) => ({
        unit: keyFor(a.address), module_id: MODULE, address: a.address, serial: a.serial, firmware: "800",
      }));
      g.bus_scanned_at = now();
    });
  },
  gate_map_walkthrough(p) {
    g.walkthrough = !!p?.enabled;
    if (g.walkthrough) {
      g.hits = [];
      const tick = () => {
        if (!g.walkthrough || g.manual) return;
        const mapped = Object.values(g.entries).filter((e) => e.status === "mapped");
        const pick = Math.random() < 0.8 && mapped.length ? mapped[Math.floor(Math.random() * mapped.length)].unit : 99;
        hit(this, pick, "1122334455");
        g.timers.push(setTimeout(tick, 2500));
      };
      g.timers.push(setTimeout(tick, 1200));
    }
    emit(this);
  },
  gate_map_save_layout(p) {
    if (g.run) { this._fire("gate_map_error", { error: "Finish or discard the mapping run before changing the layout" }); return; }
    g.layout = p.layout;
    emit(this);
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
    emit(this);
    if (!g.manual) {
      later(this, 1500, () => {
        const first = g.antennas.find((a) => a.online);
        if (g.run?.phase !== "probe" || !first) return;
        busRead(this, first.address, PROBE);
        simulatePass(this);
      });
    }
  },
  gate_map_action(p) {
    const run = g.run;
    if (!run) return;
    clearTimers();
    const gate = run.scope[run.index];
    const serialOf = (key) => g.bus?.find((b) => b.unit === key)?.serial ?? null;
    const entry = (status, key) => {
      const [module_id, unit] = key ? [key.slice(0, key.lastIndexOf(":")), Number(key.slice(key.lastIndexOf(":") + 1))] : [null, null];
      return { gate, status, module_id, unit, unit_serial: key ? serialOf(key) : null, mapped_at: now(), run_id: run.run_id };
    };
    const iss = issue(run);
    switch (p.action) {
      case "back":
        if (run.phase === "review") { run.phase = "gate"; run.index = run.scope.length - 1; }
        else if (run.phase === "gate" && run.index > 0) run.index -= 1;
        else { this._fire("gate_map_error", { error: "Already at the first gate" }); return; }
        prompt(run); simulatePass(this);
        emit(this);
        return;
      case "retry":
        prompt(run); simulatePass(this);
        emit(this);
        return;
      case "confirm": {
        if (!run.capture.settled) { this._fire("gate_map_error", { error: "No antenna has read the probe at this gate yet" }); return; }
        let key = run.capture.first;
        if (iss?.kind === "multiple") {
          if (!p.unit || !(p.unit in run.capture.counts)) { this._fire("gate_map_error", { error: "Several units read the probe: pick one" }); return; }
          key = p.unit;
        }
        const others = (mappedUnits(runEntries(run))[key] ?? []).filter((x) => x !== gate);
        if (others.length) {
          if (p.resolve === "move") for (const other of others) run.results[other] = null;
          else if (p.resolve !== "share") { this._fire("gate_map_error", { error: `${key} is already ${others.join(", ")}: move it or share it` }); return; }
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
    emit(this);
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
    emit(this);
  },
  gate_map_discard_run() {
    g.run = null;
    clearTimers();
    emit(this);
  },

  // ── Simulated bus panel (mock only; MockBusPanel.jsx) ──
  mock_bus_get() {
    g ??= initialState();
    this._fire("mock_bus_state", busView());
  },
  mock_bus_read(p) {
    g ??= initialState();
    const a = g.antennas.find((x) => x.address === Number(p?.address));
    if (!a || !a.online) return;
    busRead(this, a.address, g.tagMode === "animal" ? ANIMAL : PROBE);
    emit(this);
  },
  mock_bus_set(p) {
    g ??= initialState();
    if (typeof p?.manual === "boolean") {
      g.manual = p.manual;
      if (!g.manual && g.run?.phase === "gate" && !g.run.capture.first) simulatePass(this);
      if (!g.manual && g.run?.phase === "probe") {
        later(this, 800, () => {
          const first = g.antennas.find((a) => a.online);
          if (g.run?.phase === "probe" && first) { busRead(this, first.address, PROBE); simulatePass(this); }
        });
      }
    }
    if (p?.tag_mode === "probe" || p?.tag_mode === "animal") g.tagMode = p.tag_mode;
    emit(this);
  },
  mock_bus_antenna(p) {
    const a = g.antennas.find((x) => x.address === Number(p?.address));
    if (!a) return;
    if (typeof p.online === "boolean") a.online = p.online;
    if (p.swap) {
      // A different decoder on the same address: a new serial.
      a.serial = `SN${String(100000 + Math.floor(Math.random() * 899999))}`;
    }
    if (g.custom) storeAntennas(g.antennas);
    emit(this);
  },
  mock_bus_set_list(p) {
    if (g.run) { g.busError = "Finish or discard the mapping run before changing the antenna list"; emit(this); return; }
    const parsed = parseAntennaList(p?.text);
    if (parsed.error) { g.busError = parsed.error; emit(this); return; }
    clearTimers();
    g.antennas = parsed.antennas;
    g.custom = true;
    g.entries = {};
    g.updated_at = null;
    g.bus = null;
    g.bus_scanned_at = null;
    g.changes = [];
    g.busError = null;
    storeAntennas(g.antennas);
    emit(this);
  },
  mock_bus_reset() {
    clearTimers();
    storeAntennas(null);
    const { manual, tagMode } = g ?? {};
    g = initialState();
    if (typeof manual === "boolean") g.manual = manual;
    if (tagMode) g.tagMode = tagMode;
    emit(this);
  },
  mock_bus_clear_map() {
    if (g.run) { g.busError = "Finish or discard the mapping run first"; emit(this); return; }
    g.entries = {};
    g.updated_at = null;
    g.changes = [];
    g.busError = null;
    emit(this);
  },
  mock_bus_dismiss() {
    g.busError = null;
    emit(this);
  },
};
