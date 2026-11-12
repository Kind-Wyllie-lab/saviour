#!/usr/bin/env python3
"""
SAVIOUR System - RFID Module

A thin :class:`Module` wrapper around the Trovan LID650/665 RS485 bus driver
(:class:`rfid_bus.RS485Bus`). It:

  * opens the RS485 bus (auto-detecting the serial port) and scans for reader
    units on start,
  * on every transponder read ("ping") buffers it for the live view, tells the
    controller, and - while a recording session is active - appends a row to
    the current segment CSV,
  * serves an MJPEG "pings" monitoring stream (a scrolling per-tag timeline
    with a flash on each fresh read) on ``monitoring._port`` (default 8083),
  * logs every read, recording or not, to a local SQLite store
    (:mod:`rfid_store`) that :mod:`rfid_sync` forwards to a long-term MariaDB
    whose connection settings the controller pushes (``set_rfid_db_config``).

Author: Andrew SG
"""

import collections
import os
import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

# SAVIOUR + local-variant imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.append(os.path.dirname(__file__))
from rfid_bus import RS485Bus
from rfid_mock import MOCK_PORT, PROBE_TAG, MockBusSerial, MockOptions
from rfid_store import KIND_READ, KIND_ZERO, RfidStore, StoredRead
from rfid_sync import DbSettings, RfidSync, load_settings, save_settings

from modules.mjpeg_stream import MJPEGStreamServer
from modules.module import Module, check, command
from src.shared.supervised import supervise

DEFAULT_STORE_PATH = "/var/lib/saviour/rfid/reads.db"
# rfid.mock.enabled swaps the store for this sibling file, so simulated reads
# never mix with real ones or reach the database.
MOCK_STORE_SUFFIX = "_mock"


@dataclass
class Ping:
    ts: float          # epoch seconds of the first arriving byte of the packet
    unit: int          # reader unit address
    tag: str           # transponder id, hex, upper-case
    type_name: str      # e.g. "Trovan Unique"
    type_code: int | None = None   # raw type byte; None if not a known type


@dataclass
class Visit:
    """One continuous presence of a tag at a reader - a run of pings with no
    gap longer than the tracker's gap_timeout."""
    unit: int
    tag: str
    type_name: str
    enter_ts: float          # ts of the first ping
    last_ts: float           # ts of the most recent ping
    count: int               # pings seen so far
    announced: bool = False  # has it cleared min_pings / min_dwell yet?

    @property
    def duration_s(self) -> float:
        return round(self.last_ts - self.enter_ts, 3)


class PresenceTracker:
    """Collapses the raw ping firehose into per-(unit, tag) enter/exit visits.

    Feed every ping to :meth:`observe`; call :meth:`sweep` on a timer (and
    :meth:`close_all` on a segment boundary / stop). A visit is "announced"
    once it clears ``min_pings`` AND ``min_dwell_s`` - only announced visits
    produce an enter event and, on close, an exit.
    """

    def __init__(self, *, gap_timeout_s: float, min_pings: int,
                 min_dwell_s: float):
        self.gap_timeout_s = max(0.1, float(gap_timeout_s))
        self.min_pings = max(1, int(min_pings))
        self.min_dwell_s = max(0.0, float(min_dwell_s))
        self._open: dict[tuple[int, str], Visit] = {}

    def _qualifies(self, v: Visit) -> bool:
        return (v.count >= self.min_pings
                and (v.last_ts - v.enter_ts) >= self.min_dwell_s)

    def observe(self, ping: Ping) -> tuple[Visit | None, Visit | None]:
        """Fold one ping in. Returns ``(exited, entered)`` - either may be None.

        ``exited`` is set only when this ping arrives so long after the same
        tag's previous ping that the old visit had already timed out before a
        sweep caught it.
        """
        key = (ping.unit, ping.tag)
        exited = None
        v = self._open.get(key)
        if v is not None and ping.ts - v.last_ts > self.gap_timeout_s:
            if v.announced:
                exited = v
            v = None
        if v is None:
            v = Visit(unit=ping.unit, tag=ping.tag,
                      type_name=ping.type_name or "Unknown",
                      enter_ts=ping.ts, last_ts=ping.ts, count=1)
            self._open[key] = v
        else:
            v.last_ts = ping.ts
            v.count += 1
            if ping.type_name and v.type_name in ("", "Unknown"):
                v.type_name = ping.type_name
        entered = None
        if not v.announced and self._qualifies(v):
            v.announced = True
            entered = v
        return exited, entered

    def sweep(self, now: float) -> list[Visit]:
        """Close (and return) every announced visit idle longer than the gap."""
        closed = []
        for key, v in list(self._open.items()):
            if now - v.last_ts > self.gap_timeout_s:
                del self._open[key]
                if v.announced:
                    closed.append(v)
        return closed

    def close_all(self) -> list[Visit]:
        """Force-close everything (segment boundary / stop). Announced only."""
        closed = [v for v in self._open.values() if v.announced]
        self._open.clear()
        return closed


class RFIDModule(Module):
    def __init__(self, module_type: str = "rfid"):
        super().__init__(module_type)

        self.config.load_module_config("rfid_config.json")

        # ── Live-view state ──────────────────────────────────────────────
        self._pings: collections.deque[Ping] = collections.deque(maxlen=1000)
        self._pings_lock = threading.Lock()
        self._total_reads = 0
        self._last_ping: Ping | None = None

        # ── Recording state ─────────────────────────────────────────────
        self.is_recording = False
        self._csv_handle = None
        self.current_rfid_filename: str | None = None
        self._visit_csv_handle = None
        self.current_visit_filename: str | None = None

        # ── Presence tracking (enter/exit smoothing) ────────────────────
        self._presence: PresenceTracker | None = None
        self._presence_lock = threading.Lock()
        self._sweep_timer: threading.Timer | None = None
        self._rebuild_presence()

        # ── Local store + DB sync (opened in start()) ──────────────────
        self._store: RfidStore | None = None
        self._store_error: str | None = None
        self._sync: RfidSync | None = None
        self._db_settings = DbSettings()
        self._db_stop = threading.Event()
        self._db_threads: list[threading.Thread] = []
        self._zero_reads = 0

        # ── Gate-mapping mode (habitat controller's mapping routine) ────
        # Epoch seconds it stays on until; 0 = off. Renewed by the
        # controller while a mapping page is open, so a closed browser tab
        # can't leave it on.
        self._mapping_until = 0.0

        # The simulated bus while rfid.mock.enabled is on (rfid_mock_*
        # commands drive it by hand); None otherwise.
        self._mock_bus: MockBusSerial | None = None

        # ── Bus ─────────────────────────────────────────────────────────
        self.bus = RS485Bus()
        self.bus.on_transponder_read = self._on_transponder_read
        self.bus.on_bus_status = self._on_bus_status
        # None once a reader is on the bus; otherwise a human-readable reason.
        # Surfaced every heartbeat via get_health() (the System page's "NO
        # HARDWARE" badge) and as the _check_rfid() readiness failure reason.
        # Set here so a module that never finds a reader still reports the
        # fault before start()/_bring_up_bus() has run.
        self.hardware_fault: str | None = "RFID reader not yet connected"

        # ── Monitoring stream ──────────────────────────────────────────
        self.is_streaming = False
        self.monitor_stream = MJPEGStreamServer(
            render_fn=self._render_monitor_frame,
            interval=0.06,
            logger=self.logger,
            name="RFID",
        )

        # Extra remotely-callable commands
        self.command.set_commands({
            "rfid_scan": self.rfid_scan,
        })

        self.logger.info("Initialised RFID module")

    # ══════════════════════════════════════════════════════════════════════
    # Bus callbacks
    # ══════════════════════════════════════════════════════════════════════

    def _on_transponder_read(self, unit_addr: int, tid: bytes,
                             type_name: str, ts: float,
                             type_code: int | None = None) -> None:
        """Called from the bus reader thread for every spontaneous read."""
        is_bytes = isinstance(tid, (bytes, bytearray))
        tag = tid.hex().upper() if is_bytes else str(tid)
        ping = Ping(ts=ts, unit=unit_addr, tag=tag,
                    type_name=type_name or "Unknown", type_code=type_code)
        # An all-zero ID: meaning not yet known (see rfid_store.KIND_ZERO).
        # Stored and kept in the raw CSV, but not shown as a tag, sent as an
        # rfid_read or fed to presence tracking, where it would look like one
        # very busy animal.
        zero = is_bytes and len(tid) > 0 and not any(tid)
        self._store_read(ping, KIND_ZERO if zero else KIND_READ)
        if zero:
            self._zero_reads += 1
            if self._want_raw_output():
                self._write_raw_row(ping)
            return
        self._record_ping(ping)

    def _store_read(self, ping: Ping, kind: str) -> None:
        store = self._store
        if store is None:
            return
        session = None
        if self.is_recording:
            session = getattr(getattr(self, "recording", None),
                              "current_session_name", None)
        store.put(StoredRead(ts_ns=int(ping.ts * 1e9), unit=ping.unit,
                             tag=ping.tag, type_code=ping.type_code,
                             kind=kind, session=session))

    def _record_ping(self, ping: Ping) -> None:
        with self._pings_lock:
            self._pings.append(ping)
            self._total_reads += 1
            self._last_ping = ping

        # debug, not info: a busy bus is 10+ reads/s, a journald flood at info.
        self.logger.debug(
            f"RFID ping: unit {ping.unit:#04x} tag {ping.tag} ({ping.type_name})"
        )

        # With presence tracking on, the raw per-ping status message + CSV row
        # are only kept when `record` asks for them; the enter/exit events
        # carry the useful signal. With it off, behaviour is unchanged.
        # Gate-mapping mode forces the status message (never the CSV row).
        if self._want_raw_output() or self._mapping_active():
            self._send_status_safe({
                "type": "rfid_read",
                "unit_address": ping.unit,
                "transponder_id": ping.tag,
                "transponder_type": ping.type_name,
                "packet_ts": ping.ts,
                "total_reads": self._total_reads,
            })
        if self._want_raw_output():
            self._write_raw_row(ping)

        presence = self._presence
        if presence is not None:
            with self._presence_lock:
                exited, entered = presence.observe(ping)
            if exited is not None:
                self._close_visit(exited, "gap")
            if entered is not None:
                self._open_visit(entered)

    def _write_raw_row(self, ping: Ping) -> None:
        if not (self.is_recording and self._csv_handle is not None):
            return
        try:
            utc = self.facade.get_utc_time(int(ping.ts))
            row = (f"{int(ping.ts * 1e9)},{utc},{ping.unit},"
                   f"{ping.tag},{ping.type_name}\n")
            self._csv_handle.write(row)
            self._csv_handle.flush()
        except Exception as e:
            self.logger.error(f"RFID: failed to write ping to CSV: {e}")

    # ── Presence config / visit lifecycle ─────────────────────────────────

    def _presence_record_mode(self) -> str:
        mode = str(self.config.get("rfid.presence.record", "both")).lower()
        return mode if mode in ("raw", "visits", "both") else "both"

    def _want_raw_output(self) -> bool:
        """Raw per-ping status + CSV row wanted this call?"""
        return self._presence is None or self._presence_record_mode() in ("raw", "both")

    def _want_visit_csv(self) -> bool:
        return (self._presence is not None
                and self._presence_record_mode() in ("visits", "both"))

    def _rebuild_presence(self) -> None:
        """(Re)create the tracker from config; keep the visit CSV in step if a
        recording is already running."""
        if self.config.get("rfid.presence.enabled", False):
            get = self.config.get
            self._presence = PresenceTracker(
                gap_timeout_s=float(get("rfid.presence.gap_timeout_s", 2.0)),
                min_pings=int(get("rfid.presence.min_pings", 2)),
                min_dwell_s=float(get("rfid.presence.min_dwell_s", 0.0)),
            )
        else:
            self._presence = None

        if not self.is_recording:
            return
        if self._want_visit_csv() and self._visit_csv_handle is None:
            self._open_visit_segment()
        elif not self._want_visit_csv() and self._visit_csv_handle is not None:
            if self.current_visit_filename:
                self.facade.stage_file_for_export(self.current_visit_filename)
            self._close_visit_csv()

    def _open_visit(self, v: Visit) -> None:
        self._send_status_safe({
            "type": "rfid_enter",
            "unit_address": v.unit,
            "transponder_id": v.tag,
            "transponder_type": v.type_name,
            "enter_ts": v.enter_ts,
        })

    def _close_visit(self, v: Visit, reason: str) -> None:
        self._send_status_safe({
            "type": "rfid_exit",
            "unit_address": v.unit,
            "transponder_id": v.tag,
            "transponder_type": v.type_name,
            "enter_ts": v.enter_ts,
            "exit_ts": v.last_ts,
            "duration_s": v.duration_s,
            "ping_count": v.count,
            "closed_reason": reason,
        })
        if not (self.is_recording and self._visit_csv_handle is not None):
            return
        try:
            e_utc = self.facade.get_utc_time(int(v.enter_ts))
            x_utc = self.facade.get_utc_time(int(v.last_ts))
            row = (f"{int(v.enter_ts * 1e9)},{int(v.last_ts * 1e9)},"
                   f"{e_utc},{x_utc},{v.unit},{v.tag},{v.type_name},"
                   f"{v.count},{v.duration_s},{reason}\n")
            self._visit_csv_handle.write(row)
            self._visit_csv_handle.flush()
        except Exception as e:
            self.logger.error(f"RFID: failed to write visit row: {e}")

    def _sweep_presence(self) -> None:
        p = self._presence
        if p is None:
            return
        with self._presence_lock:
            closed = p.sweep(time.time())
        for v in closed:
            self._close_visit(v, "gap")

    def _sweep_tick(self) -> None:
        try:
            self._sweep_presence()
        except Exception as e:
            self.logger.error(f"RFID presence sweep error: {e}")
        finally:
            self._start_sweeper()  # re-arm

    def _start_sweeper(self) -> None:
        self._stop_sweeper()
        if self._presence is None:
            return
        gap = float(self.config.get("rfid.presence.gap_timeout_s", 2.0))
        self._sweep_timer = threading.Timer(max(0.5, gap / 2), self._sweep_tick)
        self._sweep_timer.daemon = True
        self._sweep_timer.start()

    def _stop_sweeper(self) -> None:
        if self._sweep_timer is not None:
            self._sweep_timer.cancel()
            self._sweep_timer = None

    def _on_bus_status(self, connected: bool, port: str) -> None:
        self.logger.info(
            f"RFID bus {'connected on ' + port if connected else 'disconnected'}"
        )
        self._send_status_safe({
            "type": "rfid_bus_status",
            "connected": connected,
            "port": port,
        })

    def _send_status_safe(self, payload: dict) -> None:
        if getattr(self, "communication", None) and self.communication.controller_ip:
            try:
                self.communication.send_status(payload)
            except Exception as e:
                self.logger.debug(f"RFID: send_status failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # Recording hooks (called by the Recording base class)
    # ══════════════════════════════════════════════════════════════════════

    def _get_rfid_filename(self) -> str:
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        return (f"{self.facade.get_filename_prefix()}"
                f"_({self.facade.get_segment_id()}_{strtime}).csv")

    def _open_csv(self, filename: str) -> None:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self._csv_handle = open(filename, "w", buffering=1)
        self._csv_handle.write(
            "timestamp_ns,timestamp_utc,unit_address,transponder_id,transponder_type\n"
        )

    def _close_csv(self) -> None:
        if self._csv_handle is not None:
            try:
                self._csv_handle.flush()
                self._csv_handle.close()
            except Exception as e:
                self.logger.warning(f"RFID: error closing CSV: {e}")
            self._csv_handle = None

    def _get_visit_filename(self) -> str:
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        return (f"{self.facade.get_filename_prefix()}_visits"
                f"_({self.facade.get_segment_id()}_{strtime}).csv")

    def _open_visit_csv(self, filename: str) -> None:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        self._visit_csv_handle = open(filename, "w", buffering=1)
        self._visit_csv_handle.write(
            "enter_ts_ns,exit_ts_ns,enter_utc,exit_utc,unit_address,"
            "transponder_id,transponder_type,ping_count,duration_s,closed_reason\n"
        )

    def _close_visit_csv(self) -> None:
        if self._visit_csv_handle is not None:
            try:
                self._visit_csv_handle.flush()
                self._visit_csv_handle.close()
            except Exception as e:
                self.logger.warning(f"RFID: error closing visit CSV: {e}")
            self._visit_csv_handle = None

    def _open_visit_segment(self) -> None:
        """Open a fresh visit CSV for the current segment and register it."""
        vf = self._get_visit_filename()
        self.current_visit_filename = vf
        self.facade.add_session_file(vf)
        self._open_visit_csv(vf)

    def _flush_open_visits(self, reason: str) -> None:
        """Close every currently-open visit (segment boundary / stop)."""
        if self._presence is None:
            return
        with self._presence_lock:
            open_visits = self._presence.close_all()
        for v in open_visits:
            self._close_visit(v, reason)

    def _start_new_recording(self) -> bool:
        self.current_rfid_filename = None
        self.current_visit_filename = None
        if self._want_raw_output():
            filename = self._get_rfid_filename()
            self.current_rfid_filename = filename
            self.facade.add_session_file(filename)
            self._open_csv(filename)
        if self._want_visit_csv():
            self._open_visit_segment()
        self.is_recording = True
        self.logger.info(
            f"RFID recording started (raw={self.current_rfid_filename is not None}, "
            f"visits={self.current_visit_filename is not None})"
        )
        return True

    def _start_next_recording_segment(self) -> bool:
        self._flush_open_visits("segment_boundary")
        if self.current_rfid_filename:
            self.facade.stage_file_for_export(self.current_rfid_filename)
        self._close_csv()
        if self.current_visit_filename:
            self.facade.stage_file_for_export(self.current_visit_filename)
        self._close_visit_csv()

        self.current_rfid_filename = None
        self.current_visit_filename = None
        if self._want_raw_output():
            filename = self._get_rfid_filename()
            self.current_rfid_filename = filename
            self.facade.add_session_file(filename)
            self._open_csv(filename)
        if self._want_visit_csv():
            self._open_visit_segment()
        self.logger.info("RFID segment rotated")
        return True

    def _stop_recording(self) -> bool:
        try:
            self._flush_open_visits("recording_stopped")
            self.is_recording = False
            self._close_csv()
            self._close_visit_csv()
            if self.current_rfid_filename:
                self.facade.stage_file_for_export(self.current_rfid_filename)
            if self.current_visit_filename:
                self.facade.stage_file_for_export(self.current_visit_filename)
            self._send_status_safe({
                "type": "recording_stopped",
                "status": "success",
                "recording": False,
            })
            self.logger.info("RFID recording stopped")
            return True
        except Exception as e:
            self.logger.error(f"RFID: error stopping recording: {e}")
            self._send_status_safe({
                "type": "recording_stopped", "status": "error", "error": str(e),
            })
            return False

    # ══════════════════════════════════════════════════════════════════════
    # Config / readiness
    # ══════════════════════════════════════════════════════════════════════

    def configure_module_special(self, updated_keys: list[str] | None):
        if not updated_keys:
            return
        rfid_keys = [k for k in updated_keys if k.startswith("rfid.")]
        if not rfid_keys:
            return
        # Presence keys rebuild the tracker and sync keys apply live; only
        # bus keys (serial port, baud, ...) need a bus reconnect.
        # rfid.store.path takes effect at the next restart.
        self._rebuild_presence()
        self._start_sweeper()
        self._apply_sync_config()
        live = ("rfid.presence", "rfid.sync", "rfid.store")
        if any(not k.startswith(live) for k in rfid_keys):
            self.logger.info("RFID config changed - reconnecting bus")
            self._teardown_bus()
            # Mock on/off switches to the other store file.
            if any(k.startswith("rfid.mock") for k in rfid_keys):
                self._stop_store()
                self._start_store()
            self._bring_up_bus()

    @check()
    def _check_recording_alive(self) -> tuple[bool, str | None]:
        """Recording liveness (roadmap A1). RFID reads are sparse by nature
        -- nothing arrives unless an animal passes an antenna -- so a quiet
        bus is normal and liveness is structural: the bus is open, its reader
        thread is running, and the output CSV is open while recording. Fed
        through Recording._monitor_recording_health (2 strikes) into the
        controller's recording_health_warning and a liveness gap."""
        if not self.bus.is_connected:
            return False, "RFID bus disconnected"
        if not self.bus.reader_alive:
            return False, "RFID bus reader thread stopped"
        if self.is_recording and self._want_raw_output() and (
                self._csv_handle is None or self._csv_handle.closed):
            return False, "RFID output CSV is not open"
        return True, None

    def _check_rfid(self):
        if self.bus.is_connected:
            self.hardware_fault = None
            return True, (f"RFID bus on {self.bus.port}, "
                          f"{len(self.bus.units)} unit(s) seen")
        self.hardware_fault = "No RFID reader found on any serial port"
        return False, self.hardware_fault

    # ══════════════════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════
    # Local store + database sync
    # ══════════════════════════════════════════════════════════════════════

    def _mock_enabled(self) -> bool:
        return bool(self.config.get("rfid.mock.enabled", False))

    def _store_path(self) -> str:
        path = (self.config.get("rfid.store.path", DEFAULT_STORE_PATH)
                or DEFAULT_STORE_PATH)
        if self._mock_enabled():
            root, ext = os.path.splitext(path)
            path = f"{root}{MOCK_STORE_SUFFIX}{ext}"
        return path

    def _sync_settings(self) -> DbSettings:
        """The saved database settings, or none (sync off) on a simulated
        bus, so fake reads never reach the long-term database."""
        return DbSettings() if self._mock_enabled() else self._db_settings

    def _db_settings_path(self) -> str:
        return os.path.join(os.path.dirname(self._store_path()), "db_settings.json")

    def _start_store(self) -> None:
        """Open the store and start its writer and the sync thread. A failure
        here (disk, permissions) leaves live monitoring and session CSVs
        working; it shows in the heartbeat's rfid_db.state."""
        try:
            store = RfidStore(self._store_path(), logger=self.logger)
            store.open()
        except Exception as e:
            self._store_error = f"{type(e).__name__}: {e}"
            self.logger.error(f"RFID store: cannot open {self._store_path()}: {e}")
            return
        try:
            self._db_settings = load_settings(self._db_settings_path())
        except Exception as e:
            self.logger.warning(f"RFID DB: cannot read saved settings: {e}")
        self._store = store
        self._store_error = None
        self._sync = RfidSync(store, module_id=lambda: self.module_id,
                              get_settings=self._sync_settings,
                              logger=self.logger)
        self._apply_sync_config()
        self._db_stop.clear()
        self._db_threads = [
            supervise("rfid.store_writer", store.run_writer,
                      stop_event=self._db_stop, logger=self.logger),
            supervise("rfid.db_sync", self._sync.run,
                      stop_event=self._db_stop, logger=self.logger),
        ]
        self.logger.info(f"RFID store open at {self._store_path()} "
                         f"(store_id {store.store_id})")

    def _stop_store(self) -> None:
        self._db_stop.set()
        if self._sync is not None:
            self._sync.wake()
        for t in self._db_threads:
            t.join(timeout=5)
        self._db_threads = []
        if self._store is not None:
            try:
                self._store.close()
            except Exception as e:
                self.logger.warning(f"RFID store: error closing: {e}")
        self._store = None
        self._sync = None

    def _apply_sync_config(self) -> None:
        sync = self._sync
        if sync is None:
            return
        get = self.config.get
        sync.interval_s = max(1.0, float(get("rfid.sync.interval_s", 5.0)))
        sync.batch_rows = max(1, int(get("rfid.sync.batch_rows", 500)))
        sync.retain_days = max(1.0, float(get("rfid.store.retain_days", 30)))

    def rfid_db_status(self) -> dict:
        """Heartbeat field ModuleHealthSnapshot.rfid_db."""
        if self._sync is None:
            return {"state": "store_error" if self._store_error else "stopped",
                    "last_error": self._store_error,
                    "zero_reads": self._zero_reads}
        try:
            status = self._sync.status()
        except Exception as e:
            status = {"state": "error", "last_error": f"status: {e}"}
        status["zero_reads"] = self._zero_reads
        if self._mock_enabled():
            status["mock"] = True
        return status

    @command()
    def set_rfid_db_config(self, host: str = "", port: int = 3306,
                           database: str = "", username: str = "",
                           password: str = "") -> dict:
        """Database settings pushed by the controller (its rfid_db config
        section). Saved to a root-only file next to the store, not to the
        module config, so get_config never echoes the password."""
        settings = DbSettings.from_dict({
            "host": host, "port": port, "database": database,
            "username": username, "password": password})
        try:
            save_settings(self._db_settings_path(), settings)
        except Exception as e:
            self.logger.error(f"RFID DB: cannot save settings: {e}")
            return {"result": "error", "message": str(e)}
        self._db_settings = settings
        if self._sync is not None:
            self._sync.wake()
        if settings.configured:
            self.logger.info(
                f"RFID DB settings updated: {settings.username}@{settings.host}:"
                f"{settings.port}/{settings.database}")
        else:
            self.logger.info("RFID DB settings cleared; sync is off")
        return {"result": "success"}

    @command()
    def rfid_db_test(self) -> dict:
        """Try the saved database settings and report what happened."""
        sync = self._sync
        if sync is None:
            reason = self._store_error or "stopped"
            return {"result": "error", "ok": False,
                    "message": f"Local store not running ({reason})"}
        ok, message = sync.test_connection(self._db_settings)
        return {"result": "success" if ok else "error", "ok": ok,
                "message": message, "status": self.rfid_db_status()}

    @command()
    def rfid_scan(self):
        """Broadcast a bus logon so every reader replies with its address."""
        if not self.bus.is_connected:
            return {"result": "error", "message": "bus not connected"}
        self.bus.scan_bus()
        return {"result": "success"}

    MAPPING_MAX_S = 600.0

    def _mapping_active(self) -> bool:
        return time.time() < self._mapping_until

    @command()
    def rfid_mapping_mode(self, enabled: bool = True, timeout_s: float = 120.0):
        """Send every read as rfid_read for ``timeout_s`` (max 10 min), even
        with presence tracking set to record only visits: the habitat gate
        mapping routine needs each probe pass, not the smoothed visits. The
        session CSVs are unchanged."""
        if enabled:
            timeout_s = min(self.MAPPING_MAX_S, max(1.0, float(timeout_s)))
            self._mapping_until = time.time() + timeout_s
        else:
            self._mapping_until = 0.0
        return {"result": "success", "mapping_mode": bool(enabled),
                "until": self._mapping_until or None}

    @command()
    def rfid_units(self):
        """Every unit seen on the bus since connect (address, firmware,
        serial number, and whether it answered the latest scan), for the
        gate map's bus-vs-map checks. ``pending_queries`` is how many
        serial-number queries are still queued (the controller asks again
        until it's 0); ``mock_bus`` is the simulated bus, if this is one."""
        units = []
        for _, u in sorted(self.bus.units.items()):
            d = u.to_dict()
            d["answered_last_scan"] = self.bus.answered_last_scan(u)
            units.append(d)
        return {"result": "success",
                "connected": bool(self.bus.is_connected),
                "port": self.bus.port,
                "units": units,
                "pending_queries": self.bus.pending_queries,
                "mock_bus": self._mock_state()}

    # ── Simulated bus, driven by hand (habitat Gates page) ────────────────

    def _mock_state(self) -> dict | None:
        bus = self._mock_bus
        if bus is None or not self._mock_enabled():
            return None
        return bus.state()

    def _mock_call(self, fn) -> dict:
        """Run ``fn(bus)`` on the simulated bus; refused on a real one."""
        bus = self._mock_bus
        if bus is None or not self._mock_enabled():
            return {"result": "error", "mock_bus": None,
                    "message": "Not a simulated bus (rfid.mock.enabled is off)"}
        try:
            fn(bus)
        except (TypeError, ValueError) as e:
            return {"result": "error", "message": str(e), "mock_bus": bus.state()}
        return {"result": "success", "mock_bus": bus.state()}

    @command()
    def rfid_mock_state(self):
        """The simulated bus's units and settings (an error on a real bus)."""
        return self._mock_call(lambda bus: None)

    @command()
    def rfid_mock_read(self, unit: int, tag: str = PROBE_TAG, count: int = 3):
        """A tag held at one antenna: ``count`` reads of ``tag`` on ``unit``,
        through the real receive path. Simulated bus only."""
        return self._mock_call(lambda bus: bus.inject_read(unit, tag, count))

    @command()
    def rfid_mock_unit(self, unit: int, plugged: bool | None = None,
                       swap: bool = False):
        """Unplug / replug a unit, or swap its decoder (new serial number).
        Simulated bus only."""
        return self._mock_call(lambda bus: bus.set_unit(unit, plugged, bool(swap)))

    @command()
    def rfid_mock_animals(self, running: bool = True):
        """Start or stop the wandering simulated tags. Simulated bus only."""
        def apply(bus):
            bus.animals_paused = not running
        return self._mock_call(apply)

    # ══════════════════════════════════════════════════════════════════════
    # Bus lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def _bring_up_bus(self) -> None:
        port = self.config.get("rfid.serial_port", "") or ""
        baud = int(self.config.get("rfid.baud", 19200))
        opener = None
        self._mock_bus = None
        if self._mock_enabled():
            opts = MockOptions.from_config(self.config.get)

            def opener():
                self._mock_bus = MockBusSerial(opts)
                return self._mock_bus

            port = MOCK_PORT
            self.logger.warning(
                f"RFID: SIMULATED bus ({opts.units} units, {opts.tags} tags); "
                "rfid.mock.enabled is on, reads are not real")
        connected = False
        try:
            connected = self.bus.connect(port=port, baud=baud, opener=opener)
        except Exception as e:
            self.logger.warning(f"RFID: bus connect failed: {e}")

        if connected:
            self.hardware_fault = None
            if self.config.get("rfid.scan_on_start", True):
                threading.Timer(1.5, self._safe_scan).start()
        else:
            self.hardware_fault = "No RFID reader found on any serial port"
            self.logger.warning("RFID: no reader found on any serial port")

    def _teardown_bus(self) -> None:
        try:
            self.bus.disconnect()
        except Exception:
            pass

    def _safe_scan(self) -> None:
        try:
            if self.bus.is_connected:
                self.bus.scan_bus()
        except Exception as e:
            self.logger.debug(f"RFID: scan failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # MJPEG "pings" view
    # ══════════════════════════════════════════════════════════════════════

    _W = 820
    _H = 440
    _PAD = 14
    _LIST_W = 210
    _LANE_H = 46
    _FONT = cv2.FONT_HERSHEY_SIMPLEX
    _RATE_WINDOW_S = 60

    def _bus_status_label(self) -> str:
        if self.bus.is_connected:
            return f"bus {self.bus.port}"
        return "no reader"

    def _render_monitor_frame(self) -> bytes | None:
        try:
            now = time.time()
            hist = float(self.config.get("monitoring.history_secs", 30) or 30)
            flash = float(self.config.get("monitoring.ping_flash_secs", 1.2) or 1.2)

            with self._pings_lock:
                recent = [p for p in self._pings if now - p.ts <= hist]
                total = self._total_reads
                last = self._last_ping
                units_seen = len(self.bus.units)

            frame = np.full((self._H, self._W, 3), 16, dtype=np.uint8)

            rate = sum(1 for p in recent if now - p.ts <= self._RATE_WINDOW_S)
            header = (f"RFID  |  {self._bus_status_label()}  |  {total} reads  |  "
                      f"~{rate}/min  |  {units_seen} unit(s)")
            cv2.putText(frame, header, (self._PAD, 26), self._FONT, 0.55,
                        (210, 210, 210), 1, cv2.LINE_AA)
            cv2.line(frame, (0, 38), (self._W, 38), (46, 46, 46), 1)

            rect = (self._PAD, 52,
                    self._W - self._LIST_W - self._PAD, self._H - self._PAD)
            t = (now, hist, flash)
            if recent:
                self._draw_timeline(frame, recent, t, rect)
                self._draw_flash_banner(frame, last, t, rect)
            else:
                self._draw_waiting(frame, rect)

            self._draw_side_list(frame, recent, now)
            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            return jpeg.tobytes()

        except Exception as e:
            self.logger.error(f"RFID frame render error: {e}")
            return None

    def _draw_waiting(self, frame, rect) -> None:
        x0, y0, x1, y1 = rect
        msg = "Waiting for transponders..."
        (tw, _), _ = cv2.getTextSize(msg, self._FONT, 0.7, 1)
        cv2.putText(frame, msg, (x0 + (x1 - x0 - tw) // 2, (y0 + y1) // 2),
                    self._FONT, 0.7, (120, 120, 120), 1, cv2.LINE_AA)

    def _draw_timeline(self, frame, recent, t, rect) -> None:
        now, hist, flash = t
        x0, y0, x1, y1 = rect
        plot_w = x1 - x0

        # lanes: most-recently-seen tags first, capped at what fits
        order, seen = [], set()
        for p in reversed(recent):
            if p.tag not in seen:
                seen.add(p.tag)
                order.append(p.tag)
        max_lanes = max(1, (y1 - y0) // self._LANE_H)
        lane_of = {tag: i for i, tag in enumerate(order[:max_lanes])}

        for s in range(0, int(hist) + 1, 10):
            gx = int(x1 - s / hist * plot_w)
            cv2.line(frame, (gx, y0), (gx, y1), (32, 32, 32), 1)
            cv2.putText(frame, f"-{s}s", (gx + 3, y1 - 4), self._FONT, 0.32,
                        (90, 90, 90), 1, cv2.LINE_AA)

        for tag, lane in lane_of.items():
            ly = y0 + lane * self._LANE_H
            lmid = ly + self._LANE_H // 2
            cv2.line(frame, (x0, lmid), (x1, lmid), (40, 40, 40), 1)
            cv2.putText(frame, tag[-8:], (x0 + 2, ly + 13), self._FONT, 0.36,
                        (150, 150, 150), 1, cv2.LINE_AA)

        for p in recent:
            lane = lane_of.get(p.tag)
            if lane is None:
                continue
            age = now - p.ts
            px = int(x1 - age / hist * plot_w)
            lmid = y0 + lane * self._LANE_H + self._LANE_H // 2
            fresh = max(0.0, 1.0 - age / flash)
            col = (int(90 + 130 * fresh), int(200 + 40 * fresh), int(120 + 60 * fresh))
            cv2.line(frame, (px, lmid - 14), (px, lmid + 14), col, 2, cv2.LINE_AA)
            cv2.circle(frame, (px, lmid), 4, col, -1, cv2.LINE_AA)
            if fresh > 0:
                r = int(6 + 26 * (1 - fresh))
                shade = int(40 + 120 * fresh)
                cv2.circle(frame, (px, lmid), r, (shade, shade, shade), 1, cv2.LINE_AA)

    def _draw_flash_banner(self, frame, last, t, rect) -> None:
        now, _hist, flash = t
        if last is None or now - last.ts >= flash:
            return
        x0, y0, x1, _ = rect
        k = 1.0 - (now - last.ts) / flash
        txt = f"PING  {last.tag[-10:]}  (unit {last.unit})"
        scale = 0.8 + 0.15 * k
        (tw, _th), _ = cv2.getTextSize(txt, self._FONT, scale, 2)
        g = int(120 + 135 * k)
        cv2.putText(frame, txt, (x0 + (x1 - x0 - tw) // 2, y0 + 40), self._FONT,
                    scale, (80, g, 140), 2, cv2.LINE_AA)

    def _draw_side_list(self, frame, recent, now) -> None:
        x0 = self._W - self._LIST_W
        cv2.line(frame, (x0 - 1, 40), (x0 - 1, self._H), (46, 46, 46), 1)
        cv2.putText(frame, "recent", (x0 + 6, 58), self._FONT, 0.42,
                    (150, 150, 150), 1, cv2.LINE_AA)
        for i, p in enumerate(list(reversed(recent))[:9]):
            y = 80 + i * 34
            age = now - p.ts
            fresh = max(0.0, 1.0 - age / 1.2)
            col = (int(150 + 60 * fresh), int(180 + 60 * fresh), int(150 + 40 * fresh))
            cv2.putText(frame, p.tag[-10:], (x0 + 6, y), self._FONT, 0.44,
                        col, 1, cv2.LINE_AA)
            cv2.putText(frame, f"unit {p.unit}   {age:4.1f}s ago", (x0 + 6, y + 14),
                        self._FONT, 0.34, (120, 120, 120), 1, cv2.LINE_AA)

    # ══════════════════════════════════════════════════════════════════════
    # Streaming lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def start_streaming(self) -> bool:
        if self.is_streaming:
            return False
        port = int(self.config.get("monitoring._port", 8083))
        self.is_streaming = self.monitor_stream.start(port)
        if self.is_streaming:
            ip = getattr(self.network, "ip", "?")
            self.logger.info(f"RFID monitoring stream on http://{ip}:{port}/video_feed")
        return self.is_streaming

    def stop_streaming(self) -> bool:
        if not self.is_streaming:
            return False
        self.monitor_stream.stop()
        self.is_streaming = False
        return True

    # ══════════════════════════════════════════════════════════════════════
    # Module lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def start(self) -> bool:
        if not super().start():
            return False
        self._start_store()
        self._bring_up_bus()
        self.start_streaming()
        self._start_sweeper()
        return True

    def stop(self) -> bool:
        try:
            self._stop_sweeper()
            self.stop_streaming()
            self._teardown_bus()
            self._stop_store()
        except Exception as e:
            self.logger.error(f"RFID: error during stop: {e}")
        return super().stop()

    def cleanup(self):
        try:
            self._stop_sweeper()
            if self.is_recording:
                self._stop_recording()
            self.stop_streaming()
            self._teardown_bus()
            self._stop_store()
        except Exception as e:
            self.logger.error(f"RFID cleanup error: {e}")


def main():
    rfid = RFIDModule()
    rfid.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        rfid.stop()


if __name__ == "__main__":
    main()
