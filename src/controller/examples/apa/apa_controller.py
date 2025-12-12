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

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import habitat controller
from controller.controller import Controller

# Import APA specific manager classes
from apa_web import APAWeb # Import APA web interface manager

class APAController(Controller):
    def __init__(self, config_file_path=None):
        super().__init__(config_file_path=config_file_path)

        # Reinstantiate webapp  
        # self.web = APAWeb(config=self.config) # Instantiate an APA specific web class 

        self.web.handle_special_module_status = self.handle_special_module_status # Bind callback


        self.register_callbacks() # If reinstantiating web object make sure to re-register callbacks

    def handle_special_module_status(self, module_id: str, status: str):
        match status:
            case "arduino_state":
                self.socketio.emit("arduino_state", status) 
                return True    
            case _:
                self.logger.warning(f"APA web has no logic for {status} from {module_id}")
                return False    

if __name__ == "__main__":
    controller = APAController(config_file_path="config.json")
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
