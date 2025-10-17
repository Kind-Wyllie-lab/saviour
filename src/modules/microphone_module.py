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
import re





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
    def __init__(self, module_type="microphone", config=None, config_file_path=None):
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
        self.recording_start_time = None

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
            'get_controller_ip': self.network.controller_ip,
            'shutdown': self._shutdown,
        })
        #self.command.set_callbacks({})
        self.logger.info(f"Command handler callbacks: {self.command.callbacks}")

    def _find_audiomoths(self):
        self.mics = soundcard.all_microphones()
        for mic in self.mics:
            if "AudioMoth" in mic.name.split(" "):
                serial = re.split(r"-|_", mic.id)[-3] # Serial code, unique identifier for each audiomoth
                self.audiomoths[serial] = mic.id
        self.logger.info(f"Found {len(self.audiomoths.items())} audiomoths, serial numbers are {', '.join(self.audiomoths.keys())}")

    def _exp_name(self, experiment_name):
        # TODO: Tie this in with experiment filename creation in start_recording method.
        safe_experiment_name = "".join(c for c in experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_experiment_name = safe_experiment_name.replace(' ', '_')
        return safe_experiment_name

    def _record_microphone(self, serial, microphone: soundcard.pulseaudio._Microphone, unit, experiment_name):
        self.logger.info(f"Starting recording thread for serial {serial}, unit {unit}")
        sample_rate = self.config.get("microphone.sample_rate", 192000)
        frame_num = self.config.get("microphone.frame_num", 1024 * 128)
        block_size = self.config.get("microphone.block_size", 1024 * 128)
        file_duration = self.config.get("microphone.file_duration", 60)*60  # config in mins, convert to secs
        frame_batches_per_file = file_duration * sample_rate // frame_num

        microphone = soundcard.get_microphone(microphone)
        file_counter = 0
        with microphone.recorder(samplerate=sample_rate, blocksize=block_size) as recorder:
            while self.is_recording:
                # Get time of new file creation
                mic_start_time = datetime.datetime.now().strftime("%Y-%m-%d-%H_%M_%S_%f")
                self.logger.info(
                    f"Created new recording file for {serial} at {mic_start_time}")
                # Create new file
                batch_counter = 0
                # #TODO: Tie this in with experiment filename creation in start_recording method.
                filename = f"{self.recording_folder}/{self._exp_name(experiment_name)}_{unit}_{file_counter}_{mic_start_time}.flac"
                # Run new file writing
                with soundfile.SoundFile(filename, mode='x', samplerate=sample_rate, channels=1,
                                         subtype="PCM_16") as file:
                    while self.is_recording:
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
                        if (batch_counter == frame_batches_per_file):
                            file_counter += 1
                            break

    def _duration_process_stopper(self, duration):
        duration_seconds = float(duration)*60
        while True:
            if time.time() >= self.recording_start_time + duration_seconds:
                self.is_recording = False
                return
            time.sleep(0.5)

        

    def handle_update_audiomoth_settings(self):
        pass
    
    def clear_recordings(self):
        pass
    
    def start_streaming(self):
        pass

    def stop_streaming(self):
        pass

    def start_recording(self, experiment_name: str = None, duration: str = "1", experiment_folder: str = None, controller_share_path: str = None) -> bool:
        """Start continuous video recording"""
        # Store experiment name for use in timestamps filename
        self.current_experiment_name = experiment_name

        # First call parent class to handle common recording setup
        filename = super().start_recording(experiment_name=experiment_name, duration=duration, experiment_folder=experiment_folder, controller_share_path=controller_share_path)
        if not filename:
            return False

        try: 
            #if there are audiomoths connected
            if len(self.audiomoths) > 0:
                self.audiomoth_threads = []
                self.recording_start_time = time.time()
                for serial, mic in self.audiomoths.items():
                    #try:
                    #    unit = audiomoth_to_unit[serial]
                    #except KeyError:
                    if True:
                        self.logger.info(f"Unit not specified, using audiomoth serial instead [{serial}]")
                    unit = serial
                    # create recording thread
                    recording_thread = threading.Thread(target=self._record_microphone, args=(serial, mic, unit, experiment_name))
                    self.audiomoth_threads.append(recording_thread)
                    recording_thread.start()

                    # join recording threads
                    #for thread in self.audiomoth_threads:
                    #    thread.join()
                self.stopper_thread = threading.Thread(target=self._duration_process_stopper, args=(duration))
                self.stopper_thread.start()

            else:
                self.logger.info("No audiomoths detected, cannot record")
            
            self.is_recording = True

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
            pass
            
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
                        #"frame_count": len(self.frame_times),
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully" #with {len(self.frame_times)} frames"
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

