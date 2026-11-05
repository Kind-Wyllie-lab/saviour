"""
Tests for src/modules/variants/rfid/rfid_mock.py (the simulated RS485 bus)
and the RFIDModule wiring for rfid.mock.enabled.

The simulator runs on an injected clock whose sleep() just advances it, so
the tests are fast and deterministic apart from the movement RNG.
"""

from unittest.mock import MagicMock

from src.modules.tests.test_rfid_module import _cfg, _make_rfid
from src.modules.variants.rfid import rfid_module
from src.modules.variants.rfid.rfid_bus import (
    BROADCAST_ADDR,
    CMD_SERIAL_NUMBER,
    HOST_ADDR,
    RS485Bus,
    build_frame,
)
from src.modules.variants.rfid.rfid_mock import (
    MOCK_PORT,
    MockBusSerial,
    MockOptions,
    _parse_frames,
)
from src.modules.variants.rfid.rfid_sync import DbSettings


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def _mock(clock: _Clock, **opts) -> MockBusSerial:
    return MockBusSerial(MockOptions(**opts), clock=clock, sleep=clock.sleep)


class _Replay:
    """Serial stand-in that replays a byte string, then stops the loop."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.i = 0
        self.is_open = True

    def read(self, size: int = 1) -> bytes:
        if self.i >= len(self.data):
            raise _ReplayEndError
        b = self.data[self.i:self.i + size]
        self.i += size
        return b


class _ReplayEndError(Exception):
    pass


def _decode(data: bytes) -> tuple[list, dict]:
    bus = RS485Bus()
    reads = []
    bus.on_transponder_read = lambda u, tid, name, ts, code: reads.append(
        (u, tid, name, code))
    bus._serial = _Replay(data)
    try:
        bus._read_loop()
    except _ReplayEndError:
        pass
    return reads, bus.units


def _collect(ser: MockBusSerial, clock: _Clock, seconds: float) -> bytes:
    end = clock.t + seconds
    buf = bytearray()
    while clock.t < end:
        buf.extend(ser.read(4096))
    return bytes(buf)


def test_logon_gets_a_reply_from_every_unit():
    clock = _Clock()
    ser = _mock(clock, units=5, tags=0)
    ser.write(build_frame(HOST_ADDR, BROADCAST_ADDR))
    _, units = _decode(_collect(ser, clock, 0.5))
    assert sorted(units) == [1, 2, 3, 4, 5]
    assert all(u.firmware_version == "800" for u in units.values())


def test_serial_number_query_is_answered():
    clock = _Clock()
    ser = _mock(clock, units=2, tags=0)
    ser.write(build_frame(HOST_ADDR, 2, CMD_SERIAL_NUMBER))
    _, units = _decode(_collect(ser, clock, 0.5))
    assert units[2].serial_number == ser.unit_serials[2].hex().upper()


def test_unknown_unit_is_silent():
    clock = _Clock()
    ser = _mock(clock, units=2, tags=0)
    ser.write(build_frame(HOST_ADDR, 9, CMD_SERIAL_NUMBER))
    assert _collect(ser, clock, 0.5) == b""


def test_animals_produce_reads_at_known_units_with_their_tags():
    clock = _Clock()
    ser = _mock(clock, units=4, tags=3, ping_hz=10, zero_fraction=0.0)
    reads, _ = _decode(_collect(ser, clock, 60))
    assert len(reads) > 50
    tags = {a.tag for a in ser.animals}
    assert {tid for _, tid, _, _ in reads} <= tags
    assert {u for u, _, _, _ in reads} <= set(ser.unit_addrs)
    assert all(name == "Trovan Unique" and code == 0x20
               for _, _, name, code in reads)


def test_zero_fraction_one_sends_only_zero_ids():
    clock = _Clock()
    ser = _mock(clock, units=2, tags=2, zero_fraction=1.0)
    reads, _ = _decode(_collect(ser, clock, 30))
    assert reads and all(not any(tid) for _, tid, _, _ in reads)


def test_ids_are_stable_for_a_seed():
    a = _mock(_Clock(), units=3, tags=4, seed=7)
    b = _mock(_Clock(), units=3, tags=4, seed=7)
    c = _mock(_Clock(), units=3, tags=4, seed=8)
    assert [x.tag for x in a.animals] == [x.tag for x in b.animals]
    assert a.unit_serials == b.unit_serials
    assert [x.tag for x in a.animals] != [x.tag for x in c.animals]


def test_read_times_out_empty_with_no_tags():
    clock = _Clock()
    ser = _mock(clock, units=1, tags=0)
    start = clock.t
    assert ser.read(1) == b""
    assert clock.t - start >= ser.timeout


def test_closed_port_reads_nothing():
    ser = _mock(_Clock(), tags=2)
    ser.close()
    assert not ser.is_open
    assert ser.read(1) == b""


def test_options_from_config_clamps():
    get = _cfg({"rfid.mock.units": 999, "rfid.mock.ping_hz": 0,
                "rfid.mock.zero_fraction": 2, "rfid.mock.tags": -1})
    o = MockOptions.from_config(get)
    assert (o.units, o.ping_hz, o.zero_fraction, o.tags) == (254, 0.1, 1.0, 0)


def test_bus_connect_uses_opener_without_port_scan():
    bus = RS485Bus()
    bus.find_ports = MagicMock(side_effect=AssertionError("no scan"))
    ser = _mock(_Clock(), tags=0)
    assert bus.connect(port=MOCK_PORT, opener=lambda: ser)
    try:
        assert bus.is_connected and bus.port == MOCK_PORT
    finally:
        bus.disconnect()


# ── RFIDModule wiring ────────────────────────────────────────────────────


def test_module_mock_uses_separate_store_and_no_db():
    m = _make_rfid()
    m._db_settings = DbSettings(host="db", database="rfid", username="u")
    m.config.get.side_effect = _cfg({"rfid.store.path": "/x/reads.db"})
    assert m._store_path() == "/x/reads.db"
    assert m._sync_settings().configured

    m.config.get.side_effect = _cfg({"rfid.store.path": "/x/reads.db",
                                     "rfid.mock.enabled": True})
    assert m._store_path() == "/x/reads_mock.db"
    assert not m._sync_settings().configured
    # Same settings file either way.
    assert m._db_settings_path() == "/x/db_settings.json"


def test_module_mock_brings_up_simulated_bus():
    m = _make_rfid()
    m.config.get.side_effect = _cfg({"rfid.mock.enabled": True,
                                     "rfid.scan_on_start": False})
    m.bus.connect.return_value = True
    m._bring_up_bus()
    kwargs = m.bus.connect.call_args.kwargs
    assert kwargs["port"] == MOCK_PORT
    # rfid_module imports rfid_mock by bare name: a different class object.
    assert isinstance(kwargs["opener"](), rfid_module.MockBusSerial)
    assert m.hardware_fault is None


def test_module_without_mock_opens_real_port():
    m = _make_rfid()
    m.config.get.side_effect = _cfg({"rfid.serial_port": "/dev/ttyUSB0",
                                     "rfid.scan_on_start": False})
    m.bus.connect.return_value = True
    m._bring_up_bus()
    kwargs = m.bus.connect.call_args.kwargs
    assert kwargs["port"] == "/dev/ttyUSB0" and kwargs["opener"] is None


def test_toggling_mock_reopens_store():
    m = _make_rfid()
    m._stop_store = MagicMock()
    m._start_store = MagicMock()
    m._teardown_bus = MagicMock()
    m._bring_up_bus = MagicMock()
    m._start_sweeper = MagicMock()
    m.configure_module_special(["rfid.mock.enabled"])
    m._stop_store.assert_called_once()
    m._start_store.assert_called_once()
    m._bring_up_bus.assert_called_once()

    m._stop_store.reset_mock()
    m.configure_module_special(["rfid.baud"])
    m._stop_store.assert_not_called()


def test_parse_frames_handles_stuffed_dle_and_back_to_back_frames():
    # 0x10 (DLE) as a unit address is stuffed on the wire.
    data = (build_frame(HOST_ADDR, 0x10, CMD_SERIAL_NUMBER)
            + build_frame(HOST_ADDR, BROADCAST_ADDR))
    assert _parse_frames(data) == [(0x10, bytes([CMD_SERIAL_NUMBER])),
                                   (BROADCAST_ADDR, b"")]
