// Set while "Shut down all" runs, so the "Connection lost / Reconnecting…"
// overlay stays out of the way: losing the controller is expected then, and
// the power modal says when it's safe to cut the power.
let shuttingDown = false;
const listeners = new Set();

export const isShuttingDown = () => shuttingDown;
export function setShuttingDown(v) {
  shuttingDown = !!v;
  listeners.forEach((fn) => fn(shuttingDown));
}
export function onShuttingDown(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
