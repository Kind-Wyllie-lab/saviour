"""
Simulated LID650/665 RS485 bus, for working on the RFID module and its
frontend without a reader attached (``rfid.mock.enabled``).

``MockBusSerial`` stands in for the ``serial.Serial`` that ``RS485Bus`` reads
and writes, and speaks the real frame protocol, so the whole receive path
(DLE framing, checksum, dispatch, logon, serial-number queries) runs unchanged:

  * a broadcast logon gets a 3-digit firmware reply from every simulated unit,
    ``J`` (serial number) and ``V`` (version) queries get answers, and ``x``
    (restart) gets a warm-startup frame;
  * ``tags`` animals wander between ``units`` readers. Each sits at one unit
    for a few seconds pinging at ``ping_hz``, leaves, and turns up a while
    later at a neighbouring unit, which gives presence tracking realistic
    enter / exit visits;
  * ``zero_fraction`` of reads carry an all-zero ID, like the real bus.

Everything is driven from ``read()`` on the bus's own reader thread; there is
no extra thread. Tag IDs and unit serial numbers come from ``seed``, so they
stay the same across restarts.
"""

import random
import threading
import time
from dataclasses import dataclass

from rfid_bus import (
    BROADCAST_ADDR,
    CMD_RESTART,
    CMD_SERIAL_NUMBER,
    CMD_VERSION,
    DLE,
    ETX,
    HOST_ADDR,
    STARTUP_WARM,
    STX,
    _destuff,
    build_frame,
)

# Shown as the bus "port" (health check, stream header) so a simulated bus
# is never mistaken for a real one.
MOCK_PORT = "simulated"
MOCK_FIRMWARE = "800"
_TYPE_TROVAN_UNIQUE = 0x20


@dataclass
class MockOptions:
    units: int = 6
    tags: int = 4
    ping_hz: float = 5.0
    dwell_s: tuple[float, float] = (1.5, 8.0)
    away_s: tuple[float, float] = (3.0, 20.0)
    zero_fraction: float = 0.05
    seed: int = 1

    @classmethod
    def from_config(cls, get) -> "MockOptions":
        """Build from a ``config.get``-style callable (``rfid.mock.*`` keys),
        clamping each value to something the simulator can run."""
        d = cls()
        return cls(
            units=min(254, max(1, int(get("rfid.mock.units", d.units)))),
            tags=max(0, int(get("rfid.mock.tags", d.tags))),
            ping_hz=min(50.0, max(0.1, float(get("rfid.mock.ping_hz", d.ping_hz)))),
            zero_fraction=min(1.0, max(0.0, float(
                get("rfid.mock.zero_fraction", d.zero_fraction)))),
            seed=int(get("rfid.mock.seed", d.seed)),
        )


@dataclass
class _Animal:
    tag: bytes
    unit: int
    present: bool
    next_change: float
    next_ping: float


class MockBusSerial:
    """The subset of ``serial.Serial`` that ``RS485Bus`` uses: ``read(1)``,
    ``write()``, ``close()``, ``is_open``."""

    def __init__(self, opts: MockOptions | None = None, timeout: float = 0.1,
                 clock=time.time, sleep=time.sleep) -> None:
        self.opts = opts or MockOptions()
        self.timeout = timeout
        self.is_open = True
        self._clock = clock
        self._sleep = sleep
        self._rng = random.Random(self.opts.seed)
        self._out = bytearray()
        self._lock = threading.Lock()   # write() comes from other threads

        # Unit addresses start at 1 (0x00 and 0xFF are reserved).
        self.unit_addrs = list(range(1, self.opts.units + 1))
        self.unit_serials = {
            a: bytes(self._rng.randrange(256) for _ in range(4))
            for a in self.unit_addrs}
        now = self._clock()
        self.animals = [
            _Animal(tag=bytes(self._rng.randrange(256) for _ in range(5)),
                    unit=self._rng.choice(self.unit_addrs),
                    present=False,
                    next_change=now + self._rng.uniform(0.5, 4.0),
                    next_ping=now)
            for _ in range(self.opts.tags)]
        # Different stream per run for the movements, same IDs.
        self._rng = random.Random()

    # ── serial.Serial interface ──────────────────────────────────────────

    def read(self, size: int = 1) -> bytes:
        deadline = self._clock() + self.timeout
        while self.is_open:
            self._advance(self._clock())
            with self._lock:
                if self._out:
                    chunk = bytes(self._out[:size])
                    del self._out[:size]
                    return chunk
            now = self._clock()
            if now >= deadline:
                return b""
            self._sleep(max(0.0, min(deadline, self._next_event()) - now))
        return b""

    def write(self, data: bytes) -> int:
        for unit_to, content in _parse_frames(bytes(data)):
            self._answer(unit_to, content)
        return len(data)

    def close(self) -> None:
        self.is_open = False

    # ── Host commands ────────────────────────────────────────────────────

    def _answer(self, unit_to: int, content: bytes) -> None:
        if unit_to == BROADCAST_ADDR and not content:      # logon
            for a in self.unit_addrs:
                self._emit(a, HOST_ADDR, MOCK_FIRMWARE.encode("ascii"))
            return
        if unit_to not in self.unit_serials or not content:
            return
        cmd = content[0]
        if cmd == CMD_SERIAL_NUMBER:
            self._emit(unit_to, HOST_ADDR,
                       bytes([CMD_SERIAL_NUMBER]) + self.unit_serials[unit_to])
        elif cmd == CMD_VERSION:
            self._emit(unit_to, HOST_ADDR,
                       bytes([CMD_VERSION]) + b"LID665 mock " + MOCK_FIRMWARE.encode())
        elif cmd == CMD_RESTART:
            self._emit(unit_to, BROADCAST_ADDR,
                       bytes([STARTUP_WARM]) + MOCK_FIRMWARE.encode("ascii"))

    def _emit(self, unit_from: int, unit_to: int, content: bytes) -> None:
        frame = build_frame(unit_from, unit_to,
                            content[0] if content else None, content[1:])
        with self._lock:
            self._out.extend(frame)

    # ── Simulation ───────────────────────────────────────────────────────

    def _next_event(self) -> float:
        times = [a.next_change for a in self.animals]
        times += [a.next_ping for a in self.animals if a.present]
        return min(times, default=float("inf"))

    def _advance(self, now: float) -> None:
        period = 1.0 / self.opts.ping_hz
        for a in self.animals:
            if now >= a.next_change:
                if a.present:
                    a.present = False
                    a.next_change = now + self._rng.uniform(*self.opts.away_s)
                else:
                    a.unit = self._neighbour(a.unit)
                    a.present = True
                    a.next_ping = now
                    a.next_change = now + self._rng.uniform(*self.opts.dwell_s)
            if a.present and now >= a.next_ping:
                tag = a.tag
                if self._rng.random() < self.opts.zero_fraction:
                    tag = bytes(len(a.tag))
                self._emit(a.unit, BROADCAST_ADDR,
                           bytes([_TYPE_TROVAN_UNIQUE]) + tag)
                # A little jitter, as a real reader's read cycle isn't fixed.
                a.next_ping = now + period * self._rng.uniform(0.7, 1.3)

    def _neighbour(self, unit: int) -> int:
        """Usually the next unit along (an animal walking a tube), sometimes
        the same one again."""
        i = self.unit_addrs.index(unit)
        step = self._rng.choice((-1, 0, 1, 1))
        return self.unit_addrs[(i + step) % len(self.unit_addrs)]


def _parse_frames(data: bytes) -> list[tuple[int, bytes]]:
    """(unit_to, content) for every complete host frame in ``data``. The host
    only ever writes whole frames, so this doesn't keep partial state."""
    frames = []
    i = 0
    while True:
        start = data.find(bytes([DLE, STX]), i)
        if start < 0:
            return frames
        j = start + 2
        while j + 1 < len(data):
            if data[j] == DLE and data[j + 1] == DLE:
                j += 2
                continue
            if data[j] == DLE and data[j + 1] == ETX:
                break
            j += 1
        else:
            return frames
        payload = _destuff(data[start + 2:j])
        if len(payload) >= 2:
            frames.append((payload[1], payload[2:]))
        i = j + 3   # DLE ETX checksum
