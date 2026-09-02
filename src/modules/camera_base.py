#!/usr/bin/env python3
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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from flask import request
from picamera2 import MappedArray, Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput, SplittableOutput

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.mjpeg_stream import MJPEGStreamServer
from modules.module import Module, check, command
from src.shared.ratelimit_log import RateLimitedLogger


@dataclass
class FrameTiming:
    """Per-frame timing/CSV values computed once and shared with subclass hooks."""
    timestamp_ns: int
    timestamp_utc: str
    ts_label: str          # "{module_name} {utc string}" — the on-frame overlay string
    actual_fps: float | None
    delta_ms: Any           # float or "" — same convention as the base CSV columns
    dropped_before: Any      # int or ""
    exposure_time_us: Any    # int or "" — from Picamera2 metadata, exposed so a
    analogue_gain: Any        # float or "" — subclass hook can detect AE hunting


class _FrameShim:
    """Adapts a plain ndarray to the MappedArray.array interface used by
    _process_lores_frame hooks, without any DMA buffer involvement."""
    __slots__ = ("array",)
    def __init__(self, arr: np.ndarray) -> None:
        self.array = arr


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
        "camera.gain", "camera.brightness", "camera.contrast", "camera.exposure_time",
        "camera.manual_exposure", "camera.ae_enable",
        "camera.lens_position", "camera.autofocus_mode",
        "camera.crop_rect", "camera.sharpness", "camera.noise_reduction_mode",
        "camera.exposure_value", "camera.ae_metering_mode",
        "camera.ae_constraint_mode", "camera.ae_exposure_mode",
    }
    # libcamera's NoiseReductionMode enum (control_ids_draft.yaml) -- only the
    # three modes relevant to a continuous video pipeline are offered;
    # Minimal/ZSL are omitted (ZSL is a stills zero-shutter-lag mode, not
    # applicable here). "fast" matches Picamera2's own implicit default for a
    # video configuration, so a device with no explicit override behaves
    # identically to before this config key existed.
    _NR_MODE_MAP = {"off": 0, "fast": 1, "high_quality": 2}
    # libcamera AGC tuning enums (control_ids.yaml). These bias how auto
    # gain/exposure behaves and only take effect while AeEnable is true; they
    # are harmless no-ops otherwise, so they are always included in the control
    # set alongside Contrast/Sharpness. The default value of each maps to
    # libcamera's own default (index 0 for metering/constraint, "normal" for
    # exposure mode), so a device with no explicit override behaves exactly as
    # before these keys existed.
    _AE_METERING_MAP = {"centre_weighted": 0, "spot": 1, "matrix": 2}
    _AE_CONSTRAINT_MAP = {"normal": 0, "highlight": 1, "shadows": 2}
    _AE_EXPOSURE_MAP = {"normal": 0, "short": 1, "long": 2}

    def __init__(self, module_type: str):
        super().__init__(module_type)
        self.config.load_module_config(self.CONFIG_FILENAME)

        # Camera attributes -- set to their "no hardware" values before the
        # probe below so every attribute exists even if it fails.
        self.height = None
        self.width = None
        self.fps = None
        self.mode = None
        self.gain = None
        self.picam2 = None
        self.sensor_modes = []
        self.sensor_model = ""
        self.has_autofocus = False
        # None once a sensor is confirmed present and configured; otherwise a
        # human-readable reason. Surfaced two ways: passively every heartbeat
        # via get_health(), and as the explicit failure reason from the
        # _check_picam() readiness check (validate_readiness(), the New
        # Session drawer's readiness summary). A missing/dead camera must
        # never crash module startup -- Picamera2() raises when no sensor is
        # detected, which used to take the whole process down before it ever
        # registered with the controller (silently indistinguishable from a
        # powered-off device). The module now always starts and registers;
        # only recording/streaming are unavailable until a sensor is
        # connected and the module restarted.
        self.hardware_fault: str | None = None

        try:
            self.picam2 = Picamera2()
            self.sensor_modes = self.picam2.sensor_modes
            self.sensor_model = self.picam2.camera_properties.get("Model", "").lower()
            self.has_autofocus = "imx708" in self.sensor_model
            self.logger.info(
                f"Sensor model: {self.sensor_model!r}, "
                f"autofocus: {self.has_autofocus}"
            )
            time.sleep(0.1)
        except Exception as e:
            self.picam2 = None
            self.hardware_fault = f"No camera sensor detected: {e}"
            self.logger.error(
                f"{self.hardware_fault} -- module will still start and register "
                "with the controller, but recording/streaming are unavailable "
                "until a sensor is connected and the module is restarted."
            )

        # Streaming variables
        self.monitor_stream = MJPEGStreamServer(logger=self.logger, name="Camera")
        self.register_routes()

        self.last_frame_timestamp = None
        self._last_stream_encode_time = 0.0
        self._stream_interval_s = 0.0
        # Wall-clock time _frame_precallback last ran at all -- distinct from
        # last_frame_timestamp (the frame's own hardware timestamp, used for
        # delta_ms/fps math). Used by _check_recording_alive() to detect the
        # capture pipeline going silent; updated unconditionally regardless
        # of streaming/recording state, since _frame_precallback itself is
        # never overridden by subclasses (unlike the smaller per-frame
        # hooks), so this is a reliable proxy for "is libcamera still
        # delivering frames at all" for every camera variant.
        self._last_frame_wall_time = None

        # Configure camera -- skipped entirely if no sensor was found above.
        # A configure-time failure (sensor present but unhappy, e.g. a bad
        # ribbon cable) is folded into the same hardware_fault/picam2=None
        # state as "no sensor at all" -- both mean recording/streaming are
        # unavailable, and distinguishing them further isn't worth the
        # complexity here.
        if self.picam2 is not None:
            time.sleep(0.1)
            try:
                self._configure_camera()
            except Exception as e:
                self.hardware_fault = f"Camera found but failed to configure: {e}"
                self.logger.error(self.hardware_fault)
                self.picam2 = None
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
        self._segment_dropped = 0  # frames estimated dropped in the current segment

        # Periodic "still capturing" throughput line while recording — makes a
        # silently-wedged pipeline visible after the fact in the journal
        # (recording.camera_throughput_log_secs, 0 disables).
        self._throughput_last_log_s = 0.0
        self._throughput_last_frame_id = 0

        # Coalesces per-frame failure spam (a camera throwing on every frame,
        # missing timestamp metadata) into one line + periodic count + a
        # recovery notice — see RateLimitedLogger.
        self._rl_log = RateLimitedLogger(self.logger, interval_s=30.0)

        self._preview_timing: FrameTiming | None = None

        # --- Exposure clipping indicator (data-quality warning, coarse) ---
        # A blown-out (or crushed-black) frame has no detail and also breaks
        # motion/AE logic downstream -- and it's easy to miss on a small
        # preview. Sampled from the capture thread on a throttled interval
        # (runs whenever the camera is capturing, not just while streaming);
        # overlaid on the preview and reported in the health snapshot.
        # Advisory only, never gates.
        self._exposure_sample_history: collections.deque = collections.deque(maxlen=3)
        self._exposure_over_pct = 0.0    # % of sampled pixels at/near white
        self._exposure_under_pct = 0.0   # % at/near black
        self._exposure_last_sample_s = 0.0
        # so the WARNING log fires on the transition, not every frame
        self._exposure_warned = False


    """Self Check"""
    @check()
    def _check_picam(self) -> tuple:
        if not self.picam2:
            return False, self.hardware_fault or "No camera hardware detected"
        return True, f"{self.sensor_model or 'camera'} present"


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
    def set_camera_crop(self, crop_rect: dict | None) -> dict:
        """Save a crop/digital-zoom rectangle from the web UI's crop editor
        and apply it live via ScalerCrop. Pass None to clear an existing crop.

        Expected shape when setting:
            {"x": int, "y": int, "width": int, "height": int,
             "preview_width": int, "preview_height": int}
        x/y/width/height are in the *displayed preview's* pixel space -- the
        same space the crop editor's snapshot is shown in (i.e.
        camera.width x camera.height at the moment the rect was drawn).
        preview_width/preview_height record what camera.width/camera.height
        were at that moment, so _compute_scaler_crop_rect() can convert
        correctly even if the live camera.width/height have since changed,
        and so the frontend can detect staleness (a crop drawn against a
        since-changed sensor mode/output size) by comparing them to the
        current values -- see CLAUDE.md's crop feature design note for why
        that's a UI warning rather than something enforced here.
        """
        self.config.set("camera.crop_rect", crop_rect)
        self.communication.send_status({
            "type": "camera_crop_updated",
            "crop_rect": crop_rect,
        })
        return {"result": "success"}


    def _compute_scaler_crop_rect(self) -> tuple[int, int, int, int] | None:
        """Convert the stored preview-pixel-space camera.crop_rect into a
        sensor-native ScalerCrop rectangle (x, y, w, h), anchored to the
        active sensor mode's crop_limits. Returns None if no crop is set or
        it can't be computed (e.g. sensor modes not yet available).

        Deliberately does not try to detect/reject a stale crop_rect (drawn
        against a since-changed sensor mode or output size) -- per the
        design note in CLAUDE.md, a stale crop is applied as best-effort
        rather than blocked; the operator is expected to notice the UI's
        staleness warning and redraw. The clamp below only guards against
        picam2.set_controls() erroring out on an out-of-range rectangle, not
        against the crop looking wrong.
        """
        crop_rect = self.config.get("camera.crop_rect")
        if not crop_rect:
            return None
        try:
            mode = self.mode
            if not mode and self.sensor_modes:
                mode_index = self.config.get("camera.sensor_mode_index", 0)
                mode = self.sensor_modes[max(0, min(int(mode_index), len(self.sensor_modes) - 1))]
            if not mode:
                return None

            limit_x, limit_y, limit_w, limit_h = mode["crop_limits"]
            preview_w = crop_rect.get("preview_width") or self.width or limit_w
            preview_h = crop_rect.get("preview_height") or self.height or limit_h
            if not preview_w or not preview_h:
                return None

            scale_x = limit_w / preview_w
            scale_y = limit_h / preview_h
            x = limit_x + round(crop_rect["x"] * scale_x)
            y = limit_y + round(crop_rect["y"] * scale_y)
            w = round(crop_rect["width"] * scale_x)
            h = round(crop_rect["height"] * scale_y)

            # Clamp inside the mode's crop_limits so a stale or malformed
            # rect can't request an out-of-range ScalerCrop.
            x = max(limit_x, min(x, limit_x + limit_w - 1))
            y = max(limit_y, min(y, limit_y + limit_h - 1))
            w = max(1, min(w, limit_x + limit_w - x))
            h = max(1, min(h, limit_y + limit_h - y))
            return (x, y, w, h)
        except Exception as e:
            self.logger.warning(f"Could not compute ScalerCrop from stored crop_rect: {e}")
            return None


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


    def _ae_tuning_controls(self) -> dict:
        """AGC bias/metering controls shared by the live and full-configure
        paths. Effective only while AeEnable is true; inert otherwise, so it is
        safe to include unconditionally."""
        return {
            "ExposureValue": float(self.config.get("camera.exposure_value", 0.0)),
            "AeMeteringMode": self._AE_METERING_MAP.get(
                self.config.get("camera.ae_metering_mode", "centre_weighted"), 0
            ),
            "AeConstraintMode": self._AE_CONSTRAINT_MAP.get(
                self.config.get("camera.ae_constraint_mode", "normal"), 0
            ),
            "AeExposureMode": self._AE_EXPOSURE_MAP.get(
                self.config.get("camera.ae_exposure_mode", "normal"), 0
            ),
        }

    def _configure_module_extra(self, updated_keys) -> None:
        """Hook: subclass-specific config handling, called first in
        configure_module_special (before the shared camera restart-vs-live-controls
        branching below). Default: no-op."""
        pass


    def configure_module_special(self, updated_keys: list | None):
        """Shared restart-vs-live-controls config handling for every camera module."""
        self._configure_module_extra(updated_keys)

        if self.picam2 is None:
            # Nothing to (re)configure -- avoid a confusing AttributeError-shaped
            # log line from the picam2 calls below. Config is still saved by the
            # generic config path; it just can't be applied to hardware that
            # isn't there. _check_picam() keeps reporting why on every readiness
            # check until a sensor is connected and the module restarted.
            self.logger.info(
                f"Config changed but no camera hardware present "
                f"({self._hardware_fault_reason()}) -- nothing to apply"
            )
            return

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
                "Contrast": self.config.get("camera.contrast", 1.0),
                "Sharpness": self.config.get("camera.sharpness", 1.0),
                "NoiseReductionMode": self._NR_MODE_MAP.get(
                    self.config.get("camera.noise_reduction_mode", "fast"), 1
                ),
                "FrameRate": fps,
                "AeEnable": ae_enabled,
            }
            live_controls.update(self._ae_tuning_controls())
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

            scaler_crop = self._compute_scaler_crop_rect()
            if scaler_crop is not None:
                live_controls["ScalerCrop"] = scaler_crop

            try:
                self.picam2.set_controls(live_controls)
            except Exception as e:
                self.logger.error(f"Error applying live camera controls {live_controls}: {e}")

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
        self._cb_throughput_log_secs = self.config.get("recording.camera_throughput_log_secs", 60.0)
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
            self.monitor_stream.clear_frame()

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
                "Contrast": self.config.get("camera.contrast", 1.0),
                "Sharpness": self.config.get("camera.sharpness", 1.0),
                "NoiseReductionMode": self._NR_MODE_MAP.get(
                    self.config.get("camera.noise_reduction_mode", "fast"), 1
                ),
                "AeEnable": ae_enabled,
            }
            controls.update(self._ae_tuning_controls())
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

            scaler_crop = self._compute_scaler_crop_rect()
            if scaler_crop is not None:
                controls["ScalerCrop"] = scaler_crop

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
        # Defensive, not just belt-and-braces: recording.py's segment-rotation
        # timer doesn't check whether the initial start actually succeeded
        # (see _start_new_recording's own guard above), so this can still be
        # reached against a hardware-less camera.
        if self.picam2 is None:
            reason = self._hardware_fault_reason()
            self.logger.error(f"Cannot start next segment: {reason}")
            return False
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
        self._segment_dropped = 0
        self._throughput_last_log_s = 0.0
        self._throughput_last_frame_id = 0
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

    def _hardware_fault_reason(self) -> str:
        return self.hardware_fault or "No camera hardware detected"

    def _start_new_recording(self) -> bool:
        """Start a new recording session - set up SplittableOutput"""
        if self.picam2 is None:
            reason = self._hardware_fault_reason()
            self.logger.error(f"Cannot start recording: {reason}")
            self.facade.send_status({"type": "recording_start_failed", "error": reason})
            return False

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
        # Capture the closing segment's stats before _open_timestamp_csv resets
        # the counters — a per-segment forensic record (frames actually
        # written, estimated drops, file size, wall clock) so a mid-session
        # module reboot / stall shows up as a gap in a greppable series
        # instead of having to be reconstructed from segment-id arithmetic.
        closing = self.current_video_segment
        closing_frames = self._frame_id
        closing_dropped = self._segment_dropped
        try:
            closing_bytes = os.path.getsize(closing) if closing else 0
        except OSError:
            closing_bytes = -1

        self._close_timestamp_csv()

        self.last_video_segment = self.current_video_segment
        self.facade.stage_file_for_export(self.last_video_segment)

        filename = self._get_video_filename()
        self.current_video_segment = filename
        self.facade.add_session_file(filename)
        self._open_timestamp_csv(filename)

        self.file_output.split_output(PyavOutput(filename, format="mpegts"))
        self.logger.info(
            f"Segment rotated: closed {os.path.basename(closing) if closing else '(none)'} "
            f"({closing_frames} frames, ~{closing_dropped} dropped, "
            f"{closing_bytes / 1e6:.1f} MB) → opened {os.path.basename(filename)}"
        )
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
            self.logger.exception(f"Error stopping recording: {e}")
            return False


    def _check_recording_alive(self) -> tuple[bool, str | None]:
        """Report if the capture pipeline has gone silent -- no frames
        processed recently despite an active recording. Distinct from the
        per-frame dropped_before count in the CSV sidecar (occasional missed
        frames): this catches the pipeline stalling or the encoder dying
        outright, which dropped_before can't, since it's only ever computed
        from frames that did arrive."""
        if self._last_frame_wall_time is None:
            return True, None
        silence_secs = time.time() - self._last_frame_wall_time
        max_silence_secs = self.config.get("recording._health_check_camera_silence_secs", 5.0)
        if silence_secs > max_silence_secs:
            return False, f"no frames processed in {silence_secs:.1f}s"
        return True, None


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

    def _get_frame_timestamp(self, metadata: dict) -> int | None:
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
            self._rl_log.error("frame_ts_meta", f"Error reading frame timestamp metadata: {e}")
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


    def _apply_exposure_overlay(self, frame) -> None:
        """Draw a top-left 'OVEREXPOSED N%' / 'UNDEREXPOSED N%' warning when
        _maybe_sample_exposure's rolling figure is over camera.exposure_warn_pct.
        No-op otherwise."""
        try:
            warn_pct = float(self.config.get("camera.exposure_warn_pct", 5.0))
            over, under = self._exposure_over_pct, self._exposure_under_pct
            if max(over, under) < warn_pct:
                return
            if over >= under:
                text, col = f"OVEREXPOSED {over:.0f}%", (60, 60, 255)   # BGR red
            else:
                text, col = f"UNDEREXPOSED {under:.0f}%", (0, 200, 255)  # amber
            cv2.putText(frame, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, col, 1, cv2.LINE_AA)
        except Exception:
            pass

    def _maybe_sample_exposure(self, arr) -> None:
        """Once per camera.exposure_check_interval_s, measure what fraction of
        a strided subsample of the frame is clipped to white / crushed to
        black, and roll it into _exposure_over_pct / _exposure_under_pct
        (mean of the last few samples, ~a few seconds). Logs a WARNING on the
        transition into a sustained-bad state, cleared on recovery. Cheap: a
        ~1/64 subsample once a second."""
        now = time.monotonic()
        interval = self.config.get("camera.exposure_check_interval_s", 1.0)
        if not interval or now - self._exposure_last_sample_s < interval:
            return
        self._exposure_last_sample_s = now
        try:
            hi = int(self.config.get("camera.exposure_clip_high", 250))
            lo = int(self.config.get("camera.exposure_clip_low", 5))
            sub = arr[::8, ::8]
            # any channel clipping counts as clipped
            luma = sub.max(axis=2) if sub.ndim == 3 else sub
            n = luma.size or 1
            over = 100.0 * int(np.count_nonzero(luma >= hi)) / n
            under = 100.0 * int(np.count_nonzero(luma <= lo)) / n
            self._exposure_sample_history.append((over, under))
            h = self._exposure_sample_history
            self._exposure_over_pct = sum(o for o, _ in h) / len(h)
            self._exposure_under_pct = sum(u for _, u in h) / len(h)

            warn_pct = float(self.config.get("camera.exposure_warn_pct", 5.0))
            bad = max(self._exposure_over_pct, self._exposure_under_pct) >= warn_pct
            if bad and not self._exposure_warned:
                self.logger.warning(
                    f"Exposure out of range: {self._exposure_over_pct:.1f}% of the "
                    f"frame is clipped white, {self._exposure_under_pct:.1f}% crushed "
                    f"black -- check exposure/gain")
            elif not bad and self._exposure_warned:
                self.logger.info("Exposure back within range")
            self._exposure_warned = bad
        except Exception as e:
            self.logger.debug(f"Exposure sample failed: {e}")

    def _frame_precallback(self, req) -> None:
        try:
            self._last_frame_wall_time = time.time()

            # Single metadata fetch — reused for timestamp, CSV fields, and overlays.
            meta = req.get_metadata()

            timestamp = self._get_frame_timestamp(meta)
            if timestamp is None:
                self._rl_log.warning(
                    "frame_ts_missing", "No frame timestamp available from metadata")
                return
            self._rl_log.ok("frame_ts_missing", "Frame timestamp metadata recovered")

            actual_fps = None
            if self.last_frame_timestamp:
                actual_fps = round((1 / (timestamp - self.last_frame_timestamp)) * 1e9, 1)
            self.last_frame_timestamp = timestamp

            dt = datetime.datetime.fromtimestamp(timestamp / 1e9, tz=datetime.UTC)
            timestamp_utc = dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"

            if self._csv_prev_ns is not None and self.fps:
                delta_ms       = round((timestamp - self._csv_prev_ns) / 1e6, 3)
                expected_ms    = 1000.0 / self.fps
                dropped_before = max(0, round(delta_ms / expected_ms) - 1)
                self._segment_dropped += dropped_before
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

            exposure_time_us = meta.get("ExposureTime", "")
            analogue_gain    = meta.get("AnalogueGain", "")

            timing = FrameTiming(
                timestamp_ns=timestamp, timestamp_utc=timestamp_utc, ts_label=ts_label,
                actual_fps=actual_fps, delta_ms=delta_ms, dropped_before=dropped_before,
                exposure_time_us=exposure_time_us, analogue_gain=analogue_gain,
            )

            extra = {}
            with MappedArray(req, 'main') as m:
                rotated_in_place = False
                if rotation:
                    _rot_k = rotation // 90
                    if m.array.shape[0] == m.array.shape[1] or rotation == 180:
                        m.array[:] = np.rot90(m.array, _rot_k)
                        rotated_in_place = True
                if self._cb_flip_code is not None:
                    m.array[:] = cv2.flip(m.array, self._cb_flip_code)
                if monochrome:
                    self._apply_grayscale(m)
                self._maybe_sample_exposure(m.array)
                extra = self._process_main_frame(m, timing)
                if overlay_timestamp:
                    # 90°/270° rotation on a non-square resolution can't be
                    # applied to the frame content itself in place (see the
                    # comment on the guard above), so the recorded video stays
                    # in its unrotated capture orientation. The timestamp still
                    # needs to read correctly once a viewer rotates the file
                    # for playback, so it's pre-rotated by the same quarter
                    # turns and placed on the edge that becomes "top" — see
                    # _apply_timestamp's compensate_k handling.
                    skipped_rotation = rotation and not rotated_in_place
                    compensate_k = rotation // 90 if skipped_rotation else 0
                    self._apply_timestamp(
                        m.array, ts_label, "main", compensate_k=compensate_k,
                    )

            # Buffer CSV row for off-thread write — no file I/O on the capture thread.
            if self._timestamp_csv_writer is not None:
                wall_mono_offset = time.time() - time.monotonic()
                sync_lag_us      = meta.get("SyncTimer", "")
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
                    self._preview_ts_str = ts_label if overlay_timestamp else None
                    self._preview_actual_fps = actual_fps
                    self._preview_timing = timing

            self._after_frame_hook(timing)
            self._rl_log.ok("frame_precallback", "Frame pre-callback recovered")
            self._maybe_log_throughput()

        except Exception as e:
            # Fires on the capture thread, once per frame (30-120 fps). A
            # persistent fault here would otherwise write an identical ERROR
            # line every frame forever — coalesce it.
            self._rl_log.error("frame_precallback", f"Error in frame pre-callback: {e}")


    def _maybe_log_throughput(self) -> None:
        """Emit one INFO line every camera_throughput_log_secs while recording:
        measured fps, frames + estimated drops this segment. Cheap — a single
        monotonic comparison per frame otherwise."""
        if self._timestamp_csv_writer is None:
            return
        interval = self._cb_throughput_log_secs
        if not interval or interval <= 0:
            return
        now = time.monotonic()
        if self._throughput_last_log_s == 0.0:
            self._throughput_last_log_s = now
            self._throughput_last_frame_id = self._frame_id
            return
        elapsed = now - self._throughput_last_log_s
        if elapsed < interval:
            return
        frames = self._frame_id - self._throughput_last_frame_id
        measured_fps = frames / elapsed if elapsed else 0.0
        self.logger.info(
            f"Recording alive: {measured_fps:.1f} fps over last {elapsed:.0f}s "
            f"(target {self.fps}); segment {self._frame_id} frames, "
            f"~{self._segment_dropped} dropped"
        )
        self._throughput_last_log_s = now
        self._throughput_last_frame_id = self._frame_id

    def _apply_grayscale(self, m: MappedArray) -> None:
        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
        cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR, dst=m.array)  # in-place dst avoids allocation



    # Target fraction of image width the timestamp string should occupy per size preset.
    _TIMESTAMP_WIDTH_FRACTIONS = {"small": 0.50, "medium": 0.72, "large": 0.92}

    def _apply_framerate(self, arr, framerate: str, stream: str = "main") -> None:
        """Apply the framerate to the image, top-right corner (top-center is
        already taken by the timestamp, and the bottom edge tends to sit
        under video-player controls). Size is fixed and independent of
        text_size config.

        Uses the array's actual (post-rotation) shape rather than the
        configured stream dimensions, so placement stays correct at 90/270°.
        """
        framerate = f"{framerate}fps"
        height, width = arr.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        thickness = 1
        # Deliberately smaller and a different colour (cyan, vs. the
        # timestamp's green) so the two overlays read as distinct at a
        # glance rather than a single wall of green text.
        font_scale = max(0.15, height * 0.02 / 18 * 0.75)

        text_width, text_height = cv2.getTextSize(framerate, font, font_scale, thickness)[0]

        padding = max(4, int(height * 0.01))
        x = width - text_width - padding
        y = text_height + padding

        cv2.putText(
            img=arr, text=framerate, org=(x, y), fontFace=font,
            fontScale=font_scale, color=(255, 255, 0), thickness=thickness,  # BGR cyan
        )

    def _apply_timestamp(
        self, arr, timestamp: str, stream: str = "main", compensate_k: int = 0,
    ) -> None:
        """Apply the frame timestamp to the image.

        Layout is cached per (stream, size_preset) and recomputed whenever the
        text_size config changes or the actual frame dimensions differ from the cache.
        `arr` must already be in its final (post-rotation) orientation, UNLESS
        compensate_k is nonzero.

        compensate_k: number of un-applied 90° CCW quarter turns (1 or 3) —
        set when `arr` itself was NOT physically rotated (a 90°/270° rotation
        on a non-square main stream can't be done in place; see
        _frame_precallback). The text is rendered upright on a small canvas,
        pre-rotated by the inverse of that rotation, and pasted onto the edge
        of the unrotated frame that becomes "top" once a viewer later rotates
        the recorded file for playback — so it reads correctly there, even
        though the frame content itself stays unrotated.
        """
        size_preset = self.config.get("camera.text_size", "medium")
        cache_attr = f"_ts_layout_{stream}"
        cached = getattr(self, cache_attr, None)

        actual_height, actual_width = arr.shape[:2]
        # Font scale is computed against the eventual *viewed* width, which is
        # swapped from the raw frame's width when compensating for a skipped
        # 90°/270° rotation.
        view_width = actual_height if compensate_k in (1, 3) else actual_width
        view_height = actual_width if compensate_k in (1, 3) else actual_height
        text_len = len(timestamp)

        cache_key = (size_preset, view_height, view_width, text_len, compensate_k)
        if cached is None or cached[:5] != cache_key:
            font = cv2.FONT_HERSHEY_SIMPLEX
            target_fraction = self._TIMESTAMP_WIDTH_FRACTIONS.get(size_preset, 0.72)
            thickness = 2 if size_preset == "large" else 1
            ref_width, _ = cv2.getTextSize(timestamp, font, 1.0, thickness)[0]
            font_scale = max(0.3, (target_fraction * view_width) / ref_width)
            text_width, text_height = cv2.getTextSize(timestamp, font, font_scale, thickness)[0]
            padding = max(4, int(view_height * 0.01))
            if compensate_k == 0:
                x = int((view_width - text_width) / 2)
                y = text_height + padding
            else:
                x = y = None
            cached = (
                *cache_key, font_scale, thickness, text_width, text_height, padding, x, y,
            )
            setattr(self, cache_attr, cached)

        (*_, font_scale, thickness, text_width, text_height, padding, x, y) = cached

        if compensate_k == 0:
            cv2.putText(
                img=arr, text=timestamp, org=(x, y), fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                fontScale=font_scale, color=(50, 255, 50), thickness=thickness,
            )
            return

        # Render upright on a tight canvas, then rotate the canvas by the
        # inverse of the frame's un-applied rotation so it lands correctly
        # oriented once the frame itself is later rotated for viewing.
        canvas_h = text_height + 2 * padding
        canvas_w = text_width + 2 * padding
        canvas = np.zeros((canvas_h, canvas_w, 3), dtype=arr.dtype)
        cv2.putText(
            img=canvas, text=timestamp, org=(padding, text_height + padding),
            fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=font_scale,
            color=(50, 255, 50), thickness=thickness,
        )
        patch = np.rot90(canvas, (4 - compensate_k) % 4)
        ph, pw = patch.shape[:2]

        if compensate_k == 1:
            # Final top edge == this unrotated frame's right edge.
            px = actual_width - pw - padding
        else:  # compensate_k == 3
            # Final top edge == this unrotated frame's left edge.
            px = padding
        py = (actual_height - ph) // 2

        px = max(0, min(px, actual_width - pw))
        py = max(0, min(py, actual_height - ph))
        region = arr[py:py + ph, px:px + pw]
        np.maximum(region, patch, out=region)


    """Video streaming"""
    @command()
    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        """Start streaming video to the specified receiver using Flask to send MJPEG"""
        try:
            if self.is_streaming:
                self.logger.warning("Already streaming")
                return False

            if self.picam2 is None:
                reason = self._hardware_fault_reason()
                self.logger.error(f"Cannot start streaming: {reason}")
                self.communication.send_status({
                    'type': 'streaming_start_failed',
                    'status': 'error',
                    'error': reason,
                })
                return False

            port = 8080
            self.logger.info(f"Starting streaming from {self.network.ip}:{port}")

            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)

            self.is_streaming = self.monitor_stream.start(port)
            if not self.is_streaming:
                return False

            self.communication.send_status({
                'type': 'streaming_started',
                'port': port,
                'status': 'success',
                'message': f'Streaming started from {self.network.ip}:{port}'
            })
            return True

        except Exception as e:
            self.logger.error(f"Error starting streaming: {e!s}")
            self.communication.send_status({
                'type': 'streaming_start_failed',
                'status': 'error',
                'error': f"Failed to start streaming: {e!s}"
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
                # Monochrome — on the copy, no DMA involved
                if self._cb_monochrome:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR, dst=frame)

                # Subclass overlays (arena polygon, centroid, etc.) via shim
                timing = getattr(self, "_preview_timing", None)
                if timing is not None:
                    shim = _FrameShim(frame)
                    self._process_lores_frame(shim, timing)
                    # shim.array is the same ndarray — cv2 draws in-place, no rebind needed

                # Timestamp — already here, now just comes last
                ts_str = getattr(self, "_preview_ts_str", None)
                if ts_str:
                    self._apply_timestamp(frame, ts_str, "lores")
                if self.config.get("camera.overlay_framerate_on_preview", False):
                    actual_fps = getattr(self, "_preview_actual_fps", None)
                    if actual_fps:
                        self._apply_framerate(frame, str(actual_fps), "lores")

            # Exposure-clipping warning, top-left (timestamp is top-center, fps
            # top-right, subclass overlays bottom-left). Shown on either stream.
            if self.config.get("camera.exposure_overlay", True):
                self._apply_exposure_overlay(frame)

            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not ret:
                return
            self.monitor_stream.push_frame(jpeg.tobytes())

        except Exception as e:
            self.logger.error(f"Capture error: {e}")


    def register_routes(self):
        """Register any extra Flask routes beyond the base '/' and
        '/video_feed', which MJPEGStreamServer already provides. Subclasses
        (e.g. loom's /roi endpoints) should call super().register_routes()
        and add their own routes on self.monitor_stream.app."""
        @self.monitor_stream.app.route('/shutdown')
        def shutdown():
            func = request.environ.get('werkzeug.server.shutdown')
            if func is None:
                raise RuntimeError('Not running with the Werkzeug Server')
            func()
            return 'Server shutting down...'

        @self.monitor_stream.app.route('/snapshot.jpg', methods=['GET'])
        def snapshot():
            """Return a single JPEG snapshot from the latest preview frame --
            same bytes the MJPEG stream is currently pushing, no extra
            encode. Used by the crop editor (every camera variant, not just
            loom) to have a still frame to draw a crop rectangle on."""
            jpeg = self.monitor_stream.get_latest_frame()
            if jpeg is None:
                return ("No frame available", 503)
            return (jpeg, 200, {"Content-Type": "image/jpeg"})

    @command()
    def stop_streaming(self) -> bool:
        """Stop streaming video"""
        try:
            if not self.is_streaming:
                self.logger.warning("Not currently streaming")
                return False

            self.monitor_stream.stop()
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
                "error": f"Failed to stop streaming: {e!s}"
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
