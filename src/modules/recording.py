#!/usr/bin/env python3
"""
Module Recording Manager

This class is used to manage recording methods for the module - starting and stopping recordings, batch exporting for 24/7 recordings, updating files for export. 

Sequence
- controller sends call to start_recording()
- name and duration (if any) are set as params
- the initial recording segment is created
- threads are started
-- self.health_recording_thread records health metadata to a csv for the current segment
-- self._recording_duration_thread is used to automatically stop recording after preset duration 
-- self.monitor_recording_segments_thread is used to stop and start new recording segments when condition is met

Author: Andrew SG
Created: 12/01/2026
"""

import csv
import logging
import os
import shutil
import subprocess
import threading
import time

from src.modules.config import Config


def _journalctl(args: list, timeout: int = 20) -> str:
    """Run a journalctl query, returning stdout or a short error string.
    Never raises."""
    try:
        r = subprocess.run(
            ["journalctl", "--no-pager", *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if r.returncode == 0:
            return r.stdout or "(no output)"
        return f"journalctl {' '.join(args)} rc={r.returncode}: {r.stderr.strip()}"
    except Exception as e:
        return f"journalctl {' '.join(args)} failed: {e}"


class Recording:
    def __init__(self, config: Config):
        # Basic Setup
        self.logger = logging.getLogger(__name__)
        self.config = config

        # Parameters from config
        self.recording_folder = f'{self.config.get("recording.recording_folder", "/var/lib/saviour/recordings")}/pending' # Location that files will be recorded to
        self.logger.info(f"Recording folder: {self.recording_folder}")
        os.makedirs(self.recording_folder, exist_ok=True)

        # State Flags
        self.is_recording = False

        # Main Recording Thread
        self._recording_duration_thread = None # A thread to automatically stop recording if a duration is given # TODO: Rename this something to do with auto stop recording
        self.recording_start_time = None # When a recording session was started
        self.recording_intended_start_at = None # The start_at timestamp requested by the controller (None = start immediately)

        # Health metadata thread
        self.health_recording_thread = None # A thread to record health on
        self.health_stop_event = threading.Event() # An event to signal health recording thread to stop
        self.last_health_segment = None
        self.current_health_segment = None

        # Recording self-monitor thread — distinct from health metadata above
        # (which just logs whatever get_health() returns to a CSV): this asks
        # the module-specific implementation whether it's actually still
        # capturing data, and reports to the controller if not.
        self.recording_health_thread = None
        self.recording_health_stop_flag = threading.Event()

        # Measured recording data rate — a reality check against the
        # config-derived estimate (src/shared/data_rate.py). Sampled from the
        # recordings tree by _sample_recording_bytes() on the health-monitor
        # tick; None while not recording / before the first two samples.
        self._measured_rec_bytes_per_s: float | None = None
        self._rec_byte_hwm: dict[str, int] = {}
        self._rec_bytes_baseline = 0
        self._rec_sample_start_ts: float | None = None

        # Tracking files for export
        self.current_filename_prefix = None
        # Set by _scheduled_start to avoid when_recording_starts() running twice
        self._recording_start_prepped = False

        # Segment based recording
        self.monitor_recording_segments_stop_flag = threading.Event()
        self.monitor_recording_segments_thread = None
        self.segment_id = 0
        self.segment_start_time = None
        self.segment_files = []


    """Start / Stop Recording"""
    def start_recording(self, session_name: str = None, duration: str = None,
                        start_at: float | None = None) -> dict:
        """Accept a start_recording command from the controller.

        If start_at is provided (a UTC epoch float), recording begins at that
        timestamp rather than immediately.  All modules share a PTP-disciplined
        clock so they all fire at the same wall-clock moment.
        """
        self.logger.info(
            f"start_recording called — session_name={session_name}, "
            f"duration={duration}, start_at={start_at}"
        )

        if self.is_recording:
            self.logger.info("Already recording")
            self.facade.send_status({"type": "recording_start_failed", "error": "Already recording"})
            return {"result": "error", "error": "Already recording"}

        self.recording_intended_start_at = start_at

        if start_at is not None:
            delay = start_at - time.time()
            if delay <= 0:
                self.logger.warning(
                    f"start_at is {-delay:.3f}s in the past (PTP drift?) — starting immediately"
                )
                return self._begin_recording(session_name, duration)
            self.logger.info(f"Recording scheduled to start in {delay:.3f}s")
            threading.Thread(
                target=self._scheduled_start,
                args=(session_name, duration, start_at),
                daemon=True,
                name="scheduled-recording-start",
            ).start()
            return {"result": "success"}

        return self._begin_recording(session_name, duration)


    def _scheduled_start(self, session_name: str, duration: str, start_at: float) -> None:
        """Pre-prepare, spin-wait to start_at, then begin recording.

        Three-stage approach to minimise inter-camera start jitter:
          1. Elevate this thread to SCHED_FIFO so the OS doesn't pre-empt it
             during the spin-wait (requires CAP_SYS_NICE / root).
          2. Pre-create the video container and CSV via the module hook so the
             only work left at start_at is start_encoder().
          3. Sleep until 10 ms before start_at then busy-spin so the wakeup
             jitter floor (~100–300 µs for time.sleep) is eliminated.
        """
        # 1. Real-time scheduling
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(80))
            self.logger.info("Elevated to SCHED_FIFO priority 80 for scheduled start")
        except (PermissionError, AttributeError, OSError) as e:
            self.logger.debug(f"SCHED_FIFO unavailable ({e}); using normal scheduling")

        # This whole block runs in its own daemon thread, spawned by
        # start_recording() *after* it already returned {"result": "success"}
        # to the controller (command.py's own outer try/except has long since
        # returned by the time this runs, so it can't catch anything here) --
        # without this try/except, a failure anywhere below (e.g.
        # camera_base.py's _start_new_recording() hitting a busy encoder) had
        # no way to ever reach the controller at all, propagating only to
        # Python's default thread excepthook (stderr).
        try:
            # 2. Pre-compute session state so _get_video_filename() works
            #    correctly inside the module hook (current_filename_prefix
            #    would be None otherwise)
            self._pre_setup_session(session_name, start_at)

            # 3. Run when_recording_starts() BEFORE sleeping so that Samba I/O
            #    (config export, directory creation) is off the critical path.
            #    _begin_recording checks the flag and skips its own call.
            self.facade.when_recording_starts()
            self._recording_start_prepped = True

            # 4. Pre-create file handles before sleeping
            try:
                self.facade.pre_create_first_segment(start_at)
            except Exception as e:
                self.logger.warning(
                    f"pre_create_first_segment failed ({e}); "
                    "will open files at start time"
                )

            # 5. Sleep then spin
            delay = start_at - time.time()
            if delay > 0.010:
                time.sleep(delay - 0.010)
            while time.time() < start_at:
                pass

            self._begin_recording(session_name, duration)
        except Exception as e:
            self.logger.error(f"Scheduled recording start failed: {e}")
            self.facade.send_status({"type": "recording_start_failed", "error": str(e)})


    def _pre_setup_session(self, session_name: str, start_at: float) -> None:
        """Set the string fields that _get_video_filename() needs before
        _begin_recording has run.

        Only sets in-memory string state — deliberately does NOT call
        when_recording_starts() (which has I/O side effects and already runs
        inside _begin_recording). _begin_recording will overwrite these with
        the same values, so calling this early is safe and idempotent.
        """
        self.current_session_name = self._format_session_name(session_name)
        module_name = self.facade.get_module_name()
        short_mac   = self.facade.get_short_mac()
        self.recording_session_id = (
            module_name if short_mac in module_name
            else f"{module_name}_{short_mac}"
        )
        if session_name:
            self.current_filename_prefix = (
                f"{self.recording_folder}/"
                f"{self.current_session_name}_{self.recording_session_id}"
            )
        else:
            self.current_filename_prefix = (
                f"{self.recording_folder}/{self.recording_session_id}"
            )
        os.makedirs(self.recording_folder, exist_ok=True)
        # Seed segment state so _get_video_filename() produces the correct name
        self.segment_id = 0
        self.segment_start_time = start_at


    def _begin_recording(self, session_name: str, duration: str) -> dict:
        """Immediately start recording. Called directly or from _scheduled_start."""
        # Store experiment folder information for export
        self.current_session_name = self._format_session_name(session_name)

        # For scheduled starts, when_recording_starts() was already called in
        # _scheduled_start (before the sleep, off the critical path).
        if not self._recording_start_prepped:
            self.facade.when_recording_starts()
        self._recording_start_prepped = False

        # Set up recording - filename and folder
        module_name = self.facade.get_module_name()
        short_mac = self.facade.get_short_mac()
        # Append MAC only when the name is custom (default module IDs already embed the MAC)
        self.recording_session_id = module_name if short_mac in module_name else f"{module_name}_{short_mac}"

        if session_name:
            self.current_filename_prefix = f"{self.recording_folder}/{self.current_session_name}_{self.recording_session_id}"
        else:
            self.current_filename_prefix = f"{self.recording_folder}/{self.recording_session_id}"

        self.logger.info(f"Filenames will be prefixed {self.current_filename_prefix}")
        os.makedirs(self.recording_folder, exist_ok=True)

        self.recording_start_time = time.time()
        self._reset_byte_sampler()

        self.logger.info(f"Duration received as: {duration} with type {type(duration)}")
        if duration is not None:
            if duration > 0:
                self._recording_duration_thread = threading.Thread(
                    target=self._auto_stop_recording, args=(int(duration),)
                )

        self._create_initial_recording_segment()
        self._start_recording_segment_monitoring()
        self._start_new_health_recording()
        self._start_recording_health_monitoring()

        if self._recording_duration_thread:
            self._recording_duration_thread.start()

        self.is_recording = True

        self.logger.info("Sending recording started message to controller")
        self.facade.send_status({
            "type": "recording_started",
            "status": "success",
            "recording": True,
        })

        return {"result": "success"}


    def stop_recording(self) -> bool:
        """Stop recording. Returns True if stopped, False otherwise."""
        self.logger.info(f"Stop recording called. to_export contains: {self.facade.get_staged_files()}")
        try:
            # Check if recording
            if not self.is_recording:
                self.logger.info("Already stopped recording")
                self.facade.send_status({
                    "type": "recording_stop_failed",
                    "error": "Not recording"
                })
                return False

            # Stop monitoring recording segment length
            self._stop_recording_segment_monitoring()
            self._stop_recording_health_monitoring()

            # Stop recording in general
            if not self.facade.stop_recording(): # Module specific implementation of stop_recording
                self.logger.warning("Something went wrong stopping recording.")
                self.facade.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                })
                return {"result": "error", "error": "Failed to stop recording"}

            # Stop recording health metadata
            self._stop_recording_health_metadata()
            self.facade.stage_file_for_export(self.current_health_segment)
            self.logger.info("Made it past stop_recording_health_metadata call")

            # Snapshot this session's journal alongside the recording, so a
            # reboot/hang during a weeks-long unattended run is still
            # diagnosable once the volatile journal has rotated away and
            # nobody SSH'd in at the time.
            self._export_session_journal()

            self.facade.send_status({
                "type": "recording_stopped",
                "status": "success",
                "recording": False,
            })

            self.is_recording = False
            self._measured_rec_bytes_per_s = None
            self.logger.info("Made it past stop_recording call")

            self.logger.info(f"Config says {self.config.get('export.auto_export')}")
            if self.config.get("export.auto_export") == True:
                export_path = f"{self.current_session_name}/{self.facade.get_utc_date(time.time())}/{self.facade.get_module_name()}"
                self.facade.signal_export_ready(export_path)

            return {"result": "Success"}

        except Exception as e:
            self.logger.error(f"Error in stop_recording: {e}")
            return {"result": "failure", "message": f"Error in stop_recording: {e}"}


    def _export_session_journal(self) -> None:
        """Write this session's journal (saviour.service + kernel, since the
        recording started) into to_export/ so it rides the normal export to
        the share alongside the footage. Best-effort: never raise out of a
        stop. Toggle with recording.export_session_journal (default on)."""
        if not self.config.get("recording.export_session_journal", True):
            return
        try:
            since = self.recording_start_time or (time.time() - 86400)
            since_arg = f"@{int(since)}"
            sid = getattr(self, "recording_session_id", "module")
            prefix = getattr(self, "current_filename_prefix", None) \
                or f"{self.recording_folder}/{sid}"
            strtime = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            path = f"{prefix}_journal_({strtime}).txt"
            body = "\n".join([
                f"=== journalctl --since {since_arg} -u saviour.service ===",
                _journalctl(["--since", since_arg, "-u", "saviour.service",
                             "-n", "20000", "--output=short-precise"]),
                "",
                f"=== journalctl --since {since_arg} -k ===",
                _journalctl(["--since", since_arg, "-k", "-n", "20000"]),
                "",
            ])
            with open(path, "w") as f:
                f.write(body)
            self.facade.stage_file_for_export(path)
            self.logger.info(
                f"Staged session journal {os.path.basename(path)} for export")
        except Exception as e:
            self.logger.warning(f"Could not export session journal: {e}")


    def _format_session_name(self, session_name:str ) -> str:
        """
        Take an experiment name received from the frontend and put it in a file-safe format.
        """
        if not session_name:
            return ""
        formatted_session_name = "".join(c for c in session_name if c.isalnum() or c in (' ', '-', '_')).rstrip() # Keep alphanumeric characters and spaces, dashes, underscores
        formatted_session_name = formatted_session_name.replace(' ', '_') # Replace all spaces with underscores
        return formatted_session_name


    """Creating Recording Segments"""
    def _create_new_recording_segment(self):
        """Create new recording segment"""
        # Increment segment
        self.segment_id += 1
        self.segment_start_time = time.time()

        # Start new health metadata segment
        self._start_next_health_metadata_segment()

        # Start new actual recording segment
        self.facade.start_next_recording_segment() # Callback to tell specific module to start a new recording segment
        export_path = f"{self.current_session_name}/{self.facade.get_utc_date(time.time())}/{self.facade.get_module_name()}"
        self.facade.signal_export_ready(export_path) # Signal to controller that files are ready to export


    def _create_initial_recording_segment(self) -> None:
        self.logger.info("Creating initial recording segment")
        self.segment_id = 0
        self.segment_start_time = time.time()
        self.logger.info(f"Segment {self.segment_id} started at {self.segment_start_time}")

        # Start video
        self.facade.start_new_recording()


    """Segment Length Monitoring"""
    def _monitor_recording_length(self):
        """
        Runs in a thread and monitors length of current recording.
        If it exceeds segment length limit, stops and starts a new recording.
        """
        segment_length = self.config.get("recording.segment_length_mins", 30) * 60 # Get segment length in mins and convert to seconds
        self.logger.info(f"Segment started at {self.segment_start_time},  segment length {segment_length}")

        recording_folder = self.config.get("recording._recording_folder", "/var/lib/saviour/recordings")
        to_export_folder = f"{recording_folder}/to_export"
        min_free_pct     = self.config.get("recording.local_min_free_pct", 10)
        # Re-signal export_ready every 5 minutes while files are waiting,
        # so a controller restart or dropped ZMQ message doesn't silently block exports.
        export_signal_interval = 300
        last_export_signal     = 0.0

        while not self.monitor_recording_segments_stop_flag.is_set():
            now = time.time()

            if (now - self.segment_start_time > segment_length):
                # Check local disk space before starting a new segment.
                try:
                    usage = shutil.disk_usage(recording_folder)
                    free_pct = usage.free / usage.total * 100
                    free_mb  = usage.free / 1_048_576
                    if free_pct < min_free_pct:
                        self.logger.error(
                            f"Local disk critically low ({free_pct:.1f}% free, {free_mb:.0f} MB) — "
                            f"stopping recording to protect filesystem. Exports must clear space before recording can resume."
                        )
                        self.stop_recording()
                        return
                except Exception as e:
                    self.logger.warning(f"Could not check disk space before new segment: {e}")

                self._create_new_recording_segment()
                last_export_signal = now
                self.logger.info(f"Segment duration elapsed - new segment {self.segment_id} started at {self.segment_start_time}")

            # Periodically re-signal the controller if files are still waiting.
            elif (now - last_export_signal > export_signal_interval
                    and self.current_session_name):
                try:
                    waiting = [
                        f for f in os.listdir(to_export_folder)
                        if not f.startswith("PENDING_")
                    ]
                    if waiting:
                        export_path = (
                            f"{self.current_session_name}"
                            f"/{self.facade.get_utc_date(now)}"
                            f"/{self.facade.get_module_name()}"
                        )
                        self.logger.info(
                            f"{len(waiting)} file(s) still in to_export — re-signalling export_ready"
                        )
                        self.facade.signal_export_ready(export_path)
                        last_export_signal = now
                except Exception as e:
                    self.logger.warning(f"Export re-signal check failed: {e}")

            time.sleep(0.1) # Avoid busy waiting


    def _start_recording_segment_monitoring(self):
        self.monitor_recording_segments_stop_flag.clear()
        self.segment_start_time = self.recording_start_time
        self.segment_id = 0
        self.monitor_recording_segments_thread = threading.Thread(target=self._monitor_recording_length, daemon=True)
        self.monitor_recording_segments_thread.start()


    def _stop_recording_segment_monitoring(self):
        self.logger.info("Stopping recording segment monitoring.")
        try:
            self.monitor_recording_segments_stop_flag.set()
            self.monitor_recording_segments_thread.join(timeout=5)
            return True
        except Exception as e:
            self.logger.error(f"Error stopping recording segment monitoring thread: {e}")
            return False


    """Recording self-monitoring"""
    def _monitor_recording_health(self):
        """
        Runs in a thread for the whole recording (not per-segment, unlike
        the health-metadata thread). Periodically asks the module-specific
        implementation whether it's still actually capturing data -- e.g. a
        dead AudioMoth thread or a camera pipeline that's gone silent --
        which _monitor_recording_length never checks: that thread only
        tracks segment-rotation timing and disk space.

        Requires `strikes` consecutive failed checks before reporting, to
        absorb brief timing races rather than false-alarm on them -- e.g.
        the short window during segment rotation where a module's capture
        threads are legitimately down while old ones are joined and new
        ones started.
        """
        interval = self.config.get("recording._health_check_interval_secs", 10)
        strikes_threshold = self.config.get("recording._health_check_strikes", 2)
        strikes = 0
        was_unhealthy = False

        while not self.recording_health_stop_flag.wait(timeout=interval):
            try:
                self._sample_recording_bytes()
            except Exception as e:
                self.logger.debug(f"Recording byte sampler failed: {e}")

            try:
                alive, detail = self.facade.check_recording_alive()
            except Exception as e:
                self.logger.warning(f"Recording health check raised an exception: {e}")
                continue

            if not alive:
                strikes += 1
                self.logger.warning(
                    f"Recording health check failed ({strikes}/{strikes_threshold}): {detail}"
                )
                if strikes >= strikes_threshold and not was_unhealthy:
                    was_unhealthy = True
                    self.facade.send_status({
                        "type": "recording_health_warning",
                        "status": "unhealthy",
                        "message": detail or "Recording health check failed",
                    })
            else:
                if was_unhealthy:
                    self.logger.info("Recording health check recovered")
                    self.facade.send_status({
                        "type": "recording_health_warning",
                        "status": "recovered",
                    })
                strikes = 0
                was_unhealthy = False


    # ----- Measured recording data rate --------------------------------------

    def _reset_byte_sampler(self) -> None:
        self._measured_rec_bytes_per_s = None
        self._rec_byte_hwm = {}
        self._rec_bytes_baseline = 0
        self._rec_sample_start_ts = None

    def _sample_recording_bytes(self) -> None:
        """Walk the recordings tree (pending/ + to_export/) and keep a
        per-file high-water-mark of size, so the cumulative total is
        monotonic even after export removes a file. The average rate since
        the first sample is a reality check against the config estimate.

        First call establishes the baseline (returns None); the rate is
        available from the second call on."""
        if not self.is_recording:
            return
        root = os.path.dirname(self.recording_folder)  # .../recordings
        seen_any = False
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                seen_any = True
                try:
                    sz = os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
                # A file mid-export is briefly renamed PENDING_<name>; strip
                # it so the same file isn't tracked under two keys.
                key = name[8:] if name.startswith("PENDING_") else name
                if sz > self._rec_byte_hwm.get(key, 0):
                    self._rec_byte_hwm[key] = sz
        if not seen_any:
            return

        now = time.time()
        cum = sum(self._rec_byte_hwm.values())
        if self._rec_sample_start_ts is None:
            self._rec_sample_start_ts = now
            self._rec_bytes_baseline = cum
            return
        elapsed = now - self._rec_sample_start_ts
        if elapsed >= 1:
            self._measured_rec_bytes_per_s = max(
                0.0, (cum - self._rec_bytes_baseline) / elapsed)

    def _start_recording_health_monitoring(self):
        self.recording_health_stop_flag.clear()
        self.recording_health_thread = threading.Thread(target=self._monitor_recording_health, daemon=True)
        self.recording_health_thread.start()


    def _stop_recording_health_monitoring(self):
        self.logger.info("Stopping recording health monitoring.")
        try:
            self.recording_health_stop_flag.set()
            if self.recording_health_thread:
                self.recording_health_thread.join(timeout=5)
            return True
        except Exception as e:
            self.logger.error(f"Error stopping recording health monitoring thread: {e}")
            return False


    """Auto stop after duration"""
    def _auto_stop_recording(self, duration: int):
        self.logger.info(f"Starting thread to stop recording after {duration}s")
        while ((time.time() - self.recording_start_time) < duration):
            remaining_time = duration - (time.time() - self.recording_start_time)
            self.logger.info(f"Still recording, {remaining_time}s left")
            time.sleep(0.5) # Wait
        self.logger.info("Stopping recording")
        self.stop_recording()


    """Methods to record health metadata"""
    def _start_health_metadata_thread(self) -> None:
        """Start a thread to record health metadata segment. Will continue until stopped."""
        # Set up thread
        self.health_stop_event.clear() # Clear the stop flag before starting
        self.health_recording_thread = threading.Thread(target=self._record_health_metadata, daemon=True)
        self.health_recording_thread.start()
        if not self.health_recording_thread:
            self.logger.error("Failed to start health recording thread")
        else:
            self.logger.info("Health recording thread started")


    def _start_new_health_recording(self) -> None:
        """Start the initial health recording segment"""
        # Set up filename for initial segment
        csv_filename = self._get_health_segment_filename()
        self.current_health_segment = csv_filename

        # Start the thread
        self._start_health_metadata_thread()


    def _start_next_health_metadata_segment(self) -> None:
        """Start thread to record next health metadata segment."""
        # Stop recording health metadata
        self._stop_recording_health_metadata()

        # Get new filename and stage last file for export
        self.last_health_segment = self.current_health_segment
        self.current_health_segment = self._get_health_segment_filename()
        self.facade.stage_file_for_export(self.last_health_segment)

        # Start new thread
        self._start_health_metadata_thread()


    def _get_health_segment_filename(self) -> str:
        """Return a filename for the current health metadata segment"""
        strtime = self.facade.get_utc_time(self.segment_start_time)
        return f"{self.current_filename_prefix}_health_metadata_({self.segment_id}_{strtime}).csv"


    def _stop_recording_health_metadata(self) -> None:
        """Stop an existing health_recording_thread"""
        self.logger.info("Inside stop_recording_health_metadata call")
        if self.health_recording_thread and self.health_recording_thread.is_alive():
            self.logger.info("Signalling health recording thread to stop")
            self.health_stop_event.set()
            self.health_recording_thread.join(timeout=5)
            if self.health_recording_thread.is_alive():
                self.logger.warning("Health recording thread did not terminate cleanly")
            else:
                self.logger.info("Health recording thread stopped")
        else:
            self.logger.warning("No active health recording thread was found to stop")


    def _record_health_metadata(self):
        """Retrieve health metadata and write to csv tile"""
        interval = self.config.get("health_metadata_recording_interval", 1)
        csv_filename = self.current_health_segment
        fieldnames = list(self.facade.get_health().keys())
        with open(csv_filename, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            while not self.health_stop_event.is_set():
                data = self.facade.get_health()
                writer.writerow(data)
                f.flush() # Ensure each line is written
                # Wait for either a stop signal or timeout
                if self.health_stop_event.wait(timeout=interval): # "Sleeps" for duration of interval if it shouldn't exit
                    break


    """File handling"""
    def get_session_from_filename(self, filename: str) -> str:
        session_name = filename.split("_", maxsplit=1)[0]
        return session_name


    def get_start_time_from_filename(self, filename: str) -> str:
        import re
        # Extract "YYYYMMDD-HHMMSS" from the "(segment_YYYYMMDD-HHMMSS)" pattern.
        # Handles filenames that have suffixes after the datetime (e.g. _timestamps.csv).
        m = re.search(r'\((\d+)_(\d{8}-\d{6})\)', filename)
        if m:
            return m.group(2)
        # Fallback: old behaviour (works for bare .ts files)
        return filename.rsplit("_", maxsplit=1)[-1][0:-1]
