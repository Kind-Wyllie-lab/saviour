// What a TTL module is for (ttl.application) and what its pins do (pin
// roles). The definitions are src/shared/ttl_applications.json, the same file
// the module and controller read (src/shared/ttl_application.py, whose
// validate() this mirrors). plans/ttl-applications-ephys-sync.md
import defs from "@saviour-shared/ttl_applications.json";

export const APPLICATIONS = defs.applications;
export const ROLES = defs.roles;
export const DEFAULT_APPLICATION = defs.default_application;
export const CONFIRM_MAX_AGE_DAYS = defs.confirm_max_age_days;
export const SYNC_APPLICATIONS = ["ephys_sync", "neurologger_sync"];
export const APPLICATION_ORDER = ["ephys_sync", "neurologger_sync", "stimulus_control", "mixed"];

export function applicationOf(ttlCfg) {
  const app = ttlCfg?.application || DEFAULT_APPLICATION;
  return APPLICATIONS[app] ? app : DEFAULT_APPLICATION;
}

// A module's application, or null when it isn't a TTL module.
export function moduleApplication(module) {
  if (!module || module.type !== "ttl") return null;
  return applicationOf(module.config?.ttl);
}

export function applicationLabel(app) {
  return APPLICATIONS[app]?.label ?? app;
}

// "Ephys sync" for a TTL module with an application, else null.
export function moduleKindLabel(module) {
  const app = moduleApplication(module);
  return app && app !== DEFAULT_APPLICATION ? applicationLabel(app) : null;
}

// "Sync line (pseudorandom)": the role's label plus its `detail`, for
// pickers and headers (validation messages use the bare label).
export function roleLabel(role) {
  const r = ROLES[role];
  if (!r) return "No role";
  return r.detail ? `${r.label} (${r.detail})` : r.label;
}

export function pinRole(pin) {
  return ROLES[pin?.role] ? pin.role : "custom";
}

export function rolesFor(app) {
  return APPLICATIONS[app]?.roles ?? null;
}

// Where a channel goes, in the application's own words.
export function channelLabel(app) {
  return app === "neurologger_sync" ? "Logger input" : "Digital In channel";
}

export function systemName(app) {
  return app === "neurologger_sync" ? "the logger" : "Open Ephys";
}

export function validateTTL(ttlCfg) {
  const app = applicationOf(ttlCfg);
  const spec = APPLICATIONS[app];
  const allowed = spec.roles;
  const problems = [];
  const counts = {};
  const pins = Object.entries(ttlCfg?.pins ?? {}).sort(([a], [b]) => Number(a) - Number(b));
  for (const [gpio, pin] of pins) {
    const role = pin?.role;
    if (!role) {
      if (allowed) problems.push(`GPIO ${gpio} has no role (${spec.label} lines each need one)`);
      continue;
    }
    if (!ROLES[role]) { problems.push(`GPIO ${gpio}: unknown role '${role}'`); continue; }
    if (allowed && !allowed.includes(role)) {
      problems.push(`GPIO ${gpio}: ${ROLES[role].label} isn't used by ${spec.label}`);
    }
    if (!ROLES[role].modes.includes(pin?.mode)) {
      problems.push(`GPIO ${gpio}: a ${ROLES[role].label.toLowerCase()} must be `
        + `${ROLES[role].modes.join(" or ")} (is ${pin?.mode})`);
    }
    counts[role] = (counts[role] ?? 0) + 1;
  }
  for (const [role, n] of Object.entries(counts)) {
    const cap = ROLES[role].max;
    if (cap && n > cap) problems.push(`${n} ${ROLES[role].label.toLowerCase()}s; at most ${cap}`);
  }
  for (const role of spec.requires ?? []) {
    if (!counts[role]) problems.push(`${spec.label} needs a ${ROLES[role].label.toLowerCase()}`);
  }
  return problems;
}

// Sentences for sync lines that aren't currently confirmed, from the
// controller's `ttl_lines` payload (src/controller/ttl_lines.py).
export function syncWarnings(ttlLines) {
  const out = [];
  for (const m of Object.values(ttlLines?.modules ?? {})) {
    if (!SYNC_APPLICATIONS.includes(m.application)) continue;
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
