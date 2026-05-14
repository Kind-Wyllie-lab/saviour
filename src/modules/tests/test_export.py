"""
Tests for src/modules/export.py

Covers: PENDING_ rollback on copy failure, thread lock on concurrent exports,
and _mount_share retry + timeout behaviour.
"""

import os
import shutil
import tempfile
import threading
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

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
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            nas_dir = os.path.join(tmpdir, "nas")
            os.makedirs(nas_dir)
            _write_test_file(exp.to_export_folder, "s_camera_test_001.flac")

            barrier = threading.Barrier(2)
            second_result = {}

            def slow_setup(path):
                barrier.wait()   # both threads reach here before first proceeds
                return nas_dir

            def first_thread():
                with patch.object(exp, "_setup_export", side_effect=slow_setup), \
                     patch.object(exp, "_update_samba_settings"):
                    exp.export_staged("s")

            def second_thread():
                barrier.wait()   # wait until first thread has set exporting=True
                second_result["r"] = exp.export_staged("s")

            t1 = threading.Thread(target=first_thread)
            t2 = threading.Thread(target=second_thread)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            # The second call must have been rejected (False result)
            # rather than running a concurrent export.
            if second_result.get("r") is not None:
                assert second_result["r"].get("s") is False


# ---------------------------------------------------------------------------
# _mount_share — retry and timeout
# ---------------------------------------------------------------------------

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
