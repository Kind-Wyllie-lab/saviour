import { useMemo } from "react";
import { cueEnd, expand, highSpans } from "../../utils/sessionProtocol";
import { pinLabel } from "./pinNames";

// Expand a protocol for the designer and previews: its TTL part against one
// TTL module's Scheduled pins and its sound part against the sound module(s)
// in reach, merged into one result, shaped for ProtocolTimeline:
// { pins, result, rows, spans }. Shared by the designer and the card's
// read-only preview, so both show the same thing.

const UNKNOWN_LENGTH_S = 0.25; // how a sound of unknown length is drawn

const uniq = (xs) => [...new Set(xs)];

/** TTL + sound results as one: errors from either, both step lists. */
export function mergeResults(ttl, sound) {
  return {
    ok: ttl.ok && sound.ok,
    errors: uniq([...ttl.errors, ...sound.errors]),
    warnings: uniq([...ttl.warnings, ...sound.warnings]),
    edges: ttl.edges,
    cues: sound.cues,
    pins: ttl.pins,
    outputs: sound.outputs,
    endState: ttl.endState,
    durationS: Math.max(ttl.durationS, sound.durationS),
  };
}

/** Timeline rows and bars for a result: one row per pin, one per sound output. */
export function timelineOf(result, { pins = [], pinConfigs = null, soundModule = null } = {}) {
  if (!result) return { rows: [], spans: [] };
  const pinRows = [...new Set([...pins, ...(result.pins ?? [])])].sort((a, b) => a - b).map((p) => {
    const label = pinLabel(pinConfigs, p);
    return { key: p, label: label ?? `GPIO ${p}`, sublabel: label ? `GPIO ${p}` : undefined };
  });
  const outs = result.outputs ?? [];
  const soundRows = outs.map((o) => ({
    key: `snd:${o}`,
    label: outs.length > 1 || o > 1 ? `Sound ${o}` : "Sound",
    sublabel: soundModule ? `${soundModule.name} · ch ${2 * o - 1}-${2 * o}` : `ch ${2 * o - 1}-${2 * o}`,
  }));
  const edgeSpans = highSpans(result.edges ?? []).map((s) => ({
    row: s.pin,
    start: s.start,
    end: s.end === null && result.endState === "all_inactive" ? result.durationS : s.end,
    label: `${s.action} (step ${s.step})`,
  }));
  const cueSpans = (result.cues ?? []).map((c) => ({
    row: `snd:${c.output}`,
    start: c.t_s,
    end: c.length_s === null ? c.t_s + UNKNOWN_LENGTH_S : cueEnd(c),
    label: `${c.file} (step ${c.step})${c.length_s === null ? ", length unknown" : ""}`,
  }));
  return { rows: [...pinRows, ...soundRows], spans: [...edgeSpans, ...cueSpans] };
}

// soundModules: [{ id, name, files, outputs }] (protocolModules.soundModulesOf).
// With exactly one, sounds are checked against its files and outputs.
// scheduledPins null: check the design only, any pin allowed.
// rowPins: the pins that always get a timeline row (default: all of
// scheduledPins, which here means every pin steps may use); used pins get one
// anyway.
export default function useProtocolExpansion(protocol, scheduledPins, pinConfigs, moduleId, soundModules = [],
  rowPins = null) {
  // Keyed on the contents: callers pass a fresh array every render.
  const pinsKey = [...(scheduledPins ?? [])].sort((a, b) => a - b).join(",");
  const pins = useMemo(() => (pinsKey ? pinsKey.split(",").map(Number) : []), [pinsKey]);
  const anyPin = scheduledPins === null;
  const soundKey = JSON.stringify(soundModules ?? []);
  const soundModule = useMemo(() => {
    const list = JSON.parse(soundKey);
    return list.length === 1 ? list[0] : null;
  }, [soundKey]);

  const result = useMemo(() => {
    if (!protocol) return null;
    const ttl = expand(protocol, { moduleId, moduleType: "ttl", allowedPins: anyPin ? null : pins });
    const sound = expand(protocol, {
      moduleType: "sound", moduleId: soundModule?.id ?? null,
      soundFiles: soundModule?.files ?? null, outputs: soundModule?.outputs ?? null,
    });
    return mergeResults(ttl, sound);
  }, [protocol, moduleId, pins, anyPin, soundModule]);

  const rowKey = rowPins ? [...rowPins].sort((a, b) => a - b).join(",") : null;
  const { rows, spans } = useMemo(
    () => timelineOf(result, {
      pins: rowKey === null ? pins : rowKey ? rowKey.split(",").map(Number) : [], pinConfigs, soundModule,
    }),
    [result, pins, rowKey, pinConfigs, soundModule]);

  return { pins, result, rows, spans, soundModule };
}
