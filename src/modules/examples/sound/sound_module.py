#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Sound Module Class

This code supports enables a Pi 5 with a HifiBerry hat to play sounds on command.
Sounds must be .wav, formatted to -3dB peak and located in the sounds/ folder.

Author: Andrew SG
Created: 27/01/2026
"""
# Base Imports
import sys
import os
import subprocess
from typing import Optional
import time

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check

class SoundModule(Module):
    def __init__(self, module_type="sound"):
        super().__init__(module_type)

        self.config.load_module_config("sound_config.json")


        self.module_checks = {
            self._check_hifiberry
        }

        self.sound_commands = {
            "play_sound": self._play_sound,
            "list_sound_files": self._list_sound_files,
            "use_this_sound_file": self._use_this_sound_file
        }

        self.command.set_commands(self.sound_commands)


        self.available_sounds = self._get_available_sounds()
        self.sound_to_play = self.available_sounds[0]


    @command()
    def _play_sound(self):
        duration = self.config.get("sound.duration") # Duration in seconds to play for 
        filename = "sounds/" + self.sound_to_play # The wav to be played 
        volume = self.config.get("sound.volume") # The volume to play at (1 = 100%) 
        device = "plughw:2,0"

        timestamp = time.time_ns()

        ffmpeg_proc = subprocess.Popen([
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-t", str(duration),
            "-i", filename,
            "-filter:a",
            f"volume={volume}",
            "-f", "wav",
            "-"
        ],
        stdout=subprocess.PIPE
        )

        aplay_proc = subprocess.Popen(
            ["aplay", "-D", device],
            stdin=ffmpeg_proc.stdout
        )

        ffmpeg_proc.stdout.close()
        aplay_proc.communicate()

        if self.api.get_recording_status() == True:
            self._write_sound_event_to_file(timestamp)

        # TODO: Check if was successful
        return {"result": "success"}


    @command()
    def _list_sound_files(self):
        files = self._get_available_sounds()

        response = {
            "sound_files": files,
            "selected_file": self.sound_to_play
        }

        return response


    @command()
    def _use_this_sound_file(self, filename: str):
        self.logger.info(f"Switching to use {filename}")
        self.sound_to_play = filename


    def _get_available_sounds(self) -> list:
        files = [f for f in os.listdir("sounds/") if os.path.isfile(f"sounds/{f}")]
        return files


    """Config"""
    def configure_module(self, updated_keys: Optional[list[str]]):
        # Configure self however necessary
        pass


    """Recording"""
    def _write_sound_event_to_file(self, timestamp_ns: int):
        # Use file handler 
        if self._sound_file_handle:
            self._sound_file_handle.write(f'{timestamp_ns},{self.sound_to_play},{self.config.get("sound.volume")},{self.config.get("sound.duration")}\n')


    def _create_sound_event_file(self) ->  bool:
        filename = f"{self.api.get_filename_prefix()}_sound_events_({self.api.get_segment_id()}_{self.api.get_segment_start_time()}).csv"
        self.logger.info(f"Creating sound events file {filename}")
        self.api.stage_file_for_export(filename)
        self.api.add_session_file(filename)
        try:
            self._sound_file_handle = open(filename, "w", buffering=1)  # line-buffered
            # Write header with metadata
            f = self._sound_file_handle
            f.write("# Sound Events Recording\n")
            f.write(f"# Session ID / Segment: {self.api.get_recording_session_id()}_{self.api.get_segment_id()}\n")
            f.write(f"# Segment Start: {self.api.get_segment_start_time()}\n")
            f.write("#\n")
            f.write("Timestamp (ns), sound played, volume factor, duration (s)\n")
        except Exception as e:
            self.logger.error(f"Failed to open sound events file: {e}")
            self._sound_file_handle = None

    
    def _close_sound_event_file(self) -> bool:
        """Close sound events file"""
        try:
            self._sound_file_handle = None
            self.logger.info(f"Closed shock events file")
        except Exception as e:
            self.logger.warning(f"Error closing shock events file: {e}")


    def _start_new_recording(self):
        # Start recording session - probably tracking sounds produced in csv file
        self._create_sound_event_file()
        return True
    

    def _start_next_recording_segment(self):
        # Segment based recording - close file, open new one
        self._close_sound_event_file()
        self._create_sound_event_file()
        return True


    def _stop_recording(self):
        self._close_sound_event_file()
        return True


    """Self Check"""
    @check()
    def _check_hifiberry(self):
        # Do something here to check hifiberry working as intended
        hifiberry_working = True
        if not hifiberry_working:
            message = "Hifiberry not working"
            self.logger.warning(message)
            return False, message
        else:
            return True, "Hifiberry working" 


def main():
    sound = SoundModule()
    sound.start()

    try:
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\nShuttind down...")
        sound.stop()

if __name__ == "__main__":
    main()