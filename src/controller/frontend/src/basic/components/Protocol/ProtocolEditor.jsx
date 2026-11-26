import { useMemo, useState } from "react";
import ProtocolTimeline from "../ProtocolTimeline/ProtocolTimeline";
import { fmtTime, stepPins } from "../../utils/sessionProtocol";
import useProtocolExpansion from "./useProtocolExpansion";
import { pinLabel, pinName } from "./pinNames";
import { EMPTY_PROTOCOL, normaliseProtocol, usedOutputs } from "./protocolLibrary";
import "./ProtocolEditor.css";

// A session protocol's steps as a table, checked as you type by the same
// expansion the module runs (sessionProtocol.js, the twin of
// src/shared/session_protocol.py), with a timeline preview. Used in the
// Protocol designer modal on a library protocol (ProtocolDesigner.jsx).
//
// The protocol names the lines and sound outputs it uses (`outputs`); the
// table groups steps by line, numbered per line, and repeats are trial
// blocks with each line's steps inside. A step is on one line (a protocol
// with params.pins is split on load, normaliseProtocol); a repeat inside a
// trial block is edited in the JSON view.

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

// A play step's fields: which sound module (when there are several), file,
// output, and optional volume and length (blank = the module's setting /
// the whole file).
// fixedOutput: the output is the line the step sits on, so no picker.
function PlayFields({ step, sound, onChange, fixedOutput = false }) {
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
      {!fixedOutput && <label className="pe-field" title="Stereo output: 1 = channels 1-2, 2 = 3-4, …">
        <span className="pe-field-label">Output</span>
        <select value={params.output ?? 1} onChange={(e) => setParam("output", Number(e.target.value))}>
          {outputList.map((o) => <option key={o} value={o}>{o}{o > outputs ? " (not on the card)" : ""}</option>)}
        </select>
      </label>}
      <NumField label="Volume" value={params.volume} width="4.5em" placeholder="module"
        title="Gain factor (1 = the file as recorded); blank uses the module's volume setting"
        onChange={(v) => setParam("volume", v)} />
      <NumField label="Length" unit="s" value={params.duration_s} width="5em" placeholder="whole"
        title="Cut the sound to this many seconds; blank plays the whole file"
        onChange={(v) => setParam("duration_s", v)} />
    </>
  );
}


// True when some repeat block draws random gaps (jitter upper bound > 0):
// only then does the seed change anything.
function usesJitter(steps) {
  return (steps ?? []).some((s) => s && s.action === "repeat"
    && ((Number(s.jitter_s?.[1]) || 0) > 0 || "spread_s" in s || usesJitter(s.steps)));
}


// Which line a step is on: "ttl:<pin>" or "snd:<output>"; null for a repeat
// (a trial block) or anything the table can't place.
function trackOf(step) {
  if (!step || typeof step !== "object") return null;
  if (step.action === "play") return `snd:${Number(step.params?.output) || 1}`;
  if (TTL_ACTIONS.includes(step.action)) {
    const pins = stepPins(step);
    return pins.length === 1 ? `ttl:${pins[0]}` : null;
  }
  return null;
}

const TTL_ACTIONS = ["set", "pulse", "train"];

// Sound output n is a stereo pair: channels 2n-1 and 2n (sound_hardware.py).
const chans = (o) => `${2 * o - 1}-${2 * o}`;

// The rows of the table and timeline: [{ key, kind, n, label, sub }].
function tracksFor(outputs, pinConfigs) {
  return [
    ...outputs.ttl.map((p) => {
      const label = pinLabel(pinConfigs, p);
      return { key: `ttl:${p}`, kind: "ttl", n: p, label: label ?? `GPIO ${p}`, sub: label ? `GPIO ${p}` : "TTL line" };
    }),
    ...outputs.sound.map((o) => ({ key: `snd:${o}`, kind: "snd", n: o, label: `Sound ${o}`, sub: `stereo output ${o} · channels ${chans(o)}` })),
  ];
}

// Names for step paths ("0", "2.*.1") as the table numbers them: per line,
// and per line inside each trial block. Used to reword the checker's
// messages and the timeline's tooltips.
function stepNames(steps, tracks) {
  const label = Object.fromEntries(tracks.map((t) => [t.key, t.label]));
  const names = {};
  const name = (list, prefix, before) => {
    const count = {};
    list.forEach((s, i) => {
      const k = trackOf(s);
      const path = prefix === null ? String(i) : `${prefix}.*.${i}`;
      if (k) {
        count[k] = (count[k] ?? 0) + 1;
        names[path] = `${before}${label[k] ?? k} step ${count[k]}`;
      }
    });
  };
  name(steps, null, "");
  let b = 0;
  steps.forEach((s, i) => {
    if (s?.action !== "repeat") return;
    b += 1;
    names[String(i)] = `trial block ${b}`;
    name(s.steps ?? [], String(i), `trial block ${b}, `);
  });
  return names;
}

// The expander writes a step in a trial as "<block>.<trial>.<step>" (trial
// from 0); "<block>.*.<step>" means every trial.
const renameSteps = (msg, names) =>
  msg.replace(/\bstep (\d+)(?:\.(\d+|\*)\.(\d+))?(?![.\d])/g, (m, top, trial, inner) => {
    if (inner === undefined) return names[top] ?? m;
    const name = names[`${top}.*.${inner}`];
    if (!name) return m;
    return trial === "*" ? name : `${name} (trial ${Number(trial) + 1})`;
  });

// Lines on the TTL module in another mode: not usable here, drawn on the
// timeline for reference. Returns { rows, spans }.
const REF_MODES = {
  experiment_start: "experiment start",
  experiment_clock: "experiment clock",
  pseudorandom: "pseudorandom sync",
  interval_pulse: "interval pulses",
};
function referenceLines(pinConfigs) {
  const rows = [];
  const spans = [];
  for (const [p, c] of Object.entries(pinConfigs ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]))) {
    const mode = c?.mode;
    if (!REF_MODES[mode]) continue;
    const key = `ref:${p}`;
    const label = pinLabel(pinConfigs, Number(p)) ?? `GPIO ${p}`;
    rows.push({ key, label, sublabel: REF_MODES[mode], ref: true });
    const num = (v, d) => (Number.isFinite(Number(v)) && v !== "" && v !== null ? Number(v) : d);
    if (mode === "experiment_start") {
      const t = num(c.delay_s, 0.001);
      spans.push({ row: key, start: t, end: t + num(c.pulse_duration_s, 0.02), label: "experiment start pulse", ref: true });
    } else if (mode === "interval_pulse" && num(c.repeat_count, 0) > 0 && num(c.repeat_count, 0) <= 2000) {
      const every = num(c.interval_s, 120);
      const w = num(c.pulse_duration_s, 0.02);
      for (let i = 0; i < num(c.repeat_count, 0); i += 1) {
        spans.push({ row: key, start: i * every, end: i * every + w, label: `interval pulse ${i + 1}`, ref: true });
      }
    } else {
      const what = mode === "experiment_clock"
        ? `clock: ${num(c.period, 1)} s period, ${Math.round(num(c.duty_cycle, 0.75) * 100)} % high, for the whole recording`
        : mode === "pseudorandom"
          ? `random sync pulses ${num(c.min_interval, 0.1)}-${num(c.max_interval, 1)} s apart, for the whole recording`
          : "pulses for the whole recording";
      spans.push({ row: key, start: 0, end: null, label: what, ref: true });
    }
  }
  return { rows, spans };
}

// One step on a line: number, start time, what it does. The line itself
// (pin, sound output) is implied by the group it sits in.
function StepLine({ step, num, track, sound, inBlock, onChange, onRemove }) {
  const action = step?.action;
  const set = (patch) => onChange({ ...step, ...patch });
  const setParam = (k, v) => onChange({ ...step, params: { ...(step.params ?? {}), [k]: v } });
  return (
    <div className="pe-step">
      <div className="pe-step-main">
        <span className="pe-path" title={`${track.label} step ${num}`}>{num}</span>
        <NumField label="At" unit="s" value={step.at_s} onChange={(v) => set({ at_s: v })}
          title={inBlock ? "Seconds from the start of each trial" : "Seconds from session start"} />
        {track.kind === "ttl" && (
          <label className="pe-field">
            <span className="pe-field-label">Action</span>
            <select value={action ?? ""}
              onChange={(e) => onChange(newStep(e.target.value, [track.n], step?.at_s ?? 0, sound))}>
              {TTL_ACTIONS.map((a) => <option key={a} value={a}>{ACTION_LABELS[a]}</option>)}
            </select>
          </label>
        )}
        {action === "set" && (
          <>
            <label className="pe-field">
              <span className="pe-field-label">Level</span>
              <select value={step.params?.level ?? ""} onChange={(e) => setParam("level", e.target.value)}>
                <option value="high">High (active)</option>
                <option value="low">Low (inactive)</option>
              </select>
            </label>
            <NumField label="For" unit="s" value={step.params?.duration_s} width="5em" placeholder="0"
              title="Hold the level this long, then switch to the other level; 0 or blank = until another step changes it"
              onChange={(v) => {
                // eslint-disable-next-line no-unused-vars -- blank drops the key
                const { duration_s, ...rest } = step.params ?? {};
                onChange({ ...step, params: v === "" ? rest : { ...rest, duration_s: v } });
              }} />
            {!(Number(step.params?.duration_s) > 0) && <span className="pe-hint-inline">indefinite</span>}
          </>
        )}
        {action === "pulse" && (
          <NumField label="Width" unit="s" value={step.params?.width_s} onChange={(v) => setParam("width_s", v)} />
        )}
        {action === "train" && (
          <>
            <NumField label="Rate" unit="Hz" value={step.params?.rate_hz} onChange={(v) => setParam("rate_hz", v)} width="4.5em" />
            <NumField label="Width" unit="s" value={step.params?.width_s} onChange={(v) => setParam("width_s", v)} width="4.5em" />
            <NumField label="Count" int min={1} value={step.params?.count} onChange={(v) => setParam("count", v)} width="4.5em" />
          </>
        )}
        {action === "play" && <PlayFields step={step} sound={sound} onChange={onChange} fixedOutput />}
        <button type="button" className="pe-remove" onClick={onRemove} title="Remove step">✕</button>
      </div>
    </div>
  );
}

// The steps of one list (the protocol's top level, or one trial block),
// grouped by line, numbered per line. steps: the list; onSteps: its new
// value. Steps the table can't place (a nested repeat, a step on several
// pins left in the JSON) are listed at the end, editable as JSON.
function TrackGroups({ steps, tracks, sound, inBlock, onSteps, flags = {} }) {
  const canPlay = (sound?.modules ?? []).length > 0;
  const add = (track, action) => {
    const mine = steps.filter((s) => trackOf(s) === track.key);
    const last = mine.length ? Number(mine[mine.length - 1].at_s) || 0 : null;
    const at = last === null ? (inBlock ? 0 : 0.1) : last + 1;
    const step = newStep(action, [track.n], at, sound);
    if (action === "play") step.params = { ...step.params, output: track.n };
    onSteps([...steps, step]);
  };
  const others = steps.map((s, i) => [s, i])
    .filter(([s]) => !trackOf(s) && !(s?.action === "repeat" && !inBlock));
  return (
    <div className="pe-tracks">
      {tracks.map((t) => {
        const mine = steps.map((s, i) => [s, i]).filter(([s]) => trackOf(s) === t.key);
        return (
          <div key={t.key} className={`pe-track${mine.length ? "" : " pe-track--empty"}`}>
            <div className="pe-track-head">
              <span className="pe-track-name">{t.label}</span>
              <span className="pe-track-sub">{t.sub}</span>
              {flags[t.key] && <span className="pe-track-flag" title={flags[t.key]}>⚠ {flags[t.key]}</span>}
              <span className="pe-track-add">
                {(t.kind === "ttl" ? TTL_ACTIONS : ["play"]).map((a) => (
                  <button key={a} type="button" className="pe-add" disabled={a === "play" && !canPlay}
                    title={a === "play" && !canPlay ? "No sound module connected" : undefined}
                    onClick={() => add(t, a)}>
                    + {ACTION_LABELS[a]}
                  </button>
                ))}
              </span>
            </div>
            {mine.map(([s, i], k) => (
              <StepLine key={i} step={s} num={k + 1} track={t} sound={sound} inBlock={inBlock}
                onChange={(ns) => onSteps(steps.map((x, j) => (j === i ? ns : x)))}
                onRemove={() => onSteps(steps.filter((_, j) => j !== i))} />
            ))}
          </div>
        );
      })}
      {others.map(([s, i]) => (
        <div key={`o${i}`} className="pe-step pe-step--locked">
          <span>
            {s?.action === "repeat" ? "A repeat inside a trial block" : `A ${String(s?.action)} step the table can't place`}
            {" "}at {s?.at_s ?? 0} s: edit it in the JSON view.
          </span>
          <button type="button" className="pe-remove" onClick={() => onSteps(steps.filter((_, j) => j !== i))}
            title="Remove step">✕</button>
        </div>
      ))}
    </div>
  );
}

// A trial block: a repeat with its spacing, and inside it each line's steps,
// timed from the start of each trial. Random gaps are drawn once per trial,
// so every line moves together.
function TrialBlock({ step, num, tracks, sound, onChange, onRemove, flags }) {
  const set = (patch) => onChange({ ...step, ...patch });
  return (
    <div className="pe-block">
      <div className="pe-block-head">
        <span className="pe-block-name">Trial block {num}</span>
        <NumField label="Starts at" unit="s" value={step.at_s} onChange={(v) => set({ at_s: v })}
          title="Seconds from session start to the first trial" />
        <NumField label="Trials" int min={1} value={step.times} onChange={(v) => set({ times: v })} width="4.5em" />
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
              title="Every trial starts within this many seconds of the block's start, placed pseudo-randomly (seeded)" />
            <NumField label="Min gap" unit="s" value={step.min_gap_s ?? 0} onChange={(v) => set({ min_gap_s: v })} width="5em"
              title="Shortest time from one trial's start to the next" />
          </>
        ) : (
          <>
            <NumField label="Every" unit="s" value={step.every_s} onChange={(v) => set({ every_s: v })} width="5.5em"
              title="Gap from one trial's start to the next, before jitter" />
            <NumField label="+ jitter" value={step.jitter_s?.[0]} width="4.5em"
              title="Extra random gap per trial, drawn between these two values (seeded)"
              onChange={(v) => set({ jitter_s: [v, step.jitter_s?.[1] ?? 0] })} />
            <NumField label="to" unit="s" value={step.jitter_s?.[1]} width="4.5em"
              onChange={(v) => set({ jitter_s: [step.jitter_s?.[0] ?? 0, v] })} />
          </>
        )}
        <button type="button" className="pe-remove" onClick={onRemove} title="Remove the trial block">✕</button>
      </div>
      <TrackGroups steps={step.steps ?? []} tracks={tracks} sound={sound} inBlock flags={flags}
        onSteps={(s) => set({ steps: s })} />
    </div>
  );
}

// Which lines and sound outputs the protocol uses: TTL lines from those set
// to Scheduled on the TTL module (Settings), sound outputs from the sound
// module. A line with steps stays on; one used but not Scheduled is flagged.
function OutputPicker({ outputs, used, scheduled, pinConfigs, soundOutputs, hasTtl, hasSound, onChange }) {
  const ttlChoices = [...new Set([...scheduled, ...outputs.ttl])].sort((a, b) => a - b);
  const sndChoices = Array.from({ length: Math.max(soundOutputs, ...outputs.sound, 0) }, (_, i) => i + 1);
  const toggle = (kind, n) => {
    const list = outputs[kind];
    const next = list.includes(n) ? list.filter((x) => x !== n) : [...list, n].sort((a, b) => a - b);
    onChange({ ...outputs, [kind]: next });
  };
  const chip = (kind, n, text, sub, bad) => {
    const on = outputs[kind].includes(n);
    const locked = on && used[kind].includes(n);
    return (
      <button key={`${kind}${n}`} type="button" aria-pressed={on} disabled={locked}
        className={`pe-pin${on ? " pe-pin--on" : ""}${bad ? " pe-pin--bad" : ""}`}
        title={locked ? "Has steps: remove them to drop this output" : bad ?? (on ? "Drop this output" : "Use this output")}
        onClick={() => toggle(kind, n)}>
        {text}{sub && <span className="pe-pin-gpio">{sub}</span>}
      </button>
    );
  };
  return (
    <div className="pe-outputs">
      <div className="pe-out-row">
        <span className="pe-out-kind">TTL lines</span>
        <div className="pe-pins">
          {ttlChoices.map((p) => {
            const label = pinLabel(pinConfigs, p);
            const bad = !scheduled.includes(p) ? `GPIO ${p} isn't set to Scheduled on the TTL module` : null;
            return chip("ttl", p, label ?? `GPIO ${p}`, label ? String(p) : null, bad);
          })}
          {ttlChoices.length === 0 && (
            <span className="pe-out-none">
              {hasTtl ? "None set to Scheduled: set a line's mode to Scheduled on the TTL card (Settings)." : "No TTL module connected."}
            </span>
          )}
        </div>
      </div>
      <div className="pe-out-row">
        <span className="pe-out-kind" title="Each output is a stereo pair of the card's channels">Sound outputs (stereo)</span>
        <div className="pe-pins">
          {sndChoices.map((o) => chip("sound", o, `Output ${o}`, `ch ${chans(o)}`,
            o > soundOutputs ? "The sound module doesn't have this output" : null))}
          {sndChoices.length === 0 && (
            <span className="pe-out-none">{hasSound ? "The sound module reports no outputs." : "No sound module connected."}</span>
          )}
        </div>
      </div>
    </div>
  );
}

// The timeline always covers at least the first 10 min and has room for 8
// rows (TTL lines, sound outputs), steps or not.
const TIMELINE_MIN_S = 600;

// renderTest(result, protocol): the Test routine controls; cursorT: seconds
// into a running test, drawn on the timeline. allowClear: show "Clear".
// scheduledPins: the TTL module's lines in Scheduled mode, the ones a
// protocol can use; pinConfigs: its pin settings (labels, other modes).
export default function ProtocolEditor({ protocol, onChange, scheduledPins, pinConfigs, moduleId,
  soundModules = [], renderTest, cursorT = null, allowClear = true, intro }) {
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState(null);
  const scheduled = useMemo(() => (scheduledPins ?? []).map(Number), [scheduledPins]);
  const norm = useMemo(() => normaliseProtocol(protocol ?? EMPTY_PROTOCOL), [protocol]);
  const outputs = norm.outputs;
  const steps = norm.steps;
  const used = useMemo(() => usedOutputs(steps), [steps]);
  // Checked as a design (any pin); whether the rig has the lines Scheduled
  // is a requirement, flagged on the line.
  const { result, rows: stepRows, spans: stepSpans } = useProtocolExpansion(
    protocol ? norm : null, null, pinConfigs, moduleId, soundModules, outputs.ttl);
  const sound = { modules: soundModules };
  const soundOutputs = Math.max(0, ...soundModules.map((m) => Number(m.outputs) || 0));
  const tracks = useMemo(() => tracksFor(outputs, pinConfigs), [outputs, pinConfigs]);
  const names = useMemo(() => stepNames(steps, tracks), [steps, tracks]);
  const flags = Object.fromEntries([
    ...outputs.ttl.filter((p) => !scheduled.includes(p)).map((p) => [`ttl:${p}`,
      moduleId ? "not set to Scheduled on the TTL module" : "no TTL module connected"]),
    ...outputs.sound.filter((o) => o > soundOutputs).map((o) => [`snd:${o}`,
      soundModules.length ? "the sound module has no such output" : "no sound module connected"]),
  ]);

  const update = (patch) => onChange({ ...norm, ...patch });

  // Timeline: the protocol's lines (with steps or not), then the TTL
  // module's lines in other modes, greyed, for reference.
  const ref = useMemo(() => referenceLines(pinConfigs), [pinConfigs]);
  const rows = useMemo(() => {
    const out = [...stepRows];
    for (const o of outputs.sound) {
      if (!out.some((r) => r.key === `snd:${o}`)) out.push({ key: `snd:${o}`, label: `Sound ${o}`, sublabel: `ch ${chans(o)}` });
    }
    return [...out, ...ref.rows];
  }, [stepRows, outputs.sound, ref.rows]);
  const spans = useMemo(() => [
    ...stepSpans.map((s) => ({ ...s, label: renameSteps(s.label, names) })),
    ...ref.spans,
  ], [stepSpans, names, ref.spans]);

  const openJson = () => {
    setJsonText(JSON.stringify(norm, null, 2));
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

  const errors = (result?.errors ?? []).map((m) => renameSteps(m, names));
  const warnings = (result?.warnings ?? []).map((m) => renameSteps(m, names));
  const blocks = steps.map((s, i) => [s, i]).filter(([s]) => s?.action === "repeat");
  const unmet = Object.entries(flags);

  return (
    <div className="pe">
      {intro !== undefined ? intro : (
        <p className="pe-intro">
          Steps run on the modules during every recording, timed from session start on their
          PTP clocks. A TTL line must be in <strong>Scheduled</strong> mode (Pins tab) to be
          used, and manual pulses on it are refused while recording.
        </p>
      )}

      <div className="pe-top">
        <OutputPicker outputs={outputs} used={used} scheduled={scheduled} pinConfigs={pinConfigs}
          soundOutputs={soundOutputs} hasTtl={!!moduleId} hasSound={soundModules.length > 0}
          onChange={(o) => update({ outputs: o })} />
        <div className="pe-top-right">
          <div className="pe-field">
            <span className="pe-field-label">Length</span>
            <span className="pe-length" title="From the steps: when the last one ends">
              {result?.ok && result.durationS > 0 ? fmtTime(result.durationS, result.durationS < 60) : "–"}
            </span>
          </div>
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
          {tracks.length === 0 && (
            <div className="pe-empty">Pick the TTL lines and sound outputs this protocol uses, above.</div>
          )}
          <TrackGroups steps={steps} tracks={tracks} sound={sound} inBlock={false} flags={flags}
            onSteps={(s) => update({ steps: s })} />
          {blocks.map(([s, i], k) => (
            <TrialBlock key={i} step={s} num={k + 1} tracks={tracks} sound={sound} flags={flags}
              onChange={(ns) => update({ steps: steps.map((x, j) => (j === i ? ns : x)) })}
              onRemove={() => update({ steps: steps.filter((_, j) => j !== i) })} />
          ))}
          <div className="pe-add-row">
            <button type="button" className="pe-add" disabled={!tracks.length}
              title="Trials repeated with regular or random gaps; each line's steps inside are timed from each trial's start"
              onClick={() => {
                const last = steps.length ? Math.max(...steps.map((x) => Number(x.at_s) || 0)) : 0;
                update({ steps: [...steps, { at_s: steps.length ? last + 10 : 0, action: "repeat", times: 5, every_s: 30, jitter_s: [0, 0], steps: [] }] });
              }}>
              + Trial block
            </button>
            {steps.length > 1 && (
              <button type="button" className="pe-small" title="Order each line's steps by start time"
                onClick={() => update({ steps: [...steps].sort((a, b) => (Number(a.at_s) || 0) - (Number(b.at_s) || 0)) })}>
                Sort by time
              </button>
            )}
          </div>
        </div>
      )}

      {usesJitter(steps) && (
        <div className="pe-random">
          <div className="pe-random-controls">
            <NumField label="Random gap pattern" int min={0} value={norm.seed ?? 1} width="7em"
              onChange={(v) => update({ seed: v })} />
            <button type="button" className="pe-small"
              onClick={() => update({ seed: Math.floor(Math.random() * 1e6) })}>
              New pattern
            </button>
          </div>
          <p className="pe-random-hint">
            Trial blocks with jitter, or spread over a window, place trials at random. This
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
                  ? `${result.edges.length} edges on ${result.pins.length ? result.pins.map((p) => pinName(pinConfigs, p)).join(", ") : "no lines"}` : null,
                result.cues.length
                  ? `${result.cues.length} sound${result.cues.length === 1 ? "" : "s"} on output ${result.outputs.join(", ")}` : null,
              ].filter(Boolean).join(", ")}, last at ${fmtTime(result.durationS, result.durationS < 60)}`
              : `${errors.length} problem${errors.length === 1 ? "" : "s"}: the modules will refuse to record with this protocol`}
          </div>
          {unmet.length > 0 && (
            <div className="pe-msg pe-msg--warn">
              ⚠ This rig can't run it yet: {unmet.map(([k, why]) => `${tracks.find((t) => t.key === k)?.label ?? k} (${why})`).join("; ")}.
            </div>
          )}
          {errors.slice(0, 8).map((m, i) => <div key={`e${i}`} className="pe-msg pe-msg--error">{m}</div>)}
          {errors.length > 8 && <div className="pe-msg pe-msg--error">…and {errors.length - 8} more</div>}
          {warnings.slice(0, 5).map((m, i) => <div key={`w${i}`} className="pe-msg pe-msg--warn">⚠ {m}</div>)}
          {result.ok && result.edges.length > 0 && result.edges[0].t_s < 0.05 && (
            <div className="pe-msg pe-msg--warn">
              ⚠ The first TTL step at {result.edges[0].t_s} s lands a few ms late (the protocol starts with the
              recording); use 0.05 s or later where exact timing matters.
            </div>
          )}
        </div>
      )}

      {renderTest && renderTest(result, norm)}

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
          cursorT={cursorT}
          emptyText="Pick the TTL lines and sound outputs this protocol uses to get rows here."
        />
      </div>
    </div>
  );
}
