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
import { gateMapRoutes } from "./gateMapMock";
import { placementRoutes } from "./placementMock";

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

  _shockEvent(event, reason) {
    state.apa.lastEvent = { time_utc: new Date().toISOString(), event, reason, by: "operator" };
    this._fireShockState();
  }

  _fireShockState() {
    const r = state.apa;
    this._fire("apa_shock_state", {
      mode: r.mode ?? "manual", armed: !!r.armed, shocking: false, in_zone: false, zone_report_fresh: false,
      in_zone_s: 0, camera: null, session_recording: false, pulses_last_min: 0, capped: false,
      settings: { entry_latency_s: 0.5, zone_state_timeout_s: 0.5, max_pulses_per_min: 20 },
      last_event: r.lastEvent ?? null,
    });
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
// System -> Sync tab (backend: src/controller/sync_overview.py). Offsets are a
// deterministic wobble per module so the sparklines have shape; one module
// drifts above the 50 µs gate near the end to show the warning state.
function mockSyncOverview(windowS = 3600) {
  const t1 = Date.now() / 1000;
  const window = Math.max(300, Math.min(Number(windowS) || 3600, 86400));
  const step = Math.max(30, window / 240);
  const modules = {};
  Object.entries(state.health).forEach(([id, h], k) => {
    const history = [];
    for (let t = t1 - window; t <= t1; t += step) {
      const base = Math.sin(t / 600 + k) * (800 + k * 600);
      const spike = k === 1 && t > t1 - window * 0.08 ? 58000 : 0;
      const v = base + spike;
      const phc = Math.cos(t / 300 + k) * 300;
      history.push([Math.round(t), v, v - 400 - k * 200, v + 400 + k * 200, phc, phc - 150, phc + 150]);
    }
    const last = history[history.length - 1];
    modules[id] = {
      ptp4l_offset_ns: last[1], ptp4l_offset_ns_min: last[2], ptp4l_offset_ns_max: last[3],
      phc2sys_offset_ns: last[4], phc2sys_offset_ns_min: last[5], phc2sys_offset_ns_max: last[6],
      ptp4l_freq: -1200 + k * 300, phc2sys_freq: 24000 + k * 1700,
      ptp_restarts: k === 1 ? 2 : 0, last_ptp_restart: k === 1 ? t1 - 5400 : null,
      last_heartbeat: h.last_heartbeat ?? t1,
      history,
    };
  });
  const cameraIds = Object.keys(state.modules).filter((id) => id.includes("camera"));
  const cameras = {};
  cameraIds.forEach((id, i) => {
    cameras[id] = {
      online: state.modules[id]?.online !== false,
      recording: state.health[id]?.recording ?? false,
      framesync_enabled: true,
      sync_mode: i === 0 ? "server" : "client",
      expected_role: i === 0 ? "server" : "client",
      pending_sync_mode: null,
      fps: 30, sensor_mode_index: 1,
      waiting_for_server: false, deferred: false,
    };
  });
  const ctrlHistory = [];
  for (let t = Math.max(t1 - 3600, t1 - window); t <= t1; t += step) {
    const v = Math.sin(t / 90) * 40;
    ctrlHistory.push([Math.round(t), v, v - 15, v + 15]);
  }
  const recent_verdicts = Object.entries(state.sessions)
    .filter(([, s]) => s.framesync_verdict?.status)
    .map(([name, s]) => ({ session_name: name, ...s.framesync_verdict }));
  if (!recent_verdicts.length) {
    recent_verdicts.push({
      session_name: "mock_session_20261109", status: "amber", scope: "session",
      generated_at: new Date((t1 - 7200) * 1000).toISOString(), sync_mode: "server/client",
      phase_lock_evaluated: true,
      reasons: ["camera-module-c3d4: 0.4% frames dropped"],
      worst: { ptp4l_p95_ns: 4200, detrended_p95_us: 14, max_dropped_frac: 0.004, span_s: 3600 },
      counts: { cameras: 2 }, report_rel: null,
    });
  }
  return {
    generated_at: t1, window_s: window,
    ptp_row_fields: ["t", "ptp4l", "ptp4l_min", "ptp4l_max", "phc2sys", "phc2sys_min", "phc2sys_max"],
    thresholds: { ptp_start_gate_us: 50, ptp_threshold_us: 1000 },
    controller: {
      role: "master", status: "synchronized", ptp4l_service: "active",
      phc2sys_service: "active", phc2sys_offset_ns: ctrlHistory.at(-1)?.[1] ?? 0,
      phc2sys_freq: 21345, last_sync: t1, interface: "eth0", history: ctrlHistory,
    },
    modules,
    framesync: { transmitter: cameraIds[0] ?? null, cameras },
    recent_verdicts,
  };
}

// System -> Network tab (backend: src/controller/network_overview.py). Shows
// every kind of host the controller's DHCP server can see: registered modules
// (one offline), an unprovisioned Pi, a PC, an expired lease, a NAS on a
// static IP and a stray device with a randomised MAC.
const MOCK_RELEASED = new Set();

function mockNetworkOverview() {
  const t1 = Date.now() / 1000;
  const rtt = (base, lossEvery = 0, seed = 1) => {
    const h = [];
    for (let i = 0; i < 120; i++) {
      const t = Math.round(t1 - 3600 + i * 30);
      const lost = lossEvery && (i * seed) % lossEvery === 0;
      h.push([t, lost ? null : +(base + Math.abs(Math.sin(i / 7 + seed)) * base * 0.8).toFixed(3)]);
    }
    return h;
  };
  const ping = (history) => {
    const replies = history.slice(-30).map(([, r]) => r).filter((r) => r != null).sort((a, b) => a - b);
    const last = history[history.length - 1];
    return {
      last_ms: last[1], last_at: last[0],
      last_reply_at: [...history].reverse().find(([, r]) => r != null)?.[0] ?? null,
      median_ms: replies[Math.floor(replies.length / 2)] ?? null,
      max_ms: replies[replies.length - 1] ?? null,
      loss_pct: +(100 * (30 - replies.length) / 30).toFixed(1), samples: 30, history,
    };
  };
  const link = (over = {}) => ({
    iface: "eth0", mac: null, operstate: "up", speed_mbps: 1000, duplex: "full", mtu: 1500,
    carrier_changes: 1, carrier_changes_1h: 0, rx_errors_1h: 0, tx_errors_1h: 0,
    rx_dropped_1h: 0, tx_dropped_1h: 0, rx_bytes_1h: 12e6, tx_bytes_1h: 9.4e9, window_s: 3600, ...over,
  });
  const hb = { mean_gap_s: 30.02, max_gap_s: 30.4, jitter_s: 0.12, beats: 30 };
  const lease = (hours) => ({ expiry: Math.round(t1 + hours * 3600), state: hours > 0 ? "active" : "expired" });
  const modIds = Object.keys(state.modules);
  const hosts = modIds.map((id, i) => {
    const m = state.modules[id];
    const offline = m.online === false;
    const mac = `2c:cf:67:${(0x10 + i).toString(16)}:${(0x20 + i).toString(16)}:${(0x30 + i).toString(16)}`;
    return {
      ip: m.ip && m.ip !== "10.0.0.1" ? m.ip : `10.0.0.${150 + i}`, mac, hostname: `${(m.type || "module").replace(/_/g, "-")}-module-${id.slice(-4)}`,
      lease: lease(6 + i), neigh_state: offline ? "FAILED" : "REACHABLE",
      vendor: "Raspberry Pi", randomised: false, kind: "module", reachable: !offline,
      ping: ping(offline ? rtt(0.2, 1) : rtt(0.2 + i * 0.05, i === 1 ? 23 : 0, i + 1)),
      module: {
        id, name: m.name || id, type: m.type, online: !offline, status: m.status, version: m.version,
        last_heartbeat: offline ? t1 - 1800 : t1 - 4 - i * 3,
        link: offline ? null : link(i === 1 ? { speed_mbps: 100, rx_errors_1h: 14, carrier_changes_1h: 2 } : { mac }),
        heartbeat: offline ? null : (i === 1 ? { ...hb, max_gap_s: 71.2, jitter_s: 7.4 } : hb),
      },
    };
  });
  hosts.push(
    { ip: "10.0.0.20", mac: "00:11:32:aa:bb:01", hostname: null, lease: null, neigh_state: "STALE",
      vendor: null, randomised: false, kind: "share", reachable: true, ping: ping(rtt(0.4, 0, 3)), module: null },
    { ip: "10.0.0.160", mac: "2c:cf:67:aa:bb:cc", hostname: "saviour-unprov-bbcc", lease: lease(11),
      neigh_state: "REACHABLE", vendor: "Raspberry Pi", randomised: false, kind: "unprovisioned",
      reachable: true, ping: ping(rtt(0.25, 0, 4)), module: null },
    { ip: "10.0.0.161", mac: "d8:3a:dd:11:22:33", hostname: "raspberrypi", lease: lease(-0.4),
      neigh_state: null, vendor: "Raspberry Pi", randomised: false, kind: "raspberry_pi",
      reachable: false, ping: ping(rtt(0.3, 1)), module: null },
    { ip: "10.0.0.155", mac: "58:ef:68:7e:e3:11", hostname: "LAB-PC-3051", lease: lease(9),
      neigh_state: "REACHABLE", vendor: null, randomised: false, kind: "other", reachable: true,
      ping: ping(rtt(1.1, 0, 5)), module: null },
    { ip: "10.0.0.170", mac: "9a:3c:00:51:7e:02", hostname: null, lease: lease(2),
      neigh_state: "REACHABLE", vendor: null, randomised: true, kind: "other", reachable: true,
      ping: ping(rtt(3.5, 17, 6)), module: null },
  );
  for (let i = hosts.length - 1; i >= 0; i--) if (MOCK_RELEASED.has(hosts[i].ip)) hosts.splice(i, 1);
  const counts = { module: 0, share: 0, saviour_unregistered: 0, unprovisioned: 0, raspberry_pi: 0, other: 0 };
  hosts.forEach((h) => { counts[h.kind] += 1; });
  return {
    generated_at: t1,
    controller: { ip: "10.0.0.1", network: "10.0.0.0/16", iface: "eth0", link: link({ mac: "2c:cf:67:59:d3:c0" }) },
    interfaces: [
      { name: "eth0", operstate: "UP", mac: "2c:cf:67:59:d3:c0", ipv4: ["10.0.0.1/16"], default_route: null, role: "rig" },
      { name: "wlan0", operstate: "UP", mac: "2c:cf:67:59:d3:c1", ipv4: ["10.126.18.7/16"], default_route: "10.126.255.254", role: "uplink" },
    ],
    dhcp: { start: "10.0.0.128", end: "10.0.0.255", size: 128, in_use: hosts.filter((h) => h.lease?.state === "active").length,
            lease_time: "12h", netmask: "255.255.0.0", gateway: "10.0.0.1", dns: null, authoritative: true },
    dhcp_conf_found: true,
    share: { kind: "nas", ip: "10.0.0.20", path: "saviour", health: { status: "ok", error: null } },
    firewall: { ok: true, detail: "Firewall active on wlan0", exposed: [] },
    hosts, counts, dhcp_release_available: true,
  };
}

const ROUTER = {
  // Habitat Gates page (RFID gate mapping), see gateMapMock.js.
  ...gateMapRoutes,
  ...placementRoutes,
  // bootstrap reads
  get_system_state() { this._fire("system_state", clone(state.systemState)); },
  get_modules() { this._fire("modules_update", clone(state.modules)); },
  get_module_health() { this._fire("module_health_update", { module_health: clone(state.health) }); },
  get_controller_health() { this._fire("controller_health_response", clone(state.controllerHealth)); },
  get_network_overview() { this._fire("network_overview", mockNetworkOverview()); },
  release_dhcp_lease({ ip, mac } = {}) {
    const h = mockNetworkOverview().hosts.find((x) => x.ip === ip && x.mac === mac);
    let r;
    if (!h || !h.lease) r = { ok: false, ip, mac, detail: `No lease for ${ip} / ${mac} (already gone?)` };
    else if (h.module || h.kind === "share") r = { ok: false, ip, mac, detail: `${ip} belongs to a registered module or the export share` };
    else if (h.reachable) r = { ok: false, ip, mac, detail: `${ip} answers ping. Unplug or power off the device first, or it will just renew.` };
    else { MOCK_RELEASED.add(ip); r = { ok: true, ip, mac, hostname: h.hostname, detail: "Lease released" }; }
    setTimeout(() => this._fire("release_dhcp_lease_result", r), 1200);
  },
  get_sync_overview(payload) { this._fire("sync_overview", mockSyncOverview(payload?.window_s)); },
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
      git: { available: true, branch: "staging", remote: "git@github.com:mock/saviour.git" },
    });
  },
  // APA grid state and shock mode (controller-side in the real system; no
  // auto decisions or recording-start arming here: use Arm by hand).
  get_apa_shock_state() { this._fireShockState(); },
  set_shock_mode(payload) {
    const r = state.apa;
    r.mode = payload?.mode ?? "manual";
    if (r.mode === "dry_run") r.armed = false;
    if (r.mode !== "manual") r.shockActivated = false;
    this._shockEvent("mode", r.mode);
  },
  arm_grid() {
    if ((state.apa.mode ?? "manual") !== "dry_run") state.apa.armed = true;
    this._shockEvent("armed", "by hand");
  },
  disarm_grid() {
    state.apa.armed = false;
    state.apa.shockActivated = false;
    this._shockEvent("disarmed", "by hand");
  },
  stop_all_shocks() {
    state.apa.armed = false;
    state.apa.shockActivated = false;
    this._shockEvent("stop_all", "");
  },
  // Mock rigs are "offline": exercises the disabled Git pull option.
  check_git_remote() {
    this._fire("git_remote_status", { reachable: false, reason: "mock: no network" });
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
  logout() { this._fire("logout_success"); },
  // Another admin on a second machine, so the Sidebar indicator and the
  // confirmIfOtherAdmins() warning can be seen in mock mode.
  get_clients() {
    const t = Date.now() / 1000;
    this._fire("client_self", { id: "mock-self" });
    this._fire("clients_update", { clients: [
      { id: "mock-self", ip: "10.0.0.50", via_proxy: false, since: t - 300, admin: false },
      { id: "mock-other", ip: "10.0.0.23", via_proxy: false, since: t - 2700, admin: true },
      { id: "mock-other-2", ip: "10.0.0.23", via_proxy: false, since: t - 60, admin: true },
      { id: "mock-guest", ip: "10.0.0.41", via_proxy: false, since: t - 7200, admin: false },
    ] });
  },

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
        // The controller's mode gate (variants/apa/shock_automation.py).
        if (!r.armed || (r.mode ?? "manual") !== "manual") break;
        if (r.attempted >= r.maxShocks) break;
        if (!r.shockActivated) { r.shockActivated = true; r.nextPulseAt = t; }
        r.holdUntil = t + APA_LEASE_MS;
        break;
      case "deactivate_shock": r.shockActivated = false; r.pulseOnUntil = 0; break;
      case "start_motor": r.rotating = true; break;
      case "stop_motor": r.rotating = false; break;
      case "reset_pulse_counter": if (!r.shockActivated) { r.attempted = 0; r.delivered = 0; } break;
      case "list_audiomoths": {
        const labels = state.modules[payload.module_id]?.config?.audiomoth_labels ?? {};
        this._fire("audiomoth_list_response", { module_id: payload.module_id,
          audiomoths: Object.fromEntries(Object.keys(labels).map(s => [s, `mock-${s}`])) });
        break;
      }
      case "sync_selftest": {
        // ~6 s instead of ~30; every connected AudioMoth calibrates cleanly.
        const id = payload.module_id;
        const m = state.modules[id];
        if (m?.status === "RECORDING") {
          this._fire("sync_selftest_state", { module_id: id, result: "error",
            message: "not while recording -- use sync_pulses" });
          break;
        }
        this._fire("sync_selftest_state", { module_id: id, result: "started" });
        setTimeout(() => {
          const cfg = m?.config ?? {};
          const labels = cfg.audiomoth_labels ?? {};
          const microphones = {}, calibrated = {};
          const offsets = { ...(cfg.audiomoth_sync_offsets_ms ?? {}) };
          for (const [serial, label] of Object.entries(labels)) {
            const median = +(Math.random() * 0.4 - 0.2).toFixed(2);
            const offset = -3 - Math.random() * 3.5;
            microphones[label] = { serial, n: 20, detected: 20, median_ms: median,
              std_ms: 0.31, anchor: "latency", measured_offset_ms: +offset.toFixed(3) };
            calibrated[label] = +offset.toFixed(3);
            offsets[serial] = { offset_ms: +offset.toFixed(3), calibrated_at: Date.now() / 1000,
              sample_rate: cfg.audiomoth?.sample_rate, block_size: cfg.microphone?.block_size,
              tone_hz: cfg.sync_pulse?.tone_hz, drive: cfg.sync_pulse?.drive };
          }
          if (m) m.config = { ...cfg, audiomoth_sync_offsets_ms: offsets };
          this._fire("modules_update", clone(state.modules));
          this._fire("sync_selftest_result", { module_id: id, status: "ok",
            pulses: 20, tone_hz: cfg.sync_pulse?.tone_hz, pwm_backend: "pio",
            microphones, calibrated });
        }, 6000);
        break;
      }
      case "scan_hardware": {
        // A camera Pi with a Hailo; anything else finds nothing.
        const m = state.modules[payload.module_id];
        const cam = m?.type?.includes("camera");
        const mic = m?.type === "microphone";
        setTimeout(() => this._fire("module_hardware_scan", {
          module_id: payload.module_id, ok: true, current_type: m?.type,
          items: [
            { label: "CSI camera", value: cam ? "imx477" : "none found", present: cam, system: false },
            { label: "Hailo NPU", value: cam ? "Hailo-8 AI Processor (rev 01)" : "none found", present: cam, system: false },
            { label: "AudioMoth", value: mic ? "4 connected" : "none found", present: mic, system: false },
            { label: "eth0", value: "up, 1000 Mb/s", present: true, system: true },
          ],
          suggested: cam ? ["hailo_camera", "apa_camera", "lightning_camera", "camera", "habitat_camera", "loom_camera"]
            : mic ? ["microphone"] : [],
        }), 1500);
        break;
      }
      case "change_module_type": {
        // Prepare, reprovision, come back under the new id (web.py swaps the
        // old entry for the new one when it first reports).
        const oldId = payload.module_id;
        const newType = payload.params?.new_type;
        const newId = `${newType}_${oldId.slice(-4)}`;
        const ack = (stage, output) => this._fire("module_type_change_result",
          { module_id: oldId, stage, output, new_type: newType, new_module_id: newId });
        setTimeout(() => ack("success", `Reconfiguring as ${newType}`), 1200);
        setTimeout(() => {
          const old = state.modules[oldId];
          if (!old) return;
          delete state.modules[oldId];
          state.modules[newId] = { ...old, id: newId, name: newId, type: newType, status: "WAITING", online: true };
          ack("done", `${oldId} is now ${newId}`);
          this._fire("modules_update", clone(state.modules));
        }, 4000);
        break;
      }
      default: console.debug("[mock] send_command ignored:", payload);
    }
  },
  get_module_types() {
    this._fire("module_types", { types: [
      { type: "camera", name: "Camera", description: "Basic camera" },
      { type: "microphone", name: "Microphone", description: "AudioMoth (up to 4 microphones)" },
      { type: "rfid", name: "RFID", description: "Trovan LID650/665 RFID transponder reader" },
      { type: "ttl", name: "TTL", description: "TTL pulse generator / capture" },
    ] });
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
    device: "trial_microphone-1_1_24F319046A4B3C2D", label: "microphone-1 · 3C2D",
    segments: [seg(2, 3600), seg(3600, MOCK_DAY_S)] },
  { id: "microphone-1/trial_microphone-1_1_24F3190400001111", folder: "microphone-1",
    device: "trial_microphone-1_1_24F3190400001111", label: "microphone-1 · 1111",
    segments: [seg(2, 4400), seg(6000, MOCK_DAY_S)] },
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
