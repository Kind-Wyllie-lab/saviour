"""
Tests for src/modules/recording.py.

Covers pure helpers (session-name formatting, filename parsing), the
start/stop_recording orchestration (with threading.Thread patched so the
segment-monitor and health-metadata threads never actually spawn or touch
disk), and the segment/session-prep helpers. The monitoring loops
themselves (_monitor_recording_length, _record_health_metadata,
_auto_stop_recording, _scheduled_start's SCHED_FIFO/spin-wait) are
deliberately out of scope -- long-running orchestration bodies rather than
distinct branching logic, same reasoning as the controller-side monitor
loop.
"""

import tempfile
import time
from unittest.mock import MagicMock, patch

from src.modules.recording import Recording


def _make_config(recording_folder: str, **overrides) -> MagicMock:
    values = {"recording.recording_folder": recording_folder, **overrides}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    return cfg


def _make_recording(tmpdir: str, **config_overrides) -> tuple:
    """A Recording instance whose recording_folder points at a temp dir, with
    a MagicMock facade wired up."""
    config = _make_config(tmpdir, **config_overrides)
    rec = Recording(config)
    facade = MagicMock()
    facade.get_module_name.return_value = "camera_habitat1"
    facade.get_short_mac.return_value = "ab12cd"
    facade.get_utc_time.return_value = "20260803-120000"
    facade.get_utc_date.return_value = "20260803"
    rec.facade = facade
    return rec, facade


# ---------------------------------------------------------------------------
# Tier A: pure helpers
# ---------------------------------------------------------------------------

class TestFormatSessionName:
    def test_strips_unsafe_characters_and_replaces_spaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _facade = _make_recording(tmpdir)
            assert rec._format_session_name("weird!! name??") == "weird_name"

    def test_empty_or_none_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _facade = _make_recording(tmpdir)
            assert rec._format_session_name(None) == ""
            assert rec._format_session_name("") == ""


class TestGetSessionFromFilename:
    def test_splits_at_first_underscore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _facade = _make_recording(tmpdir)
            assert rec.get_session_from_filename("myexp_camera_abc123.ts") == "myexp"


class TestGetStartTimeFromFilename:
    def test_extracts_datetime_from_segment_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _facade = _make_recording(tmpdir)
            name = "myexp_camera_health_metadata_(3_20260803-153000).csv"
            assert rec.get_start_time_from_filename(name) == "20260803-153000"

    def test_falls_back_to_stripping_one_trailing_character(self):
        """The fallback branch is `rsplit("_", 1)[-1][0:-1]` -- it strips
        exactly one trailing character, not a whole extension. It does NOT
        cleanly handle a real ".ts" suffix (2 chars); this documents the
        actual current behaviour rather than the comment's claim."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _facade = _make_recording(tmpdir)
            name = "myexp_camera_20260803-153000X"
            assert rec.get_start_time_from_filename(name) == "20260803-153000"


# ---------------------------------------------------------------------------
# Tier B: start_recording / stop_recording orchestration
# ---------------------------------------------------------------------------

class TestStartRecording:
    def test_already_recording_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec.is_recording = True

            result = rec.start_recording("exp1", None)

            assert result == {"result": "error", "error": "Already recording"}
            facade.send_status.assert_called_once_with({
                "type": "recording_start_failed", "error": "Already recording"
            })

    def test_past_start_at_begins_immediately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)

            with patch("src.modules.recording.threading.Thread"):
                result = rec.start_recording("exp1", None, start_at=1.0)

            assert result == {"result": "success"}
            assert rec.is_recording is True
            facade.start_new_recording.assert_called_once()
            facade.send_status.assert_called_with({
                "type": "recording_started", "status": "success", "recording": True,
            })

    def test_future_start_at_schedules_a_thread_without_starting_yet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)

            with patch("src.modules.recording.threading.Thread") as mock_thread:
                result = rec.start_recording("exp1", 60, start_at=time_far_future())

            assert result == {"result": "success"}
            assert rec.is_recording is False
            mock_thread.assert_called_once()
            assert mock_thread.call_args.kwargs["target"] == rec._scheduled_start
            assert mock_thread.call_args.kwargs["args"][0] == "exp1"
            facade.start_new_recording.assert_not_called()

    def test_immediate_start_without_session_name_omits_name_from_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            facade.get_module_name.return_value = "camera_habitat1"
            facade.get_short_mac.return_value = "ab12cd"

            with patch("src.modules.recording.threading.Thread"):
                rec.start_recording(None, None)

            expected = f"{rec.recording_folder}/camera_habitat1_ab12cd"
            assert rec.current_filename_prefix == expected

    def test_mac_not_duplicated_when_module_name_already_contains_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            facade.get_module_name.return_value = "camera_ab12cd"
            facade.get_short_mac.return_value = "ab12cd"

            with patch("src.modules.recording.threading.Thread"):
                rec.start_recording(None, None)

            assert rec.recording_session_id == "camera_ab12cd"


def time_far_future() -> float:
    return time.time() + 3600


class TestStopRecording:
    def test_not_recording_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)

            result = rec.stop_recording()

            assert result is False
            facade.send_status.assert_called_once_with({
                "type": "recording_stop_failed", "error": "Not recording"
            })

    def test_success_path_reports_stopped_and_stages_health_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir, **{"export.auto_export": False})
            rec.is_recording = True
            rec.current_session_name = "exp1"
            rec.current_health_segment = "health.csv"
            facade.stop_recording.return_value = True

            result = rec.stop_recording()

            assert result == {"result": "Success"}
            assert rec.is_recording is False
            facade.stage_file_for_export.assert_called_once_with("health.csv")
            facade.signal_export_ready.assert_not_called()
            facade.send_status.assert_called_with({
                "type": "recording_stopped", "status": "success", "recording": False,
            })

    def test_auto_export_signals_export_ready_with_session_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir, **{"export.auto_export": True})
            rec.is_recording = True
            rec.current_session_name = "exp1"
            rec.current_health_segment = "health.csv"
            facade.stop_recording.return_value = True
            facade.get_module_name.return_value = "camera1"
            facade.get_utc_date.return_value = "20260803"

            rec.stop_recording()

            facade.signal_export_ready.assert_called_once_with("exp1/20260803/camera1")

    def test_module_stop_failure_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec.is_recording = True
            facade.stop_recording.return_value = False

            result = rec.stop_recording()

            assert result == {"result": "error", "error": "Failed to stop recording"}
            facade.send_status.assert_called_with({
                "type": "recording_stopped", "status": "error",
            })

    def test_exception_in_module_stop_is_caught_and_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec.is_recording = True
            facade.stop_recording.side_effect = RuntimeError("camera wedged")

            result = rec.stop_recording()

            assert result["result"] == "failure"
            assert "camera wedged" in result["message"]


# ---------------------------------------------------------------------------
# Tier C: segment / session-prep helpers (no threads involved)
# ---------------------------------------------------------------------------

class TestPreSetupSession:
    def test_sets_filename_prefix_with_session_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            facade.get_module_name.return_value = "camera_habitat1"
            facade.get_short_mac.return_value = "ab12cd"

            rec._pre_setup_session("exp1", start_at=123.0)

            assert rec.current_filename_prefix == (
                f"{rec.recording_folder}/exp1_camera_habitat1_ab12cd"
            )
            assert rec.segment_id == 0
            assert rec.segment_start_time == 123.0
            # when_recording_starts() is deferred to _begin_recording
            facade.when_recording_starts.assert_not_called()


class TestCreateInitialRecordingSegment:
    def test_resets_segment_state_and_starts_recording(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec.segment_id = 5

            rec._create_initial_recording_segment()

            assert rec.segment_id == 0
            facade.start_new_recording.assert_called_once()


class TestCreateNewRecordingSegment:
    def test_increments_segment_and_signals_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec.segment_id = 0
            rec.current_session_name = "exp1"
            rec.current_filename_prefix = f"{tmpdir}/exp1_camera1"
            facade.get_module_name.return_value = "camera1"
            facade.get_utc_date.return_value = "20260803"

            # _start_next_health_metadata_segment() (called internally) spins
            # up a real health-metadata thread unless Thread is patched.
            with patch("src.modules.recording.threading.Thread"):
                rec._create_new_recording_segment()

            assert rec.segment_id == 1
            facade.start_next_recording_segment.assert_called_once()
            facade.signal_export_ready.assert_called_once_with("exp1/20260803/camera1")


class TestGetHealthSegmentFilename:
    def test_builds_filename_from_prefix_segment_and_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec.current_filename_prefix = f"{tmpdir}/exp1_camera1"
            rec.segment_id = 2
            facade.get_utc_time.return_value = "20260803-120000"

            filename = rec._get_health_segment_filename()

            expected = f"{tmpdir}/exp1_camera1_health_metadata_(2_20260803-120000).csv"
            assert filename == expected
