import { useState } from "react";
import ProtocolTimeline from "../ProtocolTimeline/ProtocolTimeline";
import { fmtTime } from "../../utils/sessionProtocol";
import useProtocolExpansion from "./useProtocolExpansion";
import "./ProtocolEditor.css";

// A session protocol's steps as a table, checked as you type by the same
// expansion the module runs (sessionProtocol.js, the twin of
// src/shared/session_protocol.py), with a timeline preview. Used in the
// Protocol designer modal on a library protocol (ProtocolDesigner.jsx).
//
// The table edits steps and one level of repeat blocks; anything deeper is
// shown as a nested-repeat row and edited in the JSON view.

const ACTION_LABELS = { set: "Set level", pulse: "Pulse", train: "Pulse train", repeat: "Repeat" };

function defaultParams(action, pin) {
  switch (action) {
    case "set": return { pin, level: "high" };
    case "pulse": return { pin, width_s: 0.05 };
    case "train": return { pin, rate_hz: 10, width_s: 0.02, count: 10 };
    default: return {};
  }
}

function newStep(action, pin, atS = 0) {
  if (action === "repeat") {
    return {
      at_s: atS, action: "repeat", times: 5, every_s: 10, jitter_s: [0, 0],
      steps: [{ at_s: 0, action: "pulse", params: defaultParams("pulse", pin) }],
    };
  }
  return { at_s: atS, action, params: defaultParams(action, pin) };
}

// Keep what the user typed when it isn't a number yet ("" or "0."), so the
// validation can say so instead of the field snapping back.
const numOrRaw = (raw, int = false) => {
  if (raw === "") return "";
  const v = int ? Number(raw) : parseFloat(raw);
  return Number.isNaN(v) ? raw : v;
};

function NumField({ label, value, onChange, int = false, unit, min = 0, width = "5.5em", title }) {
  return (
    <label className="pe-field" title={title}>
      <span className="pe-field-label">{label}</span>
      <input
        type="number"
        min={min}
        step={int ? 1 : "any"}
        value={value ?? ""}
        style={{ width }}
        onChange={(e) => onChange(numOrRaw(e.target.value, int))}
      />
      {unit && <span className="pe-unit">{unit}</span>}
    </label>
  );
}

function PinSelect({ value, pins, onChange }) {
  const known = pins.includes(Number(value));
  return (
    <label className="pe-field">
      <span className="pe-field-label">Pin</span>
      <select value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
        {!known && <option value={value ?? ""}>{value === undefined ? "—" : `GPIO ${value} (not scheduled)`}</option>}
        {pins.map((p) => <option key={p} value={p}>GPIO {p}</option>)}
      </select>
    </label>
  );
}

// Whether a step drives *pin* (a repeat block: any step inside it does).
function touchesPin(step, pin) {
  if (!step) return false;
  if (step.action === "repeat") return (step.steps ?? []).some((s) => touchesPin(s, pin));
  return Number(step.params?.pin) === pin;
}

// focusPin: the pin picked at the top (null = all pins). With one picked,
// rows for other pins are hidden, the pin column is implied, and new steps
// go to that pin.
function StepRow({ step, path, pins, depth, focusPin, onChange, onRemove }) {
  const action = step?.action;
  const set = (patch) => onChange({ ...step, ...patch });
  const setParam = (k, v) => onChange({ ...step, params: { ...(step.params ?? {}), [k]: v } });
  const changeAction = (a) => {
    const pin = step?.params?.pin ?? focusPin ?? pins[0];
    onChange(newStep(a, pin, step?.at_s ?? 0));
  };
  const pinField = focusPin === null
    ? <PinSelect value={step.params?.pin} pins={pins} onChange={(v) => setParam("pin", v)} />
    : null;

  if (action === "repeat" && depth >= 1) {
    return (
      <div className="pe-step pe-step--locked">
        <span className="pe-path">{path}</span>
        <span>Nested repeat at {step.at_s ?? 0} s: edit it in the JSON view.</span>
        <button type="button" className="pe-remove" onClick={onRemove} title="Remove step">✕</button>
      </div>
    );
  }

  return (
    <div className={`pe-step${action === "repeat" ? " pe-step--repeat" : ""}`}>
      <div className="pe-step-main">
        <span className="pe-path"
          title={depth ? `Step ${path.replace("*", "<repetition>")} in the TTL CSV and errors`
                       : "Step number (used in the TTL CSV and errors)"}>{path}</span>
        <NumField label="At" unit="s" value={step.at_s} onChange={(v) => set({ at_s: v })}
          title={depth ? "Seconds from the start of each repetition" : "Seconds from session start"} />
        <label className="pe-field">
          <span className="pe-field-label">Action</span>
          <select value={action ?? ""} onChange={(e) => changeAction(e.target.value)}>
            {!ACTION_LABELS[action] && <option value={action ?? ""}>{String(action)}</option>}
            {Object.entries(ACTION_LABELS)
              .filter(([a]) => a !== "repeat" || depth === 0)
              .map(([a, l]) => <option key={a} value={a}>{l}</option>)}
          </select>
        </label>

        {action === "set" && (
          <>
            {pinField}
            <label className="pe-field">
              <span className="pe-field-label">Level</span>
              <select value={step.params?.level ?? ""} onChange={(e) => setParam("level", e.target.value)}>
                <option value="high">High (active)</option>
                <option value="low">Low (inactive)</option>
              </select>
            </label>
          </>
        )}
        {action === "pulse" && (
          <>
            {pinField}
            <NumField label="Width" unit="s" value={step.params?.width_s} onChange={(v) => setParam("width_s", v)} />
          </>
        )}
        {action === "train" && (
          <>
            {pinField}
            <NumField label="Rate" unit="Hz" value={step.params?.rate_hz} onChange={(v) => setParam("rate_hz", v)} width="4.5em" />
            <NumField label="Width" unit="s" value={step.params?.width_s} onChange={(v) => setParam("width_s", v)} width="4.5em" />
            <NumField label="Count" int min={1} value={step.params?.count} onChange={(v) => setParam("count", v)} width="4.5em" />
          </>
        )}
        {action === "repeat" && (
          <>
            <NumField label="Times" int min={1} value={step.times} onChange={(v) => set({ times: v })} width="4.5em" />
            <NumField label="Every" unit="s" value={step.every_s} onChange={(v) => set({ every_s: v })} width="5.5em"
              title="Gap from one repetition's start to the next, before jitter" />
            <NumField label="+ jitter" value={step.jitter_s?.[0]} width="4.5em"
              title="Extra random gap per repetition, drawn between these two values (seeded)"
              onChange={(v) => set({ jitter_s: [v, step.jitter_s?.[1] ?? 0] })} />
            <NumField label="to" unit="s" value={step.jitter_s?.[1]} width="4.5em"
              onChange={(v) => set({ jitter_s: [step.jitter_s?.[0] ?? 0, v] })} />
          </>
        )}
        <button type="button" className="pe-remove" onClick={onRemove} title="Remove step">✕</button>
      </div>

      {action === "repeat" && (
        <div className="pe-repeat-body">
          {(step.steps ?? []).map((inner, i) => (focusPin !== null && !touchesPin(inner, focusPin) ? null : (
            <StepRow
              key={i}
              step={inner}
              path={`${path}.*.${i}`}
              pins={pins}
              depth={depth + 1}
              focusPin={focusPin}
              onChange={(s) => set({ steps: step.steps.map((x, j) => (j === i ? s : x)) })}
              onRemove={() => set({ steps: step.steps.filter((_, j) => j !== i) })}
            />
          )))}
          <button type="button" className="pe-add pe-add--inner"
            onClick={() => set({ steps: [...(step.steps ?? []), newStep("pulse", focusPin ?? pins[0])] })}>
            + Step in each repetition
          </button>
        </div>
      )}
    </div>
  );
}

// True when some repeat block draws random gaps (jitter upper bound > 0):
// only then does the seed change anything.
function usesJitter(steps) {
  return (steps ?? []).some((s) => s && s.action === "repeat"
    && ((Number(s.jitter_s?.[1]) || 0) > 0 || usesJitter(s.steps)));
}

// renderTest(result): the Test routine controls, given the current
// validation; cursorT: seconds into a running test, drawn on the timeline.
// renderTest(result, protocol): the Test routine controls; cursorT: seconds
// into a running test, drawn on the timeline. allowClear: show "Clear".
export default function ProtocolEditor({ protocol, onChange, scheduledPins, pinConfigs, moduleId,
  renderTest, cursorT = null, allowClear = true, intro }) {
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState(null);
  const [pickedPin, setPickedPin] = useState("all");
  const { pins, result, rows, spans } = useProtocolExpansion(protocol, scheduledPins, pinConfigs, moduleId);
  // A pin picked before it stopped being scheduled falls back to all pins.
  const focusPin = pickedPin !== "all" && pins.includes(Number(pickedPin)) ? Number(pickedPin) : null;

  const ensure = () => protocol ?? { version: 1, seed: 1, end_state: "all_inactive", steps: [] };
  const update = (patch) => onChange({ ...ensure(), ...patch });
  const steps = protocol?.steps ?? [];

  const openJson = () => {
    setJsonText(JSON.stringify(ensure(), null, 2));
    setJsonError(null);
    setJsonMode(true);
  };
  const onJson = (text) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      setJsonError(null);
      onChange(parsed);
    } catch (e) {
      setJsonError(e.message);
    }
  };

  const errors = result?.errors ?? [];
  const warnings = result?.warnings ?? [];

  return (
    <div className="pe">
      {intro !== undefined ? intro : (
        <p className="pe-intro">
          Steps run on this module during every recording, timed from session start on its
          PTP clock. Only pins in <strong>Scheduled</strong> mode (Pins tab) can be used, and
          manual pulses on them are refused while recording.
        </p>
      )}

      {pins.length === 0 && (
        <div className="pe-notice">No scheduled pins yet: set a pin's mode to “Scheduled (session protocol)” on the Pins tab.</div>
      )}

      <div className="pe-top">
        {pins.length > 1 && (
          <label className="pe-field">
            <span className="pe-field-label">Pin</span>
            <select value={focusPin ?? "all"} onChange={(e) => setPickedPin(e.target.value)}>
              <option value="all">All pins</option>
              {pins.map((p) => {
                const n = steps.filter((st) => touchesPin(st, p)).length;
                const desc = pinConfigs?.[p]?.description;
                return (
                  <option key={p} value={p}>
                    GPIO {p}{desc ? ` · ${desc}` : ""} ({n} step{n === 1 ? "" : "s"})
                  </option>
                );
              })}
            </select>
          </label>
        )}
        <label className="pe-field">
          <span className="pe-field-label">When it ends</span>
          <select value={protocol?.end_state ?? "all_inactive"} onChange={(e) => update({ end_state: e.target.value })}>
            <option value="all_inactive">All pins inactive</option>
            <option value="hold">Hold last levels</option>
          </select>
        </label>
        <div className="pe-top-right">
          <button type="button" className="pe-small" onClick={() => (jsonMode ? setJsonMode(false) : openJson())}>
            {jsonMode ? "Table view" : "Edit as JSON"}
          </button>
          {protocol && allowClear && (
            <button type="button" className="pe-small pe-danger" onClick={() => onChange(null)}>
              Clear protocol
            </button>
          )}
        </div>
      </div>

      {jsonMode ? (
        <div className="pe-json">
          <textarea value={jsonText} spellCheck={false} rows={14} onChange={(e) => onJson(e.target.value)} />
          {jsonError && <div className="pe-msg pe-msg--error">JSON: {jsonError}</div>}
        </div>
      ) : (
        <div className="pe-steps">
          {steps.length === 0 && <div className="pe-empty">No steps. Add one below.</div>}
          {focusPin !== null && steps.length > 0 && (
            <div className="pe-empty">
              Showing the {steps.filter((st) => touchesPin(st, focusPin)).length} of {steps.length} steps
              that drive GPIO {focusPin}; new steps go to it.
            </div>
          )}
          {steps.map((s, i) => (focusPin !== null && !touchesPin(s, focusPin) ? null : (
            <StepRow
              key={i}
              step={s}
              path={String(i)}
              pins={pins}
              depth={0}
              focusPin={focusPin}
              onChange={(ns) => update({ steps: steps.map((x, j) => (j === i ? ns : x)) })}
              onRemove={() => update({ steps: steps.filter((_, j) => j !== i) })}
            />
          )))}
          <div className="pe-add-row">
            {["set", "pulse", "train", "repeat"].map((a) => (
              <button key={a} type="button" className="pe-add" disabled={!pins.length}
                onClick={() => {
                  const last = steps.length ? Number(steps[steps.length - 1].at_s) || 0 : 0;
                  update({ steps: [...steps, newStep(a, focusPin ?? pins[0], steps.length ? last + 1 : 0.1)] });
                }}>
                + {ACTION_LABELS[a]}
              </button>
            ))}
            {steps.length > 1 && (
              <button type="button" className="pe-small" title="Order the table by start time"
                onClick={() => update({ steps: [...steps].sort((a, b) => (Number(a.at_s) || 0) - (Number(b.at_s) || 0)) })}>
                Sort by time
              </button>
            )}
          </div>
        </div>
      )}

      {usesJitter(protocol?.steps) && (
        <div className="pe-random">
          <div className="pe-random-controls">
            <NumField label="Random gap pattern" int min={0} value={protocol?.seed ?? 1} width="7em"
              onChange={(v) => update({ seed: v })} />
            <button type="button" className="pe-small"
              onClick={() => update({ seed: Math.floor(Math.random() * 1e6) })}>
              New pattern
            </button>
          </div>
          <p className="pe-random-hint">
            Repeats with jitter add a random extra gap to each repetition. This number (the
            seed) picks which random gaps you get: keep it and every session uses the same
            gaps, the ones in the timeline below; change it for a different pattern. The gaps
            used are saved with each session's data.
          </p>
        </div>
      )}

      {result && (
        <div className="pe-check">
          <div className={`pe-summary${result.ok ? "" : " pe-summary--bad"}`}>
            <span className="pe-summary-icon" aria-hidden="true">{result.ok ? "✓" : "✕"}</span>
            {result.ok
              ? `Valid: ${result.edges.length} edges on ${result.pins.length ? result.pins.map((p) => `GPIO ${p}`).join(", ") : "no pins"}, last at ${fmtTime(result.durationS, result.durationS < 60)}`
              : `${errors.length} problem${errors.length === 1 ? "" : "s"}: the module will refuse to record with this protocol`}
          </div>
          {errors.slice(0, 8).map((m, i) => <div key={`e${i}`} className="pe-msg pe-msg--error">{m}</div>)}
          {errors.length > 8 && <div className="pe-msg pe-msg--error">…and {errors.length - 8} more</div>}
          {warnings.slice(0, 5).map((m, i) => <div key={`w${i}`} className="pe-msg pe-msg--warn">⚠ {m}</div>)}
          {result.ok && result.edges.length > 0 && result.edges[0].t_s < 0.05 && (
            <div className="pe-msg pe-msg--warn">
              ⚠ The first step at {result.edges[0].t_s} s lands a few ms late (the protocol starts with the
              recording); use 0.05 s or later where exact timing matters.
            </div>
          )}
        </div>
      )}

      {renderTest && renderTest(result, protocol)}

      <div className={`pe-timeline${result && !result.ok ? " pe-timeline--invalid" : ""}`}>
        <div className="pe-timeline-title">
          Timeline preview{result && !result.ok ? " (invalid protocol: may not match what would run)" : ""}
        </div>
        <ProtocolTimeline
          rows={rows}
          spans={spans}
          duration={result?.durationS ?? 0}
          highlightRow={focusPin}
          cursorT={cursorT}
          emptyText="Add a scheduled pin and some steps to see the timeline."
        />
      </div>
    </div>
  );
}
