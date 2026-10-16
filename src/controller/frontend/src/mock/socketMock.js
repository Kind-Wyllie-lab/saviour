// A stand-in for the socket.io-client singleton, used when VITE_MOCK=1
// (npm run dev:mock) so the frontend renders without a controller.
//
// Implements only the client surface the app actually uses:
//   .on(event, fn) / .off(event[, fn]) / .once(event, fn)
//   .emit(event, payload)          -- routed via ROUTER below
//   .connected                     -- boolean
//   .auth                          -- assignable ({ password })
//   internal events: connect / disconnect / reconnect_attempt
//
// An unrouted emit logs `[mock] unhandled: <event>` -- add a ROUTER entry.
// Mutations mutate the in-memory `state` (fixtures.js) and re-emit the
// relevant *_update so the UI reflects the change.

import { state, reset } from "./fixtures";

const RESPONSE_DELAY_MS = 120;   // fake a little network latency
const HEALTH_TICK_MS = 3000;     // jitter health so charts move
const EXPORT_TICK_MS = 1000;     // a module's export_progress period
const EXPORT_BYTES_PER_TICK = 12_000_000;
const APA_TICK_MS = 200;         // the module's arduino_state period
const APA_LEASE_MS = 1000;       // shock.py SHOCK_LEASE_S

function clone(v) {
  return typeof structuredClone === "function"
    ? structuredClone(v)
    : JSON.parse(JSON.stringify(v));
}

class MockSocket {
  constructor() {
    this._handlers = new Map();       // event -> Set<fn>
    // Report connected synchronously (so `useState(!socket.connected)` in
    // ConnectionOverlay doesn't flash) but still emit the "connect" event on
    // the next tick, after components have subscribed in useEffect.
    this.connected = true;
    this.auth = {};
    this.id = "mock-" + Math.random().toString(36).slice(2, 8);

    setTimeout(() => this._fire("connect"), 30);

    this._healthTimer = setInterval(() => this._tickHealth(), HEALTH_TICK_MS);
    this._exportTimer = setInterval(() => this._tickExports(), EXPORT_TICK_MS);
    this._apaTimer = setInterval(() => this._tickApa(), APA_TICK_MS);

    // Expose for console poking: __mockSocket.reset(), .state, .fire(...)
    if (typeof window !== "undefined") {
      window.__mockSocket = {
        state,
        reset: () => { reset(); this._pushAll(); },
        fire: (e, p) => this._fire(e, p),
        setModuleStatus: (id, status) => {
          if (state.modules[id]) { state.modules[id].status = status; this._fire("modules_update", clone(state.modules)); }
        },
        // Stop arduino_state for `ms` (default 5 s) to see the stale display.
        silenceArduino: (ms = 5000) => { state.apa.silentUntil = Date.now() + ms; },
        get apa() { return state.apa; },
      };
    }
    console.info("%c[SAVIOUR] mock socket active — no backend. window.__mockSocket to poke it.",
      "color:#c60;font-weight:bold");
  }

  // ── socket.io-client surface ───────────────────────────────────────────
  on(event, fn) {
    if (!this._handlers.has(event)) this._handlers.set(event, new Set());
    this._handlers.get(event).add(fn);
    return this;
  }

  once(event, fn) {
    const wrap = (...a) => { this.off(event, wrap); fn(...a); };
    return this.on(event, wrap);
  }

  off(event, fn) {
    if (!this._handlers.has(event)) return this;
    if (fn) this._handlers.get(event).delete(fn);
    else this._handlers.delete(event);
    return this;
  }

  emit(event, payload, ack) {
    const handler = ROUTER[event];
    if (handler) {
      setTimeout(() => {
        try { handler.call(this, payload, ack); }
        catch (err) { console.error(`[mock] ROUTER["${event}"] threw`, err); }
      }, RESPONSE_DELAY_MS);
    } else {
      console.debug("[mock] unhandled:", event, payload ?? "");
    }
    return this;
  }

  connect() { if (!this.connected) { this.connected = true; this._fire("connect"); } return this; }
  disconnect() { if (this.connected) { this.connected = false; this._fire("disconnect", "io client disconnect"); } return this; }

  // ── internals ─────────────────────────────────────────────────────────
  _fire(event, payload) {
    const hs = this._handlers.get(event);
    if (!hs) return;
    for (const fn of [...hs]) {
      try { fn(payload); }
      catch (err) { console.error(`[mock] handler for "${event}" threw`, err); }
    }
  }

  _pushAll() {
    this._fire("modules_update", clone(state.modules));
    this._fire("module_health_update", { module_health: clone(state.health) });
    this._fire("sessions_update", clone(state.sessions));
    this._fire("storage_overview", clone(state.storageOverview));
    this._fire("controller_config_response", { config: clone(state.controllerConfig) });
    this._fire("system_state", clone(state.systemState));
  }

  // Simulated APA rig: the shock pulses on/off while activate_shock keeps
  // renewing its lease (as shock.py does), and the table eases to its RPM.
  _tickApa() {
    const mod = Object.values(state.modules).find((m) => m.type === "apa_arduino");
    if (!mod || mod.status === "OFFLINE") return;
    const r = state.apa;
    const t = Date.now();
    if (r.shockActivated && t > r.holdUntil) r.shockActivated = false;
    if (r.shockActivated && t >= r.nextPulseAt && r.attempted < r.maxShocks) {
      r.attempted += 1;
      r.delivered += 1;
      r.pulseOnUntil = t + r.timeOnS * 1000;
      r.nextPulseAt = r.pulseOnUntil + r.timeOffS * 1000;
    }
    const pulsing = r.shockActivated && t < r.pulseOnUntil;
    const target = r.rotating ? r.rpmSetpoint : 0;
    r.rpm += (target - r.rpm) * 0.08;
    if (t < r.silentUntil) return;
    this._fire("arduino_state", {
      type: "arduino_state",
      module_id: mod.id,
      state: {
        shock_activated: r.shockActivated,
        shock_being_delivered: pulsing,
        grid_live: pulsing,
        attempted_shocks: r.attempted,
        attempted_shocks_from_arduino: 0,
        delivered_shocks: r.delivered,
        max_shocks: r.maxShocks,
        current_ma: r.currentMa,
        time_on_s: r.timeOnS,
        time_off_s: r.timeOffS,
        rpm: +(r.rpm + (r.rotating ? (Math.random() - 0.5) * 0.06 : 0)).toFixed(2),
        rotating: r.rotating,
        target_rpm: r.rpmSetpoint,
        speed_error: null,
      },
    });
  }

  // The export queue snapshot (export_queue.ExportQueue.snapshot) for the
  // simulated just-stopped session: one module copying, the rest waiting.
  _exportSnapshot() {
    const sim = state.exportSim;
    const path = (m) => `${sim.session}/20261013/${m}`;
    const active = [];
    const queued = [];
    sim.order.forEach((m, i) => {
      if (i === sim.index) {
        const [files, total] = sim.sizes[m];
        const done = Math.min(sim.bytes, total);
        active.push({
          module_id: m, export_path: path(m), session: sim.session, attempt: 1,
          since: Date.now() / 1000 - 5,
          progress: {
            files_total: files, files_done: Math.floor((done / total) * files),
            bytes_total: total, bytes_done: done,
            current_file: `${sim.session}_${m}_(0_20261013-151400).ts`,
            export_path: path(m), updated: Date.now() / 1000,
          },
          staged_bytes: total,
        });
      } else if (i > sim.index) {
        queued.push({ module_id: m, export_path: path(m), session: sim.session,
          attempt: 1, position: i - sim.index, staged_bytes: sim.sizes[m][1] });
      }
    });
    const landed = sim.order.slice(0, sim.index).reduce((n, m) => n + sim.sizes[m][1], 0);
    const waves = sim.index < sim.order.length ? { [sim.session]: { bytes_done: landed } } : {};
    return { active, queued, rerun: [], waves, max_concurrent: 1 };
  }

  _tickExports() {
    const sim = state.exportSim;
    if (sim.index >= sim.order.length) return;
    const m = sim.order[sim.index];
    sim.bytes += EXPORT_BYTES_PER_TICK;
    if (sim.bytes >= sim.sizes[m][1]) {
      const s = state.sessions[sim.session];
      s.module_export_states[m] = "complete";
      s.pending_exports = Math.max(0, s.pending_exports - 1);
      s.total_exports_complete += 1;
      sim.index += 1;
      sim.bytes = 0;
      this._fire("sessions_update", clone(state.sessions));
    }
    this._fire("export_queue", this._exportSnapshot());
  }

  _tickHealth() {
    const jitter = (v, amp) => Math.max(0, +(v + (Math.random() - 0.5) * amp).toFixed(1));
    for (const h of Object.values(state.health)) {
      h.cpu_usage = jitter(h.cpu_usage, 8);
      h.cpu_temp = jitter(h.cpu_temp, 1.5);
      h.ptp4l_offset_ns = Math.round(Math.max(0, h.ptp4l_offset_ns + (Math.random() - 0.5) * 1500));
      h.last_heartbeat = Date.now() / 1000;
    }
    state.controllerHealth.cpu_temp = jitter(state.controllerHealth.cpu_temp, 1.5);
    this._fire("module_health_update", { module_health: clone(state.health) });
    this._fire("controller_health_response", clone(state.controllerHealth));
  }
}

// ── request → response routing ──────────────────────────────────────────
// `this` is the MockSocket; call this._fire(responseEvent, data).
const ROUTER = {
  // bootstrap reads
  get_system_state() { this._fire("system_state", clone(state.systemState)); },
  get_modules() { this._fire("modules_update", clone(state.modules)); },
  get_module_health() { this._fire("module_health_update", { module_health: clone(state.health) }); },
  get_controller_health() { this._fire("controller_health_response", clone(state.controllerHealth)); },
  get_controller_info() {
    this._fire("controller_info_response", {
      ip: "10.0.0.1", version: "v0.6.1-mock", hostname: "mock-controller",
    });
  },
  get_sessions() { this._fire("sessions_update", clone(state.sessions)); },
  get_export_queue() { this._fire("export_queue", this._exportSnapshot()); },
  get_compose_jobs() {
    this._fire("compose_jobs", { jobs: Object.values(clone(state.composeJobs)) });
  },
  delete_compose_job(payload) {
    delete state.composeJobs[payload?.job_id];
    this._fire("compose_jobs", { jobs: Object.values(clone(state.composeJobs)) });
  },
  rerun_compose_job(payload) {
    const old = state.composeJobs[payload?.job_id];
    if (!old) return;
    const id = `j_${Date.now()}`;
    state.composeJobs[id] = { ...clone(old), id, state: "queued", stage: "queued",
      progress: 0, error: null, output_rel: null, output_bytes: null,
      created_at: Date.now() / 1000, started_at: null, finished_at: null };
    this._fire("compose_job_accepted", clone(state.composeJobs[id]));
  },
  cancel_compose_job(payload) {
    const j = state.composeJobs[payload?.job_id];
    if (j) { j.state = "cancelled"; j.stage = "cancelled"; j.finished_at = Date.now() / 1000; }
    this._fire("compose_job_update", clone(j));
  },
  get_recording_sessions() { this._fire("recording_sessions", clone(state.sessions)); },
  get_controller_config() { this._fire("controller_config_response", { config: clone(state.controllerConfig) }); },
  get_module_configs() { /* frontend hook ignores the result; no-op */ },
  get_nas_health() { this._fire("nas_health_update", clone(state.storageOverview.nas)); },
  get_storage_overview() { this._fire("storage_overview", clone(state.storageOverview)); },
  get_first_run_state() { this._fire("first_run_state", clone(state.firstRun)); },
  get_experiment_metadata() {
    this._fire("experiment_metadata_response", {
      status: "success", metadata: {}, experiment_name: null,
    });
  },
  get_update_info() {
    this._fire("update_info", {
      running_version: "v0.6.1-mock", latest_version: "v0.6.1-mock",
      update_available: false, staged_version: null,
    });
  },
  get_data_rate_history() { this._fire("data_rate_history", { samples: [] }); },
  get_nas_history() { this._fire("nas_history", { samples: [] }); },
  get_controller_backups() { this._fire("controller_backups", { backups: [] }); },
  get_habitat_config() { this._fire("habitat_config", { grid: { cols: 4, rows: 4 } }); },
  get_debug_data() { this._fire("debug_data", { modules: clone(state.modules) }); },

  // auth
  login(payload) {
    const ok = (payload?.password || "") === "dev" || (payload?.password || "").length >= 4;
    if (ok) this._fire("login_success", { message: "Logged in (mock)" });
    else this._fire("login_error", { error: "Wrong password (mock: any 4+ chars, or 'dev')" });
  },
  change_admin_password() { this._fire("change_password_success", {}); },

  // mutations — mutate state, re-emit the matching update
  save_controller_config(payload) {
    Object.assign(state.controllerConfig, payload?.config || {});
    this._fire("controller_config_response", { config: clone(state.controllerConfig) });
  },
  save_custom_theme(payload) {
    const fe = (state.controllerConfig.frontend ||= { custom_themes: [] });
    fe.custom_themes ||= [];
    const id = (payload?.name || "theme").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "theme";
    fe.custom_themes = [...fe.custom_themes.filter((t) => t.id !== id),
      { id, name: payload.name, light: payload.light, dark: payload.dark }];
    this._fire("custom_theme_saved", { theme: { id } });
    this._fire("controller_config_response", { config: clone(state.controllerConfig) });
  },
  delete_custom_theme(payload) {
    const fe = state.controllerConfig.frontend;
    if (fe?.custom_themes) fe.custom_themes = fe.custom_themes.filter((t) => t.id !== payload?.id);
    this._fire("controller_config_response", { config: clone(state.controllerConfig) });
  },
  save_module_config(payload) {
    const id = payload?.id;
    if (id) state.moduleConfigs[id] = payload.config || {};
    this._fire("module_status", { module_id: id, type: "config_saved" });
  },
  start_recording() {
    for (const m of Object.values(state.modules)) if (m.ready && m.type !== "ttl") m.status = "RECORDING";
    state.systemState.recording = true;
    this._fire("modules_update", clone(state.modules));
    this._fire("create_session_result", { status: "success" });
  },
  stop_recording() {
    for (const m of Object.values(state.modules)) if (m.status === "RECORDING") m.status = "READY";
    state.systemState.recording = false;
    this._fire("modules_update", clone(state.modules));
  },
  check_ready() {
    for (const m of Object.values(state.modules)) {
      if (m.status === "OFFLINE") continue;
      m.ready = !m.hardware_fault;
      m.status = m.ready ? "READY" : "NOT_READY";
    }
    this._fire("modules_update", clone(state.modules));
  },
  // Generic module command. Only the APA rig's commands do anything here.
  send_command(payload) {
    const r = state.apa;
    const t = Date.now();
    switch (payload?.type) {
      case "activate_shock":
        if (r.attempted >= r.maxShocks) break;
        if (!r.shockActivated) { r.shockActivated = true; r.nextPulseAt = t; }
        r.holdUntil = t + APA_LEASE_MS;
        break;
      case "deactivate_shock": r.shockActivated = false; r.pulseOnUntil = 0; break;
      case "start_motor": r.rotating = true; break;
      case "stop_motor": r.rotating = false; break;
      case "reset_pulse_counter": if (!r.shockActivated) { r.attempted = 0; r.delivered = 0; } break;
      default: console.debug("[mock] send_command ignored:", payload);
    }
  },
  remove_module(payload) {
    const id = payload?.module_id || payload;
    delete state.modules[id]; delete state.health[id];
    this._fire("modules_update", clone(state.modules));
  },

  // ── Post-Process compose editor (no real video: tiles are drawn as SVG) ──
  get_session_file_info(payload) {
    const name = payload?.session_name;
    const day = "20261013";
    const files = [];
    for (const cam of MOCK_CAMERAS) {
      files.push({ path: `${day}/${cam.name}/${name}_${cam.name}.ts`, name: `${name}_${cam.name}.ts`, size: 9e8 });
      files.push({ path: `${day}/${cam.name}/${name}_${cam.name}_timestamps.csv`, name: `${name}_${cam.name}_timestamps.csv`, size: 2e6 });
    }
    files.push({ path: `${day}/microphone-1/${name}_mic.flac`, name: `${name}_mic.flac`, size: 4e8 });
    files.push({ path: `${day}/microphone-1/${name}_mic_timestamps.txt`, name: `${name}_mic_timestamps.txt`, size: 1e4 });
    this._fire("session_file_info_response", {
      session_name: name, files, total_bytes: files.reduce((a, f) => a + f.size, 0),
    });
  },
  compose_streams_info(payload) {
    this._fire("compose_streams_info", {
      session_name: payload?.session_name, date_dir: payload?.date_dir || "20261013",
      dates: ["20261013"], cameras: MOCK_CAMERAS, mics: ["microphone-1"],
      mic_devices: MOCK_MICS, suggested_fps: 30, duration_s: MOCK_DAY_S,
      window_start_ns: `${MOCK_T0_MS}000000`, window_end_ns: `${MOCK_T0_MS + MOCK_DAY_S * 1000}000000`,
      playhead_pps: 120, max_strip_px: 48000,
    });
  },
  compose_layout_template(payload) {
    const cams = MOCK_CAMERAS.filter((c) => !payload?.streams || payload.streams.includes(c.name));
    const n = cams.length;
    const cols = Math.ceil(Math.sqrt(n));
    const cellW = Math.round(1920 / cols / 2) * 2;
    const cellH = Math.round(cellW * 9 / 16 / 2) * 2;
    const rows = Math.ceil(n / cols);
    const tiles = cams.map((c, i) => ({
      id: `c${i + 1}`, kind: "camera", source: c.name,
      rect: [(i % cols) * cellW, Math.floor(i / cols) * cellH, cellW, cellH], z: 0,
      options: { fit: "contain", crop: null, rotate: 0, label: true },
    }));
    let height = cellH * rows;
    const audio = payload?.audio_mode;
    const spec = { color: "intensity", fmin_hz: 0, fmax_hz: 96000, fscale: "lin",
      ascale: "log", gain: 2.5, ...(payload?.spectrogram || {}), window_s: null, playhead: 0.5 };
    if (audio === "panel" || audio === "strip") {
      const h = Math.round(height * (audio === "strip" ? 0.2 : 0.5) / 2) * 2;
      tiles.push({ id: "a1", kind: "spectrogram", source: MOCK_MICS[0].id,
        rect: [0, audio === "strip" ? height - h : height, cellW * cols, h],
        z: audio === "strip" ? 1 : 0, options: { ...spec, label: false } });
      if (audio === "panel") height += h;
    }
    this._fire("compose_layout_template", {
      request_id: payload?.request_id,
      layout_doc: {
        canvas: { width: cellW * cols, height, background: "#000000" }, tiles,
        audio_tracks: audio && audio !== "none" ? [MOCK_MICS[0].id] : [],
      },
    });
  },
  compose_preview(payload) {
    const doc = payload?.layout_doc;
    if (!doc) { this._fire("compose_preview_ready", { request_id: payload?.request_id, error: "mock: no layout" }); return; }
    const tiles = {};
    for (const t of doc.tiles) tiles[t.id] = mockTileImage(t);
    this._fire("compose_preview_ready", {
      request_id: payload?.request_id, tiles, scale: 1, layout_doc: doc,
      t_ns: payload?.preview_t_ns ?? null,
      image: mockFrameImage(doc),
    });
  },
  compose_session_video(payload) {
    const id = `j_${Date.now()}`;
    state.composeJobs[id] = { id, spec: clone(payload), state: "queued", stage: "queued",
      progress: 0, output_rel: null, error: null, warnings: [],
      created_at: Date.now() / 1000, started_at: null, finished_at: null, output_bytes: null };
    this._fire("compose_job_accepted", clone(state.composeJobs[id]));
  },
};

const MOCK_T0_MS = Date.UTC(2026, 9, 13, 14, 0, 0);
const MOCK_DAY_S = 3 * 3600 + 25 * 60;
const seg = (a, b) => [`${MOCK_T0_MS + a * 1000}000000`, `${MOCK_T0_MS + b * 1000}000000`];
const MOCK_CAMERAS = [
  { name: "camera-module-a1b2", width: 1456, height: 1088, fps: 30.0,
    segments: [seg(0, 3600), seg(3600, 7200), seg(7200, MOCK_DAY_S)] },
  { name: "camera-module-c3d4", width: 1280, height: 720, fps: 29.97,
    segments: [seg(0, 3600), seg(3600, 5100), seg(5900, MOCK_DAY_S)] },
  { name: "hailo-camera-3606", width: 1280, height: 720, fps: 30.0,
    segments: [seg(0, 3600), seg(3600, 7200), seg(7200, MOCK_DAY_S)] },
];
const MOCK_MICS = [
  { id: "microphone-1/trial_microphone-1_1_24F319046A4B3C2D", folder: "microphone-1",
    device: "trial_microphone-1_1_24F319046A4B3C2D", label: "microphone-1 · 3C2D" },
  { id: "microphone-1/trial_microphone-1_1_24F3190400001111", folder: "microphone-1",
    device: "trial_microphone-1_1_24F3190400001111", label: "microphone-1 · 1111" },
];
const MOCK_HUES = { "camera-module-a1b2": 210, "camera-module-c3d4": 140, "hailo-camera-3606": 30 };

function svgUrl(svg) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function mockTileSvg(t, w, h) {
  const [, , tw, th] = t.rect;
  w = w || tw; h = h || th;
  if (t.kind === "spectrogram") {
    const bars = Array.from({ length: 60 }, (_, i) => {
      const bh = (0.2 + 0.7 * Math.abs(Math.sin(i * 1.7))) * h;
      return `<rect x="${(i * w) / 60}" y="${h - bh}" width="${w / 80}" height="${bh}" fill="#e8c34a" opacity="0.7"/>`;
    }).join("");
    const px = (t.options?.playhead ?? 0.5) * w;
    return `<rect width="${w}" height="${h}" fill="#141014"/>${bars}`
      + `<line x1="${px}" x2="${px}" y1="0" y2="${h}" stroke="#ffd700" stroke-width="3" stroke-dasharray="14 9"/>`;
  }
  const hue = MOCK_HUES[t.source] ?? 280;
  return `<rect width="${w}" height="${h}" fill="hsl(${hue},40%,28%)"/>`
    + `<circle cx="${w / 2}" cy="${h / 2}" r="${Math.min(w, h) / 4}" fill="hsl(${hue},55%,55%)"/>`
    + (t.options?.label ? `<rect width="${Math.min(w, 14 * t.source.length + 12)}" height="34" fill="#000"/>`
      + `<text x="6" y="24" font-family="sans-serif" font-size="20" fill="#fff">${t.source}</text>` : "");
}

function mockTileImage(t) {
  const [, , w, h] = t.rect;
  return svgUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${mockTileSvg(t)}</svg>`);
}

function mockFrameImage(doc) {
  const { width, height, background } = doc.canvas;
  const order = doc.tiles.map((t, i) => [t, i]).sort((a, b) => (a[0].z - b[0].z) || (a[1] - b[1]));
  const body = order.map(([t]) => `<g transform="translate(${t.rect[0]},${t.rect[1]})">${mockTileSvg(t)}</g>`).join("");
  return svgUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="${background}"/>${body}</svg>`);
}

// No module-level side effects: the singleton is constructed by socket.jsx
// only when VITE_MOCK=1, so this whole module tree-shakes out of a real build.
export function createMockSocket() {
  return new MockSocket();
}
