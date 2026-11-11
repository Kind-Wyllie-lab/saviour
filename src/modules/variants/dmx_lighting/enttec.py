"""
SAVIOUR - Enttec DMX USB Pro widget I/O.

The widget is an FTDI serial device with its own processor: the host sends
framed messages and the widget generates DMX timing, retransmitting the last
frame on its own until told otherwise. So we only send when levels change
(plus a periodic keepalive, which is also how an unplugged widget is
noticed).

Message frame: 0x7E, label, len_lsb, len_msb, data..., 0xE7.
  * label 6  Output Only Send DMX Packet: start code 0x00 + 24..512 bytes
  * label 3  Get Widget Parameters: firmware, break/MAB time, refresh rate
  * label 10 Get Widget Serial Number: 4 bytes, BCD, little-endian

Not for the "Open DMX USB" (no processor; the host bit-bangs DMX timing).

:class:`MockWidget` stands in for the serial port (``dmx_lighting.mock.
enabled``, and the tests): it answers labels 3 and 10 and keeps the last
frame it was sent.

Author: Andrew SG
Created: 11/11/2026
"""

from __future__ import annotations

import glob
import logging
import threading
import time

SOM, EOM = 0x7E, 0xE7
LABEL_GET_PARAMS = 3
LABEL_SEND_DMX = 6
LABEL_GET_SERIAL = 10
MIN_DMX = 24
MAX_DMX = 512
MOCK_PORT = "mock://enttec"
BY_ID_GLOBS = (
    "/dev/serial/by-id/*ENTTEC*DMX_USB_PRO*",
    "/dev/serial/by-id/*DMX_USB_PRO*",
)


class WidgetError(Exception):
    pass


def encode(label: int, data: bytes = b"") -> bytes:
    n = len(data)
    return bytes([SOM, label, n & 0xFF, n >> 8]) + bytes(data) + bytes([EOM])


def dmx_packet(channels: bytes) -> bytes:
    """Label 6 message for DMX channels 1..n (padded to the 24 minimum)."""
    if len(channels) > MAX_DMX:
        raise ValueError(f"at most {MAX_DMX} channels")
    body = bytes(channels) + bytes(max(0, MIN_DMX - len(channels)))
    return encode(LABEL_SEND_DMX, b"\x00" + body)


def parse_params(data: bytes) -> dict:
    """Label 3 reply -> firmware, break/MAB time (µs), refresh rate (Hz)."""
    if len(data) < 5:
        raise WidgetError(f"short widget-parameters reply ({len(data)} bytes)")
    return {
        "firmware": f"{data[1]}.{data[0]}",
        "break_us": round(data[2] * 10.67, 1),
        "mab_us": round(data[3] * 10.67, 1),
        "refresh_hz": data[4],
    }


def parse_serial(data: bytes) -> str:
    """Label 10 reply: BCD little-endian, so the hex digits read as decimal."""
    if len(data) < 4:
        raise WidgetError(f"short serial-number reply ({len(data)} bytes)")
    return "".join(f"{b:02X}" for b in reversed(data[:4])).lstrip("0") or "0"


def find_port(configured: str = "") -> str | None:
    """The configured port, else the first Enttec DMX USB Pro by-id path."""
    if configured:
        return configured
    for pattern in BY_ID_GLOBS:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def _open_serial(port: str):
    import serial  # pyserial; imported here so tests/mock need no device

    # Baud rate is ignored by the widget (USB virtual COM).
    return serial.Serial(port, baudrate=57600, timeout=0.5, write_timeout=1.0)


class EnttecPro:
    """One open widget. Not thread-safe; the module's output thread owns it."""

    def __init__(self, port: str, *, serial_factory=None, logger=None):
        self.port = port
        self._factory = serial_factory or _open_serial
        self.logger = logger or logging.getLogger(__name__)
        self._ser = None
        self.params: dict = {}
        self.serial_number: str | None = None
        self.frames_sent = 0
        self.last_send_monotonic: float | None = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None

    def open(self) -> None:
        """Open the port and check the widget answers (label 10 + 3)."""
        self._ser = self._factory(self.port)
        try:
            try:
                self._ser.reset_input_buffer()
            except Exception:  # noqa: BLE001 -- not every stand-in has it
                pass
            self.serial_number = parse_serial(self.request(LABEL_GET_SERIAL))
            self.params = parse_params(self.request(LABEL_GET_PARAMS, b"\x00\x00"))
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception as e:  # noqa: BLE001 -- closing a dead port
                self.logger.debug(f"DMX: error closing {self.port}: {e}")

    def request(self, label: int, data: bytes = b"", timeout_s: float = 1.0) -> bytes:
        """Send a request and return the data of the reply with that label."""
        if self._ser is None:
            raise WidgetError("widget not open")
        self._ser.write(encode(label, data))
        return self._read_reply(label, timeout_s)

    def _read_reply(self, label: int, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self._ser.read(64)
            if chunk:
                buf.extend(chunk)
                msg = _extract(buf, label)
                if msg is not None:
                    return msg
        raise WidgetError(f"no reply to label {label} from {self.port} "
                          "(is it a DMX USB Pro?)")

    def send(self, channels: bytes) -> None:
        if self._ser is None:
            raise WidgetError("widget not open")
        self._ser.write(dmx_packet(channels))
        try:
            self._ser.flush()
        except Exception:  # noqa: BLE001 -- flush is advisory
            pass
        self.frames_sent += 1
        self.last_send_monotonic = time.monotonic()

    def info(self) -> dict:
        return {"port": self.port, "serial": self.serial_number, **self.params,
                "frames_sent": self.frames_sent}


def _extract(buf: bytearray, label: int) -> bytes | None:
    """Pull the first complete message with ``label`` out of ``buf``."""
    while True:
        try:
            start = buf.index(SOM)
        except ValueError:
            buf.clear()
            return None
        if start:
            del buf[:start]
        if len(buf) < 4:
            return None
        n = buf[2] | (buf[3] << 8)
        if len(buf) < 5 + n:
            return None
        if buf[4 + n] != EOM:
            del buf[0]          # false start; resync on the next 0x7E
            continue
        got_label, data = buf[1], bytes(buf[4:4 + n])
        del buf[:5 + n]
        if got_label == label:
            return data


class MockWidget:
    """Serial stand-in for a DMX USB Pro: answers labels 3 and 10, records
    every DMX frame. ``unplugged = True`` makes every call fail."""

    SERIAL_BCD = bytes([0x78, 0x56, 0x34, 0x12])   # serial "12345678"
    PARAMS = bytes([0x44, 0x01, 9, 1, 40])         # fw 1.68, 96 µs, 10.7 µs, 40 Hz

    def __init__(self, port: str = MOCK_PORT):
        self.port = port
        self.unplugged = False
        self.frames: list[bytes] = []
        self._out = bytearray()
        self._lock = threading.Lock()
        self.closed = False

    @property
    def last_frame(self) -> bytes | None:
        return self.frames[-1] if self.frames else None

    def _check(self):
        if self.unplugged or self.closed:
            raise OSError(5, "Input/output error (mock widget unplugged)")

    def write(self, data: bytes) -> int:
        self._check()
        buf = bytearray(data)
        while buf:
            if buf[0] != SOM or len(buf) < 5:
                break
            n = buf[2] | (buf[3] << 8)
            label, body = buf[1], bytes(buf[4:4 + n])
            del buf[:5 + n]
            with self._lock:
                if label == LABEL_SEND_DMX:
                    self.frames.append(body[1:])       # drop the start code
                    del self.frames[:-50]
                elif label == LABEL_GET_SERIAL:
                    self._out.extend(encode(LABEL_GET_SERIAL, self.SERIAL_BCD))
                elif label == LABEL_GET_PARAMS:
                    self._out.extend(encode(LABEL_GET_PARAMS, self.PARAMS))
        return len(data)

    def read(self, n: int = 1) -> bytes:
        self._check()
        with self._lock:
            out = bytes(self._out[:n])
            del self._out[:n]
        if not out:
            time.sleep(0.005)
        return out

    def flush(self):
        self._check()

    def reset_input_buffer(self):
        with self._lock:
            self._out.clear()

    def close(self):
        self.closed = True
