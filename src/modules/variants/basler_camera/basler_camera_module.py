#!/usr/bin/env python3
"""
SAVIOUR System - Basler Camera Module  (SKETCH / first cut — not hardware-verified)

A camera module for Basler GigE / USB3 industrial cameras, driven by pypylon
(Basler's official Python binding for the Pylon SDK).

Why this does NOT subclass CameraBase
-------------------------------------
`src/modules/camera_base.py` is the shared base for every *Picamera2* camera —
it instantiates `Picamera2()` directly in `__init__`, drives libcamera sensor
modes / transforms, and records via picamera2's in-process `H264Encoder` +
`SplittableOutput`. None of that applies to a Basler: frames come out of a
`pylon.InstantCamera` grab loop as plain numpy arrays and we encode them
ourselves. So this class subclasses `Module` directly (like the template) and
re-implements only the pieces CameraBase would otherwise provide:

  * a background grab thread                          (CameraBase: libcamera callbacks)
  * software H.264 encode, one child ffmpeg per segment
  * a per-frame timestamp CSV sidecar                 (SAME column names as
    CameraBase.BASE_CSV_COLUMNS so tools/analyse_framesync.py works unchanged)
  * an MJPEG preview via MJPEGStreamServer            (identical to CameraBase)
  * config-driven camera controls

The module-side recording lifecycle in `src/modules/recording.py` (segment
rotation, health metadata, export signalling) is reused *unchanged* through the
three hooks `_start_new_recording` / `_start_next_recording_segment` /
`_stop_recording`.

Config key mapping (basler_camera_config.json  ->  Pylon GenICam node)
--------------------------------------------------------------------
  basler.width / height / offset_x / offset_y  -> Width / Height / OffsetX / OffsetY
  basler.pixel_format                          -> PixelFormat            (BGR8 keeps OpenCV happy)
  basler.fps + basler.limit_fps               -> AcquisitionFrameRate + AcquisitionFrameRateEnable
  basler.exposure_auto / exposure_time_us      -> ExposureAuto / ExposureTime
  basler.gain_auto / gain_db                   -> GainAuto / Gain
  basler.balance_white_auto                    -> BalanceWhiteAuto
  basler.gamma                                 -> Gamma
  basler.hflip / vflip                         -> ReverseX / ReverseY
  basler.packet_size / inter_packet_delay_us   -> GevSCPSPacketSize / GevSCPD   (GigE only)
  basler.ptp_enable                            -> PtpEnable / GevIEEE1588        (GigE only; see below)

PTP / timestamps
----------------
Basler ace GigE cameras implement IEEE-1588 (PTP) natively. If `basler.ptp_enable`
is set and the camera locks to the same grandmaster as the SAVIOUR controller,
`chunk`-appended frame timestamps (ChunkTimestamp) are already on the shared
PTP timebase — that is the number to write into the CSV `timestamp_ns` column.
Until that is wired + verified against real hardware this sketch falls back to
`time.time_ns()` at grab time and flags it in the CSV via `wall_mono_offset_s`.

Author: Andrew SG
Created: 2026-09-01
"""

import os
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.mjpeg_stream import MJPEGStreamServer
from modules.module import Module, check, command

try:
    from pypylon import genicam, pylon
except ImportError:  # keep import-time safe on a dev box without the Pylon runtime
    pylon = None
    genicam = None


# CSV column names copied verbatim from CameraBase so the framesync tooling
# (tools/analyse_framesync.py, src/controller/video_compose.py) treats a Basler
# session identically to a Pi-camera one. Columns this pipeline can't yet fill
# are written empty.
CSV_COLUMNS = [
    "frame_id", "timestamp_ns", "timestamp_utc", "wall_mono_offset_s",
    "delta_ms", "dropped_before", "sync_lag_us", "exposure_time_us",
    "analogue_gain", "colour_gain_r", "colour_gain_b",
]

_TEXT_SIZE_SCALE = {"small": 0.5, "medium": 0.8, "large": 1.2}


class BaslerCameraModule(Module):
    CONFIG_FILENAME = "basler_camera_config.json"

    def __init__(self, module_type: str = "basler_camera"):
        super().__init__(module_type)
        self.config.load_module_config(self.CONFIG_FILENAME)
        self.description = "Basler GigE/USB3 camera (pypylon)"

        if pylon is None:
            raise RuntimeError(
                "pypylon is not installed — run variants/basler_camera/install_pypylon.sh "
                "(or `env/bin/pip install pypylon`)."
            )

        # --- hardware -------------------------------------------------------
        self.camera: "pylon.InstantCamera | None" = None
        self._converter = pylon.ImageFormatConverter()
        self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        self._open_camera()
        self._apply_camera_config(updated_keys=None)

        # --- capture / encode state -------------------------------------
        self._grab_thread: threading.Thread | None = None
        self._grab_stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None   # BGR, for the preview + overlay
        self._ffmpeg: subprocess.Popen | None = None
        self._csv_file = None
        self._csv_path: str | None = None
        self._frame_id = 0
        self._prev_grab_ns: int | None = None
        self._last_grab_wall = 0.0
        self._fps_ema = 0.0

        # --- preview --------------------------------------------------------
        self.monitor_stream = MJPEGStreamServer(logger=self.logger, name="Basler")
        self.is_streaming = False
        self._register_routes()

        self.is_recording = False

    # ------------------------------------------------------------------ #
    # Camera open / configure
    # ------------------------------------------------------------------ #
    def _open_camera(self) -> None:
        """Find and open the configured device (by serial / user id, else first)."""
        tl_factory = pylon.TlFactory.GetInstance()
        serial = self.config.get("basler.device_serial")
        user_id = self.config.get("basler.device_user_id")

        devices = tl_factory.EnumerateDevices()
        if not devices:
            raise RuntimeError("No Basler devices found on any transport layer")

        chosen = None
        for dev in devices:
            if serial and dev.GetSerialNumber() == str(serial):
                chosen = dev
                break
            if user_id and dev.GetUserDefinedName() == user_id:
                chosen = dev
                break
        if chosen is None:
            if serial or user_id:
                self.logger.warning(
                    "Configured Basler device not found — falling back to first device"
                )
            chosen = devices[0]

        self.camera = pylon.InstantCamera(tl_factory.CreateDevice(chosen))
        self.camera.Open()
        self.logger.info(
            f"Opened Basler {self.camera.GetDeviceInfo().GetModelName()} "
            f"(serial {self.camera.GetDeviceInfo().GetSerialNumber()})"
        )

    def _try_set(self, node_name: str, value) -> None:
        """Best-effort GenICam node write — not every node exists on every model
        (GigE-only nodes on a USB3 cam, etc). Log and continue."""
        try:
            node = getattr(self.camera, node_name, None)
            if node is None:
                return
            node.SetValue(value)
        except Exception as e:  # noqa: BLE001 - sketch; covers genicam.GenericException too
            self.logger.debug(f"Basler node {node_name}={value!r} rejected: {e}")

    def _apply_camera_config(self, updated_keys: list[str] | None) -> None:
        """Push basler.* config onto the camera. Called once at init and again
        from configure_module_special() on a live config change.

        SKETCH: most of these can be set while grabbing; width/height/pixel_format
        cannot and need a grab stop/restart — not yet split out here.
        """
        c = self.config
        self._try_set("Width", int(c.get("basler.width", 1920)))
        self._try_set("Height", int(c.get("basler.height", 1080)))
        self._try_set("OffsetX", int(c.get("basler.offset_x", 0)))
        self._try_set("OffsetY", int(c.get("basler.offset_y", 0)))
        self._try_set("PixelFormat", c.get("basler.pixel_format", "BGR8"))
        self._try_set("ReverseX", bool(c.get("basler.hflip", False)))
        self._try_set("ReverseY", bool(c.get("basler.vflip", False)))

        if c.get("basler.limit_fps", True):
            self._try_set("AcquisitionFrameRateEnable", True)
            self._try_set("AcquisitionFrameRate", float(c.get("basler.fps", 30)))
        else:
            self._try_set("AcquisitionFrameRateEnable", False)

        self._try_set("ExposureAuto", c.get("basler.exposure_auto", "Continuous"))
        if c.get("basler.exposure_auto", "Continuous") == "Off":
            self._try_set("ExposureTime", float(c.get("basler.exposure_time_us", 5000)))
        self._try_set("GainAuto", c.get("basler.gain_auto", "Continuous"))
        if c.get("basler.gain_auto", "Continuous") == "Off":
            self._try_set("Gain", float(c.get("basler.gain_db", 0.0)))
        self._try_set("BalanceWhiteAuto", c.get("basler.balance_white_auto", "Continuous"))
        self._try_set("Gamma", float(c.get("basler.gamma", 1.0)))

        # GigE transport tuning — no-ops on USB3.
        self._try_set("GevSCPSPacketSize", int(c.get("basler.packet_size", 8192)))
        self._try_set("GevSCPD", int(c.get("basler.inter_packet_delay_us", 0)))

        # PTP — node name differs by firmware generation (PtpEnable vs GevIEEE1588).
        ptp = bool(c.get("basler.ptp_enable", True))
        self._try_set("PtpEnable", ptp)
        self._try_set("GevIEEE1588", ptp)

        if updated_keys:
            self.logger.info(f"Applied Basler config change: {updated_keys}")

    def configure_module_special(self, updated_keys: list[str] | None):
        if not updated_keys or not any(k.startswith("basler.") for k in updated_keys):
            return
        # TODO: geometry/pixel-format changes need a grab stop+restart; for now
        # apply live and let the operator restart the module for those.
        self._apply_camera_config(updated_keys)

    # ------------------------------------------------------------------ #
    # Grab loop
    # ------------------------------------------------------------------ #
    def _start_grabbing(self) -> None:
        if self._grab_thread and self._grab_thread.is_alive():
            return
        strat = getattr(
            pylon,
            f"GrabStrategy_{self.config.get('basler.grab_strategy', 'LatestImageOnly')}",
            pylon.GrabStrategy_LatestImageOnly,
        )
        self.camera.StartGrabbing(strat)
        self._grab_stop.clear()
        self._grab_thread = threading.Thread(
            target=self._grab_loop, daemon=True, name="basler-grab"
        )
        self._grab_thread.start()

    def _stop_grabbing(self) -> None:
        self._grab_stop.set()
        if self._grab_thread:
            self._grab_thread.join(timeout=3)
            self._grab_thread = None
        if self.camera and self.camera.IsGrabbing():
            self.camera.StopGrabbing()

    def _grab_loop(self) -> None:
        timeout = int(self.config.get("basler.grab_timeout_ms", 1000))
        while not self._grab_stop.is_set() and self.camera.IsGrabbing():
            try:
                res = self.camera.RetrieveResult(timeout, pylon.TimeoutHandling_Return)
            except genicam.GenericException as e:
                self.logger.warning(f"Basler RetrieveResult raised: {e}")
                continue
            if res is None or not res.GrabSucceeded():
                if res is not None:
                    res.Release()
                continue

            grab_ns = time.time_ns()  # TODO: prefer res.ChunkTimestamp on a PTP-locked cam
            frame = self._converter.Convert(res).GetArray()  # HxWx3 BGR uint8
            res.Release()

            with self._frame_lock:
                self._latest_frame = frame

            if self.is_recording:
                self._write_frame(frame, grab_ns)

            self._update_fps(grab_ns)

    def _update_fps(self, grab_ns: int) -> None:
        if self._prev_grab_ns is not None:
            dt = (grab_ns - self._prev_grab_ns) / 1e9
            if dt > 0:
                inst = 1.0 / dt
                self._fps_ema = inst if self._fps_ema == 0 else 0.9 * self._fps_ema + 0.1 * inst
        self._prev_grab_ns = grab_ns
        self._last_grab_wall = time.time()

    # ------------------------------------------------------------------ #
    # Recording — ffmpeg per segment + CSV sidecar
    # ------------------------------------------------------------------ #
    def _video_filename(self) -> str:
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        ext = self.config.get("recording.recording_filetype", "ts")
        return (
            f"{self.facade.get_filename_prefix()}"
            f"_({self.facade.get_segment_id()}_{strtime}).{ext}"
        )

    def _open_segment(self) -> None:
        """Open a fresh ffmpeg child + timestamp CSV for the current segment."""
        filename = self._video_filename()
        self.facade.add_session_file(filename)

        with self._frame_lock:
            probe = self._latest_frame
        if probe is None:
            # grab loop hasn't produced a frame yet — fall back to config geometry
            h = int(self.config.get("basler.height", 1080))
            w = int(self.config.get("basler.width", 1920))
        else:
            h, w = probe.shape[:2]

        fps = float(self.config.get("basler.fps", 30))
        bitrate_mb = float(self.config.get("recording.bitrate_mb", 8))
        preset = self.config.get("recording._ffmpeg_preset", "ultrafast")
        extra = self.config.get("recording._ffmpeg_extra_args", []) or []

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}", "-r", f"{fps:g}",
            "-i", "pipe:0",
            "-an",
            "-c:v", "libx264", "-preset", preset, "-tune", "zerolatency",
            "-pix_fmt", "yuv420p", "-b:v", f"{bitrate_mb:g}M",
            *extra,
            "-f", "mpegts", filename,
        ]
        self.logger.info(f"Starting ffmpeg for segment: {filename}")
        self._ffmpeg = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.current_video_segment = filename

        self._csv_path = f"{os.path.splitext(filename)[0]}_timestamps.csv"
        self._csv_file = open(self._csv_path, "w", buffering=1 << 20)
        self._csv_file.write(",".join(CSV_COLUMNS) + "\n")
        self.facade.add_session_file(self._csv_path)
        self._frame_id = 0
        self._prev_grab_ns = None

    def _close_segment(self) -> None:
        if self._ffmpeg is not None:
            try:
                self._ffmpeg.stdin.close()
                self._ffmpeg.wait(timeout=10)
            except Exception as e:  # noqa: BLE001 - sketch
                self.logger.warning(f"ffmpeg did not exit cleanly: {e}")
                self._ffmpeg.kill()
            self._ffmpeg = None
        if self.current_video_segment:
            self.facade.stage_file_for_export(self.current_video_segment)
        if self._csv_file is not None:
            self._csv_file.flush()
            os.fsync(self._csv_file.fileno())
            self._csv_file.close()
            self._csv_file = None
            if self._csv_path:
                self.facade.stage_file_for_export(self._csv_path)
                self._csv_path = None

    def _write_frame(self, frame: np.ndarray, grab_ns: int) -> None:
        if self._ffmpeg is None or self._ffmpeg.stdin is None:
            return

        out = frame
        if self.config.get("basler.overlay_timestamp", True):
            out = self._overlay_timestamp(frame.copy(), grab_ns)

        try:
            self._ffmpeg.stdin.write(np.ascontiguousarray(out).tobytes())
        except BrokenPipeError:
            self.logger.error("ffmpeg pipe broke mid-segment — encoder died")
            return

        delta_ms = ""
        if self._prev_grab_ns is not None:
            delta_ms = f"{(grab_ns - self._prev_grab_ns) / 1e6:.3f}"
        row = [
            self._frame_id,
            grab_ns,
            self.facade.get_utc_time(grab_ns // 1_000_000_000),
            "",          # wall_mono_offset_s — TODO once PTP ChunkTimestamp is wired
            delta_ms,
            0,           # dropped_before — TODO from ChunkFrameCounter gaps
            "",          # sync_lag_us — n/a without libcamera sync
            "", "", "", "",   # exposure/gain/awb — TODO from ChunkData
        ]
        self._csv_file.write(",".join(str(x) for x in row) + "\n")
        self._frame_id += 1

    def _overlay_timestamp(self, frame: np.ndarray, grab_ns: int) -> np.ndarray:
        scale = _TEXT_SIZE_SCALE.get(self.config.get("basler.text_size", "medium"), 0.8)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(grab_ns / 1e9))
        ts = f"{ts}.{int(grab_ns % 1_000_000_000) // 1_000_000:03d}Z"
        cv2.putText(frame, ts, (10, int(30 * scale) + 10),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, ts, (10, int(30 * scale) + 10),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
        return frame

    # --- Module recording hooks (called by src/modules/recording.py) ---
    def _start_new_recording(self) -> bool:
        if not self.camera.IsGrabbing():
            self._start_grabbing()
        self._open_segment()
        self.recording_start_time = time.time()
        return True

    def _start_next_recording_segment(self) -> bool:
        self._close_segment()
        self._open_segment()
        return True

    def _stop_recording(self) -> bool:
        try:
            self._close_segment()
            return True
        except Exception as e:  # noqa: BLE001 - sketch
            self.logger.exception(f"Basler _stop_recording failed: {e}")
            return False

    def _check_recording_alive(self) -> tuple[bool, str | None]:
        """Grab loop has gone silent while we think we're recording."""
        if not self.is_recording:
            return True, None
        gap = time.time() - self._last_grab_wall
        expected = 1.0 / max(float(self.config.get("basler.fps", 30)), 1.0)
        if gap > max(2.0, expected * 20):
            return False, f"no frame from Basler for {gap:.1f}s"
        return True, None

    # ------------------------------------------------------------------ #
    # Preview stream
    # ------------------------------------------------------------------ #
    def _register_routes(self):
        @self.monitor_stream.app.route("/snapshot.jpg")
        def snapshot():
            jpeg = self.monitor_stream.get_latest_frame()
            if jpeg is None:
                return ("No frame available", 503)
            return (jpeg, 200, {"Content-Type": "image/jpeg"})

    def _preview_render(self) -> bytes | None:
        with self._frame_lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
        if frame is None:
            return None
        if self.config.get("basler.overlay_framerate_on_preview", True):
            cv2.putText(frame, f"{self._fps_ema:4.1f} fps",
                        (frame.shape[1] - 150, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 0), 2, cv2.LINE_AA)
        q = 60 if self.config.get("basler.livestream_quality") == "low" else 80
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])
        return buf.tobytes() if ok else None

    @command()
    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        if self.is_streaming:
            return False
        if not self.camera.IsGrabbing():
            self._start_grabbing()
        self.monitor_stream.render_fn = self._preview_render
        self.is_streaming = self.monitor_stream.start(8080)
        self.facade.send_status({"type": "streaming_started", "port": 8080,
                                 "status": "success"})
        return self.is_streaming

    @command()
    def stop_streaming(self) -> bool:
        if not self.is_streaming:
            return False
        self.monitor_stream.stop()
        self.is_streaming = False
        self.facade.send_status({"type": "streaming_stopped", "status": "success"})
        return True

    # ------------------------------------------------------------------ #
    # Health / readiness
    # ------------------------------------------------------------------ #
    @check()
    def _check_camera(self):
        if self.camera is None or not self.camera.IsOpen():
            return False, "Basler camera not open"
        return True, f"{self.camera.GetDeviceInfo().GetModelName()} open"

    @check()
    def _check_ffmpeg(self):
        from shutil import which
        return (True, "ffmpeg present") if which("ffmpeg") else (False, "ffmpeg not on PATH")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        if not super().start():
            return False
        self._start_grabbing()
        self.start_streaming()
        return True

    def stop(self) -> bool:
        try:
            if self.is_recording:
                self._stop_recording()
            if self.is_streaming:
                self.stop_streaming()
            self._stop_grabbing()
            if self.camera and self.camera.IsOpen():
                self.camera.Close()
        except Exception as e:  # noqa: BLE001 - sketch
            self.logger.error(f"Error stopping Basler module: {e}")
        return super().stop()


def main():
    cam = BaslerCameraModule()
    cam.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        cam.stop()


if __name__ == "__main__":
    main()
