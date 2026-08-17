#!/usr/bin/env python3
"""
SAVIOUR System - Lightning Camera Module

Single-view 2D pose-tracking camera for a multi-camera Lightning Pose 3D rig.
Built on CameraBase (src/modules/camera_base.py), which provides Picamera2
lifecycle, MJPEG streaming, segmented recording, and the timestamp-CSV
sidecar. This file adds per-frame 2D keypoint detection and a live keypoint
overlay.

Architecture note (see SHARE_lp3d_package/README.md in this folder for the
full writeup a colleague provided): the multiview Lightning Pose 3D model in
that package CANNOT run on a Hailo -- it ingests all camera views at once
with cross-view attention, and a Hailo runs one model on one stream on one
device. The supported path is a separate single-view 2D CNN pose model
(ResNet/MobileNet backbone) per camera, compiled to a Hailo .hef, with 3D
triangulation done in software (controller-side, out of scope for this
module -- see CLAUDE.md's feature-ideas list) from the collected per-camera
2D keypoints plus the shared calibration TOML.

That single-view model doesn't exist yet, so PoseDetector has two backends:
StubPoseDetector (default -- always returns "no detection", lets the rest of
the module run and be tested today) and HailoPoseDetector (real backend,
same picamera2 Hailo integration apa_camera_module.py's HailoDetector
already uses for object detection -- inert until pose_estimation.model_path
points at a real single-view .hef). See tools/convert_lp_pose_to_hailo.py
and docs/readthedocs/hailo_pose_conversion.md for the conversion path.

Author: Andrew SG
"""

import os
import sys
import time

import cv2
import numpy as np
from picamera2 import MappedArray

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

# A single keypoint result: (x, y) in the frame the detection ran on (pixels),
# or (None, None) if not detected. conf is 0-1, or None if not applicable.
Keypoint = tuple[float | None, float | None, float | None]


# ---------------------------------------------------------------------------
# Pose detection backends
# ---------------------------------------------------------------------------

class StubPoseDetector:
    """Default backend -- no model required. Always reports every configured
    keypoint as not-detected.

    This is what makes the rest of the module (recording, config, CSV
    columns, live overlay wiring) buildable and testable before a real
    single-view Hailo pose model exists -- see the module docstring."""

    def __init__(self, keypoint_names: list[str]):
        self._keypoint_names = keypoint_names

    def detect(self, frame: np.ndarray) -> dict[str, Keypoint]:
        return {name: (None, None, None) for name in self._keypoint_names}

    def close(self) -> None:
        pass


class HailoPoseDetector:
    """
    Wraps picamera2's Hailo integration for single-view 2D pose estimation.
    Same integration point as apa_camera_module.py's HailoDetector, different
    decode: a pose CNN's raw output is a per-keypoint heatmap, not NMS boxes.

    ASSUMED output shape: (K, Hm, Wm) or (Hm, Wm, K) -- one heatmap channel
    per keypoint, in keypoint_names order -- which is Lightning Pose's
    default CNN head. This has not been verified against a real compiled
    .hef (none exists yet); if the actual HEF's output differs (e.g. the
    Hailo DFC's own postprocess script instead emits direct (x, y, conf)
    triplets), only _decode_heatmaps needs to change -- everything else in
    this class and the module is unaffected. Inspect the real output shape
    via the ONNX graph dump printed by tools/convert_lp_pose_to_hailo.py.
    """

    def __init__(
        self, hef_path: str, keypoint_names: list[str], threshold: float = 0.3,
    ):
        from picamera2.devices.hailo import Hailo
        self._hailo = Hailo(hef_path)
        self._input_shape = self._hailo.get_input_shape()
        self._keypoint_names = keypoint_names
        self._threshold = threshold

    @property
    def input_size(self) -> tuple[int, int]:
        shape = self._input_shape
        if len(shape) == 4:
            return shape[1], shape[2]
        return shape[0], shape[1]

    def detect(self, frame: np.ndarray) -> dict[str, Keypoint]:
        """Run inference on a BGR frame. Returns one entry per configured
        keypoint, in original-frame pixel coordinates."""
        h, w = self.input_size
        rgb = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2RGB)
        raw = self._hailo.run(rgb)
        return self._decode_heatmaps(raw, frame.shape)

    def _decode_heatmaps(self, raw, orig_shape: tuple) -> dict[str, Keypoint]:
        heatmaps = np.asarray(raw)
        if heatmaps.ndim == 4:
            heatmaps = heatmaps[0]
        # Normalise to (K, Hm, Wm) regardless of which axis is channel-first.
        if heatmaps.shape[0] == len(self._keypoint_names):
            pass  # already (K, Hm, Wm)
        elif heatmaps.shape[-1] == len(self._keypoint_names):
            heatmaps = np.moveaxis(heatmaps, -1, 0)  # (Hm, Wm, K) -> (K, Hm, Wm)
        else:
            return dict.fromkeys(self._keypoint_names, (None, None, None))

        oh, ow = orig_shape[:2]
        _, hm_h, hm_w = heatmaps.shape
        result: dict[str, Keypoint] = {}
        for i, name in enumerate(self._keypoint_names):
            channel = heatmaps[i]
            peak_idx = int(np.argmax(channel))
            py, px = divmod(peak_idx, hm_w)
            conf = float(channel[py, px])
            if conf < self._threshold:
                result[name] = (None, None, None)
                continue
            x = (px + 0.5) / hm_w * ow
            y = (py + 0.5) / hm_h * oh
            result[name] = (x, y, conf)
        return result

    def close(self) -> None:
        self._hailo.close()


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class LightningCameraModule(CameraBase):
    CONFIG_FILENAME = "lightning_camera_config.json"
    _DEFAULT_BITRATE_MB = 2

    def __init__(self, module_type="lightning_camera"):
        super().__init__(module_type)

        self.detector: StubPoseDetector | HailoPoseDetector | None = None
        self._detector_backend: str | None = None
        self._keypoint_names: list[str] = []
        self._skeleton: list[tuple[str, str]] = []
        self.threshold = 0.3

        # Last detection, in MAIN-stream pixel coordinates -- set in
        # _process_main_frame, drawn (scaled) from _process_lores_frame.
        self._last_keypoints: dict[str, Keypoint] = {}

        self._configure_pose_estimation()

    # -----------------------------------------------------------------------
    # Config change handler
    # -----------------------------------------------------------------------

    def _configure_module_extra(self, updated_keys) -> None:
        self._configure_pose_estimation()

    def _configure_pose_estimation(self) -> None:
        self._keypoint_names = self.config.get("pose_estimation.keypoint_names", [])
        skeleton_pairs = self.config.get("pose_estimation.skeleton", [])
        self._skeleton = [tuple(pair) for pair in skeleton_pairs if len(pair) == 2]
        self.threshold = self.config.get("pose_estimation.threshold", 0.3)

        # CSV_EXTRA_COLUMNS is read fresh per recording segment (_open_timestamp_csv),
        # so recomputing it here on every config change keeps a future recording's
        # header in sync with the current keypoint list without needing a restart.
        self.CSV_EXTRA_COLUMNS = [
            col
            for name in self._keypoint_names
            for col in (f"kp_{name}_x", f"kp_{name}_y", f"kp_{name}_conf")
        ]

        if not self.config.get("pose_estimation.enabled", False):
            if self.detector is not None:
                self.detector.close()
                self.detector = None
                self._detector_backend = None
            return

        new_backend = self.config.get("pose_estimation.backend", "stub")
        if self.detector is not None and self._detector_backend == new_backend:
            return  # same backend already running

        if self.detector is not None:
            self.detector.close()
            self.detector = None
        self._detector_backend = new_backend

        if new_backend == "hailo":
            model_path = self.config.get("pose_estimation.model_path", "")
            try:
                self.detector = HailoPoseDetector(
                    model_path, self._keypoint_names, threshold=self.threshold,
                )
                self.logger.info(f"Hailo pose detector ready: {model_path}")
            except Exception as e:
                self.logger.error(f"Failed to initialise Hailo pose detector: {e}")
                self.detector = None
                self._detector_backend = None
        else:
            self.detector = StubPoseDetector(self._keypoint_names)
            self.logger.info("Stub pose detector ready (no model configured)")

    # -----------------------------------------------------------------------
    # Recording — reset detection state on each new session
    # -----------------------------------------------------------------------

    def _start_new_recording(self) -> bool:
        result = super()._start_new_recording()
        self._last_keypoints = {}
        return result

    # -----------------------------------------------------------------------
    # Per-frame hooks
    # -----------------------------------------------------------------------

    def _process_main_frame(self, m: MappedArray, timing) -> dict:
        pose_enabled = self.config.get("pose_estimation.enabled", False)
        if pose_enabled and self.detector is not None:
            self._detect_pose(m)

        row = {}
        for name in self._keypoint_names:
            x, y, conf = self._last_keypoints.get(name, (None, None, None))
            row[f"kp_{name}_x"] = "" if x is None else f"{x:.2f}"
            row[f"kp_{name}_y"] = "" if y is None else f"{y:.2f}"
            row[f"kp_{name}_conf"] = "" if conf is None else f"{conf:.3f}"
        return row

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        # Deliberately the ONLY place the keypoint/skeleton overlay is drawn --
        # _process_main_frame (the stream that gets recorded) never draws it, so
        # it's a live-monitoring aid only, not baked into the research video.
        # See this session's loom_camera fix for the same reasoning.
        if self.config.get("pose_estimation.overlay.enabled", True):
            self._draw_pose_overlay(m)

    def _detect_pose(self, m: MappedArray) -> None:
        frame = m.array
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if frame.ndim != 3 or frame.shape[2] != 3:
            return
        try:
            self._last_keypoints = self.detector.detect(frame)
        except Exception as e:
            self.logger.error(f"Pose inference error: {e}")

    def _draw_pose_overlay(self, m: MappedArray) -> None:
        if not self._last_keypoints or self.width is None or self.height is None:
            return
        h, w = m.array.shape[:2]
        sx = w / float(self.width)
        sy = h / float(self.height)

        dot_bgr = tuple(self.config.get("pose_estimation.overlay.dot_bgr", [0, 0, 255]))
        line_bgr = tuple(
            self.config.get("pose_estimation.overlay.line_bgr", [0, 255, 0]),
        )
        thickness = int(self.config.get("pose_estimation.overlay.thickness", 2))

        def scaled(name):
            x, y, conf = self._last_keypoints.get(name, (None, None, None))
            if x is None or conf is None or conf < self.threshold:
                return None
            return (round(x * sx), round(y * sy))

        for a, b in self._skeleton:
            pa, pb = scaled(a), scaled(b)
            if pa is not None and pb is not None:
                cv2.line(m.array, pa, pb, line_bgr, thickness, cv2.LINE_AA)

        for name in self._keypoint_names:
            p = scaled(name)
            if p is not None:
                cv2.circle(m.array, p, 4, dot_bgr, -1, cv2.LINE_AA)

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
    camera = LightningCameraModule()
    camera.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        camera.stop()


if __name__ == '__main__':
    main()
