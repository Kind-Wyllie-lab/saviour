"""
Tests for the readiness-check machinery in src/modules/module.py.

Module.__init__ builds a full config/network/zmq/ptp/export/health stack (a
real Config(), a real Network() that blocks on nmcli, etc.), so every test
here builds a bare instance via object.__new__() -- same pattern as
test_module.py's _bare_instance -- and sets only the attributes the method
under test reads. Module is an ABC with abstract _start_new_recording /
_start_next_recording_segment / _stop_recording / configure_module_special;
object.__new__ still enforces __abstractmethods__ even though it skips
__init__, so a minimal concrete subclass overrides them.
"""

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

from src.modules.module import Module


class _DummyModule(Module):
    def _start_new_recording(self):
        return True

    def _start_next_recording_segment(self):
        return True

    def _stop_recording(self):
        return True

    def configure_module_special(self, updated_keys):
        pass


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _bare_module(**attrs) -> _DummyModule:
    m = object.__new__(_DummyModule)
    m.logger = MagicMock()
    m.module_type = "camera"
    m.config = _make_config()
    m.facade = MagicMock()
    m.ptp = MagicMock()
    m.export = MagicMock()
    m.is_running = True
    m.is_recording = False
    m.module_checks = []
    for key, value in attrs.items():
        setattr(m, key, value)
    return m


# ---------------------------------------------------------------------------
# Threshold getters -- plain config passthrough
# ---------------------------------------------------------------------------

class TestThresholdGetters:
    def test_disk_space_default(self):
        m = _bare_module()
        assert m._get_required_disk_space_mb() == 100.0

    def test_disk_space_configured(self):
        m = _bare_module(config=_make_config(**{"module.required_disk_space_mb": 500.0}))
        assert m._get_required_disk_space_mb() == 500.0

    def test_ptp_offset_default(self):
        m = _bare_module()
        assert m._get_ptp_offset_threshold_us() == 1000000.0

    def test_ptp_offset_configured(self):
        m = _bare_module(config=_make_config(**{"module.ptp_offset_threshold_us": 50.0}))
        assert m._get_ptp_offset_threshold_us() == 50.0


# ---------------------------------------------------------------------------
# _check_running / _check_recording -- pure state checks
# ---------------------------------------------------------------------------

class TestCheckRunning:
    def test_running(self):
        m = _bare_module(is_running=True)
        assert m._check_running() == (True, "Module is running")

    def test_not_running(self):
        m = _bare_module(is_running=False)
        assert m._check_running() == (False, "Module is not running")


class TestCheckRecording:
    def test_not_recording_is_ready(self):
        m = _bare_module(is_recording=False)
        assert m._check_recording() == (True, "Module not currently recording")

    def test_recording_blocks_readiness(self):
        m = _bare_module(is_recording=True)
        assert m._check_recording() == (False, "Module is currently recording")


# ---------------------------------------------------------------------------
# _check_readwrite -- real filesystem, temp dir
# ---------------------------------------------------------------------------

class TestCheckReadwrite:
    def test_writable_folder_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _bare_module()
            m.facade.get_recording_folder.return_value = os.path.join(tmpdir, "rec")
            result, message = m._check_readwrite()
        assert result is True
        assert "writable" in message

    def test_creates_folder_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _bare_module()
            target = os.path.join(tmpdir, "does_not_exist_yet")
            m.facade.get_recording_folder.return_value = target
            m._check_readwrite()
            assert os.path.isdir(target)

    def test_permission_error_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _bare_module()
            m.facade.get_recording_folder.return_value = tmpdir
            with patch("builtins.open", side_effect=PermissionError("denied")):
                result, message = m._check_readwrite()
        assert result is False
        assert "Permission error" in message

    def test_os_error_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _bare_module()
            m.facade.get_recording_folder.return_value = tmpdir
            with patch("builtins.open", side_effect=OSError("disk full")):
                result, message = m._check_readwrite()
        assert result is False
        assert "OSError" in message


# ---------------------------------------------------------------------------
# _check_diskspace -- os.statvfs mocked (POSIX-only; create=True so this
# still collects on a non-POSIX dev machine where the attribute is absent)
# ---------------------------------------------------------------------------

def _statvfs(*, frsize=1024 * 1024, bavail):
    """A statvfs-shaped MagicMock giving `bavail` MB of free space at 1MB blocks."""
    return MagicMock(f_frsize=frsize, f_bavail=bavail)


class TestCheckDiskspace:
    def test_sufficient_space_passes(self):
        m = _bare_module(config=_make_config(**{"module.required_disk_space_mb": 100.0}))
        with patch("src.modules.module.os.statvfs", create=True,
                    return_value=_statvfs(bavail=500)):
            result, message = m._check_diskspace()
        assert result is True
        assert "Sufficient disk space" in message

    def test_insufficient_space_fails(self):
        m = _bare_module(config=_make_config(**{"module.required_disk_space_mb": 100.0}))
        with patch("src.modules.module.os.statvfs", create=True,
                    return_value=_statvfs(bavail=10)):
            result, message = m._check_diskspace()
        assert result is False
        assert "Insufficient disk space" in message

    def test_exception_reports_error(self):
        m = _bare_module()
        with patch("src.modules.module.os.statvfs", create=True,
                    side_effect=OSError("no such device")):
            result, message = m._check_diskspace()
        assert result is False
        assert "Cannot check disk space" in message

    def test_exact_boundary_silently_returns_none_instead_of_a_result_tuple(self):
        """Real bug, not a designed edge case (see CLAUDE.md TODO, found while
        adding test coverage): free_mb == required_mb hits neither `if free_mb
        > required_mb` nor `if free_mb < required_mb` (~line 1008-1011), so the
        function falls off the end and implicitly returns None instead of a
        (bool, str) tuple. _run_checks() then does `result, message = check()`
        on that None and crashes with TypeError, taking validate_readiness()'s
        try/except with it (it reports the TypeError as a failed readiness
        check rather than the module ever finding out its disk is exactly
        full to the threshold)."""
        m = _bare_module(config=_make_config(**{"module.required_disk_space_mb": 100.0}))
        with patch("src.modules.module.os.statvfs", create=True,
                    return_value=_statvfs(bavail=100)):
            result = m._check_diskspace()
        assert result is None


# ---------------------------------------------------------------------------
# _check_ptp -- self.ptp mocked
# ---------------------------------------------------------------------------

class TestCheckPtp:
    def test_synchronised_within_threshold_passes(self):
        m = _bare_module()
        m.ptp.get_status.return_value = {"last_offset": 50}
        result, message = m._check_ptp()
        assert result is True
        assert "synchronised" in message

    def test_negative_offset_within_threshold_passes(self):
        """Confirms the threshold compares abs(offset), not raw offset."""
        m = _bare_module()
        m.ptp.get_status.return_value = {"last_offset": -500}
        result, _ = m._check_ptp()
        assert result is True

    def test_offset_exceeds_threshold_fails(self):
        m = _bare_module(config=_make_config(**{"module.ptp_offset_threshold_us": 1000.0}))
        m.ptp.get_status.return_value = {"last_offset": 5000}
        result, message = m._check_ptp()
        assert result is False
        assert "PTP not synchronized" in message

    def test_none_offset_reports_settling_message(self):
        m = _bare_module()
        m.ptp.get_status.return_value = {"last_offset": None}
        result, message = m._check_ptp()
        assert result is False
        assert "settle" in message

    def test_exception_reports_error(self):
        m = _bare_module()
        m.ptp.get_status.side_effect = RuntimeError("ptp4l not running")
        result, message = m._check_ptp()
        assert result is False
        assert "PTP check failed" in message


# ---------------------------------------------------------------------------
# _check_export -- subprocess/mount mocked, real temp dir as mount_point
# ---------------------------------------------------------------------------

def _export_config(**overrides) -> MagicMock:
    defaults = {
        "export.export_target":   "controller",
        "export.share_password":  "sekret",
        "export.share_ip":        "10.0.0.1",
        "export.share_path":      "controller_share",
        "export.share_username":  "saviour_module",
    }
    defaults.update(overrides)
    return _make_config(**defaults)


class TestCheckExport:
    def test_non_controller_target_skips_check(self):
        m = _bare_module(config=_export_config(**{"export.export_target": "local"}))
        with patch("src.modules.module.subprocess.run") as mock_run:
            result, message = m._check_export()
        assert result is True
        assert "skipping" in message
        mock_run.assert_not_called()

    def test_missing_share_ip_fails_without_touching_network(self):
        """share_ip, not password, is the real "nothing configured yet"
        signal -- a blank password alone is a legitimate guest-share
        configuration (see the guest-mount tests below)."""
        m = _bare_module(config=_export_config(**{"export.share_ip": ""}))
        with patch("src.modules.module.subprocess.run") as mock_run:
            result, message = m._check_export()
        assert result is False
        assert "credentials not set" in message
        mock_run.assert_not_called()

    def test_blank_username_and_password_mounts_as_guest(self):
        """Confirmed live 2026-08-24 against a real NAS with `guest ok = yes`
        -- a blank username/password with share_ip set must attempt a guest
        mount, not hard-fail before ever trying, and must use the same
        "guest" auth keyword export.py/web.py's mount logic already does
        rather than literal empty username=/password= fields."""
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config(**{
                "export.share_username": "", "export.share_password": "",
            }))
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch(
                     "src.modules.module.os.path.ismount", side_effect=[False, True]
                 ), \
                 patch(
                     "src.modules.module.subprocess.run",
                     return_value=MagicMock(returncode=0),
                 ) as mock_run:
                result, message = m._check_export()

        assert result is True
        assert "reachable and writable" in message
        mount_cmd = mock_run.call_args_list[0][0][0]
        opts = mount_cmd[mount_cmd.index("-o") + 1]
        assert opts.startswith("guest,")
        assert "username=" not in opts

    def test_blank_password_with_real_username_still_attempts_mount(self):
        """A non-guest share where the password just hasn't been filled in
        yet is a real (if unusual) case -- it should surface the actual
        mount/auth failure from the CIFS server, not a blanket "credentials
        not set" before ever asking the share."""
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config(**{"export.share_password": ""}))
            m.export.mount_point = mount_point
            m.export.exporting = False

            denied = MagicMock(
                returncode=1, stderr="mount error(13): Permission denied"
            )
            with patch("src.modules.module.os.path.ismount", return_value=False), \
                 patch("src.modules.module.subprocess.run", return_value=denied), \
                 patch("src.modules.module.time.sleep"):
                result, message = m._check_export()

        assert result is False
        assert "Permission denied" in message

    def test_already_mounted_and_writable_passes_without_mounting(self):
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch("src.modules.module.os.path.ismount", return_value=True), \
                 patch("src.modules.module.subprocess.run") as mock_run:
                result, message = m._check_export()

        assert result is True
        assert "reachable and writable" in message
        mock_run.assert_not_called()  # never mounted by us -> nothing to unmount either

    def test_not_mounted_mounts_writes_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch("src.modules.module.os.path.ismount", side_effect=[False, True]), \
                 patch("src.modules.module.subprocess.run",
                       return_value=MagicMock(returncode=0)) as mock_run:
                result, message = m._check_export()

        assert result is True
        assert "reachable and writable" in message
        assert mock_run.call_count == 2  # mount, then cleanup umount in finally
        mount_cmd = mock_run.call_args_list[0][0][0]
        assert "mount" in mount_cmd
        umount_cmd = mock_run.call_args_list[1][0][0]
        assert "umount" in umount_cmd

    def test_mount_failure_reports_stderr_and_skips_cleanup(self):
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch("src.modules.module.os.path.ismount", return_value=False), \
                 patch("src.modules.module.subprocess.run",
                       return_value=MagicMock(returncode=1, stderr="no route to host")) as mock_run, \
                 patch("src.modules.module.time.sleep"):
                result, message = m._check_export()

        assert result is False
        assert "Cannot mount" in message
        assert "no route to host" in message
        # Transient-failure retry (see _check_export's _mount()) means every
        # attempt fails the same way here -- all 3 get exhausted, never marked
        # mounted_for_check either way.
        assert mock_run.call_count == 3

    def test_mount_timeout_reports_clear_message(self):
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch("src.modules.module.os.path.ismount", return_value=False), \
                 patch("src.modules.module.subprocess.run",
                       side_effect=subprocess.TimeoutExpired(cmd="mount", timeout=8)):
                result, message = m._check_export()

        assert result is False
        assert "timed out" in message

    def test_stale_mount_write_failure_triggers_remount_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False  # no live export -> remount is allowed

            with patch("src.modules.module.os.path.ismount", side_effect=[True, True]), \
                 patch("src.modules.module.subprocess.run",
                       return_value=MagicMock(returncode=0)) as mock_run, \
                 patch("builtins.open", side_effect=[OSError("stale CIFS handle"), MagicMock()]), \
                 patch("src.modules.module.os.remove"):
                result, message = m._check_export()

        assert result is True
        assert "remounted stale connection" in message
        # lazy umount, remount, final cleanup umount
        assert mock_run.call_count == 3

    def test_stale_mount_remount_failure_reported(self):
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch("src.modules.module.os.path.ismount", return_value=True), \
                 patch("src.modules.module.subprocess.run",
                       side_effect=[
                           MagicMock(returncode=0),  # lazy umount
                           # remount fails on all 3 retry attempts
                           MagicMock(returncode=1, stderr="still busy"),
                           MagicMock(returncode=1, stderr="still busy"),
                           MagicMock(returncode=1, stderr="still busy"),
                       ]), \
                 patch("builtins.open", side_effect=OSError("stale CIFS handle")), \
                 patch("src.modules.module.time.sleep"):
                result, message = m._check_export()

        assert result is False
        assert "Stale mount, remount failed" in message

    def test_freshly_mounted_write_failure_also_triggers_remount_and_retry(self):
        """A mount command can report success while the share isn't actually
        serving I/O yet (e.g. controller's Samba mid-restart). Previously the
        remount-recovery path only ran when the mount point was *already*
        mounted before this check -- a write failure straight after a fresh
        mount just raised. It should get the same recovery attempt."""
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = False

            with patch("src.modules.module.os.path.ismount", side_effect=[False, True]), \
                 patch("src.modules.module.subprocess.run",
                       return_value=MagicMock(returncode=0)) as mock_run, \
                 patch("builtins.open", side_effect=[OSError("share not ready"), MagicMock()]), \
                 patch("src.modules.module.os.remove"):
                result, message = m._check_export()

        assert result is True
        assert "remounted stale connection" in message
        # initial mount, lazy umount, remount, final cleanup umount
        assert mock_run.call_count == 4

    def test_write_failure_while_export_in_progress_is_not_treated_as_stale(self):
        """When export.exporting is True we must not unmount/remount out from
        under a live export -- the OSError should just propagate as a failure."""
        with tempfile.TemporaryDirectory() as mount_point:
            m = _bare_module(config=_export_config())
            m.export.mount_point = mount_point
            m.export.exporting = True

            with patch("src.modules.module.os.path.ismount", return_value=True), \
                 patch("src.modules.module.subprocess.run") as mock_run, \
                 patch("builtins.open", side_effect=OSError("stale CIFS handle")):
                result, message = m._check_export()

        assert result is False
        assert "Export check failed" in message
        mock_run.assert_not_called()  # must not touch the mount while exporting


# ---------------------------------------------------------------------------
# _perform_module_specific_checks -- subclass-provided @check() methods
# ---------------------------------------------------------------------------

class TestPerformModuleSpecificChecks:
    def test_all_module_checks_pass(self):
        m = _bare_module(module_checks=[
            MagicMock(__name__="c1", return_value=(True, "ok1")),
            MagicMock(__name__="c2", return_value=(True, "ok2")),
        ])
        result, message = m._perform_module_specific_checks()
        assert result is True
        assert message == "camera checks passed"

    def test_no_module_checks_passes_trivially(self):
        m = _bare_module(module_checks=[])
        result, _ = m._perform_module_specific_checks()
        assert result is True

    def test_first_failing_check_short_circuits_remaining_checks(self):
        second = MagicMock(__name__="c2", return_value=(True, "ok2"))
        m = _bare_module(module_checks=[
            MagicMock(__name__="c1", return_value=(False, "camera not detected")),
            second,
        ])
        result, message = m._perform_module_specific_checks()
        assert result is False
        assert message == "camera not detected"
        second.assert_not_called()


# ---------------------------------------------------------------------------
# _run_checks -- base checks (self.checks) then module-specific checks
# ---------------------------------------------------------------------------

class TestRunChecks:
    def test_all_checks_pass(self):
        m = _bare_module(checks=[
            MagicMock(__name__="base1", return_value=(True, "ok")),
        ])
        with patch.object(m, "_perform_module_specific_checks", return_value=(True, "module ok")):
            result, message = m._run_checks()
        assert result is True
        assert message == "All tests passed"

    def test_failing_base_check_short_circuits_before_module_checks(self):
        second_base = MagicMock(__name__="base2", return_value=(True, "ok"))
        m = _bare_module(checks=[
            MagicMock(__name__="base1", return_value=(False, "disk full")),
            second_base,
        ])
        with patch.object(m, "_perform_module_specific_checks") as mock_module_checks:
            result, message = m._run_checks()
        assert result is False
        assert message == "disk full"
        second_base.assert_not_called()
        mock_module_checks.assert_not_called()

    def test_module_specific_failure_propagates(self):
        m = _bare_module(checks=[
            MagicMock(__name__="base1", return_value=(True, "ok")),
        ])
        with patch.object(m, "_perform_module_specific_checks", return_value=(False, "sensor offline")):
            result, message = m._run_checks()
        assert result is False
        assert message == "sensor offline"


# ---------------------------------------------------------------------------
# validate_readiness -- public entry point, wraps _run_checks
# ---------------------------------------------------------------------------

class TestValidateReadiness:
    def test_wraps_passing_result_with_timestamp(self):
        m = _bare_module()
        with patch.object(m, "_run_checks", return_value=(True, "All tests passed")):
            status = m.validate_readiness()
        assert status["ready"] is True
        assert status["message"] == "All tests passed"
        assert isinstance(status["timestamp"], float)

    def test_exception_in_run_checks_is_caught_and_reported(self):
        m = _bare_module()
        with patch.object(m, "_run_checks", side_effect=RuntimeError("unexpected boom")):
            status = m.validate_readiness()
        assert status["ready"] is False
        assert "unexpected boom" in status["message"]
