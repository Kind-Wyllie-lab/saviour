"""
SAVIOUR - DMX lighting schedule maths (pure; no I/O, no clock reads).

Everything here takes the time as an argument, so the level at any instant is
a function of ``(now, config, overrides)`` only. Restarts, clock steps and
config edits therefore land on the right level straight away; the module's
slew limiter (:class:`Slew`) is what keeps the change gentle.

Levels are percentages (0-100) of full output, before each strip's trim.

Author: Andrew SG
Created: 11/11/2026
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_MIN = 24 * 60
CURVES = ("perceptual", "cosine", "linear")
IR_MODES = ("constant", "night_only", "off")
ROLES = ("visible", "ir")
DMX_CHANNELS = 512
# Hard ceiling on any override/blackout/test, whatever the config says: a
# forgotten override must not keep lights on overnight.
OVERRIDE_HARD_MAX_MIN = 24 * 60

# Phase names, as they appear in the heartbeat, the UI and lighting.csv.
DAY, NIGHT, SUNRISE, SUNSET = "day", "night", "sunrise", "sunset"


# ── Parsing / validation ────────────────────────────────────────────────────

def parse_hhmm(value) -> int:
    """'07:30' -> 450 (minutes after midnight). Raises ValueError."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise ValueError(f"time must be HH:MM, got {value!r}")
    hh, mm = value.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return h * 60 + m


def resolve_tz(name: str | None):
    """The configured IANA zone; '' / None = the system's local zone."""
    if not name:
        return datetime.now().astimezone().tzinfo
    if name.upper() == "UTC":
        return UTC
    return ZoneInfo(name)


def strip_width(bits: int) -> int:
    return 2 if int(bits) == 16 else 1


def _num(value, lo, hi) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and lo <= value <= hi)


def validate(cfg: dict) -> list[str]:
    """Every problem with a ``dmx_lighting`` config section, as readable
    sentences. Empty list = OK to apply."""
    errors: list[str] = []
    sched = cfg.get("schedule") or {}
    on = off = None
    try:
        on = parse_hhmm(sched.get("lights_on"))
    except (ValueError, TypeError) as e:
        errors.append(f"Lights on: {e}")
    try:
        off = parse_hhmm(sched.get("lights_off"))
    except (ValueError, TypeError) as e:
        errors.append(f"Lights off: {e}")
    rise, fall = sched.get("sunrise_minutes", 0), sched.get("sunset_minutes", 0)
    if not _num(rise, 0, 720):
        errors.append("Sunrise must be 0-720 minutes")
    if not _num(fall, 0, 720):
        errors.append("Sunset must be 0-720 minutes")
    if on is not None and off is not None:
        day_len = (off - on) % DAY_MIN
        if day_len == 0:
            errors.append("Lights on and lights off can't be the same time")
        else:
            if _num(rise, 0, 720) and rise > day_len:
                errors.append(f"Sunrise ({rise} min) is longer than the light "
                              f"period ({day_len} min)")
            if _num(fall, 0, 720) and fall > DAY_MIN - day_len:
                errors.append(f"Sunset ({fall} min) is longer than the dark "
                              f"period ({DAY_MIN - day_len} min)")
    if sched.get("curve", "perceptual") not in CURVES:
        errors.append(f"Curve must be one of {', '.join(CURVES)}")
    for key, label in (("day_level", "Day level"), ("night_level", "Night level")):
        if not _num(sched.get(key, 0), 0, 100):
            errors.append(f"{label} must be 0-100 %")

    ir = cfg.get("ir") or {}
    if ir.get("mode", "constant") not in IR_MODES:
        errors.append(f"IR mode must be one of {', '.join(IR_MODES)}")
    if not _num(ir.get("level", 0), 0, 100):
        errors.append("IR level must be 0-100 %")

    try:
        resolve_tz(cfg.get("timezone") or "")
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        errors.append(f"Unknown time zone {cfg.get('timezone')!r}")

    bits = cfg.get("channel_bits", 8)
    if bits not in (8, 16):
        errors.append("Channel bits must be 8 or 16")
        bits = 8
    if not _num(cfg.get("slew_full_scale_s", 60), 0, 3600):
        errors.append("Slew limit must be 0-3600 s")
    if not _num(cfg.get("max_override_minutes", 240), 1, OVERRIDE_HARD_MAX_MIN):
        errors.append(f"Max override must be 1-{OVERRIDE_HARD_MAX_MIN} minutes")

    errors.extend(validate_patch(cfg.get("compartments") or [], bits))
    return errors


def validate_patch(compartments, bits: int = 8) -> list[str]:
    errors: list[str] = []
    if not isinstance(compartments, list):
        return ["Compartments must be a list"]
    width = strip_width(bits)
    seen_box: set[str] = set()
    used: dict[int, str] = {}
    for i, row in enumerate(compartments):
        if not isinstance(row, dict):
            errors.append(f"Row {i + 1}: not an object")
            continue
        box = str(row.get("box") or "").strip()
        label = box or f"row {i + 1}"
        if not box:
            errors.append(f"Row {i + 1}: box name is empty")
        elif box in seen_box:
            errors.append(f"Box {box} is listed twice")
        seen_box.add(box)
        if not _num(row.get("trim", 100), 0, 100):
            errors.append(f"{label}: trim must be 0-100 %")
        for role in ROLES:
            addr = row.get(role)
            if addr in (None, ""):
                continue
            if not isinstance(addr, int) or isinstance(addr, bool):
                errors.append(f"{label} {role}: address must be a whole number")
                continue
            last = addr + width - 1
            if addr < 1 or last > DMX_CHANNELS:
                errors.append(
                    f"{label} {role}: address {addr} is outside 1-{DMX_CHANNELS}"
                    + (f" (16-bit uses {addr} and {addr + 1})" if width == 2 else ""))
                continue
            for ch in range(addr, last + 1):
                if ch in used:
                    errors.append(f"{label} {role}: channel {ch} is already used "
                                  f"by {used[ch]}")
                    break
                used[ch] = f"{label} {role}"
    return errors


def channel_map(compartments, bits: int = 8) -> list[dict]:
    """[{channel, box, role, part}] sorted by channel; part is 'level' for
    8-bit, 'coarse'/'fine' for 16-bit."""
    width = strip_width(bits)
    out = []
    for row in compartments or []:
        for role in ROLES:
            addr = row.get(role)
            if not isinstance(addr, int) or isinstance(addr, bool):
                continue
            parts = ("coarse", "fine") if width == 2 else ("level",)
            for k, part in enumerate(parts):
                out.append({"channel": addr + k, "box": row.get("box"),
                            "role": role, "part": part})
    return sorted(out, key=lambda r: r["channel"])


# ── Curves ──────────────────────────────────────────────────────────────────

def _lstar(y: float) -> float:
    """CIE 1976 lightness L* (0-100) from relative luminance y (0-1)."""
    y = min(1.0, max(0.0, y))
    return 116 * y ** (1 / 3) - 16 if y > 0.008856 else 903.3 * y


def _lstar_inv(lightness: float) -> float:
    lightness = min(100.0, max(0.0, lightness))
    return ((lightness + 16) / 116) ** 3 if lightness > 8 else lightness / 903.3


def _cos_ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, x)))


def ramp_level(lo: float, hi: float, progress: float, curve: str) -> float:
    """Level (%) a fraction ``progress`` (0 = night, 1 = day) of the way
    between the night level ``lo`` and day level ``hi``."""
    p = min(1.0, max(0.0, progress))
    if curve == "linear":
        return lo + (hi - lo) * p
    if curve == "cosine":
        return lo + (hi - lo) * _cos_ease(p)
    # perceptual: ease in L*, then back to output
    l_lo, l_hi = _lstar(lo / 100), _lstar(hi / 100)
    return 100 * _lstar_inv(l_lo + (l_hi - l_lo) * _cos_ease(p))


# ── The schedule ────────────────────────────────────────────────────────────

@dataclass
class ScheduleState:
    phase: str                 # day / night / sunrise / sunset
    progress: float            # 0 = fully night .. 1 = fully day
    visible: float             # % for visible strips
    ir: float                  # % for IR strips
    next_change: str           # what happens next ("sunrise", "day", ...)
    next_change_at: float      # epoch seconds
    local_time: str            # HH:MM:SS in the schedule's zone

    def to_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


def _wall_minutes(local: datetime) -> float:
    return local.hour * 60 + local.minute + local.second / 60 + local.microsecond / 6e7


def _phase(m: float, on: int, off: int, rise: float, fall: float):
    """(phase, progress) at wall-clock minute ``m``."""
    day_len = (off - on) % DAY_MIN
    since_on = (m - on) % DAY_MIN
    if since_on < day_len:
        if rise > 0 and since_on < rise:
            return SUNRISE, since_on / rise
        return DAY, 1.0
    since_off = (m - off) % DAY_MIN
    if fall > 0 and since_off < fall:
        return SUNSET, 1.0 - since_off / fall
    return NIGHT, 0.0


def _next_change(local: datetime, m: float, on, off, rise, fall, tz):
    events = [(on, SUNRISE if rise > 0 else DAY), (off, SUNSET if fall > 0 else NIGHT)]
    if rise > 0:
        events.append((on + rise, DAY))
    if fall > 0:
        events.append((off + fall, NIGHT))
    best = None
    for at, name in events:
        delta = (at - m) % DAY_MIN
        if delta < 1e-6:
            delta = DAY_MIN
        if best is None or delta < best[0]:
            best = (delta, name, at % DAY_MIN)
    delta, name, at = best
    # Wall-clock target (so DST days come out right), then to epoch.
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    target = midnight + timedelta(minutes=at)
    if at < m:
        target += timedelta(days=1)
    return name, target.replace(tzinfo=tz).timestamp()


def schedule_at(now: float, cfg: dict) -> ScheduleState:
    """Scheduled visible/IR levels at epoch time ``now`` (config assumed
    valid; see :func:`validate`)."""
    sched = cfg.get("schedule") or {}
    tz = resolve_tz(cfg.get("timezone") or "")
    local = datetime.fromtimestamp(now, tz)
    m = _wall_minutes(local)
    on, off = parse_hhmm(sched["lights_on"]), parse_hhmm(sched["lights_off"])
    rise = float(sched.get("sunrise_minutes", 0))
    fall = float(sched.get("sunset_minutes", 0))
    phase, progress = _phase(m, on, off, rise, fall)
    curve = sched.get("curve", "perceptual")
    lo = float(sched.get("night_level", 0))
    hi = float(sched.get("day_level", 100))
    visible = ramp_level(lo, hi, progress, curve)

    ir_cfg = cfg.get("ir") or {}
    ir_level = float(ir_cfg.get("level", 100))
    mode = ir_cfg.get("mode", "constant")
    if mode == "off":
        ir = 0.0
    elif mode == "night_only":
        # opposite of the visible ramp, in the same curve shape
        ir = ramp_level(0.0, ir_level, 1.0 - progress, curve)
    else:
        ir = ir_level
    name, at = _next_change(local, m, on, off, rise, fall, tz)
    return ScheduleState(phase=phase, progress=progress, visible=visible, ir=ir,
                         next_change=name, next_change_at=at,
                         local_time=local.strftime("%H:%M:%S"))


# ── Overrides ───────────────────────────────────────────────────────────────

@dataclass
class Override:
    """A temporary level set on some strips. ``kind``: override (manual,
    from the UI), blackout (visible 0 everywhere) or test (flash one strip).
    ``visible`` / ``ir`` = None leaves that role on its schedule."""
    id: str
    kind: str
    boxes: list[str] | None          # None = every compartment
    visible: float | None
    ir: float | None
    started_at: float
    until: float
    by: str = ""
    role: str | None = None          # test: which strip flashes
    note: str = ""
    extra: dict = field(default_factory=dict)

    def applies_to(self, box: str) -> bool:
        return self.boxes is None or box in self.boxes

    def remaining_s(self, now: float) -> float:
        return max(0.0, self.until - now)

    def to_dict(self, now: float | None = None) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "extra"}
        if now is not None:
            d["remaining_s"] = round(self.remaining_s(now), 1)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Override:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


FLASH_PERIOD_S = 1.0


def compartment_levels(now: float, cfg: dict, sched: ScheduleState,
                       overrides: list[Override]) -> dict[str, dict]:
    """{box: {visible, ir, source_visible, source_ir}} -- scheduled level per
    strip, with active overrides applied in order (later ones win). ``source``
    is 'schedule' or the override kind."""
    out: dict[str, dict] = {}
    for row in cfg.get("compartments") or []:
        box = row["box"]
        lv = {"visible": sched.visible, "ir": sched.ir,
              "source_visible": "schedule", "source_ir": "schedule"}
        for ov in overrides:
            if ov.until <= now or not ov.applies_to(box):
                continue
            if ov.kind == "test":
                if ov.role in ROLES:
                    on = int((now - ov.started_at) / (FLASH_PERIOD_S / 2)) % 2 == 0
                    lv[ov.role] = 100.0 if on else 0.0
                    lv[f"source_{ov.role}"] = "test"
                continue
            for role in ROLES:
                val = getattr(ov, role)
                if val is not None:
                    lv[role] = float(val)
                    lv[f"source_{role}"] = ov.kind
        out[box] = lv
    return out


# ── Slew limit ──────────────────────────────────────────────────────────────

class Slew:
    """Per-strip rate limit on scheduled output, so a clock step, a mid-day
    config edit or a restart after a power cut fades instead of flashing.
    Strips driven by an override/test/blackout jump (they're deliberate);
    the schedule then fades on from wherever they were left."""

    def __init__(self, full_scale_s: float, initial: dict[str, float] | None = None):
        self.full_scale_s = float(full_scale_s)
        self.current: dict[str, float] = dict(initial or {})

    def step(self, key: str, target: float, dt: float, bypass: bool = False) -> float:
        cur = self.current.get(key)
        if bypass or cur is None or self.full_scale_s <= 0:
            self.current[key] = target
            return target
        max_step = 100.0 * max(0.0, dt) / self.full_scale_s
        if abs(target - cur) <= max_step:
            cur = target
        else:
            cur += max_step if target > cur else -max_step
        self.current[key] = cur
        return cur

    def settled(self, key: str, target: float) -> bool:
        return abs(self.current.get(key, target) - target) < 1e-6


# ── DMX frame ───────────────────────────────────────────────────────────────

def strip_value(level_pct: float, trim_pct: float, bits: int) -> int:
    """0-255 (8-bit) or 0-65535 (16-bit) output for a level and trim."""
    frac = min(1.0, max(0.0, level_pct / 100.0)) * min(1.0, max(0.0, trim_pct / 100.0))
    full = 65535 if int(bits) == 16 else 255
    return int(round(frac * full))


def build_frame(levels: dict[str, dict], cfg: dict) -> bytes:
    """512 channel bytes (index 0 = DMX channel 1) for per-box levels."""
    bits = int(cfg.get("channel_bits", 8))
    frame = bytearray(DMX_CHANNELS)
    for row in cfg.get("compartments") or []:
        box = row["box"]
        lv = levels.get(box)
        if lv is None:
            continue
        trim = float(row.get("trim", 100))
        for role in ROLES:
            addr = row.get(role)
            if not isinstance(addr, int) or isinstance(addr, bool):
                continue
            value = strip_value(lv[role], trim, bits)
            if bits == 16:
                frame[addr - 1] = value >> 8
                frame[addr] = value & 0xFF
            else:
                frame[addr - 1] = value
    return bytes(frame)
