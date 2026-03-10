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
import numpy as np
import threading
import soundfile
import soundcard
import re

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

        # Update config
        self.config.load_module_config("microphone_config.json")

        # Set up audiomoth-specific callbacks for the command handler
        self.audiomoth_commands = {
            'monitor': self.monitor,
            'list_audiomoths': self.list_audiomoths
        }
        self.command.set_commands(self.audiomoth_commands) # Append new audiomoth callbacks


    def configure_module(self):
        self.logger.info("No implementation yet for configure_module in audiomoth")


    @command
    def list_audiomoths(self):
        """Returns dict containing list of audiomoths"""
        return {"result": "Success", "audiomoths": self.audiomoths}


    @command
    def monitor(self):
        """Command method for monitoring the output of the audiomoth microphones"""
        self.logger.warning("No implementation yet for monitor method")
        return {"result": "Failure", "message": "No implementation for monitor method"}


    def _find_audiomoths(self):
        self.mics = soundcard.all_microphones()
        for mic in self.mics:
            if "AudioMoth" in mic.name.split(" "):
                serial = re.split(r"-|_", mic.id)[-3] # Serial code, unique identifier for each audiomoth
                self.audiomoths[serial] = mic.id
        self.logger.info(f"Found {len(self.audiomoths.items())} audiomoths, serial numbers are {', '.join(self.audiomoths.keys())}")


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

        for serial, mic_id in self.audiomoths.items():
            filename = self._get_audio_filename(serial)
            self.current_recording_files[serial] = filename
            self.facade.add_session_file(filename)
            thread = threading.Thread(
                target=self._record_microphone_segment,
                args=(serial, mic_id, filename),
                daemon=True,
                name=f"audiomoth-{serial}"
            )
            self.audiomoth_threads.append(thread)
            thread.start()

        self.logger.info(f"Started {len(self.audiomoth_threads)} audiomoth recording threads")


    def _start_next_recording_segment(self) -> None:
        """Stage current files for export and start new recording segment for all audiomoths."""
        # Stage current segment files for export
        for filename in self.current_recording_files.values():
            self.facade.stage_file_for_export(filename)

        # Signal current threads to stop after their current audio block
        self._segment_stop_event.set()
        for thread in self.audiomoth_threads:
            thread.join(timeout=10)

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
                args=(serial, mic_id, filename),
                daemon=True,
                name=f"audiomoth-{serial}"
            )
            self.audiomoth_threads.append(thread)
            thread.start()

        self.logger.info(f"Switched to recording segment {self.facade.get_segment_id()}")


    def _record_microphone_segment(self, serial: str, mic_id: str, filename: str) -> None:
        """Record audio from one audiomoth to a single file until segment stop or recording stop."""
        sample_rate = self.config.get("microphone.sample_rate", 192000)
        frame_num = self.config.get("microphone.frame_num", 1024 * 128)
        block_size = self.config.get("microphone.block_size", 1024 * 128)

        timestamps_filename = f"{os.path.splitext(filename)[0]}_timestamps.txt"
        self.facade.add_session_file(timestamps_filename)

        self.logger.info(f"Recording thread started for audiomoth {serial}: {filename}")
        try:
            microphone = soundcard.get_microphone(mic_id)
            with open(timestamps_filename, 'w') as timestamps_writer:
                with microphone.recorder(samplerate=sample_rate, blocksize=block_size) as recorder:
                    with soundfile.SoundFile(filename, mode='x', samplerate=sample_rate, channels=1, subtype="PCM_16") as f:
                        while not self._recording_stop_event.is_set() and not self._segment_stop_event.is_set():
                            data = recorder.record(numframes=frame_num)
                            timestamps_writer.write(str(time.time()) + "\n")
                            f.write(data)
        except Exception as e:
            self.logger.error(f"Recording thread error for audiomoth {serial}: {e}")

        self.logger.info(f"Recording thread finished for audiomoth {serial}")


    def configure_module_special(self):
        pass


    def start_streaming(self):
        # TODO: Could monitor stuff go here? It's basically streaming but for audio
        pass


    def stop_streaming(self):
        pass


    def _stop_recording(self) -> bool:
        """Stop continuous recording with audiomoth-specific code"""
        try:
            self.is_recording = False
            self._recording_stop_event.set()

            # Stage the current segment's files for export
            for filename in self.current_recording_files.values():
                self.facade.stage_file_for_export(filename)

            # Wait for recording threads to finish their current block
            for thread in self.audiomoth_threads:
                thread.join(timeout=10)
            self.audiomoth_threads = []

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


    def get_latest_recording(self):
        """Get the latest recording"""
        return self.latest_recording


    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        super().when_controller_discovered(controller_ip, controller_port)


    def start(self) -> bool:
        """Start the audiomoth module - including streaming"""
        try:
            # Start the parent module first
            if not super().start():
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error starting module: {e}")
            return False


    def stop(self) -> bool:
        """Stop the module and cleanup"""
        try:
            # Stop streaming if active
            if self.is_streaming:
                self.stop_streaming()

            # Call parent stop
            return super().stop()

        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False


def main():
    audiomoth = AudiomothModule()
    audiomoth.start()

    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        audiomoth.stop()

if __name__ == '__main__':
    main()
