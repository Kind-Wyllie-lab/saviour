import { useEffect, useState } from "react";
import socket from "../../../socket";

// The controller's named protocol library (src/controller/protocol_library.py)
// over Socket.IO: `protocols` lists them (broadcast after every change, with
// `saved` / `deleted` naming what changed), `protocol_error` reports a refusal.

export function useProtocolLibrary() {
  const [items, setItems] = useState(null); // null until the first list arrives
  const [error, setError] = useState(null);
  const [change, setChange] = useState(null); // last {saved|deleted, previous_name, seq}

  useEffect(() => {
    let seq = 0;
    const onList = (d) => {
      setItems(Array.isArray(d?.items) ? d.items : []);
      if (d?.saved || d?.deleted) {
        seq += 1;
        setChange({ saved: d.saved, deleted: d.deleted, previous_name: d.previous_name, seq });
      }
    };
    const onError = (d) => setError(d?.error ?? "The controller refused that change.");
    socket.on("protocols", onList);
    socket.on("protocol_error", onError);
    socket.emit("get_protocols");
    return () => {
      socket.off("protocols", onList);
      socket.off("protocol_error", onError);
    };
  }, []);

  return {
    items,
    error,
    clearError: () => setError(null),
    change,
    save: (name, protocol, previousName) => {
      setError(null);
      socket.emit("save_protocol", { name, protocol, previous_name: previousName ?? null });
    },
    copy: (name) => {
      setError(null);
      socket.emit("copy_protocol", { name });
    },
    remove: (name) => {
      setError(null);
      socket.emit("delete_protocol", { name });
    },
  };
}

// JSON with keys sorted at every level, so two copies of a protocol compare
// equal whatever order their keys were written in (the controller sorts).
function stable(v) {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${stable(v[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

const TTL_ACTIONS = new Set(["set", "pulse", "train", "square"]);
const intList = (xs) => [...new Set((Array.isArray(xs) ? xs : []).map(Number).filter(Number.isInteger))]
  .sort((x, y) => x - y);

/** Steps as the designer edits them: each on one line. A TTL step that
 *  drives several pins at once (params.pins) becomes one step per pin at the
 *  same time, which runs identically. */
function splitPins(steps) {
  return (Array.isArray(steps) ? steps : []).flatMap((s) => {
    if (s && s.action === "repeat") return [{ ...s, steps: splitPins(s.steps) }];
    if (s && TTL_ACTIONS.has(s.action) && Array.isArray(s.params?.pins)) {
      const { pins, ...rest } = s.params;
      return pins.length ? pins.map((pin) => ({ ...s, params: { pin, ...rest } })) : [s];
    }
    return [s];
  });
}

/** The lines and sound outputs a protocol's steps use: {ttl, sound}. */
export function usedOutputs(steps) {
  const ttl = [];
  const sound = [];
  const walk = (list) => (Array.isArray(list) ? list : []).forEach((s) => {
    if (!s) return;
    if (s.action === "repeat") walk(s.steps);
    else if (s.action === "play") sound.push(Number(s.params?.output) || 1);
    else if (TTL_ACTIONS.has(s.action)) {
      const p = s.params ?? {};
      (Array.isArray(p.pins) ? p.pins : [p.pin]).forEach((x) => ttl.push(x));
    }
  });
  walk(steps);
  return { ttl: intList(ttl), sound: intList(sound) };
}

/** A protocol as the designer edits it: steps one line each, and
 *  `outputs` ({ttl: [pins], sound: [outputs]}: the lines and sound outputs
 *  it uses, its rows) always present, at least what the steps use. The
 *  modules ignore `outputs`; it is what the protocol needs from the rig. */
export function normaliseProtocol(p) {
  if (!p || typeof p !== "object") return p;
  const steps = splitPins(p.steps);
  const used = usedOutputs(steps);
  const o = p.outputs ?? {};
  return {
    ...p,
    steps,
    outputs: { ttl: intList([...intList(o.ttl), ...used.ttl]), sound: intList([...intList(o.sound), ...used.sound]) },
  };
}

/** Same steps and settings, ignoring the name (and how steps are split). */
export function sameProtocol(a, b) {
  if (!a || !b) return a === b;
  const strip = (p) => {
    const rest = { ...normaliseProtocol(p) };
    delete rest.name;
    return rest;
  };
  return stable(strip(a)) === stable(strip(b));
}

// New protocols draw new random times for every session (seed_mode);
// older ones without the key keep their fixed pattern.
export const EMPTY_PROTOCOL = { version: 1, seed: 1, seed_mode: "per_session", end_state: "all_inactive", steps: [], outputs: { ttl: [], sound: [] } };

/** "base", or "base 2", "base 3"… whichever isn't taken. */
export function freeName(base, items) {
  const taken = new Set((items ?? []).map((i) => i.name));
  if (!taken.has(base)) return base;
  for (let n = 2; ; n += 1) if (!taken.has(`${base} ${n}`)) return `${base} ${n}`;
}
