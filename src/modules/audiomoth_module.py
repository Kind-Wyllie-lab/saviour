#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Audiomoth Module Class

This class extends the base Module class to handle audiomoth-specific functionality.

Author: Andrew SG / Domagoj Anticic
Created: 18/08/2025
License: GPLv3

# TODO: Consider using http.server instead of flask
"""

import datetime
import subprocess
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
from src.modules.module import Module
from src.modules.command import Command
import logging
import numpy as np
import threading
import json
import soundfile 
import soundcard

class AudiomothCommand(Command):
    """Command handler specific to audiomoth functionality"""
    def __init__(self, module_id, module_type, config=None, start_time=None):
        super().__init__(module_id, module_type, config, start_time)
        self.logger.info("Initialised")

    def handle_command(self, command: str):
        """Handle audiomoth-specific commands while preserving base functionality"""
        self.logger.info("Checking for audiomoth specific commands.")
        
        try:
            # Parse command and parameters
            parts = command.split()
            cmd = parts[0]
            params = parts[1:] if len(parts) > 1 else []
            
            # Handle audiomoth-specific commands
            match cmd:
                case "monitor":
                    self._handle_monitor() # For monitoring output, ensuring there is output
                case _:
                    # If not a audiomoth-specific command, pass to parent class
                    super().handle_command(command)
                    
        except Exception as e:
            self._handle_error(e)

    def _handle_list_audiomoths(self):
        self.logger.info("Command identified as list audiomoths")
        try: 
            self.callbacks["send_status"]({
                "type": "list_audiomoths",
                "audiomoths": self.audiomoths
            })
        except Exception as e:
            self.logger.error(f"Error listing audiomoths: {str(e)}")
            self.callbacks["send_status"]({
                "type": "list_audiomoths",
                # "type": "command_failed", # TODO: Refactor like this?
                # "command": "list_audiomoths"
                "error": str(e)
            })

    def _handle_monitor(self):
        self.logger.info("Command identified as monitor")
        try:
            if 'monitor' in self.callbacks:
                self.callbacks['monitor']()
            else:
                self.logger.error("No monitor callback provided")
                self.callbacks["send_status"]({
                    "type": "monitor_failed",
                    "error": "Module not configured for monitor"
                })
        except Exception as e:
            self.logger.error(f"Error starting monitor: {str(e)}")
            self.callbacks["send_status"]({
                "type": "monitor_start_failed",
                "error": str(e)
            })

class AudiomothModule(Module):
    def __init__(self, module_type="audiomoth", config=None, config_file_path=None):
        # Initialize command handler before parent class
        self.command = AudiomothCommand(
            module_id=self.generate_module_id(module_type),
            module_type=module_type,
            config=None,  # Will be set by parent class
            start_time=None  # Will be set during start()
        )
        
        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)
    
        # Initialize audiomoth
        self.mics = [] # An empty list for discovering all connected mics 
        self.audiomoths = {} # An empty dict for discovering connected audiomoths
        self._find_audiomoths() # Discover any and all connected microphones

        # Microphone recording threads
        self._recording_threads = [] # One per microphone

        # State flags
        self.is_recording = False
        self.latest_recording = None

        # Set up audiomoth-specific callbacks for the command handler
        # TODO: is this necessary? Already done in base class? We just want to append new audiomoth-specific callbacks
        self.command.set_callbacks({
            'generate_session_id': lambda module_id: self.generate_session_id(module_id),
            'get_samplerate': lambda: self.config.get("module.samplerate", 200),
            'get_ptp_status': self.ptp.get_status,
            'get_streaming_status': lambda: self.is_streaming,
            'get_recording_status': lambda: self.is_recording,
            'send_status': lambda status: self.communication.send_status(status),
            'get_health': self.health.get_health,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'list_recordings': self.list_recordings,
            'clear_recordings': self.clear_recordings,
            'export_recordings': self.export_recordings,
            'handle_update_audiomoth_settings': self.handle_update_audiomoth_settings,  # audiomoth specific
            'get_latest_recording': self.get_latest_recording,  # audiomoth specific
            'start_streaming': self.start_streaming,
            'stop_streaming': self.stop_streaming,
            'get_controller_ip': self.service.controller_ip,
            'shutdown': self._shutdown,
        })

        self.logger.info(f"Command handler callbacks: {self.command.callbacks}")

    def _find_audiomoths(self):
        self.mics = soundcard.all_microphones()
        for mic in self.mics():
            if "AudioMoth" in mic.name.split(" "):
                serial = re.split(r"-|_", mic.id)[-3] # Serial code, unique identifier for each audiomoth
                audiomoths[serial] = mic.id

    def _record_microphone(self, serial, microphone: soundcard.pulseaudio._Microphone, unit):
        self.logger.info(f"Starting recording thread for serial {serial}, unit {unit}")
        microphone = soundcard.get_microphone(microphone)
        file_counter = 0
        with microphone.recorder(samplerate=self.samplerate, blocksize=self.blocksize) as recorder:
            while file_counter < file_number and recording:
                # Get time of new file creation
                mic_start_time = datetime.now().strftime("%Y-%m-%d-%H_%M_%S_%f")
                self.logger.info(f"Created new recording file (number {file_counter+1}) for {serial} at {mic_start_time}")
                # Create new file
                batch_counter = 0
                filename = f"{self.recording_folder}/{unit}_{mic_start_time}.flac" # #TODO: Tie this in with experiment filename creation in start_recording method.
                # Run new file writing
                with soundfile.SoundFile(filename, mode='x', samplerate=self.samplerate, channels=1, subtype="PCM_16") as file:
                    while recording:
                        data = recorder.record(numframes=frame_num)
                        # from docs:
                        """The data will be returned as a frames × channels float32 numpy array.
                        This function will wait until numframes frames have been recorded.
                        If numframes is given, it will return exactly `numframes` frames,
                        and buffer the rest for later."""
                        batch_counter += 1
                        #data += recorder.flush()
                        file.write(data)
                        # If all frames written
                        if (batch_counter == frame_batches):
                            file_counter += 1
                            break
        
        self.logger.info(f"Stopped recording on {serial}")

    def start_recording(self, experiment_name: str = None, duration: str = None, experiment_folder: str = None, controller_share_path: str = None) -> bool:
        """Start continuous video recording"""
        # Store experiment name for use in timestamps filename
        self.current_experiment_name = experiment_name
        
        # First call parent class to handle common recording setup
        filename = super().start_recording(experiment_name=experiment_name, duration=duration, experiment_folder=experiment_folder, controller_share_path=controller_share_path)
        if not filename:
            return False
        
        try:
            # Start the microphone if not already running
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)  # Give audiomoth time to start
            
            # Send status response after successful recording start
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    "filename": filename,
                    "recording": True,
                    "session_id": self.recording_session_id
                })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_start_failed",
                    "error": str(e)
                })
            return False

    def stop_recording(self) -> bool:
        """Stop continuous video recording"""
        # First check if recording using parent class
        if not super().stop_recording():
            return False
        
        try:
            # Stop recording with audiomoth-specific code
            self.picam2.stop_encoder(self.main_encoder)
            
            # Stop frame capture thread
            self.is_recording = False
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                # Save timestamps with experiment name if available
                if hasattr(self, 'current_experiment_name') and self.current_experiment_name:
                    # Sanitize experiment name for filename (remove special characters)
                    safe_experiment_name = "".join(c for c in self.current_experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                    safe_experiment_name = safe_experiment_name.replace(' ', '_')
                    timestamps_file = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_timestamps.txt"
                else:
                    timestamps_file = f"{self.recording_folder}/{self.recording_session_id}_timestamps.txt"
                
                np.savetxt(timestamps_file, self.frame_times)
                
                # Send status response after successful recording stop
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "filename": self.current_filename,
                        "session_id": self.recording_session_id,
                        "duration": duration,
                        "frame_count": len(self.frame_times),
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully with {len(self.frame_times)} frames"
                    })
                
                # Auto-export is now handled by child classes (e.g., APAaudiomoth)
                # to use the new export manager methods
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

