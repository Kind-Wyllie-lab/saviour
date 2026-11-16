import { useEffect, useRef, useState } from "react";
import socket from "../../../../socket";
import { fmtTime } from "../../../utils/sessionProtocol";

// Bench run of the session protocol on the module (TTL test_protocol): the
// same edges at the same times as in a recording, nothing recorded, pins
// inactive at the end. The module reports running / finished / stopped /
// error / refused as `ttl_protocol_test` events (web.py forwards them).

// The module starts the first edge this long after the command
// (_PROTOCOL_TEST_LEAD_S).
const LEAD_S = 0.2;

export function useProtocolTest(moduleId) {
  const [test, setTest] = useState(null);
  const [elapsed, setElapsed] = useState(null);
  const startLocal = useRef(null);

  useEffect(() => {
    const onStatus = (msg) => {
      if (msg?.module_id !== moduleId) return;
      if (msg.state === "running") {
        // Browser and module clocks can differ: time the run from receipt.
        startLocal.current = Date.now() / 1000 + LEAD_S;
      } else {
        startLocal.current = null;
        setElapsed(null);
      }
      setTest((prev) => ({ ...(msg.state === "running" ? {} : prev), ...msg }));
    };
    socket.on("ttl_protocol_test", onStatus);
    return () => socket.off("ttl_protocol_test", onStatus);
  }, [moduleId]);

  const running = test?.state === "running" || test?.state === "starting";

  useEffect(() => {
    if (!running) return undefined;
    const iv = setInterval(() => {
      if (startLocal.current !== null) {
        setElapsed(Math.max(0, Date.now() / 1000 - startLocal.current));
      }
    }, 100);
    return () => clearInterval(iv);
  }, [running]);

  const start = (protocol) => {
    setTest({ state: "starting" });
    setElapsed(null);
    socket.emit("send_command", { module_id: moduleId, type: "test_protocol", params: { protocol } });
  };
  const stop = () => {
    socket.emit("send_command", { module_id: moduleId, type: "stop_protocol_test", params: {} });
  };
  return { test, elapsed: running ? elapsed : null, running, start, stop };
}

// blocked: why the test can't start now (null = it can).
export function ProtocolTestControls({ test, elapsed, running, onStart, onStop, blocked }) {
  const duration = test?.duration_s;
  // No word from the module well past the end: say so, keep Stop available.
  const overdue = running && test?.state === "running" && elapsed !== null
    && duration !== undefined && elapsed > duration + 10;

  let line = null;
  if (test?.state === "starting") {
    line = "Starting…";
  } else if (test?.state === "running") {
    line = overdue
      ? "No end report from the module yet; Stop sets its pins inactive."
      : `Running on the module: ${fmtTime(Math.min(elapsed ?? 0, duration ?? 0))} of ${fmtTime(duration ?? 0)}`
        + ` · ${test.edges} edge${test.edges === 1 ? "" : "s"} on GPIO ${(test.pins ?? []).join(", ")}`;
  } else if (test?.state === "finished") {
    line = `Test finished: ${test.fired} of ${test.edges} edges fired, worst ${Number(test.max_late_ms ?? 0).toFixed(2)} ms late`
      + `${test.late ? ` (${test.late} late)` : ""}. Pins are inactive.`;
  } else if (test?.state === "stopped") {
    line = test.message ?? `Test stopped after ${test.fired ?? 0} of ${test.edges ?? 0} edges. Pins are inactive.`;
  } else if (test?.state === "error" || test?.state === "refused") {
    line = test.message ?? "The module could not run the test.";
  }
  const bad = test?.state === "error" || test?.state === "refused";

  return (
    <div className="pe-test">
      <div className="pe-test-row">
        {running ? (
          <button type="button" className="pe-small pe-test-stop" onClick={onStop}>
            ■ Stop test
          </button>
        ) : (
          <button type="button" className="pe-small pe-test-start" onClick={onStart}
            disabled={!!blocked} title={blocked ?? "Run the protocol on the pins now, as it will run in a recording"}>
            ▶ Test routine
          </button>
        )}
        <span className={`pe-test-line${bad ? " pe-test-line--bad" : ""}`}>
          {line ?? blocked ?? "Runs the steps on the real pins now, timed as in a recording; nothing is recorded and pins go inactive at the end."}
        </span>
      </div>
    </div>
  );
}
