import { useEffect, useState } from "react";
import socket from "../../../../socket";

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

/** Same steps and settings, ignoring the name. */
export function sameProtocol(a, b) {
  if (!a || !b) return a === b;
  const strip = (p) => {
    const rest = { ...p };
    delete rest.name;
    return rest;
  };
  return stable(strip(a)) === stable(strip(b));
}

export const EMPTY_PROTOCOL = { version: 1, seed: 1, end_state: "all_inactive", steps: [] };

/** "base", or "base 2", "base 3"… whichever isn't taken. */
export function freeName(base, items) {
  const taken = new Set((items ?? []).map((i) => i.name));
  if (!taken.has(base)) return base;
  for (let n = 2; ; n += 1) if (!taken.has(`${base} ${n}`)) return `${base} ${n}`;
}
