#!/usr/bin/env python3
"""
SAVIOUR System - Hailo AI Camera Module

Built on CameraBase (src/modules/camera_base.py): normal segmented recording +
MJPEG stream + timestamp-CSV sidecar, unchanged. This variant adds a live
inference overlay on the MJPEG preview only — it runs a stock pre-compiled HEF
from the Hailo model zoo over each streamed frame and draws the detections.

Deliberately generic and demo-oriented:
  - No training, no DFC conversion. Models come from `download_hefs.sh`
    (Hailo model-zoo S3), curated in `modules/hailo_infer.CURATED_MODELS`.
  - `hailo.model` picks one; the "AI" tab in the camera config card is a
    dropdown over the curated list, grouped by category.
  - No Hailo device / missing HEF → runs as a plain camera (overlay shows why).
  - Recording is untouched: the overlay is on the lores/preview stream, the
    recorded "main" stream never sees it. A synchronized detection sidecar is
    a deliberate later step (see CLAUDE.md).

Author: Andrew SG
"""

import os
import sys
import threading
import time

import cv2
from picamera2 import MappedArray

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase
from modules.hailo_infer import (
    COCO_SKELETON,
    CURATED_MODELS,
    DEFAULT_MODEL,
    MODEL_DIR,
    HailoDetector,
    HailoPoseDetector,
    labels_for,
)
from modules.module import check, command

# One distinct BGR colour per class index, cycled — enough for COCO's 80.
_PALETTE = [
    (0, 255, 0), (0, 165, 255), (255, 128, 0), (0, 0, 255), (255, 0, 255),
    (255, 255, 0), (128, 0, 255), (0, 255, 255), (200, 200, 200), (0, 128, 255),
]
_KP_COLOUR = (0, 255, 255)      # keypoints
_BONE_COLOUR = (0, 255, 0)      # skeleton edges


class HailoCameraModule(CameraBase):
    CONFIG_FILENAME = "hailo_camera_config.json"

    def __init__(self, module_type="hailo_camera"):
        super().__init__(module_type)
        self._det_lock = threading.Lock()
        self._rebuild_lock = threading.Lock()   # serialises rebuilds; not held per-frame
        self.detector = None                 # HailoDetector | HailoPoseDetector | None
        self._detector_error: str | None = None
        self._model_key = DEFAULT_MODEL
        self._task = "detection"             # "detection" | "pose"
        self._labels: list[str] = labels_for(DEFAULT_MODEL)
        self._max_labels = 40
        self._infer_error_logged = False
        self._rebuilding = False
        # CameraBase.__init__ runs _configure_camera(), not
        # configure_module_special(), so build the detector explicitly here —
        # same pattern as habitat_camera's _configure_habitat_motion() call.
        # Synchronous on first construction (nothing is streaming yet); every
        # later rebuild goes through _rebuild_detector_async().
        self._build_detector()

    # ── config ───────────────────────────────────────────────────────────────

    def _configure_module_extra(self, updated_keys) -> None:
        if updated_keys is None or any(k.startswith("hailo.") for k in updated_keys):
            # Loading a HEF onto the Hailo device takes a few seconds and the
            # 8L won't hold two VDevices at once, so do it off the config-set
            # handler thread and drop to a plain-camera preview while it swaps.
            self._rebuild_detector_async()

    def _rebuild_detector_async(self) -> None:
        threading.Thread(target=self._build_detector, args=(True,),
                         daemon=True, name="hailo-rebuild").start()

    def _hef_path(self, spec: dict) -> str:
        return os.path.join(MODEL_DIR, spec["hef"])

    def _build_detector(self, swap: bool = False) -> None:
        with self._rebuild_lock:
            model_key = self.config.get("hailo.model", DEFAULT_MODEL)
            threshold = float(self.config.get("hailo.threshold", 0.4))
            self._max_labels = int(self.config.get("hailo.max_labels", 40))

            spec = CURATED_MODELS.get(model_key)
            if spec is None:
                self.logger.warning(f"hailo.model '{model_key}' not in the curated list — using {DEFAULT_MODEL}")
                model_key = DEFAULT_MODEL
                spec = CURATED_MODELS[DEFAULT_MODEL]

            # Release the current device handle BEFORE opening a new one — the
            # Hailo-8L can't hold two VDevices, so building-new-while-old-open
            # contends and stalls.
            if swap:
                with self._det_lock:
                    old, self.detector = self.detector, None
                    self._detector_error = "loading model…"
                    self._rebuilding = True
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass

            task = spec.get("task", "detection")
            hef_path = self._hef_path(spec)
            new_detector = None
            error = None
            if not os.path.exists(hef_path):
                error = f"HEF not found: {hef_path} — run download_hefs.sh (wrong Hailo-8/8L arch is the usual cause)"
            else:
                try:
                    if task == "pose":
                        new_detector = HailoPoseDetector(hef_path, threshold=threshold)
                    else:
                        new_detector = HailoDetector(hef_path, threshold=threshold)
                except Exception as e:  # no Hailo device, driver missing, arch mismatch …
                    error = f"{type(e).__name__}: {e}"

            with self._det_lock:
                old = self.detector if not swap else None
                self.detector = new_detector
                self._detector_error = error
                self._model_key = model_key
                self._task = task
                self._labels = labels_for(model_key)
                self._infer_error_logged = False
                self._rebuilding = False
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass

        if new_detector is not None:
            self.logger.info(f"Hailo {task} model ready: {model_key} ({spec['hef']}), threshold {threshold}")
        else:
            self.logger.warning(f"Hailo model unavailable ({error}) — running as a plain camera")

    # ── per-frame overlay (MJPEG/preview stream only) ─────────────────────────

    def _status_line(self, m: MappedArray, text: str, colour: tuple) -> None:
        # Bottom-left corner: timestamp owns top-centre, FPS owns top-right
        # (same collision habitat_camera dodges the same way).
        cv2.putText(m.array, text, (10, m.array.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        with self._det_lock:
            detector = self.detector
            labels = self._labels
            err = self._detector_error
            model_key = self._model_key
            task = self._task
            rebuilding = self._rebuilding

        if detector is None:
            if rebuilding:
                self._status_line(m, "AI: loading model…", (0, 191, 255))
            else:
                self._status_line(m, f"AI off: {err or 'no model'}", (0, 0, 255))
            return

        try:
            results = detector.detect(m.array, labels)
        except Exception as e:
            if not self._infer_error_logged:
                self.logger.error(f"Hailo inference failed: {e}")
                self._infer_error_logged = True
            self._status_line(m, "AI: inference error (see journal)", (0, 0, 255))
            return

        if task == "pose":
            summary = self._draw_poses(m.array, results)
        else:
            summary = self._draw_detections(m.array, results, labels)
        self._status_line(m, f"[{model_key}] {summary}", (255, 255, 255))

    def _draw_detections(self, frame, dets, labels) -> str:
        counts: dict[str, int] = {}
        for d in dets[: self._max_labels]:
            name = labels[d.category] if d.category < len(labels) else str(d.category)
            counts[name] = counts.get(name, 0) + 1
            x, y, w, h = d.box
            colour = _PALETTE[d.category % len(_PALETTE)]
            cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2, cv2.LINE_AA)
            cv2.putText(frame, f"{name} {int(d.conf * 100)}%", (x, max(12, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
        return "  ".join(f"{n}x {k}" for k, n in
                         sorted(counts.items(), key=lambda kv: -kv[1])) or "nothing"

    def _draw_poses(self, frame, poses) -> str:
        for p in poses[: self._max_labels]:
            kp = p.keypoints
            for a, b in COCO_SKELETON:
                xa, ya, ca = kp[a]
                xb, yb, cb = kp[b]
                if ca > 0.3 and cb > 0.3:
                    cv2.line(frame, (xa, ya), (xb, yb), _BONE_COLOUR, 2, cv2.LINE_AA)
            for x, y, c in kp:
                if c > 0.3:
                    cv2.circle(frame, (x, y), 3, _KP_COLOUR, -1, cv2.LINE_AA)
        n = len(poses)
        return f"{n} {'person' if n == 1 else 'people'}"

    # ── commands / checks ────────────────────────────────────────────────────

    @command()
    def list_hailo_models(self) -> dict:
        """Return the curated model list + the module's current selection for
        the config card's 'AI' tab dropdown."""
        return {"models": CURATED_MODELS, "current": self._model_key}

    @check()
    def _check_hailo(self) -> tuple[bool, str]:
        # Never blocks readiness — the module records fine without inference.
        with self._det_lock:
            if self.detector is not None:
                return True, f"Hailo inference active ({self._model_key})"
            return True, f"Hailo inference off ({self._detector_error or 'no model'}) — recording still works"


def main():
    camera = HailoCameraModule()
    camera.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        camera.stop()


if __name__ == "__main__":
    main()
