"""
LID650 / LID665 RS485 bus driver.

Frame format:
    DLE STX <from> <to> [<cmd>] [<data>...] DLE ETX <checksum>

All bytes equal to DLE (0x10) inside the payload are stuffed with an extra DLE.
Checksum is XOR over every byte in the frame except the checksum itself.

Host address : 0xFE
Broadcast    : 0xFF  (all units reply; unit numbers 0x00 and 0xFF are reserved)
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

# ── Protocol constants ────────────────────────────────────────────────────────
DLE            = 0x10
STX            = 0x02
ETX            = 0x03
HOST_ADDR      = 0xFE
BROADCAST_ADDR = 0xFF

# Startup markers (sent to BROADCAST_ADDR by the decoder itself)
STARTUP_COLD = 0x4B   # 'K'  - first power-on (unit_from == 0x00)
STARTUP_WARM = 0x57   # 'W'  - warm restart

# Command bytes
CMD_VERSION        = 0x56   # 'V'  - read firmware version string
CMD_SERIAL_NUMBER  = 0x4A   # 'J'  - read 4-byte hardware serial number
CMD_READ_CONFIG    = 0x43   # 'C'  - read configuration byte nn
CMD_WRITE_CONFIG   = 0x63   # 'c'  - write configuration byte nn
CMD_READ_WAITTIME  = 0x4C   # 'L'  - read LED/relay time
CMD_WRITE_WAITTIME = 0x6C   # 'l'  - write LED/relay time
CMD_MEM_STATUS     = 0x41   # 'A'  - read number of saved codes
CMD_READ_MEMORY    = 0x4D   # 'M'  - read historical memory
CMD_READ_DATE      = 0x44   # 'D'  - read date/time
CMD_WRITE_DATE     = 0x64   # 'd'  - write date/time
CMD_ERASE_ALL      = 0x45   # 'E'  - erase all codes
CMD_ERASE_HISTORY  = 0x65   # 'e'  - erase history codes
CMD_TRIGGER        = 0x47   # 'G'  - software trigger
CMD_READ_KEYCARDS  = 0x54   # 'T'  - read keycards
CMD_WRITE_KEYCARD  = 0x74   # 't'  - write keycard
CMD_RENUMBER       = 0x75   # 'u'  - set unit number
CMD_RESTART        = 0x78   # 'x'  - restart
CMD_RESET_FACTORY  = 0x58   # 'X'  - factory reset
CMD_READ_CYCLES    = 0x52   # 'R'  - read readcycles count
CMD_WRITE_CYCLES   = 0x72   # 'r'  - write readcycles count

# Transponder type bytes that may prefix the payload on spontaneous reads
TRANSPONDER_TYPES: dict[int, str] = {
    0x20: 'Trovan Unique',
    0x13: 'Trovan Flex 3',
    0x15: 'Trovan Flex 5',
    0x17: 'Trovan Flex 7',
}

BAUD_DEFAULT = 19200

# Reader state-machine states (see RS485Bus._read_loop)
_S_IDLE, _S_AFTER_DLE, _S_IN_FRAME, _S_AFTER_FRAME_DLE, _S_CHECKSUM = range(5)


# ── Unit record ───────────────────────────────────────────────────────────────

@dataclass
class UnitInfo:
    address: int
    firmware_version: str = ''
    serial_number: str | None = None
    last_seen: float = field(default_factory=time.time)

    @property
    def address_hex(self) -> str:
        return f'{self.address:02X}h'

    def to_dict(self) -> dict:
        return {
            'address':          self.address,
            'address_hex':      self.address_hex,
            'firmware_version': self.firmware_version,
            'serial_number':    self.serial_number,
            'last_seen':        self.last_seen,
        }


# ── Frame codec ───────────────────────────────────────────────────────────────

def _xor(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs


def _stuff(data: bytes) -> bytes:
    """Insert an extra DLE before every DLE byte in the payload."""
    out = bytearray()
    for b in data:
        out.append(b)
        if b == DLE:
            out.append(DLE)
    return bytes(out)


def _destuff(data: bytes) -> bytes:
    """Remove DLE stuffing: each DLE DLE pair becomes a single DLE."""
    out = bytearray()
    i = 0
    while i < len(data):
        out.append(data[i])
        if data[i] == DLE and i + 1 < len(data) and data[i + 1] == DLE:
            i += 2
        else:
            i += 1
    return bytes(out)


def build_frame(unit_from: int, unit_to: int,
                cmd: int | None = None,
                data: bytes = b'') -> bytes:
    """
    Build a complete RS485 frame.

    For the broadcast logon (empty message), pass cmd=None and data=b''.
    The resulting frame is: 10 02 FE FF 10 03 00
    """
    payload = bytearray([unit_from, unit_to])
    if cmd is not None:
        payload.append(cmd)
    payload.extend(data)
    stuffed = _stuff(bytes(payload))
    body = bytes([DLE, STX]) + stuffed + bytes([DLE, ETX])
    return body + bytes([_xor(body)])


# ── Bus manager ───────────────────────────────────────────────────────────────

class RS485Bus:
    """
    Manages the RS485 serial connection to a network of LID650/665 decoders.

    Assign callables to the on_* attributes before calling connect().
    All callbacks are invoked from the reader thread - use thread-safe emit
    wrappers (e.g. flask_socketio.SocketIO.emit) where needed.

    Callbacks
    ---------
    on_unit_found(unit: UnitInfo)
        Fired when a unit is first seen AND on every subsequent update
        (firmware/serial/last_seen refresh).  Check unit.address to distinguish.

    on_transponder_read(unit_addr, tid: bytes, type_name: str, timestamp: float)
        Fired for every spontaneous transponder read frame.

    on_bus_status(connected: bool, port: str)
        Fired on connect and disconnect.

    on_raw_frame(unit_from, unit_to, content: bytes)
        Optional debug hook for every validated incoming frame.
    """

    def __init__(self) -> None:
        self._serial: serial.Serial | None = None
        self._port  = ''
        self._baud  = BAUD_DEFAULT
        self._lock  = threading.Lock()
        self._stop  = threading.Event()
        self._reader_thread: threading.Thread | None = None

        self.units: dict[int, UnitInfo] = {}

        # Public callbacks
        self.on_unit_found:       Callable | None = None
        self.on_transponder_read: Callable | None = None
        self.on_bus_status:       Callable | None = None
        self.on_raw_frame:        Callable | None = None

        # Scan-window state
        self._scanning       = False
        self._scan_deadline  = 0.0

        # Post-discovery serial-number query queue (one at a time, rate-limited)
        self._query_queue: queue.Queue = queue.Queue()
        self._query_thread = threading.Thread(
            target=self._query_worker, daemon=True, name='rs485-query')
        self._query_thread.start()

    # ── Port discovery ────────────────────────────────────────────────────────

    @staticmethod
    def find_ports() -> list[str]:
        """
        Return candidate USB-serial port names ordered by likelihood.
        Prefers /dev/cu.* over /dev/tty.* on macOS.
        """
        candidates = []
        for p in serial.tools.list_ports.comports():
            combined = ' '.join(
                [p.device, p.description or '', p.hwid or '']
            ).lower()
            if any(kw in combined for kw in
                   ['usb', 'uart', 'serial', 'cp210', 'ch340', 'ftdi',
                    'pl2303', 'silabs', 'prolific', 'rs485', 'rs-485']):
                candidates.append(p.device)
        candidates.sort(key=lambda x: (0 if 'cu.' in x.lower() else 1, x))
        return candidates

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self, port: str = '', baud: int = BAUD_DEFAULT) -> bool:
        """
        Open the serial port.  If *port* is empty the first auto-detected
        USB-serial adapter is used.  Returns True on success.
        """
        if not port:
            ports = self.find_ports()
            if not ports:
                log.error('No USB-serial port found')
                return False
            port = ports[0]
            log.info('Auto-selected port: %s', port)

        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
            )
        except serial.SerialException as exc:
            log.error('Cannot open %s: %s', port, exc)
            self._serial = None
            return False

        self._port = port
        self._baud = baud
        self._stop.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name='rs485-reader')
        self._reader_thread.start()
        log.info('Connected to %s @ %d baud', port, baud)
        if self.on_bus_status:
            self.on_bus_status(True, port)
        return True

    def disconnect(self) -> None:
        self._stop.set()
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        if self.on_bus_status:
            self.on_bus_status(False, self._port)
        log.info('Disconnected from %s', self._port)

    @property
    def is_connected(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    @property
    def port(self) -> str:
        return self._port

    @property
    def baud(self) -> int:
        return self._baud

    # ── Commands ──────────────────────────────────────────────────────────────

    def _send(self, frame: bytes) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.write(frame)
            else:
                log.warning('Send attempted while disconnected')

    def scan_bus(self) -> None:
        """
        Broadcast logon: a single short frame causes every unit on the bus to
        reply with its unit address and firmware version.  Responses are
        collected for 3 seconds; this does NOT poll individual addresses.
        """
        if not self.is_connected:
            log.warning('scan_bus called while not connected')
            return
        frame = build_frame(HOST_ADDR, BROADCAST_ADDR)   # empty = logon
        self._scanning      = True
        self._scan_deadline = time.time() + 3.0
        self._send(frame)
        log.info('Bus scan started (broadcast logon sent)')

    def get_serial_number(self, unit_addr: int) -> None:
        """Request the 4-byte hardware serial number from a specific unit."""
        self._send(build_frame(HOST_ADDR, unit_addr, CMD_SERIAL_NUMBER))

    def get_version(self, unit_addr: int) -> None:
        self._send(build_frame(HOST_ADDR, unit_addr, CMD_VERSION))

    def read_config_byte(self, unit_addr: int, config_num: int) -> None:
        """config_num: 1..6  ->  sent as 0x31..0x36"""
        self._send(build_frame(HOST_ADDR, unit_addr, CMD_READ_CONFIG,
                               bytes([0x30 + config_num])))

    def write_config_byte(self, unit_addr: int, config_num: int, value: int) -> None:
        self._send(build_frame(HOST_ADDR, unit_addr, CMD_WRITE_CONFIG,
                               bytes([0x30 + config_num, value])))

    def restart_unit(self, unit_addr: int) -> None:
        self._send(build_frame(HOST_ADDR, unit_addr, CMD_RESTART))

    def renumber_unit(self, current_addr: int, new_addr: int) -> None:
        """Assign a new unit number.  Use with care - ensure no duplicates."""
        self._send(build_frame(HOST_ADDR, current_addr, CMD_RENUMBER,
                               bytes([new_addr])))

    # ── Rate-limited post-discovery query worker ──────────────────────────────

    def _query_worker(self) -> None:
        """Processes one serial-number query at a time with a bus-friendly gap."""
        while True:
            addr = self._query_queue.get()
            if not self.is_connected:
                continue
            time.sleep(0.25)   # small gap - never saturate the bus
            log.debug('Querying serial number for unit %02Xh', addr)
            self.get_serial_number(addr)

    # ── Frame reader state machine ────────────────────────────────────────────

    def _read_loop(self) -> None:
        """
        Byte-level state machine that assembles DLE-framed packets from the
        raw serial stream.  Runs in a dedicated daemon thread.
        """
        state = _S_IDLE
        buf   = bytearray()

        while not self._stop.is_set():
            try:
                raw = self._serial.read(1)
            except serial.SerialException as exc:
                log.error('Serial read error: %s', exc)
                self._serial = None
                if self.on_bus_status:
                    self.on_bus_status(False, self._port)
                break

            if not raw:
                # Timeout - check scan window expiry
                if self._scanning and time.time() > self._scan_deadline:
                    self._scanning = False
                    log.info('Bus scan complete - %d unit(s) known', len(self.units))
                continue

            b = raw[0]

            if state == _S_IDLE:
                if b == DLE:
                    state = _S_AFTER_DLE

            elif state == _S_AFTER_DLE:
                if b == STX:
                    buf = bytearray([DLE, STX])
                    state = _S_IN_FRAME
                else:
                    state = _S_IDLE

            elif state == _S_IN_FRAME:
                buf.append(b)
                if b == DLE:
                    state = _S_AFTER_FRAME_DLE

            elif state == _S_AFTER_FRAME_DLE:
                buf.append(b)
                if b == ETX:
                    state = _S_CHECKSUM
                elif b == DLE:
                    # Stuffed DLE - remain in frame
                    state = _S_IN_FRAME
                elif b == STX:
                    # Unexpected DLE STX - resync to new frame
                    buf   = bytearray([DLE, STX])
                    state = _S_IN_FRAME
                else:
                    buf   = bytearray()
                    state = _S_IDLE

            elif state == _S_CHECKSUM:
                buf.append(b)
                self._process_raw_frame(bytes(buf))
                buf   = bytearray()
                state = _S_IDLE

    # ── Frame processing ──────────────────────────────────────────────────────

    def _process_raw_frame(self, raw: bytes) -> None:
        """Verify checksum, decode, dispatch."""
        if len(raw) < 5:
            return

        # raw = DLE STX <stuffed_payload> DLE ETX <checksum>
        # Checksum covers everything before itself
        if _xor(raw[:-1]) != raw[-1]:
            log.debug('Checksum error: %s', raw.hex())
            return

        # Stuffed payload sits between DLE STX and DLE ETX
        stuffed_payload = raw[2:-3]    # strip DLE STX at front, DLE ETX CS at back
        payload = _destuff(stuffed_payload)

        if len(payload) < 2:
            return

        unit_from = payload[0]
        unit_to   = payload[1]
        content   = payload[2:]
        ts        = time.time()

        log.debug('RX %02Xh->%02Xh [%s]', unit_from, unit_to, content.hex())

        if self.on_raw_frame:
            try:
                self.on_raw_frame(unit_from, unit_to, content)
            except Exception as exc:
                log.error('on_raw_frame error: %s', exc)

        self._dispatch(unit_from, unit_to, content, ts)

    def _dispatch(self, unit_from: int, unit_to: int,
                  content: bytes, ts: float) -> None:
        """Route a validated frame to the appropriate handler."""

        # ── Startup messages (decoder -> broadcast) ────────────────────────────
        if unit_to == BROADCAST_ADDR:
            if content and content[0] in (STARTUP_COLD, STARTUP_WARM):
                version = content[1:].decode('ascii', errors='replace').strip()
                kind    = 'cold' if content[0] == STARTUP_COLD else 'warm'
                log.info('Unit %02Xh %s startup, fw=%s', unit_from, kind, version)
                self._register_unit(unit_from, firmware_version=version, ts=ts)
                return

            # Spontaneous transponder read
            # The first byte may be a type indicator (0x20 / 0x13 / 0x15 / 0x17)
            if content:
                if content[0] in TRANSPONDER_TYPES:
                    type_name = TRANSPONDER_TYPES[content[0]]
                    tid       = content[1:]
                else:
                    type_name = 'Unknown'
                    tid       = content

                tid_hex = tid.hex().upper()
                log.info('Transponder read unit %02Xh: %s (%s)',
                         unit_from, tid_hex, type_name)
                self._register_unit(unit_from, ts=ts)
                if self.on_transponder_read:
                    try:
                        self.on_transponder_read(unit_from, tid, type_name, ts)
                    except Exception as exc:
                        log.error('on_transponder_read error: %s', exc)
            return

        # ── Responses addressed to host ───────────────────────────────────────
        if unit_to == HOST_ADDR:
            is_new = unit_from not in self.units
            self._register_unit(unit_from, ts=ts)

            # Logon response: 3 ASCII decimal bytes (e.g. 38 30 30 = "800")
            # These are NOT prefixed by a command echo byte.
            if (len(content) == 3
                    and all(0x30 <= b <= 0x39 for b in content)):
                version = content.decode('ascii')
                log.info('Unit %02Xh logon response, fw=%s', unit_from, version)
                self._register_unit(unit_from, firmware_version=version, ts=ts)
                # Queue a serial-number query for newly discovered units
                if is_new:
                    self._query_queue.put(unit_from)
                return

            if not content:
                return

            cmd  = content[0]
            data = content[1:]

            if cmd == CMD_SERIAL_NUMBER and len(data) >= 4:
                sn = data[:4].hex().upper()
                log.info('Unit %02Xh serial number: %s', unit_from, sn)
                self._register_unit(unit_from, serial_number=sn, ts=ts)

            elif cmd == CMD_VERSION:
                version = data.decode('ascii', errors='replace').strip()
                log.info('Unit %02Xh firmware: %s', unit_from, version)
                self._register_unit(unit_from, firmware_version=version, ts=ts)

        # Expire the scan window
        if self._scanning and time.time() > self._scan_deadline:
            self._scanning = False
            log.info('Bus scan complete - %d unit(s) known', len(self.units))

    def _register_unit(self, addr: int,
                        firmware_version: str = '',
                        serial_number: str | None = None,
                        ts: float | None = None) -> None:
        """Create or update a UnitInfo entry and fire on_unit_found."""
        if ts is None:
            ts = time.time()

        is_new = addr not in self.units
        if is_new:
            self.units[addr] = UnitInfo(address=addr)

        unit = self.units[addr]
        unit.last_seen = ts
        if firmware_version:
            unit.firmware_version = firmware_version
        if serial_number:
            unit.serial_number = serial_number

        if self.on_unit_found:
            try:
                self.on_unit_found(unit)
            except Exception as exc:
                log.error('on_unit_found error: %s', exc)
