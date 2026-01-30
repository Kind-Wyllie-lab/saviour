


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