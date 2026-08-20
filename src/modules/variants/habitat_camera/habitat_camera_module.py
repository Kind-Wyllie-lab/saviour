#!/usr/bin/env python3
"""
SAVIOUR System - Habitat Camera Module Class

Built on CameraBase (src/modules/camera_base.py), which provides Picamera2
lifecycle, MJPEG streaming, segmented recording, and the timestamp-CSV
sidecar. This file adds a per-frame motion/activity score (see CLAUDE.md's
"habitat_camera" feature idea) -- currently shadow-mode only: the score and
a derived armed/waiting/active state are logged to the CSV sidecar and drawn
on the live preview, but nothing yet gates when a clip starts/stops. The
gating (pre-roll via Picamera2's CircularOutput, export-on-clip-close) is a
deliberately separate next step once a sensible threshold has been chosen
from real recorded data.

"Armed" here just means "a normal recording session is active"
(self.is_recording, set by the existing start_recording/stop_recording
command path) -- no new RPC needed. Motion state layered on top of that is
purely a live/logged readout for now, not yet wired to actually start/stop
anything.

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

# Per-pixel brightness delta (0-255) above which a pixel counts as "changed"
# for the frame_diff algorithm. Fixed rather than a config key for now --
# the frame-level activity_threshold is the intended user-facing sensitivity
# dial (tune from the live score/CSV), not this pixel-level one.
_FRAME_DIFF_PIXEL_THRESHOLD = 15

# MOG2 foreground-mask pixel values: 0 = background, 127 = shadow (excluded
# from the score so shadows don't count as motion), 255 = foreground.
_MOG2_FOREGROUND_VALUE = 200

_STATE_COLOR_BGR = {
    "idle":    (128, 128, 128),
    "waiting": (0, 191, 255),
    "active":  (0, 0, 255),
}
_STATE_LABEL = {
    "idle":    "IDLE",
    "waiting": "ARMED",
    "active":  "RECORDING (motion)",
}


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


class HabitatCameraModule(CameraBase):
    CONFIG_FILENAME = "habitat_camera_config.json"
    CSV_EXTRA_COLUMNS = ["motion_score", "motion_state"]

    def __init__(self, module_type="habitat_camera"):
        super().__init__(module_type)
        self._motion_detector: HabitatMotionDetector | None = None
        self._motion_state = "idle"                # idle | waiting | active
        self._motion_since_ns: int | None = None   # start of current above/below streak
        self._motion_last_above: bool | None = None
        self._motion_last_score = 0.0
        # CameraBase.__init__ configures the camera via _configure_camera(),
        # not configure_module_special() -- _configure_module_extra() (and
        # thus the motion detector) otherwise wouldn't exist until the first
        # config push from the controller. Same pattern as loom_camera_module's
        # explicit _configure_loom_tracking() call in its own __init__.
        self._configure_habitat_motion()

    def _configure_module_extra(self, updated_keys) -> None:
        self._configure_habitat_motion()

    def _configure_habitat_motion(self) -> None:
        # Config.get()'s dotted form falls back to the `_`-prefixed internal
        # key when the plain one isn't present (see config.py) -- used here
        # for mog2_history/mog2_var_threshold, which aren't exposed in the
        # frontend's Motion tab.
        self._motion_activity_threshold = float(
            self.config.get("habitat_motion.activity_threshold", 0.02))
        self._motion_activity_min_duration_s = float(
            self.config.get("habitat_motion.activity_min_duration_s", 1.0))
        self._motion_inactivity_min_duration_s = float(
            self.config.get("habitat_motion.inactivity_min_duration_s", 120.0))

        self._motion_detector = HabitatMotionDetector(
            algorithm=self.config.get("habitat_motion.algorithm", "frame_diff"),
            process_width=int(self.config.get("habitat_motion.process_width", 256)),
            mog2_history=int(self.config.get("habitat_motion.mog2_history", 500)),
            mog2_var_threshold=float(
                self.config.get("habitat_motion.mog2_var_threshold", 16)),
        )
        self._motion_state = "idle"
        self._motion_since_ns = None
        self._motion_last_above = None
        self._motion_last_score = 0.0

    def _process_main_frame(self, m: MappedArray, timing) -> dict:
        score = self._motion_detector.score(m.array) if self._motion_detector else 0.0
        self._motion_last_score = score

        # Hysteresis state only progresses while a normal recording session
        # is active ("armed" -- see module docstring). This is shadow-mode
        # only for now: state/score are logged and shown live, nothing acts
        # on them yet.
        if not self.is_recording:
            self._motion_state = "idle"
            self._motion_since_ns = None
            self._motion_last_above = None
        else:
            above = score >= self._motion_activity_threshold
            if self._motion_last_above is None or above != self._motion_last_above:
                self._motion_since_ns = timing.timestamp_ns
                self._motion_last_above = above

            elapsed_s = (
                (timing.timestamp_ns - self._motion_since_ns) / 1e9
                if self._motion_since_ns is not None else 0.0
            )

            if self._motion_state != "active":
                triggered = above and elapsed_s >= self._motion_activity_min_duration_s
                self._motion_state = "active" if triggered else "waiting"
            elif not above and elapsed_s >= self._motion_inactivity_min_duration_s:
                self._motion_state = "waiting"

        return {
            "motion_score": f"{score:.4f}",
            "motion_state": self._motion_state,
        }

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        color = _STATE_COLOR_BGR.get(self._motion_state, _STATE_COLOR_BGR["idle"])
        label = _STATE_LABEL.get(self._motion_state, self._motion_state.upper())
        cv2.circle(m.array, (24, 24), 10, color, -1, cv2.LINE_AA)
        cv2.putText(
            m.array, f"{label}  {self._motion_last_score:.3f}", (42, 32),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA,
        )


def main():
    camera = HabitatCameraModule()
    camera.start()

    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        camera.stop()

if __name__ == '__main__':
    main()
