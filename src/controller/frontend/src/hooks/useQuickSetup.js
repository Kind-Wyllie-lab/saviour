import { useEffect, useState } from "react";
import socket from "../socket";
import { formatUtcDate, formatUtcTime, utcDateKey } from "/src/basic/utils/time";

// The last Quick setup run, kept on the controller (src/controller/quick_setup.py).
// undefined = not loaded yet, null = never run.
let latest;
const listeners = new Set();
socket.on("quick_setup", (d) => {
  latest = d?.last_run ?? null;
  listeners.forEach((fn) => fn(latest));
});

export default function useQuickSetup() {
  const [lastRun, setLastRun] = useState(latest);
  useEffect(() => {
    listeners.add(setLastRun);
    if (latest === undefined) socket.emit("get_quick_setup");
    return () => listeners.delete(setLastRun);
  }, []);
  return lastRun;
}

export function saveQuickSetup(run) {
  socket.emit("save_quick_setup", { run });
}

// "today 09:12 · 1 module not confirmed" / "18 Nov 14:05 · all confirmed".
export function describeRun(run) {
  if (!run?.at) return null;
  const today = utcDateKey(run.at) === utcDateKey();
  const time = formatUtcTime(run.at, { utc: true });
  const day = today ? "today" : formatUtcDate(run.at, { year: false });
  const mods = Object.values(run.modules ?? {});
  const open = mods.filter((m) => !m.confirmed).length;
  const tail = !mods.length ? "no modules" : open ? `${open} module${open > 1 ? "s" : ""} not confirmed` : "all modules confirmed";
  return `${day} ${time} · ${tail}`;
}
