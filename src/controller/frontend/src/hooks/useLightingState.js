import { useCallback, useEffect, useState } from "react";
import socket from "../socket";
import useIsLoggedIn from "./useIsLoggedIn";

// Live state of one dmx_lighting module (DmxLightingModule.lighting_status).
// Sources, newest wins: the heartbeat's `lighting` block (anyone), the
// module's pushed lighting_state events (anyone; sent on every phase change,
// override, fault, config change), and - signed in only, since commands need
// auth - a get_lighting_state poll. `send(type, params)` runs a lighting
// command; `lastResult` is the reply to the last one.
export default function useLightingState(moduleId, { pollMs = 10000 } = {}) {
  const loggedIn = useIsLoggedIn();
  const [state, setState] = useState(null);
  const [lastResult, setLastResult] = useState(null);

  const take = useCallback((st) => {
    if (!st) return;
    setState((prev) => (prev && prev.updated_at > st.updated_at ? prev : st));
  }, []);

  useEffect(() => {
    if (!moduleId) return undefined;
    const onState = (msg) => {
      if (msg?.module_id !== moduleId) return;
      take(msg.state);
      if (msg.command && msg.command !== "get_lighting_state") {
        setLastResult({ command: msg.command, result: msg.result, error: msg.error, at: Date.now() });
      }
    };
    const onHealth = (data) => take(data?.module_health?.[moduleId]?.lighting);
    socket.on("lighting_state", onState);
    socket.on("module_health_update", onHealth);
    socket.emit("get_module_health");
    return () => {
      socket.off("lighting_state", onState);
      socket.off("module_health_update", onHealth);
    };
  }, [moduleId, take]);

  useEffect(() => {
    if (!moduleId || !loggedIn) return undefined;
    const poll = () => socket.emit("send_command", {
      module_id: moduleId, type: "get_lighting_state", params: {} });
    poll();
    const t = setInterval(poll, pollMs);
    return () => clearInterval(t);
  }, [moduleId, loggedIn, pollMs]);

  const send = useCallback((type, params = {}) => {
    setLastResult(null);
    socket.emit("send_command", { module_id: moduleId, type, params });
  }, [moduleId]);

  return { state, send, lastResult, loggedIn };
}

// Seconds left on an override, from its `until` (the module's clock).
export function remainingS(ov, nowMs = Date.now()) {
  return Math.max(0, (ov?.until ?? 0) - nowMs / 1000);
}

export function fmtDuration(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}
