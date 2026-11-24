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
  return { files, outputs, board: hw.board ?? null };
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
