"""
Tests for src/controller/network.py.

Network.__init__ blocks on a real nmcli retry loop and constructs a real
Zeroconf object bound to the discovered IP, so every test here constructs
via Network.__new__(Network) (bypassing __init__) and sets only the
attributes the method under test reads -- same pattern as PTP.__new__ in
test_ptp.py. register_service()/cleanup() are tested with ServiceInfo/
ServiceBrowser/Zeroconf themselves mocked, since constructing the real
ones does actual mDNS network I/O.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.controller.network import Network


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_network(**attrs) -> Network:
    net = Network.__new__(Network)
    net.logger = MagicMock()
    net.config = _make_config()
    net.module_discovery_times = {}
    net.module_last_seen = {}
    net._zeroconf_name_to_id = {}
    net.interface = "eth0"
    net.ip_is_valid = False
    net.ip = "10.0.0.1"
    net.service_registered = False
    net.on_module_removed = None
    net.facade = MagicMock()
    for key, value in attrs.items():
        setattr(net, key, value)
    return net


def _make_service_info(*, module_id="cam1", name="cam1", version="1.0",
                        mtype="camera", ip="10.0.0.5", port=5555) -> MagicMock:
    info = MagicMock()
    info.properties = {
        b"id": module_id.encode(), b"name": name.encode(),
        b"version": version.encode(), b"type": mtype.encode(),
    }
    info.addresses = [__import__("socket").inet_aton(ip)]
    info.port = port
    return info


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestValidateIp:
    def test_accepts_configured_prefix(self):
        net = _make_network(config=_make_config(**{
            "zeroconf._valid_ip_prefixes": ["10.0."]
        }))
        assert net._validate_ip("10.0.0.42") is True
        assert net.ip_is_valid is True

    def test_rejects_unknown_prefix(self):
        net = _make_network(config=_make_config(**{
            "zeroconf._valid_ip_prefixes": ["10.0."]
        }))
        assert net._validate_ip("192.168.5.5") is False
        assert net.ip_is_valid is False

    def test_default_prefixes_when_unconfigured(self):
        net = _make_network(config=_make_config())
        assert net._validate_ip("192.168.1.50") is True


class TestProp:
    def test_decodes_present_property(self):
        assert Network._prop({b"id": b"cam1"}, b"id") == "cam1"

    def test_missing_key_returns_decoded_default(self):
        assert Network._prop({}, b"id") == "unknown"

    def test_none_value_returns_decoded_default(self):
        assert Network._prop({b"id": None}, b"id") == "unknown"

    def test_custom_default(self):
        assert Network._prop({}, b"name", default=b"anon") == "anon"


class TestGetModuleStatus:
    def test_returns_last_seen_discovery_time_and_uptime(self):
        net = _make_network()
        now = time.time()
        net.module_last_seen["cam1"] = now
        net.module_discovery_times["cam1"] = now - 30

        status = net.get_module_status("cam1")

        assert status["last_seen"] == now
        assert status["discovery_time"] == now - 30
        assert status["uptime"] >= 30

    def test_unknown_module_raises_instead_of_returning_none_fields(self):
        """Real bug, not a designed edge case: `discovery_time > 0` (~line
        273) compares None > 0 for a module never seen, raising TypeError
        instead of the None-filled dict the docstring/return type implies.
        Currently unreachable in production -- nothing in the codebase
        calls get_module_status() today -- but documents the crash rather
        than asserting the sensible behaviour the code doesn't actually have."""
        net = _make_network()
        with pytest.raises(TypeError):
            net.get_module_status("ghost")


class TestGetOwnIp:
    def test_returns_ip_when_already_valid(self):
        net = _make_network(ip_is_valid=True, ip="10.0.0.9")
        assert net.get_own_ip() == "10.0.0.9"

    def test_rescans_when_not_known_valid(self):
        net = _make_network(ip_is_valid=False)
        with patch.object(net, "_wait_for_proper_ip") as mock_wait:
            net.get_own_ip()
        mock_wait.assert_called_once()


# ---------------------------------------------------------------------------
# _get_eth0_ip_nm / _wait_for_proper_ip -- subprocess/retry-loop mocked
# ---------------------------------------------------------------------------

class TestGetEth0IpNm:
    def test_parses_ip_from_nmcli_output(self):
        net = _make_network()
        with patch(
            "src.controller.network.subprocess.run",
            return_value=MagicMock(stdout="10.0.0.7/24\n"),
        ):
            assert net._get_eth0_ip_nm() == "10.0.0.7"


class TestWaitForProperIp:
    def test_returns_immediately_on_first_valid_ip(self):
        net = _make_network()
        with patch.object(net, "_get_eth0_ip_nm", return_value="10.0.0.7"), \
             patch.object(net, "_validate_ip", return_value=True):
            ip = net._wait_for_proper_ip(max_wait_secs=5, retry_interval=1)
        assert ip == "10.0.0.7"

    def test_raises_after_timeout_when_never_valid(self):
        net = _make_network()
        with patch.object(net, "_get_eth0_ip_nm", return_value="1.2.3.4"), \
             patch.object(net, "_validate_ip", return_value=False), \
             patch("src.controller.network.time.sleep"):
            try:
                net._wait_for_proper_ip(max_wait_secs=0.01, retry_interval=0.01)
                raised = False
            except RuntimeError:
                raised = True
        assert raised is True


# ---------------------------------------------------------------------------
# Zeroconf callbacks -- add_service / update_service / remove_service
# ---------------------------------------------------------------------------

class TestAddService:
    def test_no_service_info_is_a_no_op(self):
        net = _make_network()
        zc = MagicMock()
        zc.get_service_info.return_value = None
        net.add_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")
        net.facade.module_discovery.assert_not_called()

    def test_discovers_module_and_updates_tracking(self):
        net = _make_network()
        zc = MagicMock()
        zc.get_service_info.return_value = _make_service_info(module_id="cam1")

        net.add_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")

        assert "cam1" in net.module_discovery_times
        assert "cam1" in net.module_last_seen
        assert net._zeroconf_name_to_id["cam1._module._tcp.local."] == "cam1"
        net.facade.module_discovery.assert_called_once()
        discovered = net.facade.module_discovery.call_args[0][0]
        assert discovered.id == "cam1"
        assert discovered.ip == "10.0.0.5"


class TestUpdateService:
    def test_no_service_info_is_a_no_op(self):
        net = _make_network()
        zc = MagicMock()
        zc.get_service_info.return_value = None
        net.update_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")
        net.facade.module_discovery.assert_not_called()
        net.facade.module_rediscovered.assert_not_called()

    def test_updates_last_seen_and_notifies_facade(self):
        net = _make_network()
        zc = MagicMock()
        zc.get_service_info.return_value = _make_service_info(module_id="cam1")

        net.update_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")

        assert "cam1" in net.module_last_seen
        net.facade.module_rediscovered.assert_called_once_with("cam1")
        net.facade.module_discovery.assert_called_once()


class TestRemoveService:
    def test_uses_live_service_info_when_available(self):
        net = _make_network(on_module_removed=MagicMock())
        zc = MagicMock()
        zc.get_service_info.return_value = _make_service_info(module_id="cam1")

        net.remove_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")

        net.on_module_removed.assert_called_once_with("cam1")

    def test_falls_back_to_cached_name_map_when_info_withdrawn(self):
        net = _make_network(on_module_removed=MagicMock())
        net._zeroconf_name_to_id["cam1._module._tcp.local."] = "cam1"
        zc = MagicMock()
        zc.get_service_info.return_value = None

        net.remove_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")

        net.on_module_removed.assert_called_once_with("cam1")
        assert "cam1._module._tcp.local." not in net._zeroconf_name_to_id

    def test_unknown_module_with_no_cached_mapping_is_a_no_op(self):
        net = _make_network(on_module_removed=MagicMock())
        zc = MagicMock()
        zc.get_service_info.return_value = None

        net.remove_service(zc, "_module._tcp.local.", "ghost._module._tcp.local.")

        net.on_module_removed.assert_not_called()

    def test_no_callback_registered_only_logs(self):
        net = _make_network(on_module_removed=None)
        zc = MagicMock()
        zc.get_service_info.return_value = _make_service_info(module_id="cam1")
        # must not raise
        net.remove_service(zc, "_module._tcp.local.", "cam1._module._tcp.local.")


# ---------------------------------------------------------------------------
# register_service / cleanup -- ServiceInfo/ServiceBrowser/Zeroconf mocked
# ---------------------------------------------------------------------------

class TestRegisterService:
    def test_already_registered_is_a_no_op(self):
        net = _make_network(service_registered=True)
        assert net.register_service() is True

    def test_registers_service_and_starts_browser(self):
        net = _make_network(zeroconf=MagicMock())
        with patch("src.controller.network.ServiceInfo") as mock_info_cls, \
             patch("src.controller.network.ServiceBrowser") as mock_browser_cls:
            net.service_port = 5353
            net.service_type = "_controller._tcp.local."
            net.service_name = "controller._controller._tcp.local."
            result = net.register_service()

        assert result is True
        assert net.service_registered is True
        net.zeroconf.register_service.assert_called_once_with(mock_info_cls.return_value)
        mock_browser_cls.assert_called_once()

    def test_exception_returns_false(self):
        net = _make_network(zeroconf=MagicMock())
        with patch(
            "src.controller.network.ServiceInfo", side_effect=RuntimeError("boom")
        ):
            assert net.register_service() is False


class TestCleanup:
    def test_unregisters_cancels_browser_and_clears_tracking(self):
        net = _make_network(zeroconf=MagicMock(), browser=MagicMock())
        net.service_info = MagicMock()
        net.module_discovery_times["cam1"] = time.time()
        net.module_last_seen["cam1"] = time.time()

        net.cleanup()

        net.zeroconf.unregister_service.assert_called_once_with(net.service_info)
        net.browser.cancel.assert_called_once()
        net.zeroconf.close.assert_called_once()
        assert net.module_discovery_times == {}
        assert net.module_last_seen == {}

    def test_swallows_exceptions(self):
        net = _make_network(zeroconf=MagicMock())
        net.service_info = MagicMock()
        net.zeroconf.unregister_service.side_effect = RuntimeError("already gone")
        net.cleanup()  # must not raise
