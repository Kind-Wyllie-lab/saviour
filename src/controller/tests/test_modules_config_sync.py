"""
Tests for config sync state transitions in src/controller/modules.py

Covers: PENDING→SYNCED, PENDING→FAILED, and the thread-safety of
received_module_config / set_target_module_config running concurrently.
"""

import threading
from unittest.mock import MagicMock

from src.controller.modules import ConfigSyncStatus, Module, Modules


def _make_modules() -> Modules:
    m = Modules()
    # Don't start the background thread — not needed for these tests
    m.facade = None
    return m


def _register(mgr: Modules, module_id: str = "camera_abc") -> None:
    mgr.add_module(Module(
        id=module_id, name=module_id, type="camera", version="1.0", ip="10.0.0.2"
    ))


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestConfigSyncTransitions:
    def test_initial_received_config_marks_synced(self):
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        state = mgr._config_states["camera_abc"]
        assert state.status == ConfigSyncStatus.SYNCED

    def test_set_target_marks_pending(self):
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.set_target_module_config("camera_abc", {"camera": {"fps": 60}})
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.PENDING

    def test_matching_config_resolves_to_synced(self):
        mgr = _make_modules()
        _register(mgr)
        target = {"camera": {"fps": 60}}
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.set_target_module_config("camera_abc", target)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 60}})
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.SYNCED

    def test_mismatched_config_resolves_to_failed(self):
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.set_target_module_config("camera_abc", {"camera": {"fps": 60}})
        # Module replies with the OLD value — config didn't take
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.FAILED

    def test_private_keys_ignored_in_diff(self):
        """_-prefixed keys in true_config are filtered before diffing, so a
        module that echoes internal keys back shouldn't cause a FAILED status."""
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.set_target_module_config("camera_abc", {"camera": {"fps": 60}})
        # Module's reply includes an internal key the frontend never set
        mgr.received_module_config("camera_abc", {
            "camera": {"fps": 60, "_internal": "system_value"}
        })
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.SYNCED

    def test_unregistered_module_auto_registered(self):
        """received_module_config for an unknown module should auto-register it."""
        mgr = _make_modules()
        mgr.received_module_config("ghost_module", {"camera": {"fps": 25}})
        assert "ghost_module" in mgr._modules


# ---------------------------------------------------------------------------
# FrameSync reconciliation hook
# ---------------------------------------------------------------------------

class TestReceivedModuleConfigFramesyncHook:
    def test_camera_config_triggers_reconcile(self):
        mgr = _make_modules()
        mgr.facade = MagicMock()
        _register(mgr, "camera_abc")

        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})

        mgr.facade.reconcile_framesync.assert_called_once()

    def test_non_camera_module_does_not_trigger_reconcile(self):
        mgr = _make_modules()
        mgr.facade = MagicMock()
        mgr.add_module(Module(id="ttl_1", name="ttl_1", type="ttl", version="1.0", ip="10.0.0.3"))

        mgr.received_module_config("ttl_1", {"ttl": {"pin": 4}})

        mgr.facade.reconcile_framesync.assert_not_called()

    def test_no_facade_does_not_crash(self):
        """facade is None in a bare Modules() before the controller wires it
        up post-construction -- the hook must guard against that, not crash
        the very first config fetch during startup."""
        mgr = _make_modules()
        _register(mgr, "camera_abc")
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})  # must not raise


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestConfigSyncThreadSafety:
    def test_concurrent_set_target_and_received_no_crash(self):
        """Interleaved set_target_module_config and received_module_config must
        not raise or leave the status in an undefined state."""
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})

        errors = []
        n_iters = 100

        def setter():
            for i in range(n_iters):
                try:
                    mgr.set_target_module_config(
                        "camera_abc", {"camera": {"fps": 30 + i % 60}}
                    )
                except Exception as e:
                    errors.append(e)

        def receiver():
            for i in range(n_iters):
                try:
                    mgr.received_module_config(
                        "camera_abc", {"camera": {"fps": 30 + i % 60}}
                    )
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=setter)
        t2 = threading.Thread(target=receiver)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Exceptions during concurrent access: {errors}"
        # Status must be one of the valid states — never undefined
        state = mgr._config_states["camera_abc"]
        assert state.status in (
            ConfigSyncStatus.SYNCED,
            ConfigSyncStatus.PENDING,
            ConfigSyncStatus.FAILED,
        )

    def test_multiple_modules_independent(self):
        """Config sync state for separate modules must not bleed into each other."""
        mgr = _make_modules()
        ids = [f"camera_{i:03}" for i in range(5)]
        for mid in ids:
            _register(mgr, mid)

        def configure(mid, fps):
            mgr.received_module_config(mid, {"camera": {"fps": 30}})
            mgr.set_target_module_config(mid, {"camera": {"fps": fps}})
            mgr.received_module_config(mid, {"camera": {"fps": fps}})

        threads = [threading.Thread(target=configure, args=(mid, 30 + i * 10))
                   for i, mid in enumerate(ids)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for mid in ids:
            assert mgr._config_states[mid].status == ConfigSyncStatus.SYNCED


class TestExportCredentialsApplied:
    """2026-10-02: a freshly installed camera showed FAILED with the only diff
    export.share_password -- set_export_config's ack carries no config, so the
    cached true_config kept get_config's password-less export section."""

    CREDS = {"share_ip": "10.0.0.1", "share_path": "controller_share",
             "share_username": "saviour_module", "share_password": "pw"}

    def _failed_on_password(self):
        mgr = _make_modules()
        _register(mgr)
        no_pw = {"camera": {"fps": 30},
                 "export": {"share_ip": "10.0.0.1", "share_password": None}}
        mgr.received_module_config("camera_abc", no_pw)
        mgr.set_target_module_config(
            "camera_abc", {"camera": {"fps": 30},
                           "export": {"share_ip": "10.0.0.1", "share_password": "pw"}})
        mgr.received_module_config("camera_abc", no_pw)
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.FAILED
        return mgr

    def test_ack_folds_credentials_in_and_resolves_synced(self):
        mgr = self._failed_on_password()
        mgr.export_credentials_applied("camera_abc", self.CREDS)
        state = mgr._config_states["camera_abc"]
        assert state.status == ConfigSyncStatus.SYNCED
        assert state.diffs == []
        assert state.true_config["export"]["share_password"] == "pw"
        assert state.true_config["camera"] == {"fps": 30}   # other sections kept

    def test_real_mismatch_elsewhere_stays_failed(self):
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.set_target_module_config("camera_abc", {"camera": {"fps": 60}})
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.export_credentials_applied("camera_abc", self.CREDS)
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.FAILED

    def test_pending_change_is_not_judged_early(self):
        mgr = _make_modules()
        _register(mgr)
        mgr.received_module_config("camera_abc", {"camera": {"fps": 30}})
        mgr.set_target_module_config("camera_abc", {"camera": {"fps": 60}})
        mgr.export_credentials_applied("camera_abc", self.CREDS)
        assert mgr._config_states["camera_abc"].status == ConfigSyncStatus.PENDING

    def test_unknown_module_or_no_config_is_a_noop(self):
        mgr = _make_modules()
        mgr.export_credentials_applied("ghost", self.CREDS)
        assert "ghost" not in mgr._config_states
