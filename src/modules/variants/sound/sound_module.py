#!/usr/bin/env python3
"""
SAVIOUR System - Sound Module Class

This code supports enables a Pi 5 with a HifiBerry hat to play sounds on command.
Sounds must be .wav, formatted to -3dB peak and located in the sounds/ folder.

Author: Andrew SG
Created: 27/01/2026
"""
# Base Imports
import os
import subprocess
import sys
import time

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, check, command


class SoundModule(Module):
    def __init__(self, module_type="sound"):
        super().__init__(module_type)

        self.config.load_module_config("sound_config.json")

        # .wav files ship alongside this module; resolve relative to the file
        # so discovery/playback don't depend on the process CWD.
        self._sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")

        # None once a HiFiBerry sound card is detected; otherwise a
        # human-readable reason. Surfaced every heartbeat via get_health()
        # (the System page's "NO HARDWARE" badge) and as the _check_hifiberry
        # readiness-failure reason.
        self.hardware_fault: str | None = None

        self.module_checks = [self._check_hifiberry]

        self.sound_commands = {
            "play_sound": self._play_sound,
            "list_sound_files": self._list_sound_files,
            "use_this_sound_file": self._use_this_sound_file
        }

        self.command.set_commands(self.sound_commands)


        # Sound files
        self.available_sounds = self._get_available_sounds()
        self.sound_to_play = (
            self.available_sounds[0] if self.available_sounds else None
        )

        # Recording
        self.current_sound_event_file = None
        self._sound_file_handle = None

        # Populate hardware_fault for the first heartbeat (the readiness check
        # refreshes it thereafter).
        self._check_hifiberry()

    @command()
    def _play_sound(self):
        if not self.sound_to_play:
            return {"result": "error", "message": "no sound file available"}
        duration = self.config.get("sound.duration")  # seconds to play for
        filename = os.path.join(self._sounds_dir, self.sound_to_play)
        volume = self.config.get("sound.volume")  # 1 = 100%
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

        if self.facade.get_recording_status() == True:
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
        try:
            return [
                f for f in os.listdir(self._sounds_dir)
                if os.path.isfile(os.path.join(self._sounds_dir, f))
            ]
        except FileNotFoundError:
            self.logger.warning(f"No sounds directory at {self._sounds_dir}")
            return []


    """Config"""
    def configure_module_special(self, updated_keys: list[str] | None):
        # Configure self however necessary
        pass


    """Recording"""
    def _get_sound_segment_filename(self) -> str:
        """Return a filename for the current sound events segment"""
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        return f"{self.facade.get_filename_prefix()}_sound_events_({self.facade.get_segment_id()}_{strtime}).csv"


    def _write_sound_event_to_file(self, timestamp_ns: int):
        # Use file handler
        if self._sound_file_handle:
            self._sound_file_handle.write(f'{timestamp_ns},{self.sound_to_play},{self.config.get("sound.volume")},{self.config.get("sound.duration")}\n')


    def _create_sound_event_file(self) ->  bool:
        filename = self._get_sound_segment_filename()
        self.logger.info(f"Creating sound events file {filename}")
        self.current_sound_event_file = filename
        try:
            self._sound_file_handle = open(filename, "w", buffering=1)  # line-buffered
            # Write header with metadata
            f = self._sound_file_handle
            f.write("# Sound Events Recording\n")
            f.write(f"# Session ID / Segment: {self.facade.get_recording_session_id()}_{self.facade.get_segment_id()}\n")
            f.write(f"# Segment Start: {self.facade.get_segment_start_time()}\n")
            f.write("#\n")
            f.write("Timestamp (ns), sound played, volume factor, duration (s)\n")
        except Exception as e:
            self.logger.error(f"Failed to open sound events file: {e}")
            self._sound_file_handle = None


    def _close_sound_event_file(self) -> bool:
        """Close sound events file"""
        try:
            if self._sound_file_handle is not None:
                self._sound_file_handle.flush()
                self._sound_file_handle.close()
                self._sound_file_handle = None
            self.logger.info("Closed sound events file")
        except Exception as e:
            self.logger.warning(f"Error closing sound events file: {e}")


    def _start_new_recording(self):
        # Start recording session - probably tracking sounds produced in csv file
        self._create_sound_event_file()
        return True


    def _start_next_recording_segment(self):
        self._close_sound_event_file()
        self.facade.stage_file_for_export(self.current_sound_event_file)
        self._create_sound_event_file()
        return True


    def _stop_recording(self):
        self._close_sound_event_file()
        self.facade.stage_file_for_export(self.current_sound_event_file)
        return True


    """Self Check"""
    def _detect_hifiberry(self) -> tuple[bool, str]:
        """A HiFiBerry DAC/AMP HAT registers an ALSA card whose id/name
        contains "hifiberry" (e.g. "sndrpihifiberry"). /proc/asound/cards is
        the cheapest source; fall back to `aplay -l`."""
        try:
            with open("/proc/asound/cards") as f:
                text = f.read()
            for line in text.splitlines():
                if "hifiberry" in line.lower():
                    return True, line.strip()
        except OSError:
            pass
        try:
            out = subprocess.run(
                ["aplay", "-l"], capture_output=True, text=True,
                timeout=5, check=False,
            ).stdout
            if "hifiberry" in out.lower():
                return True, "aplay -l"
        except (OSError, subprocess.SubprocessError):
            pass
        return False, ""

    @check()
    def _check_hifiberry(self):
        detected, detail = self._detect_hifiberry()
        if detected:
            self.hardware_fault = None
            return True, f"HiFiBerry sound card present ({detail})"
        self.hardware_fault = "No HiFiBerry sound card detected"
        self.logger.warning(self.hardware_fault)
        return False, self.hardware_fault


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
