// Hand-maintained fake backend state for mock mode (npm run dev:mock).
//
// This is deliberately NOT a faithful mirror of every controller response --
// it's "enough to render and click through the main pages of every variant
// without a Pi". Shapes track what the frontend actually reads; when you hit
// a gap, the mock logs `[mock] unhandled: <event>` to the console -- add the
// event to ROUTER in socketMock.js and any data it needs here.
//
// Edit freely to reproduce a state you're working on (a module in fault, a
// session mid-recording, a full disk, ...). Nothing here ships in a real
// build -- socketMock.js is only imported when VITE_MOCK=1.

const now = () => Date.now() / 1000;

// ── Modules ────────────────────────────────────────────────────────────────
// status: READY | RECORDING | NOT_READY | OFFLINE | FAULT
function makeModules() {
  return {
    "camera-module-a1b2": {
      id: "camera-module-a1b2",
      name: "Home",
      type: "camera",
      ip: "10.0.0.21",
      status: "READY",
      ready: true,
      checks: { _check_picam: [true, "imx708 present"], _check_ptp: [true, "PTP locked"] },
      group: "cameras",
      version: "v0.6.1-mock",
      config_sync_status: "SYNCED",
    },
    "camera-module-c3d4": {
      id: "camera-module-c3d4",
      name: "Arena",
      type: "camera",
      ip: "10.0.0.22",
      status: "RECORDING",
      ready: true,
      checks: { _check_picam: [true, "imx708 present"], _check_ptp: [true, "PTP locked"] },
      group: "cameras",
      version: "v0.6.1-mock",
      config_sync_status: "SYNCED",
    },
    "microphone-module-e5f6": {
      id: "microphone-module-e5f6",
      name: "Ceiling mic",
      type: "microphone",
      ip: "10.0.0.31",
      status: "NOT_READY",
      ready: false,
      checks: { _check_audiomoths: [false, "No AudioMoth microphones detected"] },
      hardware_fault: "No AudioMoth microphones detected",
      group: "microphones",
      version: "v0.6.1-mock",
      config_sync_status: "SYNCED",
    },
    "ttl-module-7788": {
      id: "ttl-module-7788",
      name: "TTL box",
      type: "ttl",
      ip: "10.0.0.41",
      status: "OFFLINE",
      ready: false,
      checks: {},
      group: "other",
      version: "v0.6.1-mock",
      config_sync_status: "SYNCED",
    },
  };
}

// ── Per-module health (ModuleHealthSnapshot-ish) ──────────────────────────
function makeHealth() {
  return {
    "camera-module-a1b2": {
      cpu_usage: 34.2, cpu_temp: 52.1, memory_usage: 41.0,
      memory_total_gb: 8, disk_space: 38.0, disk_used_gb: 45.0, disk_total_gb: 118.0,
      ptp: 0.0000012, phc: 0.0000009, ptp4l_offset_ns: 1200, phc2sys_offset_ns: 900,
      status: "READY", recording: false, hardware_fault: null,
      throttled: 0, last_heartbeat: now(), rec_bytes_per_s: null,
      audio_clip_pct: null, frame_clip_pct: 0.0, version: "v0.6.1-mock",
    },
    "camera-module-c3d4": {
      cpu_usage: 58.7, cpu_temp: 61.4, memory_usage: 52.0,
      memory_total_gb: 8, disk_space: 71.0, disk_used_gb: 84.0, disk_total_gb: 118.0,
      ptp: 0.0000021, phc: 0.0000015, ptp4l_offset_ns: 2100, phc2sys_offset_ns: 1500,
      status: "RECORDING", recording: true, hardware_fault: null,
      throttled: 0, last_heartbeat: now(), rec_bytes_per_s: 2_600_000,
      audio_clip_pct: null, frame_clip_pct: 1.2, version: "v0.6.1-mock",
    },
    "microphone-module-e5f6": {
      cpu_usage: 12.0, cpu_temp: 44.9, memory_usage: 22.0,
      memory_total_gb: 8, disk_space: 9.0, disk_used_gb: 108.0, disk_total_gb: 118.0,
      ptp: 0.0000008, phc: 0.0000006, ptp4l_offset_ns: 800, phc2sys_offset_ns: 600,
      status: "NOT_READY", recording: false,
      hardware_fault: "No AudioMoth microphones detected",
      throttled: 0x50005, last_heartbeat: now(), rec_bytes_per_s: null,
      audio_clip_pct: null, frame_clip_pct: null, version: "v0.6.1-mock",
    },
  };
}

function makeControllerHealth() {
  return {
    ip: "10.0.0.1", cpu_temp: 49.3, cpu_usage: 22.0, memory_usage: 37.0,
    memory_total_gb: 8, disk_space: 61.0, disk_used_gb: 72.0, disk_total_gb: 118.0,
    uptime: 4260, throttled: 0, ptp_sync: 2100, version: "v0.6.1-mock",
    load_average: [0.6, 0.7, 0.8],
  };
}

// ── Sessions (RecordingSession dataclass, asdict) ─────────────────────────
function makeSessions() {
  return {
    "arena-run-20260902_101500": {
      session_name: "arena-run-20260902_101500",
      target: "cameras",
      state: "active",
      modules: ["camera-module-c3d4"],
      start_time: "2026-09-02T10:15:00",
      end_time: null,
      error_message: "",
      error_time: null,
      scheduled: false,
      duration_minutes: 12.25,
      timed_stop_at: now() + 600,
      module_stop_states: { "camera-module-c3d4": "recording" },
      module_export_states: { "camera-module-c3d4": "idle" },
      total_exports_complete: 3, total_exports_failed: 0, pending_exports: 0,
      stopped_epoch: null,
    },
    "habituation-20260901_140000": {
      session_name: "habituation-20260901_140000",
      target: "cameras",
      state: "stopped",
      modules: ["camera-module-a1b2", "camera-module-c3d4"],
      start_time: "2026-09-01T14:00:00",
      end_time: "2026-09-01T14:45:00",
      error_message: "",
      error_time: null,
      scheduled: false,
      duration_minutes: 45,
      timed_stop_at: null,
      module_stop_states: {},
      module_export_states: {
        "camera-module-a1b2": "complete", "camera-module-c3d4": "complete",
      },
      total_exports_complete: 18, total_exports_failed: 0, pending_exports: 0,
      stopped_epoch: now() - 80000,
    },
  };
}

// ── Controller config (base_config.json controller side, trimmed) ─────────
function makeControllerConfig() {
  return {
    controller: { name: "Mock Rig", location: "Bench" },
    export: {
      max_concurrent_exports: 1,
      share_ip: "10.0.0.9", share_path: "recordings",
      share_username: "researcher", share_password: "",
    },
    recording: {
      ptp_start_gate_us: 50.0, ptp_threshold_us: 1000.0,
      nas_min_free_pct: 5, nas_warn_free_pct: 15, local_min_free_pct: 10,
      export_stale_mins: 150,
    },
    alerts: { teams_webhook_url: "" },
    frontend: { theme_id: "default", dark_mode: true, accent_color: "#6495ed", custom_themes: [] },
  };
}

// ── Storage overview (web.py _storage_overview) ───────────────────────────
function makeStorageOverview() {
  return {
    nas: {
      destination: "//10.0.0.9/recordings", status: "ok",
      checked_at: now() - 40,
      free_gb: 812.4, free_pct: 63, total_gb: 1288.0,
      warn_free_pct: 15, min_free_pct: 5,
    },
    exports: { pending: 0, failed: 0, sessions: [] },
    disks: [
      {
        module_id: "microphone-module-e5f6", name: "Ceiling mic", type: "microphone",
        used_pct: 91.0, free_gb: 10.0, total_gb: 118.0, recording: false,
        est_mb_per_min: 22.0, measured_mb_per_min: null, est_note: "",
        local_buffer_min: 465,
      },
      {
        module_id: "camera-module-c3d4", name: "Arena", type: "camera",
        used_pct: 71.0, free_gb: 34.0, total_gb: 118.0, recording: true,
        est_mb_per_min: 15.2, measured_mb_per_min: 14.8, est_note: "",
        local_buffer_min: 2350,
      },
      {
        module_id: "camera-module-a1b2", name: "Home", type: "camera",
        used_pct: 38.0, free_gb: 73.0, total_gb: 118.0, recording: false,
        est_mb_per_min: 15.2, measured_mb_per_min: null, est_note: "",
        local_buffer_min: 4900,
      },
    ],
    data_rate: {
      recording_mb_per_min: 14.8, recording_module_count: 1,
      share_runway_hours: 1096.5,
      fleet_est_mb_per_min: 52.4, fleet_est_module_count: 3,
      est_share_runway_hours: 264.7,
    },
  };
}

// Single mutable state object the mock reads/writes so mutations (start
// recording, save config, ...) reflect in the UI. reset() rebuilds it.
export const state = {
  modules: makeModules(),
  health: makeHealth(),
  controllerHealth: makeControllerHealth(),
  sessions: makeSessions(),
  controllerConfig: makeControllerConfig(),
  storageOverview: makeStorageOverview(),
  moduleConfigs: {},
  systemState: { recording: true, uptime: 71, ptp_sync: 2100 },
  firstRun: { needed: false },
};

export function reset() {
  state.modules = makeModules();
  state.health = makeHealth();
  state.controllerHealth = makeControllerHealth();
  state.sessions = makeSessions();
  state.controllerConfig = makeControllerConfig();
  state.storageOverview = makeStorageOverview();
  state.moduleConfigs = {};
  state.systemState = { recording: true, uptime: 71, ptp_sync: 2100 };
  state.firstRun = { needed: false };
}
