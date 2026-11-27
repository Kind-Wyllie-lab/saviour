// dev:mock twin of src/controller/ttl_lines.py: TTL lines confirmed on the
// acquisition system, per module and pin, tied to the wiring at the time.
// The mock ephys sync module (fixtures.js) starts with its sync line
// confirmed and its experiment-start line not.
import {
  ROLES, CONFIRM_MAX_AGE_DAYS, applicationOf, applicationLabel, validateTTL, pinRole, pinSystem,
} from "/src/basic/utils/ttlApplications";

const store = {
  "ttl-ephys-2904": {
    lines: { 26: { key: "26:pseudorandom:1", confirmed_at: Date.now() / 1000 - 2 * 86400 } },
    last_verified: { session: "Ephys_test-20261118-101500", at: Date.now() / 1000 - 2 * 86400,
      gpio: 26, r2: 0.9999987, edges_used: 412, residual_max_ms: 0.21 },
  },
};

const wiringKey = (l) => `${l.gpio}:${l.mode}:${l.channel ?? "None"}`;

function lines(ttl) {
  return Object.entries(ttl?.pins ?? {})
    .filter(([, p]) => ROLES[pinRole(p)])
    .map(([g, p]) => ({ gpio: Number(g), role: pinRole(p), mode: p.mode,
      channel: p.channel || null, label: p.label || "", system: pinSystem(p, ttl) }))
    .sort((a, b) => a.gpio - b.gpio);
}

function lineState(mid, line) {
  const e = store[mid]?.lines?.[line.gpio];
  if (!e) return { ...line, confirmed_at: null, state: "unconfirmed" };
  const state = e.key !== wiringKey(line) ? "changed"
    : Date.now() / 1000 - e.confirmed_at > CONFIRM_MAX_AGE_DAYS * 86400 ? "stale" : "confirmed";
  return { ...line, confirmed_at: e.confirmed_at, state };
}

export function ttlLinesPayload(modules) {
  const out = {};
  for (const [mid, m] of Object.entries(modules ?? {})) {
    if (m.type !== "ttl") continue;
    const ttl = m.config?.ttl ?? {};
    const app = applicationOf(ttl);
    const ls = lines(ttl);
    if (app === "mixed" && !ls.some((l) => ROLES[l.role]?.channel)) continue;
    out[mid] = { application: app, label: applicationLabel(app), name: m.name || mid,
      online: m.online !== false, lines: ls.map((l) => lineState(mid, l)),
      problems: validateTTL(ttl), last_verified: store[mid]?.last_verified ?? null };
  }
  return { modules: out, confirm_max_age_days: CONFIRM_MAX_AGE_DAYS };
}

export function makeTTLLineRoutes(getModules) {
  return {
    get_ttl_lines() { this._fire("ttl_lines", ttlLinesPayload(getModules())); },
    confirm_ttl_line({ module_id, gpio } = {}) {
      const line = lines(getModules()[module_id]?.config?.ttl).find((l) => l.gpio === Number(gpio));
      if (!line) { this._fire("ttl_lines_error", { error: `GPIO ${gpio} has no role to confirm` }); return; }
      ((store[module_id] ??= {}).lines ??= {})[line.gpio] = { key: wiringKey(line), confirmed_at: Date.now() / 1000 };
      this._fire("ttl_lines", ttlLinesPayload(getModules()));
    },
    clear_ttl_line({ module_id, gpio } = {}) {
      delete store[module_id]?.lines?.[Number(gpio)];
      this._fire("ttl_lines", ttlLinesPayload(getModules()));
    },
  };
}
