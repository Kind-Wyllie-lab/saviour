"""
Tests for src/controller/recording.py session lifecycle.

Scheduled start/stop time-window logic already has dedicated coverage in
test_recording_schedule.py -- this file covers session creation/deletion,
module lifecycle events (offline/back online/export updates), the stop/
confirm handshake, and the filesystem-touching helpers (share-writable
probe, NAS space, session persistence), all with self.facade mocked and
the background monitor thread suppressed so it never actually runs during
a test.
"""

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import src.controller.recording as recording_module
from src.controller.recording import Recording, RecordingSession, SessionState


def _make_recording(sessions_file: str | None = None, **config_overrides) -> tuple:
    """A Recording instance with the background monitor thread suppressed
    (Thread is patched during __init__ so _monitor_sessions never actually
    starts looping) and a MagicMock facade wired up with defaults that pass
    the PTP gate most lifecycle methods check before acting.

    SESSIONS_FILE is repointed at a fresh temp path per call (unless the
    caller supplies its own, for tests that specifically exercise
    persistence) — it's a module-level constant pointing at a real
    filesystem path (/var/lib/saviour/controller/sessions.json), and without
    this every test in the process reads/writes the *same* file, so one
    test's sessions leak into the next (and, on a real deployment, running
    these tests would read/write the live controller's actual session
    state)."""
    recording_module.SESSIONS_FILE = sessions_file or os.path.join(tempfile.mkdtemp(), "sessions.json")
    with patch("src.controller.recording.threading.Thread"):
        rec = Recording()
    facade = MagicMock()
    facade.get_config.return_value = config_overrides or {}
    facade.get_module_health.return_value = {
        "status": "online", "last_heartbeat": time.time(), "ptp4l_offset_ns": 1000,
    }
    facade.is_module_recording.return_value = True
    rec.facade = facade
    rec._check_share_writable = lambda: None  # bypass the real /home/pi/... probe
    return rec, facade


def _session(**overrides) -> RecordingSession:
    defaults = dict(session_name="exp1", target="camera", state=SessionState.ACTIVE)
    defaults.update(overrides)
    return RecordingSession(**defaults)


# ---------------------------------------------------------------------------
# Tier A: pure logic -- no facade involved
# ---------------------------------------------------------------------------

class TestFormatSessionName:
    def test_appends_target_and_timestamp(self):
        rec, _facade = _make_recording()
        name = rec._format_session_name("my exp", "camera")
        assert name.startswith("my_exp-camera-")

    def test_all_target_omits_target_segment(self):
        rec, _facade = _make_recording()
        name = rec._format_session_name("my exp", "all")
        assert name.startswith("my_exp-")
        assert "-all-" not in name

    def test_strips_unsafe_characters(self):
        rec, _facade = _make_recording()
        name = rec._format_session_name("weird!!name??", "all")
        assert name.startswith("weirdname-")


class TestBusyModules:
    def test_no_sessions_returns_empty_set(self):
        rec, _facade = _make_recording()
        assert rec._busy_modules() == set()

    def test_only_active_sessions_claim_their_modules(self):
        rec, _facade = _make_recording()
        rec.sessions["active1"] = _session(
            session_name="active1", state=SessionState.ACTIVE, modules=["cam1"]
        )
        rec.sessions["sched1"] = _session(
            session_name="sched1", state=SessionState.SCHEDULED, modules=["cam2"]
        )
        rec.sessions["stopped1"] = _session(
            session_name="stopped1", state=SessionState.STOPPED, modules=["cam3"]
        )
        assert rec._busy_modules() == {"cam1"}


class TestGetSessionNameFromTarget:
    def test_no_sessions_returns_none(self):
        rec, _facade = _make_recording()
        assert rec.get_session_name_from_target("all") is None

    def test_all_matches_the_single_non_stopped_session(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.ACTIVE)
        assert rec.get_session_name_from_target("all") == "exp1"

    def test_all_is_ambiguous_with_multiple_non_stopped_sessions(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(session_name="exp1", state=SessionState.ACTIVE)
        rec.sessions["exp2"] = _session(
            session_name="exp2", state=SessionState.SCHEDULED
        )
        assert rec.get_session_name_from_target("all") is None

    def test_matches_session_containing_the_module(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1", "cam2"])
        assert rec.get_session_name_from_target("cam2") == "exp1"

    def test_stopped_sessions_are_ignored(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED, modules=["cam1"])
        assert rec.get_session_name_from_target("cam1") is None


class TestSimpleGetters:
    def test_recording_status_true_when_any_session_active(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.SCHEDULED)
        assert rec.get_recording_status() is False
        rec.sessions["exp2"] = _session(session_name="exp2", state=SessionState.ACTIVE)
        assert rec.get_recording_status() is True

    def test_get_active_recording_sessions_filters_by_state(self):
        rec, _facade = _make_recording()
        rec.sessions["active"] = _session(
            session_name="active", state=SessionState.ACTIVE
        )
        rec.sessions["stopped"] = _session(
            session_name="stopped", state=SessionState.STOPPED
        )
        assert list(rec.get_active_recording_sessions().keys()) == ["active"]


# ---------------------------------------------------------------------------
# Tier B: session lifecycle -- facade mocked
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_rejects_empty_name(self):
        rec, _facade = _make_recording()
        result = rec.create_session("   ", "camera")
        assert result == {"success": False, "error": "Session name cannot be empty"}

    def test_rejects_when_share_not_writable(self):
        rec, _facade = _make_recording()
        rec._check_share_writable = lambda: "disk full"
        result = rec.create_session("exp1", "camera")
        assert result == {"success": False, "error": "disk full"}

    def test_rejects_when_no_modules_online(self):
        rec, facade = _make_recording()
        facade.get_modules_by_target.return_value = {}
        result = rec.create_session("exp1", "camera")
        assert result["success"] is False
        assert "No online modules" in result["error"]

    def test_rejects_when_modules_already_recording(self):
        rec, facade = _make_recording()
        rec.sessions["busy"] = _session(
            session_name="busy", state=SessionState.ACTIVE, modules=["cam1"]
        )
        facade.get_modules_by_target.return_value = {"cam1": {}}
        result = rec.create_session("exp1", "camera")
        assert result["success"] is False
        assert "cam1" in result["error"]

    def test_rejects_when_ptp_not_synced(self):
        rec, facade = _make_recording()
        facade.get_modules_by_target.return_value = {"cam1": {}}
        facade.get_module_health.return_value = {
            "status": "online",
            "last_heartbeat": time.time(),
            "ptp4l_offset_ns": 999_999,
        }
        result = rec.create_session("exp1", "camera")
        assert result["success"] is False
        assert "PTP" in result["error"]

    def test_success_creates_active_session_and_starts_modules(self):
        rec, facade = _make_recording()
        facade.get_modules_by_target.return_value = {"cam1": {}, "cam2": {}}

        result = rec.create_session(
            "exp1", "camera", duration_minutes=30, researcher="alice"
        )

        assert result["success"] is True
        session = rec.sessions[result["session_name"]]
        assert session.state == SessionState.ACTIVE
        assert session.modules == ["cam1", "cam2"]
        assert session.researcher == "alice"
        assert session.duration_minutes == 30
        assert session.module_stop_states == {"cam1": "recording", "cam2": "recording"}

        sent_ids = {c.args[0] for c in facade.send_command.call_args_list}
        assert sent_ids == {"cam1", "cam2"}
        facade.update_sessions.assert_called_once_with(rec.sessions)

    def test_raw_name_bypasses_timestamp_suffix(self):
        rec, facade = _make_recording()
        facade.get_modules_by_target.return_value = {"cam1": {}}
        result = rec.create_session("exact_name", "camera", raw_name=True)
        assert result["session_name"] == "exact_name"


class TestCreateScheduledSession:
    def test_rejects_empty_name(self):
        rec, _facade = _make_recording()
        result = rec.create_scheduled_session("", "camera", "09:00", "17:00")
        assert result["success"] is False

    def test_rejects_missing_times(self):
        rec, _facade = _make_recording()
        result = rec.create_scheduled_session("exp1", "camera", "", "17:00")
        assert result["success"] is False

    def test_succeeds_even_with_no_modules_online_yet(self):
        rec, facade = _make_recording()
        facade.get_modules_by_target.return_value = {}
        result = rec.create_scheduled_session(
            "exp1", "camera", "09:00", "17:00", days=[0, 1]
        )
        assert result["success"] is True
        session = rec.sessions[result["session_name"]]
        assert session.state == SessionState.SCHEDULED
        assert session.scheduled_days == [0, 1]


class TestDeleteSession:
    def test_unknown_session_returns_error(self):
        rec, _facade = _make_recording()
        assert rec.delete_session("ghost") == {"error": "Unknown session 'ghost'"}

    def test_active_session_cannot_be_deleted(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.ACTIVE)
        result = rec.delete_session("exp1")
        assert "error" in result
        assert "exp1" in rec.sessions

    def test_stopped_session_deleted_without_touching_files(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED)
        result = rec.delete_session("exp1", delete_files=False)
        assert result == {"success": True}
        assert "exp1" not in rec.sessions
        facade.update_sessions.assert_called_once_with(rec.sessions)

    def test_deletes_session_files_when_requested(self):
        with tempfile.TemporaryDirectory() as share_root:
            rec, facade = _make_recording()
            facade.get_share_path.return_value = share_root
            session_dir = os.path.join(share_root, "exp1")
            os.makedirs(session_dir)
            with open(os.path.join(session_dir, "data.txt"), "w") as f:
                f.write("x")
            rec.sessions["exp1"] = _session(state=SessionState.STOPPED)

            result = rec.delete_session("exp1", delete_files=True)

            assert result == {"success": True}
            assert not os.path.isdir(session_dir)

    def test_refuses_when_exports_still_pending(self):
        """A session with files that never confirmed landing on the share must
        not be silently deletable — that's the only record an operator has
        that data may be missing."""
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED, pending_exports=2)

        result = rec.delete_session("exp1", delete_files=False)

        assert result["export_warning"] is True
        assert "exp1" in rec.sessions

    def test_refuses_when_exports_permanently_failed(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED, total_exports_failed=1)

        result = rec.delete_session("exp1", delete_files=False)

        assert result["export_warning"] is True
        assert "exp1" in rec.sessions

    def test_force_deletes_despite_unresolved_exports(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED, pending_exports=2)

        result = rec.delete_session("exp1", delete_files=False, force=True)

        assert result == {"success": True}
        assert "exp1" not in rec.sessions


class TestClearEndedSessions:
    def test_removes_stopped_and_error_sessions_only(self):
        rec, _facade = _make_recording()
        rec.sessions["stopped"] = _session(
            session_name="stopped", state=SessionState.STOPPED
        )
        rec.sessions["errored"] = _session(
            session_name="errored", state=SessionState.ERROR
        )
        rec.sessions["active"] = _session(
            session_name="active", state=SessionState.ACTIVE
        )

        result = rec.clear_ended_sessions()

        assert result == {"cleared": 2, "skipped": 0, "skipped_sessions": []}
        assert list(rec.sessions.keys()) == ["active"]

    def test_skips_sessions_with_unresolved_exports(self):
        rec, _facade = _make_recording()
        rec.sessions["clean"] = _session(session_name="clean", state=SessionState.STOPPED)
        rec.sessions["stuck"] = _session(
            session_name="stuck", state=SessionState.STOPPED, pending_exports=1
        )

        result = rec.clear_ended_sessions()

        assert result == {"cleared": 1, "skipped": 1, "skipped_sessions": ["stuck"]}
        assert list(rec.sessions.keys()) == ["stuck"]

    def test_force_clears_sessions_with_unresolved_exports_too(self):
        rec, _facade = _make_recording()
        rec.sessions["stuck"] = _session(
            session_name="stuck", state=SessionState.STOPPED, pending_exports=1
        )

        result = rec.clear_ended_sessions(force=True)

        assert result == {"cleared": 1, "skipped": 0, "skipped_sessions": []}
        assert rec.sessions == {}


class TestStopSession:
    def test_unknown_session_is_a_no_op(self):
        rec, facade = _make_recording()
        rec.stop_session("ghost")
        facade.send_command.assert_not_called()

    def test_already_stopped_is_a_no_op(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED)
        rec.stop_session("exp1")
        facade.send_command.assert_not_called()

    def test_sends_stop_only_to_modules_actually_recording(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1", "cam2"])
        facade.is_module_recording.side_effect = lambda m: m == "cam1"

        rec.stop_session("exp1")

        session = rec.sessions["exp1"]
        assert session.module_stop_states == {"cam1": "stopping", "cam2": "stopped"}
        facade.send_command.assert_called_once_with("cam1", "stop_recording", {})


class TestAddModuleToSession:
    def test_unknown_session_returns_error(self):
        rec, _facade = _make_recording()
        result = rec.add_module_to_session("ghost", "cam1")
        assert result["success"] is False

    def test_inactive_session_rejected(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.SCHEDULED)
        result = rec.add_module_to_session("exp1", "cam1")
        assert result["success"] is False

    def test_module_already_in_session_rejected(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1"])
        result = rec.add_module_to_session("exp1", "cam1")
        assert result["success"] is False

    def test_module_busy_in_another_session_rejected(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(session_name="exp1", modules=["cam1"])
        rec.sessions["exp2"] = _session(session_name="exp2", modules=["cam2"])
        result = rec.add_module_to_session("exp2", "cam1")
        assert result["success"] is False

    def test_success_adds_module_and_sends_start(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1"])
        result = rec.add_module_to_session("exp1", "cam2")

        assert result == {"success": True}
        session = rec.sessions["exp1"]
        assert "cam2" in session.modules
        assert session.module_stop_states["cam2"] == "recording"
        facade.send_command.assert_any_call(
            "cam2", "start_recording", {"duration": 0, "session_name": "exp1"}
        )
        facade.send_command.assert_any_call(
            "cam2", "report_recording_state", {"session_name": "exp1"}
        )
        facade.update_sessions.assert_called_once_with(rec.sessions)

    def test_recovers_error_session_when_broken_module_confirmed_stopped(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.ERROR,
            modules=["cam1"],
            module_stop_states={"cam1": "recording"},
        )
        facade.is_module_recording.return_value = False  # cam1 is the broken module

        result = rec.add_module_to_session("exp1", "cam2")

        assert result == {"success": True}
        session = rec.sessions["exp1"]
        assert session.state == SessionState.ACTIVE
        assert session.module_stop_states["cam1"] == "stopped"


class TestModuleStopped:
    def test_confirms_stop_and_checks_completion(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            modules=["cam1"], module_stop_states={"cam1": "stopping"}
        )

        rec.module_stopped("cam1")

        session = rec.sessions["exp1"]
        assert session.module_stop_states["cam1"] == "stopped"
        assert session.state == SessionState.STOPPED
        facade.update_sessions.assert_called()

    def test_unrelated_module_is_ignored(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            modules=["cam1"], module_stop_states={"cam1": "recording"}
        )
        rec.module_stopped("cam_unrelated")
        facade.update_sessions.assert_not_called()


class TestModuleOffline:
    def test_no_session_for_module_is_a_no_op(self):
        rec, facade = _make_recording()
        rec.module_offline("cam1")
        facade.update_sessions.assert_not_called()

    def test_marks_session_errored(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1"])
        rec.module_offline("cam1")
        session = rec.sessions["exp1"]
        assert session.state == SessionState.ERROR
        assert "cam1" in session.error_message


class TestReportModuleFault:
    def test_no_session_for_module_is_a_no_op(self):
        rec, facade = _make_recording()
        rec.report_module_fault("cam1", "recording_start_failed: Already recording")
        facade.update_sessions.assert_not_called()

    def test_marks_session_errored_with_module_and_message(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1"])
        rec.report_module_fault("cam1", "recording_start_failed: Already recording")
        session = rec.sessions["exp1"]
        assert session.state == SessionState.ERROR
        assert "cam1" in session.error_message
        assert "recording_start_failed" in session.error_message
        facade.update_sessions.assert_called_once()

    def test_stopped_session_is_a_no_op(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(state=SessionState.STOPPED, modules=["cam1"])
        rec.report_module_fault("cam1", "recording_stop_failed: boom")
        facade.update_sessions.assert_not_called()


class TestRetryFailedExports:
    def test_unknown_session_returns_error(self):
        rec, _facade = _make_recording()
        result = rec.retry_failed_exports("nope")
        assert result["result"] == "error"

    def test_no_failed_exports_returns_error(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(
            modules=["cam1"], module_export_states={"cam1": "complete"}
        )
        result = rec.retry_failed_exports("exp1")
        assert result["result"] == "error"

    def test_routes_through_enqueue_export_not_send_command_directly(self):
        """Sending start_export directly (the original implementation) let a
        retry race a concurrently-dispatched real export to the same module,
        producing a spurious export_failed for the loser -- confirmed live,
        see the CLAUDE.md entry for 2026-08-20. Routing through
        facade.enqueue_export() instead means a retry is subject to
        export_queue.py's own active/dedup tracking like any other
        export_ready signal, so it can never double-dispatch."""
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            modules=["cam1", "cam2"],
            start_time="20260820-101500",
            module_export_states={"cam1": "failed", "cam2": "complete"},
        )
        result = rec.retry_failed_exports("exp1")
        assert result["result"] == "success"
        facade.enqueue_export.assert_called_once_with("cam1", "exp1/20260820/cam1")
        facade.send_command.assert_not_called()


class TestPollRecordingState:
    """_poll_recording_state() -- the periodic (5-min) step that asks every
    module in every non-STOPPED session to report its local
    pending/to_export/exported summary, giving the Recordings page live
    visibility into a session that's still running."""

    def test_sends_report_recording_state_to_every_module_in_active_session(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.ACTIVE, modules=["cam1", "cam2"]
        )
        rec._poll_recording_state()
        facade.send_command.assert_any_call(
            "cam1", "report_recording_state", {"session_name": "exp1"}
        )
        facade.send_command.assert_any_call(
            "cam2", "report_recording_state", {"session_name": "exp1"}
        )

    def test_scheduled_session_also_polled(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.SCHEDULED, modules=["cam1"]
        )
        rec._poll_recording_state()
        facade.send_command.assert_any_call(
            "cam1", "report_recording_state", {"session_name": "exp1"}
        )

    def test_stopped_session_not_polled(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.STOPPED, modules=["cam1"]
        )
        rec._poll_recording_state()
        facade.send_command.assert_not_called()

    def test_send_command_exception_for_one_module_does_not_block_others(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.ACTIVE, modules=["cam1", "cam2"]
        )
        facade.send_command.side_effect = [Exception("boom"), None]
        rec._poll_recording_state()  # must not raise
        assert facade.send_command.call_count == 2


class TestRequestRecordingStateRefresh:
    def test_unknown_session_returns_error(self):
        rec, _facade = _make_recording()
        result = rec.request_recording_state_refresh("nope")
        assert result["result"] == "error"

    def test_sends_report_recording_state_to_every_member_module(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(modules=["cam1", "cam2"])
        result = rec.request_recording_state_refresh("exp1")
        assert result["result"] == "success"
        facade.send_command.assert_any_call(
            "cam1", "report_recording_state", {"session_name": "exp1"}
        )
        facade.send_command.assert_any_call(
            "cam2", "report_recording_state", {"session_name": "exp1"}
        )


class TestModuleBackOnline:
    def test_resumes_recording_for_errored_session(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.ERROR,
            modules=["cam1"],
            module_stop_states={"cam1": "unknown"},
        )
        facade.is_module_recording.return_value = False

        rec.module_back_online("cam1")

        session = rec.sessions["exp1"]
        assert session.state == SessionState.ACTIVE
        assert session.module_stop_states["cam1"] == "recording"
        facade.send_command.assert_any_call(
            "cam1", "start_recording", {"duration": 0, "session_name": "exp1"}
        )
        facade.send_command.assert_any_call(
            "cam1", "report_recording_state", {"session_name": "exp1"}
        )

    def test_already_tracking_module_is_a_no_op(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.ACTIVE,
            modules=["cam1"],
            module_stop_states={"cam1": "recording"},
        )
        facade.is_module_recording.return_value = True

        rec.module_back_online("cam1")

        facade.send_command.assert_not_called()


class TestModuleExportUpdate:
    def test_complete_increments_counter(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session()

        rec.module_export_update("cam1", "exp1/20260803/cam1", "complete")

        assert rec.sessions["exp1"].total_exports_complete == 1
        facade.update_sessions.assert_called_once_with(rec.sessions)

    def test_failed_increments_failure_counter(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session()

        rec.module_export_update("cam1", "exp1/20260803/cam1", "failed")

        assert rec.sessions["exp1"].total_exports_failed == 1

    def test_unknown_session_prefix_is_ignored(self):
        rec, facade = _make_recording()
        rec.module_export_update("cam1", "ghost_session/20260803/cam1", "complete")
        facade.update_sessions.assert_not_called()

    def test_pending_increments_and_complete_decrements(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session()

        rec.module_export_update("cam1", "exp1/20260803/cam1", "pending")
        assert rec.sessions["exp1"].pending_exports == 1

        rec.module_export_update("cam1", "exp1/20260803/cam1", "complete")
        assert rec.sessions["exp1"].pending_exports == 0

    def test_final_failure_decrements_pending(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session()

        rec.module_export_update("cam1", "exp1/20260803/cam1", "pending")
        rec.module_export_update("cam1", "exp1/20260803/cam1", "failed", final=True)

        assert rec.sessions["exp1"].pending_exports == 0

    def test_retrying_failure_leaves_pending_outstanding(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session()

        rec.module_export_update("cam1", "exp1/20260803/cam1", "pending")
        rec.module_export_update("cam1", "exp1/20260803/cam1", "failed", final=False)

        assert rec.sessions["exp1"].pending_exports == 1

    def test_pending_never_goes_negative(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session()
        rec.module_export_update("cam1", "exp1/20260803/cam1", "complete")
        assert rec.sessions["exp1"].pending_exports == 0

    def test_recovery_logged_when_pending_clears_after_stall_alert(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.STOPPED, pending_exports=1, export_stall_alerted=True,
        )
        with patch.object(rec, "_log_session_event") as mock_log:
            rec.module_export_update("cam1", "exp1/20260803/cam1", "complete")

        assert rec.sessions["exp1"].export_stall_alerted is False
        assert any(c.args[1] == "RECOVERY" for c in mock_log.call_args_list)


class TestCheckExportStallAfterStop:
    def test_alerts_when_stopped_session_has_pending_exports_past_threshold(self):
        rec, facade = _make_recording(recording={"export_stall_after_stop_mins": 15})
        rec.sessions["exp1"] = _session(
            state=SessionState.STOPPED,
            pending_exports=2,
            stopped_epoch=time.time() - 20 * 60,
        )
        rec._check_export_stall_after_stop()

        assert rec.sessions["exp1"].export_stall_alerted is True
        facade.send_alert.assert_called_once()
        assert facade.send_alert.call_args.kwargs["severity"] == "warning"

    def test_no_alert_before_threshold_elapsed(self):
        rec, facade = _make_recording(recording={"export_stall_after_stop_mins": 15})
        rec.sessions["exp1"] = _session(
            state=SessionState.STOPPED,
            pending_exports=2,
            stopped_epoch=time.time() - 5 * 60,
        )
        rec._check_export_stall_after_stop()

        assert rec.sessions["exp1"].export_stall_alerted is False
        facade.send_alert.assert_not_called()

    def test_no_alert_when_nothing_pending(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(
            state=SessionState.STOPPED,
            pending_exports=0,
            stopped_epoch=time.time() - 60 * 60,
        )
        rec._check_export_stall_after_stop()
        facade.send_alert.assert_not_called()

    def test_does_not_realert_once_already_flagged(self):
        rec, facade = _make_recording(recording={"export_stall_after_stop_mins": 15})
        rec.sessions["exp1"] = _session(
            state=SessionState.STOPPED,
            pending_exports=2,
            stopped_epoch=time.time() - 60 * 60,
            export_stall_alerted=True,
        )
        rec._check_export_stall_after_stop()
        facade.send_alert.assert_not_called()

    def test_active_sessions_are_not_considered(self):
        rec, facade = _make_recording(recording={"export_stall_after_stop_mins": 15})
        rec.sessions["exp1"] = _session(
            state=SessionState.ACTIVE,
            pending_exports=5,
            stopped_epoch=time.time() - 60 * 60,
        )
        rec._check_export_stall_after_stop()
        facade.send_alert.assert_not_called()


class TestCheckAllStopped:
    def test_transitions_to_stopped_when_nothing_still_stopping(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(module_stop_states={"cam1": "stopped"})
        rec._check_all_stopped("exp1")
        assert rec.sessions["exp1"].state == SessionState.STOPPED
        facade.update_sessions.assert_called_once_with(rec.sessions)

    def test_stays_active_while_a_module_is_still_stopping(self):
        rec, facade = _make_recording()
        rec.sessions["exp1"] = _session(module_stop_states={"cam1": "stopping"})
        rec._check_all_stopped("exp1")
        assert rec.sessions["exp1"].state == SessionState.ACTIVE
        facade.update_sessions.assert_not_called()

    def test_returns_to_scheduled_for_daily_sessions(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(
            module_stop_states={"cam1": "stopped"}, scheduled_stopping=True
        )
        rec._check_all_stopped("exp1")
        session = rec.sessions["exp1"]
        assert session.state == SessionState.SCHEDULED
        assert session.scheduled_stopping is False


class TestForceStartScheduledSession:
    def test_unknown_session_returns_error(self):
        rec, _facade = _make_recording()
        result = rec.force_start_scheduled_session("ghost")
        assert result["success"] is False

    def test_non_scheduled_session_rejected(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(scheduled=False)
        result = rec.force_start_scheduled_session("exp1")
        assert result["success"] is False

    def test_already_active_rejected(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(scheduled=True, state=SessionState.ACTIVE)
        result = rec.force_start_scheduled_session("exp1")
        assert result["success"] is False

    def test_stopped_session_rejected(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(scheduled=True, state=SessionState.STOPPED)
        result = rec.force_start_scheduled_session("exp1")
        assert result["success"] is False

    def test_success_when_start_scheduled_session_activates_it(self):
        rec, _facade = _make_recording()
        rec.sessions["exp1"] = _session(scheduled=True, state=SessionState.SCHEDULED)
        rec._start_scheduled_session = lambda name, today: setattr(
            rec.sessions[name], "state", SessionState.ACTIVE
        )
        result = rec.force_start_scheduled_session("exp1")
        assert result == {"success": True}


# ---------------------------------------------------------------------------
# Tier C: filesystem-touching helpers -- real tmp paths, no hardcoded /var or /home
# ---------------------------------------------------------------------------

class TestCheckShareWritable:
    def test_writable_share_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording()
            del rec._check_share_writable  # restore the real method for this test
            facade.get_share_path.return_value = tmpdir
            assert rec._check_share_writable() is None

    def test_unwritable_path_returns_error_string(self):
        rec, facade = _make_recording()
        del rec._check_share_writable
        facade.get_share_path.return_value = "/no/such/path/at/all"
        error = rec._check_share_writable()
        assert error is not None
        assert "not writable" in error


class TestCheckNasSpace:
    def test_reachable_share_reports_free_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording()
            facade.get_share_path.return_value = tmpdir
            result = rec._check_nas_space()
            assert result["ok"] is True
            assert 0 <= result["free_pct"] <= 100

    def test_unreachable_share_reports_error(self):
        rec, facade = _make_recording()
        facade.get_share_path.return_value = "/no/such/path/at/all"
        result = rec._check_nas_space()
        assert result["ok"] is False
        assert "error" in result


class TestSessionPersistence:
    def test_save_then_load_round_trips_session_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_file = os.path.join(tmpdir, "sessions.json")
            rec, _facade = _make_recording(sessions_file=sessions_file)
            rec.sessions["exp1"] = _session(
                state=SessionState.STOPPED, modules=["cam1"]
            )
            rec._save_sessions()

            with open(sessions_file) as f:
                saved = json.load(f)
            assert saved["exp1"]["session_name"] == "exp1"

            with patch("src.controller.recording.threading.Thread"):
                rec2 = Recording()
            assert rec2.sessions["exp1"].state == SessionState.STOPPED

    def test_active_session_recovered_as_error_on_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_file = os.path.join(tmpdir, "sessions.json")
            rec, _facade = _make_recording(sessions_file=sessions_file)
            rec.sessions["exp1"] = _session(
                state=SessionState.ACTIVE, modules=["cam1"]
            )
            rec._save_sessions()

            with patch("src.controller.recording.threading.Thread"):
                rec2 = Recording()

            session = rec2.sessions["exp1"]
            assert session.state == SessionState.ERROR
            assert session.error_message == (
                "Controller restarted during active session"
            )
            assert session.module_stop_states == {"cam1": "unknown"}
