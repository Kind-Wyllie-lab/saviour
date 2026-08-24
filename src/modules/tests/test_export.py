"""
Tests for src/modules/export.py

Covers: PENDING_ rollback on copy failure, thread lock on concurrent exports,
and _mount_share retry + timeout behaviour.
"""

import json
import os
import subprocess
import tempfile
import time
from unittest.mock import MagicMock, patch

from src.modules.export import Export

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_export(tmpdir: str) -> Export:
    """Return an Export instance wired to a temp directory."""
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "recording.recording_folder": tmpdir,
        "export.share_ip":            "10.0.0.1",
        "export.share_path":          "controller_share",
        "export.share_username":      "saviour_module",
        "export.share_password":      "",
        "export.delete_on_export":    False,
        "export.manifest_enabled":    False,
        "export.max_bitrate_mb":      10,
        "export.max_burst_kb":        30,
    }.get(key, default)
    cfg.active_config_path = os.path.join(tmpdir, "active_config.json")

    export = Export.__new__(Export)
    export.module_id = "camera_test"
    export.config = cfg
    export.logger = MagicMock()
    export.mount_point = os.path.join(tmpdir, "mnt")
    export.pending_folder = os.path.join(tmpdir, "pending")
    export.to_export_folder = os.path.join(tmpdir, "to_export")
    export.exported_folder = os.path.join(tmpdir, "exported")
    export.samba_share_ip = "10.0.0.1"
    export.samba_share_path = "controller_share"
    export.samba_share_username = "saviour_module"
    export.samba_share_password = ""
    export.exporting = False
    export.staged_for_export = []
    export.session_files = []
    export.session_name = None
    export.recording_name = None
    export.export_path = None

    import threading as _t
    export._export_lock = _t.Lock()

    os.makedirs(export.pending_folder, exist_ok=True)
    os.makedirs(export.to_export_folder, exist_ok=True)
    os.makedirs(export.exported_folder, exist_ok=True)
    os.makedirs(export.mount_point, exist_ok=True)

    return export


def _write_test_file(folder: str, name: str = "test_file.flac") -> str:
    """Write a dummy file and return its path."""
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"FAKE_AUDIO_DATA" * 64)
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestExportStagedHappyPath:
    def test_file_moved_to_exported_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            nas_dir = os.path.join(tmpdir, "nas_session")
            os.makedirs(nas_dir)
            _write_test_file(exp.to_export_folder, "session_camera_test_001.flac")

            with patch.object(exp, "_setup_export", return_value=nas_dir), \
                 patch.object(exp, "_update_samba_settings"):
                results = exp.export_staged("session")

            assert results.get("session") is True
            assert os.path.exists(os.path.join(nas_dir, "session_camera_test_001.flac"))

    def test_source_file_no_longer_in_to_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            nas_dir = os.path.join(tmpdir, "nas_session")
            os.makedirs(nas_dir)
            _write_test_file(exp.to_export_folder, "session_camera_test_001.flac")

            with patch.object(exp, "_setup_export", return_value=nas_dir), \
                 patch.object(exp, "_update_samba_settings"):
                exp.export_staged("session")

            assert not os.path.exists(
                os.path.join(exp.to_export_folder, "session_camera_test_001.flac")
            )


# ---------------------------------------------------------------------------
# PENDING_ rollback on copy failure
# ---------------------------------------------------------------------------

class TestPendingRollback:
    def test_source_restored_on_copy_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            nas_dir = os.path.join(tmpdir, "nas_session")
            os.makedirs(nas_dir)
            filename = "session_camera_test_001.flac"
            src = _write_test_file(exp.to_export_folder, filename)

            def raise_on_copy(*_args, **_kwargs):
                raise OSError("Simulated NAS write failure")

            with patch.object(exp, "_setup_export", return_value=nas_dir), \
                 patch.object(exp, "_update_samba_settings"), \
                 patch("shutil.copy2", side_effect=raise_on_copy):
                results = exp.export_staged("session")

            # session reports failure
            assert results.get("session") is False
            # source file must be restored under its original name
            assert os.path.exists(src), "source file was not rolled back"
            pending = os.path.join(exp.to_export_folder, f"PENDING_{filename}")
            assert not os.path.exists(pending), "PENDING_ file left behind"

    def test_partial_nas_copy_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            nas_dir = os.path.join(tmpdir, "nas_session")
            os.makedirs(nas_dir)
            filename = "session_camera_test_002.flac"
            _write_test_file(exp.to_export_folder, filename)

            def partial_copy(src, dst):
                # Write a partial file to simulate interrupted copy
                with open(dst, "wb") as f:
                    f.write(b"PARTIAL")
                raise OSError("Interrupted")

            with patch.object(exp, "_setup_export", return_value=nas_dir), \
                 patch.object(exp, "_update_samba_settings"), \
                 patch("shutil.copy2", side_effect=partial_copy):
                exp.export_staged("session")

            pending_dest = os.path.join(nas_dir, f"PENDING_{filename}")
            assert not os.path.exists(pending_dest), "partial NAS copy was not cleaned up"

    def test_exporting_flag_cleared_after_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "session_camera_test_003.flac")

            with patch.object(exp, "_setup_export", return_value=False), \
                 patch.object(exp, "_update_samba_settings"):
                exp.export_staged("session")

            assert exp.exporting is False


# ---------------------------------------------------------------------------
# Thread lock — concurrent export_staged calls
# ---------------------------------------------------------------------------

class TestConcurrentExportRejected:
    def test_second_call_rejected_while_first_in_progress(self):
        # Set the exporting flag directly to simulate a concurrent export in
        # progress.  A threading barrier approach is inherently racy (Thread 1
        # can complete the full export before Thread 2 checks the flag), so we
        # test the guard in isolation without real concurrency.
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "s_camera_test_001.flac")
            exp.exporting = True  # simulate another export already running

            result = exp.export_staged("s")

            assert result.get("s") is False

    def test_exporting_flag_set_during_export(self):
        # Verify the flag is True while export_staged is executing, so that a
        # concurrent second call (tested above) sees it.
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            nas_dir = os.path.join(tmpdir, "nas")
            os.makedirs(nas_dir)
            _write_test_file(exp.to_export_folder, "s_camera_test_001.flac")
            flag_during = {}

            def capturing_setup(path):
                flag_during["exporting"] = exp.exporting
                return nas_dir

            with patch.object(exp, "_setup_export", side_effect=capturing_setup), \
                 patch.object(exp, "_update_samba_settings"):
                exp.export_staged("s")

            assert flag_during.get("exporting") is True


# ---------------------------------------------------------------------------
# _mount_share — retry and timeout
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# delete_on_export — local copies removed after successful NAS transfer
# ---------------------------------------------------------------------------

class TestDeleteOnExport:
    def test_exported_file_deleted_locally_when_flag_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            # Enable delete_on_export for this instance
            exp.config.get.side_effect = lambda key, default=None: {
                "recording.recording_folder": tmpdir,
                "export.share_ip":            "10.0.0.1",
                "export.share_path":          "controller_share",
                "export.share_username":      "saviour_module",
                "export.share_password":      "",
                "export.delete_on_export":    True,
                "export.manifest_enabled":    False,
                "export.max_bitrate_mb":      10,
                "export.max_burst_kb":        30,
            }.get(key, default)

            nas_dir = os.path.join(tmpdir, "nas_session")
            os.makedirs(nas_dir)
            filename = "session_camera_test_del.flac"
            _write_test_file(exp.to_export_folder, filename)

            with patch.object(exp, "_setup_export", return_value=nas_dir), \
                 patch.object(exp, "_update_samba_settings"):
                results = exp.export_staged("session")

            assert results.get("session") is True
            # File should have been removed from exported/ after NAS copy
            assert not os.path.exists(os.path.join(exp.exported_folder, filename)), \
                "local exported copy was not deleted despite delete_on_export=True"

    def test_exported_file_kept_locally_when_flag_unset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)  # delete_on_export=False by default
            nas_dir = os.path.join(tmpdir, "nas_session")
            os.makedirs(nas_dir)
            filename = "session_camera_test_keep.flac"
            _write_test_file(exp.to_export_folder, filename)

            with patch.object(exp, "_setup_export", return_value=nas_dir), \
                 patch.object(exp, "_update_samba_settings"):
                exp.export_staged("session")

            assert os.path.exists(os.path.join(exp.exported_folder, filename)), \
                "local exported copy was deleted despite delete_on_export=False"


class TestMountShare:
    def test_succeeds_on_first_attempt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            ok = MagicMock(returncode=0, stderr="")
            with patch("subprocess.run", return_value=ok) as mock_run, \
                 patch("os.path.ismount", return_value=False):
                result = exp._mount_share()
            assert result is True
            assert mock_run.call_count == 1

    def test_retries_on_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            fail = MagicMock(returncode=1, stderr="connection refused")
            with patch("subprocess.run", return_value=fail), \
                 patch("os.path.ismount", return_value=False), \
                 patch("time.sleep"):  # skip real delays
                result = exp._mount_share()
            assert result is False

    def test_succeeds_on_second_attempt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            fail = MagicMock(returncode=1, stderr="timeout")
            ok   = MagicMock(returncode=0, stderr="")
            with patch("subprocess.run", side_effect=[fail, ok]) as mock_run, \
                 patch("os.path.ismount", return_value=False), \
                 patch("time.sleep"):
                result = exp._mount_share()
            assert result is True
            assert mock_run.call_count == 2

    def test_timeout_is_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            ok = MagicMock(returncode=0, stderr="")
            with patch("subprocess.run",
                       side_effect=[subprocess.TimeoutExpired("mount", 30), ok]) as mock_run, \
                 patch("os.path.ismount", return_value=False), \
                 patch("time.sleep"):
                result = exp._mount_share()
            assert result is True
            assert mock_run.call_count == 2

    def test_gives_up_after_max_attempts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            fail = MagicMock(returncode=1, stderr="unreachable")
            with patch("subprocess.run", return_value=fail) as mock_run, \
                 patch("os.path.ismount", return_value=False), \
                 patch("time.sleep"):
                result = exp._mount_share()
            assert result is False
            assert mock_run.call_count == Export._MOUNT_MAX_ATTEMPTS

    def test_all_timeouts_gives_up(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            with patch("subprocess.run",
                       side_effect=subprocess.TimeoutExpired("mount", 30)) as mock_run, \
                 patch("os.path.ismount", return_value=False), \
                 patch("time.sleep"):
                result = exp._mount_share()
            assert result is False
            assert mock_run.call_count == Export._MOUNT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# summarize_recording_state
#
# module_id is "camera_test" (see _make_export) -- since it has no "-", the
# short-id marker in _extract_session_from_filename equals the full-id
# marker, both "_camera_test_", so filenames below embed that directly.
# ---------------------------------------------------------------------------

class TestSummarizeRecordingState:
    def test_empty_folders_returns_zeroed_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            zero = {"count": 0, "total_bytes": 0, "oldest_mtime": None, "newest_mtime": None}
            assert exp.summarize_recording_state("sessionA") == {
                "pending": zero, "to_export": zero, "exported": zero,
            }

    def test_counts_and_bytes_for_named_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "sessionA_camera_test_(0_20260820-101500).ts")
            _write_test_file(exp.to_export_folder, "sessionA_camera_test_(1_20260820-101600).ts")
            _write_test_file(exp.exported_folder, "sessionA_camera_test_(2_20260820-101700).ts")
            result = exp.summarize_recording_state("sessionA")
            assert result["to_export"]["count"] == 2
            assert result["to_export"]["total_bytes"] > 0
            assert result["exported"]["count"] == 1

    def test_never_returns_raw_filenames(self):
        """A habitat session can have 16 modules producing files for weeks --
        this must stay small regardless, so it's summary-only, never paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "sessionA_camera_test_(0_20260820-101500).ts")
            result = exp.summarize_recording_state("sessionA")
            assert "camera_test" not in json.dumps(result)
            assert ".ts" not in json.dumps(result)

    def test_filters_to_requested_session_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "sessionA_camera_test_(0_20260820-101500).ts")
            _write_test_file(exp.to_export_folder, "sessionB_camera_test_(0_20260820-101500).ts")
            result = exp.summarize_recording_state("sessionA")
            assert result["to_export"]["count"] == 1

    def test_no_session_name_groups_by_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "sessionA_camera_test_(0_20260820-101500).ts")
            _write_test_file(exp.to_export_folder, "sessionB_camera_test_(0_20260820-101500).ts")
            result = exp.summarize_recording_state()
            assert result["to_export"]["sessionA"]["count"] == 1
            assert result["to_export"]["sessionB"]["count"] == 1

    def test_unrecognised_filename_grouped_under_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            _write_test_file(exp.to_export_folder, "not_a_recognised_pattern.ts")
            result = exp.summarize_recording_state()
            assert result["to_export"]["_unknown"]["count"] == 1

    def test_oldest_and_newest_mtime_span_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            older = _write_test_file(exp.to_export_folder, "sessionA_camera_test_(0_20260820-101500).ts")
            _write_test_file(exp.to_export_folder, "sessionA_camera_test_(1_20260820-101600).ts")
            past = time.time() - 3600
            os.utime(older, (past, past))
            result = exp.summarize_recording_state("sessionA")
            assert result["to_export"]["oldest_mtime"] < result["to_export"]["newest_mtime"]

    def test_missing_folder_treated_as_empty_not_an_error(self):
        """The pending folder in particular may not exist yet on a module
        that has never recorded -- must not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            os.rmdir(exp.pending_folder)
            result = exp.summarize_recording_state("sessionA")
            assert result["pending"]["count"] == 0
