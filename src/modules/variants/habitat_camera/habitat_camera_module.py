#!/usr/bin/env python3
"""
SAVIOUR System - Habitat Camera Module Class

Built on CameraBase (src/modules/camera_base.py), which provides Picamera2
lifecycle, MJPEG streaming, segmented recording, and the timestamp-CSV
sidecar. This file adds a per-frame motion/activity score and gates
recording on it: no clip file is written until the score crosses
activity_threshold for activity_min_duration_s, using a CircularOutput
pre-roll buffer (habitat_motion.pre_roll_secs) so the clip includes footage
from just before the trigger.

"Armed" means a normal recording session is active
(self.facade.get_recording_status(); self.is_recording is unused for camera
modules). Each motion-triggered clip is a file added to that session via the
per-file export API (facade.add_session_file() / stage_file_for_export()),
not a session of its own.

While armed, CameraBase's own continuous SplittableOutput recording is
replaced with a CircularOutput the encoder always writes into (buffering
recent frames regardless of whether a clip file is currently open) --
_start_new_recording() below builds it, _open_clip()/_close_clip() flush it
to/from an actual file on each idle/waiting<->active transition, and
_stop_recording() finalises whatever's open when disarmed.

Author: Andrew SG
"""

import csv
import os
import subprocess
import sys
import threading
import time

import cv2
from picamera2 import MappedArray
from picamera2.outputs import CircularOutput

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase
from modules.module import command
from modules.variants.habitat_camera.motion_detector import HabitatMotionDetector
from modules.variants.habitat_camera.occupancy_detector import OccupancyDetector

# Downscale width the occupancy side-thread works from -- small enough that
# the per-interval copy off the capture thread is a couple of ms, large
# enough for a classifier to still see the subject. The classifier resizes
# further to occupancy.input_size.
_OCC_CACHE_WIDTH = 320

_STATE_COLOR_BGR = {
    "idle":    (128, 128, 128),
    "waiting": (0, 191, 255),
    "active":  (0, 0, 255),
}
# _motion_state runs identically whether or not armed, so the livestream
# preview shows the real trigger logic and an operator can tune against it
# without recording. "idle" = below threshold; "waiting" = above threshold,
# accumulating toward activity_min_duration_s; "active" = triggered (writes a
# clip only when armed).


class HabitatCameraModule(CameraBase):
    CONFIG_FILENAME = "habitat_camera_config.json"
    CSV_EXTRA_COLUMNS = [
        "motion_score", "motion_state", "occupancy_score", "occupancy_present",
    ]

    def __init__(self, module_type="habitat_camera"):
        super().__init__(module_type)
        self._motion_detector: HabitatMotionDetector | None = None
        self._motion_state = "idle"                # idle | waiting | active
        self._motion_since_ns: int | None = None   # start of current above/below streak
        self._motion_last_above: bool | None = None   # fused (motion OR occupancy)
        self._motion_last_motion_above: bool | None = None  # motion component only, for the overlay
        self._motion_last_occupied = False                  # occupancy component only, for the overlay
        self._motion_last_score = 0.0
        self._motion_last_exposure_time_us = None  # previous frame's AE metadata, for
        self._motion_last_analogue_gain = None      # detecting an active AE adjustment
        self._motion_ae_unstable_until_ns: int | None = None  # see _update_ae_stability
        self._motion_ae_stable = True                          # for the live overlay
        self._circular_output: CircularOutput | None = None  # built on arm, see _start_new_recording
        self._clip_open = False        # whether a clip file is currently being written
        self._clip_h264_path = None    # raw encoder output, remuxed to .ts on close
        self._clip_counter = 0         # per-armed-session clip numbering, for unique filenames
        self._diag_csv_file = None     # continuous score/state log for the whole armed session --
        self._diag_csv_writer = None   # independent of clip_open, see _open_diagnostic_csv
        self._diag_csv_path = None
        # CameraBase.__init__ configures the camera via _configure_camera(),
        # not configure_module_special() -- _configure_module_extra() (and
        # thus the motion detector) otherwise wouldn't exist until the first
        # config push from the controller. Same pattern as loom_camera_module's
        # explicit _configure_loom_tracking() call in its own __init__.
        self._configure_habitat_motion()

        # --- Occupancy trigger (slow CPU "is a subject in frame?" check) ---
        # OR-fused with the motion trigger: a rat that stops moving stops
        # scoring motion within ~1s, but stays "occupied" here, so the clip
        # keeps recording. Disabled unless occupancy.enabled + a usable model
        # -- otherwise habitat_camera is motion-only exactly as before.
        self._occupancy_detector: OccupancyDetector | None = None
        self._occupancy_interval_s = 2.0
        self._occ_frame_lock = threading.Lock()
        self._latest_occ_frame = None       # downscaled BGR copy, refreshed on the capture thread
        self._occ_last_cache_ns = 0
        self._occupancy_stop = threading.Event()
        self._occupancy_thread: threading.Thread | None = None
        self._configure_occupancy()
        self._occupancy_thread = threading.Thread(
            target=self._occupancy_loop, daemon=True, name="habitat-occupancy")
        self._occupancy_thread.start()

        self._recover_orphaned_clips()

    def _configure_module_extra(self, updated_keys) -> None:
        # Only rebuild the detector (which resets its hysteresis timer) when a
        # habitat_motion.* key changed, or on a full reconfigure (updated_keys
        # None). This hook runs on EVERY config push, including FrameSync's
        # sync_mode push on most reconnects; rebuilding each time would keep
        # resetting the streak before it reaches activity_min_duration_s.
        if updated_keys is None or any(k.startswith("habitat_motion.") for k in updated_keys):
            self._configure_habitat_motion()
        if updated_keys is None or any(k.startswith("occupancy.") for k in updated_keys):
            self._configure_occupancy()

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
            self.config.get("habitat_motion.inactivity_min_duration_s", 300.0))
        self._motion_pre_roll_secs = float(
            self.config.get("habitat_motion.pre_roll_secs", 3.0))
        self._motion_ae_settle_s = float(
            self.config.get("habitat_motion.ae_settle_s", 0.75))

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
        self._motion_last_exposure_time_us = None
        self._motion_last_analogue_gain = None
        self._motion_ae_unstable_until_ns = None
        self._motion_ae_stable = True

    def _configure_occupancy(self) -> None:
        occ_cfg = dict(self.config.get("occupancy", {}) or {})
        self._occupancy_interval_s = max(
            0.2, float(occ_cfg.get("interval_s", 2.0)))
        # Resolve a relative model_path against this variant folder (matches
        # hailo_camera's MODEL_DIR/<hef> convention).
        mp = occ_cfg.get("model_path", "")
        if mp and not os.path.isabs(mp):
            occ_cfg["model_path"] = os.path.join(os.path.dirname(__file__), mp)
        new = OccupancyDetector.from_config(occ_cfg)
        # Carry the current `present` state across a live reconfigure so a
        # threshold/interval tweak doesn't drop a subject mid-clip.
        if new is not None and self._occupancy_detector is not None:
            new.present = self._occupancy_detector.present
        self._occupancy_detector = new
        self.logger.info(
            "Occupancy trigger %s",
            "enabled" if self._occupancy_detector else "disabled (motion-only)")

    def _occupancy_loop(self) -> None:
        """Score the latest cached frame every occupancy.interval_s. Runs for
        the module's lifetime; does nothing until frames are flowing and a
        detector is configured."""
        while not self._occupancy_stop.wait(self._occupancy_interval_s):
            detector = self._occupancy_detector
            if detector is None:
                continue
            with self._occ_frame_lock:
                frame = self._latest_occ_frame
            if frame is None:
                continue
            try:
                detector.observe(frame, time.time_ns())
            except Exception as e:
                self.logger.warning(f"Occupancy loop error: {e}")

    def _maybe_cache_occ_frame(self, arr, timing) -> None:
        """Stash a small downscaled BGR copy of the current frame for the
        occupancy thread, at most once per interval so the capture thread
        isn't doing a resize every frame."""
        if self._occupancy_detector is None:
            return
        if (timing.timestamp_ns - self._occ_last_cache_ns
                < self._occupancy_interval_s * 1e9):
            return
        self._occ_last_cache_ns = timing.timestamp_ns
        try:
            h, w = arr.shape[:2]
            nh = max(1, round(_OCC_CACHE_WIDTH * h / w))
            small = cv2.resize(arr, (_OCC_CACHE_WIDTH, nh),
                               interpolation=cv2.INTER_AREA)
            with self._occ_frame_lock:
                self._latest_occ_frame = small
        except Exception as e:
            self.logger.debug(f"Occupancy frame cache failed: {e}")

    def _update_ae_stability(self, timing) -> bool:
        """Return whether auto-exposure/gain has been steady for at least
        ae_settle_s (0.75s default). MOG2 (and, to a lesser extent, frame_diff)
        treat a sudden global brightness step -- which is exactly what a
        continuous-AE camera produces every time it nudges exposure_time_us or
        analogue_gain in response to changing ambient light -- as a frame full
        of "foreground" pixels, indistinguishable from real motion until the
        background model catches up. In a real deployment AE drift caused
        all of the daytime false triggers; gating on AE stability removes
        them without raising activity_threshold (which would dull genuine
        motion).

        Compares the previous frame's exact metadata values (no epsilon):
        Picamera2 reports the identical float once AE has converged, so any
        change means AE is still moving."""
        exposure_time_us = timing.exposure_time_us
        analogue_gain = timing.analogue_gain
        changed = (
            self._motion_last_exposure_time_us is not None
            and (
                exposure_time_us != self._motion_last_exposure_time_us
                or analogue_gain != self._motion_last_analogue_gain
            )
        )
        self._motion_last_exposure_time_us = exposure_time_us
        self._motion_last_analogue_gain = analogue_gain

        if changed:
            self._motion_ae_unstable_until_ns = (
                timing.timestamp_ns + int(self._motion_ae_settle_s * 1e9)
            )
        self._motion_ae_stable = (
            self._motion_ae_unstable_until_ns is None
            or timing.timestamp_ns >= self._motion_ae_unstable_until_ns
        )
        return self._motion_ae_stable

    def _process_main_frame(self, m: MappedArray, timing) -> dict:
        score = self._motion_detector.score(m.array) if self._motion_detector else 0.0
        self._motion_last_score = score
        # AE-unstable frames never count toward a trigger, regardless of score
        # -- see _update_ae_stability's docstring. The raw score is still
        # computed/logged above/below so the diagnostic CSV keeps showing
        # exactly what the algorithm saw, not a suppressed value.
        ae_stable = self._update_ae_stability(timing)
        motion_above = score >= self._motion_activity_threshold and ae_stable

        # Hand a downscaled frame to the occupancy side-thread (throttled).
        self._maybe_cache_occ_frame(m.array, timing)
        occupied = bool(self._occupancy_detector and self._occupancy_detector.present)
        # Stashed for the livestream overlay (which runs on the lores stream
        # and doesn't recompute these) so it can attribute the trigger.
        self._motion_last_motion_above = motion_above
        self._motion_last_occupied = occupied
        # OR fusion: the two triggers are independent and a missed detection
        # on either loses footage, so record while EITHER says so. Note the
        # occupancy side is already debounced (confirm_samples / clear_secs)
        # in OccupancyDetector, so the motion trigger's own sustained-duration
        # / inactivity-hangover timing below applies to the fused signal
        # without double-counting -- once a subject stops moving, `occupied`
        # holds the streak True past inactivity_min_duration_s until the
        # classifier stops seeing it.
        above = motion_above or occupied

        # The state machine runs whether or not armed (see _STATE_COLOR_BGR);
        # get_recording_status() below only gates opening a clip file.
        if self._motion_last_above is None or above != self._motion_last_above:
            self._motion_since_ns = timing.timestamp_ns
            self._motion_last_above = above

        elapsed_s = (
            (timing.timestamp_ns - self._motion_since_ns) / 1e9
            if self._motion_since_ns is not None else 0.0
        )

        if self._motion_state != "active":
            # An occupancy trigger is a deliberate, already-debounced
            # classification -- it doesn't also need to clear the motion
            # trigger's noise-debounce (activity_min_duration_s), so it fires
            # as soon as the fused signal is up.
            triggered = above and (
                occupied or elapsed_s >= self._motion_activity_min_duration_s)
            if triggered:
                self._motion_state = "active"
                if self.facade.get_recording_status():
                    self._open_clip()
            else:
                self._motion_state = "waiting" if above else "idle"
        elif not above and elapsed_s >= self._motion_inactivity_min_duration_s:
            self._motion_state = "idle"
            if self._clip_open:
                self._close_clip()

        # Independent of whether a clip is open -- the per-clip timestamp CSV
        # only exists while one is, so without this there's no record at all
        # of what the score was doing during an idle/waiting stretch, making
        # it impossible to tell after the fact whether a session that never
        # triggered saw no real motion, or saw motion that just never crossed
        # threshold/lasted long enough. See _open_diagnostic_csv().
        occ_score = (self._occupancy_detector.last_score
                     if self._occupancy_detector else 0.0)
        if self._diag_csv_writer is not None:
            self._diag_csv_writer.writerow(
                [timing.timestamp_utc, f"{score:.4f}", self._motion_state,
                 self._clip_open, self._motion_ae_stable,
                 f"{occ_score:.4f}", occupied]
            )

        return {
            "motion_score": f"{score:.4f}",
            "motion_state": self._motion_state,
            "occupancy_score": f"{occ_score:.4f}",
            "occupancy_present": "1" if occupied else "0",
        }

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        color = _STATE_COLOR_BGR.get(self._motion_state, _STATE_COLOR_BGR["idle"])
        if self._motion_state == "idle":
            # A high score held off by the AE gate shows as "AE settling", so
            # an operator can tell it from a threshold/duration problem.
            # ("waiting" implies AE is stable, so this only applies to idle.)
            score_over = self._motion_last_score >= self._motion_activity_threshold
            if not self._motion_ae_stable and score_over:
                label = "IDLE (AE settling)"
            else:
                label = "IDLE"
        elif self._motion_state == "waiting":
            label = "ABOVE THRESHOLD"
        else:  # active
            # Attribute the trigger: which of the two OR'd signals is
            # currently holding the state active (see _process_main_frame's
            # OR fusion). Only meaningful when the occupancy detector is on;
            # with it off it's always motion.
            if self._occupancy_detector is not None:
                m_on, o_on = self._motion_last_motion_above, self._motion_last_occupied
                reason = ("motion+rat" if (m_on and o_on)
                          else "rat" if o_on else "motion")
            else:
                reason = "motion"
            label = (f"RECORDING ({reason})" if self._clip_open
                     else f"TRIGGERED ({reason}, not armed)")
            # self._motion_last_above is False exactly while the
            # inactivity-duration countdown toward closing is running --
            # True (or None, before any frame's been scored) means the fused
            # trigger is still up right now, so there's nothing counting down.
            if self._motion_last_above is False and self._motion_since_ns is not None:
                elapsed_s = (timing.timestamp_ns - self._motion_since_ns) / 1e9
                remaining_s = max(0.0, self._motion_inactivity_min_duration_s - elapsed_s)
                mins, secs = divmod(int(remaining_s), 60)
                label = f"{label} - closing in {mins:02d}:{secs:02d}"
        # Drawn bottom-left: top-center holds the timestamp and top-right the
        # FPS overlay, and this label is long enough to collide with them.
        # Standalone occupancy verdict, shown in every state -- so an operator
        # tuning `threshold` can watch the confidence directly, and can see at
        # a glance whether the classifier thinks the enclosure is empty.
        if self._occupancy_detector is not None:
            occ = self._occupancy_detector
            label = (f"{label}   RAT {occ.last_score:.2f}" if occ.present
                     else f"{label}   empty {occ.last_score:.2f}")

        height = m.array.shape[0]
        circle_y = height - 20
        text_y = height - 12
        cv2.circle(m.array, (18, circle_y), 6, color, -1, cv2.LINE_AA)
        cv2.putText(
            m.array, f"{label}  {self._motion_last_score:.3f}", (30, text_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA,
        )

    @command()
    def reset_motion_trigger(self) -> dict:
        """Manually clear a waiting/triggered state back to idle, without
        waiting out inactivity_min_duration_s (300s default) -- lets an
        operator reset quickly while testing/tuning against the livestream
        instead of waiting the same duration a real clip close-out would
        take. Finalises any currently-open clip first, same as the natural
        active -> idle transition would, so this is safe to call even while
        genuinely armed and recording -- it just closes the current clip
        out early rather than discarding anything."""
        if self._clip_open:
            self._close_clip()
        self._motion_state = "idle"
        self._motion_since_ns = None
        self._motion_last_above = None
        if self._occupancy_detector is not None:
            self._occupancy_detector.reset()
        self.logger.info("Motion/occupancy trigger manually reset to idle")
        return {"result": "success"}

    """Motion-gated recording.

    Overrides CameraBase's continuous SplittableOutput recording. Arming
    (start_recording/stop_recording, i.e. this module's normal Session)
    works exactly as for every other camera type -- what changes is that no
    clip file is written until _process_main_frame's hysteresis state
    machine above actually transitions into "active"; each active streak
    becomes its own clip, tagged onto the same armed session via the normal
    per-file export API (add_session_file/stage_file_for_export), not a
    separate session of its own.
    """

    def _start_new_recording(self) -> bool:
        """Called once when arming (Recording._create_initial_recording_segment
        -> facade.start_new_recording()). Starts the camera capturing and a
        CircularOutput pre-roll buffer -- the encoder runs continuously for
        the whole armed window, but no clip file is opened until the first
        motion trigger."""
        if self.picam2 is None:
            reason = self._hardware_fault_reason()
            self.logger.error(f"Cannot arm: {reason}")
            self.facade.send_status({"type": "recording_start_failed", "error": reason})
            return False

        # Same guard as CameraBase._start_new_recording: never arm with the
        # crop editor's full-view ScalerCrop still applied.
        if getattr(self, "_crop_editing", False):
            self._end_crop_editing(restore=True)

        if not self.picam2.started:
            self.picam2.start()
            time.sleep(0.1)

        fps = self.fps or self.config.get("camera.fps", 25)
        buffersize = max(1, int(self._motion_pre_roll_secs * fps))
        self._circular_output = CircularOutput(buffersize=buffersize)
        self.main_encoder.output = self._circular_output
        self.picam2.start_encoder(self.main_encoder, name="main")

        self.recording_start_time = time.time()
        self._clip_open = False
        self._clip_counter = 0
        self._open_diagnostic_csv()
        # Already triggered before arming (animal mid-activity at Start): the
        # -> active transition is one-shot and fired while unarmed, so open
        # the clip now rather than after inactivity_min_duration_s + retrigger.
        if self._motion_state == "active":
            self._open_clip()
        return True

    def _start_next_recording_segment(self) -> bool:
        """No-op. CameraBase's shared time-based segment monitor
        (Recording._monitor_recording_length) still runs while armed, but
        clip rotation here is motion-driven (_open_clip/_close_clip below),
        not time-driven, so its rotation callback has nothing to do.

        Known v1 limitation: an unusually long single continuous "active"
        streak isn't capped/segmented -- acceptable given typical activity
        bouts are short; revisit if it turns out to matter."""
        return True

    def _stop_recording(self) -> bool:
        """Disarm. Finalises any currently-open clip, then stops the
        encoder entirely."""
        try:
            self.logger.info("Attempting to stop habitat_camera recording")
            if self._clip_open:
                self._close_clip()
            self._stop_main_encoder()
            self._circular_output = None
            self._close_diagnostic_csv()
            return True
        except Exception as e:
            self.logger.error(f"Error stopping habitat_camera recording: {e}")
            return False

    def _open_diagnostic_csv(self) -> None:
        """Per-frame score/state log for the whole armed session, clip open
        or not; kept separate from the per-clip _timestamps.csv, which must
        stay 1:1 with the clip's video frames. Plain buffered writes (no
        flush thread): losing the tail on a crash is acceptable here."""
        path = f"{self.facade.get_filename_prefix()}_motion_diagnostic.csv"
        self._diag_csv_file = open(path, "w", newline="", buffering=1 << 16)
        self._diag_csv_writer = csv.writer(self._diag_csv_file)
        self._diag_csv_writer.writerow(
            ["timestamp_utc", "motion_score", "motion_state", "clip_open",
             "ae_stable", "occupancy_score", "occupancy_present"]
        )
        self._diag_csv_path = path
        self.facade.add_session_file(path)

    def _close_diagnostic_csv(self) -> None:
        if self._diag_csv_file is None:
            return
        self._diag_csv_file.flush()
        self._diag_csv_file.close()
        self._diag_csv_file = None
        self._diag_csv_writer = None
        if self._diag_csv_path:
            self.facade.stage_file_for_export(self._diag_csv_path)
            self._diag_csv_path = None

    def _get_clip_filename(self) -> str:
        """Unique-per-clip filename. _get_video_filename() can't be used:
        segment_id/start_time never advance here (segment rotation is a
        no-op), so this uses a per-arm counter plus the current time."""
        self._clip_counter += 1
        strtime = self.facade.get_utc_time(time.time())
        ext = self.config.get('recording.recording_filetype', 'ts')
        return f"{self.facade.get_filename_prefix()}_(clip{self._clip_counter}_{strtime}).{ext}"

    def _open_clip(self) -> None:
        """Flush the pre-roll buffer to a new clip file and open its
        timestamp CSV. Called on the waiting/idle -> active transition."""
        if self._circular_output is None or self._clip_open:
            return
        ts_path = self._get_clip_filename()
        self._clip_h264_path = os.path.splitext(ts_path)[0] + ".h264"
        self.current_video_segment = ts_path
        self.facade.add_session_file(ts_path)
        self._circular_output.fileoutput = self._clip_h264_path
        self._circular_output.start()
        self._open_timestamp_csv(ts_path)
        self._clip_open = True
        self.logger.info(f"Motion clip opened: {ts_path}")

    def _close_clip(self) -> None:
        """Stop writing the current clip and finish it on a background
        thread. Callers are the capture pre_callback thread and the command
        thread; neither may block on the CSV join (up to 5 s) or ffmpeg.

        No lock against a new _open_clip() racing the cleanup: re-entering
        "active" takes at least inactivity_min_duration_s, far longer than
        the cleanup.
        """
        if not self._clip_open or self._circular_output is None:
            return
        self._circular_output.stop()
        self._circular_output.fileoutput = None
        self._clip_open = False

        h264_path = self._clip_h264_path
        ts_path = self.current_video_segment
        threading.Thread(
            target=self._finish_clip, args=(h264_path, ts_path),
            daemon=True, name="habitat-clip-finish",
        ).start()

    def _finish_clip(self, h264_path: str, ts_path: str) -> None:
        """Background: close the timestamp CSV, remux .h264 -> .ts, stage
        for export. See _close_clip()'s docstring for why this runs off
        the calling thread."""
        self._close_timestamp_csv()
        final_path = self._remux_clip_to_ts(h264_path, ts_path)
        self.facade.stage_file_for_export(final_path)
        self.logger.info(f"Motion clip closed and staged: {final_path}")

    def _remux_clip_to_ts(self, h264_path: str, ts_path: str) -> str:
        """Remux CircularOutput's raw .h264 to .ts (the export format the
        tooling expects) with -c copy, no re-encode. Returns ts_path, or
        h264_path if the remux failed (stage the raw stream rather than lose
        the clip); _recover_orphaned_clips handles a remux that never ran."""
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", h264_path, "-c", "copy", "-f", "mpegts", ts_path],
                check=True, capture_output=True,
            )
            os.remove(h264_path)
            return ts_path
        except Exception as e:
            self.logger.error(f"Failed to remux motion clip {h264_path} to {ts_path}: {e}")
            return h264_path

    def _recover_orphaned_clips(self) -> None:
        """Remux and stage (with any timestamp CSV) every .h264 left by a
        clip that never reached _close_clip() (crash / power loss). Runs
        once at startup, before a new .h264 can exist."""
        folder = self.recording.recording_folder
        try:
            orphans = [f for f in os.listdir(folder) if f.endswith(".h264")]
        except FileNotFoundError:
            return
        for name in orphans:
            h264_path = os.path.join(folder, name)
            ts_path = h264_path[:-len(".h264")] + ".ts"
            self.logger.warning(f"Recovering orphaned motion clip: {h264_path}")
            final_path = self._remux_clip_to_ts(h264_path, ts_path)
            self.facade.stage_file_for_export(final_path)

            csv_path = h264_path[:-len(".h264")] + "_timestamps.csv"
            if os.path.exists(csv_path):
                self.facade.stage_file_for_export(csv_path)

    def stop(self) -> bool:
        """Stop the occupancy side-thread before CameraBase tears the camera
        down."""
        self._occupancy_stop.set()
        if self._occupancy_thread is not None:
            self._occupancy_thread.join(timeout=self._occupancy_interval_s + 1)
        return super().stop()


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
