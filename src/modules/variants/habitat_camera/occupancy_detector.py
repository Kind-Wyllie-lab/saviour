"""OccupancyDetector -- a slow, CPU-only "is an animal in frame?" check that
runs alongside habitat_camera's fast motion trigger.

Motion detection (frame_diff / MOG2, see motion_detector.py) fires on *change*,
so a rat that walks in and then sits still scores ~0 within a second or two and
the clip closes out from under it. The occupancy check is the other half of an
OR-fused trigger: a detector run on a downscaled frame every couple of seconds,
cheap enough for the Pi 5 CPU without a Hailo HAT, that re-confirms "there is
still a subject in frame" independent of whether it's moving. Recording
continues while EITHER trigger says so.

This module deliberately has no picamera2 dependency (same reason as
motion_detector.py) so the debounce logic here is unit-testable and the
`analysis/` eval tooling can reuse it.

The scorer is pluggable via `score_fn` (frame_bgr -> float in [0,1]).
`build_score_fn()` builds an ONNX-Runtime one from a model path: it recognises
an Ultralytics YOLOv8/11 *detection* export (output `[1, 4+nc, N]` of already-
sigmoided class scores) and reduces it to occupancy = the single highest class
confidence anywhere in the frame -- no NMS needed. A rank-2 output is treated as
a plain binary classifier instead. With no model configured (the default), the
occupancy trigger is simply disabled and habitat_camera behaves exactly as it
does today (motion-only).

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
        tc = cfg.get("target_class")
        score_fn = build_score_fn(
            cfg.get("model_path", ""),
            target_class=int(tc) if tc is not None else None,
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


def _letterbox(img_rgb, dst_h: int, dst_w: int):
    """Resize keeping aspect ratio, pad to (dst_h, dst_w) with grey (114) --
    Ultralytics' own inference preprocessing, so the detector sees the frame
    the way it was trained rather than horizontally squished (habitat frames
    are 16:9, the model input is square)."""
    h, w = img_rgb.shape[:2]
    r = min(dst_h / h, dst_w / w)
    nh, nw = max(1, round(h * r)), max(1, round(w * r))
    resized = cv2.resize(img_rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.full((dst_h, dst_w, 3), 114, dtype=np.uint8)
    top, left = (dst_h - nh) // 2, (dst_w - nw) // 2
    out[top:top + nh, left:left + nw] = resized
    return out


def build_score_fn(model_path: str, *, target_class: "int | None" = None):
    """Return a `frame_bgr -> float[0,1]` occupancy scorer backed by an ONNX
    model, or None if the model file / onnxruntime isn't available.

    Recognises:
      * an Ultralytics YOLOv8/11 detection export -- output `[1, 4+nc, N]`
        (or `[1, N, 4+nc]`) of box coords + already-sigmoided class scores.
        Occupancy = the highest class confidence over every anchor (optionally
        restricted to `target_class`); NMS is irrelevant to "is anything
        there". Input size and layout are read from the model.
      * a rank-2 output -> plain binary classifier (single logit -> sigmoid,
        or 2-logit -> softmax P(class 1)).
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
    inp = sess.get_inputs()[0]
    in_name = inp.name
    # Input is [N, C, H, W]; use 416 for any dynamic/unknown dim.
    shp = list(inp.shape) + [416, 416, 416, 416]
    in_h = shp[2] if isinstance(shp[2], int) and shp[2] > 0 else 416
    in_w = shp[3] if isinstance(shp[3], int) and shp[3] > 0 else 416
    out_shape = sess.get_outputs()[0].shape
    is_detector = len(out_shape) == 3
    log.info("occupancy model %s: input %dx%d, output %s (%s)", model_path,
             in_w, in_h, out_shape, "detector" if is_detector else "classifier")

    def score(frame_bgr) -> float:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = _letterbox(rgb, in_h, in_w) if is_detector else cv2.resize(
            rgb, (in_w, in_h), interpolation=cv2.INTER_AREA)
        x = np.transpose(img.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
        out = np.asarray(sess.run(None, {in_name: x})[0])

        if not is_detector:
            v = out.ravel()
            if v.size == 1:
                return float(1.0 / (1.0 + np.exp(-v[0])))
            e = np.exp(v - v.max())
            return float((e / e.sum())[-1])

        # out is [1, C, N] or [1, N, C]; the small axis is 4+nc, the large one
        # is the anchor count. Class scores are index 4: along the small axis.
        a = out[0]
        if a.shape[0] <= a.shape[1]:          # [C, N]
            cls = a[4:, :]
        else:                                 # [N, C]
            cls = a[:, 4:].T
        if target_class is not None and 0 <= target_class < cls.shape[0]:
            cls = cls[target_class:target_class + 1, :]
        return float(cls.max()) if cls.size else 0.0

    return score
