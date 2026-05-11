#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Audiomoth Module Class

This class extends the base Module class to handle audiomoth-specific functionality.

Author: Andrew SG / Domagoj Anticic
Created: 18/08/2025

Parts of code based on https://github.com/Kind-Wyllie-lab/audiomoth_multimicrophone_setup by Domagoj Anticic
"""

import datetime
import os
import sys
import time
import logging
import subprocess
import numpy as np
import threading
import soundfile
import soundcard
import re
import cv2
from flask import Flask, Response

AUDIOMOTH_CMD = "/usr/local/bin/AudioMoth-USB-Microphone"

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command


class AudiomothModule(Module):
    def __init__(self, module_type="microphone"):
        # Call the parent class constructor
        super().__init__(module_type)

        # Initialize audiomoth
        self.mics = [] # An empty list for discovering all connected mics
        self.audiomoths = {} # An empty dict for discovering connected audiomoths
        self._find_audiomoths() # Discover any and all connected microphones

        # Per-segment recording threads and state
        self.audiomoth_threads = []
        self.current_recording_files = {}  # serial -> current filename
        self._segment_stop_event = threading.Event()
        self._recording_stop_event = threading.Event()

        # State flags
        self.is_recording = False
        self.latest_recording = None
        self.recording_start_time = None

        # Monitoring stream state
        self.monitoring_app = Flask(__name__)
        self.monitoring_server = None
        self.monitoring_server_thread = None
        self.should_stop_monitoring_stream = False
        self.monitor_threads = []
        # {serial: {'level_db': float, 'peak_db': float, 'spectrum_db': ndarray, 'freqs': ndarray}}
        self.monitor_data = {}
        self.monitor_data_lock = threading.Lock()
        self.peak_hold_data = {}  # {serial: {'value_db': float, 'time': float}}
        self._register_monitoring_routes()

        # Update config
        self.config.load_module_config("microphone_config.json")

        # Set up audiomoth-specific callbacks for the command handler
        self.audiomoth_commands = {
            'monitor': self.monitor,
            'list_audiomoths': self.list_audiomoths,
            'configure_audiomoth': self.configure_audiomoth,
            'update_gain': self.update_gain,
        }
        self.command.set_commands(self.audiomoth_commands)


    @command()
    def configure_audiomoth(self):
        """Configure all connected AudioMoths from current audiomoth config. Interrupts the stream briefly."""
        sample_rate = int(self.config.get("audiomoth.sample_rate", 192000))
        gain        = int(self.config.get("audiomoth.gain", 2))
        filter_type = self.config.get("audiomoth.filter_type", "none")
        filter_lo   = int(self.config.get("audiomoth.filter_lo_hz", 20000))
        filter_hi   = int(self.config.get("audiomoth.filter_hi_hz", 90000))

        cmd = [AUDIOMOTH_CMD, "config", str(sample_rate), "gain", str(gain)]

        if filter_type == "lpf":
            cmd += ["lpf", str(filter_hi)]
        elif filter_type == "hpf":
            cmd += ["hpf", str(filter_lo)]
        elif filter_type == "bpf":
            cmd += ["bpf", str(filter_lo), str(filter_hi)]

        # CLI flag names: lgr / esm / d48  (not low/energy/no48)
        if self.config.get("audiomoth.low_gain_range", False):
            cmd.append("lgr")
        if self.config.get("audiomoth.energy_saver_mode", False):
            cmd.append("esm")
        if self.config.get("audiomoth.disable_48hz_filter", False):
            cmd.append("d48")

        def _run(c):
            r = subprocess.run(c, capture_output=True, text=True, timeout=10)
            # The binary writes errors to stdout (puts()), not stderr
            msg = (r.stdout.strip() or r.stderr.strip())
            return r.returncode, msg

        try:
            rc, msg = _run(cmd)
            if rc != 0:
                self.logger.error(f"AudioMoth config failed: {msg}")
                return {"result": "Error", "error": msg}
            self.logger.info(f"AudioMoth configured: {' '.join(cmd)}")

            # LED is a separate top-level operation, not a config flag
            led_enabled = self.config.get("audiomoth.led_enabled", True)
            led_rc, led_msg = _run([AUDIOMOTH_CMD, "led", "true" if led_enabled else "false"])
            if led_rc != 0:
                self.logger.warning(f"AudioMoth LED set failed: {led_msg}")

            return {"result": "Success", "output": msg}
        except FileNotFoundError:
            self.logger.error(f"AudioMoth binary not found at {AUDIOMOTH_CMD}")
            return {"result": "Error", "error": "Binary not found — run setup.sh to install"}
        except subprocess.TimeoutExpired:
            return {"result": "Error", "error": "Command timed out"}
        except Exception as e:
            return {"result": "Error", "error": str(e)}


    @command()
    def update_gain(self, gain=None):
        """Update AudioMoth gain without interrupting the audio stream."""
        if gain is None:
            gain = int(self.config.get("audiomoth.gain", 2))
        gain = int(gain)

        try:
            result = subprocess.run(
                [AUDIOMOTH_CMD, "update", "gain", str(gain)],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                self.logger.info(f"AudioMoth gain updated to {gain}")
                return {"result": "Success", "gain": gain}
            else:
                self.logger.error(f"AudioMoth gain update failed: {result.stderr.strip()}")
                return {"result": "Error", "error": result.stderr.strip()}
        except FileNotFoundError:
            return {"result": "Error", "error": "Binary not found — run setup.sh to install"}
        except subprocess.TimeoutExpired:
            return {"result": "Error", "error": "Command timed out"}
        except Exception as e:
            return {"result": "Error", "error": str(e)}


    @command()
    def list_audiomoths(self):
        """Returns dict containing list of audiomoths"""
        return {"result": "Success", "audiomoths": self.audiomoths}


    @command()
    def monitor(self):
        """Returns the URL of the monitoring MJPEG stream"""
        port = self.config.get("monitoring._port", 8081)
        ip = self.network.ip if hasattr(self.network, 'ip') and self.network.ip else "unknown"
        return {
            "result": "Success",
            "streaming": self.is_streaming,
            "url": f"http://{ip}:{port}/video_feed"
        }


    def _find_audiomoths(self):
        self.mics = soundcard.all_microphones()
        for mic in self.mics:
            if "AudioMoth" in mic.name.split(" "):
                serial = re.split(r"-|_", mic.id)[-3] # Serial code, unique identifier for each audiomoth
                self.audiomoths[serial] = mic.id
        self.logger.info(f"Found {len(self.audiomoths)} audiomoths, serial numbers are {', '.join(self.audiomoths.keys())}")


    """Recording"""

    def _get_audio_filename(self, serial: str) -> str:
        """Build a per-audiomoth filename for the current recording segment."""
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        filetype = self.config.get("recording.recording_filetype", "flac")
        return f"{self.facade.get_filename_prefix()}_{serial}_({self.facade.get_segment_id()}_{strtime}).{filetype}"


    def _start_new_recording(self) -> None:
        """Start initial recording segments for all connected audiomoths."""
        self._recording_stop_event.clear()
        self._segment_stop_event.clear()
        self.audiomoth_threads = []
        self.current_recording_files = {}
        self.recording_start_time = time.time()

        if not self.audiomoths:
            self.logger.warning("No audiomoths connected, cannot start recording")
            return

        intended_start_at = getattr(self, "recording_intended_start_at", None)
        for serial, mic_id in self.audiomoths.items():
            filename = self._get_audio_filename(serial)
            self.current_recording_files[serial] = filename
            self.facade.add_session_file(filename)
            thread = threading.Thread(
                target=self._record_microphone_segment,
                args=(serial, mic_id, filename, intended_start_at),
                daemon=True,
                name=f"audiomoth-{serial}"
            )
            self.audiomoth_threads.append(thread)
            thread.start()

        self.logger.info(f"Started {len(self.audiomoth_threads)} audiomoth recording threads")


    def _start_next_recording_segment(self) -> None:
        """Stage current files for export and start new recording segment for all audiomoths."""
        for thread in self.audiomoth_threads:
            thread.join(timeout=10)

        # Stage completed segment files (audio + timestamp sidecars) for export
        for filename in self.current_recording_files.values():
            self.facade.stage_file_for_export(filename)
            timestamps_filename = f"{os.path.splitext(filename)[0]}_timestamps.txt"
            if os.path.isfile(timestamps_filename):
                self.facade.stage_file_for_export(timestamps_filename)

        # Start new segment
        self._segment_stop_event.clear()
        self.audiomoth_threads = []
        self.current_recording_files = {}

        for serial, mic_id in self.audiomoths.items():
            filename = self._get_audio_filename(serial)
            self.current_recording_files[serial] = filename
            self.facade.add_session_file(filename)
            thread = threading.Thread(
                target=self._record_microphone_segment,
                args=(serial, mic_id, filename, None),  # start_at only meaningful for first segment
                daemon=True,
                name=f"audiomoth-{serial}"
            )
            self.audiomoth_threads.append(thread)
            thread.start()

        self.logger.info(f"Switched to recording segment {self.facade.get_segment_id()}")


    def _record_microphone_segment(self, serial: str, mic_id: str, filename: str, intended_start_at: float | None) -> None:
        """Record audio from one audiomoth to a single file until segment stop or recording stop."""
        sample_rate = self.config.get("audiomoth.sample_rate", 192000)
        frame_num = self.config.get("microphone.frame_num", 1024 * 128)
        block_size = self.config.get("microphone.block_size", 1024 * 128)

        timestamps_filename = f"{os.path.splitext(filename)[0]}_timestamps.txt"
        self.facade.add_session_file(timestamps_filename)

        self.logger.info(f"Recording thread started for audiomoth {serial}: {filename}")
        try:
            microphone = soundcard.get_microphone(mic_id)
            with open(timestamps_filename, 'w') as timestamps_writer:
                if intended_start_at is not None:
                    timestamps_writer.write(f"START_AT {intended_start_at:.6f}\n")
                with microphone.recorder(samplerate=sample_rate, blocksize=block_size) as recorder:
                    # Timestamp the moment the recorder is open and ready — this is
                    # the tightest available proxy for when the first audio sample
                    # was captured.  Used for post-hoc alignment with video.
                    actual_start = time.time()
                    timestamps_writer.write(f"STARTED {actual_start:.6f}\n")
                    if intended_start_at is not None:
                        startup_latency_ms = (actual_start - intended_start_at) * 1000
                        timestamps_writer.write(f"STARTUP_LATENCY_MS {startup_latency_ms:.1f}\n")
                    timestamps_writer.flush()
                    with soundfile.SoundFile(filename, mode='x', samplerate=sample_rate, channels=1, subtype="PCM_16") as f:
                        while not self._recording_stop_event.is_set() and not self._segment_stop_event.is_set():
                            # Timestamp BEFORE record() so it marks the start of
                            # the block, not the end (each block is frame_num /
                            # sample_rate seconds long, ~683 ms at default settings).
                            block_start = time.time()
                            data = recorder.record(numframes=frame_num)
                            timestamps_writer.write(f"{block_start}\n")
                            f.write(data)
        except Exception as e:
            self.logger.error(f"Recording thread error for audiomoth {serial}: {e}", exc_info=True)

        self.logger.info(f"Recording thread finished for audiomoth {serial}")


    def _stop_recording(self) -> bool:
        """Stop continuous recording with audiomoth-specific code"""
        try:
            self.is_recording = False
            self._recording_stop_event.set()

            # Wait for recording threads to finish writing before staging files
            for thread in self.audiomoth_threads:
                thread.join(timeout=10)
            self.audiomoth_threads = []

            # Stage audio files and their timestamp sidecars for export
            for filename in self.current_recording_files.values():
                self.facade.stage_file_for_export(filename)
                timestamps_filename = f"{os.path.splitext(filename)[0]}_timestamps.txt"
                if os.path.isfile(timestamps_filename):
                    self.facade.stage_file_for_export(timestamps_filename)

            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "duration": duration,
                        "status": "success",
                        "recording": False,
                        "message": "Recording completed successfully"
                    })
                self.logger.info("Concluded audiomoth _stop_recording")
                return True
            else:
                self.logger.error("Error: recording_start_time was None")
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "status": "error",
                        "error": "Recording start time was not set, so could not create timestamps."
                    })
                return False

        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": str(e)
                })
            return False


    """Monitoring stream"""

    def _monitoring_params(self):
        """Return sanitized (freq_lo_hz, freq_hi_hz, time_window_s) from config."""
        sample_rate = self.config.get("audiomoth.sample_rate", 192000)
        nyquist = sample_rate / 2

        freq_lo = int(self.config.get("monitoring.freq_lo_hz", 20_000))
        freq_hi = int(self.config.get("monitoring.freq_hi_hz", 70_000))
        time_window = float(self.config.get("monitoring.time_window_s", 3.0))

        # Clamp each to [0, nyquist]
        freq_lo = max(0, min(freq_lo, int(nyquist)))
        freq_hi = max(0, min(freq_hi, int(nyquist)))

        # Ensure at least 1 kHz separation; swap if inverted
        if freq_lo >= freq_hi:
            self.logger.warning(
                f"monitoring.freq_lo_hz ({freq_lo}) >= freq_hi_hz ({freq_hi}); swapping"
            )
            freq_lo, freq_hi = freq_hi, freq_lo
        if freq_hi - freq_lo < 1000:
            freq_hi = min(int(nyquist), freq_lo + 1000)

        # Time window: between 0.5 s and 60 s
        time_window = max(0.5, min(time_window, 60.0))

        return freq_lo, freq_hi, time_window


    def _monitor_audiomoth(self, serial: str, mic_id: str) -> None:
        """
        Continuously read short audio blocks from one audiomoth and compute
        level (dBFS) and FFT spectrum. Writes results to self.monitor_data.

        Runs as a separate recorder from the recording thread — PulseAudio/
        PipeWire supports multiple simultaneous readers on the same device.
        """
        sample_rate = self.config.get("audiomoth.sample_rate", 192000)
        fft_size = self.config.get("monitoring._fft_size", 4096)  # ~21ms at 192kHz

        self.logger.info(f"Monitor thread started for audiomoth {serial}")
        try:
            microphone = soundcard.get_microphone(mic_id)
            window = np.hanning(fft_size)
            with microphone.recorder(samplerate=sample_rate, blocksize=fft_size) as recorder:
                while not self.should_stop_monitoring_stream:
                    data = recorder.record(numframes=fft_size)
                    mono = data[:, 0] if data.ndim > 1 else data

                    # RMS level in dBFS
                    rms = np.sqrt(np.mean(mono ** 2))
                    level_db = float(20 * np.log10(max(rms, 1e-10)))

                    # Peak level in dBFS
                    peak = np.max(np.abs(mono))
                    peak_db = float(20 * np.log10(max(peak, 1e-10)))

                    # Windowed FFT magnitude spectrum in dBFS
                    fft_vals = np.abs(np.fft.rfft(mono * window))
                    # Normalise by window sum so full-scale sine = 0 dBFS
                    spectrum_db = 20 * np.log10(fft_vals / (np.sum(window) / 2) + 1e-10)
                    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)

                    _, _, time_window_s = self._monitoring_params()
                    spec_cols = max(10, int(time_window_s * sample_rate / fft_size))
                    with self.monitor_data_lock:
                        prev_buf = self.monitor_data.get(serial, {}).get('spec_buffer', [])
                        new_buf  = (prev_buf + [spectrum_db])[-spec_cols:]
                        self.monitor_data[serial] = {
                            'level_db':    level_db,
                            'peak_db':     peak_db,
                            'spec_buffer': new_buf,
                            'freqs':       freqs,
                        }
        except Exception as e:
            self.logger.error(f"Monitor thread error for audiomoth {serial}: {e}")

        self.logger.info(f"Monitor thread finished for audiomoth {serial}")


    def _render_monitor_frame(self) -> bytes | None:
        """
        Render a combined monitoring image for all audiomoths.

        Layout per audiomoth (stacked vertically):
          - Header: serial number + current dBFS reading
          - Spectrogram: time scrolling left→right on X, frequency on Y (0 Hz
            at bottom, Nyquist at top), power encoded as colour (INFERNO map)
          - Peak meter: colour-coded RMS dBFS bar with 2-second peak hold marker
        Returns JPEG bytes, or None on error.
        """
        try:
            with self.monitor_data_lock:
                data_snapshot = dict(self.monitor_data)

            sample_rate = self.config.get("audiomoth.sample_rate", 192000)
            nyquist = sample_rate / 2

            WIDTH    = 800
            ROW_H    = 315   # pixels per audiomoth row
            PLOT_H   = 200   # spectrogram height
            PADDING  = 15
            LABEL_W  = 30    # space reserved left of plots for freq labels
            DB_MIN, DB_MAX = -80, 0
            PEAK_HOLD_DURATION = 2.0  # seconds

            px0 = PADDING + LABEL_W   # left edge of plot area
            px1 = WIDTH - PADDING     # right edge
            pw  = px1 - px0           # plot width in pixels

            if not data_snapshot:
                frame = np.zeros((200, WIDTH, 3), dtype=np.uint8)
                cv2.putText(frame, "Waiting for audiomoth data...",
                            (PADDING, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (140, 140, 140), 1)
                _, jpeg = cv2.imencode('.jpg', frame)
                return jpeg.tobytes()

            frame = np.zeros((ROW_H * len(data_snapshot), WIDTH, 3), dtype=np.uint8)

            for row, (serial, data) in enumerate(data_snapshot.items()):
                y0       = row * ROW_H
                level_db = data.get('level_db', DB_MIN)
                peak_db  = data.get('peak_db',  DB_MIN)
                spec_buf = data.get('spec_buffer', [])

                py0 = y0 + 30
                py1 = py0 + PLOT_H

                # ── Header ──────────────────────────────────────────────────
                label = f"Audiomoth {serial}    RMS {level_db:.1f} dBFS    Peak {peak_db:.1f} dBFS"
                cv2.putText(frame, label, (PADDING, y0 + 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

                # ── Spectrogram ───────────────────────────────────────────────
                freq_lo_hz, freq_hi_hz, _ = self._monitoring_params()
                if spec_buf:
                    spec_mat = np.array(spec_buf, dtype=np.float32).T  # (n_bins, n_cols)
                    n_bins = spec_mat.shape[0]
                    bin_lo = int(freq_lo_hz / nyquist * n_bins)
                    bin_hi = int(freq_hi_hz / nyquist * n_bins)
                    spec_mat = spec_mat[bin_lo:bin_hi, :]
                    spec_img = cv2.resize(spec_mat, (pw, PLOT_H),
                                          interpolation=cv2.INTER_LINEAR)
                    spec_img = np.flipud(spec_img)
                    spec_norm    = np.clip((spec_img - DB_MIN) /
                                           (DB_MAX - DB_MIN) * 255, 0, 255).astype(np.uint8)
                    spec_colored = cv2.applyColorMap(spec_norm, cv2.COLORMAP_INFERNO)
                    frame[py0:py1, px0:px1] = spec_colored

                cv2.rectangle(frame, (px0, py0), (px1, py1), (60, 60, 60), 1)

                # ── Y-axis: frequency labels ──────────────────────────────────
                freq_lo_khz = freq_lo_hz / 1000
                freq_hi_khz = freq_hi_hz / 1000
                freq_range_khz = freq_hi_khz - freq_lo_khz
                freq_ticks_khz = [round(v, 1) for v in np.linspace(freq_lo_khz, freq_hi_khz, 5)]
                for fk in freq_ticks_khz:
                    fy = int(py1 - ((fk - freq_lo_khz) / freq_range_khz) * PLOT_H)
                    cv2.line(frame, (px0 - 3, fy), (px0, fy), (110, 110, 110), 1)
                    cv2.putText(frame, f"{fk:.0f}k" if fk == int(fk) else f"{fk:.1f}k", (3, fy + 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.30, (110, 110, 110), 1)

                # ── Peak meter with peak hold ─────────────────────────────────
                bx0, bx1 = PADDING, WIDTH - PADDING
                by0, by1 = py1 + 32, py1 + 62
                bw = bx1 - bx0

                # Update peak hold: reset when a new peak exceeds the held value
                # or when the hold duration has elapsed
                now = time.time()
                hold = self.peak_hold_data.get(serial, {'value_db': DB_MIN, 'time': 0.0})
                if peak_db >= hold['value_db'] or (now - hold['time']) > PEAK_HOLD_DURATION:
                    self.peak_hold_data[serial] = {'value_db': peak_db, 'time': now}
                    hold_db = peak_db
                else:
                    hold_db = hold['value_db']

                level_norm = max(0.0, min(1.0, (level_db - DB_MIN) / (DB_MAX - DB_MIN)))
                bar_w = int(level_norm * bw)

                if level_norm > 0.85:
                    bar_colour = (0, 50, 220)    # red (BGR)
                elif level_norm > 0.65:
                    bar_colour = (0, 140, 230)   # orange
                else:
                    bar_colour = (0, 200, 80)    # green

                cv2.rectangle(frame, (bx0, by0), (bx0 + bar_w, by1), bar_colour, -1)
                cv2.rectangle(frame, (bx0, by0), (bx1, by1), (80, 80, 80), 1)

                # Peak hold marker: white vertical line at the held peak position
                hold_norm = max(0.0, min(1.0, (hold_db - DB_MIN) / (DB_MAX - DB_MIN)))
                hold_x = bx0 + int(hold_norm * bw)
                cv2.line(frame, (hold_x, by0), (hold_x, by1), (255, 255, 255), 2)

                cv2.putText(frame, "Level (dBFS)", (bx0, by0 - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.32, (100, 100, 100), 1)

                # dB tick marks and labels below the bar
                for db_tick in [DB_MIN, -60, -40, -20, -10, DB_MAX]:
                    tx = bx0 + int((db_tick - DB_MIN) / (DB_MAX - DB_MIN) * bw)
                    cv2.line(frame, (tx, by1), (tx, by1 + 4), (100, 100, 100), 1)
                    label = f"{db_tick}"
                    cv2.putText(frame, label, (tx - 6, by1 + 13),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.28, (100, 100, 100), 1)

                # Row separator
                if row < len(data_snapshot) - 1:
                    cv2.line(frame, (0, y0 + ROW_H - 1), (WIDTH, y0 + ROW_H - 1), (55, 55, 55), 1)

            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return jpeg.tobytes()

        except Exception as e:
            self.logger.error(f"Frame render error: {e}")
            return None


    def _generate_monitor_frames(self):
        """MJPEG generator — yields frames at ~10 fps."""
        while not self.should_stop_monitoring_stream:
            frame = self._render_monitor_frame()
            if frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    frame +
                    b"\r\n"
                )
            time.sleep(0.1)


    def _register_monitoring_routes(self):
        @self.monitoring_app.route('/')
        def index():
            return "Audiomoth Monitoring Server"

        @self.monitoring_app.route('/video_feed')
        def video_feed():
            return Response(
                self._generate_monitor_frames(),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )


    def run_monitoring_server(self, port: int = 8081) -> None:
        try:
            from werkzeug.serving import make_server
            self.monitoring_server = make_server('0.0.0.0', port, self.monitoring_app, threaded=True)
            self.logger.info(f"Monitoring server listening on port {port}")
            self.monitoring_server.serve_forever()
        except Exception as e:
            self.logger.error(f"Monitoring server error: {e}")
            self.monitoring_server = None
            self.is_streaming = False


    def start_streaming(self) -> bool:
        """Start the MJPEG monitoring stream for all connected audiomoths."""
        try:
            if self.is_streaming:
                self.logger.warning("Monitoring stream already running")
                return False

            if not self.audiomoths:
                self.logger.warning("No audiomoths found, cannot start monitoring stream")
                return False

            port = self.config.get("monitoring._port", 8081)
            self.should_stop_monitoring_stream = False

            # One monitor thread per audiomoth
            self.monitor_threads = []
            for serial, mic_id in self.audiomoths.items():
                t = threading.Thread(
                    target=self._monitor_audiomoth,
                    args=(serial, mic_id),
                    daemon=True,
                    name=f"monitor-{serial}"
                )
                self.monitor_threads.append(t)
                t.start()

            # Flask server thread
            self.monitoring_server_thread = threading.Thread(
                target=self.run_monitoring_server,
                args=(port,),
                daemon=True,
                name="monitoring-server"
            )
            self.monitoring_server_thread.start()

            self.is_streaming = True
            self.logger.info(f"Monitoring stream started — http://{getattr(self.network, 'ip', '?')}:{port}/video_feed")

            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    'type': 'streaming_started',
                    'port': port,
                    'status': 'success',
                })

            return True

        except Exception as e:
            self.logger.error(f"Error starting monitoring stream: {e}")
            return False


    def stop_streaming(self) -> bool:
        """Stop the MJPEG monitoring stream."""
        try:
            if not self.is_streaming:
                return False

            self.should_stop_monitoring_stream = True

            # Stop monitor threads (each will exit after its current record() call, ~21ms)
            for t in self.monitor_threads:
                t.join(timeout=3)
            self.monitor_threads = []

            # Stop Flask server
            if self.monitoring_server:
                self.monitoring_server.shutdown()
                self.monitoring_server = None

            self.is_streaming = False
            self.logger.info("Monitoring stream stopped")
            return True

        except Exception as e:
            self.logger.error(f"Error stopping monitoring stream: {e}")
            return False


    """Module lifecycle"""

    def configure_module_special(self, updated_keys=None):
        if updated_keys is None:
            return
        audiomoth_keys = {k for k in updated_keys if k.startswith("audiomoth.")}
        if not audiomoth_keys:
            return
        # Gain-only change while streaming: use non-disruptive update
        if audiomoth_keys == {"audiomoth.gain"} and self.is_streaming:
            self.update_gain()
        else:
            self.configure_audiomoth()


    def get_latest_recording(self):
        return self.latest_recording


    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        super().when_controller_discovered(controller_ip, controller_port)


    def start(self) -> bool:
        """Start the audiomoth module."""
        try:
            if not super().start():
                return False
            self.start_streaming()
            return True
        except Exception as e:
            self.logger.error(f"Error starting module: {e}")
            return False


    def stop(self) -> bool:
        """Stop the module and cleanup."""
        try:
            if self.is_streaming:
                self.stop_streaming()
            return super().stop()
        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False


def main():
    audiomoth = AudiomothModule()
    audiomoth.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        audiomoth.stop()

if __name__ == '__main__':
    main()
