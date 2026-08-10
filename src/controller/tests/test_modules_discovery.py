"""
Tests for Modules.module_discovery() in src/controller/modules.py.

Regression coverage for a bug found 2026-08-10: a spurious mDNS re-announcement
(Network.update_service() firing with no real change -- e.g. an avahi TTL
refresh) reached module_discovery() with a freshly-constructed, mostly-default
Module object. module_discovery() used to wholesale-replace the existing
tracked Module with it via add_module(), silently resetting status back to
WAITING (and config to {}) for an already-known module that was still
RECORDING the whole time -- undoing the protection module_rediscovered()
(called immediately before it in the same Network.update_service() handler)
had just applied. That false "not recording" state tripped
RecordingManager._monitor_sessions()'s fault detection for a session that
never actually had a problem.
"""

from src.controller.modules import Module, Modules, ModuleStatus


def _make_modules() -> Modules:
    m = Modules()
    m.facade = None
    return m


def _register(mgr: Modules, module_id: str = "camera_abc") -> Module:
    module = Module(
        id=module_id, name=module_id, type="camera", version="1.0", ip="10.0.0.2"
    )
    mgr.add_module(module)
    return module


class TestModuleDiscoveryRediscovery:
    def test_rediscovery_of_known_module_does_not_reset_status(self):
        mgr = _make_modules()
        existing = _register(mgr)
        existing.status = ModuleStatus.RECORDING
        existing.config = {"camera": {"fps": 30}}
        existing.last_heartbeat_time = 12345.0

        # A fresh, mostly-default Module -- exactly what Network.update_service()
        # constructs from a zeroconf TXT-record re-announcement.
        reannounced = Module(
            id="camera_abc", name="camera_abc", type="camera",
            version="1.0", ip="10.0.0.2",
        )
        mgr.module_discovery(reannounced)

        tracked = mgr._modules["camera_abc"]
        assert tracked.status == ModuleStatus.RECORDING
        assert tracked.config == {"camera": {"fps": 30}}
        assert tracked.last_heartbeat_time == 12345.0

    def test_rediscovery_does_not_replace_the_tracked_object(self):
        """Guards against a partial fix that still swaps the Module instance
        wholesale but happens to copy the right fields -- other code (e.g.
        module_rediscovered(), called right before this in production) may
        hold a reference to the original object and mutate it in place."""
        mgr = _make_modules()
        existing = _register(mgr)
        existing.status = ModuleStatus.RECORDING

        reannounced = Module(
            id="camera_abc", name="camera_abc", type="camera",
            version="1.0", ip="10.0.0.2",
        )
        mgr.module_discovery(reannounced)

        assert mgr._modules["camera_abc"] is existing

    def test_genuinely_new_module_is_still_registered(self):
        mgr = _make_modules()
        new_module = Module(
            id="camera_new", name="camera_new", type="camera",
            version="1.0", ip="10.0.0.9",
        )
        mgr.module_discovery(new_module)

        assert mgr._modules["camera_new"] is new_module
        assert "camera_new" in mgr._config_states
