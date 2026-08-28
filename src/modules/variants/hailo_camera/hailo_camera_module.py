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
    CURATED_MODELS,
    DEFAULT_MODEL,
    MODEL_DIR,
    HailoDetector,
    labels_for,
)
from modules.module import check, command

# One distinct BGR colour per class index, cycled — enough for COCO's 80.
_PALETTE = [
    (0, 255, 0), (0, 165, 255), (255, 128, 0), (0, 0, 255), (255, 0, 255),
    (255, 255, 0), (128, 0, 255), (0, 255, 255), (200, 200, 200), (0, 128, 255),
]


class HailoCameraModule(CameraBase):
    CONFIG_FILENAME = "hailo_camera_config.json"

    def __init__(self, module_type="hailo_camera"):
        super().__init__(module_type)
        self._det_lock = threading.Lock()
        self.detector: HailoDetector | None = None
        self._detector_error: str | None = None
        self._model_key = DEFAULT_MODEL
        self._labels: list[str] = labels_for(DEFAULT_MODEL)
        self._max_labels = 40
        self._infer_error_logged = False
        # CameraBase.__init__ runs _configure_camera(), not
        # configure_module_special(), so build the detector explicitly here —
        # same pattern as habitat_camera's _configure_habitat_motion() call.
        self._build_detector()

    # ── config ───────────────────────────────────────────────────────────────

    def _configure_module_extra(self, updated_keys) -> None:
        if updated_keys is None or any(k.startswith("hailo.") for k in updated_keys):
            self._build_detector()

    def _hef_path(self, spec: dict) -> str:
        return os.path.join(MODEL_DIR, spec["hef"])

    def _build_detector(self) -> None:
        model_key = self.config.get("hailo.model", DEFAULT_MODEL)
        threshold = float(self.config.get("hailo.threshold", 0.4))
        self._max_labels = int(self.config.get("hailo.max_labels", 40))

        spec = CURATED_MODELS.get(model_key)
        if spec is None:
            self.logger.warning(f"hailo.model '{model_key}' not in the curated list — using {DEFAULT_MODEL}")
            model_key = DEFAULT_MODEL
            spec = CURATED_MODELS[DEFAULT_MODEL]

        hef_path = self._hef_path(spec)
        new_detector = None
        error = None
        if not os.path.exists(hef_path):
            error = f"HEF not found: {hef_path} — run download_hefs.sh on this module"
        else:
            try:
                new_detector = HailoDetector(hef_path, threshold=threshold)
            except Exception as e:  # no Hailo device, driver missing, bad HEF …
                error = f"{type(e).__name__}: {e}"

        with self._det_lock:
            old = self.detector
            self.detector = new_detector
            self._detector_error = error
            self._model_key = model_key
            self._labels = labels_for(model_key)
            self._infer_error_logged = False
        if old is not None:
            try:
                old.close()
            except Exception:
                pass

        if new_detector is not None:
            self.logger.info(f"Hailo detector ready: {model_key} ({spec['hef']}), threshold {threshold}")
        else:
            self.logger.warning(f"Hailo detector unavailable ({error}) — running as a plain camera")

    # ── per-frame overlay (MJPEG/preview stream only) ─────────────────────────

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        with self._det_lock:
            detector = self.detector
            labels = self._labels
            err = self._detector_error
            model_key = self._model_key

        if detector is None:
            cv2.putText(m.array, f"Hailo: {err or 'no model'}", (10, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
            return

        try:
            dets = detector.detect(m.array, labels)
        except Exception as e:
            if not self._infer_error_logged:
                self.logger.error(f"Hailo inference failed: {e}")
                self._infer_error_logged = True
            return

        counts: dict[str, int] = {}
        for d in dets[: self._max_labels]:
            name = labels[d.category] if d.category < len(labels) else str(d.category)
            counts[name] = counts.get(name, 0) + 1
            x, y, w, h = d.box
            colour = _PALETTE[d.category % len(_PALETTE)]
            cv2.rectangle(m.array, (x, y), (x + w, y + h), colour, 2, cv2.LINE_AA)
            tag = f"{name} {int(d.conf * 100)}%"
            cv2.putText(m.array, tag, (x, max(12, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)

        summary = "  ".join(f"{n}x {k}" for k, n in
                            sorted(counts.items(), key=lambda kv: -kv[1])) or "nothing detected"
        cv2.putText(m.array, f"[{model_key}]  {summary}", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

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
