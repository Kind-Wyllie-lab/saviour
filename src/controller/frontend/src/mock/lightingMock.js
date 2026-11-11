// Habitat lighting (dmx_lighting) in mock mode: one module with a 16-box
// patch (A-D rows x 1-4 columns, 8-bit, addresses 1-32), its live state
// computed from the same schedule maths the config card previews, and the
// lighting commands (override, blackout, strip test, clear).

import { levelsAt } from "../basic/components/ConfigCard/DmxLightingConfigCard/lightingSchedule";

export const LIGHTING_ID = "dmx_lighting_5e1f";

const BOXES = ["A", "B", "C", "D"].flatMap((r) => [1, 2, 3, 4].map((c) => `${r}${c}`));

export function makeLightingConfig() {
  return {
    module: { name: "Habitat lights", group: "lighting" },
    dmx_lighting: {
      serial_port: "", timezone: "Europe/London",
      schedule: { lights_on: "07:00", lights_off: "19:00", sunrise_minutes: 30,
        sunset_minutes: 30, curve: "perceptual", day_level: 100, night_level: 0 },
      ir: { mode: "constant", level: 80 },
      channel_bits: 8,
      compartments: BOXES.map((box, i) => ({ box, visible: 2 * i + 1, ir: 2 * i + 2,
        ...(box === "C3" ? { trim: 85 } : {}) })),
      slew_full_scale_s: 60, max_override_minutes: 240, require_synced_clock: true,
      mock: { enabled: false },
      _send_hz: 10, _keepalive_s: 2,
    },
  };
}

export function makeLightingModule() {
  return {
    id: LIGHTING_ID, name: "Habitat lights", type: "dmx_lighting", ip: "10.0.0.61",
    status: "READY", online: true, ready: true, checks: {}, group: "lighting",
    version: "v0.6.1-mock", config_sync_status: "SYNCED", config: makeLightingConfig(),
  };
}

const overrides = [];
let seq = 0;

function minuteNow(tz) {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", { timeZone: tz || undefined, hour: "2-digit",
      minute: "2-digit", second: "2-digit", hour12: false }).formatToParts(new Date());
    const g = (t) => Number(parts.find((p) => p.type === t)?.value);
    return (g("hour") % 24) * 60 + g("minute") + g("second") / 60;
  } catch {
    const d = new Date();
    return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
  }
}

export function lightingState(module) {
  const cfg = module?.config?.dmx_lighting ?? makeLightingConfig().dmx_lighting;
  const now = Date.now() / 1000;
  for (let i = overrides.length - 1; i >= 0; i--) if (overrides[i].until <= now) overrides.splice(i, 1);
  const m = minuteNow(cfg.timezone);
  const sch = levelsAt(m, cfg);
  let ahead = 1;
  while (ahead < 1440 && levelsAt((m + ahead) % 1440, cfg).phase === sch.phase) ahead += 1;
  const nextPhase = levelsAt((m + ahead) % 1440, cfg).phase;
  const boxes = {};
  for (const r of cfg.compartments ?? []) {
    const lv = { visible: sch.visible, ir: sch.ir, source_visible: "schedule", source_ir: "schedule" };
    for (const ov of overrides) {
      if (ov.boxes && !ov.boxes.includes(r.box)) continue;
      if (ov.kind === "test") {
        lv[ov.role] = Math.floor((now - ov.started_at) * 2) % 2 === 0 ? 100 : 0;
        lv[`source_${ov.role}`] = "test";
        continue;
      }
      for (const role of ["visible", "ir"]) {
        if (ov[role] != null) { lv[role] = ov[role]; lv[`source_${role}`] = ov.kind; }
      }
    }
    boxes[r.box] = lv;
  }
  const kinds = new Set(overrides.map((o) => o.kind));
  const mode = kinds.has("test") ? "test" : kinds.has("blackout") ? "blackout"
    : kinds.has("override") ? "override" : sch.phase;
  return {
    mode,
    schedule: { phase: sch.phase, progress: sch.progress, visible: sch.visible, ir: sch.ir,
      next_change: nextPhase, next_change_at: now + ahead * 60, local_time: "" },
    boxes,
    overrides: overrides.map((o) => ({ ...o, remaining_s: o.until - now })),
    widget: { connected: true, port: "/dev/serial/by-id/usb-ENTTEC_DMX_USB_PRO_EN123456-if00-port0",
      serial: "12345678", firmware: "1.68", break_us: 96, mab_us: 10.7, refresh_hz: 40, frames_sent: 8123 },
    fault: null, config_error: null,
    clock: { trusted: true, source: "PTP locked", waiting_s: null },
    timezone: cfg.timezone || "system",
    max_override_minutes: cfg.max_override_minutes ?? 240,
    mock: false, recording: module?.status === "RECORDING", updated_at: now,
  };
}

// Handles the lighting commands; returns true when it took the command.
export function lightingCommand(sock, payload, state) {
  const module = state.modules[payload?.module_id];
  if (module?.type !== "dmx_lighting") return false;
  const p = payload.params ?? {};
  const now = Date.now() / 1000;
  const cfg = module.config.dmx_lighting;
  const reply = (extra = {}) => sock._fire("lighting_state", {
    module_id: module.id, command: payload.type, result: extra.error ? "error" : "success",
    error: extra.error ?? null, state: lightingState(module) });
  const add = (ov) => { overrides.push({ id: `m${(seq += 1)}`, started_at: now, note: "", ...ov }); };
  const max = cfg.max_override_minutes ?? 240;
  switch (payload.type) {
    case "get_lighting_state": reply(); return true;
    case "set_override": {
      const minutes = Number(p.minutes);
      if (!(minutes > 0 && minutes <= max)) return reply({ error: `duration must be more than 0 and at most ${max} minutes` }), true;
      if (p.visible == null && p.ir == null) return reply({ error: "give a visible and/or IR level" }), true;
      add({ kind: "override", boxes: p.boxes ?? null, visible: p.visible ?? null, ir: p.ir ?? null,
        until: now + minutes * 60, note: p.note ?? "" });
      reply(); return true;
    }
    case "blackout": {
      const minutes = Number(p.minutes);
      if (!(minutes > 0 && minutes <= max)) return reply({ error: `duration must be more than 0 and at most ${max} minutes` }), true;
      add({ kind: "blackout", boxes: null, visible: 0, ir: null, until: now + minutes * 60, note: p.note ?? "" });
      reply(); return true;
    }
    case "test_strip": {
      for (let i = overrides.length - 1; i >= 0; i--) if (overrides[i].kind === "test") overrides.splice(i, 1);
      add({ kind: "test", boxes: [p.box], role: p.role, visible: null, ir: null,
        until: now + Number(p.seconds || 30) });
      reply(); return true;
    }
    case "clear_override": {
      const before = overrides.length;
      for (let i = overrides.length - 1; i >= 0; i--) {
        if (!p.override_id || overrides[i].id === p.override_id) overrides.splice(i, 1);
      }
      reply(before === overrides.length && p.override_id ? { error: `no active override ${p.override_id}` } : {});
      return true;
    }
    default: return false;
  }
}

// save_module_config for the lighting module: adopt the saved config, as the
// real module would echo it back.
export function lightingSave(sock, payload, state, clone) {
  const module = state.modules[payload?.id];
  if (module?.type !== "dmx_lighting") return false;
  module.config = { ...module.config, ...clone(payload.config || {}) };
  module.config_sync_status = "SYNCED";
  sock._fire("modules_update", clone(state.modules));
  sock._fire("lighting_state", { module_id: module.id, state: lightingState(module) });
  return true;
}

