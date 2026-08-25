"""
HabitatMotionDetector -- the per-frame activity-score algorithm used by
habitat_camera_module.py, split into its own module so it can be imported
(e.g. by analysis/replay_habitat_motion.py) without dragging in picamera2,
which is only needed by the live camera pipeline around it and generally
isn't installable off a Raspberry Pi.

Author: Andrew SG
"""

import cv2
import numpy as np

# Per-pixel brightness delta (0-255) above which a pixel counts as "changed"
# for the frame_diff algorithm. Fixed rather than a config key for now --
# the frame-level activity_threshold is the intended user-facing sensitivity
# dial (tune from the live score/CSV), not this pixel-level one.
_FRAME_DIFF_PIXEL_THRESHOLD = 15

# MOG2 foreground-mask pixel values: 0 = background, 127 = shadow (excluded
# from the score so shadows don't count as motion), 255 = foreground.
_MOG2_FOREGROUND_VALUE = 200


class HabitatMotionDetector:
    """Per-frame activity score, algorithm-selectable (frame_diff | mog2).

    Both algorithms run on a downscaled grayscale copy of the frame and
    return the same shape of output -- a 0.0-1.0 fraction of "changed"
    pixels -- so callers don't need to know which one is active. Downscaling
    keeps this cheap enough to run on every captured frame regardless of
    recording resolution (same technique as loom_camera's LoomBlobDiffTracker).
    """

    def __init__(self, *, algorithm="frame_diff", process_width=256,
                 mog2_history=500, mog2_var_threshold=16):
        self.algorithm = algorithm
        self.process_width = max(1, int(process_width))
        self._prev_gray = None
        self._bg_subtractor = (
            cv2.createBackgroundSubtractorMOG2(
                history=int(mog2_history),
                varThreshold=float(mog2_var_threshold),
                detectShadows=True,
            )
            if algorithm == "mog2" else None
        )

    @staticmethod
    def _resize_to_width(w0: int, h0: int, target_width: int) -> tuple[int, int]:
        w = max(1, int(target_width))
        h = max(1, round(w * h0 / w0))
        return w, h

    def score(self, frame_bgr: np.ndarray) -> float:
        """Return the fraction (0.0-1.0) of processed pixels flagged as
        changed/foreground. 0.0 on the first frame of a given algorithm
        instance (frame_diff has no previous frame to compare against yet;
        MOG2's background model hasn't seen anything yet either)."""
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = self._resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA)
        proc = cv2.GaussianBlur(proc, (5, 5), 0)

        if self._bg_subtractor is not None:
            mask = self._bg_subtractor.apply(proc)
            changed = int(np.count_nonzero(mask >= _MOG2_FOREGROUND_VALUE))
        else:
            if self._prev_gray is None or self._prev_gray.shape != proc.shape:
                self._prev_gray = proc
                return 0.0
            diff = cv2.absdiff(proc, self._prev_gray)
            self._prev_gray = proc
            changed = int(np.count_nonzero(diff >= _FRAME_DIFF_PIXEL_THRESHOLD))

        return changed / float(nx * ny)
