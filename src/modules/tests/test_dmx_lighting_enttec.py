"""Enttec DMX USB Pro framing and the mock widget (variants/dmx_lighting/enttec.py)."""

import pytest

from src.modules.variants.dmx_lighting import enttec


def test_encode_frame():
    assert enttec.encode(10) == bytes([0x7E, 10, 0, 0, 0xE7])
    msg = enttec.encode(6, b"\x00" + bytes(range(3)))
    assert msg == bytes([0x7E, 6, 4, 0, 0, 0, 1, 2, 0xE7])


def test_dmx_packet_pads_to_24_and_caps_at_512():
    pkt = enttec.dmx_packet(b"\xff")
    assert pkt[1] == 6
    assert pkt[2] | (pkt[3] << 8) == 25          # start code + 24 channels
    assert pkt[4] == 0 and pkt[5] == 0xFF and pkt[-1] == 0xE7
    full = enttec.dmx_packet(bytes(512))
    assert full[2] | (full[3] << 8) == 513
    with pytest.raises(ValueError):
        enttec.dmx_packet(bytes(513))


def test_parse_replies():
    assert enttec.parse_serial(bytes([0x78, 0x56, 0x34, 0x12])) == "12345678"
    p = enttec.parse_params(bytes([0x44, 0x01, 9, 1, 40]))
    assert p == {"firmware": "1.68", "break_us": 96.0, "mab_us": 10.7, "refresh_hz": 40}


def test_extract_skips_noise_and_other_labels():
    buf = bytearray(b"\x01\x02" + enttec.encode(3, b"ab") + enttec.encode(10, b"\x01\x00\x00\x00"))
    assert enttec._extract(buf, 10) == b"\x01\x00\x00\x00"
    buf = bytearray(enttec.encode(10, b"xy")[:-1])      # incomplete
    assert enttec._extract(buf, 10) is None


def test_widget_against_mock():
    mock = enttec.MockWidget()
    w = enttec.EnttecPro(enttec.MOCK_PORT, serial_factory=lambda _p: mock)
    w.open()
    assert w.serial_number == "12345678"
    assert w.params["refresh_hz"] == 40
    frame = bytes([10, 20]) + bytes(510)
    w.send(frame)
    assert mock.last_frame == frame
    assert w.frames_sent == 1


def test_unplugged_mock_fails_open_and_send():
    mock = enttec.MockWidget()
    w = enttec.EnttecPro(enttec.MOCK_PORT, serial_factory=lambda _p: mock)
    w.open()
    mock.unplugged = True
    with pytest.raises(OSError):
        w.send(bytes(512))
    mock2 = enttec.MockWidget()
    mock2.unplugged = True
    w2 = enttec.EnttecPro(enttec.MOCK_PORT, serial_factory=lambda _p: mock2)
    with pytest.raises(OSError):
        w2.open()
    assert not w2.is_open


def test_find_port_prefers_config(monkeypatch):
    assert enttec.find_port("/dev/ttyUSB3") == "/dev/ttyUSB3"
    monkeypatch.setattr(enttec.glob, "glob", lambda pat: ["/dev/serial/by-id/usb-ENTTEC_DMX_USB_PRO_EN1-if00-port0"] if "ENTTEC" in pat else [])
    assert enttec.find_port("").endswith("EN1-if00-port0")
    monkeypatch.setattr(enttec.glob, "glob", lambda pat: [])
    assert enttec.find_port("") is None
