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


class TestModuleDiscoveryRefreshesNetworkIdentity:
    """Regression coverage for a bug found 2026-08-17: a module whose
    received_module_config() auto-registered it with ip="" (its ZMQ hello/config
    reached the controller before mDNS ever discovered it) stayed at ip="" forever
    -- module_discovery()'s early-return for already-known IDs discarded every
    subsequent real mDNS announcement's ip/port/zeroconf_name, and
    module_ip_changed() (the only other writer of .ip) is never called from
    anywhere. Symptom: module shows online with heartbeats but no IP in the
    frontend, and the livestream URL (built directly from module.ip) breaks."""

    def test_blank_ip_is_corrected_by_a_real_mdns_announcement(self):
        mgr = _make_modules()
        existing = Module(
            id="camera_abc", name="camera_abc", type="camera", version="", ip=""
        )
        mgr.add_module(existing)
        existing.status = ModuleStatus.RECORDING  # must survive the refresh

        real_announcement = Module(
            id="camera_abc", name="camera_abc", type="camera", version="1.0",
            ip="10.0.0.237", port=5353, zeroconf_name="camera_abc._module._tcp.local.",
        )
        mgr.module_discovery(real_announcement)

        tracked = mgr._modules["camera_abc"]
        assert tracked.ip == "10.0.0.237"
        assert tracked.port == 5353
        assert tracked.zeroconf_name == "camera_abc._module._tcp.local."
        assert tracked.status == ModuleStatus.RECORDING
        assert tracked is existing

    def test_dhcp_reassigned_ip_is_refreshed_on_re_announcement(self):
        mgr = _make_modules()
        existing = _register(mgr)  # ip="10.0.0.2"
        existing.status = ModuleStatus.RECORDING
        existing.config = {"camera": {"fps": 30}}

        reannounced = Module(
            id="camera_abc", name="camera_abc", type="camera",
            version="1.0", ip="10.0.0.55",
        )
        mgr.module_discovery(reannounced)

        tracked = mgr._modules["camera_abc"]
        assert tracked.ip == "10.0.0.55"
        # Non-identity state must still be preserved
        assert tracked.status == ModuleStatus.RECORDING
        assert tracked.config == {"camera": {"fps": 30}}

    def test_blank_version_is_filled_by_the_mdns_announcement(self):
        """Seen after a fleet deploy (2026-11-11): the microphone's config reply
        reached the restarted controller before its mDNS announcement, so it was
        auto-registered with version "" and the announcement only refreshed the
        IP -- the module showed no version until the next controller restart."""
        mgr = _make_modules()
        existing = Module(id="microphone_4703", name="microphone_4703",
                          type="microphone", version="", ip="")
        mgr.add_module(existing)
        existing.status = ModuleStatus.RECORDING
        mgr.module_discovery(Module(
            id="microphone_4703", name="A", type="microphone",
            version="v0.9-826-g4bbd8315", ip="10.0.0.156",
        ))
        tracked = mgr._modules["microphone_4703"]
        assert tracked.version == "v0.9-826-g4bbd8315"
        assert tracked.ip == "10.0.0.156"
        assert tracked.status == ModuleStatus.RECORDING
        assert tracked is existing

    def test_announcement_without_a_version_keeps_the_known_one(self):
        mgr = _make_modules()
        existing = _register(mgr)  # version="1.0"
        mgr.module_discovery(Module(id="camera_abc", name="camera_abc",
                                    type="camera", version="", ip="10.0.0.2"))
        assert existing.version == "1.0"
