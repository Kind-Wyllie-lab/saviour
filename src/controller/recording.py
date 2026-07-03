"""
Recording manager for the SAVIOUR Controller.

Each module can only be associated with one recording session at a time.

Author: Andrew SG
Created: 26/01/2026
"""

import os
import json
import logging
import shutil
from datetime import datetime, date
import time
from typing import Optional, Dict
from dataclasses import dataclass, field, asdict
import threading
from enum import StrEnum


SESSIONS_FILE = "/var/lib/saviour/controller/sessions.json"
_SHARE_ROOT_DEFAULT = "/home/pi/controller_share"

_MONITOR_INTERVAL_SECS = 5

# How far into the future modules are told to start recording.
# PTP-synchronised clocks mean all modules hit this timestamp together.
LEAD_SECS = 3

# How long after recording_start_at to suppress fault detection.
# Modules take a few seconds to spin up after their scheduled start time.
_STARTUP_GRACE_SECS = 15


# ---------------------------------------------------------------------------
# State enums
# ---------------------------------------------------------------------------

class SessionState(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE    = "active"
    STOPPED   = "stopped"
    ERROR     = "error"


# ---------------------------------------------------------------------------
# RecordingSession dataclass
# ---------------------------------------------------------------------------

@dataclass
class RecordingSession:
    session_name:              str
    target:                    str
    state:                     str  = SessionState.ACTIVE
    modules:                   list = field(default_factory=list)
    start_time:                Optional[str] = None
    end_time:                  Optional[str] = None
    error_message:             str  = ""
    scheduled:                 bool = False
    scheduled_start_time:      Optional[str] = None   # HH:MM
    scheduled_end_time:        Optional[str] = None   # HH:MM
    # Prevents a scheduled session from starting more than once on the same
    # calendar day (YYYY-MM-DD).
    scheduled_last_start_date: Optional[str] = None
    # Per-module stop acknowledgement: "recording" | "stopping" | "stopped" | "unknown"
    module_stop_states:        dict = field(default_factory=dict)
    # Per-module export tracking:  "idle" | "pending" | "complete" | "failed"
    module_export_states:      dict = field(default_factory=dict)
    # Cumulative count of completed exports across all segments
    total_exports_complete:    int  = 0
    total_exports_failed:      int  = 0
    # UTC epoch at which modules are scheduled to begin recording (time.time() + LEAD_SECS).
    # None for immediate starts (e.g. module_back_online).
    recording_start_at:        Optional[float] = None
    # Set by _stop_scheduled_session so _check_all_stopped returns to SCHEDULED
    # rather than STOPPED when the day's run finishes.
    scheduled_stopping:        bool = False
    # Timestamp (YYYYMMDD-HHMMSS) when this session most recently entered ERROR state.
    # Never cleared after recovery — preserves the fault record for display.
    error_time:                Optional[str] = None
    # Timed sessions: requested duration in minutes (for display purposes).
    duration_minutes:          Optional[int]   = None
    # Timed sessions: epoch timestamp at which the session should auto-stop.
    # None means no auto-stop (infinite / manual stop).
    timed_stop_at:             Optional[float] = None
    # Scheduled sessions: weekday ints (0=Mon…6=Sun) on which to run.
    # Empty list means every day.
    scheduled_days:            list = field(default_factory=list)
    researcher:                Optional[str] = None
    # Set while PTP offset exceeds threshold on any recording module; cleared on recovery.
    ptp_warning:               Optional[str] = None


# ---------------------------------------------------------------------------
# Recording manager
# ---------------------------------------------------------------------------

class Recording:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions: Dict[str, RecordingSession] = {}
        self._lock = threading.Lock()
        self._health_probe_times: dict = {}  # module_id → timestamp of last get_health probe
        self._not_recording_strikes: dict = {}  # (session_name, module_id) → consecutive miss count
        self._ptp_degraded: Dict[str, set] = {}  # session_name → set of currently-degraded module IDs
        self._last_export_success: Dict[str, float] = {}   # module_id → epoch of last successful export
        self._export_failure_streak: Dict[str, int] = {}   # module_id → consecutive export failures
        self._daily_run_export_start: Dict[str, tuple] = {} # session_name → (complete, failed) at day-start
        self._daily_summary_sent: set = set()               # "session:date" already summarized
        self._gap_check_date: Optional[str] = None          # last date gap-check ran
        self._monitor_cycle: int = 0                        # loop counter for periodic tasks
        self._readiness_checks: Dict[str, float] = {}       # session_name → epoch when validate_readiness was dispatched

        self._load_sessions()

        self._monitor_thread = threading.Thread(
            target=self._monitor_sessions,
            daemon=True,
            name="session-monitor",
        )
        self._monitor_thread.start()


    # -----------------------------------------------------------------------
    # Public session API
    # -----------------------------------------------------------------------

    def _busy_modules(self) -> set:
        """Return the set of module IDs that are already in an active session."""
        return {
            m
            for s in self.sessions.values()
            if s.state == SessionState.ACTIVE
            for m in s.modules
        }

    def _check_ptp_sync(self, modules: list) -> dict:
        """Gate-check PTP synchronisation for all target modules before starting a session.

        Uses the most recent heartbeat data — no blocking network request.  Data
        older than three heartbeat intervals (90 s) is treated as a failure because
        a stale offset is not a synchronisation guarantee.

        Offline modules are skipped: they will not participate in the recording and
        the session fault monitor will handle their absence independently.

        Returns {"ok": True} on pass, or:
          {"ok": False, "error": str, "failures": [{"module_id": str, "reason": str, ...}]}
        """
        config = self.facade.get_config()
        threshold_us: float = config.get("recording", {}).get("ptp_threshold_us", 50.0)
        max_age_secs: float = 90.0
        now = time.time()
        failures = []
        synced = []  # {"module_id": str, "offset_us": float}

        for module_id in modules:
            health = self.facade.get_module_health(module_id)
            if not health:
                failures.append({"module_id": module_id, "reason": "no health data received yet"})
                continue

            if health.get("status") == "offline":
                continue  # offline modules are handled separately by the session monitor

            age = now - health.get("last_heartbeat", 0)
            if age > max_age_secs:
                failures.append({
                    "module_id": module_id,
                    "reason": f"health data is {age:.0f}s old — module may have disconnected",
                })
                continue

            offset_ns = health.get("ptp4l_offset_ns")
            if offset_ns is None:
                failures.append({
                    "module_id": module_id,
                    "reason": "PTP offset not yet reported — ptp4l may still be settling",
                })
                continue

            offset_us = offset_ns / 1000
            if abs(offset_us) > threshold_us:
                failures.append({
                    "module_id": module_id,
                    "offset_us": round(offset_us, 1),
                    "reason": (
                        f"offset {offset_us:.1f}µs exceeds "
                        f"{threshold_us:.0f}µs threshold"
                    ),
                })
            else:
                synced.append({"module_id": module_id, "offset_us": round(offset_us, 1)})

        if not failures:
            max_offset = max((abs(m["offset_us"]) for m in synced), default=0.0)
            return {
                "ok": True,
                "synced": synced,
                "max_offset_us": round(max_offset, 1),
                "threshold_us": threshold_us,
            }

        detail = "; ".join(f"{f['module_id']}: {f['reason']}" for f in failures)
        return {
            "ok": False,
            "failures": failures,
            "error": f"PTP not synchronised on {len(failures)} module(s) — {detail}",
        }


    def _check_share_writable(self) -> Optional[str]:
        """Return an error string if the controller share is not writable, else None."""
        share = self._get_share_root()
        probe = os.path.join(share, ".saviour_write_probe")
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return None
        except Exception as e:
            return f"Controller share not writable ({share}): {e}"

    def _check_nas_space(self) -> dict:
        """Return NAS free-space stats.

        Returns {"ok": True, "free_pct": float, "free_gb": float} on success, or
        {"ok": False, "error": str} if the share is unreachable or the call fails.
        """
        share = self._get_share_root()
        try:
            usage = shutil.disk_usage(share)
            free_pct = usage.free / usage.total * 100
            free_gb  = usage.free / 1_073_741_824  # bytes → GiB
            return {"ok": True, "free_pct": round(free_pct, 1), "free_gb": round(free_gb, 1)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


    def _send_daily_summary(self, session_name: str, session: "RecordingSession") -> None:
        """Send a Teams alert summarising a scheduled session's completed daily run."""
        run_date = session.scheduled_last_start_date or date.today().isoformat()
        summary_key = f"{session_name}:{run_date}"
        if summary_key in self._daily_summary_sent:
            return
        self._daily_summary_sent.add(summary_key)

        start_snap, failed_snap = self._daily_run_export_start.get(session_name, (0, 0))
        exports_today  = session.total_exports_complete - start_snap
        failures_today = session.total_exports_failed   - failed_snap

        nas = self._check_nas_space()
        nas_str = (
            f"{nas['free_pct']:.1f}% free ({nas['free_gb']:.0f} GiB)"
            if nas.get("ok")
            else f"check failed: {nas.get('error', 'unknown')}"
        )

        lines = [
            f"Session **{session_name}** completed its {run_date} run.",
            f"",
            f"- Modules: {len(session.modules)}",
            f"- Start: {session.start_time or '—'}  |  End: {session.end_time or '—'}",
            f"- Exports this run: {exports_today} completed, {failures_today} failed",
            f"- NAS free space: {nas_str}",
        ]
        if session.ptp_warning:
            lines.append(f"- PTP warning at stop: {session.ptp_warning}")

        self.facade.send_alert(
            key=f"daily_summary_{session_name}_{run_date}",
            title=f"Daily summary — {session_name} — {run_date}",
            message="\n".join(lines),
            severity="info",
        )


    def create_session(self, session_name: str, target: str,
                       duration_minutes: Optional[int] = None,
                       researcher: Optional[str] = None,
                       raw_name: bool = False) -> dict:
        """Create a session that begins recording immediately.

        Returns a result dict so the caller can surface errors to the frontend.
        """
        if not session_name or not session_name.strip():
            self.logger.warning("create_session: empty session_name")
            return {"success": False, "error": "Session name cannot be empty"}

        share_err = self._check_share_writable()
        if share_err:
            self.logger.error(f"create_session: {share_err}")
            return {"success": False, "error": share_err}

        modules = list(self.facade.get_modules_by_target(target).keys())
        if not modules:
            self.logger.warning(f"create_session: no modules for target '{target}'")
            return {"success": False, "error": f"No online modules found for target '{target}'"}

        overlap = self._busy_modules() & set(modules)
        if overlap:
            self.logger.warning(f"create_session: modules already recording: {overlap}")
            return {"success": False, "error": f"Already recording: {', '.join(sorted(overlap))}"}

        ptp = self._check_ptp_sync(modules)
        if not ptp["ok"]:
            self.logger.warning(f"create_session blocked by PTP check: {ptp['error']}")
            return {"success": False, "error": ptp["error"]}

        session_name = self._format_session_name(session_name, target) if not raw_name else \
            "".join(c for c in session_name if c.isalnum() or c in ("-", "_"))

        start_at = time.time() + LEAD_SECS
        timed_stop_at = (start_at + duration_minutes * 60) if duration_minutes else None

        session = RecordingSession(
            session_name=session_name,
            target=target,
            state=SessionState.ACTIVE,
            modules=modules,
            start_time=datetime.now().strftime("%Y%m%d-%H%M%S"),
            module_stop_states={m: "recording" for m in modules},
            module_export_states={m: "idle" for m in modules},
            recording_start_at=start_at,
            duration_minutes=duration_minutes,
            timed_stop_at=timed_stop_at,
            researcher=researcher or None,
        )

        with self._lock:
            self.sessions[session_name] = session

        params = {"duration": 0, "session_name": session_name, "start_at": start_at}
        for module_id in modules:
            self.facade.send_command(module_id, "start_recording", params)
        self.facade.update_sessions(self.sessions)
        self._save_sessions()

        self.logger.info(
            f"Session '{session_name}' created targeting {target} ({len(modules)} modules)"
        )
        self._log_session_event(session_name, "INFO",
            f"Session started — modules: {', '.join(modules)}")
        self.facade.send_alert(
            key=f"session_started_{session_name}",
            title=f"Recording started — {session_name}",
            message=f"Session **{session_name}** started with {len(modules)} module(s): {', '.join(modules)}.",
            severity="info",
        )
        return {"success": True, "session_name": session_name}


    def create_scheduled_session(self, session_name: str, target: str,
                                  start_time: str, end_time: str,
                                  days: Optional[list] = None,
                                  researcher: Optional[str] = None,
                                  raw_name: bool = False) -> dict:
        """Create a session that records on specified days between start_time and end_time (HH:MM).

        days is a list of weekday ints (0=Mon…6=Sun). Empty / None means every day.
        """
        if not session_name or not session_name.strip():
            return {"success": False, "error": "Session name cannot be empty"}
        if not start_time or not end_time:
            return {"success": False, "error": "start_time and end_time are required (HH:MM)"}

        modules = list(self.facade.get_modules_by_target(target).keys())
        if not modules:
            # No modules online at creation time — permitted for scheduled sessions.
            # _start_scheduled_session will refresh the list from target at run time.
            self.logger.info(
                f"create_scheduled_session: no '{target}' modules online yet — "
                f"session will pick them up when it starts"
            )

        session_name = self._format_session_name(session_name, target) if not raw_name else \
            "".join(c for c in session_name if c.isalnum() or c in ("-", "_"))

        session = RecordingSession(
            session_name=session_name,
            target=target,
            state=SessionState.SCHEDULED,
            modules=modules,
            scheduled=True,
            scheduled_start_time=start_time,
            scheduled_end_time=end_time,
            scheduled_days=days or [],
            module_stop_states={m: "recording" for m in modules},
            module_export_states={m: "idle" for m in modules},
            researcher=researcher or None,
        )

        with self._lock:
            self.sessions[session_name] = session

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(
            f"Scheduled session '{session_name}' created for {target} "
            f"between {start_time}–{end_time}"
        )
        return {"success": True, "session_name": session_name}


    def delete_session(self, session_name: str, delete_files: bool = True) -> dict:
        """Remove a stopped/error session from the list and optionally delete its files.

        Active and scheduled sessions cannot be deleted; stop them first.
        """
        if session_name not in self.sessions:
            return {"error": f"Unknown session '{session_name}'"}

        session = self.sessions[session_name]
        if session.state in (SessionState.ACTIVE, SessionState.SCHEDULED):
            return {"error": f"Cannot delete a session in state '{session.state}' — stop it first"}

        if delete_files:
            share_dir = os.path.join(self._get_share_root(), session_name)
            if os.path.isdir(share_dir):
                try:
                    shutil.rmtree(share_dir)
                    self.logger.info(f"Deleted files for session '{session_name}' at {share_dir}")
                except Exception as e:
                    self.logger.error(f"Failed to delete files for '{session_name}': {e}")
                    return {"error": f"File deletion failed: {e}"}

        with self._lock:
            del self.sessions[session_name]

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Session '{session_name}' deleted (delete_files={delete_files})")
        return {"success": True}

    def clear_ended_sessions(self, delete_files: bool = False) -> dict:
        """Remove all stopped/error sessions. Files are not deleted by default."""
        ended = [
            name for name, s in list(self.sessions.items())
            if s.state not in (SessionState.ACTIVE, SessionState.SCHEDULED)
        ]
        for name in ended:
            self.delete_session(name, delete_files=delete_files)
        return {"cleared": len(ended)}

    def stop_session(self, session_name: str) -> None:
        """Stop a recording session.

        Sends stop_recording to all modules and marks each as 'stopping'.
        The session transitions to STOPPED only once all modules confirm via
        module_stopped(), so the frontend can track progress accurately.
        """
        if session_name not in self.sessions:
            self.logger.warning(f"stop_session: unknown session '{session_name}'")
            return

        session = self.sessions[session_name]

        if session.state == SessionState.STOPPED:
            self.logger.info(f"Session '{session_name}' is already stopped")
            return

        with self._lock:
            for module_id in session.modules:
                # Modules that aren't actually recording can't respond — count them done immediately
                if not self.facade.is_module_recording(module_id):
                    session.module_stop_states[module_id] = "stopped"
                else:
                    session.module_stop_states[module_id] = "stopping"

        for module_id in session.modules:
            if session.module_stop_states.get(module_id) == "stopping":
                self.facade.send_command(module_id, "stop_recording", {})

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(
            f"Stop command sent to {len(session.modules)} module(s) in '{session_name}'"
        )
        # If all modules were already offline, complete the transition immediately
        self._check_all_stopped(session_name)


    def force_start_scheduled_session(self, session_name: str) -> dict:
        """Immediately start a scheduled (or errored scheduled) session.

        Bypasses the time-of-day check so the operator can start a session on
        demand without waiting for the scheduled window.  All other pre-flight
        checks (module availability, PTP, NAS space) still run.
        """
        if session_name not in self.sessions:
            return {"success": False, "error": f"Unknown session '{session_name}'"}
        session = self.sessions[session_name]
        if not session.scheduled:
            return {"success": False, "error": "Not a scheduled session — use stop/create instead"}
        if session.state == SessionState.ACTIVE:
            return {"success": False, "error": "Session is already recording"}
        if session.state == SessionState.STOPPED:
            return {"success": False, "error": "Session is stopped — recreate it to restart"}

        # Clear any stale day-lock and pending readiness check so
        # _start_scheduled_session will proceed immediately.
        today = date.today().isoformat()
        self._readiness_checks.pop(session_name, None)
        with self._lock:
            session.scheduled_last_start_date = None
            if session.state == SessionState.ERROR:
                session.state = SessionState.SCHEDULED
                session.error_message = ""
        self._start_scheduled_session(session_name, today)

        with self._lock:
            new_state = session.state
            error_msg = session.error_message
        if new_state == SessionState.ACTIVE:
            return {"success": True}
        if new_state == SessionState.ERROR:
            return {"success": False, "error": error_msg or "Session failed to start"}
        # Still SCHEDULED — soft failure (PTP settling, no modules online, etc.)
        return {
            "success": False,
            "error": "Could not start yet — PTP may still be settling or no modules are online. Try again in a few seconds.",
        }


    def module_stopped(self, module_id: str) -> None:
        """Called when a module sends recording_stopped.

        Marks the module as confirmed-stopped and checks whether all modules
        in the session have now confirmed, transitioning the session to STOPPED.
        """
        for name, session in self.sessions.items():
            if session.module_stop_states.get(module_id) == "stopping":
                with self._lock:
                    session.module_stop_states[module_id] = "stopped"
                self.logger.info(
                    f"Module {module_id} confirmed stopped in session '{name}'"
                )
                self._check_all_stopped(name)
                return
        self.logger.debug(
            f"module_stopped: {module_id} not found in any 'stopping' session — ignoring"
        )


    def module_export_update(self, module_id: str, export_path: str, state: str) -> None:
        """Update export state for a module.

        The session is identified from the first path component of export_path,
        which is always the session_name (e.g. 'myexp-20260312/20260312/camera_d61e').
        """
        session_name = export_path.split('/')[0] if export_path else None
        if not session_name or session_name not in self.sessions:
            return

        with self._lock:
            self.sessions[session_name].module_export_states[module_id] = state
            if state == "complete":
                self.sessions[session_name].total_exports_complete += 1
                self._last_export_success[module_id] = time.time()
                self._export_failure_streak[module_id] = 0
            elif state == "failed":
                self.sessions[session_name].total_exports_failed += 1
                streak = self._export_failure_streak.get(module_id, 0) + 1
                self._export_failure_streak[module_id] = streak
                self._log_session_event(session_name, "WARNING",
                    f"Export failed for {module_id} — path: {export_path}"
                    + (f" ({streak} consecutive failures)" if streak > 1 else ""))

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Export state for {module_id} in '{session_name}': {state}")


    # -----------------------------------------------------------------------
    # Getters
    # -----------------------------------------------------------------------

    def get_recording_status(self) -> bool:
        return any(s.state == SessionState.ACTIVE for s in self.sessions.values())

    def get_recording_sessions(self) -> dict:
        return self.sessions

    def get_active_recording_sessions(self) -> dict:
        return {k: v for k, v in self.sessions.items() if v.state == SessionState.ACTIVE}

    def get_session_name_from_target(self, target: str) -> Optional[str]:
        """Find a non-stopped session that the target belongs to."""
        non_stopped = {
            k: v for k, v in self.sessions.items()
            if v.state != SessionState.STOPPED
        }
        if not non_stopped:
            return None
        if target == "all":
            if len(non_stopped) != 1:
                return None
            return next(iter(non_stopped))
        for name, session in non_stopped.items():
            if target in session.modules:
                return name
        return None


    # -----------------------------------------------------------------------
    # Module lifecycle events
    # -----------------------------------------------------------------------

    def add_module_to_session(self, session_name: str, module_id: str) -> dict:
        """Add a late-joining or replacement module to an active session.

        If the session is in ERROR state (e.g. a module broke), broken modules
        whose stop_state is "recording" but are not actually recording are marked
        "stopped" so the monitor can clear the error once the new module starts.
        """
        if session_name not in self.sessions:
            return {"success": False, "error": f"Unknown session '{session_name}'"}

        session = self.sessions[session_name]

        if session.state not in (SessionState.ACTIVE, SessionState.ERROR):
            return {"success": False, "error": f"Session is not active (state: {session.state})"}

        if module_id in session.modules:
            return {"success": False, "error": f"{module_id} is already in this session"}

        if module_id in self._busy_modules():
            return {"success": False, "error": f"{module_id} is already recording in another session"}

        with self._lock:
            if session.state == SessionState.ERROR:
                # Mark broken modules as stopped so the monitor can recover the session.
                for m in session.modules:
                    if (session.module_stop_states.get(m) == "recording"
                            and not self.facade.is_module_recording(m)):
                        session.module_stop_states[m] = "stopped"
                session.error_message = ""
                session.state = SessionState.ACTIVE

            session.modules.append(module_id)
            session.module_stop_states[module_id] = "recording"
            session.module_export_states[module_id] = "idle"

        params = {"duration": 0, "session_name": session_name}
        self.facade.send_command(module_id, "start_recording", params)
        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Module {module_id} added to session '{session_name}'")
        return {"success": True}


    def module_offline(self, module_id: str) -> None:
        """Record that a module went offline; if it was mid-stop, count it as done."""
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            return
        session = self.sessions[session_name]

        if session.module_stop_states.get(module_id) == "stopping":
            with self._lock:
                session.module_stop_states[module_id] = "stopped"
            self._check_all_stopped(session_name)

        if session.state != SessionState.STOPPED:
            session.error_message = f"{module_id} is offline"
            if session.state != SessionState.ERROR:
                session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
            session.state = SessionState.ERROR
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            self.logger.info(f"Session '{session_name}' → ERROR: {module_id} offline")
            self._log_session_event(session_name, "FAULT", f"{module_id} went offline")
            self.facade.send_alert(
                key=f"module_offline_{module_id}",
                title=f"Module offline — {module_id}",
                message=f"Module **{module_id}** went offline during recording session **{session_name}**.",
            )


    def module_back_online(self, module_id: str) -> None:
        """Resume recording for a module that reconnected during an active session."""
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            return
        session = self.sessions[session_name]

        if session.state in (SessionState.ACTIVE, SessionState.ERROR):
            already_tracking = (
                session.module_stop_states.get(module_id) == "recording"
                and session.state == SessionState.ACTIVE
                and self.facade.is_module_recording(module_id)
            )
            if already_tracking:
                # Module is already tracked as recording and confirmed still recording
                # (e.g. an mDNS service-update triggered a spurious online transition).
                # No recovery needed — avoid sending a duplicate start_recording.
                self.logger.info(
                    f"Module {module_id} online event — already recording in '{session_name}', no action needed"
                )
                return

            # Mark as RECORDING immediately so the session monitor doesn't see a
            # discrepancy between stop_state and module.status in the window between
            # sending start_recording and receiving the ack (or "Already recording").
            self.facade.notify_module_recording(module_id)
            params = {"duration": 0, "session_name": session_name}
            self.facade.send_command(module_id, "start_recording", params)
            with self._lock:
                session.module_stop_states[module_id] = "recording"
                if session.state == SessionState.ERROR:
                    session.error_message = ""
                    session.state = SessionState.ACTIVE
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            self.logger.info(
                f"Module {module_id} back online — restarted recording in '{session_name}'"
            )
            self._log_session_event(session_name, "RECOVERY",
                f"{module_id} came back online — recording resumed")


    def handle_module_health_response(self, module_id: str, is_recording: bool) -> None:
        """Called when a get_health response arrives for a module in 'unknown' stop state.

        If the module is still recording, recover it via module_back_online().
        If it stopped recording, mark it as stopped so the session can be assessed.
        """
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            return
        session = self.sessions[session_name]
        if session.module_stop_states.get(module_id) != "unknown":
            return

        crash_recovery = session.error_message == "Controller restarted during active session"

        if is_recording or crash_recovery:
            # Re-issue start_recording in two cases:
            # 1. Module is still recording (e.g. survived a partial outage).
            # 2. Controller restarted — module stopped because we crashed, not because
            #    the session ended, so command it to resume.
            action = "still recording" if is_recording else "controller restart recovery"
            self.logger.info(
                f"Health probe: {module_id} — {action} — resuming in '{session_name}'"
            )
            self.module_back_online(module_id)
        else:
            self.logger.info(
                f"Health probe: {module_id} is not recording — marking stopped in '{session_name}'"
            )
            with self._lock:
                session.module_stop_states[module_id] = "stopped"
            self.facade.update_sessions(self.sessions)
            self._save_sessions()


    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _format_session_name(self, session_name: str, target: str = "all") -> str:
        timestamp = datetime.now().strftime("%H%M%S")
        safe = "".join(c for c in session_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe = safe.replace(' ', '_')
        if target and target != "all":
            return f"{safe}-{target}-{timestamp}"
        return f"{safe}-{timestamp}"


    def _check_all_stopped(self, session_name: str) -> None:
        """Transition the session to STOPPED (or back to SCHEDULED for daily sessions)
        when no module is still 'stopping'."""
        session = self.sessions.get(session_name)
        if not session or session.state == SessionState.STOPPED:
            return

        still_stopping = any(
            v == "stopping" for v in session.module_stop_states.values()
        )
        if still_stopping:
            return

        with self._lock:
            if session.scheduled_stopping:
                # Daily scheduled session — return to SCHEDULED so it runs tomorrow
                session.scheduled_stopping = False
                session.state = SessionState.SCHEDULED
                new_state = SessionState.SCHEDULED
            else:
                session.state = SessionState.STOPPED
                session.end_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                new_state = SessionState.STOPPED

        self.logger.info(
            f"All modules confirmed stopped — session '{session_name}' → {new_state}"
        )
        if new_state == SessionState.STOPPED:
            self._log_session_event(session_name, "INFO",
                "Session stopped — all modules confirmed")
        else:
            self._log_session_event(session_name, "INFO",
                "Daily recording run ended")
        self.facade.update_sessions(self.sessions)
        self._save_sessions()

        if new_state == SessionState.SCHEDULED:
            self._send_daily_summary(session_name, session)


    def _start_scheduled_session(self, session_name: str, today: str) -> None:
        session = self.sessions[session_name]
        config = self.facade.get_config()
        rec_cfg = config.get("recording", {})

        # ── Refresh module list from target ───────────────────────────────────
        # Modules online at session-creation time may differ from today's set.
        current_modules = list(self.facade.get_modules_by_target(session.target).keys())
        if not current_modules:
            # Transient — modules may not have connected yet.  Retry next cycle.
            self.logger.info(
                f"Scheduled session '{session_name}': no '{session.target}' modules online yet "
                f"— will retry"
            )
            return

        # Skip any module already occupied by another active session
        busy = self._busy_modules()
        available = [m for m in current_modules if m not in busy]
        if not available:
            # Transient — busy modules may finish stopping soon.  Retry next cycle.
            self.logger.info(
                f"Scheduled session '{session_name}': all target modules are busy — will retry"
            )
            return

        if set(available) != set(session.modules):
            self.logger.info(
                f"Module list for '{session_name}' refreshed: "
                f"{sorted(session.modules)} → {sorted(available)}"
            )
        with self._lock:
            session.modules = available

        # ── Pre-flight readiness check (two-pass, non-blocking) ───────────────
        # Pass 1: dispatch validate_readiness + get_health to all target modules
        #         and return — responses arrive asynchronously over the PoE LAN.
        # Pass 2: one monitor cycle later (≥5 s) the responses have arrived;
        #         check module statuses and alert on NOT_READY before proceeding.
        _READINESS_WAIT_SECS = 5  # one monitor cycle is ample for LAN round-trips
        sent_at = self._readiness_checks.get(session_name)
        if sent_at is None:
            for mid in available:
                self.facade.send_command(mid, "get_health", {})
                self.facade.send_command(mid, "validate_readiness", {})
            self._readiness_checks[session_name] = time.time()
            self.logger.info(
                f"Scheduled session '{session_name}': dispatched readiness checks "
                f"to {len(available)} module(s) — will verify next cycle"
            )
            return

        if time.time() - sent_at < _READINESS_WAIT_SECS:
            return  # responses still in flight — wait one more cycle

        # Responses should be in by now — check and clear the pending entry
        del self._readiness_checks[session_name]
        not_ready = []
        for mid in available:
            mod = self.facade.get_modules_by_target(mid).get(mid, {})
            if mod.get("status") == "NOT_READY":
                msg = mod.get("ready_message") or "no detail"
                not_ready.append(f"{mid}: {msg}")

        if not_ready:
            self.logger.warning(
                f"Scheduled session '{session_name}': module readiness warnings — "
                + "; ".join(not_ready)
            )
            self.facade.send_alert(
                key=f"readiness_{session_name}_{today}",
                title=f"Module readiness warning — {session_name}",
                message=(
                    f"Session **{session_name}** started its {today} run but "
                    f"the following module(s) reported not ready:\n\n"
                    + "\n".join(f"- {m}" for m in not_ready)
                    + "\n\nRecording will proceed — check module logs for details."
                ),
                severity="warning",
            )

        # ── Expected module count ─────────────────────────────────────────────
        expected_counts: dict = rec_cfg.get("expected_module_counts", {})
        expected = expected_counts.get(session.target, 0)
        if expected > 0 and len(available) < expected:
            self.facade.send_alert(
                key=f"module_count_{session_name}_{today}",
                title=f"Low module count — {session_name}",
                message=(
                    f"Session **{session_name}** ({today}): expected {expected} "
                    f"'{session.target}' module(s) but only {len(available)} are online.\n\n"
                    f"Online: {', '.join(available)}"
                ),
                severity="warning",
            )

        # ── Local disk space (warning only — does not block) ──────────────────
        local_min_free = rec_cfg.get("local_min_free_pct", 10)
        low_disk = []
        for module_id in available:
            h = self.facade.get_module_health(module_id)
            if h:
                disk_used = h.get("disk_space")
                if disk_used is not None and disk_used > (100 - local_min_free):
                    free_pct = 100 - disk_used
                    low_disk.append(f"{module_id} ({free_pct:.0f}% free)")
        if low_disk:
            self.facade.send_alert(
                key=f"local_disk_{session_name}_{today}",
                title=f"Low local disk — {session_name}",
                message=(
                    f"Session **{session_name}** ({today}): these modules have less than "
                    f"{local_min_free}% local disk free — recording may fail mid-session:\n\n"
                    + "\n".join(f"- {m}" for m in low_disk)
                ),
                severity="warning",
            )

        # ── NAS free space ────────────────────────────────────────────────────
        nas_min  = rec_cfg.get("nas_min_free_pct",  5)
        nas_warn = rec_cfg.get("nas_warn_free_pct", 15)
        nas = self._check_nas_space()
        if not nas["ok"]:
            self.logger.error(
                f"Scheduled session '{session_name}': NAS space check failed: {nas['error']}"
            )
        else:
            if nas["free_pct"] < nas_min:
                err = (
                    f"NAS only {nas['free_pct']:.1f}% free ({nas['free_gb']:.0f} GiB) — "
                    f"minimum threshold is {nas_min}%"
                )
                self.logger.error(f"Scheduled session '{session_name}' blocked: {err}")
                with self._lock:
                    session.state = SessionState.ERROR
                    session.error_message = err
                    session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                    session.scheduled_last_start_date = today
                self.facade.update_sessions(self.sessions)
                self._save_sessions()
                self._log_session_event(session_name, "FAULT",
                    f"Scheduled recording blocked — NAS full: {err}")
                self.facade.send_alert(
                    key=f"nas_full_{session_name}_{today}",
                    title=f"Scheduled recording blocked — NAS nearly full",
                    message=f"Session **{session_name}** could not start its {today} run.\n\n{err}",
                    severity="error",
                )
                return
            elif nas["free_pct"] < nas_warn:
                self.facade.send_alert(
                    key=f"nas_warn_{today}",
                    title="NAS space low",
                    message=(
                        f"NAS is {nas['free_pct']:.1f}% free ({nas['free_gb']:.0f} GiB). "
                        f"At current write rates this may fill before the campaign ends."
                    ),
                    severity="warning",
                )

        # ── PTP sync ──────────────────────────────────────────────────────────
        ptp = self._check_ptp_sync(session.modules)
        if not ptp["ok"]:
            # Distinguish transient "still settling" from confirmed bad offsets.
            # "No health data" and "not yet reported" are startup-transient — retry
            # next cycle rather than locking out the entire day.
            settling_phrases = ("not yet reported", "still be settling", "no health data")
            failures = ptp.get("failures", [])
            all_settling = bool(failures) and all(
                any(p in f.get("reason", "").lower() for p in settling_phrases)
                for f in failures
            )
            if all_settling:
                self.logger.info(
                    f"Scheduled session '{session_name}': PTP still settling on "
                    f"{len(failures)} module(s) — will retry next cycle"
                )
                return  # don't lock out the day

            self.logger.error(
                f"Scheduled session '{session_name}' blocked by PTP check: {ptp['error']}"
            )
            with self._lock:
                session.state = SessionState.ERROR
                session.error_message = ptp["error"]
                session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                session.scheduled_last_start_date = today
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            self._log_session_event(session_name, "FAULT",
                f"Scheduled recording blocked — PTP not synchronised: {ptp['error']}")
            self.facade.send_alert(
                key=f"ptp_fail_{session_name}_{today}",
                title=f"Scheduled recording blocked — PTP not synchronised",
                message=(
                    f"Session **{session_name}** could not start its {today} run.\n\n"
                    f"{ptp['error']}"
                ),
                severity="error",
            )
            return

        # ── Snapshot export counts for daily summary ──────────────────────────
        self._daily_run_export_start[session_name] = (
            session.total_exports_complete,
            session.total_exports_failed,
        )

        # ── Start recording ───────────────────────────────────────────────────
        start_at = time.time() + LEAD_SECS
        with self._lock:
            session.state = SessionState.ACTIVE
            session.scheduled_last_start_date = today
            session.start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
            session.module_stop_states = {m: "recording" for m in session.modules}
            session.module_export_states = {m: "idle" for m in session.modules}
            session.recording_start_at = start_at

        params = {"duration": 0, "session_name": session_name, "start_at": start_at}
        for module_id in session.modules:
            self.facade.send_command(module_id, "start_recording", params)
        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Scheduled session '{session_name}' started for {today}")
        self._log_session_event(session_name, "INFO",
            f"Scheduled recording started — run for {today}, modules: {', '.join(session.modules)}")
        self.facade.send_alert(
            key=f"session_started_{session_name}_{today}",
            title=f"Scheduled recording started — {session_name}",
            message=(
                f"Session **{session_name}** started its {today} run "
                f"with {len(session.modules)} module(s)."
            ),
            severity="info",
        )


    def _stop_scheduled_session(self, session_name: str) -> None:
        """Send stop commands for today's run of a scheduled session.

        The session stays in ACTIVE state until all modules confirm via
        module_stopped(), at which point _check_all_stopped() transitions it
        back to SCHEDULED (not STOPPED) so it runs again tomorrow.
        """
        session = self.sessions[session_name]
        with self._lock:
            for module_id in session.modules:
                session.module_stop_states[module_id] = "stopping"
            session.end_time = datetime.now().strftime("%Y%m%d-%H%M%S")
            # Mark as SCHEDULED_STOPPING so _check_all_stopped knows to
            # return to SCHEDULED rather than STOPPED.
            session.scheduled_stopping = True

        for module_id in session.modules:
            self.facade.send_command(module_id, "stop_recording", {})

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Scheduled session '{session_name}' stop commands sent")


    def _check_ptp_mid_recording(self, session_name: str, session: RecordingSession) -> None:
        """Warn when PTP offset exceeds threshold on any actively-recording module.

        Only fires on transitions (newly degraded / newly recovered) — not every cycle.
        None offsets are skipped: ptp4l may be restarting; we want confirmed violations only.
        """
        config = self.facade.get_config()
        threshold_us: float = config.get("recording", {}).get("ptp_threshold_us", 50.0)
        now = time.time()
        currently_degraded = self._ptp_degraded.setdefault(session_name, set())
        newly_degraded: list = []
        newly_recovered: list = []

        for module_id in session.modules:
            if session.module_stop_states.get(module_id) != "recording":
                continue
            health = self.facade.get_module_health(module_id)
            if not health or health.get("status") == "offline":
                continue
            if now - health.get("last_heartbeat", 0) > 90.0:
                continue
            offset_ns = health.get("ptp4l_offset_ns")
            if offset_ns is None:
                continue

            offset_us = offset_ns / 1000
            was_degraded = module_id in currently_degraded
            is_degraded = abs(offset_us) > threshold_us

            if is_degraded and not was_degraded:
                currently_degraded.add(module_id)
                newly_degraded.append((module_id, round(offset_us, 1)))
            elif not is_degraded and was_degraded:
                currently_degraded.discard(module_id)
                newly_recovered.append(module_id)

        if newly_degraded:
            detail = "; ".join(f"{mid}: {us:+.1f}µs" for mid, us in newly_degraded)
            warning = f"PTP sync degraded — {detail} (threshold {threshold_us:.0f}µs)"
            self.logger.warning(f"Session '{session_name}': {warning}")
            with self._lock:
                session.ptp_warning = warning
            self._log_session_event(session_name, "WARNING", warning)
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            self.facade.send_alert(
                key=f"ptp_degraded_{session_name}",
                title=f"PTP sync degraded — {session_name}",
                message=warning,
                severity="warning",
            )

        if newly_recovered and not currently_degraded:
            self.logger.info(f"Session '{session_name}': PTP recovered on all modules")
            with self._lock:
                session.ptp_warning = None
            self._log_session_event(session_name, "RECOVERY",
                "PTP sync recovered — all modules within threshold")
            self.facade.update_sessions(self.sessions)
            self._save_sessions()


    def _check_nas_space_periodic(self) -> None:
        """Periodically alert when NAS free space crosses the warning threshold."""
        config = self.facade.get_config()
        rec_cfg = config.get("recording", {})
        nas_warn = rec_cfg.get("nas_warn_free_pct", 15)
        nas_min  = rec_cfg.get("nas_min_free_pct",  5)
        nas = self._check_nas_space()
        if not nas.get("ok"):
            return  # mount error handled elsewhere
        free = nas["free_pct"]
        if free < nas_min:
            self.facade.send_alert(
                key="nas_critical",
                title="NAS critically low — recording at risk",
                message=(
                    f"NAS is only **{free:.1f}%** free ({nas['free_gb']:.0f} GiB). "
                    f"New sessions will be blocked below {nas_min}%. "
                    f"Free up space immediately."
                ),
                severity="error",
            )
        elif free < nas_warn:
            self.facade.send_alert(
                key="nas_low",
                title="NAS space low",
                message=(
                    f"NAS is {free:.1f}% free ({nas['free_gb']:.0f} GiB). "
                    f"At current write rates this may fill before the campaign ends."
                ),
                severity="warning",
            )


    def _check_export_staleness(self) -> None:
        """Alert when a recording module has not produced a successful export for too long."""
        config = self.facade.get_config()
        stale_mins = config.get("recording", {}).get("export_stale_mins", 150)
        stale_secs = stale_mins * 60
        now = time.time()

        for session_name, session in list(self.sessions.items()):
            if session.state != SessionState.ACTIVE:
                continue
            start_at = session.recording_start_at or 0
            if now - start_at < stale_secs:
                continue  # session too young to have produced an export yet

            for module_id in session.modules:
                if session.module_stop_states.get(module_id) != "recording":
                    continue
                last_ok = self._last_export_success.get(module_id, 0)
                if last_ok < start_at and (now - start_at) >= stale_secs:
                    self.facade.send_alert(
                        key=f"export_stale_{module_id}",
                        title=f"Export stale — {module_id}",
                        message=(
                            f"Module **{module_id}** in session **{session_name}** "
                            f"has been recording for {int((now - start_at) / 60)} min "
                            f"without a successful export. "
                            f"Check local disk, Samba mount, and export queue."
                        ),
                        severity="warning",
                    )


    def _check_session_gaps(self, today: str) -> None:
        """Alert if a scheduled session missed its previous run.

        Runs once per calendar day, immediately after midnight.  A 'gap' is
        detected when the session should have run yesterday but its
        scheduled_last_start_date is not yesterday.
        """
        from datetime import timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        yesterday_weekday = (date.today() - timedelta(days=1)).weekday()

        for session_name, session in list(self.sessions.items()):
            if not session.scheduled:
                continue
            if session.state == SessionState.STOPPED:
                continue
            # Was yesterday in scope for this session?
            days_match = (
                not session.scheduled_days
                or yesterday_weekday in session.scheduled_days
            )
            if not days_match:
                continue
            if session.scheduled_last_start_date != yesterday:
                self.facade.send_alert(
                    key=f"gap_{session_name}_{today}",
                    title=f"Scheduled session missed a run — {session_name}",
                    message=(
                        f"Session **{session_name}** was expected to run on {yesterday} "
                        f"but its last recorded run was "
                        f"**{session.scheduled_last_start_date or 'never'}**. "
                        f"Check the controller logs for that date."
                    ),
                    severity="error",
                )
                self.logger.warning(
                    f"Gap detected: session '{session_name}' last ran "
                    f"{session.scheduled_last_start_date or 'never'}, "
                    f"expected {yesterday}"
                )


    def _monitor_sessions(self) -> None:
        """Background thread: drive scheduled timers and health-check active sessions."""
        while True:
            time.sleep(_MONITOR_INTERVAL_SECS)
            self._monitor_cycle += 1
            current_time = datetime.now().strftime("%H:%M")
            today = date.today().isoformat()

            # ── Periodic checks (every ~5 min = 60 × 5 s cycles) ─────────────
            if self._monitor_cycle % 60 == 0:
                self._check_nas_space_periodic()
                self._check_export_staleness()

            # ── Daily gap detection (once per calendar day) ───────────────────
            if self._gap_check_date != today:
                self._gap_check_date = today
                self._check_session_gaps(today)

            for session_name, session in list(self.sessions.items()):
                try:
                    if session.state == SessionState.STOPPED:
                        continue

                    if session.scheduled:
                        today_weekday = date.today().weekday()
                        days_match = (
                            not session.scheduled_days
                            or today_weekday in session.scheduled_days
                        )

                        # Start if today matches the day filter, not already started today,
                        # and the start time has been reached
                        if (session.state != SessionState.ACTIVE
                                and session.scheduled_last_start_date != today
                                and days_match
                                and current_time >= session.scheduled_start_time):
                            self._start_scheduled_session(session_name, today)

                        # Stop if active, started today, and end time reached
                        elif (session.state == SessionState.ACTIVE
                                and session.scheduled_last_start_date == today
                                and current_time >= session.scheduled_end_time):
                            self._stop_scheduled_session(session_name)

                    elif session.state in (SessionState.ACTIVE, SessionState.ERROR):
                        # Skip health check during lead window and startup grace period
                        if (session.recording_start_at
                                and time.time() < session.recording_start_at + _STARTUP_GRACE_SECS):
                            continue

                        # Auto-stop timed sessions when their duration has elapsed
                        if (session.timed_stop_at
                                and time.time() >= session.timed_stop_at
                                and session.state == SessionState.ACTIVE):
                            self.logger.info(
                                f"Timed session '{session_name}' duration elapsed — stopping"
                            )
                            self.stop_session(session_name)
                            continue

                        # Probe any modules whose state is unknown (e.g. after black start).
                        # Re-probe on a cooldown so slow-booting modules are not abandoned
                        # after a single unanswered attempt.
                        _REPROBE_INTERVAL_S = 60
                        now_ts = time.time()
                        for m in session.modules:
                            if session.module_stop_states.get(m) != "unknown":
                                continue
                            last_probe = self._health_probe_times.get(m, 0)
                            if now_ts - last_probe >= _REPROBE_INTERVAL_S:
                                self._health_probe_times[m] = now_ts
                                try:
                                    self.facade.send_command(m, "get_health", {})
                                    self.logger.info(
                                        f"Sent get_health probe to {m} to resolve unknown state"
                                    )
                                except Exception as e:
                                    self.logger.warning(f"Could not probe {m}: {e}")

                        # Check every module that should be recording actually is.
                        # Require _NOT_RECORDING_STRIKES_THRESHOLD consecutive misses before
                        # declaring ERROR — one miss is normal during a segment transition.
                        _NOT_RECORDING_STRIKES_THRESHOLD = 2
                        should_be_recording = [
                            m for m in session.modules
                            if session.module_stop_states.get(m) == "recording"
                        ]
                        not_recording = []
                        for m in should_be_recording:
                            key = (session_name, m)
                            if not self.facade.is_module_recording(m):
                                strikes = self._not_recording_strikes.get(key, 0) + 1
                                self._not_recording_strikes[key] = strikes
                                if strikes >= _NOT_RECORDING_STRIKES_THRESHOLD:
                                    not_recording.append(m)
                            else:
                                self._not_recording_strikes.pop(key, None)
                        if not_recording:
                            msg = f"Not recording: {', '.join(not_recording)}"
                            if session.error_message != msg or session.state != SessionState.ERROR:
                                session.error_message = msg
                                if session.state != SessionState.ERROR:
                                    session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                                session.state = SessionState.ERROR
                                self.facade.update_sessions(self.sessions)
                                self.facade.send_alert(
                                    key=f"session_error_{session_name}",
                                    title=f"Recording error — {session_name}",
                                    message=(
                                        f"Session **{session_name}** has entered an error state. "
                                        f"The following modules are not recording: {', '.join(not_recording)}."
                                    ),
                                )
                        elif session.state == SessionState.ERROR and should_be_recording:
                            # All modules we were actively checking are now recording — recover.
                            # Guard: if should_be_recording is empty (e.g. all states are "unknown"
                            # after a restart) we cannot confirm recovery, so leave the ERROR state.
                            session.error_message = ""
                            session.state = SessionState.ACTIVE
                            for m in session.modules:
                                self._not_recording_strikes.pop((session_name, m), None)
                            self.facade.update_sessions(self.sessions)

                        if session.state == SessionState.ACTIVE:
                            self._check_ptp_mid_recording(session_name, session)

                except Exception as e:
                    self.logger.error(f"Error monitoring session '{session_name}': {e}")


    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _save_sessions(self) -> None:
        """Write all sessions to disk as JSON."""
        try:
            os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
            data = {name: asdict(session) for name, session in self.sessions.items()}
            with open(SESSIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save sessions: {e}")


    def _load_sessions(self) -> None:
        """Load sessions from disk on startup.

        Sessions that were ACTIVE when the controller last stopped are marked ERROR
        so the operator can see they need attention.
        """
        share_err = self._check_share_writable()
        if share_err:
            self.logger.warning(f"Startup share check: {share_err}")

        if not os.path.exists(SESSIONS_FILE):
            return
        try:
            with open(SESSIONS_FILE) as f:
                data = json.load(f)
            for name, d in data.items():
                session = RecordingSession(**d)
                if session.state == SessionState.ACTIVE:
                    session.state = SessionState.ERROR
                    session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                    session.error_message = "Controller restarted during active session"
                    session.module_stop_states = {m: "unknown" for m in session.modules}
                    self._log_session_event(name, "FAULT",
                        "Controller restarted during active session — awaiting module reconnect")
                self.sessions[name] = session
            self.logger.info(f"Loaded {len(self.sessions)} session(s) from disk")
        except Exception as e:
            self.logger.error(f"Failed to load sessions: {e}")


    def _get_share_root(self) -> str:
        try:
            return self.facade.get_share_path()
        except AttributeError:
            return _SHARE_ROOT_DEFAULT

    def _log_session_event(self, session_name: str, level: str, message: str) -> None:
        """Append a timestamped event line to session_events.log on the NAS share.

        Silently swallows all errors — the log is best-effort and must never
        affect session operation or propagate exceptions to the caller.
        """
        log_path = os.path.join(self._get_share_root(), session_name, "session_events.log")
        line = f"{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')} [{level:<8}] {message}\n"
        try:
            session_dir = os.path.dirname(log_path)
            os.makedirs(session_dir, exist_ok=True)
            os.chmod(session_dir, 0o777)
            with open(log_path, "a") as f:
                f.write(line)
        except Exception:
            pass
