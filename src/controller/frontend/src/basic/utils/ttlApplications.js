// What each TTL pin is used for (its role, which follows from its mode) and
// what a TTL module syncs with (its application, worked out from its sync
// line's system). The definitions are src/shared/ttl_applications.json, the
// same file the module and controller read (src/shared/ttl_application.py,
// which this mirrors). plans/ttl-applications-ephys-sync.md
import defs from "@saviour-shared/ttl_applications.json";

export const APPLICATIONS = defs.applications;
export const ROLES = defs.roles;
export const SYNC_SYSTEMS = defs.sync_systems;
// The three main uses offered first; the rest of ROLES under "More".
export const USES = defs.uses;
export const DEFAULT_APPLICATION = defs.default_application;
export const CONFIRM_MAX_AGE_DAYS = defs.confirm_max_age_days;
export const SYNC_APPLICATIONS = ["ephys_sync", "neurologger_sync"];
// What a session can be synchronised with: {kind: {label, application,
// system, description}} (the New Session "External sync" choice).
export const EXTERNAL_SYNC = defs.external_sync ?? {};

export function syncApplication(kind) {
  return EXTERNAL_SYNC[kind]?.application ?? null;
}

// The sync TTL modules (with their lines) for an external sync kind, from
// the controller's `ttl_lines` payload: [{id, name, online, lines, ...}].
export function syncModules(ttlLines, kind) {
  const app = syncApplication(kind);
  if (!app) return [];
  return Object.entries(ttlLines?.modules ?? {})
    .filter(([, m]) => m.application === app)
    .map(([id, m]) => ({ id, ...m }));
}
// For rig profile items ("expects a TTL module used for ...").
export const APPLICATION_ORDER = ["ephys_sync", "neurologger_sync", "mixed"];

const ROLE_OF_MODE = Object.fromEntries(
  Object.entries(ROLES).flatMap(([r, spec]) => spec.modes.map((m) => [m, r])));

// A pin's use, from its mode ("custom" for an unknown mode).
export function pinRole(pin) {
  return ROLE_OF_MODE[pin?.mode ?? "None"] ?? "custom";
}

const sortedPins = (ttlCfg) =>
  Object.entries(ttlCfg?.pins ?? {}).sort(([a], [b]) => Number(a) - Number(b));

// The system a sync line feeds (a SYNC_SYSTEMS key) or null. A line without
// one takes it from an older config's ttl.application.
export function pinSystem(pin, ttlCfg) {
  if (pinRole(pin) !== "sync") return null;
  if (pin && "system" in pin) return SYNC_SYSTEMS[pin.system] ? pin.system : null;
  return APPLICATIONS[ttlCfg?.application]?.system ?? null;
}

export function applicationOf(ttlCfg) {
  for (const [, pin] of sortedPins(ttlCfg)) {
    const system = pinSystem(pin, ttlCfg);
    if (system) return SYNC_SYSTEMS[system].application;
  }
  return DEFAULT_APPLICATION;
}

// A module's application, or null when it isn't a TTL module.
export function moduleApplication(module) {
  if (!module || module.type !== "ttl") return null;
  return applicationOf(module.config?.ttl);
}

export function applicationLabel(app) {
  return APPLICATIONS[app]?.label ?? app;
}

// "Ephys sync" for a TTL module with a sync system, else null.
export function moduleKindLabel(module) {
  const app = moduleApplication(module);
  return app && app !== DEFAULT_APPLICATION ? applicationLabel(app) : null;
}

// "Sync line (pseudorandom)": the role's label plus its `detail`.
export function roleLabel(role) {
  const r = ROLES[role];
  if (!r) return "Other";
  return r.detail ? `${r.label} (${r.detail})` : r.label;
}

// Where a channel goes, in the system's own words.
export function channelLabel(app) {
  return app === "neurologger_sync" ? "Logger input No." : "Ephys Digital In Channel No.";
}

// The Open Ephys acquisition board's Digital In port carries 8 TTL channels
// (one BNC each on the HDMI I/O board).
export const DIGITAL_IN_CHANNELS = [1, 2, 3, 4, 5, 6, 7, 8];

export function systemName(app) {
  return app === "neurologger_sync" ? "the logger" : "Open Ephys";
}

export function validateTTL(ttlCfg) {
  const problems = [];
  const counts = {};
  for (const [gpio, pin] of sortedPins(ttlCfg)) {
    const role = pinRole(pin);
    counts[role] = (counts[role] ?? 0) + 1;
    if (role === "sync" && pin?.system && !SYNC_SYSTEMS[pin.system]) {
      problems.push(`GPIO ${gpio}: unknown sync system '${pin.system}'`);
    }
  }
  for (const [role, n] of Object.entries(counts)) {
    const cap = ROLES[role]?.max;
    if (cap && n > cap) problems.push(`${n} ${ROLES[role].label.toLowerCase()}s; at most ${cap}`);
  }
  return problems;
}

// Sentences for sync lines that aren't currently confirmed, from the
// controller's `ttl_lines` payload (src/controller/ttl_lines.py).
// `application`: only modules set to it (e.g. "ephys_sync").
export function syncWarnings(ttlLines, application = null) {
  const out = [];
  for (const m of Object.values(ttlLines?.modules ?? {})) {
    if (!SYNC_APPLICATIONS.includes(m.application)) continue;
    if (application && m.application !== application) continue;
    const sync = (m.lines ?? []).find((l) => l.role === "sync");
    if (!sync) { out.push(`${m.name} has no sync line`); continue; }
    const where = `GPIO ${sync.gpio}${sync.channel ? ` → channel ${sync.channel}` : ""}`;
    if (sync.state === "unconfirmed") out.push(`${m.name}: sync line (${where}) never confirmed on ${systemName(m.application)}`);
    else if (sync.state === "changed") out.push(`${m.name}: sync line (${where}) changed since it was last confirmed`);
    else if (sync.state === "stale") out.push(`${m.name}: sync line (${where}) last confirmed more than ${CONFIRM_MAX_AGE_DAYS} days ago`);
  }
  return out;
}

// BCM GPIO number -> physical pin on the Pi 5's 40-pin header, so the
// picker can say "GPIO 17 (pin 11)" next to the pinout picture.
const HEADER_PIN = {
  2: 3, 3: 5, 4: 7, 17: 11, 27: 13, 22: 15, 10: 19, 9: 21, 11: 23, 0: 27, 5: 29,
  6: 31, 13: 33, 19: 35, 26: 37, 14: 8, 15: 10, 18: 12, 23: 16, 24: 18, 25: 22,
  8: 24, 7: 26, 1: 28, 12: 32, 16: 36, 20: 38, 21: 40,
};

export function pinLabel(gpio) {
  const header = HEADER_PIN[Number(gpio)];
  return header ? `GPIO ${gpio} (pin ${header})` : `GPIO ${gpio}`;
}
