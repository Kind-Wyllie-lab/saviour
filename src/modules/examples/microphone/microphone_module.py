#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Audiomoth Module Class

This class extends the base Module class to handle audiomoth-specific functionality.

Author: Andrew SG / Domagoj Anticic
Created: 18/08/2025

Parts of code based on https://github.com/Kind-Wyllie-lab/audiomoth_multimicrophone_setup by Domagoj Anticic

# TODO: Refactor to override abstract methods configure_module, _start_recording, _stop_recording
"""

import datetime
import os
import sys
import time
import logging
import numpy as np
import threading
import soundfile 
import soundcard
import re

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command


class AudiomothModule(Module):
    def __init__(self, module_type="microphone"):
        # Call the parent class constructor
        super().__init__(module_type)
    
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

        # Update config 
        self.config.load_module_config("microphone_config.json")

        # Set up audiomoth-specific callbacks for the command handler
        self.audiomoth_callbacks = {
            'monitor': self.monitor,
            'list_audiomoths': self.list_audiomoths
        }
        self.command.set_callbacks(self.audiomoth_callbacks) # Append new audiomoth callbacks
        self.logger.info(f"Command handler callbacks: {self.command.callbacks}")

    def configure_module(self):
        self.logger.info("No implementation yet for configure_module in audiomoth")

    @command
    def list_audiomoths(self):
        """Returns dict containing list of audiomoths""" 
        return {"result": "Success", "audiomoths": self.audiomoths}

    @command
    def monitor(self):
        """Command method for monitoring the output of the audiomoth microphones"""
        self.logger.warning("No implementation yet for monitor method")
        return {"result": "Failure", "message": "No implementation for monitor method"}

    def _find_audiomoths(self):
        self.mics = soundcard.all_microphones()
        for mic in self.mics:
            if "AudioMoth" in mic.name.split(" "):
                serial = re.split(r"-|_", mic.id)[-3] # Serial code, unique identifier for each audiomoth
                self.audiomoths[serial] = mic.id
        self.logger.info(f"Found {len(self.audiomoths.items())} audiomoths, serial numbers are {', '.join(self.audiomoths.keys())}")

    def _record_microphone(self, serial, microphone: soundcard.pulseaudio._Microphone, unit, experiment_name: str):
        self.logger.info(f"Starting recording thread for serial {serial}, unit {unit}")
        sample_rate = self.config.get("microphone.sample_rate", 192000)
        frame_num = self.config.get("microphone.frame_num", 1024 * 128)
        block_size = self.config.get("microphone.block_size", 1024 * 128)
        file_duration = self.config.get("recording.file_duration", 60)*60  # config in mins, convert to secs
        frame_batches_per_file = file_duration * sample_rate // frame_num
        
        start_time = datetime.datetime.now().strftime("%Y-%m-%d-H%M%S-%f")
        # TODO centralise timestamp naming - check if name sanitised before being passed here
        timestamps_file = f"{self.recording_folder}/{experiment_name}_{unit}_timestamps.txt"
        self.add_session_file(timestamps_file)
        timestamps_writer = open(timestamps_file, 'w')

        microphone = soundcard.get_microphone(microphone)
        file_counter = 0
        with microphone.recorder(samplerate=sample_rate, blocksize=block_size) as recorder:
            while self.is_recording:
                # Get time of new file creation
                mic_start_time = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S-%f") # Add _%f to include microseconds
                self.logger.info(
                    f"Created new recording file for {serial} at {mic_start_time}")
                # Create new file
                batch_counter = 0
                # TODO: Tie this in with experiment filename creation in start_recording method.
                filename = f"{self.recording_folder}/{experiment_name}_{unit}_{file_counter}_{mic_start_time}.flac" 
                self.add_session_file(filename)
                # Run new file writing
                with soundfile.SoundFile(filename, mode='x', samplerate=sample_rate, channels=1,
                                         subtype="PCM_16") as file:
                    while self.is_recording:
                        data = recorder.record(numframes=frame_num)
                        # from docs:
                        """
                        The data will be returned as a [frames x channels] float32 numpy array.
                        This function will wait until numframes frames have been recorded.
                        If numframes is given, it will return exactly `numframes` frames,
                        and buffer the rest for later.
                        """
                        batch_counter += 1
                        #data += recorder.flush()
                        timestamps_writer.write(str(time.time()) + "\n")
                        file.write(data)
                        # If all frames written
                        if (batch_counter == frame_batches_per_file):
                            file_counter += 1
                            break

        timestamps_writer.close()

    # def _duration_process_stopper(self, duration):
    #     # TODO: This exists in base module class now which will call _stop_recording at the appropriate time. 
    #     duration_seconds = float(duration)*60
    #     while True:
    #         if time.time() >= self.recording_start_time + duration_seconds:
    #             self.is_recording = False
    #             return
    #         time.sleep(0.5)

        

    
    def clear_recordings(self):
        pass
    
    def start_streaming(self):
        # TODO: Could monitor stuff go here? It's basically streaming but for audio
        pass

    def stop_streaming(self):
        pass

    def _start_recording(self) -> bool:
        """Start continuous audio recording"""
        self.logger.info("Executing audiomoth specific recording functionality...")

        # Store experiment name for use in timestamps filename
        self.logger.info(f"Recording will use {self.current_experiment_name} for filenames ")

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
                    experiment_name = self.current_experiment_name
                    recording_thread = threading.Thread(target=self._record_microphone, args=(serial, mic, unit, experiment_name))
                    self.audiomoth_threads.append(recording_thread)
                    recording_thread.start()

                    # join recording threads
                    #for thread in self.audiomoth_threads:
                    #    thread.join()
                # self.stopper_thread = threading.Thread(target=self._duration_process_stopper, args=(duration))
                # self.stopper_thread.start()

            else:
                self.logger.info("No audiomoths detected, cannot record")
            
            self.is_recording = True

            # Send status response after successful recording start
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:

                self.communication.send_status({
                    "type": "recording_started",
                    "filename": "Multiple filenames exist for each microphone",
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

    def _stop_recording(self) -> bool:
        """Stop continuous recording with audiomoth-specific code"""
        response = {}
        try:
            # Stop capture thread
            self.is_recording = False # This gets checked in each _record_microphone thread, so they should exit.
            
            # Calculate recording duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time

                
                # Send status response after successful recording stop
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "duration": duration,
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully"
                    })

                self.logger.info("Concluded audiomoth _stop_recording, waiting to exit")

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

