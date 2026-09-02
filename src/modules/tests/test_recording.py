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

import os
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


class TestStartRecordingModuleReportsFailure:
    """facade.start_new_recording() returning literal False (e.g.
    CameraBase/microphone with no hardware) must not fall through to
    declaring recording_started/success -- see _create_initial_recording_
    segment's `is not False` gate. The module itself is responsible for its
    own recording_start_failed status (not asserted here -- that's the
    module's contract, exercised in test_camera_base.py); this only checks
    the generic caller stops correctly instead of lying about success."""

    def test_false_return_stops_before_declaring_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            facade.start_new_recording.return_value = False

            with patch("src.modules.recording.threading.Thread"):
                result = rec.start_recording("exp1", None)

            assert result == {
                "result": "error",
                "error": "Module could not start recording "
                         "(see recording_start_failed status)",
            }
            assert rec.is_recording is False
            sent_types = [c.args[0]["type"] for c in facade.send_status.call_args_list]
            assert "recording_started" not in sent_types

    def test_none_return_is_still_treated_as_success(self):
        """Most module types (microphone pre-fix, ttl, sound, template) return
        None on a successful start, not True -- the gate must not start
        treating every falsy return as failure, only an explicit False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            facade.start_new_recording.return_value = None

            with patch("src.modules.recording.threading.Thread"):
                result = rec.start_recording("exp1", None)

            assert result == {"result": "success"}
            assert rec.is_recording is True


class TestScheduledStart:
    """_scheduled_start's own SCHED_FIFO/pre-stage/spin-wait mechanics are
    deliberately out of scope (see module docstring) -- these tests cover
    only the try/except boundary wrapped around the whole method body
    (fixed 2026-08-25), by calling it directly with start_at in the past so
    the sleep/spin step falls through immediately."""

    def test_exception_during_prep_reports_recording_start_failed(self):
        """Was a real bug: this whole method ran in its own daemon thread,
        spawned after start_recording() had already returned {"result":
        "success"} to the controller -- command.py's outer try/except has
        long since returned by the time this thread body runs, so a failure
        here (e.g. camera_base.py's _start_new_recording() hitting a busy
        encoder) previously vanished into Python's default thread
        excepthook (stderr) with no send_status of any kind."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            rec._pre_setup_session = MagicMock(side_effect=RuntimeError("encoder busy"))

            rec._scheduled_start("exp1", None, time.time())

            facade.send_status.assert_called_once_with({
                "type": "recording_start_failed", "error": "encoder busy"
            })
            assert rec.is_recording is False

    def test_exception_in_begin_recording_reports_recording_start_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)
            facade.start_new_recording.side_effect = RuntimeError("device busy")

            rec._scheduled_start("exp1", None, time.time())

            facade.send_status.assert_called_once_with({
                "type": "recording_start_failed", "error": "device busy"
            })
            assert rec.is_recording is False

    def test_successful_run_begins_recording_without_reporting_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir)

            rec._scheduled_start("exp1", None, time.time())

            assert rec.is_recording is True
            failure_calls = [
                c for c in facade.send_status.call_args_list
                if c.args[0].get("type") == "recording_start_failed"
            ]
            assert failure_calls == []


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
            rec, facade = _make_recording(
                tmpdir,
                **{"export.auto_export": False,
                   "recording.export_session_journal": False},
            )
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

    def test_stop_also_stages_a_session_journal_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(tmpdir, **{"export.auto_export": False})
            rec.is_recording = True
            rec.current_session_name = "exp1"
            rec.current_health_segment = "health.csv"
            rec.recording_start_time = 1_787_800_000.0
            rec.current_filename_prefix = f"{rec.recording_folder}/exp1_cam_ab12cd"
            facade.stop_recording.return_value = True

            rec.stop_recording()

            staged = [c.args[0] for c in facade.stage_file_for_export.call_args_list]
            assert "health.csv" in staged
            journal = [p for p in staged if "_journal_(" in p and p.endswith(".txt")]
            assert len(journal) == 1
            assert os.path.exists(journal[0])

    def test_session_journal_disabled_by_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, facade = _make_recording(
                tmpdir, **{"recording.export_session_journal": False})
            rec.is_recording = True
            rec.current_session_name = "exp1"
            rec.current_health_segment = "health.csv"
            facade.stop_recording.return_value = True

            rec.stop_recording()

            staged = [c.args[0] for c in facade.stage_file_for_export.call_args_list]
            assert not any("_journal_(" in p for p in staged)

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


class TestMeasuredRecordingRate:
    def _write(self, path, size):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\0" * size)

    def test_first_sample_sets_baseline_and_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _ = _make_recording(tmpdir)
            rec.is_recording = True
            rec._reset_byte_sampler()
            self._write(f"{tmpdir}/pending/seg0.ts", 1000)
            rec._sample_recording_bytes()
            assert rec._measured_rec_bytes_per_s is None
            assert rec._rec_bytes_baseline == 1000

    def test_rate_from_growth_between_samples(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _ = _make_recording(tmpdir)
            rec.is_recording = True
            rec._reset_byte_sampler()
            self._write(f"{tmpdir}/pending/seg0.ts", 1_000_000)
            rec._sample_recording_bytes()  # baseline
            rec._rec_sample_start_ts -= 10  # pretend 10s elapsed
            self._write(f"{tmpdir}/pending/seg0.ts", 3_000_000)
            rec._sample_recording_bytes()
            # +2 MB over 10s ~= 200_000 B/s
            assert 180_000 <= rec._measured_rec_bytes_per_s <= 220_000

    def test_hwm_survives_export_moving_and_deleting_a_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _ = _make_recording(tmpdir)
            rec.is_recording = True
            rec._reset_byte_sampler()
            self._write(f"{tmpdir}/pending/seg0.ts", 5_000_000)
            rec._sample_recording_bytes()          # baseline includes seg0
            rec._rec_sample_start_ts -= 10
            # export moves seg0 to to_export, then removes it; a new segment appears
            os.remove(f"{tmpdir}/pending/seg0.ts")
            self._write(f"{tmpdir}/pending/seg1.ts", 2_000_000)
            rec._sample_recording_bytes()
            # cum = seg0 HWM (5MB, retained) + seg1 (2MB) = 7MB; baseline 5MB
            # -> +2MB / 10s, NOT negative from the deletion
            assert rec._measured_rec_bytes_per_s > 0
            assert 180_000 <= rec._measured_rec_bytes_per_s <= 220_000

    def test_pending_export_rename_not_double_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _ = _make_recording(tmpdir)
            rec.is_recording = True
            rec._reset_byte_sampler()
            self._write(f"{tmpdir}/to_export/seg0.ts", 1_000_000)
            rec._sample_recording_bytes()
            rec._rec_sample_start_ts -= 10
            # same file, now PENDING_-prefixed mid-export
            os.rename(f"{tmpdir}/to_export/seg0.ts", f"{tmpdir}/to_export/PENDING_seg0.ts")
            rec._sample_recording_bytes()
            assert rec._measured_rec_bytes_per_s == 0.0  # no new data

    def test_noop_when_not_recording(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec, _ = _make_recording(tmpdir)
            rec.is_recording = False
            rec._reset_byte_sampler()
            self._write(f"{tmpdir}/pending/seg0.ts", 1000)
            rec._sample_recording_bytes()
            assert rec._measured_rec_bytes_per_s is None
            assert rec._rec_sample_start_ts is None
