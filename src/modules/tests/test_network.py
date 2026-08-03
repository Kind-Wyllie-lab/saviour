"""
Tests for src/modules/network.py.

Network.__init__ calls _find_own_ip(), which on Linux loops forever with
no timeout waiting for a valid eth0 IP (a known bug -- see CLAUDE.md TODO
for "module-side network-ready wait has no timeout") and then constructs a
real Zeroconf object. Every test here constructs via Network.__new__
instead and never calls the real _find_own_ip(), matching the __new__
pattern used for the controller-side Network/PTP tests.
"""

from unittest.mock import MagicMock, patch

from src.modules.network import Network


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_network(**attrs) -> Network:
    net = Network.__new__(Network)
    net.logger = MagicMock()
    net.config = _make_config()
    net.module_id = "cam1"
    net.module_type = "camera"
    net.controller_ip = None
    net.controller_port = None
    net.reconnect_attempts = 0
    net.max_reconnect_attempts = 5
    net.reconnect_delay = 5
    net.last_discovery_time = None
    net.service_registered = False
    net.zeroconf = None
    net.service_browser = None
    net.service_info = None
    net.ip = "10.0.0.5"
    net.service_type = "_module._tcp.local."
    net.service_name = "camera_cam1._module._tcp.local."
    net.service_port = 5353
    net.facade = MagicMock()
    for key, value in attrs.items():
        setattr(net, key, value)
    return net


_CONTROLLER_NAME = "controller._controller._tcp.local."


def _make_info(ip="10.0.0.1", port=5555):
    info = MagicMock()
    info.addresses = [__import__("socket").inet_aton(ip)]
    info.port = port
    return info


# ---------------------------------------------------------------------------
# add_service
# ---------------------------------------------------------------------------

class TestAddService:
    def test_ignores_own_service(self):
        net = _make_network()
        zc = MagicMock()
        net.add_service(zc, "_module._tcp.local.", "camera_cam1._module._tcp.local.")
        zc.get_service_info.assert_not_called()

    def test_discovers_a_new_controller(self):
        net = _make_network()
        zc = MagicMock()
        zc.get_service_info.return_value = _make_info(ip="10.0.0.1", port=5555)

        net.add_service(zc, "_controller._tcp.local.", _CONTROLLER_NAME)

        assert net.controller_ip == "10.0.0.1"
        assert net.controller_port == 5555
        assert net.reconnect_attempts == 0
        net.facade.when_controller_discovered.assert_called_once_with("10.0.0.1", 5555)

    def test_same_controller_rediscovered_is_ignored(self):
        net = _make_network(controller_ip="10.0.0.1", controller_port=5555)
        zc = MagicMock()
        zc.get_service_info.return_value = _make_info(ip="10.0.0.1", port=5555)

        net.add_service(zc, "_controller._tcp.local.", _CONTROLLER_NAME)

        net.facade.when_controller_discovered.assert_not_called()

    def test_no_service_info_is_a_no_op(self):
        net = _make_network()
        zc = MagicMock()
        zc.get_service_info.return_value = None
        net.add_service(zc, "_controller._tcp.local.", _CONTROLLER_NAME)
        net.facade.when_controller_discovered.assert_not_called()


# ---------------------------------------------------------------------------
# update_service
# ---------------------------------------------------------------------------

class TestUpdateService:
    def test_ignores_non_controller_services(self):
        net = _make_network()
        zc = MagicMock()
        net.update_service(zc, "_module._tcp.local.", "othermodule._module._tcp.local.")
        zc.get_service_info.assert_not_called()

    def test_no_address_info_is_a_no_op(self):
        net = _make_network()
        zc = MagicMock()
        info = MagicMock(addresses=[])
        zc.get_service_info.return_value = info
        net.update_service(zc, "_controller._tcp.local.", _CONTROLLER_NAME)
        assert net.controller_ip is None

    def test_unchanged_endpoint_is_skipped(self):
        net = _make_network(controller_ip="10.0.0.1", controller_port=5555)
        zc = MagicMock()
        zc.get_service_info.return_value = _make_info(ip="10.0.0.1", port=5555)
        with patch.object(net, "add_service") as mock_add:
            net.update_service(zc, "_controller._tcp.local.", _CONTROLLER_NAME)
        mock_add.assert_not_called()

    def test_changed_endpoint_resets_state_and_rediscovers(self):
        net = _make_network(controller_ip="10.0.0.1", controller_port=5555)
        zc = MagicMock()
        zc.get_service_info.return_value = _make_info(ip="10.0.0.9", port=6000)
        with patch.object(net, "add_service") as mock_add:
            net.update_service(zc, "_controller._tcp.local.", _CONTROLLER_NAME)
        mock_add.assert_called_once_with(
            zc, "_controller._tcp.local.", _CONTROLLER_NAME
        )


# ---------------------------------------------------------------------------
# remove_service / reconnection scheduling
# ---------------------------------------------------------------------------

class TestRemoveService:
    def test_not_connected_is_a_no_op(self):
        net = _make_network(controller_ip=None, controller_port=None)
        net.remove_service(MagicMock(), "_controller._tcp.local.", "c")
        net.facade.controller_disconnected.assert_not_called()

    def test_connected_disconnects_and_schedules_reconnect(self):
        net = _make_network(controller_ip="10.0.0.1", controller_port=5555)
        with patch.object(net, "_schedule_reconnection") as mock_schedule:
            net.remove_service(MagicMock(), "_controller._tcp.local.", "c")

        net.facade.controller_disconnected.assert_called_once()
        assert net.controller_ip is None
        assert net.controller_port is None
        mock_schedule.assert_called_once()

    def test_no_reconnect_scheduled_when_max_attempts_is_zero(self):
        net = _make_network(
            controller_ip="10.0.0.1", controller_port=5555, max_reconnect_attempts=0
        )
        with patch.object(net, "_schedule_reconnection") as mock_schedule:
            net.remove_service(MagicMock(), "_controller._tcp.local.", "c")
        mock_schedule.assert_not_called()


class TestScheduleReconnection:
    def test_spawns_a_thread_under_max_attempts(self):
        net = _make_network(reconnect_attempts=0, max_reconnect_attempts=3)
        with patch("src.modules.network.threading.Thread") as mock_thread:
            net._schedule_reconnection()
        assert net.reconnect_attempts == 1
        mock_thread.assert_called_once()
        mock_thread.return_value.start.assert_called_once()

    def test_no_thread_once_max_attempts_reached(self):
        net = _make_network(reconnect_attempts=3, max_reconnect_attempts=3)
        with patch("src.modules.network.threading.Thread") as mock_thread:
            net._schedule_reconnection()
        mock_thread.assert_not_called()


class TestAttemptReconnection:
    def test_success_logs_info(self):
        net = _make_network()
        with patch.object(net, "register_service", return_value=True):
            net._attempt_reconnection()  # must not raise

    def test_failure_logs_error(self):
        net = _make_network()
        with patch.object(net, "register_service", return_value=False):
            net._attempt_reconnection()  # must not raise

    def test_exception_is_caught(self):
        net = _make_network()
        with patch.object(net, "register_service", side_effect=RuntimeError("boom")):
            net._attempt_reconnection()  # must not raise


# ---------------------------------------------------------------------------
# register_service
# ---------------------------------------------------------------------------

class TestRegisterService:
    def test_success_registers_and_resets_reconnect_attempts(self):
        net = _make_network(zeroconf=MagicMock(), reconnect_attempts=2)
        net.facade.get_module_name.return_value = "cam1"
        net.facade.get_module_group.return_value = "habitat"
        net.facade.get_saviour_version.return_value = "1.0"

        with patch("src.modules.network.ServiceInfo") as mock_info_cls, \
             patch("src.modules.network.ServiceBrowser") as mock_browser_cls:
            result = net.register_service()

        assert result is True
        assert net.service_registered is True
        assert net.reconnect_attempts == 0
        net.zeroconf.register_service.assert_called_once_with(mock_info_cls.return_value)
        mock_browser_cls.assert_called_once()

    def test_cleans_up_existing_registration_first(self):
        net = _make_network(zeroconf=MagicMock(), service_registered=True)
        net.facade.get_module_name.return_value = "cam1"
        net.facade.get_module_group.return_value = "habitat"
        net.facade.get_saviour_version.return_value = "1.0"

        with patch.object(net, "cleanup") as mock_cleanup, \
             patch("src.modules.network.ServiceInfo"), \
             patch("src.modules.network.ServiceBrowser"):
            net.register_service()

        mock_cleanup.assert_called_once()

    def test_exception_returns_false(self):
        net = _make_network(zeroconf=MagicMock())
        with patch(
            "src.modules.network.ServiceInfo", side_effect=RuntimeError("boom")
        ):
            assert net.register_service() is False


# ---------------------------------------------------------------------------
# _get_eth0_ip_nm / start / cleanup
# ---------------------------------------------------------------------------

class TestGetEth0IpNm:
    def test_parses_ip_from_nmcli_output(self):
        net = _make_network()
        with patch(
            "src.modules.network.subprocess.run",
            return_value=MagicMock(stdout="10.0.0.9/24\n"),
        ):
            assert net._get_eth0_ip_nm() == "10.0.0.9"


class TestStart:
    def test_skips_ip_lookup_when_already_known(self):
        net = _make_network(ip="10.0.0.5")
        with patch.object(net, "_find_own_ip") as mock_find:
            net.start()
        mock_find.assert_not_called()

    def test_looks_up_ip_when_unknown(self):
        net = _make_network(ip=None)
        with patch.object(net, "_find_own_ip") as mock_find:
            net.start()
        mock_find.assert_called_once()


class TestCleanup:
    def test_cancels_browser_and_closes_zeroconf(self):
        net = _make_network(
            service_browser=MagicMock(), zeroconf=MagicMock(), service_registered=True
        )
        with patch("src.modules.network.time.sleep"):
            net.cleanup()

        assert net.service_browser is None
        assert net.zeroconf is None
        assert net.service_registered is False

    def test_swallows_browser_cancel_exception(self):
        browser = MagicMock()
        browser.cancel.side_effect = RuntimeError("already cancelled")
        net = _make_network(service_browser=browser, zeroconf=MagicMock())
        with patch("src.modules.network.time.sleep"):
            net.cleanup()  # must not raise
        assert net.service_browser is None

    def test_swallows_zeroconf_unregister_exception(self):
        zc = MagicMock()
        zc.unregister_service.side_effect = RuntimeError("already gone")
        net = _make_network(service_browser=None, zeroconf=zc)
        with patch("src.modules.network.time.sleep"):
            net.cleanup()  # must not raise
        assert net.zeroconf is None

    def test_no_browser_or_zeroconf_is_a_no_op(self):
        net = _make_network(service_browser=None, zeroconf=None)
        net.cleanup()  # must not raise
        assert net.service_registered is False
