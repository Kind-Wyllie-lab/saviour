#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Base Module Class

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 17/03/2025
License: GPLv3
"""

import sys
import os
from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import subprocess
import time
import socket
import logging
import uuid
import threading
import random
import psutil
import asyncio
from typing import Dict, Any, Optional
import numpy as np
from enum import Enum, auto
import datetime


# Import managers
from src.modules.module_file_transfer_manager import ModuleFileTransfer
from src.modules.module_config_manager import ModuleConfigManager
from src.modules.module_communication_manager import ModuleCommunicationManager
import src.controller.controller_session_manager as controller_session_manager # TODO: Change this to a module manager
from src.modules.module_health_manager import ModuleHealthManager
from src.modules.module_command_handler import ModuleCommandHandler
from src.modules.module_ptp_manager import PTPManager, PTPRole
from src.modules.module_export_manager import ExportManager

class Module:
    """
    Base class for all modules in the Habitat Controller.

    This class provides common functionality that all hardware modules (camera, microphone, TTL IO, RFID) share.
    It handles network communication with the main controller, PTP synchronization, power management, health monitoring, and basic lifecycle operations.

    Attributes:
        module_id (str): Unique identifier for the module
        module_type (str): Type of module (camera, microphone, ttl_io, rfid)
        config (dict): Configuration parameters for the module

    """
    def __init__(self, module_type: str, config: dict = None, config_path: str = None):
        # Module type
        self.module_type = module_type
        self.module_id = self.generate_module_id(self.module_type)
        self.config_path = config_path
        self.recording_folder = "rec"  # Default recording folder
        
        # Create recording folder if it doesn't exist
        if not os.path.exists(self.recording_folder):
            os.makedirs(self.recording_folder)
            self.logger.info(f"(MODULE) Created recording folder: {self.recording_folder}")
        
        # Setup logging first
        self.logger = logging.getLogger(f"{self.module_type}.{self.module_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.info(f"Initializing {self.module_type} module {self.module_id}")
        
        # Add console handler if none exists
        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

        # Managers
        self.logger.info(f"(MODULE) Initialising managers")
        self.logger.info(f"(MODULE) Initialising config manager")
        self.config_manager = ModuleConfigManager(self.logger, self.module_type, self.config_path)
        self.export_manager = ExportManager(
            module_id=self.module_id,
            recording_folder=self.recording_folder,
            config=self.config_manager.get_all(),
            logger=self.logger
        )
        self.logger.info(f"(MODULE) Initialising communication manager")
        self.communication_manager = ModuleCommunicationManager(         # Communication manager - handles ZMQ messaging
            self.logger, # Pass in the logger
            self.module_id, # Pass in the module ID for use in messages
            config_manager=self.config_manager # Pass in the config manager for getting properties
        )
        self.logger.info(f"(MODULE) Initialising health manager")
        self.health_manager = ModuleHealthManager(
            self.logger, 
            config_manager=self.config_manager,
            communication_manager=self.communication_manager
        )
        self.logger.info(f"(MODULE) Initialising PTP manager")
        self.ptp_manager = PTPManager(
            logger=self.logger,
            role=PTPRole.SLAVE)
        if not hasattr(self, 'command_handler'): # Initialize command handler if not already set - extensions of module class might set their own command handler
            self.logger.info(f"(MODULE) Initialising command handler")
            self.command_handler = ModuleCommandHandler(
                self.logger,
                self.module_id,
                self.module_type,
                config_manager=self.config_manager,
                start_time=None # Will be set during start()
            )
        from src.modules.module_service_manager import ModuleServiceManager # Lazy import and initialization of ServiceManager to avoid circular imports
        self.logger.info(f"(MODULE) Initialising service manager")
        self.service_manager = ModuleServiceManager(self.logger, self)
        self.logger.info(f"(MODULE) Initialising service manager")
        self.session_manager = controller_session_manager.SessionManager()

        # Register Callbacks
        self.health_manager.get_ptp_offsets = self.ptp_manager.get_status # Bind health manager's callback to the ptp_manager method
        self.communication_manager.command_callback = self.command_handler.handle_command # Set the callback in the communication manager to use the command handler
        self.command_handler.set_callbacks({ # Define callbacks for the command handler
            'generate_session_id': lambda module_id: self.session_manager.generate_session_id(module_id), # 
            'get_samplerate': lambda: self.config_manager.get("module.samplerate", 200), # Use a lambda function to get it fresh from the config manager every time
            'get_ptp_status': self.ptp_manager.get_status, # Use a lambda function to get status fresh from ptp manager everytime
            'get_streaming_status': lambda: self.is_streaming,
            'get_recording_status': lambda: self.is_recording,
            'send_status': lambda status: self.communication_manager.send_status(status),
            'get_health': self.health_manager.get_health,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'list_recordings': self.list_recordings,
            'clear_recordings': self.clear_recordings,
            'export_recordings': self.export_recordings
        })
        self.export_manager.set_callbacks({
            'get_controller_ip': lambda: self.service_manager.controller_ip  # or whatever the callback function is
        })
        
        # Recording management
        self.recording_session_id = None
        self.current_filename = None

        # Parameters from config
        self.samplerate = self.config_manager.get("module.samplerate")
        self.recording_folder = self.config_manager.get("recording_folder")
        self.recording_filetype = self.config_manager.get(f"{self.module_type}.file_format", None) # Find the appropriate filetype for this module type, 

        # Control flags
        self.is_running = False  # Start as False
        self.is_recording = False # Flag to indicate if the module is recording e.g. video, TTL, audio, etc.
        self.is_streaming = False # Flag to indicate if the module is streaming on a network port e.g. video, TTL, audio, etc.
        
        # Track when module started for uptime calculation
        self.start_time = None

    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        """Callback when controller is discovered via zeroconf"""
        self.logger.info(f"(MODULE) Service manager informs that controller was discovered at {controller_ip}:{controller_port}")
        self.logger.info(f"(MODULE) Module will now initialize the necessary managers")
        # Only proceed if we're not already connected
        if self.communication_manager.controller_ip:
            self.logger.info("(MODULE) Already connected to controller")
            return
            
        try:
            # 1. Initialize file transfer
            self.logger.info("(MODULE) Initializing file transfer")
            from src.modules.module_file_transfer_manager import ModuleFileTransfer
            self.file_transfer = ModuleFileTransfer(
                controller_ip=controller_ip,
                logger=self.logger
            )
            self.logger.info("(MODULE) File transfer initialized")
            
            # 2. Connect communication manager
            self.logger.info("(MODULE) Connecting communication manager to controller")
            self.communication_manager.connect(controller_ip, controller_port)
            self.logger.info("(MODULE) Communication manager connected to controller")
            
            # 3. Start command listener
            self.logger.info("(MODULE) Requesting communication manager to start command listener")
            self.communication_manager.start_command_listener()
            self.logger.info("(MODULE) Command listener started")
            
            # 4. Start heartbeats if module is running
            if self.is_running:
                self.logger.info("(MODULE) Requesting health manager to start heartbeats")
                self.health_manager.start_heartbeats()
                self.logger.info("(MODULE) Heartbeats started")
            
            # 5. Start PTP
            self.logger.info("(MODULE) Starting PTP manager")
            self.ptp_manager.start()
            
            self.logger.info("(MODULE) Controller connection and initialization complete")
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error during controller initialization: {e}")
            # Clean up any partial initialization
            self.communication_manager.cleanup()
            self.file_transfer = None
            raise

    def controller_disconnected(self):
        """Callback when controller is disconnected"""
        # What should happen here - we don't want to stop the module altogether, we want to stop recording, deregister controller, and wait for new controller connection.
        self.logger.info("(MODULE) Controller disconnected")
        self.ptp_manager.stop()
        self.communication_manager.cleanup()
        self.file_transfer = None
        self.is_running = False
        self.is_streaming = False


    # Recording functions
    def start_recording(self) -> Optional[str]:
        """
        Start recording. Should be extended with module-specific implementation.
        Returns the filename if setup was successful, None otherwise.
        """
        # Check not already recording
        if self.is_recording:
            self.logger.info("(MODULE) Already recording")
            self.communication_manager.send_status({
                "type": "recording_start_failed",
                "error": "Already recording"
            })
            return None
        
        # Set up recording - filename and folder
        self.recording_session_id = self.session_manager.generate_session_id(self.module_id)
        self.current_filename = f"{self.recording_folder}/{self.recording_session_id}.{self.recording_filetype}"
        os.makedirs(self.recording_folder, exist_ok=True)
        
        return self.current_filename  # Just return filename, let child class handle status

    def stop_recording(self) -> bool:
        """
        Stop recording. Should be extended with module-specific implementation.
        Returns True if ready to stop, False otherwise.
        """
        # Check if recording
        if not self.is_recording:
            self.logger.info("(MODULE) Already stopped recording")
            self.communication_manager.send_status({
                "type": "recording_stop_failed",
                "error": "Not recording"
            })
            return False
        
        return True  # Just return True, let child class handle status

    def _get_recordings_list(self):
        """Internal method to get list of recordings with metadata"""
        try:
            recordings = []
            if not os.path.exists(self.recording_folder):
                return []
                
            for filename in os.listdir(self.recording_folder):
                filepath = os.path.join(self.recording_folder, filename)
                stat = os.stat(filepath)
                recordings.append({
                    "filename": filename,
                    "size": stat.st_size,
                    "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                })
            
            # Sort by creation time, newest first
            recordings.sort(key=lambda x: x["created"], reverse=True)
            return recordings
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error getting recordings list: {e}")
            raise

    def list_recordings(self):
        """List all recorded files with metadata and send to controller"""
        try:
            recordings = self._get_recordings_list()
            
            # Send status response
            self.communication_manager.send_status({
                "type": "recordings_list",
                "recordings": recordings
            })
            
            return recordings
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error listing recordings: {e}")
            self.communication_manager.send_status({
                "type": "recordings_list_failed",
                "error": str(e)
            })
            raise

    def clear_recordings(self, older_than: int = None, keep_latest: int = 0):
        """Clear old recordings
        
        Args:
            older_than: Optional timestamp - delete recordings older than this
            keep_latest: Optional number of latest recordings to keep
            
        Returns:
            dict with deleted_count and kept_count
        """
        try:
            if not os.path.exists(self.recording_folder):
                return {"deleted_count": 0, "kept_count": 0}
                
            # Get list of recordings using internal method
            recordings = self._get_recordings_list()
            if not recordings:
                return {"deleted_count": 0, "kept_count": 0}
                
            # Sort by creation time, newest first
            recordings.sort(key=lambda x: x["created"], reverse=True)
            
            deleted_count = 0
            kept_count = 0
            
            # Keep the latest N recordings if specified
            if keep_latest > 0:
                kept_recordings = recordings[:keep_latest]
                recordings = recordings[keep_latest:]
                kept_count = len(kept_recordings)
            
            # Delete recordings older than timestamp if specified
            for recording in recordings:
                if older_than and recording["created"] >= older_than:
                    continue
                    
                try:
                    filepath = os.path.join(self.recording_folder, recording["filename"])
                    os.remove(filepath)
                    # Also try to remove associated timestamp file if it exists
                    base_name = os.path.splitext(recording["filename"])[0]
                    timestamp_file = os.path.join(self.recording_folder, f"{base_name}_timestamps.txt")
                    if os.path.exists(timestamp_file):
                        os.remove(timestamp_file)
                    deleted_count += 1
                except Exception as e:
                    self.logger.error(f"(MODULE) Error deleting recording {recording['filename']}: {e}")
            
            return {
                "deleted_count": deleted_count,
                "kept_count": kept_count
            }
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error clearing recordings: {e}")
            raise

    def export_recordings(self, filename: str, length: int = 0, destination: ExportManager.ExportDestination = ExportManager.ExportDestination.CONTROLLER):
        """Export a video to the specified destination
        
        Args:
            filename: Name of the file to export
            length: Optional length of the video
            destination: Where to export to - ExportManager.ExportDestination.CONTROLLER or ExportManager.ExportDestination.NAS
            
        Returns:
            bool: True if export successful
        """
        try:
            if filename == "all":
                # Export all recordings in a single export session
                if not self.export_manager.export_all_files(destination):
                    self.communication_manager.send_status({
                        "type": "export_failed",
                        "filename": "all",
                        "error": "Failed to export files"
                    })
                    return False
                return True
                
            elif filename == "latest":
                # Export latest recording
                latest_recording = self.get_latest_recording()
                if not self.export_manager.export_file(latest_recording["filename"], destination):
                    self.communication_manager.send_status({
                        "type": "export_failed",
                        "filename": latest_recording["filename"],
                        "error": "Failed to export file"
                    })
                    return False
                return True
                
            # Export specific file
            if not self.export_manager.export_file(filename, destination):
                self.communication_manager.send_status({
                    "type": "export_failed",
                    "filename": filename,
                    "error": "Failed to export file"
                })
                return False
                
            # Send success status
            self.communication_manager.send_status({
                "type": "video_export_complete",
                "filename": filename,
                "session_id": self.stream_session_id,
                "length": length,
                "destination": destination.value,
                "has_timestamps": os.path.exists(f"{filename}_timestamps.txt")
            })
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error exporting recordings: {e}")
            self.communication_manager.send_status({
                "type": "export_failed",
                "filename": filename,
                "error": str(e)
            })
            return False

    # Start and stop functions
    def start(self) -> bool:
        """
        Start the module.

        This method should be overridden by the subclass to implement specific module initialization logic.
        
        Returns:
            bool: True if the module started successfully, False otherwise.
        """
        self.logger.info(f"(MODULE) Starting {self.module_type} module {self.module_id}")
        if self.is_running:
            self.logger.info("(MODULE) Module already running")
            return False
        else:
            self.is_running = True
            self.start_time = time.strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(f"(MODULE) Module started at {self.start_time}")
            
            # Update start time in command handler
            self.command_handler.start_time = self.start_time
            
            # Start command listener thread if controller is discovered
            if self.service_manager.controller_ip:
                self.logger.info(f"(MODULE) Attempting to connect to controller at {self.service_manager.controller_ip}")
                # Connect to the controller
                self.communication_manager.connect(
                    self.service_manager.controller_ip,
                    self.service_manager.controller_port
                )
                self.communication_manager.start_command_listener()

                # Start PTP only if it's not already running
                if not self.ptp_manager.running:
                    time.sleep(0.1)
                    self.ptp_manager.start()

                # Start sending heartbeats
                time.sleep(0.1)
                self.health_manager.start_heartbeats()
            
        return True

    def stop(self) -> bool:
        """
        Stop the module.

        This method should be overridden by the subclass to implement specific module shutdown logic.

        Returns:
            bool: True if the module stopped successfully, False otherwise.
        """
        self.logger.info(f"(MODULE) Stopping {self.module_type} module {self.module_id} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if not self.is_running:
            self.logger.info("(MODULE) Module already stopped")
            return False

        try:
            # First: Clean up command handler (stops streaming and thread)
            self.logger.info("(MODULE) Cleaning up command handler...")
            self.command_handler.cleanup()
            
            # Second: Stop the health manager (and its heartbeat thread)
            self.logger.info("(MODULE) Stopping health manager...")
            self.health_manager.stop_heartbeats()

            # Third: Stop PTP manager
            self.logger.info("(MODULE) Stopping PTP manager...")
            self.ptp_manager.stop()

            # Fourth: Stop the service manager (doesn't use ZMQ directly)
            self.logger.info("(MODULE) Cleaning up service manager...")
            self.service_manager.cleanup()
            
            # Fifth: Stop the communication manager (ZMQ cleanup)
            self.logger.info("(MODULE) Cleaning up communication manager...")
            self.communication_manager.cleanup()

            # Unmount any mounted destination
            if hasattr(self, 'export_manager'):
                self.export_manager.unmount()

        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping module: {e}")
            return False

        # Confirm the module is stopped
        self.is_running = False
        self.logger.info(f"(MODULE) Module stopped at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return True

    def generate_module_id(self, module_type: str) -> str:
        """Generate a module ID based on the module type and the MAC address"""
        mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
        short_id = mac[-4:]  # Takes last 4 characters
        return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"    
