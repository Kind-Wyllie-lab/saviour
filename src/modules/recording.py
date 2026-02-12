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
-- self._recording_duration_thread is used to automatically stop recording after preset duration 
-- self.monitor_recording_segments_thread is used to stop and start new recording segments when condition is met

Author: Andrew SG
Created: 12/01/2026
"""

import logging
import threading
import os
import datetime
import time
import csv
from typing import Dict, Any, Optional

from src.modules.config import Config

class Recording():
    def __init__(self, config: Config):
        # Basic Setup
        self.logger = logging.getLogger(__name__)
        self.config = config
        
        # Parameters from config
        self.recording_folder = f'{self.config.get("recording.recording_folder", "/var/lib/saviour/recordings")}/pending' # Location that files will be recorded to 
        self.logger.info(f"Recording folder: {self.recording_folder}")
        os.makedirs(self.recording_folder, exist_ok=True)

        # State Flags
        self.is_recording = False

        # Main Recording Thread
        self._recording_duration_thread = None # A thread to automatically stop recording if a duration is given # TODO: Rename this something to do with auto stop recording
        self.recording_start_time = None # When a recording session was started
        
        # Health metadata thread
        self.health_recording_thread = None # A thread to record health on
        self.health_stop_event = threading.Event() # An event to signal health recording thread to stop
        self.last_health_segment = None
        self.current_health_segment = None

        # Tracking files for export
        self.current_filename_prefix = None

        # Segment based recording
        self.monitor_recording_segments_stop_flag = threading.Event()
        self.monitor_recording_segments_thread = None 
        self.segment_id = 0
        self.segment_start_time = None
        self.segment_files = []


    """Start / Stop Recording"""
    def start_recording(self, session_name: str = None, duration: str = None) -> Optional[str]:
        """When module starts recording this gets triggered"""
        """
        Start recording. Should be extended with module-specific implementation.
        
        Args:
            session_name: Optional experiment name to prefix the filename
            duration: Optional duration parameter (not currently used)
        """
        self.logger.info(f"start_recording called with session_name {session_name}, duration {duration}")
        
        # Check not already recording
        if self.is_recording:
            self.logger.info("Already recording")
            self.facade.send_status({
                "type": "recording_start_failed",
                "error": "Already recording"
            })
            return None

        # Store experiment folder information for export
        self.current_session_name = self._format_session_name(session_name)

        # Set the export folder based on the supplied experiment name
        self.facade.set_session_name(session_name)
        self.facade.when_recording_starts()
        
        # Set up recording - filename and folder
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.recording_session_id = f"{self.facade.get_module_name()}"
        
        # Use experiment name in filename if provided
        if session_name:
            self.current_filename_prefix = f"{self.recording_folder}/{self.current_session_name}_{self.recording_session_id}"
        else:
            self.current_filename_prefix = f"{self.recording_folder}/{self.recording_session_id}"
        
        self.logger.info(f"Filenames will be prefixed {self.current_filename_prefix}")
        os.makedirs(self.recording_folder, exist_ok=True)

        # Set recording start time
        self.recording_start_time = time.time()

        # Check if duration was supplied
        self.logger.info(f"Duration received as: {duration} with type {type(duration)}")
        if duration is not None:
            if duration > 0:
                self._recording_duration_thread = threading.Thread(target=self._auto_stop_recording, args=(int(duration),))

        # Execute module specific recoridng e.g. camera video
        self._create_initial_recording_segment()

        # Start monitoring recoridng segment durations
        self._start_recording_segment_monitoring()

        # Start generating health metadata to go with file
        self._start_new_health_recording()

        # Start auto stop thread if needed
        if self._recording_duration_thread:
            self._recording_duration_thread.start()

        self.is_recording = True

        self.logger.info("Sending recording started message to controller")
        self.facade.send_status({
            "type": "recording_started",
            "status": "success",
            "recording": True
        })

        return {"result": "success"}
        

    def stop_recording(self) -> bool:
        """Stop recording. Returns True if stopped, False otherwise."""
        self.logger.info(f"Stop recording called. to_export contains: {self.facade.get_staged_files()}")
        try:
            # Check if recording
            if not self.is_recording:
                self.logger.info("Already stopped recording")
                self.facade.send_status({
                    "type": "recording_stop_failed",
                    "error": "Not recording"
                })
                return False

            # Stop monitoring recording segment length
            self._stop_recording_segment_monitoring()

            # Stop recording in general
            if not self.facade.stop_recording(): # Module specific implementation of stop_recording
                self.logger.warning(f"Something went wrong stopping recording.")
                self.facade.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                })
                return
            
            # Stop recording health metadata
            self._stop_recording_health_metadata()
            self.facade.stage_file_for_export(self.current_health_segment)
            self.logger.info("Made it past stop_recording_health_metadata call")

            self.facade.send_status({
                "type": "recording_stopped",
                "status": "success",
                "recording": False,
            })

            self.is_recording = False
            self.logger.info("Made it past stop_recording call")

            self.logger.info(f"Config says {self.config.get('export.auto_export')}")
            if self.config.get("export.auto_export") == True:
                self.facade.export_staged()

            return {"result": "Success"}

        except Exception as e:
            self.logger.error(f"Error in stop_recording: {e}")
            return {"result": "failure", "message": f"Error in stop_recording: {e}"}


    def _format_session_name(self, session_name:str ) -> str:
        """
        Take an experiment name received from the frontend and put it in a file-safe format.
        """
        if not session_name:
            return ""
        formatted_session_name = "".join(c for c in session_name if c.isalnum() or c in (' ', '-', '_')).rstrip() # Keep alphanumeric characters and spaces, dashes, underscores 
        formatted_session_name = formatted_session_name.replace(' ', '_') # Replace all spaces with underscores
        return formatted_session_name


    """Creating Recording Segments"""
    def _create_new_recording_segment(self):
        """Create new recording segment"""
        # Increment segment
        self.segment_id += 1
        self.segment_start_time = time.time()
        
        # Start new health metadata segment
        self._start_next_health_metadata_segment()

        # Start new actual recording segment 
        self.facade.start_next_recording_segment() # Callback to tell specific module to start a new recording segment
        self.facade.export_staged() # Export files that have been marked for export


    def _create_initial_recording_segment(self) -> None:
        self.logger.info(f"Creating initial recording segment")
        self.segment_id = 0
        self.segment_start_time = time.time()
        self.logger.info(f"Segment {self.segment_id} started at {self.segment_start_time}")

        # Start video
        self.facade.start_new_recording()


    """Segment Length Monitoring"""
    def _monitor_recording_length(self):
        """
        Runs in a thread and monitors length of current recording.
        If it exceeds segment length limit, stops and starts a new recording.
        """
        segment_length = self.config.get("recording.segment_length_seconds", 30) # Default to 30 for debug for now 050126
        self.logger.info(f"Segment started at {self.segment_start_time},  segment length {segment_length}")

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
        self.logger.info("Stopping recording segment monitoring.")
        try:
            self.monitor_recording_segments_stop_flag.set()
            self.monitor_recording_segments_thread.join(timeout=5)
            return True
        except Exception as e:
            self.logger.error(f"Error stopping recording segment monitoring thread: {e}")
            return False


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
    def _start_health_metadata_thread(self) -> None:
        """Start a thread to record health metadata segment. Will continue until stopped."""
        # Set up thread
        self.health_stop_event.clear() # Clear the stop flag before starting
        self.health_recording_thread = threading.Thread(target=self._record_health_metadata, daemon=True)
        self.health_recording_thread.start()
        if not self.health_recording_thread:
            self.logger.error("Failed to start health recording thread")
        else:
            self.logger.info("Health recording thread started")


    def _start_new_health_recording(self) -> None:
        """Start the initial health recording segment"""
        # Set up filename for initial segment
        csv_filename = self._get_health_segment_filename()
        self.current_health_segment = csv_filename

        # Start the thread
        self._start_health_metadata_thread()


    def _start_next_health_metadata_segment(self) -> None:
        """Start thread to record next health metadata segment."""
        # Stop recording health metadata
        self._stop_recording_health_metadata()
        
        # Get new filename and stage last file for export
        self.last_health_segment = self.current_health_segment
        self.current_health_segment = self._get_health_segment_filename()
        self.facade.stage_file_for_export(self.last_health_segment)

        # Start new thread
        self._start_health_metadata_thread()


    def _get_health_segment_filename(self) -> str:
        """Return a filename for the current health metadata segment""" 
        strtime = self.facade.get_utc_time(self.segment_start_time)
        return f"{self.current_filename_prefix}_health_metadata_({self.segment_id}_{strtime}).csv"


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


    def _record_health_metadata(self):
        """Retrieve health metadata and write to csv tile"""
        interval = self.config.get("health_metadata_recording_interval", 5)
        csv_filename = self.current_health_segment 
        fieldnames = list(self.facade.get_health().keys())
        with open(csv_filename, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            while not self.health_stop_event.is_set():
                data = self.facade.get_health()
                writer.writerow(data)
                f.flush() # Ensure each line is written
                # Wait for either a stop signal or timeout
                if self.health_stop_event.wait(timeout=interval): # "Sleeps" for duration of interval if it shouldn't exit
                    break