import { useEffect, useRef, useState } from "react";
import socket from "../../../../socket";

// Bench run of the session protocol on the module (TTL test_protocol): the
// same edges at the same times as in a recording, nothing recorded, pins
// inactive at the end. The module reports running / finished / stopped /
// error / refused as `ttl_protocol_test` events (web.py forwards them).

// The module starts the first edge this long after the command
// (_PROTOCOL_TEST_LEAD_S).
const LEAD_S = 0.2;

export default function useProtocolTest(moduleId) {
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
