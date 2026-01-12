#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Recording Manager

This class is used to manage recording methods for the module - starting and stopping recordings, batch exporting for 24/7 recordings, updating files for export. 

Author: Andrew SG
Created: 12/01/2026
"""

import logging
import threading
import os
import datetime

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