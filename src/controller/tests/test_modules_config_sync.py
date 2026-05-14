"""
Tests for config sync state transitions in src/controller/modules.py

Covers: PENDING→SYNCED, PENDING→FAILED, and the thread-safety of
received_module_config / set_target_module_config running concurrently.
"""

import threading
import time
from src.controller.modules import Modules, ConfigSyncStatus, Module


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
