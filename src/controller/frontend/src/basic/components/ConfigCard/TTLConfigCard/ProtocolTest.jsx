import { fmtTime } from "../../../utils/sessionProtocol";

// Test routine / Stop test controls and the result line; the state comes
// from useProtocolTest.

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
      : `Running on the module: ${fmtTime(Math.min(elapsed ?? 0, duration ?? 0), (duration ?? 0) < 60)}`
        + ` of ${fmtTime(duration ?? 0, (duration ?? 0) < 60)}`
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
