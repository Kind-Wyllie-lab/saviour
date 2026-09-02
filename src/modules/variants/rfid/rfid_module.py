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
    with a flash on each fresh read) on ``monitoring._port`` (default 8083).

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

from modules.mjpeg_stream import MJPEGStreamServer
from modules.module import Module, check, command


@dataclass
class Ping:
    ts: float          # epoch seconds of the first arriving byte of the packet
    unit: int          # reader unit address
    tag: str           # transponder id, hex, upper-case
    type_name: str      # e.g. "Trovan Unique"


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
                             type_name: str, ts: float) -> None:
        """Called from the bus reader thread for every spontaneous read."""
        tag = tid.hex().upper() if isinstance(tid, (bytes, bytearray)) else str(tid)
        self._record_ping(Ping(ts=ts, unit=unit_addr, tag=tag,
                               type_name=type_name or "Unknown"))

    def _record_ping(self, ping: Ping) -> None:
        with self._pings_lock:
            self._pings.append(ping)
            self._total_reads += 1
            self._last_ping = ping

        self.logger.info(
            f"RFID ping: unit {ping.unit:#04x} tag {ping.tag} ({ping.type_name})"
        )

        # With presence tracking on, the raw per-ping status message + CSV row
        # are only kept when `record` asks for them; the enter/exit events
        # carry the useful signal. With it off, behaviour is unchanged.
        if self._want_raw_output():
            self._send_status_safe({
                "type": "rfid_read",
                "unit_address": ping.unit,
                "transponder_id": ping.tag,
                "transponder_type": ping.type_name,
                "packet_ts": ping.ts,
                "total_reads": self._total_reads,
            })
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
        # Presence keys just rebuild the tracker; only non-presence rfid.*
        # changes (serial port, baud, ...) need a bus reconnect.
        self._rebuild_presence()
        self._start_sweeper()
        if any(not k.startswith("rfid.presence") for k in rfid_keys):
            self.logger.info("RFID config changed - reconnecting bus")
            self._teardown_bus()
            self._bring_up_bus()

    @check()
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

    @command()
    def rfid_scan(self):
        """Broadcast a bus logon so every reader replies with its address."""
        if not self.bus.is_connected:
            return {"result": "error", "message": "bus not connected"}
        self.bus.scan_bus()
        return {"result": "success"}

    # ══════════════════════════════════════════════════════════════════════
    # Bus lifecycle
    # ══════════════════════════════════════════════════════════════════════

    def _bring_up_bus(self) -> None:
        port = self.config.get("rfid.serial_port", "") or ""
        baud = int(self.config.get("rfid.baud", 19200))
        connected = False
        try:
            connected = self.bus.connect(port=port, baud=baud)
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
        self._bring_up_bus()
        self.start_streaming()
        self._start_sweeper()
        return True

    def stop(self) -> bool:
        try:
            self._stop_sweeper()
            self.stop_streaming()
            self._teardown_bus()
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
