"""
Contract tests between src/controller/network.py and src/modules/network.py.

The two Network classes never import each other -- they agree on zeroconf
service-type strings and property keys only by convention (each side hardcodes
what it expects the other side to use). src/controller/tests/test_network.py
and src/modules/tests/test_network.py each mock the *other* side entirely, so
neither catches it if the two sides drift apart, e.g. the controller starts
browsing for "_modules._tcp.local." while a module still registers itself as
"_module._tcp.local." -- discovery would then silently never fire, with no
exception anywhere.

These tests instantiate both real Network classes (network/zeroconf I/O
mocked, same __new__ pattern used in test_network.py) and assert the two
sides' literal strings and property keys actually line up.
"""

from unittest.mock import MagicMock, patch

from src.controller.network import Network as ControllerNetwork
from src.modules.network import Network as ModuleNetwork


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_controller_network() -> ControllerNetwork:
    net = ControllerNetwork.__new__(ControllerNetwork)
    net.logger = MagicMock()
    net.config = _make_config()
    net.module_discovery_times = {}
    net.module_last_seen = {}
    net._zeroconf_name_to_id = {}
    net.ip = "10.0.0.1"
    net.ip_is_valid = True
    net.service_registered = False
    net.zeroconf = MagicMock()
    net.service_port = net.config.get("zeroconf.port", 5353)
    net.service_type = net.config.get("zeroconf.service_type", "_controller._tcp.local.")
    net.service_name = "controller_test._controller._tcp.local."
    net.facade = MagicMock()
    return net


def _make_module_network() -> ModuleNetwork:
    net = ModuleNetwork.__new__(ModuleNetwork)
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
    net.zeroconf = MagicMock()
    net.service_browser = None
    net.service_info = None
    net.ip = "10.0.0.5"
    net.service_type = net.config.get("network.zeroconf_service_type", "_module._tcp.local.")
    net.service_name = f"{net.module_type}_{net.module_id}._module._tcp.local."
    net.service_port = 5353
    net.facade = MagicMock()
    return net


class TestServiceTypeContract:
    """controller/network.py's register_service() hardcodes ServiceBrowser(...,
    "_module._tcp.local.", ...) and modules/network.py's register_service()
    hardcodes ServiceBrowser(..., "_controller._tcp.local.", ...). Each must
    match the *other* side's own (configurable) service_type."""

    def test_controller_browses_the_type_modules_register_as(self):
        controller = _make_controller_network()
        module = _make_module_network()

        with patch("src.controller.network.ServiceInfo"), \
             patch("src.controller.network.ServiceBrowser") as mock_browser_cls:
            controller.register_service()

        browsed_type = mock_browser_cls.call_args[0][1]
        assert browsed_type == module.service_type

    def test_module_browses_the_type_controller_registers_as(self):
        controller = _make_controller_network()
        module = _make_module_network()

        with patch("src.modules.network.ServiceInfo"), \
             patch("src.modules.network.ServiceBrowser") as mock_browser_cls:
            module.register_service()

        browsed_type = mock_browser_cls.call_args[0][1]
        assert browsed_type == controller.service_type


class TestPropertyKeyContract:
    """Network._prop() calls in controller/network.py's add_service/update_service
    decode b'id', b'name', b'version' and b'type' out of a discovered module's
    ServiceInfo.properties. If the module ever stopped sending one of these keys,
    discovery wouldn't error -- it would silently fall back to 'unknown'. Assert
    the module's registration actually sends every key the controller reads."""

    CONTROLLER_READ_KEYS = {"id", "name", "version", "type"}

    def test_module_registers_every_property_key_the_controller_reads(self):
        module = _make_module_network()

        with patch("src.modules.network.ServiceInfo") as mock_info_cls, \
             patch("src.modules.network.ServiceBrowser"):
            module.register_service()

        registered_properties = mock_info_cls.call_args.kwargs["properties"]
        assert self.CONTROLLER_READ_KEYS <= set(registered_properties.keys())
