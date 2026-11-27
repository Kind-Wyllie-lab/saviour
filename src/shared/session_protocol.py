"""
Session protocols: expand and validate a timed step list (plans/session-protocol.md).

Actions: TTL outputs (``set``, ``pulse``, ``train``; the TTL module runs
them on its ``scheduled`` pins, ``ttl_module.py``) and sound (``play``; the
sound module runs them, ``sound_module.py``), plus ``repeat`` blocks (seeded
jitter, or spread over a window). One protocol can drive both: each module
expands the whole protocol with ``module_type`` set and keeps its own steps.

Format (v1)::

    {"version": 1, "seed": 1234, "end_state": "all_inactive",
     "steps": [
        {"at_s": 0,   "action": "set",   "params": {"pin": 17, "level": "high"}},
        {"at_s": 20,  "action": "train", "params": {"pin": 17, "rate_hz": 10,
                                                    "width_s": 0.02, "count": 50}},
        {"at_s": 60,  "action": "repeat", "times": 10, "every_s": 2,
         "jitter_s": [0, 3],
         "steps": [{"at_s": 0, "action": "pulse",
                    "params": {"pin": 22, "width_s": 0.05}}]}]}

- ``at_s`` is seconds from session start (inside a repeat, from that
  repetition's start).
- Levels are logical: ``high`` = the pin's active state, ``low`` = inactive,
  so ``ttl.active_logic`` applies as it does to every other output mode and
  the CSV's HIGH/LOW match.
- ``repeat``: repetition 0 starts at the block's ``at_s``; each later one
  starts ``every_s`` plus a draw from ``jitter_s`` = [lo, hi] after the
  previous one. Draws come from mulberry32 seeded with ``seed``: a small PRNG
  that a JavaScript twin can reproduce exactly (Python's Mersenne Twister
  can't be matched cheaply in the browser).
- ``repeat`` spread over a window (instead of ``every_s``): ``spread_s``
  and optional ``min_gap_s``. The ``times`` repetitions start pseudo-randomly
  between ``at_s`` and ``at_s + spread_s``, at least ``min_gap_s`` apart
  (start to start): ``times`` draws in [0, spread_s - (times-1)*min_gap_s],
  sorted, the k-th plus k*min_gap_s, so every valid placement is equally
  likely. E.g. 10 trials pseudo-randomly over 20 min, ≥ 60 s apart. Same
  seed, same placement. With ``max_gap_s`` too (the editor's "Random" with a
  max gap), each of the ``times - 1`` gaps (start to start) is drawn in
  [min_gap_s, max_gap_s]; if they add up to more than ``spread_s`` their
  parts above the minimum are scaled down to fit; the run then starts at a
  draw in [0, spread_s - their sum]. Gaps first, then that offset.
- Top-level ``duration_s`` (optional): how long the protocol is meant to
  run, e.g. for a timed session to take its length from. Must be > 0; steps
  that run past it are a warning (nothing is stopped there).
- ``train`` takes ``count`` pulses, or ``duration_s`` instead: every pulse
  that starts within it, ``ceil(duration_s * rate_hz)`` (10 s at 5 Hz = 50).
- ``set`` takes an optional ``duration_s``: hold the level that long, then
  switch to the other level (a second edge from the same step); absent or 0
  holds it until another step changes it.
- ``pins`` (a list) in place of ``pin`` drives several pins with one step:
  the same edges on each, at the same instants.
- A ``module`` key on a step is optional; ``expand(..., module_id=...)``
  keeps steps with no module or that module. A repeat's ``module`` applies
  to the steps inside it. Blocks for another module are still walked, only
  emitting nothing, so every module draws the same random gaps in the same
  order and modules running one protocol stay in step.

- ``play`` (sound): ``{"file": "tone_5k.wav", "output": 1, "volume": 1,
  "duration_s": 2}``. ``file`` is a name in the module's sounds folder;
  ``output`` the stereo output (1 = channels 1-2, 2 = 3-4, ...; a DAC8x has
  4, a DAC Pro 1; default 1); ``volume`` a gain factor (default: the
  module's ``sound.volume``); ``duration_s`` cuts the sound short (default:
  the whole file). Two plays on one output must not overlap, checked where
  the length is known (``duration_s``, or the file's length when the caller
  passes ``sound_files``).

``expand`` returns every edge (TTL) and cue (sound) in time order with errors
and warnings. A protocol with errors must not run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SUPPORTED_VERSION = 1
END_STATES = ("all_inactive", "hold")
TTL_ACTIONS = ("set", "pulse", "train")
SOUND_ACTIONS = ("play",)
ACTIONS = TTL_ACTIONS + SOUND_ACTIONS
MODULE_TYPES = ("ttl", "sound")
# The type of module that runs each action.
ACTION_TYPE = {**{a: "ttl" for a in TTL_ACTIONS}, **{a: "sound" for a in SOUND_ACTIONS}}
# A play's gain factor above this is refused (1 = the file as recorded).
MAX_VOLUME = 4.0

# A step later than this still fires but is counted as late. Measured on the
# desk rig 2026-11-16: worst 0.39 ms over 3,600 edges in 30 min (the t = 0
# edge ~3 ms); 5 ms flags a real stall without tripping on those.
LATE_THRESHOLD_S = 0.005
# Below these the editor warns: a Python thread can't hold them reliably.
MIN_WIDTH_S = 0.005
MAX_RATE_HZ = 50.0
# Guard against a typo (count: 1e7) building a huge list on a module.
MAX_EDGES = 200_000


@dataclass(frozen=True)
class Edge:
    t_s: float      # seconds from session start
    pin: int
    level: str      # "high" (active) | "low" (inactive)
    step: str       # step path, e.g. "3" or "4.2.0" (block 4, repetition 2, step 0)
    action: str

    def to_dict(self) -> dict:
        return {"t_s": round(self.t_s, 6), "pin": self.pin, "level": self.level,
                "step": self.step, "action": self.action}


@dataclass(frozen=True)
class Cue:
    """A sound to start: *file* on *output* at session time *t_s*."""
    t_s: float
    output: int
    file: str
    volume: float | None      # None = the module's sound.volume
    duration_s: float | None  # requested cut; None = the whole file
    length_s: float | None    # how long it plays, where known
    step: str
    action: str = "play"

    @property
    def end_s(self) -> float:
        return self.t_s + (self.length_s or 0.0)

    def to_dict(self) -> dict:
        return {"t_s": round(self.t_s, 6), "output": self.output, "file": self.file,
                "volume": self.volume, "duration_s": self.duration_s,
                "length_s": None if self.length_s is None else round(self.length_s, 6),
                "step": self.step, "action": self.action}


@dataclass
class Expansion:
    edges: list[Edge] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end_state: str = "all_inactive"

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def duration_s(self) -> float:
        last_edge = self.edges[-1].t_s if self.edges else 0.0
        last_cue = max((c.end_s for c in self.cues), default=0.0)
        return max(last_edge, last_cue)

    @property
    def outputs(self) -> list[int]:
        return sorted({c.output for c in self.cues})

    @property
    def pins(self) -> list[int]:
        return sorted({e.pin for e in self.edges})


class Mulberry32:
    """Tiny seeded PRNG, chosen so the frontend can reproduce the draws."""

    def __init__(self, seed: int):
        self.state = int(seed) & 0xFFFFFFFF

    def random(self) -> float:
        self.state = (self.state + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.state
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.random()


def _num(value, name: str, where: str, errors: list[str], *,
         minimum: float | None = None, exclusive_min: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        errors.append(f"step {where}: {name} must be a number (got {value!r})")
        return None
    if minimum is not None:
        if exclusive_min and value <= minimum:
            errors.append(f"step {where}: {name} must be > {minimum} (got {value})")
            return None
        if not exclusive_min and value < minimum:
            errors.append(f"step {where}: {name} must be ≥ {minimum} (got {value})")
            return None
    return float(value)


def _int(value, name: str, where: str, errors: list[str], minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        errors.append(f"step {where}: {name} must be a whole number ≥ {minimum} "
                      f"(got {value!r})")
        return None
    return value


def _pin(value, where: str, errors: list[str],
         allowed_pins: set[int] | None) -> int | None:
    try:
        pin = int(value)
    except (TypeError, ValueError):
        errors.append(f"step {where}: pin must be a GPIO number (got {value!r})")
        return None
    if isinstance(value, bool) or (isinstance(value, float) and value != pin):
        errors.append(f"step {where}: pin must be a GPIO number (got {value!r})")
        return None
    if allowed_pins is not None and pin not in allowed_pins:
        have = ", ".join(str(p) for p in sorted(allowed_pins)) or "none"
        errors.append(f"step {where}: pin {pin} is not a 'scheduled' output pin "
                      f"(scheduled pins: {have})")
        return None
    return pin


def _pins(params: dict, where: str, errors: list[str],
          allowed_pins: set[int] | None) -> list[int] | None:
    """A step's pins: ``pin`` (one) or ``pins`` (a list, driven together)."""
    if "pins" in params:
        if "pin" in params:
            errors.append(f"step {where}: give pin or pins, not both")
            return None
        value = params["pins"]
        if not isinstance(value, list) or not value:
            errors.append(f"step {where}: pins must be a non-empty list of GPIO "
                          f"numbers (got {value!r})")
            return None
        pins = [_pin(v, where, errors, allowed_pins) for v in value]
        if any(p is None for p in pins):
            return None
        dupes = sorted({p for p in pins if pins.count(p) > 1})
        if dupes:
            errors.append(f"step {where}: pin {dupes[0]} is listed twice")
            return None
        return pins
    pin = _pin(params.get("pin"), where, errors, allowed_pins)
    return None if pin is None else [pin]


def _sound_file(value, where: str, errors: list[str],
                sound_files: dict | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"step {where}: file must be a sound file name (got {value!r})")
        return None
    if "/" in value or "\\" in value or value.startswith("."):
        errors.append(f"step {where}: file must be a plain file name, not a path "
                      f"(got {value!r})")
        return None
    if sound_files is not None and value not in sound_files:
        have = ", ".join(sorted(sound_files)) or "none"
        errors.append(f"step {where}: no sound file {value!r} on this module "
                      f"(files: {have})")
        return None
    return value


def expand(protocol: dict, *, module_id: str | None = None,
           allowed_pins: set[int] | None = None,
           module_type: str | None = None,
           sound_files: dict | None = None,
           outputs: int | None = None,
           stretchable=None) -> Expansion:
    """Expand *protocol* into time-ordered edges and cues and validate it.

    *module_type*: "ttl" or "sound" keeps only that module type's actions
    (the others are neither run nor checked); None keeps every action.
    *allowed_pins*: the pins the protocol may drive (the TTL module passes its
    ``scheduled`` pins); None skips that check. *sound_files*: file name ->
    length in seconds (None if unknown) on the sound module; None skips the
    file check. *outputs*: the sound module's output count; None skips it.
    *stretchable*: names of built sounds the module renders at a step's
    ``duration_s`` (their length is the cut, not capped by the file).
    """
    out = Expansion()
    errors, warnings = out.errors, out.warnings

    if not isinstance(protocol, dict):
        errors.append("protocol must be a JSON object")
        return out
    version = protocol.get("version", SUPPORTED_VERSION)
    if version != SUPPORTED_VERSION:
        errors.append(f"unsupported protocol version {version!r} "
                      f"(this module understands {SUPPORTED_VERSION})")
        return out
    end_state = protocol.get("end_state", "all_inactive")
    if end_state not in END_STATES:
        errors.append(f"end_state must be one of {', '.join(END_STATES)} "
                      f"(got {end_state!r})")
    else:
        out.end_state = end_state
    seed = protocol.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        errors.append(f"seed must be a whole number (got {seed!r})")
        seed = 0
    # duration_s (optional): how long the protocol is meant to run; a timed
    # session can take its length from it. Steps past it are only warned
    # about: the modules don't stop anything at that point.
    declared = protocol.get("duration_s")
    if declared is not None and (isinstance(declared, bool)
                                 or not isinstance(declared, (int, float))
                                 or not math.isfinite(declared) or declared <= 0):
        errors.append("duration_s must be a positive number of seconds")
        declared = None
    steps = protocol.get("steps")
    if not isinstance(steps, list):
        errors.append("protocol needs a 'steps' list")
        return out

    if module_type is not None and module_type not in MODULE_TYPES:
        errors.append(f"unknown module type {module_type!r}")
        return out

    rng = Mulberry32(seed)
    edges: list[Edge] = []
    cues: list[Cue] = []

    def walk(block: list, offset: float, prefix: str, owner=None) -> None:
        # owner: the module a containing repeat names (None = any). A step
        # for another module emits nothing and reports nothing here (that
        # module checks it), but a repeat for one is still walked so the
        # random draws stay in the same order on every module.
        for i, step in enumerate(block):
            where = f"{prefix}{i}"
            if len(edges) + len(cues) > MAX_EDGES:
                return
            if not isinstance(step, dict):
                errors.append(f"step {where}: must be an object")
                continue
            mod = step.get("module") or owner
            skip = module_id is not None and mod not in (None, module_id)
            errs = [] if skip else errors
            if skip and step.get("action") != "repeat":
                continue
            if module_type is not None \
                    and ACTION_TYPE.get(step.get("action"), module_type) != module_type:
                continue
            at = _num(step.get("at_s", 0), "at_s", where, errs, minimum=0)
            if at is None:
                continue
            t0 = offset + at
            action = step.get("action")

            if action == "repeat":
                times = _int(step.get("times"), "times", where, errs, 1)
                if "spread_s" in step:
                    if "every_s" in step:
                        errs.append(f"step {where}: give every_s or spread_s, not both")
                        continue
                    spread = _num(step.get("spread_s"), "spread_s", where, errs,
                                  minimum=0)
                    gap = _num(step.get("min_gap_s", 0), "min_gap_s", where, errs,
                               minimum=0)
                    inner = step.get("steps")
                    if not isinstance(inner, list) or not inner:
                        errs.append(f"step {where}: repeat needs a non-empty 'steps' list")
                        continue
                    max_gap = step.get("max_gap_s")
                    if max_gap is not None:
                        max_gap = _num(max_gap, "max_gap_s", where, errs, minimum=0)
                        if max_gap is None:
                            continue
                        if gap is not None and max_gap < gap:
                            errs.append(f"step {where}: max_gap_s ({max_gap:g}) is "
                                        f"below min_gap_s ({gap:g})")
                            continue
                    if times is None or spread is None or gap is None:
                        continue
                    free = spread - (times - 1) * gap
                    if free < -1e-9:
                        errs.append(
                            f"step {where}: {times} repetitions at least {gap:g} s apart "
                            f"need a spread of at least {(times - 1) * gap:g} s "
                            f"(got {spread:g} s)")
                        continue
                    free = max(free, 0.0)
                    if max_gap is not None:
                        gaps = [rng.uniform(gap, max_gap) if max_gap > gap else gap
                                for _ in range(times - 1)]
                        extra = sum(gaps) - (times - 1) * gap
                        if extra > free > 0 or (extra > 0 and free == 0):
                            gaps = [gap + (g - gap) * (free / extra) for g in gaps]
                        slack = max(spread - sum(gaps), 0.0)
                        start = t0 + (rng.uniform(0.0, slack) if slack > 0 else 0.0)
                        for k in range(times):
                            if k > 0:
                                start += gaps[k - 1]
                            walk(inner, start, f"{where}.{k}.", mod)
                            if len(edges) + len(cues) > MAX_EDGES:
                                return
                        continue
                    draws = sorted(rng.uniform(0.0, free) if free > 0 else 0.0
                                   for _ in range(times))
                    for k, u in enumerate(draws):
                        walk(inner, t0 + u + k * gap, f"{where}.{k}.", mod)
                        if len(edges) + len(cues) > MAX_EDGES:
                            return
                    continue
                every = _num(step.get("every_s"), "every_s", where, errs,
                             minimum=0, exclusive_min=True)
                jitter = step.get("jitter_s", [0, 0])
                lo = hi = 0.0
                if (not isinstance(jitter, list) or len(jitter) != 2
                        or any(isinstance(j, bool) or not isinstance(j, (int, float))
                               for j in jitter)
                        or not 0 <= jitter[0] <= jitter[1]):
                    errs.append(f"step {where}: jitter_s must be [lo, hi] "
                                  f"with 0 ≤ lo ≤ hi (got {jitter!r})")
                    jitter = None
                else:
                    lo, hi = float(jitter[0]), float(jitter[1])
                inner = step.get("steps")
                if not isinstance(inner, list) or not inner:
                    errs.append(f"step {where}: repeat needs a non-empty 'steps' list")
                    continue
                if times is None or every is None or jitter is None:
                    continue
                start = t0
                for k in range(times):
                    if k > 0:
                        start += every + (rng.uniform(lo, hi) if hi > 0 else 0.0)
                    walk(inner, start, f"{where}.{k}.", mod)
                    if len(edges) + len(cues) > MAX_EDGES:
                        return
                continue

            if action not in ACTIONS:
                errors.append(f"step {where}: unknown action {action!r} "
                              f"(TTL: {', '.join(TTL_ACTIONS)}; sound: "
                              f"{', '.join(SOUND_ACTIONS)}; repeat)")
                continue
            params = step.get("params")
            if not isinstance(params, dict):
                errors.append(f"step {where}: '{action}' needs a 'params' object")
                continue

            if action == "play":
                name = _sound_file(params.get("file"), where, errors, sound_files)
                output = _int(params.get("output", 1), "output", where, errors, 1)
                if output is not None and outputs is not None and output > outputs:
                    errors.append(f"step {where}: output {output} doesn't exist "
                                  f"(this module has {outputs} output"
                                  f"{'' if outputs == 1 else 's'})")
                    output = None
                volume = params.get("volume")
                if volume is not None:
                    volume = _num(volume, "volume", where, errors, minimum=0)
                    if volume is not None and volume > MAX_VOLUME:
                        errors.append(f"step {where}: volume {volume} is above "
                                      f"{MAX_VOLUME:g}")
                        volume = None
                    elif volume is not None and volume > 1:
                        warnings.append(f"step {where}: volume {volume} is above 1; "
                                        f"loud parts of the sound may clip")
                    if volume is None:
                        continue
                cut = params.get("duration_s")
                if cut is not None:
                    cut = _num(cut, "duration_s", where, errors,
                               minimum=0, exclusive_min=True)
                    if cut is None:
                        continue
                if name is None or output is None:
                    continue
                file_len = (sound_files or {}).get(name)
                if isinstance(file_len, bool) or not isinstance(file_len, (int, float)):
                    file_len = None
                # A built sound (stretchable) is rendered at the cut's length,
                # so the cut isn't capped by its library copy.
                if cut is not None and stretchable and name in stretchable:
                    file_len = None
                lengths = [x for x in (cut, file_len) if x is not None]
                cues.append(Cue(t0, output, name, volume, cut,
                                min(lengths) if lengths else None, where))
                continue

            pins = _pins(params, where, errors, allowed_pins)

            if action == "set":
                level = params.get("level")
                if level not in ("high", "low"):
                    errors.append(f"step {where}: level must be 'high' or 'low' "
                                  f"(got {level!r})")
                    continue
                # duration_s (optional): hold the level this long, then the
                # other level; absent or 0 = until another step changes it.
                hold = params.get("duration_s", 0)
                if hold is None or (not isinstance(hold, bool) and hold == 0):
                    hold = 0.0
                else:
                    hold = _num(hold, "duration_s", where, errors, minimum=0)
                    if hold is None:
                        continue
                    if 0 < hold < MIN_WIDTH_S:
                        warnings.append(f"step {where}: duration_s {hold} is below "
                                        f"{MIN_WIDTH_S} s; timing may not hold")
                back = "low" if level == "high" else "high"
                for pin in pins or ():
                    edges.append(Edge(t0, pin, level, where, action))
                    if hold > 0:
                        edges.append(Edge(t0 + hold, pin, back, where, action))

            elif action == "pulse":
                width = _num(params.get("width_s"), "width_s", where, errors,
                             minimum=0, exclusive_min=True)
                if pins is None or width is None:
                    continue
                if width < MIN_WIDTH_S:
                    warnings.append(f"step {where}: width_s {width} is below "
                                    f"{MIN_WIDTH_S} s; timing may not hold")
                for pin in pins:
                    edges.append(Edge(t0, pin, "high", where, action))
                    edges.append(Edge(t0 + width, pin, "low", where, action))

            elif action == "train":
                rate = _num(params.get("rate_hz"), "rate_hz", where, errors,
                            minimum=0, exclusive_min=True)
                width = _num(params.get("width_s"), "width_s", where, errors,
                             minimum=0, exclusive_min=True)
                # count, or duration_s: every pulse that starts within it
                # (ceil(duration_s * rate_hz)).
                if "duration_s" in params:
                    if "count" in params:
                        errors.append(f"step {where}: give count or duration_s, not both")
                        continue
                    dur = _num(params.get("duration_s"), "duration_s", where, errors,
                               minimum=0, exclusive_min=True)
                    count = (None if dur is None or rate is None
                             else max(1, math.ceil(dur * rate - 1e-9)))
                else:
                    count = _int(params.get("count"), "count", where, errors, 1)
                if pins is None or rate is None or width is None or count is None:
                    continue
                period = 1.0 / rate
                if width >= period:
                    errors.append(f"step {where}: width_s {width} must be shorter "
                                  f"than the period 1/rate_hz = {period:.6g} s")
                    continue
                if rate > MAX_RATE_HZ:
                    warnings.append(f"step {where}: rate_hz {rate} is above "
                                    f"{MAX_RATE_HZ} Hz; timing may not hold")
                if width < MIN_WIDTH_S:
                    warnings.append(f"step {where}: width_s {width} is below "
                                    f"{MIN_WIDTH_S} s; timing may not hold")
                if 2 * count * len(pins) + len(edges) + len(cues) > MAX_EDGES:
                    errors.append(f"step {where}: too many edges "
                                  f"(limit {MAX_EDGES})")
                    return
                for pin in pins:
                    for n in range(count):
                        on = t0 + n * period
                        edges.append(Edge(on, pin, "high", where, action))
                        edges.append(Edge(on + width, pin, "low", where, action))

    walk(steps, 0.0, "")
    if len(edges) + len(cues) > MAX_EDGES:
        errors.append(f"protocol expands to more than {MAX_EDGES} edges")
        return out

    # Sound: one play at a time per output, where the lengths are known.
    cues.sort(key=lambda c: c.t_s)
    last_on: dict[int, Cue] = {}
    for c in cues:
        prev = last_on.get(c.output)
        if prev is not None and prev.length_s is not None \
                and prev.end_s > c.t_s + 1e-9:
            errors.append(
                f"step {c.step}: output {c.output} is still playing {prev.file} "
                f"(step {prev.step}, until {prev.end_s:.3f} s) at {c.t_s:.3f} s; "
                f"plays overlap")
        last_on[c.output] = c
    out.cues = cues

    # Ties: a falling edge before a rising one at the same instant, so a
    # pulse that ends exactly where the next begins is back-to-back, not an
    # overlap.
    edges.sort(key=lambda e: (e.t_s, 0 if e.level == "low" else 1))

    # Overlap: on each pin the edges must alternate, starting from low.
    level_now: dict[int, tuple[str, str]] = {}
    for e in edges:
        prev_level, prev_step = level_now.get(e.pin, ("low", ""))
        if e.level == prev_level:
            if e.level == "high":
                errors.append(
                    f"step {e.step}: pin {e.pin} is already high at "
                    f"{e.t_s:.3f} s (from step {prev_step}); steps overlap")
            elif e.action == "set":
                warnings.append(
                    f"step {e.step}: pin {e.pin} is already low at {e.t_s:.3f} s")
            else:
                errors.append(
                    f"step {e.step}: pin {e.pin} goes low at {e.t_s:.3f} s "
                    f"while another step holds it; steps overlap")
        level_now[e.pin] = (e.level, e.step)

    out.edges = edges
    if declared is not None and out.duration_s > declared + 1e-9:
        warnings.append(f"steps run until {out.duration_s:.3f} s, past the "
                        f"protocol's duration ({declared:g} s)")
    return out


def module_tags(protocol) -> set[str]:
    """Every module ID a step (or repeat block) names in *protocol*."""
    found: set[str] = set()

    def walk(block) -> None:
        for step in block if isinstance(block, list) else ():
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("module"), str) and step["module"]:
                found.add(step["module"])
            walk(step.get("steps"))

    walk((protocol or {}).get("steps") if isinstance(protocol, dict) else None)
    return found


def state_at(edges: list[Edge], t_s: float) -> dict[int, str]:
    """Each pin's level just after *t_s*, from the edges at or before it."""
    state: dict[int, str] = {}
    for e in edges:
        if e.t_s > t_s:
            break
        state[e.pin] = e.level
    return state
