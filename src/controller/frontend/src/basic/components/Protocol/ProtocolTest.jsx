import { fmtTime } from "../../utils/sessionProtocol";

// Test routine / Stop test controls and the result line; the state comes
// from useProtocolTest (TTL and sound reports folded together).

// blocked: why the test can't start now (null = it can).
export function ProtocolTestControls({ test, elapsed, running, onStart, onStop, blocked }) {
  const duration = test?.duration_s;
  // No word from the module well past the end: say so, keep Stop available.
  const overdue = running && test?.state === "running" && elapsed !== null
    && duration !== undefined && elapsed > duration + 10;

  const parts = [];
  if (test?.ttl) parts.push(`${test.ttl.edges ?? 0} edge${test.ttl.edges === 1 ? "" : "s"} on GPIO ${(test.ttl.pins ?? []).join(", ")}`);
  if (test?.sound) parts.push(`${test.sound.cues} sound${test.sound.cues === 1 ? "" : "s"} on output ${test.sound.outputs.join(", ")}`);
  const done = [];
  if (test?.ttl && test.ttl.state === "finished") {
    done.push(`${test.ttl.fired} of ${test.ttl.edges} edges, worst ${Number(test.ttl.max_late_ms ?? 0).toFixed(2)} ms late`);
  }
  if (test?.sound && test.sound.states.every((st) => st === "finished")) {
    done.push(`${test.sound.fired} of ${test.sound.cues} sounds, worst ${Number(test.sound.max_late_ms ?? 0).toFixed(1)} ms late`);
  }

  let line = null;
  if (test?.state === "starting") {
    line = "Starting…";
  } else if (test?.state === "running") {
    line = overdue
      ? "No end report from the modules yet; Stop sets pins inactive and stops the sound."
      : `Running on the rig: ${fmtTime(Math.min(elapsed ?? 0, duration ?? 0), (duration ?? 0) < 60)}`
        + ` of ${fmtTime(duration ?? 0, (duration ?? 0) < 60)}`
        + (parts.length ? ` · ${parts.join(", ")}` : "")
        + (test.message ? ` · ${test.message}` : "");
  } else if (test?.state === "finished") {
    line = `Test finished: ${done.join("; ")}. Pins are inactive.`;
  } else if (test?.state === "stopped") {
    line = `Test stopped${done.length ? ` (${done.join("; ")})` : ""}. Pins are inactive and the sound stopped.`
      + (test.message ? ` ${test.message}` : "");
  } else if (test?.state === "error" || test?.state === "refused") {
    line = test.message ?? "The rig could not run the test.";
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
            disabled={!!blocked} title={blocked ?? "Run the protocol on the rig now (TTL lines and sounds), as it will run in a recording"}>
            ▶ Test routine
          </button>
        )}
        <span className={`pe-test-line${bad ? " pe-test-line--bad" : ""}`}>
          {line ?? blocked ?? "Runs the steps on the rig now (TTL lines and sounds), timed as in a recording; nothing is recorded and pins go inactive at the end."}
        </span>
      </div>
    </div>
  );
}
