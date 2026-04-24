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

_MONITOR_INTERVAL_SECS = 5

# How far into the future modules are told to start recording.
# PTP-synchronised clocks mean all modules hit this timestamp together.
LEAD_SECS = 3


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


# ---------------------------------------------------------------------------
# Recording manager
# ---------------------------------------------------------------------------

class Recording:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions: Dict[str, RecordingSession] = {}
        self._lock = threading.Lock()
        self._health_probed: set = set()  # modules already probed for black-start recovery

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

    def _check_share_writable(self) -> Optional[str]:
        """Return an error string if the controller share is not writable, else None."""
        share = "/home/pi/controller_share"
        probe = os.path.join(share, ".saviour_write_probe")
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return None
        except Exception as e:
            return f"Controller share not writable ({share}): {e}"

    def create_session(self, session_name: str, target: str) -> dict:
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

        session_name = self._format_session_name(session_name, target)

        start_at = time.time() + LEAD_SECS

        session = RecordingSession(
            session_name=session_name,
            target=target,
            state=SessionState.ACTIVE,
            modules=modules,
            start_time=datetime.now().strftime("%Y%m%d-%H%M%S"),
            module_stop_states={m: "recording" for m in modules},
            module_export_states={m: "idle" for m in modules},
            recording_start_at=start_at,
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
        self.facade.send_alert(
            key=f"session_started_{session_name}",
            title=f"Recording started — {session_name}",
            message=f"Session **{session_name}** started with {len(modules)} module(s): {', '.join(modules)}.",
            severity="info",
        )
        return {"success": True, "session_name": session_name}


    def create_scheduled_session(self, session_name: str, target: str,
                                  start_time: str, end_time: str) -> dict:
        """Create a session that records daily between start_time and end_time (HH:MM)."""
        if not session_name or not session_name.strip():
            return {"success": False, "error": "Session name cannot be empty"}
        if not start_time or not end_time:
            return {"success": False, "error": "start_time and end_time are required (HH:MM)"}

        modules = list(self.facade.get_modules_by_target(target).keys())
        if not modules:
            return {"success": False, "error": f"No online modules found for target '{target}'"}

        session_name = self._format_session_name(session_name, target)

        session = RecordingSession(
            session_name=session_name,
            target=target,
            state=SessionState.SCHEDULED,
            modules=modules,
            scheduled=True,
            scheduled_start_time=start_time,
            scheduled_end_time=end_time,
            module_stop_states={m: "recording" for m in modules},
            module_export_states={m: "idle" for m in modules},
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
            share_dir = f"/home/pi/controller_share/{session_name}"
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
                session.module_stop_states[module_id] = "stopping"

        for module_id in session.modules:
            self.facade.send_command(module_id, "stop_recording", {})

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(
            f"Stop command sent to {len(session.modules)} module(s) in '{session_name}'"
        )


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
            elif state == "failed":
                self.sessions[session_name].total_exports_failed += 1

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
            session.state = SessionState.ERROR
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            self.logger.info(f"Session '{session_name}' → ERROR: {module_id} offline")
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

        if is_recording:
            self.logger.info(
                f"Health probe: {module_id} is still recording — recovering session '{session_name}'"
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
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
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
        self.facade.update_sessions(self.sessions)
        self._save_sessions()


    def _start_scheduled_session(self, session_name: str, today: str) -> None:
        session = self.sessions[session_name]
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
        self.facade.send_alert(
            key=f"session_started_{session_name}_{today}",
            title=f"Scheduled recording started — {session_name}",
            message=f"Session **{session_name}** started its daily run for {today} with {len(session.modules)} module(s).",
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


    def _monitor_sessions(self) -> None:
        """Background thread: drive scheduled timers and health-check active sessions."""
        while True:
            time.sleep(_MONITOR_INTERVAL_SECS)
            current_time = datetime.now().strftime("%H:%M")
            today = date.today().isoformat()

            for session_name, session in list(self.sessions.items()):
                try:
                    if session.state == SessionState.STOPPED:
                        continue

                    if session.scheduled:
                        # Start if not already started today and time has come
                        if (session.state != SessionState.ACTIVE
                                and session.scheduled_last_start_date != today
                                and current_time >= session.scheduled_start_time):
                            self._start_scheduled_session(session_name, today)

                        # Stop if active, started today, and end time reached
                        elif (session.state == SessionState.ACTIVE
                                and session.scheduled_last_start_date == today
                                and current_time >= session.scheduled_end_time):
                            self._stop_scheduled_session(session_name)

                    elif session.state in (SessionState.ACTIVE, SessionState.ERROR):
                        # Skip health check while still in the synchronised lead window
                        if session.recording_start_at and time.time() < session.recording_start_at:
                            continue

                        # Probe any modules whose state is unknown (e.g. after black start).
                        # Send get_health once per module so the response can resolve their state.
                        for m in session.modules:
                            if (session.module_stop_states.get(m) == "unknown"
                                    and m not in self._health_probed):
                                self._health_probed.add(m)
                                try:
                                    self.facade.send_command(m, "get_health", {})
                                    self.logger.info(
                                        f"Sent get_health probe to {m} to resolve unknown state"
                                    )
                                except Exception as e:
                                    self.logger.warning(f"Could not probe {m}: {e}")

                        # Check every module that should be recording actually is
                        should_be_recording = [
                            m for m in session.modules
                            if session.module_stop_states.get(m) == "recording"
                        ]
                        not_recording = [
                            m for m in should_be_recording
                            if not self.facade.is_module_recording(m)
                        ]
                        if not_recording:
                            msg = f"Not recording: {', '.join(not_recording)}"
                            if session.error_message != msg or session.state != SessionState.ERROR:
                                session.error_message = msg
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
                        elif session.state == SessionState.ERROR:
                            session.error_message = ""
                            session.state = SessionState.ACTIVE
                            self.facade.update_sessions(self.sessions)

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
                    session.error_message = "Controller restarted during active session"
                    session.module_stop_states = {m: "unknown" for m in session.modules}
                self.sessions[name] = session
            self.logger.info(f"Loaded {len(self.sessions)} session(s) from disk")
        except Exception as e:
            self.logger.error(f"Failed to load sessions: {e}")
