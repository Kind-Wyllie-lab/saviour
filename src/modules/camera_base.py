#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Camera Base Class

Shared infrastructure for every Picamera2-backed module (plain camera, loom
camera, APA camera, and any future camera variant). Ported from the original
camera_module.py, which had no module-specific logic of its own and so was
the natural reference implementation for what's actually shared.

Concrete camera modules subclass CameraBase and override a small set of
named hooks to add their own per-frame processing (tracking, detection,
overlays) and CSV columns — everything else (Picamera2 lifecycle, MJPEG
streaming server, segmented recording, the timestamp CSV sidecar, and base
overlay drawing) lives here once.

Author: Andrew SG
"""

import collections
import csv
import datetime
import os
import sys
import time
import threading
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import cv2
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput, SplittableOutput
from flask import Flask, Response, request

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check


@dataclass
class FrameTiming:
    """Per-frame timing/CSV values computed once and shared with subclass hooks."""
    timestamp_ns: int
    timestamp_utc: str
    ts_label: str          # "{module_name} {utc string}" — the on-frame overlay string
    actual_fps: Optional[float]
    delta_ms: Any           # float or "" — same convention as the base CSV columns
    dropped_before: Any      # int or ""


class CameraBase(Module):
    """Shared Picamera2 module base. Directly instantiable — a camera variant
    with no unique logic (e.g. plain CameraModule) needs only to set
    CONFIG_FILENAME."""

    CONFIG_FILENAME: str = "camera_config.json"
    CSV_EXTRA_COLUMNS: list = []          # subclass override, e.g. ["cx", "cy", "zone_state", "event"]
    _BUFFER_COUNT = 16
    _DEFAULT_BITRATE_MB = 5
    _STREAM_FPS = 24  # cap for high-fps cameras; low-fps cameras pass every frame

    BASE_CSV_COLUMNS = [
        "frame_id", "timestamp_ns", "timestamp_utc", "wall_mono_offset_s",
        "delta_ms", "dropped_before", "sync_lag_us", "exposure_time_us",
        "analogue_gain", "colour_gain_r", "colour_gain_b",
    ]

    # Config keys that require a full camera stop/reconfigure/restart.
    _CAMERA_RESTART_KEYS = {
        "camera.sensor_mode_index", "camera.width", "camera.height",
        "camera.bitrate_mb", "camera.sync_mode", "camera.sync_lock_exposure",
        "camera.sync_lock_awb", "camera.hflip", "camera.vflip", "camera.rotation",
    }
    # Config keys that can be applied live via set_controls() without stopping.
    _CAMERA_CONTROLS_ONLY_KEYS = {
        "camera.gain", "camera.brightness", "camera.exposure_time",
        "camera.manual_exposure", "camera.ae_enable",
        "camera.lens_position", "camera.autofocus_mode",
    }

    def __init__(self, module_type: str):
        super().__init__(module_type)
        self.config.load_module_config(self.CONFIG_FILENAME)

        # Initialize camera
        self.picam2 = Picamera2()
        self.height = None
        self.width = None
        self.fps = None
        self.mode = None
        self.gain = None

        # Get camera modes and sensor identity
        self.sensor_modes = self.picam2.sensor_modes
        self.sensor_model = self.picam2.camera_properties.get("Model", "").lower()
        self.has_autofocus = "imx708" in self.sensor_model
        self.logger.info(f"Sensor model: {self.sensor_model!r}, autofocus: {self.has_autofocus}")
        time.sleep(0.1)

        # Streaming variables
        self.streaming_app = Flask(__name__)
        self.streaming_server_thread = None
        self.streaming_server = None
        self.should_stop_streaming = False
        self.register_routes()

        self.latest_frame = None
        self.last_frame_timestamp = None
        self.frame_lock = threading.Lock()
        self._last_stream_encode_time = 0.0
        self._stream_interval_s = 0.0

        # Configure camera
        time.sleep(0.1)
        self._configure_camera()
        time.sleep(0.1)

        # State flags
        self.is_recording = False
        self.is_streaming = False

        # Per-frame callback caches — updated by _cache_frame_config()
        self._cb_monochrome        = False
        self._cb_overlay_timestamp = True
        self._cb_flip_code         = None   # None | -1 | 0 | 1 — hflip/vflip are done via hardware Transform
        self._cb_module_name       = None
        self._cache_frame_config()

        # Off-thread CSV write buffer: pre_callback appends rows here;
        # a background thread drains them so file I/O never stalls capture.
        self._csv_row_buffer  = collections.deque()
        self._csv_flush_stop  = threading.Event()
        self._csv_flush_thread = None  # type: Optional[threading.Thread]

        # Segment based recording
        self.current_video_segment = None
        self.last_video_segment = None

        # Pre-created segment for scheduled starts (set by _pre_create_first_segment,
        # consumed by _start_new_recording so start_encoder() is the only call at t0)
        self._prestaged_segment = None

        # Per-frame timestamp CSV sidecar
        self._timestamp_csv_file = None
        self._timestamp_csv_writer = None
        self._current_csv_path = None
        self._frame_id = 0
        self._csv_prev_ns = None  # previous frame timestamp for delta/drop calculation


    """Self Check"""
    @check()
    def _check_picam(self) -> tuple:
        if not self.picam2:
            return False, "No picam2 object"
        return True, "Picam2 object instantiated"


    @command()
    def get_sensor_modes(self):
        if not self.sensor_modes:
            return {"sensor_modes": []}

        # Identify the largest crop area across all modes to distinguish full-FoV modes.
        max_area = max(
            m['crop_limits'][2] * m['crop_limits'][3]
            for m in self.sensor_modes
        )

        enriched = []
        for i, mode in enumerate(self.sensor_modes):
            crop = mode['crop_limits']
            mode_area = crop[2] * crop[3]
            if mode_area >= max_area:
                fov = "Full FoV"
            else:
                pct = round(100 * mode_area / max_area)
                fov = f"Partial FoV ({pct}%)"

            w, h = mode['size']
            fps = mode['fps']
            enriched.append({
                "index": i,
                "size": [w, h],
                "fps": round(fps, 1),
                "bit_depth": mode['bit_depth'],
                "crop_limits": list(crop),
                "format": str(mode['format']),
                "label": f"Mode {i}: {w}×{h} @ {fps:.0f}fps — {fov}",
            })

        return {
            "sensor_modes": enriched,
            "sensor_model": self.sensor_model,
            "has_autofocus": self.has_autofocus,
        }


    @command()
    def trigger_autofocus(self):
        """Trigger a one-shot autofocus cycle (IMX708 / Camera Module 3 only)."""
        if not self.has_autofocus:
            return {"result": "error", "output": "Camera does not support autofocus"}
        if not self.picam2.started:
            return {"result": "error", "output": "Camera is not running"}
        try:
            self.picam2.set_controls({"AfMode": 1, "AfTrigger": 0})  # Auto + Start
            return {"result": "success"}
        except Exception as e:
            self.logger.error(f"trigger_autofocus error: {e}")
            return {"result": "error", "output": str(e)}


    def get_health(self) -> dict:
        """Extend base health with wall/monotonic offset for SensorTimestamp alignment."""
        health = super().get_health()
        health["wall_mono_offset_s"] = time.time() - time.monotonic()
        return health


    def _configure_module_extra(self, updated_keys) -> None:
        """Hook: subclass-specific config handling, called first in
        configure_module_special (before the shared camera restart-vs-live-controls
        branching below). Default: no-op."""
        pass


    def configure_module_special(self, updated_keys: Optional[list]):
        """Shared restart-vs-live-controls config handling for every camera module."""
        self._configure_module_extra(updated_keys)

        if self.is_streaming:
            self._restarting_stream = bool(
                updated_keys and self._CAMERA_RESTART_KEYS.intersection(updated_keys)
            )

            if self._restarting_stream:
                self.logger.info("Restarting stream to apply new configuration")
                self.stop_streaming()
                time.sleep(1)
                try:
                    self._configure_camera()
                    self.logger.info("Camera reconfigured successfully")
                except Exception as e:
                    self.logger.error(f"Error restarting streaming: {e}")

                try:
                    self.logger.info("Restarting stream with new settings")
                    self.start_streaming()
                    self.logger.info("Streaming restarted")
                except Exception as e:
                    self.logger.error(f"Error restarting streaming: {e}")

            self._restarting_stream = False

            _cb_keys = {"camera.monochrome", "camera.overlay_timestamp", "module.name"}
            if _cb_keys.intersection(updated_keys or []):
                self._cache_frame_config()

            fps = self.config.get("camera.fps", 25)
            # Keep self.fps current even though fps is applied live via set_controls
            # below rather than through a full _configure_camera() restart — other
            # code (dropped-frame CSV math, subclass tracking-rate decimation) reads
            # self.fps and would otherwise see a stale value from the last restart.
            self.fps = fps
            if self.config.get("camera.manual_exposure", False):
                exposure_time = self.config.get("camera.exposure_time", 10000)
            else:
                exposure_time = int(1_000_000 / fps)

            ae_enabled = bool(self.config.get("camera.ae_enable", False))
            sync_mode_str = self.config.get("camera.sync_mode", "none")
            if sync_mode_str in ("server", "client") and self.config.get("camera.sync_lock_exposure", False):
                ae_enabled = False  # sync lock overrides auto-gain to keep synced cameras' brightness matched

            live_controls = {
                "Brightness": self.config.get("camera.brightness"),
                "FrameRate": fps,
                "AeEnable": ae_enabled,
            }
            if not ae_enabled:
                live_controls["AnalogueGain"] = self.config.get("camera.gain", 1)
                live_controls["ExposureTime"] = exposure_time
            if self.has_autofocus:
                _AF_MODE_MAP = {"manual": 0, "auto": 1, "continuous": 2}
                af_mode_str = self.config.get("camera.autofocus_mode", "manual")
                af_mode = _AF_MODE_MAP.get(af_mode_str, 0)
                live_controls["AfMode"] = af_mode
                if af_mode == 0:
                    live_controls["LensPosition"] = float(self.config.get("camera.lens_position", 0.0))
            self.picam2.set_controls(live_controls)

        elif not self.is_streaming:
            try:
                self._configure_camera()
                self.logger.info("Camera reconfigured successfully (not streaming)")
            except Exception as e:
                self.logger.error(f"Error reconfiguring camera: {e}")


    def _cache_frame_config(self) -> None:
        """Cache config values that are read on every capture callback.

        Called once at startup and again whenever relevant config keys change,
        so the pre_callback never pays the dict-traversal cost per frame.
        """
        self._cb_monochrome        = self.config.get("camera.monochrome") is True
        self._cb_overlay_timestamp = self.config.get("camera.overlay_timestamp", True)
        self._cb_flip_code = None
        self._cb_rotation = getattr(self, "_rotation", 0)
        self._cb_module_name = self.facade.get_module_name() if hasattr(self, 'facade') else None
        # Clear layout caches so _apply_timestamp recomputes font_scale for the new text width
        self._ts_layout_main  = None
        self._ts_layout_lores = None

    def _csv_flush_worker(self) -> None:
        """Drain _csv_row_buffer to disk every 50 ms until stopped."""
        while not self._csv_flush_stop.wait(0.05):
            self._drain_csv_buffer()
        self._drain_csv_buffer()  # final flush after stop

    def _drain_csv_buffer(self) -> None:
        if self._timestamp_csv_writer is None:
            return
        buf = self._csv_row_buffer
        try:
            while buf:
                self._timestamp_csv_writer.writerow(buf.popleft())
        except Exception as e:
            self.logger.warning(f"CSV flush error: {e}")

    def _configure_camera(self):
        """Configure the camera with current settings — shared by every camera variant."""
        try:
            self.logger.info("Configure camera called")

            if self.picam2.started:
                self.picam2.stop()

            # Clear stale frame so reconnecting clients wait for fresh data
            # rather than receiving the last frame from the old configuration.
            with self.frame_lock:
                self.latest_frame = None

            self.fps = self.config.get("camera.fps", 25)
            self.width = self.config.get("camera.width", 1280)
            self.height = self.config.get("camera.height", 720)
            self.lores_width = min(self.width, 640)
            self.lores_height = min(self.height, int(640 * self.height / self.width))
            # Only throttle the preview stream for high-fps cameras.  When camera
            # fps is close to _STREAM_FPS the fixed interval skips nearly every other
            # frame (e.g. 25 fps camera with 41.7 ms interval → ~12.5 fps stream).
            self._stream_interval_s = 0.0 if self.fps <= 35 else 1.0 / self._STREAM_FPS

            # Pick sensor mode from config (clamped to valid range)
            mode_index = self.config.get("camera.sensor_mode_index", 0)
            mode_index = max(0, min(int(mode_index), len(self.sensor_modes) - 1))
            self.mode = self.sensor_modes[mode_index]

            # Clamp fps to the selected mode's maximum
            max_fps = float(self.mode.get("fps", float("inf")))
            if self.fps > max_fps:
                self.logger.warning(
                    f"Requested fps {self.fps} exceeds sensor mode {mode_index} "
                    f"max {max_fps:.1f}fps — clamping."
                )
                self.fps = max_fps

            # Clamp output size to the selected mode's maximum output dimensions
            max_w, max_h = self.mode["size"]
            if self.width > max_w or self.height > max_h:
                self.logger.warning(
                    f"Requested output {self.width}×{self.height} exceeds sensor mode {mode_index} "
                    f"max {max_w}×{max_h} — clamping."
                )
                self.width = min(self.width, max_w)
                self.height = min(self.height, max_h)
                self.lores_width = int(self.width / 2)
                self.lores_height = int(self.height / 2)

            sensor = {"output_size": self.mode["size"], "bit_depth": self.mode["bit_depth"]}
            main = {"size": (self.width, self.height), "format": "RGB888"}
            lores = {"size": (self.lores_width, self.lores_height), "format": "RGB888"}
            if self.config.get("camera.manual_exposure", False):
                exposure_time = self.config.get("camera.exposure_time", 10000)
            else:
                exposure_time = int(1_000_000 / self.fps)

            ae_enabled = bool(self.config.get("camera.ae_enable", False))

            controls = {
                "FrameRate": self.fps,
                "Brightness": self.config.get("camera.brightness"),
                "AeEnable": ae_enabled,
            }
            if not ae_enabled:
                controls["AnalogueGain"] = self.config.get("camera.gain")
                controls["ExposureTime"] = exposure_time

            if self.has_autofocus:
                _AF_MODE_MAP = {"manual": 0, "auto": 1, "continuous": 2}
                af_mode_str = self.config.get("camera.autofocus_mode", "manual")
                af_mode = _AF_MODE_MAP.get(af_mode_str, 0)
                controls["AfMode"] = af_mode
                if af_mode == 0:  # Manual — set fixed lens position
                    controls["LensPosition"] = float(self.config.get("camera.lens_position", 0.0))

            sync_mode_str = self.config.get("camera.sync_mode", "none")
            from libcamera import controls as lc
            if sync_mode_str in ("server", "client"):
                controls["SyncMode"] = (
                    lc.rpi.SyncModeEnum.Server
                    if sync_mode_str == "server"
                    else lc.rpi.SyncModeEnum.Client
                )
                self.logger.info(f"Camera sync mode: {sync_mode_str}")
                if self.config.get("camera.sync_lock_exposure", False):
                    controls["AeEnable"] = False
                    controls["AnalogueGain"] = self.config.get("camera.gain")
                    controls["ExposureTime"] = exposure_time
                    self.logger.info("AEC disabled (sync_lock_exposure)")
                if self.config.get("camera.sync_lock_awb", False):
                    controls["AwbEnable"] = False
                    self.logger.info("AWB disabled (sync_lock_awb)")
            else:
                controls["SyncMode"] = lc.rpi.SyncModeEnum.Off

            if self.config.get("camera.monochrome") is True:
                self.logger.info("Camera configured for grayscale - applying grayscale conversion in pre-callback.")

            # Apply hflip/vflip via hardware Transform so the ISP handles it at
            # zero Python CPU cost — no cv2.flip() on every frame.
            from libcamera import Transform
            hflip = self.config.get("camera.hflip", False) is True
            vflip = self.config.get("camera.vflip", False) is True
            rotation = int(self.config.get("camera.rotation", 0))
            if rotation not in (0, 90, 180, 270):
                rotation = 0
            self._rotation = rotation
            self._rotation_logged = False  # reset so post_callback logs the first rotated frame
            # hflip/vflip are supported by the ISP; rotation is done in the
            # frame callback because the ISP drops the transpose component of
            # rot90/rot270, producing a plain flip instead of a true rotation.
            transform = Transform(hflip=hflip, vflip=vflip)
            if hflip or vflip:
                self.logger.info(f"Hardware transform: hflip={hflip} vflip={vflip}")
            if rotation:
                self.logger.info(f"Software rotation: {rotation}°")

            self.logger.info(f"Sensor stream set to size {self.width},{self.height} and bit depth {self.mode['bit_depth']} to target {self.fps}fps.")

            config = self.picam2.create_video_configuration(
                main=main,
                lores=lores,
                sensor=sensor,
                controls=controls,
                transform=transform,
                buffer_count=self._BUFFER_COUNT,
            )
            self.picam2.configure(config)

            self.picam2.pre_callback = self._frame_precallback
            self.picam2.post_callback = self._stream_post_callback

            bitrate = self.config.get("camera.bitrate_mb", self._DEFAULT_BITRATE_MB) * 1_000_000
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate / 10)

            # Invalidate cached timestamp layout on resolution change
            for attr in ("_ts_layout_main", "_ts_layout_lores"):
                if hasattr(self, attr):
                    setattr(self, attr, None)

            self._cache_frame_config()
            self.logger.info(f"Camera configured successfully at {self.fps}fps")
            return True

        except Exception as e:
            self.logger.error(f"Error configuring camera: {e}")
            # Always rebuild encoders even on failure, so a subsequent recording
            # start doesn't hit a missing main_encoder/lores_encoder attribute.
            bitrate = self.config.get("camera.bitrate_mb", self._DEFAULT_BITRATE_MB) * 1_000_000
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate / 10)
            return False


    """Segment Oriented Recording (to manage long recordings)"""
    def _start_next_recording_segment(self) -> bool:
        self._start_new_video_segment()
        return True


    def _open_timestamp_csv(self, video_filename: str) -> None:
        """Open a per-frame timestamp CSV sidecar alongside video_filename."""
        stem = os.path.splitext(video_filename)[0]
        self._current_csv_path = f"{stem}_timestamps.csv"
        self._timestamp_csv_file = open(self._current_csv_path, "w", newline="",
                                        buffering=1 << 20)  # 1 MiB write buffer
        self._timestamp_csv_writer = csv.writer(self._timestamp_csv_file)
        self._timestamp_csv_writer.writerow(self.BASE_CSV_COLUMNS + self.CSV_EXTRA_COLUMNS)
        self._frame_id = 0
        self._csv_prev_ns = None
        self._csv_row_buffer.clear()
        self._csv_flush_stop.clear()
        self._csv_flush_thread = threading.Thread(
            target=self._csv_flush_worker, daemon=True, name="csv-flush"
        )
        self._csv_flush_thread.start()
        self.facade.add_session_file(self._current_csv_path)

    def _close_timestamp_csv(self) -> None:
        """Flush, close, and stage the current timestamp CSV for export."""
        if self._timestamp_csv_file is not None:
            self._csv_flush_stop.set()
            if self._csv_flush_thread is not None:
                self._csv_flush_thread.join(timeout=5)
                self._csv_flush_thread = None
            self._drain_csv_buffer()
            self._timestamp_csv_file.flush()
            os.fsync(self._timestamp_csv_file.fileno())
            self._timestamp_csv_file.close()
            self._timestamp_csv_file = None
            self._timestamp_csv_writer = None
            if self._current_csv_path:
                self.facade.stage_file_for_export(self._current_csv_path)
                self._current_csv_path = None

    def _pre_create_first_segment(self, start_at: float) -> None:
        """Pre-create the video file and CSV before sleeping so that only
        start_encoder() needs to run at the scheduled start moment.

        Called by Recording._scheduled_start before the spin-wait.
        Any exception is caught by the caller and falls back to normal start.
        """
        filename = self._get_video_filename()
        self.logger.info(f"Pre-staging recording segment: {filename}")

        # Open the mpegts container — this is the slow step (~5–20 ms on Pi).
        file_output = SplittableOutput(PyavOutput(filename, format="mpegts"))

        self._prestaged_segment = {
            "filename":    filename,
            "file_output": file_output,
        }

        self.logger.info("Pre-staging complete")

    def _start_new_recording(self) -> bool:
        """Start a new recording session - set up SplittableOutput"""
        if self._prestaged_segment is not None:
            # Fast path: file and CSV were pre-created before the spin-wait.
            prestaged = self._prestaged_segment
            self._prestaged_segment = None
            filename = prestaged["filename"]
            self.logger.info(f"Using pre-staged segment: {filename}")
            self.current_video_segment = filename
            self.facade.add_session_file(filename)
            self.file_output = prestaged["file_output"]
            self.main_encoder.output = self.file_output
            self._open_timestamp_csv(filename)
        else:
            # Normal path (immediate start or pre-stage failed)
            filename = self._get_video_filename()
            self.logger.info(f"Starting recording with filename {filename}")
            self.current_video_segment = filename
            self.facade.add_session_file(filename)

            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)

            self.file_output = SplittableOutput(PyavOutput(filename, format="mpegts"))
            self.main_encoder.output = self.file_output
            self._open_timestamp_csv(filename)

        # Start recording — this is the precise moment we want to align across cameras.
        # Cameras in sync mode have been running since module startup; we join the
        # existing phase state rather than resetting it with SyncFrames/sync_enable,
        # which would discard any accumulated phase convergence.
        self.picam2.start_encoder(self.main_encoder, name="main")
        self.recording_start_time = time.time()
        return True


    def _get_video_filename(self) -> str:
        """Shorthand way to create a filename"""
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        ext = self.config.get('recording.recording_filetype', 'ts')
        return f"{self.facade.get_filename_prefix()}_({self.facade.get_segment_id()}_{strtime}).{ext}"


    def _start_new_video_segment(self):
        """Start recording a new splittable output video segment."""
        self._close_timestamp_csv()

        self.last_video_segment = self.current_video_segment
        self.facade.stage_file_for_export(self.last_video_segment)

        filename = self._get_video_filename()
        self.current_video_segment = filename
        self.facade.add_session_file(filename)
        self._open_timestamp_csv(filename)

        self.file_output.split_output(PyavOutput(filename, format="mpegts"))
        self.logger.info(f"Switched to new segment {filename}")
        if not self._check_file_exists(filename):
            self.logger.warning(f"{filename} does not exist in recording folder!")


    def _fix_positioning_timestamps(self, filename: str) -> None:
        """Take an mp4/ts file produced by picamera2 SplittableOutput and reset positioning timestamps"""
        tmp_filename = f"{filename[:-3]}_formatted.ts"
        try:
            subprocess.run([
                "ffmpeg", "-i", filename, "-map", "0", "-c", "copy",
                "-reset_timestamps", "1", tmp_filename,
            ], check=True)
            os.replace(tmp_filename, filename)
        except Exception as e:
            self.logger.error(f"ffmpeg timestamp fix failed for {filename}: {e}")


    """Recording"""
    def _stop_recording_video(self):
        """Stop recording current segment"""
        self.picam2.stop_encoder(self.main_encoder)
        self.last_video_segment = self.current_video_segment


    def _stop_recording(self) -> bool:
        """Shared implementation of Module's abstract stop-recording hook."""
        try:
            self.logger.info("Attempting to stop camera recording")

            self._stop_recording_video()
            self._close_timestamp_csv()

            for file in self.session_files:
                if file.endswith(".ts"):
                    self.logger.info(f"Fixing positioning timestamps for {file}")
                    self._fix_positioning_timestamps(file)

            self.facade.stage_file_for_export(self.current_video_segment)
            return True

        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False


    """Timestamping frames"""
    # Cached wall-clock minus monotonic offset in nanoseconds.
    # Recomputed at most once per second; drift between recomputations is <1 µs.
    _wall_mono_offset_ns: int = 0
    _wall_mono_offset_updated_s: float = 0.0

    def _get_wall_mono_offset_ns(self) -> int:
        now = time.monotonic()
        if now - self._wall_mono_offset_updated_s >= 0.01:
            self._wall_mono_offset_ns = int((time.time() - now) * 1e9)
            self._wall_mono_offset_updated_s = now
        return self._wall_mono_offset_ns

    def _get_frame_timestamp(self, metadata: dict) -> Optional[int]:
        """Return the frame exposure time as wall-clock nanoseconds.

        Prefers SensorTimestamp (hardware-stamped at actual sensor exposure,
        CLOCK_MONOTONIC) converted to CLOCK_REALTIME.  Falls back to
        FrameWallClock if SensorTimestamp is unavailable.
        """
        try:
            sensor_ts = metadata.get('SensorTimestamp')
            if sensor_ts is not None:
                return sensor_ts + self._get_wall_mono_offset_ns()
            frame_wall_clock = metadata.get('FrameWallClock')
            if frame_wall_clock is not None:
                return frame_wall_clock
            return None
        except Exception as e:
            self.logger.error(f"Error capturing frame metadata: {e}")
            return None


    def _process_main_frame(self, m: MappedArray, timing: FrameTiming) -> dict:
        """Hook: subclass-specific per-frame processing/overlays on the main stream,
        run after shared rotation/monochrome and before the timestamp overlay.

        Return a dict of extra CSV column values keyed by name (matching
        CSV_EXTRA_COLUMNS) — the shared CSV writer appends them to the base row.
        Default: no-op, no extra columns.
        """
        return {}

    def _process_lores_frame(self, m: MappedArray, timing: FrameTiming) -> None:
        """Hook: subclass-specific overlays on the (never-rotated-here) lores
        stream, run only while actively streaming and within the throttle
        interval. Default: no-op."""
        pass

    def _after_frame_hook(self, timing: FrameTiming) -> None:
        """Hook: runs once per frame after all main/lores processing and the
        CSV write. For anything that needs to happen every frame but isn't
        frame-buffer work (e.g. polling a subprocess). Default: no-op."""
        pass


    def _frame_precallback(self, req) -> None:
        try:
            # Single metadata fetch — reused for timestamp, CSV fields, and overlays.
            meta = req.get_metadata()

            timestamp = self._get_frame_timestamp(meta)
            if timestamp is None:
                self.logger.warning("No frame timestamp available from metadata")
                return

            actual_fps = None
            if self.last_frame_timestamp:
                actual_fps = round((1 / (timestamp - self.last_frame_timestamp)) * 1e9, 3)
            self.last_frame_timestamp = timestamp

            dt = datetime.datetime.fromtimestamp(timestamp / 1e9, tz=datetime.timezone.utc)
            timestamp_utc = dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"

            if self._csv_prev_ns is not None and self.fps:
                delta_ms       = round((timestamp - self._csv_prev_ns) / 1e6, 3)
                expected_ms    = 1000.0 / self.fps
                dropped_before = max(0, round(delta_ms / expected_ms) - 1)
            else:
                delta_ms = dropped_before = ""
            self._csv_prev_ns = timestamp

            # Use cached config values — read once per config change, not per frame
            monochrome        = self._cb_monochrome
            overlay_timestamp = self._cb_overlay_timestamp
            rotation          = self._cb_rotation
            module_name       = self._cb_module_name or self.facade.get_module_name()

            ts_label = (f"{module_name} "
                        f"{dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}+00:00")

            timing = FrameTiming(
                timestamp_ns=timestamp, timestamp_utc=timestamp_utc, ts_label=ts_label,
                actual_fps=actual_fps, delta_ms=delta_ms, dropped_before=dropped_before,
            )

            extra = {}
            with MappedArray(req, 'main') as m:
                if rotation:
                    _rot_k = rotation // 90
                    if m.array.shape[0] == m.array.shape[1] or rotation == 180:
                        m.array[:] = np.rot90(m.array, _rot_k)
                if self._cb_flip_code is not None:
                    m.array[:] = cv2.flip(m.array, self._cb_flip_code)
                if monochrome:
                    self._apply_grayscale(m)
                extra = self._process_main_frame(m, timing)
                if overlay_timestamp:
                    self._apply_timestamp(m.array, ts_label, "main")

            # Buffer CSV row for off-thread write — no file I/O on the capture thread.
            if self._timestamp_csv_writer is not None:
                wall_mono_offset = time.time() - time.monotonic()
                sync_lag_us      = meta.get("SyncTimer", "")
                exposure_time_us = meta.get("ExposureTime", "")
                analogue_gain    = meta.get("AnalogueGain", "")
                colour_gains     = meta.get("ColourGains") or ("", "")
                row = [
                    self._frame_id, timestamp, timestamp_utc,
                    round(wall_mono_offset, 9),
                    delta_ms, dropped_before, sync_lag_us,
                    exposure_time_us, analogue_gain,
                    colour_gains[0], colour_gains[1],
                ]
                row.extend(extra.get(col, "") for col in self.CSV_EXTRA_COLUMNS)
                self._csv_row_buffer.append(row)
                self._frame_id += 1

            # Lores stream — only process frames that will actually be JPEG-encoded.
            # _stream_post_callback throttles encoding to _STREAM_FPS. Mirroring the
            # same time check here means cv2 work only happens on frames the
            # post_callback will actually encode. Both callbacks run on the same
            # capture thread so sharing _last_stream_encode_time is safe.
            #
            # Timestamp/framerate text is NOT stamped here: rotation for this stream
            # happens later in _stream_post_callback (out-of-place, on a make_array
            # copy — see comment there for why). Stamping before that rotation would
            # bake the text in at the wrong orientation/edge once the frame is
            # rotated, so the strings are cached and stamped after rotation instead.
            if self.is_streaming:
                now = time.monotonic()
                if now - self._last_stream_encode_time >= self._stream_interval_s:
                    with MappedArray(req, "lores") as m:
                        if self._cb_flip_code is not None:
                            m.array[:] = cv2.flip(m.array, self._cb_flip_code)
                        if monochrome:
                            self._apply_grayscale(m)
                        self._process_lores_frame(m, timing)
                    self._preview_ts_str = ts_label if overlay_timestamp else None
                    self._preview_actual_fps = actual_fps

            self._after_frame_hook(timing)

        except Exception as e:
            self.logger.error(f"Error capturing frame metadata: {e}")


    def _apply_grayscale(self, m: MappedArray) -> None:
        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
        m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


    # Target fraction of image width the timestamp string should occupy per size preset.
    _TIMESTAMP_WIDTH_FRACTIONS = {"small": 0.50, "medium": 0.72, "large": 0.92}

    def _apply_framerate(self, arr, framerate: str, stream: str = "main") -> None:
        """Apply the framerate to the image. Size is fixed and independent of text_size config.

        Uses the array's actual (post-rotation) shape rather than the
        configured stream dimensions, so placement stays correct at 90/270°.
        """
        framerate = f"{framerate}fps"
        height, width = arr.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        thickness = 1
        font_scale = max(0.2, height * 0.02 / 18)

        text_width, text_height = cv2.getTextSize(framerate, font, font_scale, thickness)[0]

        x = int((width - text_width) / 2)
        y = height - max(4, int(height * 0.01))

        cv2.putText(
            img=arr, text=framerate, org=(x, y), fontFace=font,
            fontScale=font_scale, color=(50, 255, 50), thickness=thickness,
        )

    def _apply_timestamp(self, arr, timestamp: str, stream: str = "main") -> None:
        """Apply the frame timestamp to the image.

        Layout is cached per (stream, size_preset) and recomputed whenever the
        text_size config changes or the actual frame dimensions differ from the cache.
        `arr` must already be in its final (post-rotation) orientation.
        """
        size_preset = self.config.get("camera.text_size", "medium")
        cache_attr = f"_ts_layout_{stream}"
        cached = getattr(self, cache_attr, None)

        actual_height, actual_width = arr.shape[:2]
        text_len = len(timestamp)

        if (cached is None or cached[0] != size_preset or cached[1] != actual_height
                or cached[2] != actual_width or cached[3] != text_len):
            font = cv2.FONT_HERSHEY_SIMPLEX
            target_fraction = self._TIMESTAMP_WIDTH_FRACTIONS.get(size_preset, 0.72)
            thickness = 2 if size_preset == "large" else 1
            ref_width, _ = cv2.getTextSize(timestamp, font, 1.0, thickness)[0]
            font_scale = max(0.3, (target_fraction * actual_width) / ref_width)
            text_width, text_height = cv2.getTextSize(timestamp, font, font_scale, thickness)[0]
            x = int((actual_width - text_width) / 2)
            padding = max(4, int(actual_height * 0.01))
            y = text_height + padding
            cached = (size_preset, actual_height, actual_width, text_len, font_scale, thickness, x, y)
            setattr(self, cache_attr, cached)

        _, _, _, _, font_scale, thickness, x, y = cached
        cv2.putText(
            img=arr, text=timestamp, org=(x, y), fontFace=cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=font_scale, color=(50, 255, 50), thickness=thickness,
        )


    """Video streaming"""
    @command()
    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        """Start streaming video to the specified receiver using Flask to send MJPEG"""
        try:
            if self.is_streaming:
                self.logger.warning("Already streaming")
                return False

            port = 8080
            self.logger.info(f"Starting streaming from {self.network.ip}:{port}")

            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)

            self.should_stop_streaming = False

            self.streaming_server_thread = threading.Thread(target=self.run_streaming_server, args=(port,))
            self.streaming_server_thread.daemon = True
            self.streaming_server_thread.start()

            self.is_streaming = True

            self.communication.send_status({
                'type': 'streaming_started',
                'port': port,
                'status': 'success',
                'message': f'Streaming started from {self.network.ip}:{port}'
            })
            return True

        except Exception as e:
            self.logger.error(f"Error starting streaming: {str(e)}")
            self.communication.send_status({
                'type': 'streaming_start_failed',
                'status': 'error',
                'error': f"Failed to start streaming: {str(e)}"
            })
            return False


    def _stream_post_callback(self, request):
        """Capture and JPEG-encode one lores frame, throttled for high-fps cameras.

        The post-callback fires on every camera frame regardless of recording fps.
        For cameras running above 35 fps, frames are throttled to _STREAM_FPS to
        avoid saturating the CPU with JPEG encodes. For cameras at or below 35 fps
        every frame is passed through so the interval never accidentally skips
        frames (e.g. a 25 fps camera with a 24 fps throttle loses every other frame).
        """
        if not self.is_streaming:
            return
        try:
            now = time.monotonic()
            if now - self._last_stream_encode_time < self._stream_interval_s:
                return
            self._last_stream_encode_time = now

            high_quality = self.config.get("camera.livestream_quality", "normal") == "high"
            stream_name = "main" if high_quality else "lores"
            jpeg_quality = 90 if high_quality else 80
            frame = request.make_array(stream_name)
            rotation = getattr(self, "_rotation", 0)
            if rotation:
                k = rotation // 90
                # rot90 returns a non-contiguous view; putText below needs a
                # contiguous buffer, so make the copy once here.
                frame = np.ascontiguousarray(np.rot90(frame, k))
                if not getattr(self, "_rotation_logged", False):
                    self.logger.info(
                        f"Preview rotation: {rotation}° applied — "
                        f"output {frame.shape[1]}×{frame.shape[0]}"
                    )
                    self._rotation_logged = True
            else:
                self._rotation_logged = False

            # Timestamp/framerate for the lores stream are stamped here, after
            # rotation, so they land on the correctly-oriented final frame (see
            # comment in _frame_precallback). The "main"/high-quality path is
            # already stamped pre-rotation upstream, so skip it here.
            if stream_name == "lores":
                ts_str = getattr(self, "_preview_ts_str", None)
                if ts_str:
                    self._apply_timestamp(frame, ts_str, "lores")
                if self.config.get("camera.overlay_framerate_on_preview", False):
                    actual_fps = getattr(self, "_preview_actual_fps", None)
                    if actual_fps:
                        self._apply_framerate(frame, str(actual_fps), "lores")

            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not ret:
                return
            with self.frame_lock:
                self.latest_frame = jpeg.tobytes()

        except Exception as e:
            self.logger.error(f"Capture error: {e}")


    def generate_streaming_frames(self):
        """Generate streaming frames for MJPEG stream.

        Yields each encoded frame exactly once by comparing object identity
        against the last yielded frame. Rate is naturally limited by
        _stream_post_callback which encodes at _STREAM_FPS.
        """
        last_frame = None
        while not self.should_stop_streaming:
            with self.frame_lock:
                frame = self.latest_frame

            if frame is None or frame is last_frame:
                time.sleep(0.005)
                continue

            last_frame = frame
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" +
                frame +
                b"\r\n"
            )

    def run_streaming_server(self, port=8080):
        """Run the flask server to stream upon"""
        try:
            from werkzeug.serving import make_server
            self.streaming_server = make_server('0.0.0.0', port, self.streaming_app, threaded=True)
            self.logger.info(f"Starting Flask server on port {port}")
            self.streaming_server.serve_forever()
        except Exception as e:
            self.logger.error(f"Error running streaming server: {e}")
            self.is_streaming = False
            self.streaming_server = None

    def register_routes(self):
        """Register Flask routes"""
        @self.streaming_app.route('/')
        def index():
            return "Camera Streaming Server"

        @self.streaming_app.route('/video_feed')
        def video_feed():
            return Response(
                self.generate_streaming_frames(),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )

        @self.streaming_app.route('/shutdown')
        def shutdown():
            func = request.environ.get('werkzeug.server.shutdown')
            if func is None:
                raise RuntimeError('Not running with the Werkzeug Server')
            func()
            return 'Server shutting down...'

    @command()
    def stop_streaming(self) -> bool:
        """Stop streaming video"""
        try:
            if not self.is_streaming:
                self.logger.warning("Not currently streaming")
                return False

            self.should_stop_streaming = True

            if self.streaming_server:
                self.streaming_server.shutdown()
                self.streaming_server = None

            if self.streaming_server_thread and self.streaming_server_thread.is_alive():
                self.streaming_server_thread.join(timeout=1.0)

            try:
                os.system("pkill -f 'python.*flask'")
            except Exception:
                pass

            self.is_streaming = False

            self.communication.send_status({
                "type": "streaming_stopped",
                "status": "success",
                "message": "Streaming stopped successfully"
            })
            return True

        except Exception as e:
            self.logger.error(f"Error stopping stream: {e}")
            self.communication.send_status({
                "type": "streaming_stopped",
                "status": "error",
                "error": f"Failed to stop streaming: {str(e)}"
            })
            return False


    def start(self) -> bool:
        """Start the camera module - including streaming"""
        try:
            if not super().start():
                return False
            # TODO: add check for config parameter stream_on_start?
            self.start_streaming()
            return True
        except Exception as e:
            self.logger.error(f"Error starting module: {e}")
            return False

    def stop(self) -> bool:
        """Stop the module and cleanup"""
        try:
            if self.is_streaming:
                self.stop_streaming()
            return super().stop()
        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False
