#!/usr/bin/env python3
"""
SAVIOUR System - RFID Module (minimal / demo)

A thin :class:`Module` wrapper around the Trovan LID650/665 RS485 bus driver
(:class:`rfid_bus.RS485Bus`). It:

  * opens the RS485 bus (auto-detecting the serial port) and scans for reader
    units on start,
  * on every transponder read ("ping") buffers it for the live view, tells the
    controller, and - while a recording session is active - appends a row to
    the current segment CSV,
  * serves an MJPEG "pings" monitoring stream (a scrolling per-tag timeline
    with a flash on each fresh read) on ``monitoring._port`` (default 8083).

With no reader attached, or with ``rfid.simulate`` true (the default), it runs a
synthetic ping generator so the stream and the recording path are demoable on
any Pi without hardware.

Author: Andrew SG
"""

import collections
import os
import random
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


# Fake transponder ids used in simulate mode - just need to look plausible.
_SIM_TAG_IDS = [
    "0000DEADBEEF", "0000CAFEF00D", "00001234ABCD", "0000A1B2C3D4",
    "0000FEEDFACE", "00005A5A5A5A", "0000900DBEEF", "0000ABAD1DEA",
]


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

        # ── Bus ─────────────────────────────────────────────────────────
        self.bus = RS485Bus()
        self.bus.on_transponder_read = self._on_transponder_read
        self.bus.on_bus_status = self._on_bus_status

        # ── Simulation ─────────────────────────────────────────────────
        self._sim_stop = threading.Event()
        self._sim_thread: threading.Thread | None = None

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
            "rfid_inject_ping": self.rfid_inject_ping,
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
        self._send_status_safe({
            "type": "rfid_read",
            "unit_address": ping.unit,
            "transponder_id": ping.tag,
            "transponder_type": ping.type_name,
            "packet_ts": ping.ts,
            "total_reads": self._total_reads,
        })

        if self.is_recording and self._csv_handle is not None:
            try:
                utc = self.facade.get_utc_time(int(ping.ts))
                row = (f"{int(ping.ts * 1e9)},{utc},{ping.unit},"
                       f"{ping.tag},{ping.type_name}\n")
                self._csv_handle.write(row)
                self._csv_handle.flush()
            except Exception as e:
                self.logger.error(f"RFID: failed to write ping to CSV: {e}")

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

    def _start_new_recording(self) -> bool:
        filename = self._get_rfid_filename()
        self.current_rfid_filename = filename
        self.facade.add_session_file(filename)
        self._open_csv(filename)
        self.is_recording = True
        self.logger.info(f"RFID recording started -> {filename}")
        return True

    def _start_next_recording_segment(self) -> bool:
        if self.current_rfid_filename:
            self.facade.stage_file_for_export(self.current_rfid_filename)
        self._close_csv()

        filename = self._get_rfid_filename()
        self.current_rfid_filename = filename
        self.facade.add_session_file(filename)
        self._open_csv(filename)
        self.logger.info(f"RFID segment rotated -> {filename}")
        return True

    def _stop_recording(self) -> bool:
        try:
            self.is_recording = False
            self._close_csv()
            if self.current_rfid_filename:
                self.facade.stage_file_for_export(self.current_rfid_filename)
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
        if any(k.startswith("rfid.") for k in updated_keys):
            self.logger.info("RFID config changed - reconnecting bus")
            self._teardown_bus()
            self._bring_up_bus()

    @check()
    def _check_rfid(self):
        if self.bus.is_connected:
            return True, (f"RFID bus on {self.bus.port}, "
                          f"{len(self.bus.units)} unit(s) seen")
        if self.config.get("rfid.simulate", True):
            return True, "simulate mode (no physical reader)"
        return False, "no RFID reader found on any serial port"

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

    @command()
    def rfid_inject_ping(self, tag: str | None = None, unit: int = 1):
        """Inject one synthetic ping - handy as a demo trigger."""
        self._record_ping(Ping(
            ts=time.time(), unit=int(unit),
            tag=(tag or random.choice(_SIM_TAG_IDS)).upper(),
            type_name="Injected",
        ))
        return {"result": "success"}

    # ══════════════════════════════════════════════════════════════════════
    # Bus / simulation lifecycle
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
            self._stop_sim()
            if self.config.get("rfid.scan_on_start", True):
                threading.Timer(1.5, self._safe_scan).start()
        elif self.config.get("rfid.simulate", True):
            self.logger.info("RFID: no reader - starting simulate mode")
            self._start_sim()

    def _teardown_bus(self) -> None:
        self._stop_sim()
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

    def _start_sim(self) -> None:
        if self._sim_thread and self._sim_thread.is_alive():
            return
        self._sim_stop.clear()
        self._sim_thread = threading.Thread(
            target=self._sim_loop, daemon=True, name="rfid-sim")
        self._sim_thread.start()

    def _stop_sim(self) -> None:
        self._sim_stop.set()
        if self._sim_thread and self._sim_thread.is_alive():
            self._sim_thread.join(timeout=2)
        self._sim_thread = None

    def _sim_loop(self) -> None:
        pool = max(1, int(self.config.get("rfid.simulate_tag_pool", 4)))
        tags = _SIM_TAG_IDS[:pool]
        while not self._sim_stop.is_set():
            base = float(self.config.get("rfid.simulate_interval_s", 3.0))
            # jittered wait so the stream doesn't look metronomic
            if self._sim_stop.wait(random.uniform(base * 0.4, base * 1.6)):
                break
            self._record_ping(Ping(
                ts=time.time(),
                unit=random.choice([1, 1, 1, 2]),
                tag=random.choice(tags),
                type_name="Trovan Unique (sim)",
            ))

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
        if self._sim_thread and self._sim_thread.is_alive():
            return "simulate mode"
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
        return True

    def stop(self) -> bool:
        try:
            self.stop_streaming()
            self._teardown_bus()
        except Exception as e:
            self.logger.error(f"RFID: error during stop: {e}")
        return super().stop()

    def cleanup(self):
        try:
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
