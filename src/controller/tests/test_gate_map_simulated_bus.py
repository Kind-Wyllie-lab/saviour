"""
End to end, with no readers: the habitat gate-mapping service
(gate_map_service.py) driving a real RFIDModule whose bus is the simulated
one (rfid_mock.py), as the Gates page's Simulated bus panel does.

Everything below the Socket.IO layer is the real code: the simulated bus
speaks the reader protocol to a real RS485Bus (its reader and query
threads), the module's command methods answer as cmd_acks, its rfid_read
messages reach the service, and the service runs a MappingRun. Only ZMQ is
replaced by direct calls, and the scan timers are sped up.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "variants",
                                "habitat"))

import gate_map as gm  # noqa: E402 -- needs the sys.path entry above
from gate_map_service import GateMapService  # noqa: E402

from src.modules.tests.test_rfid_module import _cfg, _make_rfid  # noqa: E402
from src.modules.variants.rfid import rfid_module  # noqa: E402

MODULE = "rfid_sim"
SPEED = 0.15            # scan timers run at this fraction of real time


class _Rig:
    def __init__(self, tmp_path, units=4):
        self.errors = []
        m = _make_rfid()
        m.config.get.side_effect = _cfg({
            "rfid.mock.enabled": True, "rfid.mock.units": units,
            "rfid.mock.tags": 2, "rfid.scan_on_start": False})
        # rfid_module imports rfid_bus by bare name: use its RS485Bus.
        m.bus = rfid_module.RS485Bus()
        m.bus.on_transponder_read = m._on_transponder_read
        m.communication.controller_ip = "10.0.0.1"
        m.communication.send_status.side_effect = self._status
        m._bring_up_bus()
        assert m.bus.is_connected and m._mock_bus is not None
        m._mock_bus.animals_paused = True          # no stray tags
        self.module = m

        def timer(delay, fn, args=(), kwargs=None):
            # The scan follow-ups sped up; the settle window kept real, as
            # a gate's injected reads take a few tenths of a second.
            if delay != gm.SETTLE_S:
                delay *= SPEED
            return threading.Timer(delay, fn, args, kwargs)

        self.svc = GateMapService(
            gm.GateMapStore(str(tmp_path)),
            emit=self._emit, send_command=self._send,
            rfid_module_ids=lambda: [MODULE], timer=timer)

    def close(self):
        self.module.bus.disconnect()

    # The module's half of the controller link.
    def _send(self, module_id, command, params):
        assert module_id == MODULE
        result = getattr(self.module, command)(**params)
        self._status({"type": "cmd_ack", "command": command, **result})

    def _status(self, payload):
        self.svc.on_module_status(MODULE, payload)

    def _emit(self, event, data):
        if event == "gate_map_error":
            self.errors.append(data["error"])

    def wait(self, cond, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            time.sleep(0.02)
        raise AssertionError("timed out")

    def run(self):
        return self.svc.state()["run"]

    def read(self, unit, tag=None):
        self.svc.mock_read(MODULE, unit, tag)

    def pass_probe(self, *units):
        """Hold the probe at these antennas, then wait for the gate to settle."""
        for u in units:
            self.read(u)
        self.wait(lambda: self.run()["capture"]["settled"])


@pytest.fixture
def rig(tmp_path):
    r = _Rig(tmp_path)
    yield r
    r.close()


def test_mapping_run_on_the_simulated_bus(rig):
    rig.svc.refresh_mock()
    assert [b["module_id"] for b in rig.svc.state()["mock_buses"]] == [MODULE]
    probe = rig.svc.state()["mock_buses"][0]["probe_tag"]
    animal = rig.svc.state()["mock_buses"][0]["animal_tag"]

    rig.svc.start_run("test", {"wall": "A1B1"})
    # The scan answered with every unit and, once its queries went out,
    # each serial number.
    rig.wait(lambda: rig.svc.bus is not None and len(rig.svc.bus) == 4
             and all(b.serial for b in rig.svc.bus.values()))

    rig.read(1)                                     # registers the probe
    rig.wait(lambda: rig.run()["phase"] == "gate")
    assert rig.run()["probe_tag"] == probe
    time.sleep(0.6)                                 # the held tag's other reads
    assert rig.run()["capture"]["first"] is None    # don't count for A1B1_a
    assert rig.run()["probe_at"] == f"{MODULE}:1"
    time.sleep(gm.PROBE_AWAY_S)                     # taken away, then back

    rig.pass_probe(1)                               # A1B1_a <- unit 1
    assert rig.run()["issue"] is None
    rig.svc.act("confirm")
    assert rig.run()["gate"] == "A1B1_b"

    rig.read(2, animal)                             # an animal: ignored
    time.sleep(0.5)
    assert rig.run()["capture"]["first"] is None

    rig.pass_probe(1)                               # unit 1 again: caught
    assert rig.run()["issue"] == {"kind": "already_mapped",
                                  "unit": f"{MODULE}:1", "gates": ["A1B1_a"]}
    with pytest.raises(gm.GateMapError, match="move it or share it"):
        rig.svc.act("confirm")
    rig.svc.act("retry")

    rig.pass_probe(2, 3)                            # two antennas read it
    assert rig.run()["issue"]["kind"] == "multiple"
    rig.svc.act("confirm", unit=f"{MODULE}:2")

    rig.pass_probe(3)
    rig.svc.act("confirm")
    rig.svc.act("skip_closed")                      # A1B1_d
    assert rig.run()["phase"] == "review"
    assert rig.run()["review"]["on_bus_not_mapped"] == [f"{MODULE}:4"]
    rig.svc.save_run()
    entries = rig.svc.map["entries"]
    assert [entries[g].get("unit") for g in ("A1B1_a", "A1B1_b", "A1B1_c")] == [1, 2, 3]
    serial_1 = entries["A1B1_a"]["unit_serial"]
    assert serial_1 == rig.module._mock_bus.unit_serials[1].hex().upper()
    assert rig.errors == []


def test_bus_checks_catch_an_unplugged_unit_and_a_swapped_decoder(rig):
    rig.svc.refresh_mock()
    rig.svc.start_run("test", {"gates": ["A1B1_a", "A1B1_b"]})
    rig.wait(lambda: rig.svc.bus and all(b.serial for b in rig.svc.bus.values())
             and len(rig.svc.bus) == 4)
    rig.read(4)                                     # register at the spare
    rig.wait(lambda: rig.run()["phase"] == "gate")
    rig.pass_probe(1)
    rig.svc.act("confirm")
    rig.pass_probe(2)
    rig.svc.act("confirm")
    rig.svc.save_run()

    rig.svc.mock_unit(MODULE, 2, plugged=False)
    rig.svc.mock_unit(MODULE, 1, swap=True)
    new_serial = rig.module._mock_bus.unit_serials[1].hex().upper()
    rig.svc.scan()
    rig.wait(lambda: rig.svc.state()["check"]["mapped_not_on_bus"]
             and rig.svc.state()["check"]["serial_changed"])
    check = rig.svc.state()["check"]
    assert check["mapped_not_on_bus"] == [{"unit": f"{MODULE}:2", "gates": ["A1B1_b"]}]
    assert [(c["gate"], c["bus_serial"]) for c in check["serial_changed"]] == [
        ("A1B1_a", new_serial)]

    rig.svc.mock_read(MODULE, 2)                    # unplugged: refused, reported
    rig.wait(lambda: rig.errors)
    assert "unplugged" in rig.errors[-1]
