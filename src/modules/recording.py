#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Recording Manager

This class is used to manage recording methods for the module - starting and stopping recordings, batch exporting for 24/7 recordings, updating files for export. 

Sequence
- controller sends call to start_recording()
- name and duration (if any) are set as params
- the initial recording segment is created
- threads are started
-- self.health_recording_thread records health metadata to a csv for the current segment
-- self._recording_thread is used to automatically stop recording after preset duration 
-- self.monitor_recording_segments_thread is used to stop and start new recording segments when condition is met

Author: Andrew SG
Created: 12/01/2026
"""

import logging
import threading
import os
import datetime
from typing import Dict, Any, Optional

from src.modules.config import Config

class Recording():
    def __init__(self, config: Config):
        # Basic Setup
        self.logger = logging.getLogger(__name__)
        self.config = config
        
        # Parameters from config
        self.recording_folder = self.config.get("recording.recording_folder", "rec") # Location that files will be recorded to 
        if not os.path.exists(self.recording_folder):         # Create recording folder if it doesn't exist
            os.makedirs(self.recording_folder, exist_ok=True)
        self.logger.info(f"Recording folder = {self.recording_folder}")

        # State Flags
        self.is_recording = False

        # Main Recording Thread
        self._recording_thread = None # A thread to automatically stop recording if a duration is given # TODO: Rename this something to do with auto stop recording
        self.recording_start_time = None # When a recording was started
        
        # Health metadata thread
        self.health_recording_thread = None # A thread to record health on
        self.health_stop_event = threading.Event() # An event to signal health recording thread to stop

        # Tracking files for export
        self.session_files = []
        self.to_export = []

        # Segment based recording
        self.monitor_recording_segments_stop_flag = threading.Event()
        self.monitor_recording_segments_thread = None 
        self.segment_id = 0
        self.segment_start_time = None
        self.segment_files = []


    """Start / Stop Recording"""
    def start_recording(self, experiment_name: str = None, duration: str = None):
        """When module starts recording this gets triggered"""
        # Empty session files
        self.session_files = []

        # Store experiment folder information for export
        self.current_experiment_name = self._format_experiment_name(experiment_name)
        
        # Set up recording - filename and folder
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.recording_session_id = f"{self.api.get_module_id()}"
        
        # Use experiment name in filename if provided
        if experiment_name:
            self.current_filename_prefix = f"{self.recording_folder}/{self.current_experiment_name}_{self.recording_session_id}"
        else:
            self.current_filename_prefix = f"{self.recording_folder}/{self.recording_session_id}"
        
        os.makedirs(self.recording_folder, exist_ok=True)

        # Start generating health metadata to go with file
        self._start_recording_health_metadata()

        self.logger.info(f"Duration received as: {duration} with type {type(duration)}")
        if duration is not None:
            if duration > 0:
                self._recording_thread = threading.Thread(target=self._auto_stop_recording, args=(int(duration),))

        result = self._start_recording() # Call the start_recording method which handles the actual implementation
        self.logger.info(f"Child class _start_recording call returned {result}")

        # Start auto stop thread if needed
        if self._recording_thread:
            self._recording_thread.start()

        self.is_recording = True
        
        # Previously in camera start_recording()
        self.logger.info("Executing camera specific recording functionality...")

        # New approach
        try:
            self.to_export = []
            self._create_initial_recording_segment()
            self._start_recording_segment_monitoring()
            return True
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            return False
    

    def stop_recording(self):
        """Stop recording"""
        try:
            self._stop_recording_callback() # Callback to tell specific module to stop recording
            
            # Stop recording and tidy up session files
            self._stop_recording_segment_monitoring()
            return True
        
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False


    def _format_experiment_name(self, experiment_name:str ) -> str:
        """
        Take an experiment name received from the frontend and put it in a file-safe format.
        """
        if not experiment_name:
            return ""
        formatted_experiment_name = "".join(c for c in experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip() # Keep alphanumeric characters and spaces, dashes, underscores 
        formatted_experiment_name = formatted_experiment_name.replace(' ', '_') # Replace all spaces with underscores
        return formatted_experiment_name


    """Creating Recording Segments"""
    def _create_new_recording_segment(self):
        """Create new recording segment"""
        self.segment_id += 1
        self.segment_start_time = time.time()
        self._start_new_segment_callback() # Callback to tell specific module to start a new recording segment
        self._export_staged() # Export files that have been marked for export


    def _create_initial_recording_segment(self) -> None:
        self.segment_id = 0
        self.segment_start_time = time.time()

        # Start video
        filename = self._start_initial_segment_callback() # Callback to tell specific module to start initial recording segment - should return a filename.
        self.current_segment = filename
        self.add_session_file(filename)


    def add_session_file(self, filename: str) -> None:
        """Method to append a recording file to the current list of session files"""
        self.session_files.append(filename)
        self.logger.info(f"Session file {filename} added, new list {self.session_files}")


    """Segment Length Monitoring"""
    def _monitor_recording_length(self):
        """
        Runs in a thread and monitors length of current recording.
        If it exceeds segment length limit, stops and starts a new recording.
        """
        segment_length = self.config.get("recording.segment_length_seconds", 30) # Default to 30 for debug for now 050126

        while not self.monitor_recording_segments_stop_flag.is_set():
            if (time.time() - self.segment_start_time > segment_length):
                self._create_new_recording_segment()
                self.logger.info(f"Segment duration elapsed - new segment {self.segment_id} started at {self.segment_start_time}")
            time.sleep(0.1) # Avoid busy waiting
            
                
    def _start_recording_segment_monitoring(self):
        self.monitor_recording_segments_stop_flag.clear()
        self.segment_start_time = self.recording_start_time 
        self.segment_id = 0
        self.monitor_recording_segments_thread = threading.Thread(target=self._monitor_recording_length, daemon=True)
        self.monitor_recording_segments_thread.start()


    def _stop_recording_segment_monitoring(self): 
        self.monitor_recording_segments_stop_flag.set()
        self.monitor_recording_segments_thread.join(timeout=5)


    """Auto stop after duration"""
    def _auto_stop_recording(self, duration: int):
        self.logger.info(f"Starting thread to stop recording after {duration}s")
        while ((time.time() - self.recording_start_time) < duration):
            remaining_time = duration - (time.time() - self.recording_start_time)
            self.logger.info(f"Still recording, {remaining_time}s left")
            time.sleep(0.5) # Wait
        self.logger.info("Stopping recording")
        self.stop_recording()


    """Methods to record health metadata"""
    def _start_recording_health_metadata(self, filename: Optional[str] = None) -> None:
        """Start a thread to record health metadata. Will continue until stopped."""
        if not filename:
            filename = self.current_filename_prefix
        self.health_stop_event.clear() # Clear the stop flag before starting
        self.health_recording_thread = threading.Thread(target=self._record_health_metadata, args=(filename,), daemon=True)
        self.health_recording_thread.start()
        if not self.health_recording_thread:
            self.logger.error("Failed to start health recording thread")
        else:
            self.logger.info("Health recording thread started")


    def _stop_recording_health_metadata(self) -> None:
        """Stop an existing health_recording_thread"""
        self.logger.info("Inside stop_recording_health_metadata call")
        if self.health_recording_thread and self.health_recording_thread.is_alive():
            self.logger.info("Signalling health recording thread to stop")
            self.health_stop_event.set()
            self.health_recording_thread.join(timeout=5)
            if self.health_recording_thread.is_alive():
                self.logger.warning("Health recording thread did not terminate cleanly")
            else:
                self.logger.info("Health recording thread stopped")
        else:
            self.logger.warning("No active health recording thread was found to stop")


    def _record_health_metadata(self, filename_prefix: str):
        """
        Runs in a thread.
        Polls Health class for current health data.
        Saves to file.
        """
        interval = 5 # Interval in seconds # TODO: Take this from config
        csv_filename = f"{filename_prefix}_health_metadata.csv"
        self.add_session_file(csv_filename)
        fieldnames = ["timestamp", "cpu_temp", "cpu_usage", "memory_usage", "uptime", "disk_space", "ptp4l_offset", "ptp4l_freq", "phc2sys_offset", "phc2sys_freq", "recording", "streaming"] # Tightly coupled. #TODO: Get keys of dict returned from health.get_health()
        with open(csv_filename, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            while not self.health_stop_event.is_set():
                data = self.health.get_health()
                writer.writerow(data)
                f.flush() # Ensure each line is written
                # Wait for either a stop signal or timeout
                if self.health_stop_event.wait(timeout=interval): # "Sleeps" for duration of interval if it shouldn't exit
                    break


    """Callbacks"""  
    def start_initial_segment_callback():
        """Does this"""
        raise NotImplementedError
    

    def start_new_segment_callback():
        """Does this"""
        raise NotImplementedError
    

    def stop_recording_callback():
        """Does this"""
        raise NotImplementedError