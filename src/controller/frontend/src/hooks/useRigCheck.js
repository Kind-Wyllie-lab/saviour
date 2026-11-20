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

// An item with `when` (e.g. "ephys") counts only for a session with that
// condition (plans/ttl-applications-ephys-sync.md); the live result marks it
// inactive, and a session form passes its own conditions.
export function itemActive(item, conditions = []) {
  return !item.when || conditions.includes(item.when);
}

export function rigShortfalls(rig, severity, conditions = []) {
  return (rig?.items || []).filter(
    (i) => itemActive(i, conditions)
      && (i.status === "short" || i.status === "faulty")
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
// `externalSync`: the session's external sync kind ("ephys", "neurologger"),
// so items marked `when: <kind>` count and `syncWarnings` (that kind's
// unconfirmed sync lines, ttlApplications.syncWarnings) are listed too.
export function confirmRigShortfall(rig, action = "Start this session",
  { externalSync = "", syncWarnings = [] } = {}) {
  const missing = rigShortfalls(rig, "required", externalSync ? [externalSync] : []);
  const sync = externalSync ? syncWarnings : [];
  if (!missing.length && !sync.length) return true;
  const lines = missing.map((i) => {
    const notes = [
      ...(i.faulty || []).map((f) => `${f.name || f.id}: ${f.reason}`),
      ...(i.offline || []).map((o) => `${o.name || o.id} offline`),
    ];
    return `• ${i.label}: ${i.count} of ${i.min}${notes.length ? ` (${notes.join("; ")})` : ""}`;
  });
  const parts = [];
  if (lines.length) parts.push(`This rig is missing required hardware:\n\n${lines.join("\n")}`);
  if (sync.length) {
    parts.push(`Sync lines to check (pulse each with Test line on its TTL card and confirm `
      + `it in the acquisition software):\n\n${sync.map((w) => `• ${w}`).join("\n")}`);
  }
  return window.confirm(`${parts.join("\n\n")}\n\n${action} anyway?`);
}
