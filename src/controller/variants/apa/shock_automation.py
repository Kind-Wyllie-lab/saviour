"""
APA shock grid: armed state, shock mode and automated shocking, run on the
controller.

The apa_arduino module stays a plain actuator: activate_shock starts its
pulse loop (duration on / intershock_latency off) for as long as the lease
(SHOCK_LEASE_S, 1 s) keeps being refreshed, and deactivate_shock stops it.
Everything about *whether* and *when* to shock lives here:

- Shock mode (`apa.shock_mode`, a setting that persists): manual | auto |
  dry_run. It says how an armed grid is driven, and dry_run means "record
  without arming".
- Armed / disarmed (runtime only; a controller restart comes back disarmed):
  the grid arms itself when a recording session starts, unless the mode is
  dry_run, and disarms when the last session ends. Stop all disarms (and
  stops any shock); the operator can re-arm or disarm by hand.
- Shocks only while armed: in manual mode from the dashboard's spacebar /
  hold button (any other source is refused), in auto mode from this class.
- auto: the APA camera reports zone_entered / zone_exited the frame the
  tracked position crosses the zone edge, and zone_state at ~5 Hz while
  tracking. Once the rat has been in the zone for entry_latency_s, the shock
  is held on (lease refreshed) until it leaves. Zone reports older than
  zone_state_timeout_s count as "not in zone", so a camera that crashes or
  stops tracking ends the shock within that time (the module's lease backs
  that up). Automated shocks need a session recording; pulses are capped per
  minute (all sources, from the module's attempted_shocks count) on top of
  the module's per-session cap.

Tracking itself is independent of all this: the camera saves it to its
timestamps CSV whenever tracking is on. Decisions are logged to the
controller journal and to <share>/<session>/<session>_shock_decisions.csv
for each active session.
"""

import csv
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

MODES = ("manual", "auto", "dry_run")
DEFAULT_MODE = "manual"

DEFAULTS = {
    "entry_latency_s": 0.5,
    "zone_state_timeout_s": 0.5,
    "max_pulses_per_min": 20,
}

_TICK_S = 0.05
_REFRESH_S = 0.25       # well inside the module's 1 s lease
_EMIT_ARMED_S = 0.25
_EMIT_IDLE_S = 1.0
_LOG_FIELDS = ["time_utc", "mode", "armed", "event", "reason", "in_zone_s",
               "det_cx", "det_cy", "det_conf", "camera", "by"]


class ShockAutomation:
    def __init__(
        self, *,
        send_command: Callable[[str, str, dict], None],
        get_modules: Callable[[], dict],
        get_active_sessions: Callable[[], list[str]],
        get_settings: Callable[[], dict],
        get_mode: Callable[[], str],
        save_mode: Callable[[str], None],
        get_share_path: Callable[[], str],
        emit: Callable[[dict], None],
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._send_command = send_command
        self._get_modules = get_modules
        self._get_active_sessions = get_active_sessions
        self._get_settings = get_settings
        self._get_mode = get_mode
        self._save_mode = save_mode
        self._get_share_path = get_share_path
        self._emit = emit
        self.logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._lock = threading.RLock()

        self.armed = False
        # Latest zone report (any APA camera; the rig has one).
        self._zone_camera: str | None = None
        self._in_zone = False
        self._entered_at: float | None = None
        self._last_zone_at: float | None = None
        self._pos: dict = {}
        # Auto shock currently held on.
        self.shocking = False
        self._last_refresh = 0.0
        self._capped = False
        # None until the first tick: a controller (re)start mid-session must
        # not arm the grid on its own.
        self._had_session: bool | None = None
        self._sessions: list[str] = []
        # (time, attempted_shocks) from arduino_state, for the per-minute cap.
        self._attempted: deque[tuple[float, int]] = deque()
        self.last_event: dict | None = None
        self._last_emit = 0.0
        self._last_refused = float("-inf")

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="apa-shock-automation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self.armed:
                self._deactivate_all()
            self.armed = self.shocking = False

    def _run(self) -> None:
        while not self._stop.wait(_TICK_S):
            try:
                self.tick()
            except Exception as e:  # keep deciding; a crash here would strand nothing
                self.logger.error(f"Shock automation tick failed: {e}")

    # ── settings / operator actions ────────────────────────────────────────

    @property
    def mode(self) -> str:
        mode = self._get_mode()
        return mode if mode in MODES else DEFAULT_MODE

    def settings(self) -> dict:
        got = self._get_settings() or {}
        return {k: got.get(k, v) for k, v in DEFAULTS.items()}

    def set_mode(self, mode: str, by: str = "operator") -> bool:
        if mode not in MODES:
            return False
        with self._lock:
            old = self.mode
            if mode == old:
                return True
            self._save_mode(mode)
            self._log("mode", f"{old} -> {mode}", by=by)
            if old == "manual" and self.armed:
                self._deactivate_all()  # ends a manual hold
            self.tick()  # leaving auto ends its shock; dry_run disarms
            self._emit_state(force=True)
        return True

    def arm(self, by: str = "operator", reason: str = "") -> bool:
        with self._lock:
            if self.mode == "dry_run":
                return False
            if not self.armed:
                self.armed = True
                self._capped = False
                self._log("armed", reason, by=by)
            self._emit_state(force=True)
        return True

    def disarm(self, by: str = "operator", reason: str = "") -> None:
        with self._lock:
            was = self.armed
            self.armed = self.shocking = False
            # Also ends a manual hold the frontend may still be refreshing
            # (its refreshes are refused from here on).
            self._deactivate_all()
            if was:
                self._log("disarmed", reason, by=by)
            self._emit_state(force=True)

    def stop_all(self, by: str = "operator") -> None:
        with self._lock:
            self.armed = self.shocking = False
            self._deactivate_all()
            self._log("stop_all", "", by=by)
            self._emit_state(force=True)

    def manual_allowed(self) -> bool:
        return self.armed and self.mode == "manual"

    def refuse_manual(self, by: str = "operator") -> None:
        with self._lock:
            # A held button re-sends every 250 ms: log the first, not each.
            now = self._clock()
            if now - self._last_refused >= 1.0:
                why = "grid disarmed" if not self.armed else f"mode is {self.mode}"
                self._log("manual_refused", why, by=by)
                self._emit_state(force=True)
            self._last_refused = now

    # ── inputs from modules ────────────────────────────────────────────────

    def on_zone_status(self, camera_id: str, status: dict) -> None:
        now = self._clock()
        kind = status.get("type")
        with self._lock:
            if kind == "zone_entered":
                in_zone = True
            elif kind == "zone_exited":
                in_zone = False
            elif kind == "zone_state":
                in_zone = bool(status.get("in_zone"))
                self._pos = {k: status.get(k) for k in ("det_cx", "det_cy", "det_conf")}
            else:
                return
            if in_zone and not self._in_zone:
                self._entered_at = now
            elif not in_zone:
                self._entered_at = None
            self._in_zone = in_zone
            self._zone_camera = camera_id
            self._last_zone_at = now
        if kind != "zone_state":
            self.tick()  # react to an edge now, not on the next tick

    def on_arduino_state(self, state: dict) -> None:
        n = state.get("attempted_shocks")
        if not isinstance(n, int):
            return
        now = self._clock()
        with self._lock:
            self._attempted.append((now, n))
            while len(self._attempted) > 2 and self._attempted[1][0] <= now - 60:
                self._attempted.popleft()

    # ── decision ───────────────────────────────────────────────────────────

    def pulses_last_min(self) -> int:
        if len(self._attempted) < 2:
            return 0
        return max(0, self._attempted[-1][1] - self._attempted[0][1])

    def tick(self) -> None:
        with self._lock:
            now = self._clock()
            s = self.settings()
            mode = self.mode
            sessions = list(self._get_active_sessions())
            session = bool(sessions)
            if session:
                self._sessions = sessions

            # Arm at recording start, disarm at the end (logged to the
            # session that just ended, so _sessions is cleared after).
            if self._had_session is not None and session != self._had_session:
                if session and mode != "dry_run":
                    self.arm(by="controller", reason="recording started")
                elif not session and self.armed:
                    self.disarm(by="controller", reason="recording ended")
            self._had_session = session
            self._sessions = sessions
            if self.armed and mode == "dry_run":
                self.disarm(by="controller", reason="mode set to dry run")

            fresh = (self._last_zone_at is not None
                     and now - self._last_zone_at <= s["zone_state_timeout_s"])
            in_zone_s = (now - self._entered_at
                         if self._in_zone and self._entered_at is not None else 0.0)
            cap = s["max_pulses_per_min"]
            capped = bool(cap) and self.pulses_last_min() >= cap

            reason = None
            if not self.armed:
                reason = "disarmed"
            elif mode != "auto":
                reason = "mode"
            elif not session:
                reason = "no session recording"
            elif not fresh:
                reason = "no zone report"
            elif not self._in_zone:
                reason = "left zone"
            elif in_zone_s < s["entry_latency_s"]:
                reason = "entry latency"
            elif capped:
                reason = "per-minute cap"
            want = reason is None

            if capped and not self._capped and self.armed and mode == "auto":
                self._log("capped", f"{self.pulses_last_min()} pulses in the last minute",
                          in_zone_s=in_zone_s)
            self._capped = capped

            if want and not self.shocking:
                self.shocking = True
                self._last_refresh = now
                self._activate_all()
                self._log("shock_start", "", in_zone_s=in_zone_s)
                self._emit_state(force=True)
            elif want and now - self._last_refresh >= _REFRESH_S:
                self._last_refresh = now
                self._activate_all()
            elif not want and self.shocking:
                self.shocking = False
                self._deactivate_all()
                self._log("shock_stop", reason, in_zone_s=in_zone_s)
                self._emit_state(force=True)
            self._emit_state()

    # ── outputs ────────────────────────────────────────────────────────────

    def _arduinos(self) -> list[str]:
        return [mid for mid, m in (self._get_modules() or {}).items()
                if m.get("type") == "apa_arduino" and m.get("online", True)]

    def _activate_all(self) -> None:
        for mid in self._arduinos():
            self._send_command(mid, "activate_shock", {})

    def _deactivate_all(self) -> None:
        for mid in self._arduinos():
            self._send_command(mid, "deactivate_shock", {})

    def state(self) -> dict:
        now = self._clock()
        s = self.settings()
        fresh = (self._last_zone_at is not None
                 and now - self._last_zone_at <= s["zone_state_timeout_s"])
        return {
            "mode": self.mode,
            "armed": self.armed,
            "shocking": self.shocking,
            "in_zone": self._in_zone and fresh,
            "zone_report_fresh": fresh,
            "in_zone_s": round(now - self._entered_at, 2)
                         if self._in_zone and fresh and self._entered_at is not None else 0.0,
            "camera": self._zone_camera,
            "session_recording": bool(self._sessions),
            "pulses_last_min": self.pulses_last_min(),
            "capped": self._capped,
            "settings": s,
            "last_event": self.last_event,
        }

    def _emit_state(self, force: bool = False) -> None:
        now = self._clock()
        period = _EMIT_ARMED_S if self.armed else _EMIT_IDLE_S
        if not force and now - self._last_emit < period:
            return
        self._last_emit = now
        try:
            self._emit(self.state())
        except Exception as e:
            self.logger.debug(f"apa_shock_state emit failed: {e}")

    def _log(self, event: str, reason: str, *, in_zone_s: float | None = None,
             by: str = "auto") -> None:
        row = {
            "time_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "mode": self.mode,
            "armed": int(self.armed),
            "event": event,
            "reason": reason,
            "in_zone_s": "" if in_zone_s is None else round(in_zone_s, 3),
            "det_cx": self._pos.get("det_cx", ""),
            "det_cy": self._pos.get("det_cy", ""),
            "det_conf": self._pos.get("det_conf", ""),
            "camera": self._zone_camera or "",
            "by": by,
        }
        self.last_event = {k: row[k] for k in ("time_utc", "event", "reason", "by")}
        self.logger.info(f"Shock {event} ({self.mode}, "
                         f"{'armed' if self.armed else 'disarmed'})"
                         f"{': ' + reason if reason else ''}")
        for session in self._sessions or self._get_active_sessions():
            self._append_csv(session, row)

    def _append_csv(self, session: str, row: dict) -> None:
        try:
            d = os.path.join(self._get_share_path(), session)
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{session}_shock_decisions.csv")
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=_LOG_FIELDS)
                if new:
                    w.writeheader()
                w.writerow({k: ("" if v is None else v) for k, v in row.items()})
        except OSError as e:
            self.logger.warning(f"Could not write shock decision log for {session}: {e}")
