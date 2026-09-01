"""OccupancyDetector -- a slow, CPU-only "is an animal in frame?" check that
runs alongside habitat_camera's fast motion trigger.

Motion detection (frame_diff / MOG2, see motion_detector.py) fires on *change*,
so a rat that walks in and then sits still scores ~0 within a second or two and
the clip closes out from under it. The occupancy check is the other half of an
OR-fused trigger: a binary classifier run on a downscaled frame every couple of
seconds, cheap enough for the Pi 5 CPU without a Hailo HAT, that re-confirms
"there is still a subject in frame" independent of whether it's moving. Recording
continues while EITHER trigger says so.

This module deliberately has no picamera2 dependency (same reason as
motion_detector.py) so the debounce logic here is unit-testable and the
`analysis/` eval tooling can reuse it.

The classifier itself is pluggable via `score_fn` (frame_bgr -> float in [0,1]).
`build_score_fn()` builds an ONNX-Runtime-backed one from a model path; with no
model configured (the default), the occupancy trigger is simply disabled and
habitat_camera behaves exactly as it does today (motion-only). Training that
model is follow-up work -- see analysis/.

Author: Andrew SG
"""

import logging
import os

import cv2
import numpy as np

log = logging.getLogger(__name__)


class OccupancyDetector:
    """Debounced wrapper around a per-frame occupancy score.

    Feed frames to `observe()` on a timer. `present` only flips True after
    `confirm_samples` consecutive over-threshold scores (a single false
    positive from one frame shouldn't open a clip), and only flips back False
    once the score has stayed under threshold for `clear_secs` (so a subject
    that briefly occludes / the classifier momentarily missing doesn't end
    the clip -- this is the occupancy analogue of the motion trigger's
    inactivity hangover).
    """

    def __init__(self, *, score_fn, threshold=0.5, confirm_samples=2,
                 clear_secs=30.0):
        self._score_fn = score_fn
        self.threshold = float(threshold)
        self.confirm_samples = max(1, int(confirm_samples))
        self.clear_secs = max(0.0, float(clear_secs))
        self.present = False
        self.last_score = 0.0
        self._consec_positive = 0
        self._last_positive_ns: int | None = None

    def observe(self, frame_bgr, now_ns: int) -> bool:
        """Score one frame and update `present`. `now_ns` is a wall-clock
        nanosecond timestamp (the same clock habitat_camera stamps frames
        with). Never raises -- a scorer failure is logged and the previous
        `present` is held."""
        try:
            score = float(self._score_fn(frame_bgr))
        except Exception as e:
            log.warning("occupancy score_fn failed: %s", e)
            return self.present

        self.last_score = score
        positive = score >= self.threshold

        if positive:
            self._consec_positive += 1
            self._last_positive_ns = now_ns
            if self._consec_positive >= self.confirm_samples:
                self.present = True
        else:
            self._consec_positive = 0
            if (self.present and self._last_positive_ns is not None
                    and (now_ns - self._last_positive_ns) / 1e9 >= self.clear_secs):
                self.present = False
        return self.present

    def reset(self) -> None:
        self.present = False
        self.last_score = 0.0
        self._consec_positive = 0
        self._last_positive_ns = None

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, cfg: dict) -> "OccupancyDetector | None":
        """Build a detector from an `occupancy.*` config block, or return
        None (occupancy trigger disabled) when it's off / no usable model.
        Logs the reason so an operator can tell "off by config" from
        "meant to be on but the model/runtime is missing"."""
        if not cfg or not cfg.get("enabled"):
            return None
        score_fn = build_score_fn(
            cfg.get("model_path", ""),
            input_size=int(cfg.get("input_size", 128)),
            grayscale=bool(cfg.get("grayscale", True)),
        )
        if score_fn is None:
            log.warning("occupancy.enabled is set but no usable model -- "
                        "occupancy trigger disabled (motion-only)")
            return None
        return cls(
            score_fn=score_fn,
            threshold=float(cfg.get("threshold", 0.5)),
            confirm_samples=int(cfg.get("confirm_samples", 2)),
            clear_secs=float(cfg.get("clear_secs", 30.0)),
        )


def build_score_fn(model_path: str, *, input_size: int = 128,
                   grayscale: bool = True):
    """Return a `frame_bgr -> float[0,1]` scorer backed by an ONNX binary
    classifier, or None if the model file / onnxruntime isn't available.

    Pre-processing here (resize -> optional grayscale -> /255 -> NCHW) is a
    reasonable default but MUST match whatever model is eventually trained
    (analysis/); treat it as unverified until checked against a real export,
    same caveat as hailo_camera's decoders.
    """
    if not model_path:
        return None
    if not os.path.isfile(model_path):
        log.warning("occupancy model not found: %s", model_path)
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        log.warning("occupancy.model_path set but onnxruntime is not installed")
        return None

    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    in_name = sess.get_inputs()[0].name

    def score(frame_bgr) -> float:
        img = cv2.resize(frame_bgr, (input_size, input_size),
                         interpolation=cv2.INTER_AREA)
        if grayscale:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[..., None]
        x = np.transpose(img.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
        out = np.asarray(sess.run(None, {in_name: x})[0]).ravel()
        if out.size == 1:  # single logit
            return float(1.0 / (1.0 + np.exp(-out[0])))
        e = np.exp(out - out.max())  # 2-class softmax -> P(occupied)
        return float((e / e.sum())[-1])

    return score
