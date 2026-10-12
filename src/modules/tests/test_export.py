"""
Tests for src/modules/export.py

Covers: PENDING_ rollback on copy failure, thread lock on concurrent exports,
and _mount_share retry + timeout behaviour.
"""

import io
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
    export.tc_last_error = None

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

    def test_triggered_session_reports_false_when_nothing_exported(self):
        """The real 2026-09-07 bug: files present, every export path fails
        (mount down), but `session_results[triggered_session]` came back True
        because the loop keyed results by the un-extractable session name and
        the fallback set an unconditional True -- so the controller marked the
        session exported and never retried."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            # module_id "microphone_4703" but filenames say "audiomoth_4703" --
            # _extract_session_from_filename returns None, so the loop keys on
            # the full export_path, which != triggered_session.
            exp.module_id = "microphone_4703"
            _write_test_file(
                exp.to_export_folder,
                "mysession_audiomoth_4703_(0_20260907-145949).flac")
            _write_test_file(
                exp.to_export_folder,
                "mysession_audiomoth_4703_(0_20260907-145949)_timestamps.txt")

            with patch.object(exp, "_setup_export", return_value=False), \
                 patch.object(exp, "_setup_recovered_export", return_value=False), \
                 patch.object(exp, "_update_samba_settings"):
                results = exp.export_staged("mysession/20260907/audiomoth")

            assert results.get("mysession") is False
            # files must still be recoverable in to_export/
            assert len(os.listdir(exp.to_export_folder)) == 2


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



class TestExtractSessionFromFilename:
    """Underscore module ids whose filenames carry the display name, not the
    type: a stranded mic_leak2 segment was exported into the next session's
    folder because the session couldn't be read off its filename."""

    def _exp(self, tmpdir, module_id):
        exp = _make_export(tmpdir)
        exp.module_id = module_id
        return exp

    def test_microphone_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = self._exp(tmpdir, "microphone_4703")
            fn = ("mic_leak2-microphone_4703-110924_audiomoth_4703_"
                  "24FCBD0864934CA8_(1_20261001-102428).flac")
            assert exp._extract_session_from_filename(fn) ==                 "mic_leak2-microphone_4703-110924"

    def test_hailo_display_name_with_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = self._exp(tmpdir, "hailo_camera_3606")
            fn = "rot1min-101803_ai camera_3606_(0_20261002-091806).ts"
            assert exp._extract_session_from_filename(fn) == "rot1min-101803"

    def test_camera_full_id_marker_still_preferred(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = self._exp(tmpdir, "camera_d074")
            fn = "rot1min_b-102844_camera_d074_(5_20261002-093347).ts"
            assert exp._extract_session_from_filename(fn) == "rot1min_b-102844"

    def test_session_from_filename_keeps_underscored_session(self):
        # Boot-time crash recovery files PARTIAL segments under this name.
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = self._exp(tmpdir, "camera_d074")
            fn = "my_exp-102844_camera_d074_(5_20261002-093347)_PARTIAL"
            assert exp.session_from_filename(fn) == "my_exp-102844"

    def test_session_from_filename_falls_back_to_first_underscore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = self._exp(tmpdir, "camera_d074")
            assert exp.session_from_filename("myexp_other_abc123.ts") == "myexp"


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

    def test_reuses_healthy_existing_mount_without_umount(self):
        """A working existing mount must be reused as-is -- never torn down
        first (a failed `umount: target is busy` under load then aborted the
        whole export, 2026-09-07)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            with patch("subprocess.run") as mock_run, \
                 patch("os.path.ismount", return_value=True), \
                 patch.object(exp, "_update_samba_settings"):
                result = exp._mount_share()
            assert result is True
            mock_run.assert_not_called()  # no umount, no mount

    def test_stale_mount_replaced_and_failed_umount_is_nonfatal(self):
        """If the existing mount is unusable, replace it -- and a non-zero
        umount must not abort the remount."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            umount_fail = MagicMock(returncode=32, stderr="target is busy")
            mount_ok = MagicMock(returncode=0, stderr="")
            with patch("subprocess.run",
                       side_effect=[umount_fail, umount_fail, mount_ok]), \
                 patch("os.path.ismount", return_value=True), \
                 patch.object(exp, "_mount_is_usable", return_value=False), \
                 patch.object(exp, "_update_samba_settings"), \
                 patch("time.sleep"):
                result = exp._mount_share()
            assert result is True

    def test_dead_mount_invisible_to_ismount_is_unmounted_not_stacked(self):
        """2026-10-02: after the controller was replaced, the old CIFS mount
        made os.path.ismount() report False (stat fails on a dead server), so
        a fresh mount went on top and the dead one retried its login forever.
        The kernel mount table must count: umount first, then mount."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            target = os.path.realpath(exp.mount_point)
            mounts = (f"//10.0.0.1/controller_share {target.replace(' ', chr(92) + '040')}"
                      " cifs rw 0 0\n")
            ok = MagicMock(returncode=0, stderr="")
            real_open = open

            def fake_open(path, *a, **kw):
                if path == "/proc/self/mounts":
                    return io.StringIO(mounts)
                if ".export_probe_" in str(path):
                    raise OSError(112, "Host is down")  # dead server
                return real_open(path, *a, **kw)

            with patch("subprocess.run", return_value=ok) as mock_run, \
                 patch("os.path.ismount", return_value=False), \
                 patch("builtins.open", side_effect=fake_open), \
                 patch.object(exp, "_update_samba_settings"), \
                 patch("time.sleep"):
                result = exp._mount_share()
            assert result is True
            cmds = [c.args[0] for c in mock_run.call_args_list]
            assert cmds[0][:2] == ["sudo", "umount"]
            assert cmds[-1][:3] == ["sudo", "mount", "-t"]


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


# ---------------------------------------------------------------------------
# Traffic shaping (tc) — failures must be visible, not swallowed
# ---------------------------------------------------------------------------

class TestTrafficControl:
    def test_run_shell_command_returns_true_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            with patch("subprocess.run") as run:
                run.return_value = MagicMock(stdout="", stderr="")
                assert exp._run_shell_command(["tc", "qdisc", "show"]) is True

    def test_run_shell_command_returns_false_on_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            with patch("subprocess.run",
                       side_effect=subprocess.CalledProcessError(1, "tc", stderr="boom")):
                assert exp._run_shell_command(["tc", "bad"]) is False

    def test_run_shell_command_returns_false_when_binary_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            with patch("subprocess.run", side_effect=FileNotFoundError("no tc")):
                assert exp._run_shell_command(["tc", "qdisc", "show"]) is False

    def test_apply_filter_clears_error_when_all_commands_succeed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            exp.tc_last_error = "stale"
            with patch.object(exp, "_run_shell_command", return_value=True):
                assert exp._apply_traffic_control_filter() is True
            assert exp.tc_last_error is None

    def test_apply_filter_sets_error_when_rate_class_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            # qdisc add "succeeds", class add fails, filter add "succeeds"
            with patch.object(exp, "_run_shell_command", side_effect=[True, False, True]):
                assert exp._apply_traffic_control_filter() is False
            assert exp.tc_last_error is not None
            assert "NOT rate-limited" in exp.tc_last_error
            exp.logger.error.assert_called()

    def test_apply_filter_refuses_without_share_ip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            exp.samba_share_ip = None
            with patch.object(exp, "_run_shell_command") as run:
                assert exp._apply_traffic_control_filter() is False
                run.assert_not_called()
            assert exp.tc_last_error is not None


# ---------------------------------------------------------------------------
# Date folder from the filename, not the clock (desk soak 2026-09-30)
# ---------------------------------------------------------------------------

class TestExportDateFromFilename:
    def test_extracts_the_segment_date(self):
        assert Export._extract_date_from_filename(
            "desk_soak-140122_camera_test_(0_20260930-130442)_PARTIAL.ts") == "20260930"
        assert Export._extract_date_from_filename("config.json") is None

    def test_routes_by_recording_date_not_the_current_clock(self):
        """After a power-loss reboot the module clock read 31 Aug until PTP
        converged, so a salvaged segment was filed under 20260831/."""
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            exp.facade = MagicMock()
            exp.facade.get_module_name.return_value = "camera_test"
            exp.facade.get_utc_date.return_value = "20260831"  # wrong clock
            nas_dir = os.path.join(tmpdir, "nas")
            os.makedirs(nas_dir)
            for name in ("soak_camera_test_(0_20260929-235900).ts",
                         "soak_camera_test_(1_20260930-000100).ts"):
                _write_test_file(exp.to_export_folder, name)
            targets = []

            def setup(path):
                targets.append(path)
                return nas_dir

            with patch.object(exp, "_setup_export", side_effect=setup), \
                 patch.object(exp, "_update_samba_settings"):
                result = exp.export_staged("soak/20260831/camera_test")

            assert sorted(targets) == [
                "soak/20260929/camera_test", "soak/20260930/camera_test"]
            assert result["soak"] is True

    def test_any_failed_date_group_fails_the_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exp = _make_export(tmpdir)
            exp.facade = MagicMock()
            exp.facade.get_module_name.return_value = "camera_test"
            nas_dir = os.path.join(tmpdir, "nas")
            os.makedirs(nas_dir)
            for name in ("soak_camera_test_(0_20260929-235900).ts",
                         "soak_camera_test_(1_20260930-000100).ts"):
                _write_test_file(exp.to_export_folder, name)

            def setup(path):
                return False if path.endswith("20260929/camera_test") else nas_dir

            with patch.object(exp, "_setup_export", side_effect=setup), \
                 patch.object(exp, "_setup_recovered_export", return_value=False), \
                 patch.object(exp, "_update_samba_settings"):
                result = exp.export_staged("soak/20260930/camera_test")

            assert result["soak"] is False
