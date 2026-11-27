// Session protocols: expand and validate a timed step list in the browser.
//
// A line-for-line twin of src/shared/session_protocol.py (read its docstring
// for the format). Both run the cases in
// src/shared/session_protocol_fixtures.json and must agree edge for edge
// (src/tests/test_session_protocol.py runs this file under node), so the
// preview shows exactly what the module will run.

export const SUPPORTED_VERSION = 1;
export const END_STATES = ["all_inactive", "hold"];
export const TTL_ACTIONS = ["set", "pulse", "train"];
export const SOUND_ACTIONS = ["play"];
export const ACTIONS = [...TTL_ACTIONS, ...SOUND_ACTIONS];
export const MODULE_TYPES = ["ttl", "sound"];
export const ACTION_TYPE = Object.fromEntries([
  ...TTL_ACTIONS.map((a) => [a, "ttl"]), ...SOUND_ACTIONS.map((a) => [a, "sound"])]);
export const MAX_VOLUME = 4.0;
export const LATE_THRESHOLD_S = 0.005;
export const MIN_WIDTH_S = 0.005;
export const MAX_RATE_HZ = 50.0;
export const MAX_EDGES = 200_000;

// What each action takes, for the editor.
export const ACTION_PARAMS = {
  set:   [{ key: "level", kind: "level" }, { key: "duration_s", kind: "number", unit: "s", step: "any", min: 0, optional: true }],
  pulse: [{ key: "width_s", kind: "number", unit: "s", step: "any", min: 0 }],
  train: [
    { key: "rate_hz", kind: "number", unit: "Hz", step: "any", min: 0 },
    { key: "width_s", kind: "number", unit: "s", step: "any", min: 0 },
    { key: "count", kind: "int", unit: "", step: 1, min: 1 },
  ],
};

export function mulberry32(seed) {
  let a = seed >>> 0;
  return {
    random() {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    },
    uniform(lo, hi) {
      return lo + (hi - lo) * this.random();
    },
  };
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isInt = (v) => Number.isInteger(v);
const show = (v) => (v === undefined ? "None" : JSON.stringify(v));
const fmtG = (v) => String(Number(v.toPrecision(6)));

function num(value, name, where, errors, { minimum = null, exclusiveMin = false } = {}) {
  if (!isNum(value)) {
    errors.push(`step ${where}: ${name} must be a number (got ${show(value)})`);
    return null;
  }
  if (minimum !== null) {
    if (exclusiveMin && value <= minimum) {
      errors.push(`step ${where}: ${name} must be > ${minimum} (got ${value})`);
      return null;
    }
    if (!exclusiveMin && value < minimum) {
      errors.push(`step ${where}: ${name} must be ≥ ${minimum} (got ${value})`);
      return null;
    }
  }
  return value;
}

function int(value, name, where, errors, minimum) {
  if (!isInt(value) || value < minimum) {
    errors.push(`step ${where}: ${name} must be a whole number ≥ ${minimum} (got ${show(value)})`);
    return null;
  }
  return value;
}

function pinOf(value, where, errors, allowedPins) {
  const pin = typeof value === "string" && /^-?\d+$/.test(value.trim())
    ? Number(value) : value;
  if (!isInt(pin)) {
    errors.push(`step ${where}: pin must be a GPIO number (got ${show(value)})`);
    return null;
  }
  if (allowedPins && !allowedPins.has(pin)) {
    const have = [...allowedPins].sort((a, b) => a - b).join(", ") || "none";
    errors.push(`step ${where}: pin ${pin} is not a 'scheduled' output pin (scheduled pins: ${have})`);
    return null;
  }
  return pin;
}

// A step's pins: `pin` (one) or `pins` (a list, driven together).
function pinsOf(params, where, errors, allowedPins) {
  if ("pins" in params) {
    if ("pin" in params) {
      errors.push(`step ${where}: give pin or pins, not both`);
      return null;
    }
    const value = params.pins;
    if (!Array.isArray(value) || value.length === 0) {
      errors.push(`step ${where}: pins must be a non-empty list of GPIO numbers (got ${show(value)})`);
      return null;
    }
    const pins = value.map((v) => pinOf(v, where, errors, allowedPins));
    if (pins.some((p) => p === null)) return null;
    const dupes = [...new Set(pins.filter((p, i) => pins.indexOf(p) !== i))].sort((a, b) => a - b);
    if (dupes.length) {
      errors.push(`step ${where}: pin ${dupes[0]} is listed twice`);
      return null;
    }
    return pins;
  }
  const pin = pinOf(params.pin, where, errors, allowedPins);
  return pin === null ? null : [pin];
}

/** The pins a (non-repeat) step names, for the editor: [] when none. */
export function stepPins(step) {
  const p = step?.params ?? {};
  const raw = Array.isArray(p.pins) ? p.pins : (p.pin === undefined ? [] : [p.pin]);
  return raw.map(Number).filter(Number.isInteger);
}

function soundFile(value, where, errors, soundFiles) {
  if (typeof value !== "string" || !value.trim()) {
    errors.push(`step ${where}: file must be a sound file name (got ${show(value)})`);
    return null;
  }
  if (value.includes("/") || value.includes("\\") || value.startsWith(".")) {
    errors.push(`step ${where}: file must be a plain file name, not a path (got ${show(value)})`);
    return null;
  }
  if (soundFiles && !Object.prototype.hasOwnProperty.call(soundFiles, value)) {
    const have = Object.keys(soundFiles).sort().join(", ") || "none";
    errors.push(`step ${where}: no sound file ${show(value)} on this module (files: ${have})`);
    return null;
  }
  return value;
}

export const cueEnd = (c) => c.t_s + (c.length_s ?? 0);

/**
 * Expand a protocol into time-ordered edges (TTL) and cues (sound) and
 * validate it. Options as in Python: moduleType ("ttl" | "sound" | null),
 * allowedPins, soundFiles ({name: length_s | null}), outputs, stretchable
 * (built sounds rendered at the step's duration_s: not capped by the file).
 * Returns { edges: [{t_s, pin, level, step, action}],
 *           cues: [{t_s, output, file, volume, duration_s, length_s, step, action}],
 *           errors, warnings, endState, ok, durationS, pins, outputs }.
 */
export function expand(protocol, { moduleId = null, allowedPins = null, moduleType = null,
  soundFiles = null, outputs = null, stretchable = null } = {}) {
  const out = { edges: [], cues: [], errors: [], warnings: [], endState: "all_inactive" };
  const { errors, warnings } = out;
  const finish = (complete = false) => {
    const lastEdge = out.edges.length ? out.edges[out.edges.length - 1].t_s : 0;
    out.durationS = Math.max(lastEdge, ...out.cues.map(cueEnd), 0);
    if (complete && out.declaredS && out.durationS > out.declaredS + 1e-9) {
      warnings.push(`steps run until ${out.durationS.toFixed(3)} s, past the protocol's duration (${fmtG(out.declaredS)} s)`);
    }
    out.ok = errors.length === 0;
    out.pins = [...new Set(out.edges.map((e) => e.pin))].sort((a, b) => a - b);
    out.outputs = [...new Set(out.cues.map((c) => c.output))].sort((a, b) => a - b);
    return out;
  };
  const allowed = allowedPins ? new Set(allowedPins) : null;

  if (!protocol || typeof protocol !== "object" || Array.isArray(protocol)) {
    errors.push("protocol must be a JSON object");
    return finish();
  }
  const version = protocol.version ?? SUPPORTED_VERSION;
  if (version !== SUPPORTED_VERSION) {
    errors.push(`unsupported protocol version ${show(version)} (this module understands ${SUPPORTED_VERSION})`);
    return finish();
  }
  const endState = protocol.end_state ?? "all_inactive";
  if (!END_STATES.includes(endState)) {
    errors.push(`end_state must be one of ${END_STATES.join(", ")} (got ${show(endState)})`);
  } else {
    out.endState = endState;
  }
  let seed = protocol.seed ?? 0;
  if (!isInt(seed)) {
    errors.push(`seed must be a whole number (got ${show(seed)})`);
    seed = 0;
  }
  // duration_s (optional): see src/shared/session_protocol.py.
  let declared = protocol.duration_s ?? null;
  if (declared !== null && !(isNum(declared) && declared > 0)) {
    errors.push("duration_s must be a positive number of seconds");
    declared = null;
  }
  out.declaredS = declared;
  const steps = protocol.steps;
  if (!Array.isArray(steps)) {
    errors.push("protocol needs a 'steps' list");
    return finish();
  }

  if (moduleType !== null && !MODULE_TYPES.includes(moduleType)) {
    errors.push(`unknown module type ${show(moduleType)}`);
    return finish();
  }

  const rng = mulberry32(seed);
  const edges = [];
  const cues = [];
  let capped = false;

  // owner: the module a containing repeat names (null = any). Same rule as
  // Python: another module's repeat is still walked, so draws stay in order.
  const walk = (block, offset, prefix, owner = null) => {
    for (let i = 0; i < block.length; i++) {
      const where = `${prefix}${i}`;
      if (edges.length + cues.length > MAX_EDGES) return;
      const step = block[i];
      if (!step || typeof step !== "object" || Array.isArray(step)) {
        errors.push(`step ${where}: must be an object`);
        continue;
      }
      const mod = step.module || owner;
      const skip = moduleId !== null && mod !== null && mod !== moduleId;
      const errs = skip ? [] : errors;
      if (skip && step.action !== "repeat") continue;
      if (moduleType !== null && (ACTION_TYPE[step.action] ?? moduleType) !== moduleType) continue;
      const at = num(step.at_s ?? 0, "at_s", where, errs, { minimum: 0 });
      if (at === null) continue;
      const t0 = offset + at;
      const action = step.action;

      if (action === "repeat") {
        const times = int(step.times, "times", where, errs, 1);
        if ("spread_s" in step) {
          // Random within a window: see src/shared/session_protocol.py.
          if ("every_s" in step) {
            errs.push(`step ${where}: give every_s or spread_s, not both`);
            continue;
          }
          const spread = num(step.spread_s, "spread_s", where, errs, { minimum: 0 });
          const gap = num(step.min_gap_s ?? 0, "min_gap_s", where, errs, { minimum: 0 });
          const inner = step.steps;
          if (!Array.isArray(inner) || inner.length === 0) {
            errs.push(`step ${where}: repeat needs a non-empty 'steps' list`);
            continue;
          }
          let maxGap = step.max_gap_s ?? null;
          if (maxGap !== null) {
            maxGap = num(maxGap, "max_gap_s", where, errs, { minimum: 0 });
            if (maxGap === null) continue;
            if (gap !== null && maxGap < gap) {
              errs.push(`step ${where}: max_gap_s (${fmtG(maxGap)}) is below min_gap_s (${fmtG(gap)})`);
              continue;
            }
          }
          if (times === null || spread === null || gap === null) continue;
          let free = spread - (times - 1) * gap;
          if (free < -1e-9) {
            errs.push(`step ${where}: ${times} repetitions at least ${fmtG(gap)} s apart need a spread of at least ${fmtG((times - 1) * gap)} s (got ${fmtG(spread)} s)`);
            continue;
          }
          free = Math.max(free, 0);
          if (maxGap !== null) {
            // Random with a max gap: see src/shared/session_protocol.py.
            let gaps = [];
            for (let k = 0; k < times - 1; k++) gaps.push(maxGap > gap ? rng.uniform(gap, maxGap) : gap);
            const extra = gaps.reduce((a, b) => a + b, 0) - (times - 1) * gap;
            if ((extra > free && free > 0) || (extra > 0 && free === 0)) {
              gaps = gaps.map((g) => gap + (g - gap) * (free / extra));
            }
            const slack = Math.max(spread - gaps.reduce((a, b) => a + b, 0), 0);
            let start = t0 + (slack > 0 ? rng.uniform(0, slack) : 0);
            for (let k = 0; k < times; k++) {
              if (k > 0) start += gaps[k - 1];
              walk(inner, start, `${where}.${k}.`, mod);
              if (edges.length + cues.length > MAX_EDGES) return;
            }
            continue;
          }
          const draws = [];
          for (let k = 0; k < times; k++) draws.push(free > 0 ? rng.uniform(0, free) : 0);
          draws.sort((a, b) => a - b);
          for (let k = 0; k < times; k++) {
            walk(inner, t0 + draws[k] + k * gap, `${where}.${k}.`, mod);
            if (edges.length + cues.length > MAX_EDGES) return;
          }
          continue;
        }
        const every = num(step.every_s, "every_s", where, errs, { minimum: 0, exclusiveMin: true });
        let jitter = step.jitter_s ?? [0, 0];
        let lo = 0, hi = 0;
        if (!Array.isArray(jitter) || jitter.length !== 2 || !jitter.every(isNum)
            || !(0 <= jitter[0] && jitter[0] <= jitter[1])) {
          errs.push(`step ${where}: jitter_s must be [lo, hi] with 0 ≤ lo ≤ hi (got ${show(jitter)})`);
          jitter = null;
        } else {
          [lo, hi] = jitter;
        }
        const inner = step.steps;
        if (!Array.isArray(inner) || inner.length === 0) {
          errs.push(`step ${where}: repeat needs a non-empty 'steps' list`);
          continue;
        }
        if (times === null || every === null || jitter === null) continue;
        let start = t0;
        for (let k = 0; k < times; k++) {
          if (k > 0) start += every + (hi > 0 ? rng.uniform(lo, hi) : 0);
          walk(inner, start, `${where}.${k}.`, mod);
          if (edges.length + cues.length > MAX_EDGES) return;
        }
        continue;
      }

      if (!ACTIONS.includes(action)) {
        errors.push(`step ${where}: unknown action ${show(action)} (TTL: ${TTL_ACTIONS.join(", ")}; sound: ${SOUND_ACTIONS.join(", ")}; repeat)`);
        continue;
      }
      const params = step.params;
      if (!params || typeof params !== "object" || Array.isArray(params)) {
        errors.push(`step ${where}: '${action}' needs a 'params' object`);
        continue;
      }

      if (action === "play") {
        const name = soundFile(params.file, where, errors, soundFiles);
        let output = int(params.output ?? 1, "output", where, errors, 1);
        if (output !== null && outputs !== null && output > outputs) {
          errors.push(`step ${where}: output ${output} doesn't exist (this module has ${outputs} output${outputs === 1 ? "" : "s"})`);
          output = null;
        }
        let volume = params.volume ?? null;
        if (volume !== null) {
          volume = num(volume, "volume", where, errors, { minimum: 0 });
          if (volume !== null && volume > MAX_VOLUME) {
            errors.push(`step ${where}: volume ${volume} is above ${MAX_VOLUME}`);
            volume = null;
          } else if (volume !== null && volume > 1) {
            warnings.push(`step ${where}: volume ${volume} is above 1; loud parts of the sound may clip`);
          }
          if (volume === null) continue;
        }
        let cut = params.duration_s ?? null;
        if (cut !== null) {
          cut = num(cut, "duration_s", where, errors, { minimum: 0, exclusiveMin: true });
          if (cut === null) continue;
        }
        if (name === null || output === null) continue;
        let fileLen = soundFiles ? soundFiles[name] : null;
        if (!isNum(fileLen)) fileLen = null;
        if (cut !== null && stretchable && [...stretchable].includes(name)) fileLen = null;
        const lengths = [cut, fileLen].filter((x) => x !== null);
        cues.push({ t_s: t0, output, file: name, volume, duration_s: cut,
          length_s: lengths.length ? Math.min(...lengths) : null, step: where, action });
        continue;
      }
      const pins = pinsOf(params, where, errors, allowed);

      if (action === "set") {
        const level = params.level;
        if (level !== "high" && level !== "low") {
          errors.push(`step ${where}: level must be 'high' or 'low' (got ${show(level)})`);
          continue;
        }
        // duration_s (optional): hold the level this long, then the other
        // level; absent or 0 = until another step changes it.
        let hold = params.duration_s ?? 0;
        if (hold !== 0) {
          hold = num(hold, "duration_s", where, errors, { minimum: 0 });
          if (hold === null) continue;
          if (hold > 0 && hold < MIN_WIDTH_S) {
            warnings.push(`step ${where}: duration_s ${hold} is below ${MIN_WIDTH_S} s; timing may not hold`);
          }
        }
        const back = level === "high" ? "low" : "high";
        for (const pin of pins ?? []) {
          edges.push({ t_s: t0, pin, level, step: where, action });
          if (hold > 0) edges.push({ t_s: t0 + hold, pin, level: back, step: where, action });
        }
      } else if (action === "pulse") {
        const width = num(params.width_s, "width_s", where, errors, { minimum: 0, exclusiveMin: true });
        if (pins === null || width === null) continue;
        if (width < MIN_WIDTH_S) {
          warnings.push(`step ${where}: width_s ${width} is below ${MIN_WIDTH_S} s; timing may not hold`);
        }
        for (const pin of pins) {
          edges.push({ t_s: t0, pin, level: "high", step: where, action });
          edges.push({ t_s: t0 + width, pin, level: "low", step: where, action });
        }
      } else if (action === "train") {
        const rate = num(params.rate_hz, "rate_hz", where, errors, { minimum: 0, exclusiveMin: true });
        const width = num(params.width_s, "width_s", where, errors, { minimum: 0, exclusiveMin: true });
        const count = int(params.count, "count", where, errors, 1);
        if (pins === null || rate === null || width === null || count === null) continue;
        const period = 1 / rate;
        if (width >= period) {
          errors.push(`step ${where}: width_s ${width} must be shorter than the period 1/rate_hz = ${Number(period.toPrecision(6))} s`);
          continue;
        }
        if (rate > MAX_RATE_HZ) {
          warnings.push(`step ${where}: rate_hz ${rate} is above ${MAX_RATE_HZ} Hz; timing may not hold`);
        }
        if (width < MIN_WIDTH_S) {
          warnings.push(`step ${where}: width_s ${width} is below ${MIN_WIDTH_S} s; timing may not hold`);
        }
        if (2 * count * pins.length + edges.length + cues.length > MAX_EDGES) {
          errors.push(`step ${where}: too many edges (limit ${MAX_EDGES})`);
          capped = true;
          return;
        }
        for (const pin of pins) {
          for (let n = 0; n < count; n++) {
            const on = t0 + n * period;
            edges.push({ t_s: on, pin, level: "high", step: where, action });
            edges.push({ t_s: on + width, pin, level: "low", step: where, action });
          }
        }
      }
    }
  };

  walk(steps, 0, "");
  if (capped) return finish();
  if (edges.length + cues.length > MAX_EDGES) {
    errors.push(`protocol expands to more than ${MAX_EDGES} edges`);
    return finish();
  }

  // Sound: one play at a time per output, where the lengths are known.
  cues.sort((a, b) => a.t_s - b.t_s);
  const lastOn = new Map();
  for (const c of cues) {
    const prev = lastOn.get(c.output);
    if (prev && prev.length_s !== null && cueEnd(prev) > c.t_s + 1e-9) {
      errors.push(`step ${c.step}: output ${c.output} is still playing ${prev.file} (step ${prev.step}, until ${cueEnd(prev).toFixed(3)} s) at ${c.t_s.toFixed(3)} s; plays overlap`);
    }
    lastOn.set(c.output, c);
  }
  out.cues = cues;

  // Same tie rule as Python: falling before rising at the same instant. A
  // stable sort keeps listing order otherwise, as Python's sort does.
  edges.sort((a, b) => (a.t_s - b.t_s) || ((a.level === "low" ? 0 : 1) - (b.level === "low" ? 0 : 1)));

  const levelNow = new Map();
  for (const e of edges) {
    const [prevLevel, prevStep] = levelNow.get(e.pin) ?? ["low", ""];
    if (e.level === prevLevel) {
      if (e.level === "high") {
        errors.push(`step ${e.step}: pin ${e.pin} is already high at ${e.t_s.toFixed(3)} s (from step ${prevStep}); steps overlap`);
      } else if (e.action === "set") {
        warnings.push(`step ${e.step}: pin ${e.pin} is already low at ${e.t_s.toFixed(3)} s`);
      } else {
        errors.push(`step ${e.step}: pin ${e.pin} goes low at ${e.t_s.toFixed(3)} s while another step holds it; steps overlap`);
      }
    }
    levelNow.set(e.pin, [e.level, e.step]);
  }

  out.edges = edges;
  return finish(true);
}

/** Every module ID a step (or repeat block) names in a protocol. */
export function moduleTags(protocol) {
  const found = new Set();
  const walk = (block) => {
    for (const step of Array.isArray(block) ? block : []) {
      if (!step || typeof step !== "object") continue;
      if (typeof step.module === "string" && step.module) found.add(step.module);
      walk(step.steps);
    }
  };
  walk(protocol && typeof protocol === "object" ? protocol.steps : null);
  return found;
}

/** Pair each pin's edges into high intervals for the timeline:
 *  [{pin, start, end, step, action}], end = null for a level still high. */
export function highSpans(edges) {
  const open = new Map();
  const spans = [];
  for (const e of edges) {
    if (e.level === "high") {
      if (!open.has(e.pin)) open.set(e.pin, e);
    } else if (open.has(e.pin)) {
      const s = open.get(e.pin);
      spans.push({ pin: e.pin, start: s.t_s, end: e.t_s, step: s.step, action: s.action });
      open.delete(e.pin);
    }
  }
  for (const s of open.values()) {
    spans.push({ pin: s.pin, start: s.t_s, end: null, step: s.step, action: s.action });
  }
  return spans.sort((a, b) => a.start - b.start);
}

/** "mm:ss" (or "h:mm:ss") from seconds, with ms below 10 s spans. */
export function fmtTime(s, withMs = false) {
  const sign = s < 0 ? "-" : "";
  const abs = Math.abs(s);
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  const sec = abs % 60;
  const secStr = withMs ? sec.toFixed(3).padStart(6, "0") : String(Math.floor(sec)).padStart(2, "0");
  return h ? `${sign}${h}:${String(m).padStart(2, "0")}:${secStr}`
           : `${sign}${String(m).padStart(2, "0")}:${secStr}`;
}
