"""
A controller pi for the APA system.

Inherits the habitat controller class.

Serves up the system GUI and binds appropriate routes between GUI buttons and module commands.

@author: Andrew SG
@date: 080725
"""

import sys
import os
import logging
import threading
import dotenv

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import habitat controller
from habitat.src.controller.controller import Controller

# Import APA specific manager classes
from apa_web import APAWeb # Import APA web interface manager

class APAController(Controller):
    def __init__(self, config_file_path=None):
        super().__init__(config_file_path=config_file_path)
        self.web = APAWeb(config=self.config) # Instantiate an APA specific web class 
        self.register_apa_callbacks()

    def register_apa_callbacks(self):
        """Register APA-specific callbacks"""
        super().register_callbacks() # First register the parent callbacks
        apa_callbacks = {} # Define any APA specific callbacks
        self.web.register_callbacks(apa_callbacks) # Register them (assumes that register_callbacks appends, not replaces)
        self.logger.info(f"Web interface manager instantiated with these callbacks: {self.web.callbacks}")

    def start_experiment(self, experiment_name=None):
        """Start an experiment - create folder and notify modules"""
        try:
            if not experiment_name:
                experiment_name = f"experiment_{int(time.time())}"
            
            # Create experiment folder
            folder_result = self.create_experiment_folder(experiment_name)
            
            if folder_result['status'] == 'success':
                self.logger.info(f"Starting experiment: {experiment_name}")
                self.logger.info(f"Experiment folder: {folder_result['experiment_folder']}")
                
                # Send start_recording command to all modules with experiment folder info
                recording_params = {
                    "experiment_name": experiment_name,
                    "experiment_folder": folder_result['experiment_folder'],
                    "controller_share_path": folder_result['share_path']
                }
                
                self.communication.send_command("all", "start_recording", recording_params)
                
                return {
                    'status': 'success',
                    'experiment_name': experiment_name,
                    'experiment_folder': folder_result['experiment_folder'],
                    'message': f'Experiment started: {experiment_name}'
                }
            else:
                return folder_result
                
        except Exception as e:
            self.logger.error(f"Error starting experiment: {e}")
            return {
                'status': 'error',
                'message': f"Failed to start experiment: {str(e)}"
            }

    def stop_experiment(self):
        # TODO: Define what happens
        # self.arduino.shock.disarm()
        # status, message = self.arduino.motor.stop_motor()
        status = "ERROR"
        message = "Stop experiment functionality not yet implemented."
        return status, message
        


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
