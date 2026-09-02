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

    // Expose for console poking: __mockSocket.reset(), .state, .fire(...)
    if (typeof window !== "undefined") {
      window.__mockSocket = {
        state,
        reset: () => { reset(); this._pushAll(); },
        fire: (e, p) => this._fire(e, p),
        setModuleStatus: (id, status) => {
          if (state.modules[id]) { state.modules[id].status = status; this._fire("modules_update", clone(state.modules)); }
        },
      };
    }
    // eslint-disable-next-line no-console
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
      // eslint-disable-next-line no-console
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
  remove_module(payload) {
    const id = payload?.module_id || payload;
    delete state.modules[id]; delete state.health[id];
    this._fire("modules_update", clone(state.modules));
  },
};

// No module-level side effects: the singleton is constructed by socket.jsx
// only when VITE_MOCK=1, so this whole module tree-shakes out of a real build.
export function createMockSocket() {
  return new MockSocket();
}
