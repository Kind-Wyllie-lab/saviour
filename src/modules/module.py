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
from typing import Dict, Any, Optional, Union
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
        
        # Setup logging first
        self.logger = logging.getLogger(f"{self.module_type}.{self.module_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.info(f"Initializing {self.module_type} module {self.module_id}")
        
        # Create recording folder if it doesn't exist
        if not os.path.exists(self.recording_folder):
            os.makedirs(self.recording_folder)
            self.logger.info(f"(MODULE) Created recording folder: {self.recording_folder}")

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
        # TODO: Use a single dict of universal callbacks and pass them as needed? Lots of repetition right now
        self.health_manager.set_callbacks({
            "get_ptp_status": self.ptp_manager.get_status,
            "get_recording_status": lambda: self.is_recording,
            "get_streaming_status": lambda: self.is_streaming,
        })
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
            'export_recordings': self.export_recordings,
            'list_commands': self.list_commands,
            'get_config': self.config_manager.get_all, # Gets the complete config from
            'set_config': lambda new_config: self.set_config(new_config, persist=False), # Uses a dict to update the config manfager
            'shutdown': self._shutdown,
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
        
        # Check if we're already connected to this controller
        if (self.communication_manager.controller_ip == controller_ip and 
            self.communication_manager.controller_port == controller_port):
            self.logger.info("(MODULE) Already connected to this controller")
            return
            
        # If we're connected to a different controller, disconnect first
        if self.communication_manager.controller_ip:
            self.logger.info("(MODULE) Connected to different controller, disconnecting first")
            self.controller_disconnected()
            
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
            if not self.communication_manager.connect(controller_ip, controller_port):
                raise Exception("Failed to connect communication manager")
            self.logger.info("(MODULE) Communication manager connected to controller")
            
            # 3. Start command listener
            self.logger.info("(MODULE) Requesting communication manager to start command listener")
            if not self.communication_manager.start_command_listener():
                raise Exception("Failed to start command listener")
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
        
        # Stop recording if active
        if self.is_recording:
            self.logger.info("(MODULE) Stopping recording due to controller disconnect")
            self.stop_recording()
        
        # Stop PTP services
        self.ptp_manager.stop()
        
        # Stop heartbeats before cleaning up communication
        self.health_manager.stop_heartbeats()
        
        # Clean up communication manager (this will recreate ZMQ context and sockets)
        self.communication_manager.cleanup()
        
        # Clean up file transfer
        self.file_transfer = None
        self.is_streaming = False
        
        self.logger.info("(MODULE) Controller disconnection cleanup complete, ready for reconnection")

    # Recording functions
    def start_recording(self, experiment_name: str = None, duration: str = None) -> Optional[str]:
        """
        Start recording. Should be extended with module-specific implementation.
        
        Args:
            experiment_name: Optional experiment name to prefix the filename
            duration: Optional duration parameter (not currently used)
            
        Returns the filename if setup was successful, None otherwise.
        """
        # Check not already recording
        if self.is_recording:
            self.logger.info("(MODULE) Already recording")
            if hasattr(self, 'communication_manager') and self.communication_manager and self.communication_manager.controller_ip:
                self.communication_manager.send_status({
                    "type": "recording_start_failed",
                    "error": "Already recording"
                })
            return None
        
        # Set up recording - filename and folder
        # self.recording_session_id = self.session_manager.generate_session_id(self.module_id)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.recording_session_id = f"{timestamp}_{self.module_id}"
        
        # Use experiment name in filename if provided
        if experiment_name:
            # Sanitize experiment name for filename (remove special characters)
            safe_experiment_name = "".join(c for c in experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_experiment_name = safe_experiment_name.replace(' ', '_')
            self.current_filename = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}.{self.recording_filetype}"
        else:
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
            
            # Ensure recording folder exists
            if not os.path.exists(self.recording_folder):
                self.logger.info(f"(MODULE) Recording folder does not exist, creating: {self.recording_folder}")
                try:
                    os.makedirs(self.recording_folder, exist_ok=True)
                except Exception as e:
                    self.logger.error(f"(MODULE) Error creating recording folder {self.recording_folder}: {e}")
                    return []
            
            # Check if we can access the folder
            if not os.access(self.recording_folder, os.R_OK):
                self.logger.error(f"(MODULE) No read permission for recording folder: {self.recording_folder}")
                return []
                
            try:
                for filename in os.listdir(self.recording_folder):
                    try:
                        filepath = os.path.join(self.recording_folder, filename)
                        if os.path.isfile(filepath):  # Only include files, not directories
                            stat = os.stat(filepath)
                            recordings.append({
                                "filename": filename,
                                "size": stat.st_size,
                                "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            })
                    except (OSError, IOError) as e:
                        # Skip files that can't be accessed
                        self.logger.warning(f"(MODULE) Skipping file {filename} due to access error: {e}")
                        continue
                    except Exception as e:
                        # Skip files that cause other errors
                        self.logger.warning(f"(MODULE) Skipping file {filename} due to error: {e}")
                        continue
                
                # Sort by creation time, newest first
                recordings.sort(key=lambda x: x["created"], reverse=True)
                self.logger.info(f"(MODULE) Found {len(recordings)} recordings in {self.recording_folder}")
                return recordings
                
            except (OSError, IOError) as e:
                self.logger.error(f"(MODULE) Error accessing recording folder {self.recording_folder}: {e}")
                return []
                
        except Exception as e:
            self.logger.error(f"(MODULE) Unexpected error getting recordings list: {e}")
            # Don't re-raise the exception - return empty list instead
            return []

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
            # Send error status but don't re-raise the exception
            try:
                self.communication_manager.send_status({
                    "type": "recordings_list_failed",
                    "error": str(e)
                })
            except Exception as send_error:
                self.logger.error(f"(MODULE) Error sending failure status: {send_error}")
            # Return empty list instead of re-raising
            return []

    def clear_recordings(self, filename: str = None, filenames: list = None, older_than: int = None, keep_latest: int = 0):
        """Clear recordings
        
        Args:
            filename: Optional specific filename to delete
            filenames: Optional list of specific filenames to delete
            older_than: Optional timestamp - delete recordings older than this
            keep_latest: Optional number of latest recordings to keep
            
        Returns:
            dict with deleted_count and kept_count
        """
        try:
            if not os.path.exists(self.recording_folder):
                return {"deleted_count": 0, "kept_count": 0}
            
            # If multiple filenames are provided, delete them
            if filenames:
                deleted_count = 0
                for single_filename in filenames:
                    try:
                        filepath = os.path.join(self.recording_folder, single_filename)
                        if os.path.exists(filepath):
                            os.remove(filepath)
                            deleted_count += 1
                            self.logger.info(f"(MODULE) Deleted file: {single_filename}")
                        else:
                            self.logger.warning(f"(MODULE) File not found: {single_filename}")
                    except Exception as e:
                        self.logger.error(f"(MODULE) Error deleting recording {single_filename}: {e}")
                return {"deleted_count": deleted_count, "kept_count": 0}
            
            # If specific filename is provided, delete just that file
            if filename:
                try:
                    filepath = os.path.join(self.recording_folder, filename)
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        return {"deleted_count": 1, "kept_count": 0}
                    else:
                        self.logger.warning(f"(MODULE) File not found: {filename}")
                        return {"deleted_count": 0, "kept_count": 0}
                except Exception as e:
                    self.logger.error(f"(MODULE) Error deleting recording {filename}: {e}")
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

    def export_recordings(self, filename: str, length: int = 0, destination: Union[str, ExportManager.ExportDestination] = ExportManager.ExportDestination.CONTROLLER, experiment_name: str = None):
        """Export a video to the specified destination
        
        Args:
            filename: Name of the file to export (can be comma-separated list)
            length: Optional length of the video
            destination: Where to export to - string or ExportManager.ExportDestination enum
            experiment_name: Optional experiment name to include in export directory
            
        Returns:
            bool: True if export successful
        """
        try:
            # Convert string destination to enum if needed
            if isinstance(destination, str):
                try:
                    destination = ExportManager.ExportDestination.from_string(destination)
                except ValueError as e:
                    self.logger.error(f"(MODULE) Invalid destination '{destination}': {e}")
                    self.communication_manager.send_status({
                        "type": "export_failed",
                        "filename": filename,
                        "error": f"Invalid destination: {destination}"
                    })
                    return False
            
            if filename == "all":
                # Export all recordings in a single export session
                if not self.export_manager.export_all_files(destination, experiment_name):
                    self.communication_manager.send_status({
                        "type": "export_failed",
                        "filename": "all",
                        "error": "Failed to export files",
                        "success": False
                    })
                    return False
                return True
                
            elif filename == "latest":
                # Export latest recording
                latest_recording = self.get_latest_recording()
                if not self.export_manager.export_file(latest_recording["filename"], destination, experiment_name):
                    self.communication_manager.send_status({
                        "type": "export_failed",
                        "filename": latest_recording["filename"],
                        "error": "Failed to export file",
                        "success": False
                    })
                    return False
                return True
            
            # Handle comma-separated filenames
            if ',' in filename:
                filenames = [f.strip() for f in filename.split(',')]
                self.logger.info(f"(MODULE) Exporting multiple files: {filenames}")
                
                # Export each file individually
                for single_filename in filenames:
                    if not self.export_manager.export_file(single_filename, destination, experiment_name):
                        self.communication_manager.send_status({
                            "type": "export_failed",
                            "filename": single_filename,
                            "error": "Failed to export file",
                            "success": False
                        })
                        return False
                
                # Send success status for all files
                self.communication_manager.send_status({
                    "type": "export_complete",
                    "filename": filename,  # Original comma-separated string
                    "session_id": self.stream_session_id,
                    "length": length,
                    "destination": destination.value,
                    "experiment_name": experiment_name,
                    "success": True
                })
                return True
                
            # Export specific single file
            if not self.export_manager.export_file(filename, destination, experiment_name):
                self.communication_manager.send_status({
                    "type": "export_failed",
                    "filename": filename,
                    "error": "Failed to export file",
                    "success": False
                })
                return False
                
            # Send success status
            self.communication_manager.send_status({
                "type": "export_complete",
                "filename": filename,
                "session_id": self.stream_session_id,
                "length": length,
                "destination": destination.value,
                "experiment_name": experiment_name,
                "has_timestamps": os.path.exists(f"{filename}_timestamps.txt"),
                "success": True
            })
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error exporting recordings: {e}")
            self.communication_manager.send_status({
                "type": "export_failed",
                "filename": filename,
                "error": str(e),
                "success": False
            })
            return False

    def list_commands(self):
        """
        Return a dict of zmq commands that the module understands.
        """
        commands = {
            
        }


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
        
        # Wait for proper network connectivity (DHCP-assigned IP)
        if not self._wait_for_network_ready():
            self.logger.error("(MODULE) Failed to get proper network connectivity within timeout")
            return False
        
        # Register service with proper IP address
        if not self.service_manager.register_service():
            self.logger.error("(MODULE) Failed to register service")
            return False
        
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

    def _wait_for_network_ready(self, timeout: int = 60, check_interval: float = 2.0) -> bool:
        """
        Wait for proper network connectivity with DHCP-assigned IP address.
        
        Args:
            timeout: Maximum time to wait in seconds (default: 60)
            check_interval: Time between checks in seconds (default: 2.0)
            
        Returns:
            bool: True if proper IP is obtained, False if timeout
        """
        self.logger.info(f"(MODULE) Waiting for proper network connectivity (timeout: {timeout}s)")
        
        start_time = time.time()
        attempts = 0
        
        while time.time() - start_time < timeout:
            attempts += 1
            
            try:
                # Get all IP addresses
                result = subprocess.run(['hostname', '-I'], 
                                      capture_output=True, text=True, timeout=5)
                
                if result.returncode == 0:
                    ip_addresses = result.stdout.strip().split()
                    
                    # Check for proper DHCP-assigned IP (192.168.x.x)
                    for ip in ip_addresses:
                        if ip.startswith('192.168.'):
                            self.logger.info(f"(MODULE) Network ready! Got IP: {ip} (attempt {attempts})")
                            return True
                    
                    # Log current IPs for debugging
                    if ip_addresses:
                        self.logger.info(f"(MODULE) Attempt {attempts}: Current IPs: {ip_addresses}")
                    else:
                        self.logger.info(f"(MODULE) Attempt {attempts}: No IP addresses found")
                        
                else:
                    self.logger.warning(f"(MODULE) Attempt {attempts}: hostname -I failed: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                self.logger.warning(f"(MODULE) Attempt {attempts}: hostname -I timed out")
            except Exception as e:
                self.logger.warning(f"(MODULE) Attempt {attempts}: Error checking network: {e}")
            
            # Wait before next check
            time.sleep(check_interval)
        
        self.logger.error(f"(MODULE) Network readiness timeout after {attempts} attempts")
        return False

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

    def _shutdown(self): 
        """Shut down the module"""
        try:
            # Stop the module gracefully
            if self.stop():
                # Only shutdown system if module stopped successfully
                subprocess.run(["sudo", "shutdown", "now"])
            else:
                self.logger.error("(MODULE) Failed to stop module, not shutting down system")
        except Exception as e:
            self.logger.error(f"(MODULE) Error during shutdown: {e}")

    def set_config(self, new_config: dict, persist: bool = False) -> bool:
        """
        Set the entire configuration from a dictionary
        
        Args:
            new_config: Dictionary containing the new configuration
            persist: Whether to persist the changes to the config file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate that new_config is a dictionary
            if not isinstance(new_config, dict):
                self.logger.error(f"(MODULE) set_config called with non-dict argument: {type(new_config)}")
                return False
            
            # Use the config manager's merge method to update the config
            self.config_manager._merge_configs(self.config_manager.config, new_config)
            
            # Persist to file if requested
            if persist:
                return self.config_manager.save_config()
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting all config: {e}")
            return False


    def generate_module_id(self, module_type: str) -> str:
        """Generate a module ID based on the module type and the MAC address"""
        mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
        short_id = mac[-4:]  # Takes last 4 characters
        return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"    
