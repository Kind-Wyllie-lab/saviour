#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - APA Camera Module

Top-mounted camera module for the Active Place Avoidance (APA) test rig.
Extends the base Module class with the same recording/streaming infrastructure
as CameraModule, plus APA-specific overlays (circular mask, shock zone) and
Hailo-accelerated rat detection.

Inference runs on a Hailo-8L AI accelerator (Raspberry Pi AI Kit).
No PyTorch / CUDA required.  To use a custom model, export ratnet.pt → ONNX
→ compile to HEF with the Hailo DFC, then set object_detection.model_path.

Author: Andrew SG
"""

import csv
import datetime
import sys
import os
import time
import logging
import numpy as np
import threading
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput, SplittableOutput
from functools import lru_cache
import json
import subprocess
from flask import Flask, Response, request
import cv2
from typing import Optional, List
from collections import deque

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Detection:
    """A single object detection result."""
    def __init__(self, category: int, conf: float, box: tuple):
        self.category = category   # integer class index
        self.conf = conf           # confidence 0–1
        self.box = box             # (x, y, w, h) pixels on the original frame


# ---------------------------------------------------------------------------
# Hailo inference backend
# ---------------------------------------------------------------------------

class HailoDetector:
    """
    Wraps picamera2's Hailo integration for object detection.

    Expected model output (hailo-all yolov8n and compatible models):
        results[class_id] = ndarray (N, 5): [y_min, x_min, y_max, x_max, confidence]
        all values normalised 0–1.
    """

    def __init__(self, hef_path: str, threshold: float = 0.5):
        from picamera2.devices.hailo import Hailo
        self._hailo = Hailo(hef_path)
        self._input_shape = self._hailo.get_input_shape()
        self._threshold = threshold

    @property
    def input_size(self) -> tuple:
        shape = self._input_shape
        if len(shape) == 4:
            return shape[1], shape[2]
        return shape[0], shape[1]

    def detect(self, frame: np.ndarray, labels: List[str]) -> List[Detection]:
        """Run inference on a BGR frame. Returns detections by descending confidence."""
        h, w = self.input_size
        rgb = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2RGB)
        return self._decode(self._hailo.run(rgb), frame.shape, labels)

    def _decode(self, results, orig_shape: tuple, labels: List[str]) -> List[Detection]:
        detections = []
        oh, ow = orig_shape[:2]
        for class_id, class_dets in enumerate(results):
            if class_dets is None or len(class_dets) == 0:
                continue
            for det in class_dets:
                if len(det) < 5:
                    continue
                y1, x1, y2, x2 = float(det[0]), float(det[1]), float(det[2]), float(det[3])
                score = float(det[4])
                if score < self._threshold:
                    continue
                box = (int(x1 * ow), int(y1 * oh),
                       int((x2 - x1) * ow), int((y2 - y1) * oh))
                detections.append(Detection(
                    class_id if class_id < len(labels) else 0, score, box
                ))
        detections.sort(key=lambda d: d.conf, reverse=True)
        return detections

    def close(self):
        self._hailo.close()


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class APACameraModule(Module):
    def __init__(self, module_type="apa_camera"):
        super().__init__(module_type)
        self.config.load_module_config("apa_camera_config.json")

        # ── Camera ──────────────────────────────────────────────────────────
        self.picam2 = Picamera2()
        self.height = None
        self.width = None
        self.lores_width = None
        self.lores_height = None
        self.fps = None
        self.mode = None

        self.sensor_modes = self.picam2.sensor_modes
        self.sensor_model = self.picam2.camera_properties.get("Model", "").lower()
        self.has_autofocus = "imx708" in self.sensor_model
        self.logger.info(f"Sensor: {self.sensor_model!r}, autofocus: {self.has_autofocus}")
        time.sleep(0.1)

        # ── Streaming ───────────────────────────────────────────────────────
        self.streaming_app = Flask(__name__)
        self.streaming_server_thread = None
        self.streaming_server = None
        self.should_stop_streaming = False
        self.latest_frame = None
        self.last_frame_timestamp = None
        self.frame_lock = threading.Lock()
        self._last_stream_encode_time = 0.0
        self.register_routes()

        # ── Configure camera ────────────────────────────────────────────────
        time.sleep(0.1)
        self._configure_camera()
        time.sleep(0.1)

        # ── State ───────────────────────────────────────────────────────────
        self.is_recording = False
        self.is_streaming = False

        # ── Commands ────────────────────────────────────────────────────────
        self.command.set_commands({
            'start_streaming': self.start_streaming,
            'stop_streaming': self.stop_streaming,
            'get_sensor_modes': self.get_sensor_modes,
        })

        # ── Segmented recording ─────────────────────────────────────────────
        self.monitor_recording_segments_stop_flag = threading.Event()
        self.monitor_recording_segments_thread = None
        self.segment_id = 0
        self.segment_start_time = None
        self.segment_files = []
        self.current_video_segment = None
        self.last_video_segment = None

        # ── Per-frame timestamp CSV sidecar ──────────────────────────────────
        self._timestamp_csv_file = None
        self._timestamp_csv_writer = None
        self._current_csv_path = None
        self._frame_id = 0
        self._csv_prev_ns = None

        # ── APA: detection state ────────────────────────────────────────────
        self.detector: Optional[HailoDetector] = None
        self._labels: List[str] = ["rat"]
        self._detection_buffer = deque(maxlen=3)
        self._last_known_det: Optional[Detection] = None

        # ── APA: position tracking ───────────────────────────────────────────
        self.last_cx = None
        self.last_cy = None

        # ── APA: mask / shock zone geometry ─────────────────────────────────
        self.inner_offset = None
        self.outer_radius = None
        self.start_angle = None
        self.end_angle = None

        # ── Health checks ────────────────────────────────────────────────────
        self.module_checks = {self._check_picam}

        # ── Apply APA config ─────────────────────────────────────────────────
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()


    # -----------------------------------------------------------------------
    # Config change handler (abstract in base)
    # -----------------------------------------------------------------------

    def configure_module_special(self, updated_keys: Optional[list]):
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()

        restart_keys = ["camera.fps", "camera.width", "camera.height", "camera.bitrate_mb"]
        needs_restart = any(k in restart_keys for k in (updated_keys or []))

        if self.is_streaming and needs_restart:
            self.stop_streaming()
            time.sleep(1)
            try:
                self._configure_camera()
            except Exception as e:
                self.logger.error(f"Error reconfiguring camera: {e}")
            try:
                self.start_streaming()
            except Exception as e:
                self.logger.error(f"Error restarting stream: {e}")
        elif self.is_streaming:
            fps = self.config.get("camera.fps", 30)
            exposure_time = (
                self.config.get("camera.exposure_time", 10000)
                if self.config.get("camera.manual_exposure", False)
                else int(1_000_000 / fps)
            )
            self.picam2.set_controls({
                "AnalogueGain": self.config.get("camera.gain", 1),
                "ExposureTime": exposure_time,
                "Brightness": self.config.get("camera.brightness", 0),
                "FrameRate": fps,
            })
        else:
            try:
                self._configure_camera()
            except Exception as e:
                self.logger.error(f"Error reconfiguring camera: {e}")


    # -----------------------------------------------------------------------
    # Object detection config
    # -----------------------------------------------------------------------

    def _configure_object_detection(self):
        self._labels = self.config.get("object_detection.labels", ["rat"])
        self.threshold = self.config.get("object_detection.threshold", 0.55)
        self.max_detections = self.config.get("object_detection.max_detections", 2)

        if not self.config.get("object_detection.enabled", False):
            return
        if self.detector is not None:
            return

        model_path = self.config.get(
            "object_detection.model_path",
            "/usr/share/hailo-models/yolov8n.hef"
        )
        try:
            self.detector = HailoDetector(model_path, threshold=self.threshold)
            self.logger.info(f"Hailo detector ready: {model_path}")
        except Exception as e:
            self.logger.error(f"Failed to initialise Hailo detector: {e}")
            self.detector = None


    # -----------------------------------------------------------------------
    # Camera configuration
    # -----------------------------------------------------------------------

    def _configure_camera(self):
        try:
            if self.picam2.started:
                self.picam2.stop()

            # Invalidate cached mask centre — must happen after picam2 stops so
            # no old-resolution frames can race and re-set them before the new
            # resolution is configured.
            self.mask_center_x = None
            self.mask_center_y = None

            self.fps    = self.config.get("camera.fps", 30)
            self.width  = self.config.get("camera.width", 1080)
            self.height = self.config.get("camera.height", 1080)

            # lores is used for the MJPEG preview stream
            if self.width > 2000 or self.height > 2000:
                self.lores_width  = int(self.width / 1.5)
                self.lores_height = int(self.height / 1.5)
            else:
                self.lores_width  = self.width
                self.lores_height = self.height

            # APA uses sensor mode 0 (highest framerate)
            mode_index = max(0, min(
                self.config.get("camera.sensor_mode_index", 0),
                len(self.sensor_modes) - 1
            ))
            self.mode = self.sensor_modes[mode_index]

            max_fps = float(self.mode.get("fps", float("inf")))
            if self.fps > max_fps:
                self.logger.warning(f"fps {self.fps} clamped to mode max {max_fps:.1f}")
                self.fps = max_fps

            max_w, max_h = self.mode["size"]
            if self.width > max_w or self.height > max_h:
                self.width  = min(self.width, max_w)
                self.height = min(self.height, max_h)
                self.lores_width  = int(self.width / 2)
                self.lores_height = int(self.height / 2)

            exposure_time = (
                self.config.get("camera.exposure_time", 10000)
                if self.config.get("camera.manual_exposure", False)
                else int(1_000_000 / self.fps)
            )

            sensor   = {"output_size": self.mode["size"], "bit_depth": self.mode["bit_depth"]}
            main     = {"size": (self.width, self.height), "format": "RGB888"}
            lores    = {"size": (self.lores_width, self.lores_height), "format": "RGB888"}
            controls = {
                "FrameRate": self.fps,
                "AnalogueGain": self.config.get("camera.gain", 1),
                "ExposureTime": exposure_time,
                "Brightness": self.config.get("camera.brightness", 0),
            }

            config = self.picam2.create_video_configuration(
                main=main, lores=lores, sensor=sensor,
                controls=controls, buffer_count=16
            )
            self.picam2.configure(config)
            self.picam2.pre_callback  = self._apa_frame_precallback
            self.picam2.post_callback = self._stream_post_callback

            bitrate = self.config.get("camera.bitrate_mb", 2) * 1_000_000
            self.main_encoder  = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate // 10)

            # Invalidate cached timestamp layout on resolution change
            for attr in ("_ts_layout_main", "_ts_layout_lores"):
                if hasattr(self, attr):
                    delattr(self, attr)

            self.logger.info(f"Camera configured: {self.width}×{self.height} @ {self.fps}fps")
            return True
        except Exception as e:
            self.logger.error(f"Error configuring camera: {e}")
            bitrate = self.config.get("camera.bitrate_mb", 2) * 1_000_000
            self.main_encoder  = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate // 10)
            return False


    # -----------------------------------------------------------------------
    # Sensor modes (used by frontend CameraConfigCard)
    # -----------------------------------------------------------------------

    @command()
    def get_sensor_modes(self):
        if not self.sensor_modes:
            return {"sensor_modes": []}
        max_area = max(m['crop_limits'][2] * m['crop_limits'][3] for m in self.sensor_modes)
        enriched = []
        for i, mode in enumerate(self.sensor_modes):
            crop = mode['crop_limits']
            pct  = round(100 * crop[2] * crop[3] / max_area)
            fov  = "Full FoV" if pct >= 100 else f"Partial FoV ({pct}%)"
            w, h = mode['size']
            fps  = mode['fps']
            enriched.append({
                "index": i, "size": [w, h], "fps": round(fps, 1),
                "bit_depth": mode['bit_depth'], "crop_limits": list(crop),
                "format": str(mode['format']),
                "label": f"Mode {i}: {w}×{h} @ {fps:.0f}fps — {fov}",
            })
        return {
            "sensor_modes": enriched,
            "sensor_model": self.sensor_model,
            "has_autofocus": self.has_autofocus,
        }


    def get_health(self) -> dict:
        health = super().get_health()
        health["wall_mono_offset_s"] = time.time() - time.monotonic()
        return health


    # -----------------------------------------------------------------------
    # Recording — abstract method implementations
    # -----------------------------------------------------------------------

    def _start_recording(self):
        try:
            self._create_initial_recording_segment()
            self._start_recording_segment_monitoring()
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    "filename": self.current_video_segment,
                    "recording": True,
                    "session_id": self.recording_session_id,
                })
            return True
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            return False


    def _start_new_recording(self) -> None:
        filename = self._get_video_filename()
        self.logger.info(f"Starting recording: {filename}")
        self.current_video_segment = filename
        self.facade.add_session_file(filename)

        if not self.picam2.started:
            self.picam2.start()
            time.sleep(0.1)

        self.file_output = SplittableOutput(PyavOutput(filename, format="mpegts"))
        self.main_encoder.output = self.file_output
        self.picam2.start_encoder(self.main_encoder, name="main")
        self.recording_start_time = time.time()
        self._open_timestamp_csv(filename)


    def _start_next_recording_segment(self) -> None:
        self._start_new_video_segment()


    def _start_new_video_segment(self):
        self._close_timestamp_csv()
        self.last_video_segment = self.current_video_segment
        self.facade.stage_file_for_export(self.last_video_segment)

        filename = self._get_video_filename()
        self.current_video_segment = filename
        self.facade.add_session_file(filename)
        self._open_timestamp_csv(filename)
        self.file_output.split_output(PyavOutput(filename, format="mpegts"))
        self.logger.info(f"Switched to new segment: {filename}")


    def _stop_recording(self) -> bool:
        try:
            self.picam2.stop_encoder(self.main_encoder)
            self._close_timestamp_csv()
            for f in self.session_files:
                if f.endswith(".ts"):
                    self._fix_positioning_timestamps(f)
            self.facade.stage_file_for_export(self.current_video_segment)
            return True
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False


    def _start_recording_segment_monitoring(self):
        self.monitor_recording_segments_stop_flag.clear()
        self.segment_start_time = self.recording_start_time
        self.segment_id = 0
        self.monitor_recording_segments_thread = threading.Thread(
            target=self._monitor_recording_length, daemon=True
        )
        self.monitor_recording_segments_thread.start()


    def _get_video_filename(self) -> str:
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        ext = self.config.get('recording.recording_filetype', 'ts')
        return f"{self.facade.get_filename_prefix()}_({self.facade.get_segment_id()}_{strtime}).{ext}"


    # ── Timestamp CSV sidecar ────────────────────────────────────────────────

    def _open_timestamp_csv(self, video_filename: str) -> None:
        stem = os.path.splitext(video_filename)[0]
        self._current_csv_path = f"{stem}_timestamps.csv"
        self._timestamp_csv_file = open(self._current_csv_path, "w", newline="")
        self._timestamp_csv_writer = csv.writer(self._timestamp_csv_file)
        self._timestamp_csv_writer.writerow(
            ["frame_id", "timestamp_ns", "timestamp_utc", "delta_ms", "dropped_before"]
        )
        self._frame_id = 0
        self._csv_prev_ns = None
        self.facade.add_session_file(self._current_csv_path)


    def _close_timestamp_csv(self) -> None:
        if self._timestamp_csv_file is not None:
            self._timestamp_csv_file.flush()
            os.fsync(self._timestamp_csv_file.fileno())
            self._timestamp_csv_file.close()
            self._timestamp_csv_file = None
            self._timestamp_csv_writer = None
            if self._current_csv_path:
                self.facade.stage_file_for_export(self._current_csv_path)
                self._current_csv_path = None


    def _fix_positioning_timestamps(self, filename: str) -> None:
        tmp = f"{filename[:-3]}_fmt.ts"
        try:
            subprocess.run(
                ["ffmpeg", "-i", filename, "-map", "0", "-c", "copy",
                 "-reset_timestamps", "1", tmp],
                check=True
            )
            os.replace(tmp, filename)
        except Exception as e:
            self.logger.error(f"ffmpeg timestamp fix failed for {filename}: {e}")


    # -----------------------------------------------------------------------
    # Per-frame callbacks
    # -----------------------------------------------------------------------

    def _get_frame_timestamp(self, req) -> Optional[int]:
        try:
            metadata = req.get_metadata()
            sensor_ts = metadata.get('SensorTimestamp')
            if sensor_ts is not None:
                offset_ns = int((time.time() - time.monotonic()) * 1e9)
                return sensor_ts + offset_ns
            return metadata.get('FrameWallClock')
        except Exception as e:
            self.logger.error(f"Timestamp error: {e}")
            return None


    def _apa_frame_precallback(self, req) -> None:
        """Combined pre-callback: timestamps + APA overlays + Hailo detection."""
        try:
            timestamp = self._get_frame_timestamp(req)

            # ── Update per-frame CSV ────────────────────────────────────────
            actual_fps = None
            if timestamp is not None:
                if self.last_frame_timestamp:
                    actual_fps = round((1 / (timestamp - self.last_frame_timestamp)) * 1e9, 3)
                self.last_frame_timestamp = timestamp

                if self._timestamp_csv_writer is not None:
                    dt = datetime.datetime.fromtimestamp(timestamp / 1e9, tz=datetime.timezone.utc)
                    ts_utc = dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"
                    if self._csv_prev_ns is not None and self.fps:
                        delta_ms     = round((timestamp - self._csv_prev_ns) / 1e6, 3)
                        dropped      = max(0, round(delta_ms / (1000.0 / self.fps)) - 1)
                    else:
                        delta_ms = dropped = ""
                    self._csv_prev_ns = timestamp
                    self._timestamp_csv_writer.writerow(
                        [self._frame_id, timestamp, ts_utc, delta_ms, dropped]
                    )
                    self._frame_id += 1

            # ── Format display timestamp ────────────────────────────────────
            if timestamp is not None:
                dt = datetime.datetime.fromtimestamp(timestamp / 1e9, tz=datetime.timezone.utc)
                ts_label = (
                    f"{self.facade.get_module_name()} "
                    + dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "+00:00"
                )
            else:
                ts_label = None

            monochrome        = self.config.get("camera.monochrome") is True
            overlay_timestamp = self.config.get("camera.overlay_timestamp", True)
            detection_enabled = self.config.get("object_detection.enabled", False)

            # ── Main stream (recorded) ──────────────────────────────────────
            with MappedArray(req, 'main') as m:
                if monochrome:
                    self._apply_grayscale(m)
                self._apply_mask(m)
                if overlay_timestamp and ts_label:
                    self._apply_timestamp_label(m, ts_label, "main")
                if detection_enabled:
                    self._detect_objects(m)
                    self._draw_detections(m)

            # ── Lores stream (preview/MJPEG) ────────────────────────────────
            with MappedArray(req, 'lores') as m:
                if monochrome:
                    self._apply_grayscale(m)
                self._apply_mask(m)
                self._apply_shock_zone(m)
                if overlay_timestamp and ts_label:
                    self._apply_timestamp_label(m, ts_label, "lores")
                if actual_fps and self.config.get("camera.overlay_framerate_on_preview", False):
                    self._apply_framerate_label(m, str(actual_fps))
                if detection_enabled:
                    self._draw_detections(m)

        except Exception as e:
            self.logger.error(f"Error in _apa_frame_precallback: {e}")


    _STREAM_FPS = 24
    _STREAM_INTERVAL_S = 1.0 / _STREAM_FPS

    def _stream_post_callback(self, req):
        try:
            now = time.monotonic()
            if now - self._last_stream_encode_time < self._STREAM_INTERVAL_S:
                return
            self._last_stream_encode_time = now
            frame = req.make_array("lores")
            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                with self.frame_lock:
                    self.latest_frame = jpeg.tobytes()
        except Exception as e:
            self.logger.error(f"Stream encode error: {e}")


    # -----------------------------------------------------------------------
    # Hailo object detection
    # -----------------------------------------------------------------------

    def _detect_objects(self, m: MappedArray):
        if self.detector is None:
            return
        frame = m.array
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if frame.ndim != 3 or frame.shape[2] != 3:
            return
        try:
            dets = self.detector.detect(frame, self._labels)[: self.max_detections]
            if dets:
                self._last_known_det = dets[0]
                self._detection_buffer.append(dets[0])
            else:
                self._detection_buffer.append(None)
        except Exception as e:
            self.logger.error(f"Hailo inference error: {e}")


    def _draw_detections(self, m: MappedArray) -> None:
        try:
            if self._last_known_det is None:
                return
            det = self._last_known_det
            x, y, w, h = det.box
            if w <= 0 or h <= 0:
                return

            cx, cy = int(x + w / 2), int(y + h / 2)

            if self.config.get("object_detection.coordinate_smoothing"):
                alpha = 0.5
                if self.last_cx is None: self.last_cx = cx
                if self.last_cy is None: self.last_cy = cy
                cx = int(alpha * cx + (1 - alpha) * self.last_cx)
                cy = int(alpha * cy + (1 - alpha) * self.last_cy)
            self.last_cx, self.last_cy = cx, cy

            in_zone = self._is_in_shock_zone(cx, cy)
            color = (0, 0, 255) if in_zone else (0, 255, 0)
            cv2.circle(m.array, (cx, cy), 5, color, -1)

            if in_zone:
                cv2.putText(m.array, "IN SHOCK ZONE", (50, 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 255), 4, cv2.LINE_AA)

            lbl = (f"{self._labels[det.category]}" if det.category < len(self._labels)
                   else f"cls{det.category}")
            cv2.putText(m.array, f"{lbl} ({det.conf:.2f})", (cx + 10, cy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        except Exception as e:
            self.logger.error(f"Error in _draw_detections: {e}")


    # -----------------------------------------------------------------------
    # APA geometry — mask and shock zone
    # -----------------------------------------------------------------------

    def _configure_mask_and_shock_zone(self):
        try:
            self.mask_radius          = self.config.get("mask.mask_radius")
            self.mask_center_x        = None
            self.mask_center_y        = None
            self.mask_center_x_offset = self.config.get("mask.mask_center_x_offset") or 0
            self.mask_center_y_offset = self.config.get("mask.mask_center_y_offset") or 0
            self.mask_enabled         = self.config.get("mask.mask_enabled")

            self.shock_zone_enabled      = self.config.get("shock_zone.shock_zone_enabled")
            self.shock_zone_display      = self.config.get("shock_zone.shock_zone_display")
            self.shock_zone_angle_span   = self.config.get("shock_zone.shock_zone_angle_span_deg")
            self.shock_zone_start_angle  = self.config.get("shock_zone.shock_zone_start_angle_deg") - 90
            self.shock_zone_inner_offset = self.config.get("shock_zone.shock_zone_inner_offset")
            color = self.config.get("shock_zone.shock_zone_color")
            self.shock_zone_color     = list(color.values()) if isinstance(color, dict) else color
            self.shock_zone_thickness = self.config.get("shock_zone.shock_zone_line_thickness")
        except Exception as e:
            self.logger.error(f"Error configuring mask/shock zone: {e}")


    def _apply_mask(self, m: MappedArray) -> None:
        shape = m.array.shape[:2]
        if self.mask_center_x is None:
            self.mask_center_x = int(shape[1] / 2) + self.mask_center_x_offset
        if self.mask_center_y is None:
            self.mask_center_y = int(shape[0] / 2) + self.mask_center_y_offset

        if self.mask_enabled and self.mask_radius is not None:
            r = int(0.5 * self.mask_radius * shape[1])
            if r > 0:
                mask = np.zeros(shape, dtype="uint8")
                cv2.circle(mask, (self.mask_center_x, self.mask_center_y), r, 255, -1)
                m.array[:] = cv2.bitwise_and(m.array, m.array, mask=mask)


    def _apply_grayscale(self, m: MappedArray) -> None:
        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
        m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


    _TIMESTAMP_WIDTH_FRACTIONS = {"small": 0.50, "medium": 0.72, "large": 0.92}

    def _apply_timestamp_label(self, m: MappedArray, timestamp: str, stream: str) -> None:
        cache_attr = f"_ts_layout_{stream}"
        layout = getattr(self, cache_attr, None)
        if layout is None:
            w = self.width  if stream == "main" else self.lores_width
            h = self.height if stream == "main" else self.lores_height
            font    = cv2.FONT_HERSHEY_SIMPLEX
            preset  = self.config.get("camera.text_size", "medium")
            frac    = self._TIMESTAMP_WIDTH_FRACTIONS.get(preset, 0.72)
            thick   = 2 if preset == "large" else 1
            ref_w, _ = cv2.getTextSize(timestamp, font, 1.0, thick)[0]
            scale   = max(0.3, (frac * w) / ref_w)
            tw, th  = cv2.getTextSize(timestamp, font, scale, thick)[0]
            x = int((w - tw) / 2)
            y = th + max(4, int(h * 0.01))
            layout = (scale, thick, x, y)
            setattr(self, cache_attr, layout)
        scale, thick, x, y = layout
        cv2.putText(m.array, timestamp, (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (50, 255, 50), thick)


    def _apply_framerate_label(self, m: MappedArray, fps_str: str) -> None:
        text = f"{fps_str}fps"
        font  = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.2, self.lores_height * 0.02 / 18)
        tw, th = cv2.getTextSize(text, font, scale, 1)[0]
        x = int((self.lores_width - tw) / 2)
        y = self.lores_height - max(4, int(self.lores_height * 0.01))
        cv2.putText(m.array, text, (x, y), font, scale, (50, 255, 50), 1)


    def _apply_shock_zone(self, m: MappedArray) -> None:
        if not self.shock_zone_display or self.mask_radius is None:
            return

        shape = m.array.shape[:2]
        self.outer_radius = int(0.5 * self.mask_radius * shape[1])
        if self.outer_radius <= 0:
            return

        self.inner_offset = max(0, int(self.shock_zone_inner_offset * self.outer_radius))
        self.start_angle  = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5)
        self.end_angle    = self.start_angle + self.shock_zone_angle_span

        sr, er  = np.radians(self.start_angle), np.radians(self.end_angle)
        cx, cy  = self.mask_center_x, self.mask_center_y
        color   = self.shock_zone_color
        thick   = max(1, self.shock_zone_thickness)

        cv2.ellipse(m.array, (cx, cy), (self.outer_radius, self.outer_radius),
                    0, self.start_angle, self.end_angle, color, thick)
        cv2.ellipse(m.array, (cx, cy), (self.inner_offset, self.inner_offset),
                    0, self.start_angle, self.end_angle, color, thick)
        for angle in (sr, er):
            ox, oy = int(cx + self.outer_radius * np.cos(angle)), int(cy + self.outer_radius * np.sin(angle))
            ix, iy = int(cx + self.inner_offset * np.cos(angle)), int(cy + self.inner_offset * np.sin(angle))
            cv2.line(m.array, (ox, oy), (ix, iy), color, thick)


    def _is_in_shock_zone(self, cx: int, cy: int) -> bool:
        if self.inner_offset is None or self.outer_radius is None:
            return False
        dx, dy = cx - self.mask_center_x, cy - self.mask_center_y
        r      = np.hypot(dx, dy)
        theta  = (np.degrees(np.arctan2(dy, dx)) + 360) % 360

        if not (self.inner_offset <= r <= self.outer_radius):
            return False
        if self.start_angle < self.end_angle:
            return self.start_angle <= theta <= self.end_angle
        return theta >= self.start_angle or theta <= self.end_angle


    # -----------------------------------------------------------------------
    # Streaming
    # -----------------------------------------------------------------------

    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        if self.is_streaming:
            self.logger.warning("Already streaming")
            return False
        port = 8080
        try:
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)
            self.should_stop_streaming = False
            self.streaming_server_thread = threading.Thread(
                target=self.run_streaming_server, args=(port,), daemon=True
            )
            self.streaming_server_thread.start()
            self.is_streaming = True
            self.communication.send_status({
                'type': 'streaming_started', 'port': port, 'status': 'success',
                'message': f'Streaming from {self.network.ip}:{port}',
            })
            return True
        except Exception as e:
            self.logger.error(f"Error starting streaming: {e}")
            return False


    def run_streaming_server(self, port=8080):
        try:
            from werkzeug.serving import make_server
            self.streaming_server = make_server('0.0.0.0', port, self.streaming_app, threaded=True)
            self.streaming_server.serve_forever()
        except Exception as e:
            self.logger.error(f"Streaming server error: {e}")
            self.is_streaming = False
            self.streaming_server = None


    def generate_streaming_frames(self):
        last_frame = None
        while not self.should_stop_streaming:
            with self.frame_lock:
                frame = self.latest_frame
            if frame is None or frame is last_frame:
                time.sleep(0.005)
                continue
            last_frame = frame
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


    def stop_streaming(self) -> bool:
        if not self.is_streaming:
            return False
        try:
            self.should_stop_streaming = True
            if self.streaming_server:
                self.streaming_server.shutdown()
                self.streaming_server = None
            if self.streaming_server_thread and self.streaming_server_thread.is_alive():
                self.streaming_server_thread.join(timeout=1.0)
            self.is_streaming = False
            self.communication.send_status({"type": "streaming_stopped", "status": "success"})
            return True
        except Exception as e:
            self.logger.error(f"Error stopping stream: {e}")
            return False


    def register_routes(self):
        @self.streaming_app.route('/')
        def index():
            return "APA Camera Streaming Server"

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


    # -----------------------------------------------------------------------
    # Health / checks
    # -----------------------------------------------------------------------

    @check()
    def _check_picam(self) -> tuple:
        if not self.picam2:
            return False, "No picam2 object"
        return True, "picam2 initialised"


    def _perform_module_specific_checks(self) -> tuple:
        for fn in self.module_checks:
            result, message = fn()
            if not result:
                return False, message
        return True, "All checks passed"


    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        super().when_controller_discovered(controller_ip, controller_port)


    def start(self) -> bool:
        try:
            if not super().start():
                return False
            self.start_streaming()
            return True
        except Exception as e:
            self.logger.error(f"Error starting module: {e}")
            return False


    def stop(self) -> bool:
        try:
            if self.is_streaming:
                self.stop_streaming()
            if self.detector is not None:
                self.detector.close()
                self.detector = None
            return super().stop()
        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False


def main():
    camera = APACameraModule()
    camera.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        camera.stop()


if __name__ == '__main__':
    main()
