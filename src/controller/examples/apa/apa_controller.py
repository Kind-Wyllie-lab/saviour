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
        self.web = APAWeb(config=self.config) # Instantiate an APA specific web class 
        self.register_callbacks()


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
