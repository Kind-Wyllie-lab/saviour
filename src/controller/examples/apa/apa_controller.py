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
from typing import Optional

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import habitat controller
from controller.controller import Controller

class APAController(Controller):
    def __init__(self):
        super().__init__()
        self.config.load_controller_config("apa_controller_config.json")
        self.web.handle_special_module_status = self.handle_special_module_status

    def configure_controller(self, updated_keys: Optional[list[str]]):
        pass

    def handle_special_module_status(self, module_id: str, status: dict):
        match status.get('type'):
            case "arduino_state":
                self.web.socketio.emit("arduino_state", status)
                return True
            case "shock_started_being_delivered":
                self.web.socketio.emit("shock_started_being_delivered", status)
                return True
            case "shock_stopped_being_delivered":
                self.web.socketio.emit("shock_stopped_being_delivered", status)
                return True
            case "zone_entered":
                self.web.socketio.emit("zone_entered", status)
                return True
            case "zone_exited":
                self.web.socketio.emit("zone_exited", status)
                return True
            case _:
                self.logger.warning(f"APA controller has no logic for {status.get('type')} from {module_id}")
                return False

if __name__ == "__main__":
    controller = APAController()
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
