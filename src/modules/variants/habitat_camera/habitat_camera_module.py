#!/usr/bin/env python3
"""
SAVIOUR System - Habitat Camera Module Class

Built on CameraBase (src/modules/camera_base.py), which provides Picamera2
lifecycle, MJPEG streaming, segmented recording, and the timestamp-CSV
sidecar. This file adds a per-frame motion/activity score (see CLAUDE.md's
"habitat_camera" feature idea) and gates recording on it: no clip file is
written until the score crosses activity_threshold for activity_min_duration_s,
using a CircularOutput pre-roll buffer (habitat_motion.pre_roll_secs) so the
clip includes footage from just before the trigger, not just after it.

"Armed" here just means "a normal recording session is active"
(self.facade.get_recording_status(), set by the existing start_recording/
stop_recording command path -- not the bare self.is_recording attribute,
which is dead for camera modules) -- no new RPC needed. Arming still creates
a normal Session on the controller exactly as for every other camera type;
individual motion-triggered clips are just files added to that same ongoing
session via the existing per-file export API (facade.add_session_file() /
facade.stage_file_for_export()), not separate sessions of their own.

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

_STATE_COLOR_BGR = {
    "idle":    (128, 128, 128),
    "waiting": (0, 191, 255),
    "active":  (0, 0, 255),
}
# _motion_state is computed identically regardless of arm status -- the
# livestream preview must be fully representative of the real recording
# trigger, not a separate simplified check, so an operator can tune
# activity_threshold/activity_min_duration_s against the exact same
# sustained-duration logic that actually gates a clip, without needing a
# real recording running. "idle" = below threshold; "waiting" = above
# threshold, accumulating toward the sustained-duration trigger; "active" =
# triggered. Only the "active" *label* depends on arm status (see
# _process_lores_frame) -- if not actually armed, a triggered state is
# real (the same math a real recording would use) but doesn't write a clip.


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
        self._recover_orphaned_clips()

    def _configure_module_extra(self, updated_keys) -> None:
        # Only rebuild the detector (and reset its hysteresis timer) when a
        # habitat_motion.* key actually changed, or on a full reconfigure
        # (updated_keys is None, e.g. reset_config). configure_module_special()
        # runs this hook on EVERY config push, including an unrelated bare
        # camera.sync_mode change from FrameSync reconcile -- which fires on
        # essentially every reconnect (see CLAUDE.md's reconcile_framesync
        # note). Rebuilding unconditionally wiped the in-progress armed ->
        # recording streak before it could ever reach activity_min_duration_s
        # -- confirmed live: state stuck at ARMED even with a real, varying,
        # above-threshold score.
        if updated_keys is None or any(k.startswith("habitat_motion.") for k in updated_keys):
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

    def _update_ae_stability(self, timing) -> bool:
        """Return whether auto-exposure/gain has been steady for at least
        ae_settle_s (0.75s default). MOG2 (and, to a lesser extent, frame_diff)
        treat a sudden global brightness step -- which is exactly what a
        continuous-AE camera produces every time it nudges exposure_time_us or
        analogue_gain in response to changing ambient light -- as a frame full
        of "foreground" pixels, indistinguishable from real motion until the
        background model catches up.

        Confirmed against a real deployment (2026-08-24, Motion_Tracking_Test):
        every false-triggered clip on two of three days showed motion_score
        decaying smoothly in lockstep with analogue_gain/exposure_time_us
        still changing frame-to-frame, then collapsing to ~0 the instant AE
        stopped adjusting -- all within a recurring several-hour daytime
        window (consistent both days, absent overnight), with no visible
        subject in the footage at any trigger. Gating the trigger on AE
        stability (rather than e.g. just raising activity_threshold) targets
        that mechanism directly without dulling sensitivity to genuine motion,
        which the AE gate doesn't touch as long as the light stays steady.

        Compares against the previous frame's exact metadata values (not a
        tolerance/epsilon) -- confirmed live that Picamera2's AE control
        reports the identical float back, frame after frame, once it has
        actually converged, so any change at all means AE is still moving."""
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
        above = score >= self._motion_activity_threshold and ae_stable

        # Runs identically regardless of arm status -- the livestream preview
        # must be fully representative of the real recording trigger, not a
        # separate simplified check (this used to reset to "idle" every frame
        # while not recording, which meant the preview never actually
        # exercised the sustained-duration logic a real trigger needs).
        # self.facade.get_recording_status() (below) only gates whether a
        # genuine "active" transition actually opens/closes a clip file --
        # not whether the state machine itself runs.
        if self._motion_last_above is None or above != self._motion_last_above:
            self._motion_since_ns = timing.timestamp_ns
            self._motion_last_above = above

        elapsed_s = (
            (timing.timestamp_ns - self._motion_since_ns) / 1e9
            if self._motion_since_ns is not None else 0.0
        )

        if self._motion_state != "active":
            triggered = above and elapsed_s >= self._motion_activity_min_duration_s
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
        if self._diag_csv_writer is not None:
            self._diag_csv_writer.writerow(
                [timing.timestamp_utc, f"{score:.4f}", self._motion_state,
                 self._clip_open, self._motion_ae_stable]
            )

        return {
            "motion_score": f"{score:.4f}",
            "motion_state": self._motion_state,
        }

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        color = _STATE_COLOR_BGR.get(self._motion_state, _STATE_COLOR_BGR["idle"])
        if self._motion_state == "idle":
            # "waiting" can only be reached via a gated-True `above` (see
            # _process_main_frame), so AE is necessarily stable by then --
            # this qualifier only ever applies to "idle": the raw score alone
            # would already be over threshold, but _update_ae_stability is
            # holding the gated trigger off, so an operator watching a
            # visibly-high score with no state change can tell it's the AE
            # gate rather than a threshold/duration problem, without needing
            # to read the diagnostic CSV.
            score_over = self._motion_last_score >= self._motion_activity_threshold
            if not self._motion_ae_stable and score_over:
                label = "IDLE (AE settling)"
            else:
                label = "IDLE"
        elif self._motion_state == "waiting":
            label = "ABOVE THRESHOLD"
        else:
            # Triggered by the same math a real recording would use -- label
            # distinguishes an actual clip from a not-armed test trigger, but
            # the countdown below applies either way, same as the state
            # machine itself (see _process_main_frame's docstring).
            label = "RECORDING (motion)" if self._clip_open else "TRIGGERED (not armed)"
            # self._motion_last_above is False exactly while the
            # inactivity-duration countdown toward closing is running --
            # True (or None, before any frame's been scored) means motion is
            # still ongoing right now, so there's nothing counting down.
            if self._motion_last_above is False and self._motion_since_ns is not None:
                elapsed_s = (timing.timestamp_ns - self._motion_since_ns) / 1e9
                remaining_s = max(0.0, self._motion_inactivity_min_duration_s - elapsed_s)
                mins, secs = divmod(int(remaining_s), 60)
                label = f"{label} - closing in {mins:02d}:{secs:02d}"
        # Bottom-left corner: top-center is the timestamp (up to 72% of frame
        # width, see _apply_timestamp), top-right is the FPS overlay (see
        # _apply_framerate's own comment on the same collision) -- with the
        # countdown appended above, this label is long enough to run straight
        # into the timestamp if left at the top. Smaller and thinner than
        # before too, matching the FPS overlay's existing precedent for a
        # secondary diagnostic overlay.
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
        self.logger.info("Motion trigger manually reset to idle")
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
        # If motion was already sustained above threshold before this arm
        # (e.g. an animal was mid-activity when the operator pressed Start),
        # _process_main_frame's idle/waiting -> active transition already
        # fired while unarmed, correctly skipping _open_clip() then -- but
        # since that's a one-shot transition, not a per-frame check, nothing
        # would otherwise open a clip until the animal goes fully quiet for
        # inactivity_min_duration_s (300s default) and re-triggers from
        # scratch. Confirmed live: the operator sees "TRIGGERED (not armed)"
        # on the livestream despite genuinely being armed, and no footage of
        # the ongoing activity gets captured. Catch up immediately instead.
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
            self.picam2.stop_encoder(self.main_encoder)
            self._circular_output = None
            self._close_diagnostic_csv()
            return True
        except Exception as e:
            self.logger.error(f"Error stopping habitat_camera recording: {e}")
            return False

    def _open_diagnostic_csv(self) -> None:
        """Continuous per-frame motion_score/motion_state/clip_open log for
        the whole armed session, regardless of whether a clip is open --
        separate from the per-clip _timestamps.csv (which is meant to stay
        aligned 1:1 with an actual clip's video frames; mixing in idle-period
        rows with no corresponding footage would break that). Not buffered
        via a background thread like the main timestamp CSV -- plain
        buffered file writes are cheap enough for one row per frame, and
        losing whatever's still in the OS write buffer if the process
        crashes mid-session is an acceptable loss for a diagnostic file."""
        path = f"{self.facade.get_filename_prefix()}_motion_diagnostic.csv"
        self._diag_csv_file = open(path, "w", newline="", buffering=1 << 16)
        self._diag_csv_writer = csv.writer(self._diag_csv_file)
        self._diag_csv_writer.writerow(
            ["timestamp_utc", "motion_score", "motion_state", "clip_open", "ae_stable"]
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
        """Unique-per-clip filename, session-scoped like every other camera
        type's _get_video_filename() -- but that helper relies on
        segment_id/segment_start_time, which _start_next_recording_segment()
        being a no-op means never advance past their arm-time values. Uses
        an incrementing per-arm counter plus the real current time instead."""
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
        """Stop writing the current clip, then hand the slow part off to a
        background thread. Called on the active -> waiting transition
        (from _process_main_frame, which runs on Picamera2's own
        pre_callback thread) and from _stop_recording() (the disarm
        command, on the ZMQ command thread) if disarmed mid-clip -- neither
        should block on file I/O or the ffmpeg subprocess. camera_base.py's
        own timestamp-CSV writer already offloads its flush work for
        exactly this reason ("file I/O never stalls capture" -- see
        _csv_flush_worker); _close_timestamp_csv()'s up-to-5s thread-join
        and the remux below both belong on a background thread the same way.

        No extra locking against a fresh _open_clip() racing this cleanup:
        the hysteresis state machine can't re-enter "active" (and therefore
        can't call _open_clip() again) until it's spent at least
        inactivity_min_duration_s (300s default) back in "waiting" first --
        several orders of magnitude longer than this cleanup should ever take.
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
        """Remux the raw .h264 CircularOutput produces into .ts, matching
        every other camera type's export format and the tooling that
        expects it (analyse_framesync.py, video_compose.py). -c copy is a
        repackage, not a re-encode -- no pixels are touched, so this is
        I/O-bound and fast regardless of clip length. Raw .h264 is not
        corrupted by an interruption before this step ever runs (no
        trailing-index format to lose, same reason .ts itself survives
        interruption where .mp4 doesn't) -- see _recover_orphaned_clips()
        for the case where this step never got the chance to run at all.
        Returns the path that actually got produced: ts_path on success,
        or h264_path itself if the remux failed (stage the raw stream
        rather than silently lose the clip)."""
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
        """Sweep for .h264 files left over from a clip that never reached
        _close_clip() -- a crash or power loss mid-recording. Remuxes each
        to .ts and stages it (plus its timestamp CSV sidecar, if present)
        for export, so an interrupted clip's footage isn't silently
        stranded on local disk forever. Run once at startup, before
        anything else could create a new .h264 to collide with one found
        here."""
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
