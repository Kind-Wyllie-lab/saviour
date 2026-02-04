"""
A controller with APA specific functionality.

Inherits the habitat controller class.

Serves up the system GUI and binds appropriate routes between GUI buttons and module commands.

@author: Andrew SG
@date: 080725
"""

import sys
import os
import logging
import threading
from typing import Optional, List

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import habitat controller
from controller.controller import Controller

class HabitatController(Controller):
    def __init__(self):
        super().__init__()

        # Update config
        self.config.load_controller_config("acoustic_startle_controller_config.json")


        self.web.register_additional_socketio_events(self._register_special_socket_events)
        self.web.handle_special_module_status = self.handle_special_module_status # Bind callback


    def configure_controller(self, updated_keys: Optional[list[str]]):
        pass


    def handle_special_module_status(self, module_id: str, status: str):
        match status:
            case _:
                self.logger.warning(f"No logic for {status} from {module_id}")
                return False    


    def _register_special_socket_events(self, socketio):
        @socketio.on("play_sound")
        def handle_play_sound(data):
            module_id = data.get("module_id")
            self.logger.info(f"Playing sound on {module_id}")
            self.api.send_command(module_id, "play_sound", {})



if __name__ == "__main__":
    controller = HabitatController()
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
