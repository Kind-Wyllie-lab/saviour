#!/usr/bin/env python3
"""
SAVIOUR System - DMX Lighting Module

Runs the habitat's light/dark cycle: compartment LED strips (one visible and
one infrared strip per box) on DMX LED decoders, driven through an Enttec DMX
USB Pro. Plan: plans/dmx-lighting-module.md.

  * Runs on its own, all the time: the schedule lives in this module's
    active config and runs on its own clock, so the lights keep cycling
    with the controller down and whatever is or isn't recording.
  * One output thread (``dmx.output``, supervised, 10 Hz) computes each
    strip's level from (time, config, overrides) -- schedule.py, pure --
    rate-limits scheduled changes (Slew) and sends a DMX frame when a byte
    changes, plus a keepalive every couple of seconds.
  * Waits for a trusted clock (PTP locked or NTP synchronised) before
    driving anything: a Pi 5 with no RTC battery boots at a stale time, and
    a wrong clock would put day lighting on at night. Meanwhile the
    decoders do their own DMX-loss behaviour.
  * Joins sessions only to log: a per-segment lighting CSV (one row per
    second while levels move, one per event otherwise). Output never
    changes because a session started; a missing widget never blocks one
    (hardware_fault, not a failing readiness check).
  * Overrides (husbandry checks, cleaning), blackouts and strip tests always
    expire; they survive a restart (state.json) and are logged.

Author: Andrew SG
Created: 11/11/2026
"""

import copy
import csv
import glob
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

# SAVIOUR + local-variant imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.append(os.path.dirname(__file__))
from enttec import MOCK_PORT, EnttecPro, MockWidget, find_port
from schedule import (
    OVERRIDE_HARD_MAX_MIN,
    ROLES,
    Override,
    Slew,
    build_frame,
    compartment_levels,
    schedule_at,
    validate,
)

from modules.module import Module, command
from src.shared.supervised import supervise

DEFAULT_STATE_DIR = "/var/lib/saviour/lighting"
TEST_MAX_S = 120
# Offsets under this on both ptp4l and phc2sys over the last few seconds
# count as "PTP locked" for trusting the wall clock.
CLOCK_TRUST_OFFSET_NS = 1_000_000
CLOCK_CHECK_INTERVAL_S = 5.0
PERSIST_INTERVAL_S = 5.0
OPEN_BACKOFF_MAX_S = 30.0


def _deep_merge(base: dict, update: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


class DmxLightingModule(Module):
    def __init__(self, module_type: str = "dmx_lighting"):
        # Set before the base __init__ in case anything there reads it.
        self._init_state()
        super().__init__(module_type)
        self.config.load_module_config("dmx_lighting_config.json")
        self.description = ("Habitat lighting: day/night cycle on DMX LED "
                            "decoders through an Enttec DMX USB Pro")
        self._apply_config(initial=True)

    def _init_state(self) -> None:
        """State the output thread and commands share (separate from
        __init__ so tests can build the module without the Module stack)."""
        self._lock = threading.RLock()
        self._csv_lock = threading.Lock()
        self._overrides: list[Override] = []
        self._cfg: dict | None = None
        self._config_error: str | None = None
        self._slew = Slew(60)
        self._sched = None
        self._levels: dict[str, dict] = {}
        self._mode = "starting"
        self._widget: EnttecPro | None = None
        self._mock_widget: MockWidget | None = None
        self._widget_port: str | None = None
        self._last_frame: bytes | None = None
        self._next_open_at = 0.0
        self._open_backoff = 1.0
        self._open_error: str | None = None
        self._clock_source: str | None = None
        self._clock_trusted_at: float | None = None
        self._clock_wait_started = time.monotonic()
        self._last_clock_check = -1e9
        self._last_persist = 0.0
        self._persisted_levels: dict[str, float] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._session_csv = None
        self._session_writer = None
        self._session_columns: list[str] = []
        self._session_filename: str | None = None
        self._last_session_row = 0.0
        self._event_day: str | None = None
        self.is_recording = False
        self.hardware_fault: str | None = "DMX widget not yet connected"

    # ══════════════════════════════════════════════════════════════════════
    # Config
    # ══════════════════════════════════════════════════════════════════════

    def _section(self) -> dict:
        return copy.deepcopy(self.config.get("dmx_lighting", {}) or {})

    def _mock_enabled(self) -> bool:
        return bool((self._cfg or self._section()).get("mock", {}).get("enabled", False))

    def _apply_config(self, initial: bool = False) -> bool:
        """Validate the active dmx_lighting section and make it the one the
        output thread uses. An invalid one (e.g. a hand-edited file) is not
        applied: the last good config keeps running."""
        cfg = self._section()
        errors = validate(cfg)
        with self._lock:
            if errors:
                self._config_error = "; ".join(errors)
                self.logger.error(f"DMX lighting config not applied: {self._config_error}")
                return False
            previous = self._cfg
            self._cfg = cfg
            self._config_error = None
            self._slew.full_scale_s = float(cfg.get("slew_full_scale_s", 60))
            port_changed = previous is not None and (
                previous.get("serial_port") != cfg.get("serial_port")
                or previous.get("mock") != cfg.get("mock"))
        if port_changed:
            self._close_widget("serial port / mock setting changed")
            self._next_open_at = 0.0
        if not initial:
            self._event("config_change", self._config_summary(cfg))
        return True

    def _config_summary(self, cfg: dict) -> str:
        s = cfg.get("schedule", {})
        ir = cfg.get("ir", {})
        return (f"on {s.get('lights_on')} off {s.get('lights_off')}, ramps "
                f"{s.get('sunrise_minutes')}/{s.get('sunset_minutes')} min "
                f"{s.get('curve')}, day {s.get('day_level')}% night "
                f"{s.get('night_level')}%, IR {ir.get('mode')} {ir.get('level')}%, "
                f"{len(cfg.get('compartments') or [])} compartments, "
                f"{cfg.get('channel_bits')}-bit, tz {cfg.get('timezone') or 'system'}")

    @command()
    def set_config(self, config: dict, persist: bool = True):
        """Reject an invalid lighting config before anything is stored, so
        the controller shows the save as failed and the old one keeps
        running."""
        if isinstance(config, dict) and isinstance(config.get("dmx_lighting"), dict):
            candidate = _deep_merge(self._section(), config["dmx_lighting"])
            errors = validate(candidate)
            if errors:
                self.logger.warning(f"Rejected lighting config: {errors}")
                return {"result": "invalid lighting config: " + "; ".join(errors[:4]),
                        "errors": errors}
        return super().set_config(config, persist)

    def configure_module_special(self, updated_keys: list[str] | None):
        if not updated_keys or any(k.startswith("dmx_lighting") for k in updated_keys):
            self._apply_config()

    # ══════════════════════════════════════════════════════════════════════
    # Persistent state (overrides + last output) and event log
    # ══════════════════════════════════════════════════════════════════════

    def _state_dir(self) -> str:
        base = self.config.get("dmx_lighting._state_dir", DEFAULT_STATE_DIR) or DEFAULT_STATE_DIR
        return base + "_mock" if self._mock_enabled() else base

    def _state_path(self) -> str:
        return os.path.join(self._state_dir(), "state.json")

    def _load_state(self) -> None:
        try:
            with open(self._state_path()) as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:  # noqa: BLE001 -- a corrupt file must not stop the lights
            self.logger.warning(f"DMX: ignoring unreadable {self._state_path()}: {e}")
            return
        now = time.time()
        overrides = []
        for d in data.get("overrides", []):
            try:
                ov = Override.from_dict(d)
            except TypeError:
                continue
            if ov.until > now:
                overrides.append(ov)
        levels = {k: float(v) for k, v in (data.get("levels") or {}).items()
                  if isinstance(v, (int, float))}
        with self._lock:
            self._overrides = overrides
            # Start the slew from where the lights were left (the widget held
            # that frame while we were down), so the resume is a fade.
            self._slew.current.update(levels)
            self._persisted_levels = dict(levels)
        if overrides:
            self.logger.info(f"DMX: resumed {len(overrides)} override(s) from state.json")

    def _save_state(self) -> None:
        with self._lock:
            data = {
                "saved_at": time.time(),
                "overrides": [ov.to_dict() for ov in self._overrides],
                "levels": {k: round(v, 4) for k, v in self._slew.current.items()},
            }
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
            self._persisted_levels = dict(data["levels"])
        except Exception as e:  # noqa: BLE001 -- disk trouble must not stop the lights
            self.logger.warning(f"DMX: could not save {path}: {e}")

    def _events_path(self, now: float) -> str:
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        return os.path.join(self._state_dir(), f"events_{day}.csv")

    def _prune_events(self, now: float) -> None:
        keep_days = int(self.config.get("dmx_lighting._event_log_days", 60))
        cutoff = now - keep_days * 86400
        for path in glob.glob(os.path.join(self._state_dir(), "events_*.csv")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass

    def _event(self, kind: str, detail: str = "") -> None:
        """Log a lighting event: module log, the rolling local event log,
        the session CSV if one is open, and a pushed state update."""
        now = time.time()
        self.logger.info(f"Lighting event: {kind}{' - ' + detail if detail else ''}")
        path = self._events_path(now)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["timestamp_ns", "timestamp_utc", "mode", "event", "detail"])
                w.writerow([int(now * 1e9), _utc(now), self._mode, kind, detail])
            day = os.path.basename(path)
            if day != self._event_day:
                self._event_day = day
                self._prune_events(now)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"DMX: event log write failed: {e}")
        self._session_row(now, event=kind, detail=detail)
        self._push_state()

    # ══════════════════════════════════════════════════════════════════════
    # Clock trust
    # ══════════════════════════════════════════════════════════════════════

    def _clock_sync_source(self) -> str | None:
        try:
            rng = self.facade.get_ptp_recent_offset_range(10)
            vals = [rng.get(k) for k in ("ptp4l_offset_ns_min", "ptp4l_offset_ns_max",
                                         "phc2sys_offset_ns_min", "phc2sys_offset_ns_max")]
            if all(v is not None for v in vals) and max(abs(v) for v in vals) <= CLOCK_TRUST_OFFSET_NS:
                return "PTP locked"
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"DMX: PTP status unavailable: {e}")
        try:
            out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                                 capture_output=True, text=True, timeout=3)
            if out.stdout.strip() == "yes":
                return "NTP synchronised"
        except Exception:  # noqa: BLE001 -- no timedatectl = not synced
            pass
        return None

    def _clock_ok(self, cfg: dict) -> bool:
        """True once the wall clock is trustworthy this run (latched: losing
        the controller later doesn't make a disciplined clock wrong)."""
        if self._clock_trusted_at is not None:
            return True
        if not cfg.get("require_synced_clock", True):
            self._clock_source = "not required (require_synced_clock off)"
            self._clock_trusted_at = time.time()
            return True
        mono = time.monotonic()
        if mono - self._last_clock_check < CLOCK_CHECK_INTERVAL_S:
            return False
        self._last_clock_check = mono
        source = self._clock_sync_source()
        if source is None:
            return False
        self._clock_source = source
        self._clock_trusted_at = time.time()
        waited = mono - self._clock_wait_started
        self._event("clock_trusted", f"{source}; waited {waited:.0f} s")
        return True

    # ══════════════════════════════════════════════════════════════════════
    # Widget
    # ══════════════════════════════════════════════════════════════════════

    def _ensure_widget(self, cfg: dict) -> EnttecPro | None:
        if self._widget is not None:
            return self._widget
        mono = time.monotonic()
        if mono < self._next_open_at:
            return None
        mock = bool(cfg.get("mock", {}).get("enabled", False))
        port = MOCK_PORT if mock else find_port(cfg.get("serial_port") or "")
        if port is None:
            self._widget_failed("No Enttec DMX USB Pro found (no /dev/serial/by-id/"
                                "*DMX_USB_PRO* device)")
            return None
        if mock:
            # Reopen the same simulated widget (so "unplugged" sticks).
            if self._mock_widget is None:
                self._mock_widget = MockWidget()
            self._mock_widget.closed = False
            factory = lambda _p: self._mock_widget  # noqa: E731
        else:
            factory = None
        widget = EnttecPro(port, serial_factory=factory, logger=self.logger)
        try:
            widget.open()
        except Exception as e:  # noqa: BLE001
            self._widget_failed(f"DMX widget on {port} not responding: {e}")
            return None
        self._widget, self._widget_port = widget, port
        self._open_backoff = 1.0
        self._open_error = None
        self._last_frame = None        # resend straight away
        self.hardware_fault = None
        self._event("widget_connected",
                    f"{port} serial {widget.serial_number} firmware "
                    f"{widget.params.get('firmware')} refresh "
                    f"{widget.params.get('refresh_hz')} Hz"
                    + (" (MOCK)" if mock else ""))
        return widget

    def _widget_failed(self, reason: str) -> None:
        self._next_open_at = time.monotonic() + self._open_backoff
        self._open_backoff = min(OPEN_BACKOFF_MAX_S, self._open_backoff * 2)
        if reason != self._open_error:
            self._open_error = reason
            self.hardware_fault = reason
            self._event("widget_fault", reason)

    def _close_widget(self, reason: str) -> None:
        widget, self._widget = self._widget, None
        if widget is not None:
            widget.close()
            self.hardware_fault = f"DMX widget disconnected ({reason})"
            self._open_error = None
            self._event("widget_disconnected", reason)

    # ══════════════════════════════════════════════════════════════════════
    # Output loop
    # ══════════════════════════════════════════════════════════════════════

    def _run_output(self, stop_event: threading.Event) -> None:
        last = time.monotonic()
        while not stop_event.is_set():
            mono = time.monotonic()
            self._tick(time.time(), mono - last)
            last = mono
            hz = float(self.config.get("dmx_lighting._send_hz", 10) or 10)
            stop_event.wait(1.0 / max(1.0, hz))

    def _set_mode(self, mode: str, detail: str = "") -> None:
        if mode != self._mode:
            previous, self._mode = self._mode, mode
            self._event("mode", f"{previous} -> {mode}" + (f" ({detail})" if detail else ""))

    def _expire_overrides(self, now: float) -> None:
        with self._lock:
            ended = [ov for ov in self._overrides if ov.until <= now]
            if not ended:
                return
            self._overrides = [ov for ov in self._overrides if ov.until > now]
        for ov in ended:
            self._event(f"{ov.kind}_end", f"{ov.id} expired")
        self._save_state()

    def _tick(self, now: float, dt: float) -> None:
        """One output step: levels for ``now``, then send if needed."""
        with self._lock:
            cfg = self._cfg
        if cfg is None:
            self._set_mode("config_error", self._config_error or "")
            return
        # Open (and check) the widget even while the clock isn't trusted, so a
        # missing one shows as a fault straight away; just don't send yet.
        self._ensure_widget(cfg)
        if not self._clock_ok(cfg):
            self._set_mode("waiting_for_clock")
            return
        self._expire_overrides(now)

        with self._lock:
            sched = schedule_at(now, cfg)
            targets = compartment_levels(now, cfg, sched, self._overrides)
            levels = {}
            for box, lv in targets.items():
                out = dict(lv)
                for role in ROLES:
                    out[role] = self._slew.step(
                        f"{box}.{role}", lv[role], dt,
                        bypass=lv[f"source_{role}"] != "schedule")
                    out[f"target_{role}"] = lv[role]
                levels[box] = out
            self._sched, self._levels = sched, levels
            kinds = {ov.kind for ov in self._overrides}
            frame = build_frame(levels, cfg)

        mode = ("test" if "test" in kinds else "blackout" if "blackout" in kinds
                else "override" if "override" in kinds else sched.phase)
        widget = self._widget
        if widget is not None:
            keepalive = float(self.config.get("dmx_lighting._keepalive_s", 2) or 2)
            due = (frame != self._last_frame
                   or widget.last_send_monotonic is None
                   or time.monotonic() - widget.last_send_monotonic >= keepalive)
            if due:
                try:
                    widget.send(frame)
                    self._last_frame = frame
                except Exception as e:  # noqa: BLE001
                    self._close_widget(f"write failed: {e}")
        self._set_mode(mode if self._widget is not None else "fault",
                       self.hardware_fault or "")

        if self._moving(sched, levels) and now - self._last_session_row >= 1.0:
            self._session_row(now)
        if (now - self._last_persist >= PERSIST_INTERVAL_S
                and self._levels_changed_since_persist()):
            self._last_persist = now
            self._save_state()

    def _moving(self, sched, levels) -> bool:
        if sched.phase in ("sunrise", "sunset") or self._overrides:
            return True
        return any(abs(lv[r] - lv[f"target_{r}"]) > 1e-6
                   for lv in levels.values() for r in ROLES)

    def _levels_changed_since_persist(self) -> bool:
        cur = self._slew.current
        old = self._persisted_levels
        return cur.keys() != old.keys() or any(
            abs(cur[k] - old.get(k, -1)) > 0.05 for k in cur)

    # ══════════════════════════════════════════════════════════════════════
    # State reporting
    # ══════════════════════════════════════════════════════════════════════

    def lighting_status(self) -> dict:
        """Heartbeat block (ModuleHealthSnapshot.lighting) and the reply to
        get_lighting_state."""
        now = time.time()
        with self._lock:
            cfg = self._cfg or {}
            sched = self._sched.to_dict() if self._sched else None
            boxes = {box: {"visible": round(lv["visible"], 2), "ir": round(lv["ir"], 2),
                           "source_visible": lv["source_visible"],
                           "source_ir": lv["source_ir"]}
                     for box, lv in self._levels.items()}
            overrides = [ov.to_dict(now) for ov in self._overrides if ov.until > now]
        widget = self._widget
        return {
            "mode": self._mode,
            "schedule": sched,
            "boxes": boxes,
            "overrides": overrides,
            "widget": ({"connected": True, **widget.info()} if widget else
                       {"connected": False, "error": self._open_error}),
            "fault": self.hardware_fault,
            "config_error": self._config_error,
            "clock": {
                "trusted": self._clock_trusted_at is not None,
                "source": self._clock_source,
                "waiting_s": (None if self._clock_trusted_at is not None
                              else round(time.monotonic() - self._clock_wait_started)),
            },
            "timezone": cfg.get("timezone") or "system",
            "max_override_minutes": cfg.get("max_override_minutes", 240),
            "mock": self._mock_enabled(),
            "recording": self.is_recording,
            "updated_at": now,
        }

    def _push_state(self) -> None:
        comm = getattr(self, "communication", None)
        if comm is None or not getattr(comm, "controller_ip", None):
            return
        try:
            comm.send_status({"type": "lighting_state", "state": self.lighting_status()})
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"DMX: state push failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════════════════

    def _known_boxes(self) -> list[str]:
        return [r["box"] for r in (self._cfg or {}).get("compartments") or []]

    def _add_override(self, ov: Override, detail: str) -> dict:
        with self._lock:
            self._overrides.append(ov)
        self._save_state()
        self._event(f"{ov.kind}_start", detail)
        return {"result": "success", "override": ov.to_dict(time.time()),
                "state": self.lighting_status()}

    def _check_minutes(self, minutes) -> float:
        cap = min(float((self._cfg or {}).get("max_override_minutes", 240)),
                  OVERRIDE_HARD_MAX_MIN)
        minutes = float(minutes)
        if not 0 < minutes <= cap:
            raise ValueError(f"duration must be more than 0 and at most {cap:g} minutes")
        return minutes

    def _check_boxes(self, boxes) -> list[str] | None:
        if boxes in (None, "", "all", []):
            return None
        if isinstance(boxes, str):
            boxes = [b.strip() for b in boxes.split(",") if b.strip()]
        unknown = [b for b in boxes if b not in self._known_boxes()]
        if unknown:
            raise ValueError(f"unknown compartment(s): {', '.join(unknown)}")
        return list(boxes)

    @staticmethod
    def _check_level(value, name) -> float | None:
        if value in (None, ""):
            return None
        value = float(value)
        if not 0 <= value <= 100:
            raise ValueError(f"{name} must be 0-100 %")
        return value

    @command()
    def get_lighting_state(self) -> dict:
        return {"result": "success", "state": self.lighting_status()}

    @command()
    def set_override(self, boxes=None, visible=None, ir=None, minutes=30,
                     note: str = "") -> dict:
        """Set visible and/or IR strips (all, or the named boxes) to a level
        for a limited time, then return to the schedule."""
        try:
            box_list = self._check_boxes(boxes)
            vis = self._check_level(visible, "visible")
            irl = self._check_level(ir, "IR")
            if vis is None and irl is None:
                raise ValueError("give a visible and/or IR level")
            minutes = self._check_minutes(minutes)
        except (TypeError, ValueError) as e:
            return {"result": "error", "error": str(e), "state": self.lighting_status()}
        now = time.time()
        ov = Override(id=uuid.uuid4().hex[:8], kind="override", boxes=box_list,
                      visible=vis, ir=irl, started_at=now, until=now + minutes * 60,
                      note=str(note or "")[:200])
        where = "all compartments" if box_list is None else ", ".join(box_list)
        parts = [f"visible {vis:g}%" if vis is not None else "",
                 f"IR {irl:g}%" if irl is not None else ""]
        return self._add_override(
            ov, f"{ov.id}: {where} {' '.join(p for p in parts if p)} for {minutes:g} min"
                + (f" ({ov.note})" if ov.note else ""))

    @command()
    def blackout(self, minutes=30, note: str = "") -> dict:
        """Visible strips to zero everywhere for a limited time; IR unchanged."""
        try:
            minutes = self._check_minutes(minutes)
        except (TypeError, ValueError) as e:
            return {"result": "error", "error": str(e), "state": self.lighting_status()}
        now = time.time()
        ov = Override(id=uuid.uuid4().hex[:8], kind="blackout", boxes=None,
                      visible=0.0, ir=None, started_at=now, until=now + minutes * 60,
                      note=str(note or "")[:200])
        return self._add_override(ov, f"{ov.id}: visible off everywhere for {minutes:g} min")

    @command()
    def test_strip(self, box: str = "", role: str = "visible", seconds=30) -> dict:
        """Flash one strip (1 Hz) while every other strip holds its level."""
        try:
            if box not in self._known_boxes():
                raise ValueError(f"unknown compartment {box!r}")
            if role not in ROLES:
                raise ValueError("role must be 'visible' or 'ir'")
            row = next(r for r in self._cfg["compartments"] if r["box"] == box)
            if not isinstance(row.get(role), int):
                raise ValueError(f"{box} has no {role} address")
            seconds = float(seconds)
            if not 0 < seconds <= TEST_MAX_S:
                raise ValueError(f"test length must be 1-{TEST_MAX_S} s")
        except (TypeError, ValueError, StopIteration) as e:
            return {"result": "error", "error": str(e), "state": self.lighting_status()}
        now = time.time()
        with self._lock:      # one test at a time
            self._overrides = [ov for ov in self._overrides if ov.kind != "test"]
        ov = Override(id=uuid.uuid4().hex[:8], kind="test", boxes=[box], visible=None,
                      ir=None, started_at=now, until=now + seconds, role=role)
        return self._add_override(ov, f"{ov.id}: flash {box} {role} for {seconds:g} s")

    @command()
    def clear_override(self, override_id: str = "") -> dict:
        """End one override/blackout/test early (or all, with no id)."""
        with self._lock:
            ended = [ov for ov in self._overrides
                     if not override_id or ov.id == override_id]
            self._overrides = [ov for ov in self._overrides if ov not in ended]
        if override_id and not ended:
            return {"result": "error", "error": f"no active override {override_id}",
                    "state": self.lighting_status()}
        self._save_state()
        for ov in ended:
            self._event(f"{ov.kind}_end", f"{ov.id} cleared")
        return {"result": "success", "cleared": [ov.id for ov in ended],
                "state": self.lighting_status()}

    # ══════════════════════════════════════════════════════════════════════
    # Recording: log only
    # ══════════════════════════════════════════════════════════════════════

    def _new_session_filename(self) -> str:
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        return (f"{self.facade.get_filename_prefix()}_lighting"
                f"_({self.facade.get_segment_id()}_{strtime}).csv")

    def _open_session_csv(self) -> None:
        filename = self._new_session_filename()
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        boxes = self._known_boxes()
        columns = ["timestamp_ns", "timestamp_utc", "mode", "phase", "event", "detail"]
        for box in boxes:
            columns += [f"{box}_visible", f"{box}_ir"]
        with self._csv_lock:
            self._session_csv = open(filename, "w", newline="", buffering=1)
            self._session_writer = csv.writer(self._session_csv)
            self._session_writer.writerow(columns)
            self._session_columns = boxes
            self._session_filename = filename
        self.facade.add_session_file(filename)

    def _close_session_csv(self) -> str | None:
        with self._csv_lock:
            f, self._session_csv = self._session_csv, None
            self._session_writer = None
            filename, self._session_filename = self._session_filename, None
        if f is not None:
            try:
                f.close()
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"DMX: error closing lighting CSV: {e}")
        return filename

    def _session_row(self, now: float, event: str = "", detail: str = "") -> None:
        with self._csv_lock:
            if self._session_writer is None:
                return
            with self._lock:
                phase = self._sched.phase if self._sched else ""
                row = [int(now * 1e9), _utc(now), self._mode, phase, event, detail]
                for box in self._session_columns:
                    lv = self._levels.get(box)
                    row += ([f"{lv['visible']:.2f}", f"{lv['ir']:.2f}"] if lv else ["", ""])
            try:
                self._session_writer.writerow(row)
                self._last_session_row = now
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"DMX: lighting CSV write failed: {e}")

    def _start_new_recording(self) -> bool:
        try:
            self._open_session_csv()
        except Exception as e:  # noqa: BLE001 -- the log must never block a session
            self.logger.error(f"DMX: could not open lighting CSV: {e}")
        self.is_recording = True
        self._session_row(time.time(), "session_start",
                          self._config_summary(self._cfg) if self._cfg else "")
        return True

    def _start_next_recording_segment(self) -> bool:
        self._session_row(time.time(), "segment_end")
        filename = self._close_session_csv()
        if filename:
            self.facade.stage_file_for_export(filename)
        try:
            self._open_session_csv()
            self._session_row(time.time(), "segment_start")
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"DMX: could not open lighting CSV: {e}")
        return True

    def _stop_recording(self) -> bool:
        self._session_row(time.time(), "session_stop")
        self.is_recording = False
        filename = self._close_session_csv()
        if filename:
            self.facade.stage_file_for_export(filename)
        return True

    # ══════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def start(self) -> bool:
        if not super().start():
            return False
        self._load_state()
        self._stop_event = threading.Event()
        self._thread = supervise("dmx.output", self._run_output,
                                 stop_event=self._stop_event, logger=self.logger)
        return True

    def stop(self) -> bool:
        self._shutdown_output()
        return super().stop()

    def cleanup(self):
        try:
            if self.is_recording:
                self._stop_recording()
            self._shutdown_output()
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"DMX cleanup error: {e}")

    def _shutdown_output(self) -> None:
        # The widget keeps sending the last frame after we let go, so the
        # lights hold their level; systemd brings us back.
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        self._save_state()
        widget, self._widget = self._widget, None
        if widget is not None:
            widget.close()


def main():
    module = DmxLightingModule()
    module.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        module.stop()


if __name__ == "__main__":
    main()
