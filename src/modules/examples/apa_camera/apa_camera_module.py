#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - APA Camera Module

Top-mounted camera module for the Active Place Avoidance (APA) test rig.
Tracks a rat inside a circular arena and reports when it enters a configurable
shock zone.

Inference runs on a Hailo-8L AI accelerator (Raspberry Pi AI Kit) via
picamera2's Hailo integration.  No PyTorch / CUDA required on the Pi itself.

To use a custom model (e.g. ratnet): export your .pt to ONNX, compile to HEF
with the Hailo DFC, then set object_detection.model_path in the config.

Author: Andrew SG
Created: 11/11/2025
"""

import datetime
import sys
import os
import time
import logging
import numpy as np
import threading
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput
from functools import lru_cache
import json
from flask import Flask, Response, request
import cv2
from typing import Optional, List
from collections import deque

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Detection:
    """A single object detection result."""
    def __init__(self, category: int, conf: float, box: tuple):
        self.category = category   # integer class index
        self.conf = conf           # confidence score 0–1
        self.box = box             # (x, y, w, h) in pixels on the *original* frame


# ---------------------------------------------------------------------------
# Hailo inference backend
# ---------------------------------------------------------------------------

class HailoDetector:
    """
    Thin wrapper around picamera2's Hailo integration.

    Loads a HEF model and exposes a simple detect(frame) → list[Detection] API.
    Expected model output format (hailo-all yolov8n / compatible models):
        results[class_id] = ndarray of shape (N, 5) where each row is
        [y_min, x_min, y_max, x_max, confidence]  (all values normalised 0–1)
    """

    def __init__(self, hef_path: str, threshold: float = 0.5):
        from picamera2.devices.hailo import Hailo
        self._hailo = Hailo(hef_path)
        self._input_shape = self._hailo.get_input_shape()
        self._threshold = threshold

    @property
    def input_size(self) -> tuple:
        """(height, width) expected by the model."""
        shape = self._input_shape
        # Shape may be (1, H, W, C) or (H, W, C)
        if len(shape) == 4:
            return shape[1], shape[2]
        return shape[0], shape[1]

    def detect(self, frame: np.ndarray, labels: List[str]) -> List[Detection]:
        """
        Run inference on a BGR frame.
        Returns detections sorted by confidence (highest first).
        """
        h, w = self.input_size
        resized = cv2.resize(frame, (w, h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        raw = self._hailo.run(rgb)
        return self._decode(raw, frame.shape, labels)

    def _decode(self, results, orig_shape: tuple, labels: List[str]) -> List[Detection]:
        detections = []
        orig_h, orig_w = orig_shape[:2]

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
                box = (
                    int(x1 * orig_w),
                    int(y1 * orig_h),
                    int((x2 - x1) * orig_w),
                    int((y2 - y1) * orig_h),
                )
                category = class_id if class_id < len(labels) else 0
                detections.append(Detection(category, score, box))

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

        # Object detection state
        self.detector: Optional[HailoDetector] = None
        self._labels: List[str] = ["rat"]
        self._detection_buffer = deque(maxlen=3)
        self._last_known_det: Optional[Detection] = None

        # Camera
        self.picam2 = Picamera2()
        self.height = None
        self.width = None
        self.fps = None
        self.mode = None
        self.camera_modes = self.picam2.sensor_modes
        time.sleep(0.1)

        # Shock zone / mask geometry (populated by _configure_mask_and_shock_zone)
        self.inner_offset = None
        self.outer_radius = None
        self.start_angle = None
        self.end_angle = None

        # Rat position (smoothed)
        self.last_cx = None
        self.last_cy = None

        # Streaming
        self.streaming_app = Flask(__name__)
        self.streaming_server_thread = None
        self.streaming_server = None
        self.should_stop_streaming = False
        self.register_routes()

        # Configure camera and overlays
        time.sleep(0.1)
        self._configure_camera()
        time.sleep(0.1)

        # State
        self.is_recording = False
        self.is_streaming = False
        self.frame_times = []

        self.camera_callbacks = {
            'start_streaming': self.start_streaming,
            'stop_streaming': self.stop_streaming,
        }
        self.command.set_callbacks(self.camera_callbacks)

        self.module_checks = {self._check_picam}

        self._configure_mask_and_shock_zone()
        self._configure_object_detection()


    def configure_module(self, updated_keys: Optional[list]):
        """Called when config changes."""
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()

        if self.is_streaming:
            restart_keys = ["camera.fps", "camera.width", "camera.height"]
            if any(k in restart_keys for k in updated_keys):
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
        else:
            try:
                self._configure_camera()
            except Exception as e:
                self.logger.error(f"Error reconfiguring camera: {e}")


    def _configure_object_detection(self):
        """(Re)initialise Hailo detector from current config."""
        self._labels = self.config.get("object_detection.labels", ["rat"])
        self.threshold = self.config.get("object_detection.threshold", 0.55)
        self.max_detections = self.config.get("object_detection.max_detections", 2)

        if not self.config.get("object_detection.enabled", False):
            return

        model_path = self.config.get(
            "object_detection.model_path",
            "/usr/share/hailo-models/yolov8n.hef"
        )

        # Re-use existing detector if the model path hasn't changed
        if self.detector is not None:
            return

        try:
            self.detector = HailoDetector(model_path, threshold=self.threshold)
            self.logger.info(f"Hailo detector ready: {model_path}")
        except Exception as e:
            self.logger.error(f"Failed to initialise Hailo detector: {e}")
            self.detector = None


    def _configure_camera(self):
        try:
            if self.picam2.started:
                self.picam2.stop()

            self.fps = self.config.get("camera.fps", 30)
            self.width = self.config.get("camera.width", 1080)
            self.height = self.config.get("camera.height", 1080)
            self.mode = self.camera_modes[0]

            sensor = {"output_size": self.mode["size"], "bit_depth": self.mode["bit_depth"]}
            main   = {"size": (self.width, self.height), "format": "RGB888"}
            lores  = {"size": (self.width, self.height), "format": "RGB888"}
            controls = {"FrameRate": self.fps}

            config = self.picam2.create_video_configuration(
                main=main, lores=lores, sensor=sensor,
                controls=controls, buffer_count=16
            )
            self.picam2.configure(config)
            self.picam2.pre_callback = self._frame_precallback

            bitrate = self.config.get("camera._bitrate", 2_000_000)
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate // 10)

            self.logger.info(f"Camera configured: {self.width}×{self.height} @ {self.fps}fps")
            return True
        except Exception as e:
            self.logger.error(f"Error configuring camera: {e}")
            bitrate = self.config.get("camera._bitrate", 2_000_000)
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate // 10)
            return False


    # -----------------------------------------------------------------------
    # Recording
    # -----------------------------------------------------------------------

    def _start_recording(self):
        filename = f"{self.recording_folder}/{self.current_experiment_name}.{self.config.get('recording.recording_filetype', 'mp4')}"
        self.add_session_file(filename)
        try:
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)

            self.file_output = PyavOutput(filename, format="mp4")
            self.main_encoder.output = self.file_output
            self.picam2.start_encoder(self.main_encoder, name="main")
            self.recording_start_time = time.time()
            self.frame_times = []

            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    "filename": filename,
                    "recording": True,
                    "session_id": self.recording_session_id,
                })
            return True
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            return False


    def _stop_recording(self):
        try:
            self.picam2.stop_encoder(self.main_encoder)

            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                name = getattr(self, 'current_experiment_name', self.recording_session_id)
                timestamps_file = f"{self.recording_folder}/{name}_timestamps.txt"
                self.add_session_file(timestamps_file)
                np.savetxt(timestamps_file, self.frame_times)

                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "session_id": self.recording_session_id,
                        "duration": duration,
                        "frame_count": len(self.frame_times),
                        "status": "success",
                        "recording": False,
                    })
            return True
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False


    # -----------------------------------------------------------------------
    # Streaming
    # -----------------------------------------------------------------------

    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        if self.is_streaming:
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
            })
            return True
        except Exception as e:
            self.logger.error(f"Error starting streaming: {e}")
            return False


    def run_streaming_server(self, port=8080):
        try:
            from werkzeug.serving import make_server
            self.streaming_server = make_server('0.0.0.0', port, self.streaming_app)
            self.streaming_server.serve_forever()
        except Exception as e:
            self.logger.error(f"Streaming server error: {e}")
            self.is_streaming = False
            self.streaming_server = None


    def generate_streaming_frames(self):
        while not self.should_stop_streaming:
            try:
                frame = None
                deadline = time.time() + 2.0
                while frame is None and time.time() < deadline:
                    try:
                        frame = self.picam2.capture_array("lores")
                    except Exception:
                        time.sleep(0.05)
                if frame is None:
                    break
                ret, jpeg = cv2.imencode('.jpg', frame)
                if ret:
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                           + jpeg.tobytes() + b'\r\n')
            except Exception as e:
                self.logger.error(f"Frame generation error: {e}")
                time.sleep(0.1)


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


    # -----------------------------------------------------------------------
    # Per-frame callback
    # -----------------------------------------------------------------------

    def _frame_precallback(self, req):
        try:
            metadata = req.get_metadata()
            self._save_frame_timestamp(metadata)

            with MappedArray(req, 'main') as m:
                if self.config.get("camera.monochrome"):
                    self._apply_grayscale(m)
                self._apply_mask(m)
                if self.config.get("object_detection.enabled"):
                    self._detect_objects(m)
                    self._draw_detections(m)

            with MappedArray(req, 'lores') as m:
                if self.config.get("camera.monochrome"):
                    self._apply_grayscale(m)
                self._apply_mask(m)
                self._apply_shock_zone(m)
                self._apply_timestamp(m)
                if self.config.get("object_detection.enabled"):
                    self._draw_detections(m)

        except Exception as e:
            self.logger.error(f"Error in _frame_precallback: {e}")


    def _detect_objects(self, m: MappedArray):
        """Run Hailo inference on the current frame and update detection state."""
        if self.detector is None:
            return

        frame = m.array
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if frame.ndim != 3 or frame.shape[2] != 3:
            return

        try:
            dets = self.detector.detect(frame, self._labels)
            dets = dets[:self.max_detections]
            if dets:
                self._last_known_det = dets[0]   # already sorted by confidence
                self._detection_buffer.append(dets[0])
            else:
                self._detection_buffer.append(None)
        except Exception as e:
            self.logger.error(f"Hailo inference error: {e}")


    # -----------------------------------------------------------------------
    # Drawing
    # -----------------------------------------------------------------------

    def _draw_detections(self, m: MappedArray) -> None:
        """Draw the smoothed last-known detection onto the frame."""
        try:
            if self._last_known_det is None:
                return

            det = self._last_known_det
            x, y, w, h = det.box
            if w <= 0 or h <= 0:
                return

            cx = int(x + w / 2)
            cy = int(y + h / 2)

            if self.config.get("object_detection.coordinate_smoothing"):
                alpha = 0.5
                if self.last_cx is None: self.last_cx = cx
                if self.last_cy is None: self.last_cy = cy
                cx = int(alpha * cx + (1 - alpha) * self.last_cx)
                cy = int(alpha * cy + (1 - alpha) * self.last_cy)
            self.last_cx = cx
            self.last_cy = cy

            in_zone = self._is_in_shock_zone(cx, cy)
            color = (0, 0, 255) if in_zone else (0, 255, 0)
            cv2.circle(m.array, (cx, cy), 5, color, -1)

            if in_zone:
                cv2.putText(m.array, "IN SHOCK ZONE", (50, 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 255), 4, cv2.LINE_AA)

            label_text = f"{self._labels[det.category]} ({det.conf:.2f})" \
                if det.category < len(self._labels) else f"cls{det.category} ({det.conf:.2f})"
            cv2.putText(m.array, label_text, (cx + 10, cy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

        except Exception as e:
            self.logger.error(f"Error in _draw_detections: {e}")


    # -----------------------------------------------------------------------
    # APA geometry — mask, shock zone, in-zone check
    # -----------------------------------------------------------------------

    def _configure_mask_and_shock_zone(self):
        try:
            self.mask_radius = self.config.get("mask.mask_radius")
            self.mask_center_x = None
            self.mask_center_y = None
            self.mask_center_x_offset = self.config.get("mask.mask_center_x_offset") or 0
            self.mask_center_y_offset = self.config.get("mask.mask_center_y_offset") or 0
            self.mask_enabled = self.config.get("mask.mask_enabled")

            self.shock_zone_enabled = self.config.get("shock_zone.shock_zone_enabled")
            self.shock_zone_display = self.config.get("shock_zone.shock_zone_display")
            self.shock_zone_angle_span = self.config.get("shock_zone.shock_zone_angle_span_deg")
            self.shock_zone_start_angle = self.config.get("shock_zone.shock_zone_start_angle_deg") - 90
            self.shock_zone_inner_offset = self.config.get("shock_zone.shock_zone_inner_offset")
            color = self.config.get("shock_zone.shock_zone_color")
            self.shock_zone_color = list(color.values()) if isinstance(color, dict) else color
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


    def _apply_timestamp(self, m: MappedArray) -> None:
        ts = time.strftime("%Y-%m-%d %X")
        cv2.putText(m.array, ts,
                    (0, self.height - int(self.height * 0.01)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (50, 255, 50), 2)


    def _apply_shock_zone(self, m: MappedArray) -> None:
        shape = m.array.shape[:2]
        if not self.shock_zone_display or self.mask_radius is None:
            return

        self.outer_radius = int(0.5 * self.mask_radius * shape[1])
        if self.outer_radius <= 0:
            return

        self.inner_offset = max(0, int(self.shock_zone_inner_offset * self.outer_radius))

        self.start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5)
        self.end_angle   = self.start_angle + self.shock_zone_angle_span

        sr, er = np.radians(self.start_angle), np.radians(self.end_angle)
        cx, cy = self.mask_center_x, self.mask_center_y
        color, thick = self.shock_zone_color, max(1, self.shock_zone_thickness)

        # Outer arc
        cv2.ellipse(m.array, (cx, cy), (self.outer_radius, self.outer_radius),
                    0, self.start_angle, self.end_angle, color, thick)
        # Inner arc
        cv2.ellipse(m.array, (cx, cy), (self.inner_offset, self.inner_offset),
                    0, self.start_angle, self.end_angle, color, thick)
        # Side lines
        for angle in (sr, er):
            ox = int(cx + self.outer_radius * np.cos(angle))
            oy = int(cy + self.outer_radius * np.sin(angle))
            ix = int(cx + self.inner_offset * np.cos(angle))
            iy = int(cy + self.inner_offset * np.sin(angle))
            cv2.line(m.array, (ox, oy), (ix, iy), color, thick)


    def _is_in_shock_zone(self, cx: int, cy: int) -> bool:
        if self.inner_offset is None or self.outer_radius is None:
            return False

        dx = cx - self.mask_center_x
        dy = cy - self.mask_center_y
        r = np.hypot(dx, dy)
        theta = (np.degrees(np.arctan2(dy, dx)) + 360) % 360

        within_radius = self.inner_offset <= r <= self.outer_radius
        if self.start_angle < self.end_angle:
            within_angle = self.start_angle <= theta <= self.end_angle
        else:
            within_angle = theta >= self.start_angle or theta <= self.end_angle

        return within_radius and within_angle


    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _save_frame_timestamp(self, metadata: dict) -> None:
        wc = metadata.get('FrameWallClock')
        if wc is not None:
            self.frame_times.append(wc)


    # -----------------------------------------------------------------------
    # Health checks
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
