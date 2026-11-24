"""
Session protocols: expand and validate a timed step list (plans/session-protocol.md).

Phase 1 slice: TTL output actions only (``set``, ``pulse``, ``train``) plus
``repeat`` blocks (seeded jitter, or spread over a window). The TTL module runs the result
(``ttl_module.py``, pin mode ``scheduled``).

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
  seed, same placement.
- ``pins`` (a list) in place of ``pin`` drives several pins with one step:
  the same edges on each, at the same instants.
- A ``module`` key on a step is optional; ``expand(..., module_id=...)``
  keeps steps with no module or that module. A repeat's ``module`` applies
  to the steps inside it. Blocks for another module are still walked, only
  emitting nothing, so every module draws the same random gaps in the same
  order and modules running one protocol stay in step.

``expand`` returns every edge in time order with errors and warnings. A
protocol with errors must not run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SUPPORTED_VERSION = 1
END_STATES = ("all_inactive", "hold")
ACTIONS = ("set", "pulse", "train")

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


@dataclass
class Expansion:
    edges: list[Edge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end_state: str = "all_inactive"

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def duration_s(self) -> float:
        return self.edges[-1].t_s if self.edges else 0.0

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


def expand(protocol: dict, *, module_id: str | None = None,
           allowed_pins: set[int] | None = None) -> Expansion:
    """Expand *protocol* into time-ordered edges and validate it.

    *allowed_pins*: the pins the protocol may drive (the TTL module passes its
    ``scheduled`` pins); None skips that check.
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
    steps = protocol.get("steps")
    if not isinstance(steps, list):
        errors.append("protocol needs a 'steps' list")
        return out

    rng = Mulberry32(seed)
    edges: list[Edge] = []

    def walk(block: list, offset: float, prefix: str, owner=None) -> None:
        # owner: the module a containing repeat names (None = any). A step
        # for another module emits nothing and reports nothing here (that
        # module checks it), but a repeat for one is still walked so the
        # random draws stay in the same order on every module.
        for i, step in enumerate(block):
            where = f"{prefix}{i}"
            if len(edges) > MAX_EDGES:
                return
            if not isinstance(step, dict):
                errors.append(f"step {where}: must be an object")
                continue
            mod = step.get("module") or owner
            skip = module_id is not None and mod not in (None, module_id)
            errs = [] if skip else errors
            if skip and step.get("action") != "repeat":
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
                    draws = sorted(rng.uniform(0.0, free) if free > 0 else 0.0
                                   for _ in range(times))
                    for k, u in enumerate(draws):
                        walk(inner, t0 + u + k * gap, f"{where}.{k}.", mod)
                        if len(edges) > MAX_EDGES:
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
                    if len(edges) > MAX_EDGES:
                        return
                continue

            if action not in ACTIONS:
                errors.append(f"step {where}: unknown action {action!r} "
                              f"(TTL actions: {', '.join(ACTIONS)}, repeat)")
                continue
            params = step.get("params")
            if not isinstance(params, dict):
                errors.append(f"step {where}: '{action}' needs a 'params' object")
                continue
            pins = _pins(params, where, errors, allowed_pins)

            if action == "set":
                level = params.get("level")
                if level not in ("high", "low"):
                    errors.append(f"step {where}: level must be 'high' or 'low' "
                                  f"(got {level!r})")
                    continue
                for pin in pins or ():
                    edges.append(Edge(t0, pin, level, where, action))

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
                if 2 * count * len(pins) + len(edges) > MAX_EDGES:
                    errors.append(f"step {where}: too many edges "
                                  f"(limit {MAX_EDGES})")
                    return
                for pin in pins:
                    for n in range(count):
                        on = t0 + n * period
                        edges.append(Edge(on, pin, "high", where, action))
                        edges.append(Edge(on + width, pin, "low", where, action))

    walk(steps, 0.0, "")
    if len(edges) > MAX_EDGES:
        errors.append(f"protocol expands to more than {MAX_EDGES} edges")
        return out

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
    return out


def state_at(edges: list[Edge], t_s: float) -> dict[int, str]:
    """Each pin's level just after *t_s*, from the edges at or before it."""
    state: dict[int, str] = {}
    for e in edges:
        if e.t_s > t_s:
            break
        state[e.pin] = e.level
    return state
