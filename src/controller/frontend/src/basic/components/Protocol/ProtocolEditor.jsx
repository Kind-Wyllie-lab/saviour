import { useMemo, useState } from "react";
import ProtocolTimeline from "../ProtocolTimeline/ProtocolTimeline";
import { fmtTime, stepPins } from "../../utils/sessionProtocol";
import useProtocolExpansion from "./useProtocolExpansion";
import { pinLabel, pinName } from "./pinNames";
import "./ProtocolEditor.css";

// A session protocol's steps as a table, checked as you type by the same
// expansion the module runs (sessionProtocol.js, the twin of
// src/shared/session_protocol.py), with a timeline preview. Used in the
// Protocol designer modal on a library protocol (ProtocolDesigner.jsx).
//
// The table edits steps and one level of repeat blocks; anything deeper is
// shown as a nested-repeat row and edited in the JSON view. A step can drive
// several pins at once (params.pins); one pin is written as params.pin.

const ACTION_LABELS = { set: "Set level", pulse: "Pulse", train: "Pulse train", play: "Play sound", repeat: "Repeat" };

// sound: { modules: [{ id, name, files, outputs }] } -- the sound modules in
// reach; files and outputs offered to a play step come from the one it names
// (or, untagged, from all of them).
function soundChoices(sound, moduleId) {
  const mods = sound?.modules ?? [];
  const chosen = moduleId ? mods.filter((m) => m.id === moduleId) : mods;
  const files = [...new Set(chosen.flatMap((m) => Object.keys(m.files ?? {})))].sort();
  const known = chosen.map((m) => m.outputs).filter(Boolean);
  // Untagged plays run on every sound module: offer what they all have.
  const outputs = known.length ? (moduleId ? Math.max(...known) : Math.min(...known)) : 4;
  return { files, outputs, lengths: Object.assign({}, ...chosen.map((m) => m.files ?? {})) };
}

// { pin } for one pin, { pins } for several: older protocols and the JSON
// view stay readable, and one-pin steps keep their old shape.
function pinParams(pins) {
  const list = (pins ?? []).filter((p) => p !== undefined && p !== null);
  return list.length > 1 ? { pins: list } : { pin: list[0] };
}

function withPins(params, pins) {
  // eslint-disable-next-line no-unused-vars -- replacing both keys
  const { pin, pins: old, ...rest } = params ?? {};
  return { ...pinParams(pins), ...rest };
}

function defaultParams(action, pins, sound) {
  switch (action) {
    case "play": return { file: soundChoices(sound).files[0] ?? "", output: 1 };
    case "set": return { ...pinParams(pins), level: "high" };
    case "pulse": return { ...pinParams(pins), width_s: 0.05 };
    case "train": return { ...pinParams(pins), rate_hz: 10, width_s: 0.02, count: 10 };
    default: return {};
  }
}

function newStep(action, pins, atS = 0, sound = null) {
  if (action === "repeat") {
    // A repeat starts with one step: a pulse, or a sound when there's no pin.
    const inner = (pins ?? []).some((p) => p !== undefined) || !(sound?.modules ?? []).length ? "pulse" : "play";
    return {
      at_s: atS, action: "repeat", times: 5, every_s: 10, jitter_s: [0, 0],
      steps: [{ at_s: 0, action: inner, params: defaultParams(inner, pins, sound) }],
    };
  }
  return { at_s: atS, action, params: defaultParams(action, pins, sound) };
}

// Switch a repeat block between regular gaps (every_s + jitter_s) and a
// pseudo-random spread over a window (spread_s + min_gap_s), keeping the
// overall length about the same.
function toSpacing(step, mode) {
  // eslint-disable-next-line no-unused-vars -- dropping the other mode's keys
  const { every_s, jitter_s, spread_s, min_gap_s, ...rest } = step;
  const gaps = Math.max((Number(step.times) || 1) - 1, 1);
  if (mode === "spread") {
    if (spread_s !== undefined) return step;
    const every = Number(every_s) || 10;
    return { ...rest, spread_s: Math.round(every * gaps), min_gap_s: Math.round(every / 2) };
  }
  if (every_s !== undefined) return step;
  const every = Math.round((Number(spread_s) || 10) / gaps) || 10;
  return { ...rest, every_s: Math.max(every, Number(min_gap_s) || 0), jitter_s: [0, 0] };
}

// Keep what the user typed when it isn't a number yet ("" or "0."), so the
// validation can say so instead of the field snapping back.
const numOrRaw = (raw, int = false) => {
  if (raw === "") return "";
  const v = int ? Number(raw) : parseFloat(raw);
  return Number.isNaN(v) ? raw : v;
};

function NumField({ label, value, onChange, int = false, unit, min = 0, width = "5.5em", title, placeholder }) {
  return (
    <label className="pe-field" title={title}>
      <span className="pe-field-label">{label}</span>
      <input
        type="number"
        min={min}
        step={int ? 1 : "any"}
        placeholder={placeholder}
        value={value ?? ""}
        style={{ width }}
        onChange={(e) => onChange(numOrRaw(e.target.value, int))}
      />
      {unit && <span className="pe-unit">{unit}</span>}
    </label>
  );
}

// The pins a step drives, as toggles: press one to add or drop it (a step
// keeps at least one). Pins the step names that aren't scheduled on this
// module show as a warning chip that removes them.
function PinChips({ value, pins, pinConfigs, onChange }) {
  const chosen = new Set(value);
  const unknown = value.filter((p) => !pins.includes(p));
  const toggle = (p) => {
    if (chosen.has(p)) {
      if (chosen.size > 1) onChange(value.filter((x) => x !== p));
    } else {
      onChange([...value, p].sort((a, b) => a - b));
    }
  };
  return (
    <div className="pe-field">
      <span className="pe-field-label">{value.length > 1 ? "Pins (together)" : "Pin"}</span>
      <div className="pe-pins" role="group" aria-label="Pins this step drives">
        {pins.map((p) => {
          const label = pinLabel(pinConfigs, p);
          const on = chosen.has(p);
          return (
            <button key={p} type="button" aria-pressed={on}
              className={`pe-pin${on ? " pe-pin--on" : ""}`}
              title={on && chosen.size === 1 ? `${pinName(pinConfigs, p)}: a step needs at least one pin`
                : `${on ? "Stop driving" : "Also drive"} ${pinName(pinConfigs, p)} with this step`}
              onClick={() => toggle(p)}>
              {label ?? `GPIO ${p}`}
              {label && <span className="pe-pin-gpio">{p}</span>}
            </button>
          );
        })}
        {unknown.map((p) => (
          <button key={`x${p}`} type="button" className="pe-pin pe-pin--bad"
            title={`GPIO ${p} is not a Scheduled pin on this module: click to remove it`}
            onClick={() => onChange(value.filter((x) => x !== p))}>
            GPIO {p} ✕
          </button>
        ))}
        {value.length === 0 && pins.length === 0 && <span className="pe-pin pe-pin--bad">no pin</span>}
      </div>
    </div>
  );
}

// A play step's fields: which sound module (when there are several), file,
// output, and optional volume and length (blank = the module's setting /
// the whole file).
function PlayFields({ step, sound, onChange }) {
  const params = step.params ?? {};
  const mods = sound?.modules ?? [];
  const { files, outputs, lengths } = soundChoices(sound, step.module);
  const setParam = (k, v) => {
    const next = { ...params };
    if (v === "" || v === undefined || v === null) delete next[k];
    else next[k] = v;
    onChange({ ...step, params: next });
  };
  const setModule = (id) => {
    // eslint-disable-next-line no-unused-vars -- dropping the key
    const { module, ...rest } = step;
    onChange(id ? { ...rest, module: id } : rest);
  };
  const outputList = Array.from({ length: Math.max(outputs, Number(params.output) || 1) }, (_, i) => i + 1);
  const len = lengths[params.file];
  return (
    <>
      {mods.length > 1 && (
        <label className="pe-field">
          <span className="pe-field-label">On</span>
          <select value={step.module ?? ""} onChange={(e) => setModule(e.target.value)}>
            <option value="">Every sound module</option>
            {mods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            {step.module && !mods.some((m) => m.id === step.module) && (
              <option value={step.module}>{step.module} (not in reach)</option>
            )}
          </select>
        </label>
      )}
      <label className="pe-field" title={typeof len === "number" ? `${len.toFixed(3)} s long` : undefined}>
        <span className="pe-field-label">Sound</span>
        <select value={params.file ?? ""} onChange={(e) => setParam("file", e.target.value)}>
          {!files.includes(params.file) && (
            <option value={params.file ?? ""}>{params.file ? `${params.file} (not on the module)` : "—"}</option>
          )}
          {files.map((f) => (
            <option key={f} value={f}>
              {f}{typeof lengths[f] === "number" ? ` (${lengths[f].toFixed(lengths[f] < 10 ? 2 : 0)} s)` : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="pe-field" title="Stereo output: 1 = channels 1-2, 2 = 3-4, …">
        <span className="pe-field-label">Output</span>
        <select value={params.output ?? 1} onChange={(e) => setParam("output", Number(e.target.value))}>
          {outputList.map((o) => <option key={o} value={o}>{o}{o > outputs ? " (not on the card)" : ""}</option>)}
        </select>
      </label>
      <NumField label="Volume" value={params.volume} width="4.5em" placeholder="module"
        title="Gain factor (1 = the file as recorded); blank uses the module's volume setting"
        onChange={(v) => setParam("volume", v)} />
      <NumField label="Length" unit="s" value={params.duration_s} width="5em" placeholder="whole"
        title="Cut the sound to this many seconds; blank plays the whole file"
        onChange={(v) => setParam("duration_s", v)} />
    </>
  );
}

// Whether a step drives *pin* (a repeat block: any step inside it does).
function touchesPin(step, pin) {
  if (!step) return false;
  if (step.action === "repeat") return (step.steps ?? []).some((s) => touchesPin(s, pin));
  return stepPins(step).includes(pin);
}

// focusPin: the pin picked at the top (null = all pins). With one picked,
// rows for other pins are hidden, the pin column is implied, and new steps
// go to that pin.
function StepRow({ step, path, pins, pinConfigs, sound, depth, focusPin, onChange, onRemove }) {
  const action = step?.action;
  const set = (patch) => onChange({ ...step, ...patch });
  const setParam = (k, v) => onChange({ ...step, params: { ...(step.params ?? {}), [k]: v } });
  const changeAction = (a) => {
    const current = stepPins(step);
    onChange(newStep(a, current.length ? current : [focusPin ?? pins[0]], step?.at_s ?? 0, sound));
  };
  // Shown even with one pin picked at the top, so a step on that pin can
  // take more pins with it.
  const pinField = pins.length > 1 || stepPins(step).some((p) => !pins.includes(p)) ? (
    <PinChips value={stepPins(step)} pins={pins} pinConfigs={pinConfigs}
      onChange={(v) => onChange({ ...step, params: withPins(step.params, v) })} />
  ) : null;

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
              .filter(([a]) => (a !== "repeat" || depth === 0)
                && (a !== "play" || action === "play" || (sound?.modules ?? []).length > 0))
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
        {action === "play" && <PlayFields step={step} sound={sound} onChange={onChange} />}
        {action === "repeat" && (
          <>
            <NumField label="Times" int min={1} value={step.times} onChange={(v) => set({ times: v })} width="4.5em" />
            <label className="pe-field">
              <span className="pe-field-label">Spacing</span>
              <select value={"spread_s" in step ? "spread" : "regular"}
                onChange={(e) => onChange(toSpacing(step, e.target.value))}>
                <option value="regular">Regular gaps</option>
                <option value="spread">Spread over a window</option>
              </select>
            </label>
            {"spread_s" in step ? (
              <>
                <NumField label="Within" unit="s" value={step.spread_s} onChange={(v) => set({ spread_s: v })} width="5.5em"
                  title="Every repetition starts within this many seconds of the block's At, placed pseudo-randomly (seeded)" />
                <NumField label="Min gap" unit="s" value={step.min_gap_s ?? 0} onChange={(v) => set({ min_gap_s: v })} width="5em"
                  title="Shortest time from one repetition's start to the next" />
              </>
            ) : (
              <>
                <NumField label="Every" unit="s" value={step.every_s} onChange={(v) => set({ every_s: v })} width="5.5em"
                  title="Gap from one repetition's start to the next, before jitter" />
                <NumField label="+ jitter" value={step.jitter_s?.[0]} width="4.5em"
                  title="Extra random gap per repetition, drawn between these two values (seeded)"
                  onChange={(v) => set({ jitter_s: [v, step.jitter_s?.[1] ?? 0] })} />
                <NumField label="to" unit="s" value={step.jitter_s?.[1]} width="4.5em"
                  onChange={(v) => set({ jitter_s: [step.jitter_s?.[0] ?? 0, v] })} />
              </>
            )}
          </>
        )}
        <button type="button" className="pe-remove" onClick={onRemove} title="Remove step">✕</button>
      </div>

      {action === "repeat" && (
        <div className="pe-repeat-body">
          {(step.steps ?? []).map((inner, i) => (focusPin !== null && !touchesPin(inner, focusPin) && inner?.action !== "play" ? null : (
            <StepRow
              key={i}
              step={inner}
              path={`${path}.*.${i}`}
              pins={pins}
              pinConfigs={pinConfigs}
              sound={sound}
              depth={depth + 1}
              focusPin={focusPin}
              onChange={(s) => set({ steps: step.steps.map((x, j) => (j === i ? s : x)) })}
              onRemove={() => set({ steps: step.steps.filter((_, j) => j !== i) })}
            />
          )))}
          <button type="button" className="pe-add pe-add--inner"
            onClick={() => set({ steps: [...(step.steps ?? []), newStep(
              !pins.length && (sound?.modules ?? []).length ? "play" : "pulse", [focusPin ?? pins[0]], 0, sound)] })}>
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
    && ((Number(s.jitter_s?.[1]) || 0) > 0 || "spread_s" in s || usesJitter(s.steps)));
}

// The timeline always covers at least the first 10 min and has room for 8
// rows (TTL lines, sound outputs), steps or not.
const TIMELINE_MIN_S = 600;

// renderTest(result): the Test routine controls, given the current
// validation; cursorT: seconds into a running test, drawn on the timeline.
// renderTest(result, protocol): the Test routine controls; cursorT: seconds
// into a running test, drawn on the timeline. allowClear: show "Clear".
// freePins: lines the TTL module has that aren't Scheduled yet but could be;
// steps may use them, and the editor says which need setting to Scheduled.
export default function ProtocolEditor({ protocol, onChange, scheduledPins, pinConfigs, moduleId,
  soundModules = [], renderTest, cursorT = null, allowClear = true, intro, freePins = [] }) {
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState(null);
  const [pickedPin, setPickedPin] = useState("all");
  const scheduled = useMemo(() => (scheduledPins ?? []).map(Number), [scheduledPins]);
  const usable = useMemo(
    () => [...new Set([...scheduled, ...(freePins ?? []).map(Number)])].sort((a, b) => a - b),
    [scheduled, freePins]);
  const { pins, result, rows, spans } = useProtocolExpansion(protocol, usable, pinConfigs, moduleId, soundModules,
    scheduled);
  const notScheduled = (result?.pins ?? []).filter((p) => !scheduled.includes(p));
  const sound = { modules: soundModules };
  const canPlay = soundModules.length > 0;
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
        <div className="pe-notice">
          No TTL lines to use: {moduleId ? "every line on this TTL module is set to another mode (Pins tab)." : "no TTL module is connected."}
        </div>
      )}
      {notScheduled.length > 0 && (
        <div className="pe-notice">
          Needs {notScheduled.map((p) => pinName(pinConfigs, p)).join(", ")} set to “Scheduled (session protocol)”
          on the TTL card's Pins tab before it can record or be tested.
        </div>
      )}

      <div className="pe-top">
        {pins.length > 1 && (
          <label className="pe-field">
            <span className="pe-field-label">Pin</span>
            <select value={focusPin ?? "all"} onChange={(e) => setPickedPin(e.target.value)}>
              <option value="all">All pins</option>
              {pins.map((p) => {
                const n = steps.filter((st) => touchesPin(st, p)).length;
                return (
                  <option key={p} value={p}>
                    {pinName(pinConfigs, p)} ({n} step{n === 1 ? "" : "s"})
                  </option>
                );
              })}
            </select>
          </label>
        )}
        {/* Length comes from the steps (the last one's end); the protocol has
            no end setting of its own, and pins go inactive when it ends. */}
        <div className="pe-field">
          <span className="pe-field-label">Length</span>
          <span className="pe-length" title="From the steps: when the last one ends">
            {result?.ok && result.durationS > 0
              ? fmtTime(result.durationS, result.durationS < 60) : "–"}
          </span>
        </div>
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
              that drive {pinName(pinConfigs, focusPin)}; new steps go to it.
            </div>
          )}
          {steps.map((s, i) => (focusPin !== null && !touchesPin(s, focusPin) && s?.action !== "play" ? null : (
            <StepRow
              key={i}
              step={s}
              path={String(i)}
              pins={pins}
              pinConfigs={pinConfigs}
              sound={sound}
              depth={0}
              focusPin={focusPin}
              onChange={(ns) => update({ steps: steps.map((x, j) => (j === i ? ns : x)) })}
              onRemove={() => update({ steps: steps.filter((_, j) => j !== i) })}
            />
          )))}
          <div className="pe-add-row">
            {["set", "pulse", "train", ...(canPlay ? ["play"] : []), "repeat"].map((a) => (
              <button key={a} type="button" className="pe-add"
                disabled={a === "play" ? !canPlay : a === "repeat" ? !pins.length && !canPlay : !pins.length}
                onClick={() => {
                  const last = steps.length ? Number(steps[steps.length - 1].at_s) || 0 : 0;
                  update({ steps: [...steps, newStep(a, [focusPin ?? pins[0]], steps.length ? last + 1 : 0.1, sound)] });
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
            Repeats with jitter, or spread over a window, place repetitions at random. This
            number (the seed) picks which random times you get: keep it and every session (and
            every animal) gets the same schedule, the one in the timeline below; change it for
            a different one. The times used are saved with each session's data.
          </p>
        </div>
      )}

      {result && (
        <div className="pe-check">
          <div className={`pe-summary${result.ok ? "" : " pe-summary--bad"}`}>
            <span className="pe-summary-icon" aria-hidden="true">{result.ok ? "✓" : "✕"}</span>
            {result.ok
              ? `Valid: ${[
                result.edges.length || !result.cues.length
                  ? `${result.edges.length} edges on ${result.pins.length ? result.pins.map((p) => pinName(pinConfigs, p)).join(", ") : "no pins"}` : null,
                result.cues.length
                  ? `${result.cues.length} sound${result.cues.length === 1 ? "" : "s"} on output ${result.outputs.join(", ")}` : null,
              ].filter(Boolean).join(", ")}, last at ${fmtTime(result.durationS, result.durationS < 60)}`
              : `${errors.length} problem${errors.length === 1 ? "" : "s"}: the modules will refuse to record with this protocol`}
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
          minDuration={TIMELINE_MIN_S}
          alwaysShow
          minRows={8}
          highlightRow={focusPin}
          cursorT={cursorT}
          emptyText="Set a TTL pin to Scheduled or connect a sound module to get a row here."
        />
      </div>
    </div>
  );
}
