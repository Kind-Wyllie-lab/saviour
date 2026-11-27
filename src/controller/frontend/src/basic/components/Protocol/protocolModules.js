// What the protocol designer and the New Session form need to know about the
// modules a protocol can run on: TTL modules' Scheduled pins, sound modules'
// files and outputs (the `sound_hardware` section a sound module reports),
// and the session check, the twin of protocol_library.check_for_session.

import { expand, fmtTime, moduleTags } from "../../utils/sessionProtocol";

const isType = (m, kind) => String(m?.type ?? "").includes(kind);

export function scheduledPins(module) {
  return Object.entries(module?.config?.ttl?.pins ?? {})
    .filter(([, c]) => c?.mode === "scheduled")
    .map(([p]) => Number(p))
    .sort((a, b) => a - b);
}

/** A sound module's reported files ({name: length_s|null}) and output count;
 *  null for what it hasn't reported. */
export function soundHardware(module) {
  const hw = module?.config?.sound_hardware ?? {};
  const files = hw.files && typeof hw.files === "object" && !Array.isArray(hw.files) ? hw.files : null;
  const outputs = Number.isInteger(hw.outputs) && hw.outputs >= 1 ? hw.outputs : null;
  // Built sounds the module renders at any length (up to recipeMax s).
  const recipes = Array.isArray(hw.recipes) ? hw.recipes : [];
  const recipeMax = Number(hw.recipe_max_s) || 300;
  return { files, outputs, board: hw.board ?? null, recipes, recipeMax };
}

/** Sound modules in a module list (array or {id: module}), for the designer:
 *  [{ id, name, files, outputs, board }]. */
export function soundModulesOf(modules) {
  const list = Array.isArray(modules) ? modules : Object.values(modules ?? {});
  return list.filter((m) => isType(m, "sound"))
    .map((m) => ({ id: m.id, name: m.name || m.id, ...soundHardware(m) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Will the protocol run on these modules? { errors, warnings, runs, durationS }. */
export function checkForSession(protocol, targetModules, durationMinutes) {
  if (!protocol) return { errors: [], warnings: [], runs: [] };
  const base = expand(protocol);
  if (!base.ok) return { errors: base.errors.slice(0, 3).map((e) => `Protocol: ${e}`), warnings: [], runs: [] };
  const ids = new Set(targetModules.map((m) => m.id));
  const errors = [...moduleTags(protocol)].filter((t) => !ids.has(t)).sort()
    .map((t) => `A step is for module ${t}, which isn't in this target.`);
  const ttl = targetModules.filter((m) => isType(m, "ttl"));
  const sound = targetModules.filter((m) => isType(m, "sound"));
  if (base.edges.length && !ttl.length) errors.push("The protocol drives TTL pins, but no TTL module is in this target.");
  if (base.cues.length && !sound.length) errors.push("The protocol plays sounds, but no sound module is in this target.");
  if (errors.length) return { errors, warnings: [], runs: [] };
  const runs = [];
  const warnings = [];
  for (const m of base.edges.length ? ttl : []) {
    const exp = expand(protocol, { moduleId: m.id, moduleType: "ttl", allowedPins: scheduledPins(m) });
    if (!exp.ok) errors.push(...exp.errors.slice(0, 3).map((e) => `${m.name || m.id}: ${e}`));
    else runs.push({ module: m, kind: "ttl", edges: exp.edges.length, pins: exp.pins });
  }
  for (const m of base.cues.length ? sound : []) {
    const { files, outputs } = soundHardware(m);
    const exp = expand(protocol, { moduleId: m.id, moduleType: "sound", soundFiles: files, outputs });
    if (!exp.ok) errors.push(...exp.errors.slice(0, 3).map((e) => `${m.name || m.id}: ${e}`));
    else {
      runs.push({ module: m, kind: "sound", cues: exp.cues.length, outputs: exp.outputs });
      if (files === null || outputs === null) {
        warnings.push(`${m.name || m.id} hasn't reported its sound files and outputs: they're checked when the recording starts.`);
      }
    }
  }
  if (durationMinutes && base.durationS > durationMinutes * 60) {
    warnings.push(`The protocol runs ${fmtTime(base.durationS)}, longer than the session: its last steps won't run.`);
  }
  return { errors, warnings, runs, durationS: base.durationS };
}

/** Why a rig test can't start now, or null. result: the designer's
 *  expansion; ttl: the TTL module (or null); sounds: the sound modules (full
 *  module records); scheduled: the TTL module's Scheduled lines. */
export function rigTestBlocked({ result, ttl, sounds = [], loggedIn, scheduled = [] }) {
  const busy = (m) => m.status === "RECORDING";
  const off = (m) => m.online === false;
  const hasTtl = (result?.edges?.length ?? 0) > 0;
  const hasSound = (result?.cues?.length ?? 0) > 0;
  if (!loggedIn) return "Sign in as admin to run a test.";
  if (!result || (!hasTtl && !hasSound)) return "Add some steps first.";
  if (!result.ok) return "Fix the problems first.";
  if (hasTtl) {
    if (!ttl) return "No TTL module connected for the TTL steps.";
    if (busy(ttl)) return "Recording: the TTL module is busy.";
    if (off(ttl)) return "The TTL module is offline.";
    if (result.pins.some((p) => !scheduled.includes(p))) return "Set the lines it uses to Protocol output first (TTL card, Pins tab).";
  }
  if (hasSound) {
    if (!sounds.length) return "No sound module connected for the sound steps.";
    if (sounds.some(busy)) return "Recording: the sound module is busy.";
    if (sounds.some(off)) return "The sound module is offline.";
  }
  return null;
}
