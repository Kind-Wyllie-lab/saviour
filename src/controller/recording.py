"""
Recording manager for the SAVIOUR Controller.

Each module can only be associated with one recording session at a time.

Author: Andrew SG
Created: 26/01/2026
"""

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from src.controller import recording_plans
from src.shared.data_rate import estimate_recording_bytes_per_s

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
    # Created, modules assigned, not yet recording -- waiting on an
    # explicit Start action (force_start_session). Immediate/timed
    # sessions land here now instead of going straight to ACTIVE; a
    # PENDING session can be discarded (delete_session) same as a
    # stopped/error one.
    PENDING   = "pending"
    SCHEDULED = "scheduled"
    ACTIVE    = "active"
    # Habitat Session paused by an operator (husbandry access etc.): every
    # plan's modules are stopped, the session object and its plans stay put,
    # resume_session() re-arms whatever should be recording at that moment.
    PAUSED    = "paused"
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
    start_time:                str | None = None
    end_time:                  str | None = None
    error_message:             str  = ""
    scheduled:                 bool = False
    scheduled_start_time:      str | None = None   # HH:MM
    scheduled_end_time:        str | None = None   # HH:MM
    # Prevents a scheduled session from starting more than once on the same
    # calendar day (YYYY-MM-DD).
    scheduled_last_start_date: str | None = None
    # Per-module stop acknowledgement: "recording" | "stopping" | "stopped" | "unknown"
    module_stop_states:        dict = field(default_factory=dict)
    # Per-module export tracking:  "idle" | "pending" | "complete" | "failed"
    module_export_states:      dict = field(default_factory=dict)
    # Cumulative count of completed exports across all segments
    total_exports_complete:    int  = 0
    total_exports_failed:      int  = 0
    # Outstanding export_ready signals not yet resolved (complete, or a final
    # give-up after retries) — the "certainty" signal for whether every file
    # this session produced has actually landed on the controller's share.
    pending_exports:           int  = 0
    # Epoch when the session transitioned to STOPPED; used to time the
    # export-stall check below. None until stopped.
    stopped_epoch:              float | None = None
    # Set once a stale-pending-export alert has fired for this stopped session,
    # so the monitor loop doesn't re-alert every cycle.
    export_stall_alerted:      bool = False
    # UTC epoch at which modules are scheduled to begin recording (time.time() + LEAD_SECS).
    # None for immediate starts (e.g. module_back_online).
    recording_start_at:        float | None = None
    # Set by _stop_scheduled_session so _check_all_stopped returns to SCHEDULED
    # rather than STOPPED when the day's run finishes.
    scheduled_stopping:        bool = False
    # Timestamp (YYYYMMDD-HHMMSS) when this session most recently entered ERROR state.
    # Never cleared after recovery — preserves the fault record for display.
    error_time:                str | None = None
    # Timed sessions: requested duration in minutes, e.g. 12.25 == 12m15s
    # (for display purposes; may carry a fractional/sub-minute part).
    duration_minutes:          float | None = None
    # Timed sessions: epoch timestamp at which the session should auto-stop.
    # None means no auto-stop (infinite / manual stop).
    timed_stop_at:             float | None = None
    # Scheduled sessions: weekday ints (0=Mon…6=Sun) on which to run.
    # Empty list means every day.
    scheduled_days:            list = field(default_factory=list)
    researcher:                str | None = None
    # Long-term / unattended posture (weeks-long habitat runs). When True the
    # session self-heals instead of parking terminally in ERROR: a module
    # dropping its recording state stays ACTIVE while _monitor_sessions keeps
    # re-issuing start_recording, and the per-fault alert is folded into a
    # once-daily digest instead of firing every event. Orthogonal to
    # scheduled/timed — an unattended session can still carry a timed_stop_at.
    unattended:                bool = False
    # Set while PTP offset exceeds threshold on any recording module; cleared on recovery.
    ptp_warning:               str | None = None
    # Set while a module self-reports its recording capture has gone unhealthy
    # (e.g. a dead AudioMoth thread, a stalled camera pipeline); cleared on recovery.
    recording_health_warning: str | None = None
    # Habitat Session: per-plan recording strategy. Empty for an ordinary
    # single-strategy session, which keeps its existing start/stop path
    # untouched. When non-empty the monitor drives each plan independently
    # (recording_plans.plan_window_action).
    plans:                     list = field(default_factory=list)
    # Why a Habitat Session is PAUSED: None/"operator" (manual, stays paused
    # until an operator resumes) or "disk" (auto-paused on critically low
    # share space, auto-resumed once space recovers).
    pause_reason:              str | None = None
    # Post-hoc sync-quality verdict (framesync_check). Plain session: one slim
    # verdict once every file is on the share. Habitat Session: day_verdicts
    # holds one slim verdict per completed YYYYMMDD; framesync_verdict is the
    # rolled-up worst-of-recent-days summary. Plain dicts so sessions.json
    # round-trips with no __post_init__ rehydration; None/{} until the first
    # check runs.
    framesync_verdict:         dict | None = None
    day_verdicts:              dict = field(default_factory=dict)

    def __post_init__(self):
        # sessions.json round-trips plans as plain dicts; rehydrate them.
        self.plans = [
            p if isinstance(p, recording_plans.RecordingPlan)
            else recording_plans.RecordingPlan.from_dict(p)
            for p in (self.plans or [])
        ]


# ---------------------------------------------------------------------------
# Recording manager
# ---------------------------------------------------------------------------

class Recording:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions: dict[str, RecordingSession] = {}
        self._lock = threading.Lock()
        self._health_probe_times: dict = {}  # module_id → timestamp of last get_health probe
        self._not_recording_strikes: dict = {}  # (session_name, module_id) → consecutive miss count
        self._ptp_degraded: dict[str, set] = {}  # session_name → set of currently-degraded module IDs
        self._recording_health_degraded: dict[str, set] = {}  # session_name → set of module IDs currently self-reporting unhealthy
        self._last_export_success: dict[str, float] = {}   # module_id → epoch of last successful export
        self._export_failure_streak: dict[str, int] = {}   # module_id → consecutive export failures
        self._daily_run_export_start: dict[str, tuple] = {} # session_name → (complete, failed) at day-start
        self._daily_summary_sent: set = set()               # "session:date" already summarized
        self._gap_check_date: str | None = None          # last date gap-check ran
        self._monitor_cycle: int = 0                        # loop counter for periodic tasks
        self._readiness_checks: dict[str, float] = {}       # session_name → epoch when validate_readiness was dispatched
        # Unattended-session fault digest: session_name → {module_id → count of
        # self-healed "not recording" episodes since the last digest flush}.
        self._unattended_fault_digest: dict[str, dict] = {}
        self._unattended_digest_flushed_at: float = time.time()
        # Habitat Session names currently doing a full stop_session(), so a
        # per-plan module stop isn't mistaken by _check_all_stopped for the
        # whole session ending.
        self._full_stopping: set[str] = set()
        # (session_name, date_dir or "__session__") for framesync checks
        # currently queued/running, so _monitor_sessions doesn't re-enqueue.
        self._framesync_inflight: set[tuple[str, str]] = set()

        self._load_sessions()

        self._monitor_thread = threading.Thread(
            target=self._monitor_sessions,
            daemon=True,
            name="session-monitor",
        )
        self._monitor_thread.start()


    # -----------------------------------------------------------------------
    # Notification helpers
    # -----------------------------------------------------------------------

    def _notify_enabled(self, key: str, default: bool = True) -> bool:
        """Return whether a teams notification toggle is enabled."""
        return self.facade.get_config().get("teams", {}).get(key, default)

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
        # Recording-START gate: its own key, deliberately tighter than
        # ptp_threshold_us (the mid-recording "degraded" warning, ~1 ms).
        # Starting only when every module is well under this means a stable,
        # converged offset a viewer can align to a frame; a looser gate lets a
        # still-settling node in and its offset then drifts *during* recording.
        # 50 us is ~10x the worst case seen on a well-connected node and still
        # sub-frame at every supported fps -- raise it (Thresholds tab) on a
        # multi-hop network where transient export-burst jitter makes 50 us
        # cause start retries.
        threshold_us: float = config.get("recording", {}).get("ptp_start_gate_us", 50.0)
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
                        f"ptp4l offset {offset_us:.1f}µs exceeds the "
                        f"{threshold_us:.0f}µs start gate"
                    ),
                })
                continue

            # Also check phc2sys offset — CLOCK_REALTIME must track the PHC.
            # Timestamps use CLOCK_REALTIME; a large phc2sys residual means step
            # corrections mid-session will corrupt inter-camera sync even when
            # ptp4l is settled.
            phc2sys_ns = health.get("phc2sys_offset_ns")
            if phc2sys_ns is not None and abs(phc2sys_ns / 1000) > threshold_us:
                failures.append({
                    "module_id": module_id,
                    "offset_us": round(phc2sys_ns / 1000, 1),
                    "reason": (
                        f"phc2sys offset {phc2sys_ns/1000:.1f}µs exceeds the "
                        f"{threshold_us:.0f}µs start gate — system clock still settling"
                    ),
                })
                continue

            synced.append({"module_id": module_id, "offset_us": round(offset_us, 1)})

        if not failures:
            max_offset = max((abs(m["offset_us"]) for m in synced), default=0.0)
            self.logger.info(
                f"PTP start gate PASSED for {len(synced)} module(s): "
                f"worst offset {max_offset:.1f}µs vs {threshold_us:.0f}µs gate"
            )
            return {
                "ok": True,
                "synced": synced,
                "max_offset_us": round(max_offset, 1),
                "threshold_us": threshold_us,
            }

        detail = "; ".join(f"{f['module_id']}: {f['reason']}" for f in failures)
        self.logger.warning(
            f"PTP start gate FAILED ({threshold_us:.0f}µs) on {len(failures)} "
            f"module(s), {len(synced)} passed — {detail}"
        )
        return {
            "ok": False,
            "failures": failures,
            "error": f"PTP not synchronised on {len(failures)} module(s) — {detail}",
        }


    def _check_share_writable(self) -> str | None:
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
            "",
            f"- Modules: {len(session.modules)}",
            f"- Start: {session.start_time or '—'}  |  End: {session.end_time or '—'}",
            f"- Exports this run: {exports_today} completed, {failures_today} failed",
            f"- NAS free space: {nas_str}",
        ]
        if session.ptp_warning:
            lines.append(f"- PTP warning at stop: {session.ptp_warning}")
        if session.recording_health_warning:
            lines.append(f"- Recording health warning at stop: {session.recording_health_warning}")

        if self._notify_enabled("notify_daily_summary"):
            self.facade.send_alert(
                key=f"daily_summary_{session_name}_{run_date}",
                title=f"Daily summary — {session_name} — {run_date}",
                message="\n".join(lines),
                severity="info",
            )


    # -----------------------------------------------------------------------
    # Unattended-session fault digest
    # -----------------------------------------------------------------------

    _UNATTENDED_DIGEST_INTERVAL_S = 86_400  # 24 h

    def _record_unattended_fault(self, session_name: str, module_ids: list) -> None:
        """Accumulate a self-healed 'not recording' episode for the daily digest
        instead of firing a per-event alert."""
        counts = self._unattended_fault_digest.setdefault(session_name, {})
        for m in module_ids:
            counts[m] = counts.get(m, 0) + 1

    def _flush_unattended_digest(self, force: bool = False) -> None:
        """Emit one Teams alert per unattended session that has accumulated
        self-healed faults, then clear. Called on a 24 h cadence from the
        monitor loop, and with force=True when such a session stops."""
        now = time.time()
        if not force and now - self._unattended_digest_flushed_at < self._UNATTENDED_DIGEST_INTERVAL_S:
            return
        self._unattended_digest_flushed_at = now
        if not self._unattended_fault_digest:
            return
        pending = self._unattended_fault_digest
        self._unattended_fault_digest = {}
        if not self._notify_enabled("notify_session_faults"):
            return
        for session_name, counts in pending.items():
            total = sum(counts.values())
            per_module = ", ".join(
                f"{m} ×{n}" if n > 1 else m
                for m, n in sorted(counts.items(), key=lambda kv: -kv[1])
            )
            self.facade.send_alert(
                key=f"unattended_digest_{session_name}_{int(now)}",
                title=f"Unattended session digest — {session_name}",
                message=(
                    f"Session **{session_name}** self-healed {total} module "
                    f"recording dropout(s) in the last 24 h and is still running: "
                    f"{per_module}. No action needed unless this count is climbing."
                ),
                severity="info",
            )


    def create_session(self, session_name: str, target: str,
                       duration_minutes: float | None = None,
                       researcher: str | None = None,
                       raw_name: bool = False,
                       unattended: bool = False) -> dict:
        """Create a session in PENDING state -- modules assigned, nothing
        recording yet. Call start_pending_session() (via the unified
        force_start_session()) to actually begin recording.

        unattended: long-term posture -- self-heal instead of terminal ERROR,
        digest fault alerts. See RecordingSession.unattended.

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

        ptp = self._check_ptp_sync(modules)
        if not ptp["ok"]:
            self.logger.warning(f"create_session blocked by PTP check: {ptp['error']}")
            return {"success": False, "error": ptp["error"]}

        session_name = self._format_session_name(session_name, target) if not raw_name else \
            "".join(c for c in session_name if c.isalnum() or c in ("-", "_"))

        session = RecordingSession(
            session_name=session_name,
            target=target,
            state=SessionState.PENDING,
            modules=modules,
            duration_minutes=duration_minutes,
            researcher=researcher or None,
            unattended=bool(unattended),
        )

        with self._lock:
            overlap = self._busy_modules() & set(modules)
            if overlap:
                self.logger.warning(f"create_session: modules already recording: {overlap}")
                return {"success": False, "error": f"Already recording: {', '.join(sorted(overlap))}"}
            self.sessions[session_name] = session

        self.facade.update_sessions(self.sessions)
        self._save_sessions()

        self.logger.info(
            f"Session '{session_name}' created (pending) targeting {target} ({len(modules)} modules)"
        )
        self._log_session_event(session_name, "INFO",
            f"Session created — modules: {', '.join(modules)}")
        return {"success": True, "session_name": session_name}


    def update_pending_session(self, session_name: str, new_session_name: str | None,
                               duration_minutes: float | None) -> dict:
        """Edit a PENDING session's name and/or duration before it starts --
        e.g. fixing a typo in the title, or changing a timed duration.
        Locked once the session leaves PENDING (start_pending_session()'s
        own state check is what actually enforces that; this method is
        simply never reachable for a non-pending session from the frontend).
        `new_session_name`/`duration_minutes` reflect the full desired
        state, not a delta -- the caller (edit form) always submits both
        current values, changed or not.
        """
        if session_name not in self.sessions:
            return {"success": False, "error": f"Unknown session '{session_name}'"}
        session = self.sessions[session_name]
        if session.state != SessionState.PENDING:
            return {"success": False, "error": f"Session is not pending (state: {session.state})"}

        final_name = session_name
        if new_session_name and new_session_name.strip():
            candidate = "".join(
                c for c in new_session_name if c.isalnum() or c in (' ', '-', '_')
            ).strip().replace(' ', '_')
            if not candidate:
                return {"success": False, "error": "Session name cannot be empty"}
            if candidate != session_name and candidate in self.sessions:
                return {"success": False, "error": f"Session '{candidate}' already exists"}
            final_name = candidate

        with self._lock:
            session.duration_minutes = duration_minutes if duration_minutes else None
            if final_name != session_name:
                session.session_name = final_name
                self.sessions[final_name] = self.sessions.pop(session_name)

        if final_name != session_name:
            # create_session() already wrote a "Session created" line to
            # {share}/{old_name}/session_events.log -- rename that folder so
            # the log stays continuous under the new name instead of being
            # orphaned under the old one. Best-effort: nothing has recorded
            # into this folder yet (session is still PENDING), so a failure
            # here is a lost log line, not lost data.
            try:
                old_dir = os.path.join(self._get_share_root(), session_name)
                new_dir = os.path.join(self._get_share_root(), final_name)
                if os.path.isdir(old_dir) and not os.path.exists(new_dir):
                    os.rename(old_dir, new_dir)
            except Exception:
                pass

        self.facade.update_sessions(self.sessions)
        self._save_sessions()

        self.logger.info(f"Session '{session_name}' updated" +
            (f" (renamed to '{final_name}')" if final_name != session_name else ""))
        self._log_session_event(final_name, "INFO", "Session details updated")
        return {"success": True, "session_name": final_name}


    def start_pending_session(self, session_name: str) -> dict:
        """Actually begin recording for a PENDING session (create_session()'s
        counterpart) -- re-validates everything fresh rather than reusing
        anything computed at creation time, since a session can sit PENDING
        for an arbitrary amount of time before an operator presses Start.
        """
        if session_name not in self.sessions:
            return {"success": False, "error": f"Unknown session '{session_name}'"}
        session = self.sessions[session_name]
        if session.state != SessionState.PENDING:
            return {"success": False, "error": f"Session is not pending (state: {session.state})"}

        if session.plans:
            return self._start_habitat_session(session_name)

        modules = list(self.facade.get_modules_by_target(session.target).keys())
        if not modules:
            return {"success": False, "error": f"No online modules found for target '{session.target}'"}

        overlap = self._busy_modules() & set(modules)
        if overlap:
            return {"success": False, "error": f"Already recording: {', '.join(sorted(overlap))}"}

        ptp = self._check_ptp_sync(modules)
        if not ptp["ok"]:
            return {"success": False, "error": ptp["error"]}

        start_at = time.time() + LEAD_SECS
        timed_stop_at = (start_at + session.duration_minutes * 60) if session.duration_minutes else None

        with self._lock:
            session.modules = modules
            session.state = SessionState.ACTIVE
            session.start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
            session.module_stop_states = {m: "recording" for m in modules}
            session.module_export_states = {m: "idle" for m in modules}
            session.recording_start_at = start_at
            session.timed_stop_at = timed_stop_at

        params = {"duration": 0, "session_name": session_name, "start_at": start_at}
        for module_id in modules:
            self.facade.send_command(module_id, "start_recording", params)
            # Immediate first read rather than waiting on the next 5-min
            # _poll_recording_state() cycle -- matters most for short
            # sessions (e.g. a 12-min loom run) where 5 min is a large
            # fraction of the whole session.
            self.facade.send_command(
                module_id, "report_recording_state", {"session_name": session_name}
            )
        self.facade.update_sessions(self.sessions)
        self._save_sessions()

        self.logger.info(
            f"Session '{session_name}' started ({len(modules)} modules)"
        )
        self._log_session_event(session_name, "INFO",
            f"Session started — modules: {', '.join(modules)}")
        if self._notify_enabled("notify_recording_started"):
            self.facade.send_alert(
                key=f"session_started_{session_name}",
                title=f"Recording started — {session_name}",
                message=f"Session **{session_name}** started with {len(modules)} module(s): {', '.join(modules)}.",
                severity="info",
            )
        return {"success": True}


    def force_start_session(self, session_name: str) -> dict:
        """Unified entry point for the frontend's "Start"/"Start Now" action,
        regardless of whether the session is PENDING (immediate/timed,
        created but not yet recording) or SCHEDULED (waiting on a time
        window) -- the frontend sends the same force_start_session event
        either way; this just routes to the right underlying start path.
        """
        if session_name not in self.sessions:
            return {"success": False, "error": f"Unknown session '{session_name}'"}
        if self.sessions[session_name].state == SessionState.PENDING:
            return self.start_pending_session(session_name)
        return self.force_start_scheduled_session(session_name)


    def create_scheduled_session(self, session_name: str, target: str,
                                  start_time: str, end_time: str,
                                  days: list | None = None,
                                  researcher: str | None = None,
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
        # Record the schedule provenance now, at creation. Without this a
        # scheduled session has no session_events.log at all until its first
        # run window opens (_start_scheduled_session), so the detail page shows
        # an empty event log for a session that may not run for hours/days.
        _weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        days_desc = (
            "every day" if not (days or [])
            else ", ".join(_weekdays[d] for d in days if 0 <= d < 7)
        )
        modules_desc = ", ".join(modules) if modules else "(resolved at run time)"
        self._log_session_event(
            session_name, "INFO",
            f"Scheduled session created — target {target}, "
            f"{start_time}–{end_time}, {days_desc}, modules: {modules_desc}"
            + (f", researcher: {researcher}" if researcher else "")
        )
        return {"success": True, "session_name": session_name}


    def delete_session(self, session_name: str, delete_files: bool = True,
                       force: bool = False) -> dict:
        """Remove a stopped/error session from the list and optionally delete its files.

        Active and scheduled sessions cannot be deleted; stop them first.

        Refuses (unless force=True) to delete a session with exports that never
        confirmed landing on the share — clearing the session record is the only
        evidence an operator has that a module's files went missing, and once
        it's gone a straggler module's later export-complete signal has no
        session left to attach to (module_export_update() no-ops on an unknown
        session_name).
        """
        if session_name not in self.sessions:
            return {"error": f"Unknown session '{session_name}'"}

        session = self.sessions[session_name]
        if session.state in (SessionState.ACTIVE, SessionState.SCHEDULED):
            return {"error": f"Cannot delete a session in state '{session.state}' — stop it first"}

        if not force and (session.pending_exports > 0 or session.total_exports_failed > 0):
            return {
                "error": (
                    f"Session '{session_name}' has {session.pending_exports} unresolved "
                    f"and {session.total_exports_failed} failed export(s) — delete anyway?"
                ),
                "export_warning": True,
                "session_name": session_name,
                "pending_exports": session.pending_exports,
                "total_exports_failed": session.total_exports_failed,
            }

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

    def clear_ended_sessions(self, delete_files: bool = False, force: bool = False) -> dict:
        """Remove all stopped/error sessions. Files are not deleted by default.

        Unless force=True, sessions with unresolved or permanently-failed
        exports are left in place rather than swept away — see delete_session()
        for why. They stay visible (and clearable individually with force) until
        either the export resolves or an operator explicitly force-clears.
        """
        ended = [
            name for name, s in list(self.sessions.items())
            if s.state not in (SessionState.PENDING, SessionState.ACTIVE, SessionState.SCHEDULED)
        ]
        cleared = []
        skipped = []
        for name in ended:
            result = self.delete_session(name, delete_files=delete_files, force=force)
            if result.get("export_warning"):
                skipped.append(name)
            else:
                cleared.append(name)
        return {"cleared": len(cleared), "skipped": len(skipped), "skipped_sessions": skipped}

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

        # Mark a full stop so _check_all_stopped will actually transition a
        # plan-driven Habitat Session (per-plan stops don't).
        self._full_stopping.add(session_name)
        for plan in session.plans:
            plan.recording = False

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


    # ------------------------------------------------------------------ #
    # Habitat Session -- one session, per-plan recording strategy        #
    # ------------------------------------------------------------------ #

    def create_habitat_session(self, session_name: str, plans_raw: list,
                               researcher: str | None = None,
                               duration_minutes: float | None = None) -> dict:
        """Create a PENDING Habitat Session: one session whose modules are
        split into plans, each recording continuously or in nightly
        windows (see recording_plans). Always unattended-posture."""
        if not session_name or not session_name.strip():
            return {"success": False, "error": "Session name cannot be empty"}
        share_err = self._check_share_writable()
        if share_err:
            return {"success": False, "error": share_err}
        try:
            plans = recording_plans.parse_plans(plans_raw)
        except recording_plans.PlanError as exc:
            return {"success": False, "error": str(exc)}

        modules = [m for p in plans for m in p.modules]
        online = set(self.facade.get_modules().keys())
        missing = [m for m in modules if m not in online]
        if missing:
            return {"success": False,
                    "error": f"module(s) offline: {', '.join(missing)}"}

        ptp = self._check_ptp_sync(modules)
        if not ptp["ok"]:
            return {"success": False, "error": ptp["error"]}

        session_name = "".join(
            c for c in session_name if c.isalnum() or c in ("-", "_")
        )
        with self._lock:
            overlap = self._busy_modules() & set(modules)
            if overlap:
                return {"success": False,
                        "error": f"Already recording: {', '.join(sorted(overlap))}"}
            self.sessions[session_name] = RecordingSession(
                session_name=session_name,
                target=",".join(sorted(online & set(modules))),
                state=SessionState.PENDING,
                modules=modules,
                researcher=researcher or None,
                duration_minutes=duration_minutes,
                unattended=True,
                plans=plans,
            )
        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self._log_session_event(
            session_name, "INFO",
            f"Habitat Session created — {len(plans)} plan(s): "
            + "; ".join(
                f"{p.label} ({p.strategy}, {len(p.modules)} mod)" for p in plans
            ),
        )
        return {"success": True, "session_name": session_name}

    def _start_habitat_session(self, session_name: str) -> dict:
        """PENDING -> ACTIVE for a plan-driven session: nothing records
        until the monitor's plan evaluation starts whatever is in-window."""
        session = self.sessions[session_name]
        start_at = time.time() + LEAD_SECS
        with self._lock:
            session.state = SessionState.ACTIVE
            session.start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
            session.recording_start_at = start_at
            session.module_stop_states = {m: "stopped" for m in session.modules}
            session.module_export_states = {m: "idle" for m in session.modules}
            session.timed_stop_at = (
                start_at + session.duration_minutes * 60
                if session.duration_minutes else None
            )
            for plan in session.plans:
                plan.recording = False
                plan.last_start_date = None
        self._log_session_event(session_name, "INFO", "Habitat Session started")
        self._evaluate_plans(session_name, session)
        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        return {"success": True}

    def pause_session(self, session_name: str) -> dict:
        """Operator pause: stop every plan's modules but keep the session
        (husbandry access etc.). Stays paused until resume_session()."""
        session = self.sessions.get(session_name)
        if not session or not session.plans:
            return {"success": False, "error": "Not a Habitat Session"}
        if session.state != SessionState.ACTIVE:
            return {"success": False,
                    "error": f"Session is {session.state}, not active"}
        self._pause(session, "operator", "PAUSE", "Session paused by operator")
        return {"success": True}

    def _pause(self, session: RecordingSession, reason: str,
               level: str, message: str) -> None:
        with self._lock:
            session.state = SessionState.PAUSED
            session.pause_reason = reason
        for plan in session.plans:
            if plan.recording:
                self._stop_plan(session, plan, f"paused ({reason})")
        self._log_session_event(session.session_name, level, message)
        self.facade.update_sessions(self.sessions)
        self._save_sessions()

    def resume_session(self, session_name: str) -> dict:
        session = self.sessions.get(session_name)
        if not session or not session.plans:
            return {"success": False, "error": "Not a Habitat Session"}
        if session.state != SessionState.PAUSED:
            return {"success": False,
                    "error": f"Session is {session.state}, not paused"}
        self._resume(session, "RESUME", "Session resumed by operator")
        return {"success": True}

    def _resume(self, session: RecordingSession, level: str, message: str) -> None:
        with self._lock:
            session.state = SessionState.ACTIVE
            session.pause_reason = None
            # Clear the once-per-window guard so an interrupted window resumes.
            for plan in session.plans:
                plan.last_start_date = None
        self._log_session_event(session.session_name, level, message)
        self._evaluate_plans(session.session_name, session)
        self.facade.update_sessions(self.sessions)
        self._save_sessions()

    def _check_habitat_disk_autopause(self) -> None:
        """Auto-pause any ACTIVE Habitat Session when the export share is
        critically low, and auto-resume a disk-paused one once space
        recovers (hysteresis so it can't flap). Operator pauses
        (pause_reason != "disk") are never auto-resumed."""
        habitat = [
            (n, s) for n, s in self.sessions.items()
            if s.plans and s.state in (SessionState.ACTIVE, SessionState.PAUSED)
        ]
        if not habitat:
            return
        nas = self._check_nas_space()
        if not nas.get("ok"):
            return
        free = nas["free_pct"]
        rec_cfg = self.facade.get_config().get("recording", {})
        pause_at = rec_cfg.get(
            "habitat_autopause_free_pct", rec_cfg.get("nas_min_free_pct", 5)
        )
        resume_at = rec_cfg.get(
            "habitat_autoresume_free_pct", rec_cfg.get("nas_warn_free_pct", 15)
        )
        for name, session in habitat:
            if session.state == SessionState.ACTIVE and free < pause_at:
                self.logger.warning(
                    f"Habitat Session '{name}': share {free:.1f}% free "
                    f"< {pause_at}% — auto-pausing"
                )
                self._pause(
                    session, "disk", "FAULT",
                    f"Auto-paused — export share critically low ({free:.1f}% free, "
                    f"{nas['free_gb']:.0f} GiB)",
                )
                if self._notify_enabled("notify_disk_space"):
                    self.facade.send_alert(
                        key=f"habitat_autopause_{name}",
                        title=f"Habitat Session auto-paused — {name}",
                        message=(
                            f"**{name}** was auto-paused: the export share is only "
                            f"**{free:.1f}%** free ({nas['free_gb']:.0f} GiB). "
                            f"Free space above {resume_at}% and it resumes "
                            f"automatically."
                        ),
                        severity="error",
                    )
            elif (session.state == SessionState.PAUSED
                  and session.pause_reason == "disk" and free >= resume_at):
                self.logger.info(
                    f"Habitat Session '{name}': share recovered to {free:.1f}% "
                    f">= {resume_at}% — auto-resuming"
                )
                self._resume(
                    session, "RESUME",
                    f"Auto-resumed — share recovered ({free:.1f}% free)",
                )
                if self._notify_enabled("notify_disk_space"):
                    self.facade.send_alert(
                        key=f"habitat_autoresume_{name}",
                        title=f"Habitat Session resumed — {name}",
                        message=(
                            f"**{name}** auto-resumed: the export share is back to "
                            f"{free:.1f}% free ({nas['free_gb']:.0f} GiB)."
                        ),
                        severity="info",
                    )

    def _evaluate_plans(self, session_name: str, session: RecordingSession,
                        now: datetime | None = None) -> None:
        """One monitor pass over a plan-driven session: start/stop each plan's
        modules per its window schedule.

        Runs while ACTIVE, and also while an *unattended* session sits in ERROR
        (a habitat-scale controller restart can drop one there via a module
        fault) — the whole point of the unattended posture is that the monitor
        keeps healing rather than parking for an absent operator. A run that
        finds every plan in the state its schedule wants clears the fault and
        returns the session to ACTIVE.
        """
        if session.state == SessionState.PAUSED:
            return
        recovering = (
            session.state == SessionState.ERROR and session.unattended
        )
        if session.state != SessionState.ACTIVE and not recovering:
            return
        now = now or datetime.now()
        for plan in session.plans:
            should = recording_plans.plan_should_record(plan, now)
            if should and not plan.recording:
                if recording_plans.plan_window_action(plan, now) == "start":
                    self._start_plan(session, plan, now)
            elif plan.recording and not should:
                self._stop_plan(session, plan, "window closed")
            elif not should and any(
                self.facade.is_module_recording(m) for m in plan.modules
            ):
                # Modules recording while the plan's window is shut — e.g. a
                # controller-restart recovery re-armed them blindly. _stop_plan
                # checks each module's real state, so a stale plan.recording
                # flag doesn't matter here.
                self._stop_plan(session, plan, "outside window")
            elif plan.recording:
                self._rearm_plan_dropouts(session, plan)

        if recovering and self._plans_all_settled(session, now):
            reason = session.error_message or "faulted modules"
            session.error_message = ""
            session.error_time = None
            session.state = SessionState.ACTIVE
            self._log_session_event(
                session_name, "RECOVERY",
                f"Recovered — {reason}; all plans back on schedule",
            )
            self.facade.update_sessions(self.sessions)
            self._save_sessions()

    def _plans_all_settled(self, session: RecordingSession,
                           now: datetime) -> bool:
        """True when every plan matches its schedule: in-window plans have all
        their modules recording, out-of-window plans have none."""
        for plan in session.plans:
            should = recording_plans.plan_should_record(plan, now)
            for m in plan.modules:
                if self.facade.is_module_recording(m) != should:
                    return False
        return True

    def _rearm_plan_dropouts(self, session: RecordingSession,
                             plan: recording_plans.RecordingPlan) -> None:
        """Re-issue start_recording to any module of a recording plan that
        isn't actually recording (self-heal, matching the unattended
        posture). Throttled per module via _health_probe_times."""
        now_ts = time.time()
        for m in plan.modules:
            if self.facade.is_module_recording(m):
                self._not_recording_strikes.pop((session.session_name, m), None)
                continue
            key = (session.session_name, m)
            self._not_recording_strikes[key] = (
                self._not_recording_strikes.get(key, 0) + 1
            )
            if self._not_recording_strikes[key] < self._NOT_RECORDING_STRIKES_THRESHOLD:
                continue
            if now_ts - self._health_probe_times.get(m, 0) < 60:
                continue
            self._health_probe_times[m] = now_ts
            self.facade.send_command(m, "start_recording", {
                "duration": 0, "session_name": session.session_name,
            })
            self._record_unattended_fault(session.session_name, [m])
            self._log_session_event(
                session.session_name, "FAULT",
                f"{m} not recording (plan '{plan.label}') — re-armed",
            )

    def _start_plan(self, session: RecordingSession,
                    plan: recording_plans.RecordingPlan,
                    now: datetime | None = None) -> None:
        start_at = time.time() + LEAD_SECS
        params = {"duration": 0, "session_name": session.session_name,
                  "start_at": start_at}
        if plan.segment_minutes:
            params["segment_minutes"] = plan.segment_minutes
        for m in plan.modules:
            session.module_stop_states[m] = "recording"
            session.module_export_states.setdefault(m, "idle")
            # A module that kept recording through a controller restart is
            # already going — re-sending start_recording just earns an
            # "Already recording" fault. Mark it tracked and move on.
            if self.facade.is_module_recording(m):
                continue
            self.facade.send_command(m, "start_recording", params)
            self.facade.send_command(
                m, "report_recording_state", {"session_name": session.session_name}
            )
        plan.recording = True
        plan.last_start_date = recording_plans.current_anchor(
            plan, now or datetime.now()
        )
        self._log_session_event(
            session.session_name, "INFO",
            f"Plan '{plan.label}' recording — {', '.join(plan.modules)}",
        )

    def _stop_plan(self, session: RecordingSession,
                   plan: recording_plans.RecordingPlan, reason: str) -> None:
        for m in plan.modules:
            if self.facade.is_module_recording(m):
                session.module_stop_states[m] = "stopping"
                self.facade.send_command(m, "stop_recording", {})
            else:
                session.module_stop_states[m] = "stopped"
        plan.recording = False
        self._log_session_event(
            session.session_name, "INFO",
            f"Plan '{plan.label}' stopped ({reason}) — {', '.join(plan.modules)}",
        )

    def estimate_habitat_volume(self, plans_raw: list,
                                expected_minutes: float) -> dict:
        """Projected data volume for a Habitat Session vs. free space, for
        the new-session form to show before the session is created."""
        try:
            plans = recording_plans.parse_plans(plans_raw)
        except recording_plans.PlanError as exc:
            return {"success": False, "error": str(exc)}
        cfg = self.facade.get_config()
        modules_info = self.facade.get_modules()
        rates: dict[str, float] = {}
        for m in (mm for p in plans for mm in p.modules):
            mtype = (modules_info.get(m) or {}).get("type", "")
            bps, _ = estimate_recording_bytes_per_s(mtype, cfg)
            rates[m] = bps or 0.0
        est = recording_plans.estimate_campaign_volume(
            plans, rates, expected_minutes
        )
        est["success"] = True
        try:
            free = shutil.disk_usage(self._get_share_root()).free
            est["share_free_bytes"] = free
            est["fits"] = est["projected_bytes_total"] <= free
        except OSError:
            est["share_free_bytes"] = None
        return est


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
                session.error_time = None
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


    def module_export_update(self, module_id: str, export_path: str, state: str,
                              final: bool = True) -> None:
        """Update export state for a module.

        The session is identified from the first path component of export_path,
        which is always the session_name (e.g. 'myexp-20260312/20260312/camera_d61e').

        `pending_exports` counts export_ready signals not yet resolved, so it
        goes up on "pending" and back down on "complete" or a *final* "failed"
        (retries exhausted — see export_queue.py). A "failed" that will still
        be retried leaves it outstanding. This is what lets the operator know
        with certainty whether every file a stopped session produced has
        actually landed on the share, rather than just that recording stopped.
        """
        session_name = export_path.split('/', maxsplit=1)[0] if export_path else None
        if not session_name or session_name not in self.sessions:
            return

        with self._lock:
            session = self.sessions[session_name]
            session.module_export_states[module_id] = state
            if state == "pending":
                session.pending_exports += 1
            elif state == "complete":
                session.pending_exports = max(0, session.pending_exports - 1)
                session.total_exports_complete += 1
                self._last_export_success[module_id] = time.time()
                self._export_failure_streak[module_id] = 0
            elif state == "failed":
                session.total_exports_failed += 1
                streak = self._export_failure_streak.get(module_id, 0) + 1
                self._export_failure_streak[module_id] = streak
                self._log_session_event(session_name, "WARNING",
                    f"Export failed for {module_id} — path: {export_path}"
                    + (f" ({streak} consecutive failures)" if streak > 1 else "")
                    + ("" if final else " — will retry"))
                if final:
                    session.pending_exports = max(0, session.pending_exports - 1)

            if (session.state == SessionState.STOPPED and session.pending_exports == 0
                    and session.export_stall_alerted):
                # Recovered after a stall alert — record the all-clear.
                self._log_session_event(session_name, "RECOVERY",
                    "All pending exports for this stopped session now confirmed")
                session.export_stall_alerted = False

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Export state for {module_id} in '{session_name}': {state}")


    def retry_failed_exports(self, session_name: str) -> dict:
        """Manually re-trigger export for every module in a session whose
        automatic retries (export_queue.py's MAX_RETRIES) have been
        exhausted. Once that cap is hit, nothing retries on its own again,
        even once whatever caused the failures (e.g. bad Samba credentials)
        is fixed. Confirmed live: a session accumulated several failed/
        pending exports after a credential-wipe bug, and nothing
        re-attempted them even after the credentials were corrected.

        Re-sends start_export for each affected module -- export.py's
        export_staged() sweeps everything currently sitting in that
        module's local to_export/ folder regardless of the specific
        export_path passed, so this recovers any stuck files for that
        module, not just ones matching this exact path.

        Routed through facade.enqueue_export() (the same entry point every
        real export_ready signal uses) rather than sending start_export
        directly. Sending it directly used to let a retry race a
        concurrently-dispatched real export to the same module -- two
        overlapping start_export threads on the module racing
        export.py's own concurrency guard, with the loser reporting a
        spurious export_failed for a call that never actually attempted
        anything. Confirmed live: clicking Retry Export made the failed
        count go up, not down, even though the files genuinely exported.
        Routing through enqueue_export() means a retry for a module
        already active/queued in export_queue.py is simply dropped as a
        duplicate, exactly like any other export_ready signal.
        """
        if session_name not in self.sessions:
            return {"result": "error", "error": "Session not found"}

        session = self.sessions[session_name]
        failed_modules = [
            m for m in session.modules
            if session.module_export_states.get(m) == "failed"
        ]
        if not failed_modules:
            return {"result": "error", "error": "No failed exports to retry"}

        date = (session.start_time or "")[:8]
        for module_id in failed_modules:
            export_path = f"{session_name}/{date}/{module_id}"
            self.facade.enqueue_export(module_id, export_path)

        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self._log_session_event(
            session_name, "RECOVERY", f"Export manually retried for {', '.join(failed_modules)}"
        )
        self.logger.info(f"Manually retried export for session '{session_name}': {failed_modules}")
        return {"result": "success"}


    def request_recording_state_refresh(self, session_name: str) -> dict:
        """On-demand refresh of every member module's local recording-pipeline
        summary (pending/to_export/exported) for a session, rather than
        waiting for the next periodic poll in _monitor_sessions(). The
        module_recording_state_update broadcasts arrive independently as
        each module's cmd_ack comes back -- fire-and-forget here, same as
        every other send_command call."""
        if session_name not in self.sessions:
            return {"result": "error", "error": "Session not found"}
        session = self.sessions[session_name]
        for module_id in session.modules:
            self.facade.send_command(
                module_id, "report_recording_state", {"session_name": session_name}
            )
        return {"result": "success"}


    # -----------------------------------------------------------------------
    # Getters
    # -----------------------------------------------------------------------

    def get_recording_status(self) -> bool:
        return any(s.state == SessionState.ACTIVE for s in self.sessions.values())

    def get_recording_sessions(self) -> dict:
        return self.sessions

    def get_active_recording_sessions(self) -> dict:
        return {k: v for k, v in self.sessions.items() if v.state == SessionState.ACTIVE}

    def get_session_name_from_target(self, target: str) -> str | None:
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
                session.error_time = None
                session.state = SessionState.ACTIVE

            session.modules.append(module_id)
            session.module_stop_states[module_id] = "recording"
            session.module_export_states[module_id] = "idle"

        params = {"duration": 0, "session_name": session_name}
        self.facade.send_command(module_id, "start_recording", params)
        self.facade.send_command(
            module_id, "report_recording_state", {"session_name": session_name}
        )
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

        # Only a session that's actually recording (or already faulted) can be
        # broken by a module dropping offline. get_session_name_from_target()
        # deliberately returns any non-stopped session -- including PENDING
        # and SCHEDULED ones that haven't started yet -- for other callers
        # (add_module_to_session, module_back_online) that need those states
        # too. Without this check, a module merely listed on a dormant
        # SCHEDULED session going offline hours before its window even opens
        # got flagged as a session fault; module_back_online() then "recovered"
        # it by starting a real recording outside the schedule (found live
        # 2026-08-27, habitat DailyAudio session — see CHANGELOG).
        if session.state in (SessionState.ACTIVE, SessionState.ERROR):
            # Habitat Session: a module whose plan window is currently shut is
            # supposed to be idle — its dropping offline isn't a session fault.
            if session.plans:
                plan = next(
                    (p for p in session.plans if module_id in p.modules), None
                )
                if plan and not recording_plans.plan_should_record(
                    plan, datetime.now()
                ):
                    with self._lock:
                        session.module_stop_states[module_id] = "stopped"
                    self._log_session_event(
                        session_name, "INFO",
                        f"{module_id} went offline while plan '{plan.label}' "
                        f"is outside its window — not a fault",
                    )
                    self.facade.update_sessions(self.sessions)
                    self._save_sessions()
                    return

            session.error_message = f"{module_id} is offline"
            if session.state != SessionState.ERROR:
                session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
            # Unattended sessions self-heal rather than parking in ERROR — see
            # report_module_fault / _check_session_recording_liveness.
            if not session.unattended:
                session.state = SessionState.ERROR
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            note = "recorded (unattended)" if session.unattended else "→ ERROR"
            self.logger.info(f"Session '{session_name}' {note}: {module_id} offline")
            self._log_session_event(session_name, "FAULT", f"{module_id} went offline")
            if self._notify_enabled("notify_module_offline"):
                if session.unattended:
                    self._record_unattended_fault(session_name, [module_id])
                else:
                    self.facade.send_alert(
                        key=f"module_offline_{module_id}",
                        title=f"Module offline — {module_id}",
                        message=(
                            f"Module **{module_id}** went offline during "
                            f"recording session **{session_name}**."
                        ),
                    )


    def report_module_fault(self, module_id: str, message: str) -> None:
        """Record a module-reported recording failure (e.g. recording_start_failed /
        recording_stop_failed) against whatever session it belongs to.

        Unlike the periodic "not recording" strikes check in _monitor_sessions
        (which needs several missed polls before it notices), this reacts the
        moment the module itself says something went wrong — closing the gap
        where a module-side failure was previously silently dropped rather
        than surfaced via the existing session-fault / Teams-alert /
        FaultAlertModal pipeline.
        """
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            self.logger.warning(f"Module {module_id} reported a fault with no active session: {message}")
            return
        session = self.sessions[session_name]
        if session.state == SessionState.STOPPED:
            return

        # "Already recording" is not a fault. A module that kept recording
        # through a controller restart replies this to the recovery-path
        # start_recording — it's doing exactly what we want. Escalating the
        # whole session to ERROR on it (16× at once, after a habitat-scale
        # restart) is what previously wedged an unattended Habitat Session:
        # ERROR disables _evaluate_plans' self-heal, so out-of-window plan
        # modules the old recovery path blindly started never got stopped
        # (found live 2026-09-03, session habitat_CRLLT3_20260903).
        if "already recording" in message.lower():
            self.logger.info(
                f"{module_id} reported '{message}' in '{session_name}' — "
                f"already going, no fault"
            )
            return

        session.error_message = f"{module_id}: {message}"
        if session.state != SessionState.ERROR:
            session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        # An unattended (long-term) session never parks terminally in ERROR --
        # it stays ACTIVE / PAUSED so the monitor loop's self-heal keeps
        # running. The fault is still recorded (badge + event log) and the
        # per-event alert folds into the daily digest. Matches the posture in
        # _check_session_recording_liveness.
        if not session.unattended:
            session.state = SessionState.ERROR
        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        note = "recorded (unattended, active)" if session.unattended else "→ ERROR"
        self.logger.info(
            f"Session '{session_name}' {note}: {module_id} reported fault: {message}"
        )
        self._log_session_event(session_name, "FAULT", f"{module_id}: {message}")
        if self._notify_enabled("notify_session_faults"):
            if session.unattended:
                self._record_unattended_fault(session_name, [module_id])
            else:
                self.facade.send_alert(
                    key=f"module_fault_{module_id}",
                    title=f"Recording error — {session_name}",
                    message=f"Module **{module_id}** reported a recording fault in session **{session_name}**: {message}",
                )


    def handle_recording_health_status(self, module_id: str, status: str, message: str | None) -> None:
        """Module self-reported recording-capture liveness (see
        Module._check_recording_alive / Recording._monitor_recording_health
        on the module side) -- a soft warning, same severity tier as
        _check_ptp_mid_recording's ptp_warning below: surfaces on the
        session card and optionally alerts, but doesn't stop the session or
        other modules, since a self-monitor's detection could itself be a
        transient false positive (e.g. a brief USB hiccup that recovers).

        Unlike _check_ptp_mid_recording, this isn't polled from the monitor
        loop against heartbeat data -- it reacts directly to the module's
        own "unhealthy"/"recovered" status push.
        """
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            return
        session = self.sessions[session_name]
        if session.state == SessionState.STOPPED:
            return

        degraded = self._recording_health_degraded.setdefault(session_name, set())

        if status == "unhealthy":
            if module_id in degraded:
                return  # already warned for this module — avoid duplicate alerts
            degraded.add(module_id)
            detail = f"{module_id}: {message}" if message else module_id
            warning = f"Recording health warning — {detail}"
            self.logger.warning(f"Session '{session_name}': {warning}")
            with self._lock:
                session.recording_health_warning = warning
            self._log_session_event(session_name, "WARNING", warning)
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            if self._notify_enabled("notify_recording_health"):
                self.facade.send_alert(
                    key=f"recording_health_{module_id}",
                    title=f"Recording health warning — {session_name}",
                    message=warning,
                    severity="warning",
                )
        elif status == "recovered":
            degraded.discard(module_id)
            if degraded:
                return  # still warned for other module(s) in this session
            self.logger.info(f"Session '{session_name}': recording health recovered ({module_id})")
            with self._lock:
                session.recording_health_warning = None
            self._log_session_event(session_name, "RECOVERY",
                f"Recording health recovered — {module_id}")
            self.facade.update_sessions(self.sessions)
            self._save_sessions()


    def module_back_online(self, module_id: str) -> None:
        """Resume recording for a module that reconnected during an active session."""
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            return
        session = self.sessions[session_name]

        if session.state in (SessionState.ACTIVE, SessionState.ERROR):
            # Habitat Session: only re-arm a reconnecting module if its plan's
            # window is actually open right now. Otherwise a controller
            # restart (which faults every session module) would start e.g. a
            # nightly-audio plan's mics in the middle of the afternoon.
            if session.plans:
                plan = next(
                    (p for p in session.plans if module_id in p.modules), None
                )
                if plan and not recording_plans.plan_should_record(
                    plan, datetime.now()
                ):
                    with self._lock:
                        session.module_stop_states[module_id] = "stopped"
                    self._log_session_event(
                        session_name, "INFO",
                        f"{module_id} back online — plan '{plan.label}' is "
                        f"outside its window, not started",
                    )
                    self.facade.update_sessions(self.sessions)
                    self._save_sessions()
                    return

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
            self.facade.send_command(
                module_id, "report_recording_state", {"session_name": session_name}
            )
            with self._lock:
                session.module_stop_states[module_id] = "recording"
                if session.state == SessionState.ERROR:
                    session.error_message = ""
                    session.error_time = None
                    session.state = SessionState.ACTIVE
            self.facade.update_sessions(self.sessions)
            self._save_sessions()
            self.logger.info(
                f"Module {module_id} back online — restarted recording in '{session_name}'"
            )
            self._log_session_event(session_name, "RECOVERY",
                f"{module_id} came back online — recording resumed")
            if self._notify_enabled("notify_module_online", default=False):
                self.facade.send_alert(
                    key=f"module_online_{module_id}",
                    title=f"Module back online — {module_id}",
                    message=f"Module **{module_id}** reconnected and resumed recording in session **{session_name}**.",
                    severity="info",
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

        # A Habitat Session stops individual plans' modules as windows close
        # (or on pause) without the whole session ending; only a real
        # stop_session() (which registers the name here) transitions it.
        if session.plans and session_name not in self._full_stopping:
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
                session.stopped_epoch = time.time()
                new_state = SessionState.STOPPED

        self._full_stopping.discard(session_name)
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

        if new_state == SessionState.STOPPED and self._notify_enabled("notify_recording_stopped", default=False):
            self.facade.send_alert(
                key=f"session_stopped_{session_name}",
                title=f"Recording stopped — {session_name}",
                message=f"Session **{session_name}** ended. Start: {session.start_time or '—'}, End: {session.end_time or '—'}.",
                severity="info",
            )

        if new_state == SessionState.STOPPED and session_name in self._unattended_fault_digest:
            self._flush_unattended_digest(force=True)

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
            if self._notify_enabled("notify_session_faults"):
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
            if self._notify_enabled("notify_session_faults"):
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
        if low_disk and self._notify_enabled("notify_disk_space"):
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
        elif nas["free_pct"] < nas_min:
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
            if self._notify_enabled("notify_session_faults"):
                self.facade.send_alert(
                    key=f"nas_full_{session_name}_{today}",
                    title="Scheduled recording blocked — NAS nearly full",
                    message=f"Session **{session_name}** could not start its {today} run.\n\n{err}",
                    severity="error",
                )
            return
        elif nas["free_pct"] < nas_warn:
            if self._notify_enabled("notify_disk_space"):
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
            if self._notify_enabled("notify_session_faults"):
                self.facade.send_alert(
                    key=f"ptp_fail_{session_name}_{today}",
                    title="Scheduled recording blocked — PTP not synchronised",
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
            self.facade.send_command(
                module_id, "report_recording_state", {"session_name": session_name}
            )
        self.facade.update_sessions(self.sessions)
        self._save_sessions()
        self.logger.info(f"Scheduled session '{session_name}' started for {today}")
        self._log_session_event(session_name, "INFO",
            f"Scheduled recording started — run for {today}, modules: {', '.join(session.modules)}")
        if self._notify_enabled("notify_recording_started"):
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
            if self._notify_enabled("notify_ptp_degraded"):
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
            if self._notify_enabled("notify_disk_space"):
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
            if self._notify_enabled("notify_disk_space"):
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
                    if self._notify_enabled("notify_session_faults"):
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


    def _check_export_stall_after_stop(self) -> None:
        """Alert when a STOPPED session still has unresolved exports well after
        stopping — recording having stopped says nothing about whether every
        file actually made it to the share; this closes that gap."""
        config = self.facade.get_config()
        stall_mins = config.get("recording", {}).get("export_stall_after_stop_mins", 15)
        stall_secs = stall_mins * 60
        now = time.time()

        for session_name, session in list(self.sessions.items()):
            if session.state != SessionState.STOPPED or session.pending_exports <= 0:
                continue
            if session.export_stall_alerted:
                continue
            if session.stopped_epoch is None or now - session.stopped_epoch < stall_secs:
                continue

            age_mins = int((now - session.stopped_epoch) / 60)
            self._log_session_event(session_name, "WARNING",
                f"Session stopped {age_mins} min ago with "
                f"{session.pending_exports} export(s) still unresolved")
            if self._notify_enabled("notify_session_faults"):
                self.facade.send_alert(
                    key=f"export_stall_stopped_{session_name}",
                    title=f"Export not confirmed — {session_name}",
                    message=(
                        f"Session **{session_name}** stopped {age_mins} min ago but "
                        f"{session.pending_exports} export(s) are still not confirmed on "
                        f"the controller share. Check module connectivity and the export queue."
                    ),
                    severity="warning",
                )
            with self._lock:
                session.export_stall_alerted = True
            self.facade.update_sessions(self.sessions)
            self._save_sessions()


    # ------------------------------------------------------------------ #
    # Sync-quality validation (framesync_check.SyncCheckWorker)          #
    # ------------------------------------------------------------------ #

    _FRAMESYNC_ROLLUP_DAYS = 7

    def apply_framesync_verdict(self, session_name: str, scope: str,
                                date_dir: str | None, verdict: dict,
                                report_rel: str | None) -> None:
        """Store a sync-quality verdict on a session. Called by the framesync
        worker via the facade once a check (or a manual re-check) finishes.
        `verdict` is already slimmed (framesync_check.slim)."""
        v = dict(verdict)
        v["report_rel"] = report_rel
        with self._lock:
            session = self.sessions.get(session_name)
            if session is None:
                self._framesync_inflight.discard(
                    (session_name, date_dir or "__session__"))
                return
            if scope == "day" and date_dir:
                session.day_verdicts = {**session.day_verdicts, date_dir: v}
                session.framesync_verdict = self._rollup_day_verdicts(
                    session.day_verdicts)
            else:
                session.framesync_verdict = v
            self._framesync_inflight.discard(
                (session_name, date_dir or "__session__"))

        self._log_session_event(
            session_name, "INFO",
            f"Sync-quality validation ({scope}"
            f"{' ' + date_dir if date_dir else ''}): {v.get('status')}")
        self._save_sessions()
        self.facade.update_sessions(self.sessions)

    @classmethod
    def _rollup_day_verdicts(cls, day_verdicts: dict) -> dict:
        """Worst-of the most recent N completed days (green < amber < red;
        skipped / error ignored unless every day is one). Carries the
        green/total-day tally and the latest day so the UI can show both a
        badge colour and progress on a weeks-long Habitat Session."""
        order = {"green": 0, "amber": 1, "red": 2}
        days = sorted(day_verdicts)
        recent = days[-cls._FRAMESYNC_ROLLUP_DAYS:]
        recent_status = [day_verdicts[d].get("status") for d in recent]
        real = [s for s in recent_status if s in order]
        if real:
            status = max(real, key=lambda s: order[s])
        elif "error" in recent_status:
            status = "error"
        else:
            status = "skipped"
        return {
            "schema": 1,
            "status": status,
            "scope": "session",
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "days": {d: day_verdicts[d].get("status") for d in days},
            "green_days": sum(1 for d in days
                              if day_verdicts[d].get("status") == "green"),
            "total_days": len(days),
            "latest": {
                "date_dir": days[-1],
                "status": day_verdicts[days[-1]].get("status"),
            } if days else None,
        }


    def _poll_recording_state(self) -> None:
        """Ask every module in every non-STOPPED session to report its local
        recording-pipeline summary (pending/to_export/exported). Fire-and-
        forget, same as the other periodic checks in this block -- each
        module's cmd_ack arrives independently and is pushed to the frontend
        via web.broadcast_recording_state_update() (see controller.py's
        'report_recording_state' cmd_ack branch). This is what gives the
        Recordings page live visibility into a session that's still running,
        rather than only after it stops."""
        for session_name, session in list(self.sessions.items()):
            # PENDING has no dispatched recording yet -- nothing for a
            # module to report for this session until start_pending_session()
            # actually begins it.
            if session.state in (SessionState.STOPPED, SessionState.PENDING):
                continue
            for module_id in session.modules:
                try:
                    self.facade.send_command(
                        module_id, "report_recording_state", {"session_name": session_name}
                    )
                except Exception as e:
                    self.logger.warning(f"Could not poll recording state for {module_id}: {e}")


    def _check_session_gaps(self, today: str) -> None:
        """Alert if a scheduled session missed its previous run.

        Runs once per calendar day, immediately after midnight.  A 'gap' is
        detected when the session should have run yesterday but its
        scheduled_last_start_date is not yesterday.
        """
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
                if self._notify_enabled("notify_session_faults"):
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


    @staticmethod
    def _scheduled_session_action(session: "RecordingSession", today: str, yesterday: str,
                                   current_time: str, today_weekday: int) -> str | None:
        """Decide whether a scheduled session's monitor tick should start or stop it.

        end < start means the window spans midnight (e.g. 22:00-06:00): the stop
        check then looks for a session started yesterday whose end time has been
        reached before today's start time, rather than a same-day match — a plain
        "HH:MM" string comparison would otherwise stop the session seconds after
        it starts.
        """
        days_match = (
            not session.scheduled_days
            or today_weekday in session.scheduled_days
        )
        crosses_midnight = (
            session.scheduled_start_time is not None
            and session.scheduled_end_time is not None
            and session.scheduled_end_time < session.scheduled_start_time
        )

        if (session.state != SessionState.ACTIVE
                and session.scheduled_last_start_date != today
                and days_match
                and current_time >= session.scheduled_start_time):
            return "start"

        if session.state == SessionState.ACTIVE:
            if not crosses_midnight:
                if (session.scheduled_last_start_date == today
                        and current_time >= session.scheduled_end_time):
                    return "stop"
            elif (session.scheduled_last_start_date == yesterday
                    and current_time >= session.scheduled_end_time
                    and current_time < session.scheduled_start_time):
                return "stop"

        return None

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
                self._check_export_stall_after_stop()
                self._poll_recording_state()
                self._flush_unattended_digest()

            # Habitat Session disk auto-pause / auto-resume runs more often
            # than the 5-min alert cadence — a critically full share should
            # stop write pressure within ~30 s, not minutes.
            if self._monitor_cycle % 6 == 0:
                try:
                    self._check_habitat_disk_autopause()
                except Exception as e:
                    self.logger.exception(f"Habitat disk auto-pause check failed: {e}")

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
                        yesterday = (date.today() - timedelta(days=1)).isoformat()
                        action = self._scheduled_session_action(
                            session, today, yesterday, current_time, today_weekday
                        )
                        if action == "start":
                            self._start_scheduled_session(session_name, today)
                        elif action == "stop":
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

                        # Habitat Session: drive each plan's own window schedule
                        # (start/stop as windows open and close, re-arm a
                        # recording plan's dropped modules). The uniform
                        # module-by-module liveness check below assumes every
                        # session module should be recording, which isn't true
                        # here, so it's skipped for plan-driven sessions.
                        if session.plans:
                            self._evaluate_plans(session_name, session)
                            if session.state == SessionState.ACTIVE:
                                self._check_ptp_mid_recording(session_name, session)
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

                        self._check_session_recording_liveness(session_name, session)

                        if session.state == SessionState.ACTIVE:
                            self._check_ptp_mid_recording(session_name, session)

                except Exception as e:
                    self.logger.exception(f"Error monitoring session '{session_name}': {e}")


    # Consecutive monitor cycles a module can be seen "not recording" before it
    # counts as a fault — one miss is normal during a segment transition.
    _NOT_RECORDING_STRIKES_THRESHOLD = 2

    def _check_session_recording_liveness(self, session_name: str, session: "RecordingSession") -> None:
        """One monitor pass over an ACTIVE/ERROR session: confirm every module
        that should be recording actually is, fault (or, for an unattended
        session, just record + self-heal) if not, and recover when they're back.

        Extracted from _monitor_sessions' loop body so it can be exercised
        directly in tests."""
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
                if strikes >= self._NOT_RECORDING_STRIKES_THRESHOLD:
                    not_recording.append(m)
            else:
                self._not_recording_strikes.pop(key, None)

        if not_recording:
            msg = f"Not recording: {', '.join(not_recording)}"
            # An unattended (long-term) session never parks terminally in
            # ERROR -- it stays ACTIVE and lets the recovery below keep
            # re-issuing start_recording. The fault is still recorded
            # (error_message/error_time for the UI badge, session_events.log)
            # but the per-event alert is folded into a daily digest.
            wants_error_state = not session.unattended
            faulted_now = (
                session.error_message != msg
                or (wants_error_state and session.state != SessionState.ERROR)
            )
            if faulted_now:
                session.error_message = msg
                if not session.error_time:
                    session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                if wants_error_state:
                    session.state = SessionState.ERROR
                self.facade.update_sessions(self.sessions)
                self._log_session_event(session_name, "FAULT", msg)
                if self._notify_enabled("notify_session_faults"):
                    if session.unattended:
                        self._record_unattended_fault(session_name, not_recording)
                    else:
                        self.facade.send_alert(
                            key=f"session_error_{session_name}",
                            title=f"Recording error — {session_name}",
                            message=(
                                f"Session **{session_name}** has entered an error state. "
                                f"The following modules are not recording: {', '.join(not_recording)}."
                            ),
                        )
            # Actively attempt recovery, not just flag the fault -- this is the
            # only place that notices a module lost its recording state without
            # ever being marked offline (e.g. a service restart fast enough to
            # stay under health.py's suspicion_timeout, so the offline->online
            # edge module_back_online() normally hangs off never fires). It has
            # the right guards (session ACTIVE/ERROR, skips a redundant resend
            # if already recording) and retries harmlessly next cycle.
            for m in not_recording:
                self.module_back_online(m)
        elif should_be_recording and (
            session.state == SessionState.ERROR
            or (session.unattended and session.error_message)
        ):
            # Every module we were checking is now recording — recover. If
            # should_be_recording is empty (e.g. all "unknown" after a restart)
            # we can't confirm recovery, so leave the fault as-is. For an
            # unattended session the state is still ACTIVE here — we're just
            # clearing the fault record.
            reason = session.error_message or "faulted modules"
            self._log_session_event(
                session_name, "RECOVERY", f"Recovered — {reason} now recording"
            )
            session.error_message = ""
            session.error_time = None
            if session.state == SessionState.ERROR:
                session.state = SessionState.ACTIVE
            for m in session.modules:
                self._not_recording_strikes.pop((session_name, m), None)
            self.facade.update_sessions(self.sessions)


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
            self.logger.exception(f"Failed to save sessions: {e}")


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
                    session.error_time = datetime.now().strftime("%Y%m%d-%H%M%S")
                    session.error_message = "Controller restarted during active session"
                    if session.plans:
                        # Reconcile each plan against the clock: an in-window
                        # plan's modules are "unknown" (re-probe + re-arm), a
                        # shut plan's modules are "stopped" so the recovery
                        # path leaves them alone until their window opens.
                        now = datetime.now()
                        states: dict = {}
                        for plan in session.plans:
                            should = recording_plans.plan_should_record(plan, now)
                            plan.recording = should
                            for m in plan.modules:
                                states[m] = "unknown" if should else "stopped"
                        session.module_stop_states = states
                    else:
                        session.module_stop_states = {
                            m: "unknown" for m in session.modules
                        }
                    # An unattended (long-term) session stays ACTIVE so the
                    # monitor loop re-arms its modules instead of parking it in
                    # ERROR for an operator who isn't watching.
                    if not session.unattended:
                        session.state = SessionState.ERROR
                    self._log_session_event(name, "FAULT",
                        "Controller restarted during active session — awaiting module reconnect")
                self.sessions[name] = session
            self.logger.info(f"Loaded {len(self.sessions)} session(s) from disk")
        except Exception as e:
            self.logger.exception(f"Failed to load sessions: {e}")


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
