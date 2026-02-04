


import sys
import os
import subprocess
from typing import Optional
import time

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
            "play_sound": self._play_sound
        }

        self.command.set_commands(self.sound_commands)


    def _play_sound(self):
        duration = self.config.get("sound.duration") # Duration in seconds to play for # TODO: Take from config
        filename = self.config.get("sound.filename") # The wav to be played # TODO: Take from config
        volume = self.config.get("sound.volume") # The volume to play at (1 = 100%) # TODO: Take from config
        device = "plughw:2,0"

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


    """Config"""
    def configure_module(self, updated_keys: Optional[list[str]]):
        # Configure self however necessary
        pass

    """Recording"""
    def _start_new_recording(self):
        # Start recording session - probably tracking sounds produced in csv file
        pass
    

    def _start_next_recording_segment(self):
        # Segment based recording - close file, open new one
        pass


    def _stop_recording(self):
        pass


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