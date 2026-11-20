import { useEffect, useState } from "react";
import socket from "../socket";

// Expected-vs-present hardware for this rig (src/controller/rig_check.py).
// The controller pushes `rig_check` on connect and whenever it changes.
// Shape: { level: ok|pending|warning|error, ok, pending, summary,
//          items: [{label, types, min, severity, count, status,
//                   present, faulty: [{id, name, reason}], offline}] }
let latest = null;
const listeners = new Set();

function onRigCheck(data) {
  latest = data;
  listeners.forEach((fn) => fn(data));
}
socket.on("rig_check", onRigCheck);

export function rigShortfalls(rig, severity) {
  return (rig?.items || []).filter(
    (i) => (i.status === "short" || i.status === "faulty")
      && (!severity || i.severity === severity));
}

export default function useRigCheck() {
  const [rig, setRig] = useState(latest);
  useEffect(() => {
    listeners.add(setRig);
    if (latest == null) socket.emit("get_rig_check");
    return () => listeners.delete(setRig);
  }, []);
  return rig;
}

// Before starting a session: if required hardware is missing, ask. Warns,
// never blocks (a researcher may knowingly run without it). True = go ahead.
export function confirmRigShortfall(rig, action = "Start this session") {
  const missing = rigShortfalls(rig, "required");
  if (!missing.length) return true;
  const lines = missing.map((i) => {
    const notes = [
      ...(i.faulty || []).map((f) => `${f.name || f.id}: ${f.reason}`),
      ...(i.offline || []).map((o) => `${o.name || o.id} offline`),
    ];
    return `• ${i.label}: ${i.count} of ${i.min}${notes.length ? ` (${notes.join("; ")})` : ""}`;
  });
  return window.confirm(
    `This rig is missing required hardware:\n\n${lines.join("\n")}\n\n${action} anyway?`);
}
