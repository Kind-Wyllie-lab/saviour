import { useEffect, useMemo, useRef, useState } from "react";
import socket from "../../../socket";

// Bench run of the session protocol on the rig: the TTL module's edges and
// the sound modules' sounds at the same times as in a recording, nothing
// recorded, pins inactive and sounds stopped at the end. The controller
// gives every module one start time (test_protocol_rig), so they run in
// step. Each module reports running / finished / stopped / error / refused
// as `ttl_protocol_test` / `sound_protocol_test` (web.py forwards them);
// this hook folds them into one state for ProtocolTestControls.

// Used when a module's report doesn't say when it starts.
const LEAD_S = 1.0;
const DONE = new Set(["finished", "stopped", "error", "refused"]);

// ttlId: the TTL module ("none" = no TTL module); soundIds: sound modules.
export default function useProtocolTest(ttlId, soundIds = []) {
  const soundKey = (soundIds ?? []).join(",");
  const [targets, setTargets] = useState([]); // modules the current test was sent to
  const [reports, setReports] = useState({}); // module_id -> latest report
  const [starting, setStarting] = useState(false);
  const [elapsed, setElapsed] = useState(null);
  const startLocal = useRef(null);

  useEffect(() => {
    const on = (kind) => (msg) => {
      const id = msg?.module_id;
      if (!id || (id !== ttlId && !soundKey.split(",").includes(id))) return;
      if (msg.state === "running" && startLocal.current === null) {
        // Browser and module clocks differ: time the run from receipt, using
        // how far ahead of its report the module said it would start.
        const ahead = Number(msg.started_at) - Number(msg.timestamp);
        startLocal.current = Date.now() / 1000 + (ahead >= 0 && ahead < 10 ? ahead : LEAD_S);
      }
      setStarting(false);
      setReports((prev) => ({ ...prev, [id]: { ...(msg.state === "running" ? {} : prev[id]), ...msg, kind } }));
    };
    const onTtl = on("ttl");
    const onSound = on("sound");
    socket.on("ttl_protocol_test", onTtl);
    socket.on("sound_protocol_test", onSound);
    return () => {
      socket.off("ttl_protocol_test", onTtl);
      socket.off("sound_protocol_test", onSound);
    };
  }, [ttlId, soundKey]);

  const test = useMemo(() => {
    const list = targets.map((id) => reports[id]).filter(Boolean);
    if (starting && !list.length) return { state: "starting" };
    if (!list.length) return null;
    const ttl = list.find((r) => r.kind === "ttl");
    const sounds = list.filter((r) => r.kind === "sound");
    const anyRunning = list.some((r) => r.state === "running");
    const waiting = targets.some((id) => !reports[id]);
    const bad = list.filter((r) => r.state === "error" || r.state === "refused");
    const state = anyRunning || waiting ? "running"
      : bad.length === list.length ? bad[0].state
      : list.every((r) => r.state === "finished") ? "finished"
      : bad.length ? "error" : "stopped";
    const sum = (k) => sounds.reduce((a, r) => a + (Number(r[k]) || 0), 0);
    return {
      state,
      duration_s: Math.max(0, ...list.map((r) => Number(r.duration_s) || 0)),
      ttl: ttl ? { state: ttl.state, edges: ttl.edges, pins: ttl.pins, fired: ttl.fired,
        late: ttl.late, max_late_ms: ttl.max_late_ms, message: ttl.message } : null,
      sound: sounds.length ? { cues: sum("cues"), fired: sum("fired"), late: sum("late"),
        max_late_ms: Math.max(0, ...sounds.map((r) => Number(r.max_late_ms) || 0)),
        outputs: [...new Set(sounds.flatMap((r) => r.outputs ?? []))].sort(),
        states: sounds.map((r) => r.state), message: sounds.map((r) => r.message).filter(Boolean).join("; ") || null } : null,
      message: bad.map((r) => `${r.kind === "ttl" ? "TTL" : "Sound"}: ${r.message ?? r.state}`).join(" · ") || null,
    };
  }, [targets, reports, starting]);

  const running = test?.state === "running" || test?.state === "starting";
  if (!running && startLocal.current !== null && test && DONE.has(test.state)) startLocal.current = null;

  useEffect(() => {
    if (!running) { setElapsed(null); return undefined; }
    const iv = setInterval(() => {
      if (startLocal.current !== null) setElapsed(Math.max(0, Date.now() / 1000 - startLocal.current));
    }, 100);
    return () => clearInterval(iv);
  }, [running]);

  // result: the designer's expansion, so only modules with steps are sent
  // the test (the TTL module when there are edges, sound modules when there
  // are sounds); without it, every module.
  const start = (protocol, result = null) => {
    const ids = [
      ...(ttlId && ttlId !== "none" && (!result || result.edges?.length) ? [ttlId] : []),
      ...(!result || result.cues?.length ? (soundIds ?? []) : []),
    ];
    if (!ids.length) return;
    startLocal.current = null;
    setReports({});
    setTargets(ids);
    setStarting(true);
    setElapsed(null);
    socket.emit("test_protocol_rig", { protocol, module_ids: ids });
  };
  const stop = () => {
    socket.emit("stop_protocol_test_rig", { module_ids: targets });
  };
  return { test, elapsed: running ? elapsed : null, running, start, stop };
}
