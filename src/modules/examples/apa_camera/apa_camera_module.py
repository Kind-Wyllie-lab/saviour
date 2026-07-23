#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - APA Camera Module

Top-mounted camera module for the Active Place Avoidance (APA) test rig.
Built on CameraBase (src/modules/camera_base.py), which provides Picamera2
lifecycle, MJPEG streaming, segmented recording, and the timestamp-CSV
sidecar. This file adds APA-specific overlays (circular mask, shock zone)
and Hailo-accelerated (or blob-diff fallback) rat detection.

Inference runs on a Hailo-8L AI accelerator (Raspberry Pi AI Kit).
No PyTorch / CUDA required.  To use a custom model, export ratnet.pt → ONNX
→ compile to HEF with the Hailo DFC, then set object_detection.model_path.

Author: Andrew SG
"""

import sys
import os
import time
import numpy as np
import cv2
from picamera2 import MappedArray
from typing import Optional, List
from collections import deque

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase


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
# Blob-differencing tracker
# ---------------------------------------------------------------------------

class BlobTracker:
    """
    Frame-differencing blob tracker — no neural network required.

    Computes the absolute pixel difference between consecutive frames, applies
    morphological bridge-filling to merge fragmented blobs, then finds the
    largest connected component above a minimum area.  Centroid position is
    exponentially smoothed and held for `patience_frames` after the blob is
    lost.

    Compatible with the HailoDetector.detect() interface so the calling code
    in the module callback is unchanged.  The returned Detection always has
    conf=1.0 and a square bounding box centred on the smoothed centroid.

    The circular arena mask is already applied to the frame before detect() is
    called, so the blacked-out region outside the arena contributes zero diff
    and acts as a natural ROI — no separate ROI mask is needed.
    """

    def __init__(self,
                 process_width: int = 256,
                 thr_hi: float = 5.0,
                 gap_h_px: int = 15,
                 gap_v_px: int = 15,
                 close_px: int = 7,
                 open_px: int = 5,
                 min_area_px: int = 50,
                 patience_frames: int = 10,
                 smoothing_alpha: float = 0.3,
                 track_square_size: int = 150):
        self._process_width   = process_width
        self._thr_hi          = thr_hi
        self._min_area        = min_area_px
        self._patience        = patience_frames
        self._alpha           = smoothing_alpha
        self._square          = track_square_size

        def _rect(w, h): return cv2.getStructuringElement(cv2.MORPH_RECT, (w, h))
        def _ellipse(s): return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (s, s))

        self._kern_h     = _rect(max(1, gap_h_px), 1)     if gap_h_px  > 1 else None
        self._kern_v     = _rect(1, max(1, gap_v_px))     if gap_v_px  > 1 else None
        self._kern_close = _ellipse(max(1, close_px))     if close_px  > 1 else None
        self._kern_open  = _ellipse(max(1, open_px))      if open_px   > 1 else None

        self._prev_gray:    Optional[np.ndarray]          = None
        self._last_center:  Optional[tuple]               = None
        self._miss_count:   int                           = 0

    def reset(self) -> None:
        """Clear temporal state.  Call between recording sessions."""
        self._prev_gray   = None
        self._last_center = None
        self._miss_count  = 0

    def detect(self, frame: np.ndarray, labels: list) -> List[Detection]:
        """
        Process one BGR (or grayscale) frame.
        Returns a one-element list with the rat's Detection, or [] if not found.
        """
        orig_h, orig_w = frame.shape[:2]

        # Downsample for speed, convert to grayscale
        proc_w = min(self._process_width, orig_w)
        proc_h = max(1, int(round(proc_w * orig_h / orig_w)))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        proc = cv2.resize(gray, (proc_w, proc_h), interpolation=cv2.INTER_AREA)

        if self._prev_gray is None or self._prev_gray.shape != proc.shape:
            self._prev_gray = proc
            return []

        # Absolute frame difference → threshold
        diff = np.abs(proc.astype(np.float32) - self._prev_gray.astype(np.float32))
        self._prev_gray = proc
        mask = (diff >= self._thr_hi).astype(np.uint8)

        # Bridge-fill horizontal and vertical gaps, then morphological close/open
        for kern, op in (
            (self._kern_h,     cv2.MORPH_CLOSE),
            (self._kern_v,     cv2.MORPH_CLOSE),
            (self._kern_close, cv2.MORPH_CLOSE),
            (self._kern_open,  cv2.MORPH_OPEN),
        ):
            if kern is not None:
                mask = cv2.morphologyEx(mask, op, kern)

        # Largest connected component above area threshold
        n_labels, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        best_label, best_area = 0, 0
        for lab in range(1, n_labels):
            area = int(stats[lab, cv2.CC_STAT_AREA])
            if area >= self._min_area and area > best_area:
                best_area = area
                best_label = lab

        scale_x = orig_w / proc_w
        scale_y = orig_h / proc_h

        if best_label > 0:
            cx = float(centroids[best_label][0]) * scale_x
            cy = float(centroids[best_label][1]) * scale_y
            if self._last_center is not None and self._alpha > 0:
                cx = (1 - self._alpha) * self._last_center[0] + self._alpha * cx
                cy = (1 - self._alpha) * self._last_center[1] + self._alpha * cy
            self._last_center = (cx, cy)
            self._miss_count  = 0
        else:
            if self._last_center is not None:
                self._miss_count += 1
                if self._miss_count > self._patience:
                    self._last_center = None
                    self._miss_count  = 0
            return []

        cx, cy  = self._last_center
        half    = self._square / 2.0
        box     = (int(cx - half), int(cy - half), self._square, self._square)
        return [Detection(category=0, conf=1.0, box=box)]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class APACameraModule(CameraBase):
    CONFIG_FILENAME = "apa_camera_config.json"
    CSV_EXTRA_COLUMNS = ["det_cx", "det_cy", "in_zone"]
    _DEFAULT_BITRATE_MB = 2

    def __init__(self, module_type="apa_camera"):
        super().__init__(module_type)

        # ── APA: detection state ────────────────────────────────────────────
        self.detector: Optional[HailoDetector] = None
        self._detector_backend: Optional[str] = None
        self._labels: List[str] = ["rat"]
        self.threshold = 0.55
        self.max_detections = 2
        self._detection_buffer = deque(maxlen=3)
        self._last_known_det: Optional[Detection] = None

        # ── APA: position tracking ───────────────────────────────────────────
        self.last_cx = None
        self.last_cy = None
        self._prev_in_zone: bool = False

        # (mask/shock-zone geometry is computed per-frame from the current array shape)
        self.mask_radius = None
        self.mask_center_x_offset = 0
        self.mask_center_y_offset = 0
        self.mask_enabled = False
        self.shock_zone_enabled = False
        self.shock_zone_display = False
        self.shock_zone_angle_span = 0
        self.shock_zone_start_angle = 0
        self.shock_zone_inner_offset = 0
        self.shock_zone_color = (0, 0, 255)
        self.shock_zone_thickness = 2

        # ── Apply APA config ─────────────────────────────────────────────────
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()


    # -----------------------------------------------------------------------
    # Config change handler
    # -----------------------------------------------------------------------

    def _configure_module_extra(self, updated_keys) -> None:
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()


    # -----------------------------------------------------------------------
    # Object detection config
    # -----------------------------------------------------------------------

    def _configure_object_detection(self):
        self._labels = self.config.get("object_detection.labels", ["rat"])
        self.threshold = self.config.get("object_detection.threshold", 0.55)
        self.max_detections = self.config.get("object_detection.max_detections", 2)

        if not self.config.get("object_detection.enabled", False):
            if self.detector is not None:
                self.detector.close()
                self.detector = None
                self._detector_backend = None
            return

        new_backend = self.config.get("object_detection.backend", "hailo")
        if self.detector is not None and self._detector_backend == new_backend:
            return  # same backend already running

        if self.detector is not None:
            self.detector.close()
            self.detector = None

        self._detector_backend = new_backend

        if new_backend == "blob":
            bt = self.config.get("blob_tracker", {})
            self.detector = BlobTracker(
                process_width     = bt.get("process_width",     256),
                thr_hi            = bt.get("thr_hi",            5.0),
                gap_h_px          = bt.get("gap_h_px",          15),
                gap_v_px          = bt.get("gap_v_px",          15),
                close_px          = bt.get("close_px",          7),
                open_px           = bt.get("open_px",           5),
                min_area_px       = bt.get("min_area_px",       50),
                patience_frames   = bt.get("patience_frames",   10),
                smoothing_alpha   = bt.get("smoothing_alpha",   0.3),
                track_square_size = bt.get("track_square_size", 150),
            )
            self.logger.info("Blob tracker detector ready")
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
            self._detector_backend = None


    # -----------------------------------------------------------------------
    # Recording — reset detector state on each new session
    # -----------------------------------------------------------------------

    def _start_new_recording(self) -> bool:
        result = super()._start_new_recording()
        if hasattr(self.detector, 'reset'):
            self.detector.reset()
        return result


    # -----------------------------------------------------------------------
    # Per-frame hooks
    # -----------------------------------------------------------------------

    def _process_main_frame(self, m: MappedArray, timing) -> dict:
        self._apply_mask(m)

        det_cx = det_cy = None
        in_zone = False

        if self.config.get("object_detection.enabled", False):
            self._detect_objects(m)
            if self._last_known_det is not None:
                x, y, w, h = self._last_known_det.box
                det_cx = int(x + w / 2)
                det_cy = int(y + h / 2)
                in_zone = self._is_in_shock_zone(det_cx, det_cy)
            self._draw_detections(m)

            if det_cx is not None and in_zone != self._prev_in_zone:
                self.communication.send_status({
                    "type": "zone_entered" if in_zone else "zone_exited",
                    "timestamp_ns": timing.timestamp_ns,
                })
                self._prev_in_zone = in_zone

        return {
            "det_cx": det_cx if det_cx is not None else "",
            "det_cy": det_cy if det_cy is not None else "",
            "in_zone": int(in_zone) if det_cx is not None else "",
        }

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        self._apply_mask(m)
        self._apply_shock_zone(m)
        if self.config.get("object_detection.enabled", False):
            self._draw_detections(m)

    def _apply_framerate(self, arr, framerate: str, stream: str = "main") -> None:
        """Two-line preview overlay: actual fps + the configured stream cap.

        Overrides CameraBase's single-line version — same call signature, so
        the shared _stream_post_callback's self._apply_framerate(...) call
        picks this up automatically via inheritance.
        """
        stream_fps = self._STREAM_FPS if self._stream_interval_s > 0 else None
        height, width = arr.shape[:2]
        font   = cv2.FONT_HERSHEY_SIMPLEX
        scale  = max(0.2, height * 0.02 / 18)
        margin = max(4, int(height * 0.01))
        color  = (50, 255, 50)

        lines = [f"{framerate}fps rec", f"~{stream_fps}fps stream"] if stream_fps is not None else [f"{framerate}fps"]

        _, th = cv2.getTextSize(lines[0], font, scale, 1)[0]
        line_h = th + margin
        y = height - margin
        for line in reversed(lines):
            tw, _ = cv2.getTextSize(line, font, scale, 1)[0]
            x = int((width - tw) / 2)
            cv2.putText(arr, line, (x, y), font, scale, color, 1)
            y -= line_h


    # -----------------------------------------------------------------------
    # Object detection
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

            # Zone check and smoothing always in main-stream pixel space
            cx = int(x + w / 2)
            cy = int(y + h / 2)

            if self.config.get("object_detection.coordinate_smoothing"):
                alpha = 0.5
                if self.last_cx is None: self.last_cx = cx
                if self.last_cy is None: self.last_cy = cy
                cx = int(alpha * cx + (1 - alpha) * self.last_cx)
                cy = int(alpha * cy + (1 - alpha) * self.last_cy)
            self.last_cx, self.last_cy = cx, cy

            in_zone = self._is_in_shock_zone(cx, cy)
            color = (0, 0, 255) if in_zone else (0, 255, 0)

            # Scale to the current frame's dimensions for drawing
            fh, fw = m.array.shape[:2]
            sx = fw / self.width  if self.width  else 1.0
            sy = fh / self.height if self.height else 1.0
            draw_cx = int(cx * sx)
            draw_cy = int(cy * sy)

            cv2.circle(m.array, (draw_cx, draw_cy), 5, color, -1)

            if in_zone:
                cv2.putText(m.array, "IN SHOCK ZONE", (50, 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 255), 4, cv2.LINE_AA)

            lbl = (f"{self._labels[det.category]}" if det.category < len(self._labels)
                   else f"cls{det.category}")
            cv2.putText(m.array, f"{lbl} ({det.conf:.2f})", (draw_cx + 10, draw_cy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        except Exception as e:
            self.logger.error(f"Error in _draw_detections: {e}")


    # -----------------------------------------------------------------------
    # APA geometry — mask and shock zone
    # -----------------------------------------------------------------------

    def _configure_mask_and_shock_zone(self):
        try:
            self.mask_radius          = self.config.get("mask.mask_radius")
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
        if not self.mask_enabled or self.mask_radius is None:
            return
        h, w = m.array.shape[:2]
        cx = w // 2 + self.mask_center_x_offset
        cy = h // 2 + self.mask_center_y_offset
        r  = int(0.5 * self.mask_radius * min(h, w))
        if r > 0:
            mask_img = np.zeros((h, w), dtype="uint8")
            cv2.circle(mask_img, (cx, cy), r, 255, -1)
            m.array[:] = cv2.bitwise_and(m.array, m.array, mask=mask_img)


    def _apply_shock_zone(self, m: MappedArray) -> None:
        if not self.shock_zone_display or self.mask_radius is None:
            return
        h, w    = m.array.shape[:2]
        cx      = w // 2 + self.mask_center_x_offset
        cy      = h // 2 + self.mask_center_y_offset
        outer_r = int(0.5 * self.mask_radius * min(h, w))
        if outer_r <= 0:
            return
        inner_r     = max(0, int(self.shock_zone_inner_offset * outer_r))
        start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5)
        end_angle   = start_angle + self.shock_zone_angle_span
        sr, er  = np.radians(start_angle), np.radians(end_angle)
        color   = self.shock_zone_color
        thick   = max(1, self.shock_zone_thickness)
        cv2.ellipse(m.array, (cx, cy), (outer_r, outer_r), 0, start_angle, end_angle, color, thick)
        cv2.ellipse(m.array, (cx, cy), (inner_r, inner_r), 0, start_angle, end_angle, color, thick)
        for angle in (sr, er):
            ox = int(cx + outer_r * np.cos(angle))
            oy = int(cy + outer_r * np.sin(angle))
            ix = int(cx + inner_r * np.cos(angle))
            iy = int(cy + inner_r * np.sin(angle))
            cv2.line(m.array, (ox, oy), (ix, iy), color, thick)


    def _is_in_shock_zone(self, cx: int, cy: int) -> bool:
        """Check whether (cx, cy) — in main-stream pixel coordinates — lies in the shock zone."""
        if self.mask_radius is None or self.width is None or self.height is None:
            return False
        frame_cx    = self.width  // 2 + self.mask_center_x_offset
        frame_cy    = self.height // 2 + self.mask_center_y_offset
        outer_r     = int(0.5 * self.mask_radius * min(self.height, self.width))
        inner_r     = max(0, int(self.shock_zone_inner_offset * outer_r))
        start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5)
        end_angle   = start_angle + self.shock_zone_angle_span

        dx, dy = cx - frame_cx, cy - frame_cy
        r      = np.hypot(dx, dy)
        theta  = (np.degrees(np.arctan2(dy, dx)) + 360) % 360

        if not (inner_r <= r <= outer_r):
            return False
        if start_angle < end_angle:
            return start_angle <= theta <= end_angle
        return theta >= start_angle or theta <= end_angle


    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def stop(self) -> bool:
        try:
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
