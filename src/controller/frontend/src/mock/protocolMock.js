// dev:mock stand-ins for the controller's protocol library
// (src/controller/protocol_library.py) and the TTL module's Test routine
// (test_protocol / stop_protocol_test). In memory, per page load.

import { expand } from "../basic/utils/sessionProtocol";

const now = () => Date.now() / 1000;

function seedLibrary(modules) {
  const lib = {};
  const fromCard = Object.values(modules ?? {})
    .map((m) => m?.config?.ttl?.protocol).find((p) => p?.name);
  if (fromCard) lib[fromCard.name] = { protocol: structuredClone(fromCard), created_at: now() - 86400, updated_at: now() - 3600 };
  lib["Light pulses, 1 Hz × 10 min"] = {
    protocol: { version: 1, name: "Light pulses, 1 Hz × 10 min", seed: 1, end_state: "all_inactive",
      steps: [{ at_s: 0.1, action: "train", params: { pin: 2, rate_hz: 1, width_s: 0.1, count: 600 } }] },
    created_at: now() - 7200, updated_at: now() - 7200,
  };
  return lib;
}

export function makeProtocolRoutes(getModules, getSessions) {
  let lib = null;
  const ensure = () => { if (!lib) lib = seedLibrary(getModules()); return lib; };
  const list = () => Object.entries(ensure())
    .sort(([a], [b]) => a.toLowerCase().localeCompare(b.toLowerCase()))
    .map(([name, e]) => ({ name, protocol: structuredClone(e.protocol), created_at: e.created_at, updated_at: e.updated_at }));
  const clean = (n) => (typeof n === "string" ? n.split(/\s+/).filter(Boolean).join(" ") : "");
  const fail = (sock, error) => sock._fire("protocol_error", { error });

  return {
    // Minimal session creation so the New Session form works in dev:mock: a
    // PENDING session with a copy of the picked protocol (the real check
    // and start live in src/controller/recording.py).
    create_session(d = {}) {
      const name = String(d.session_name || "").trim();
      if (!name) return this._fire("session_error", { error: "Session name cannot be empty" });
      let protocol = null;
      if (d.protocol) {
        const e = ensure()[d.protocol];
        if (!e) return this._fire("session_error", { error: `No protocol called “${d.protocol}” in the library` });
        protocol = structuredClone(e.protocol);
      }
      const mods = Object.values(getModules() ?? {})
        .filter((m) => d.target === "all" || m.id === d.target || m.group === d.target).map((m) => m.id);
      const p = (n) => String(n).padStart(2, "0");
      const t = new Date();
      const full = `${name.replace(/[^a-zA-Z0-9 \-_]/g, "").trim().replace(/ /g, "_")}-${p(t.getUTCHours())}${p(t.getUTCMinutes())}${p(t.getUTCSeconds())}Z`;
      const sessions = getSessions();
      sessions[full] = {
        session_name: full, target: d.target || "all", state: "pending", modules: mods,
        start_time: null, end_time: null, error_message: "", error_time: null, scheduled: false,
        duration_minutes: d.duration_minutes ?? null, timed_stop_at: null, researcher: d.researcher ?? null,
        module_stop_states: {}, module_export_states: {}, total_exports_complete: 0,
        total_exports_failed: 0, pending_exports: 0, stopped_epoch: null, protocol,
      };
      this._fire("sessions_update", structuredClone(sessions));
      this._fire("create_session_result", { success: true, session_name: full });
    },
    get_protocols() { this._fire("protocols", { items: list() }); },
    save_protocol(d = {}) {
      const name = clean(d.name);
      const prev = d.previous_name ? clean(d.previous_name) : null;
      const l = ensure();
      if (!name) return fail(this, "A protocol needs a name");
      if (l[name] && name !== prev && !d.overwrite) return fail(this, `A protocol called “${name}” already exists`);
      const created = (l[prev] ?? l[name])?.created_at ?? now();
      if (prev && prev !== name) delete l[prev];
      l[name] = { protocol: { ...structuredClone(d.protocol), name }, created_at: created, updated_at: now() };
      this._fire("protocols", { items: list(), saved: name, previous_name: prev });
    },
    copy_protocol(d = {}) {
      const l = ensure();
      const src = l[clean(d.name)];
      if (!src) return fail(this, `No protocol called “${d.name}”`);
      let target = `${clean(d.name)} (copy)`;
      for (let n = 2; l[target]; n += 1) target = `${clean(d.name)} (copy ${n})`;
      l[target] = { protocol: { ...structuredClone(src.protocol), name: target }, created_at: now(), updated_at: now() };
      this._fire("protocols", { items: list(), saved: target });
    },
    delete_protocol(d = {}) {
      const l = ensure();
      if (!l[clean(d.name)]) return fail(this, `No protocol called “${d.name}”`);
      delete l[clean(d.name)];
      this._fire("protocols", { items: list(), deleted: clean(d.name) });
    },
  };
}

// Test routine: reports running, then finished after the protocol's length
// (capped at 30 s so a long one still ends), or stopped.
const tests = {};
export function protocolTestCommand(sock, payload) {
  const id = payload?.module_id;
  if (payload?.type === "test_protocol") {
    const exp = expand(payload.params?.protocol);
    if (!exp.ok) {
      sock._fire("ttl_protocol_test", { module_id: id, state: "refused", message: `Protocol invalid: ${exp.errors[0]}` });
      return true;
    }
    const n = exp.edges.length;
    clearTimeout(tests[id]);
    sock._fire("ttl_protocol_test", { module_id: id, state: "running", duration_s: exp.durationS, edges: n, pins: exp.pins });
    // A long protocol still "finishes" after 30 s in the mock.
    tests[id] = setTimeout(() => sock._fire("ttl_protocol_test",
      { module_id: id, state: "finished", fired: n, edges: n, late: 0, max_late_ms: 0.05 }),
    Math.min(30, exp.durationS) * 1000 + 200);
    return true;
  }
  if (payload?.type === "stop_protocol_test") {
    clearTimeout(tests[id]);
    sock._fire("ttl_protocol_test", { module_id: id, state: "stopped", fired: 3, edges: 1200 });
    return true;
  }
  return false;
}
