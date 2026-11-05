// Mock-mode stand-in for the habitat controller's gate-mapping service
// (variants/habitat/gate_map_service.py), so the Gates page can be clicked
// through with `VITE_VARIANT=habitat npm run dev:mock`. A rough simulation,
// not a port: a mapping run registers a probe a moment after it starts, and
// each gate is "read" by a fake unit shortly after it's prompted (sometimes
// two units, sometimes a unit that's already mapped, to show the checks).

const SETTLE_S = 1.5;
const PROBE = "000807DC1F";
const MODULE = "rfid-module-mock";

function generateLayout(cols = 4, rows = 4, perWall = 4) {
  const id = (c, r) => `${String.fromCharCode(65 + c)}${r + 1}`;
  const boxes = [];
  const walls = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      boxes.push({ id: id(c, r), col: c, row: r });
      if (c + 1 < cols) walls.push({ id: id(c, r) + id(c + 1, r), boxes: [id(c, r), id(c + 1, r)], orientation: "vertical" });
      if (r + 1 < rows) walls.push({ id: id(c, r) + id(c, r + 1), boxes: [id(c, r), id(c, r + 1)], orientation: "horizontal" });
    }
  }
  const gates = walls.flatMap((w) => Array.from({ length: perWall }, (_, i) => (
    { id: `${w.id}_${"abcd"[i]}`, wall: w.id, index: i, fitted: true })));
  return { version: 1, cols, rows, gates_per_wall: perWall, boxes, walls, gates };
}

const now = () => Date.now() / 1000;

function initialState() {
  const layout = generateLayout();
  // As an example, the walls between the bottom row's boxes (A4B4, B4C4,
  // C4D4) have no gates fitted.
  for (const gate of layout.gates) if (/^[A-Z]4[A-Z]4$/.test(gate.wall)) gate.fitted = false;
  const entries = {};
  layout.gates.slice(0, 10).forEach((g, i) => {
    entries[g.id] = { gate: g.id, status: "mapped", module_id: MODULE, unit: i + 1,
      unit_serial: `SN${String(i + 1).padStart(6, "0")}`, mapped_at: now() - 86400, run_id: "mock0" };
  });
  entries[layout.gates[10].id] = { gate: layout.gates[10].id, status: "closed", module_id: null, unit: null, unit_serial: null, mapped_at: now() - 86400, run_id: "mock0" };
  entries[layout.gates[11].id] = { gate: layout.gates[11].id, status: "faulty", module_id: null, unit: null, unit_serial: null, mapped_at: now() - 86400, run_id: "mock0" };
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
    nextUnit: 11,
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
      units.push(g.nextUnit++);
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

export const gateMapRoutes = {
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
