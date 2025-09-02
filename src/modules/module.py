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
import logging
import uuid
import threading
from typing import Dict, Any, Optional, Union
import datetime

# Check if running under systemd
is_systemd = os.environ.get('INVOCATION_ID') is not None

# Setup logging ONCE for all modules
if is_systemd:
    # Under systemd, let it handle timestamps
    format_string = '%(levelname)s - %(name)s - %(message)s'
else:
    # When running directly, include timestamps
    format_string = '%(asctime)s - %(levelname)s - %(name)s - %(message)s'

# Setup logging ONCE for all additional classes that log
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(name)s - %(message)s'
)

# Import managers
from src.modules.config import Config
from src.modules.communication import Communication
from src.modules.health import Health
from src.modules.command import Command
from src.modules.network import Network
from src.modules.ptp import PTP, PTPRole
from src.modules.export import Export

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
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initializing {self.module_type} module {self.module_id}")
        
        # Create recording folder if it doesn't exist
        if not os.path.exists(self.recording_folder):
            os.makedirs(self.recording_folder, exist_ok=True)
            self.logger.info(f"Created recording folder: {self.recording_folder}")


        # Add file handler if none exists
        if not self.logger.handlers:
            # Add file handler for persistent logging (useful when running as systemd service)
            try:
                # Check if file logging is enabled in config
                if self.config.get("logging.to_file", True):
                    # Create logs directory if it doesn't exist
                    log_dir = self.config.get("logging.directory", "/var/log/habitat")
                    os.makedirs(log_dir, exist_ok=True)
                    
                    # Generate log filename with module info
                    log_filename = f"{self.module_type}_{self.module_id}.log"
                    log_filepath = os.path.join(log_dir, log_filename)
                    
                    # Get config values for rotation
                    max_bytes = self.config.get("logging.max_file_size_mb", 10) * 1024 * 1024
                    backup_count = self.config.get("logging.backup_count", 5)
                    
                    # Add file handler with rotation
                    from logging.handlers import RotatingFileHandler
                    file_handler = RotatingFileHandler(
                        log_filepath,
                        maxBytes=max_bytes,
                        backupCount=backup_count
                    )
                    file_handler.setLevel(logging.INFO)
                    self.logger.addHandler(file_handler)
                    
                    self.logger.info(f"File logging enabled: {log_filepath}")
                    self.logger.info(f"Log rotation: max {max_bytes//(1024*1024)}MB, keep {backup_count} backups")
                else:
                    self.logger.info("File logging disabled in config")
                    
            except Exception as e:
                # If file logging fails, log the error but don't crash
                self.logger.warning(f"Failed to setup file logging: {e}")
                self.logger.info("Continuing with console logging only")

        # Managers
        self.logger.info(f"Initialising managers")
        self.logger.info(f"Initialising config manager")
        self.config = Config(self.module_type, self.config_path)
        self.export = Export(
            module_id=self.module_id,
            recording_folder=self.recording_folder,
            config=self.config.get_all(),
        )
        self.logger.info(f"Initialising communication manager")
        self.communication = Communication(         # Communication manager - handles ZMQ messaging
            self.module_id, # Pass in the module ID for use in messages
            config=self.config # Pass in the config manager for getting properties
        )
        self.logger.info(f"Initialising health manager")
        self.health = Health(
            config=self.config
        )
        self.logger.info(f"Initialising PTP manager")
        self.ptp = PTP(
            role=PTPRole.SLAVE)
        if not hasattr(self, 'command'): # Initialize command handler if not already set - extensions of module class might set their own command handler
            self.logger.info(f"Initialising command handler")
            self.command = Command(
                self.module_id,
                self.module_type,
                config=self.config,
                start_time=None # Will be set during start()
            )

        self.logger.info(f"Initialising network manager")

        self.network = Network(self.config, module_id=self.module_id, module_type=self.module_type)

        # Register Callbacks
        self.callbacks = { # Define a universal set of callbacks
            'generate_session_id': lambda module_id: self.generate_session_id(module_id), # 
            'get_controller_ip': lambda: self.network.controller_ip,  # or whatever the callback function is
            'get_samplerate': lambda: self.config.get("module.samplerate", 200), # Use a lambda function to get it fresh from the config manager every time
            'get_ptp_status': self.ptp.get_status, # Use a lambda function to get status fresh from ptp manager everytime
            'restart_ptp': self.ptp.restart, # Restart PTP services
            'get_streaming_status': lambda: self.is_streaming,
            'get_recording_status': lambda: self.is_recording,
            'send_status': lambda status: self.communication.send_status(status),
            'get_health': self.health.get_health,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'list_recordings': self.list_recordings,
            'clear_recordings': self._clear_recordings,
            'export_recordings': self.export_recordings,
            'list_commands': self.list_commands,
            'handle_command': self.command.handle_command, 
            'get_config': self.config.get_all, # Gets the complete config from
            'set_config': lambda new_config: self.set_config(new_config, persist=True), # Uses a dict to update the config manager
            'validate_readiness': self.validate_readiness, # Validate module readiness for recording
            'get_log_file_path': self.get_log_file_path, # Get current log file path
            'shutdown': self._shutdown,
            'when_controller_discovered': self.when_controller_discovered,
            'controller_disconnected': self.controller_disconnected
        }
        self.network.set_callbacks(self.callbacks)
        self.health.set_callbacks(self.callbacks)
        self.communication.set_callbacks(self.callbacks)
        self.command.set_callbacks(self.callbacks)
        self.export.set_callbacks(self.callbacks)
        
        # Recording management
        self.recording_session_id = None
        self.current_filename = None
        self.session_files = []

        # Parameters from config
        self.samplerate = self.config.get("module.samplerate")
        self.recording_folder = self.config.get("recording_folder")
        self.recording_filetype = self.config.get("recording_filetype") # Find the appropriate filetype for this module type, 

        # Control State flags
        self.is_running = False  # Start as False
        self.is_recording = False # Flag to indicate if the module is recording e.g. video, TTL, audio, etc.
        self.is_streaming = False # Flag to indicate if the module is streaming on a network port e.g. video, TTL, audio, etc.
        self.is_connected_to_controller = False
        self.is_ready = False  # Flag to indicate if module is ready for recording
        self.last_readiness_check = None  # Timestamp of last readiness check 

        # Track when module started for uptime calculation
        self.start_time = None

    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        """Callback when controller is discovered via zeroconf"""
        self.logger.info(f"Network manager informs that controller was discovered at {controller_ip}:{controller_port}")
        self.logger.info(f"Module will now initialize the necessary managers")
        
        # Check if we're already connected to this controller
        if (self.communication.controller_ip == controller_ip and 
            self.communication.controller_port == controller_port):
            self.logger.info("Already connected to this controller")
            return
            
        # If we're connected to a different controller, disconnect first
        if self.communication.controller_ip:
            self.logger.info("Connected to different controller, disconnecting first")
            self.controller_disconnected()
            
        try:
            
            # 2. Connect communication manager
            self.logger.info("Connecting communication manager to controller")
            if not self.communication.connect(controller_ip, controller_port):
                raise Exception("Failed to connect communication manager")
            self.logger.info("Communication manager connected to controller")
            
            # 3. Start command listener
            self.logger.info("Requesting communication manager to start command listener")
            if not self.communication.start_command_listener():
                raise Exception("Failed to start command listener")
            self.logger.info("Command listener started")
            
            # 4. Start heartbeats if module is running
            if self.is_running:
                self.logger.info("Requesting health manager to start heartbeats")
                self.health.start_heartbeats()
                self.logger.info("Heartbeats started")
            
            # 5. Start 
            self.logger.info("Starting PTP manager")
            self.ptp.start()
            
            self.logger.info("Controller connection and initialization complete")

            self.is_connected_to_controller = True
            
        except Exception as e:
            self.logger.error(f"Error during controller initialization: {e}")
            # Clean up any partial initialization
            self.communication.cleanup()
            self.file_transfer = None
            raise

    def controller_disconnected(self):
        """Callback when controller is disconnected"""
        # What should happen here - we don't want to stop the module altogether, we want to stop recording, deregister controller, and wait for new controller connection.
        self.logger.info("Controller disconnected")
        
        self.is_connected_to_controller = False

        # Stop recording if active
        if self.is_recording:
            self.logger.info("Stopping recording due to controller disconnect")
            self.stop_recording()
        
        # Stop PTP services
        self.ptp.stop()
        
        # Stop heartbeats before cleaning up communication
        self.health.stop_heartbeats()
        
        # Clean up communication manager (this will recreate ZMQ context and sockets)
        self.communication.cleanup()
        
        # Clean up file transfer
        self.file_transfer = None
        self.is_streaming = False
        
        self.logger.info("Controller disconnection cleanup complete, ready for reconnection")

    # Recording functions
    def start_recording(self, experiment_name: str = None, duration: str = None, experiment_folder: str = None, controller_share_path: str = None) -> Optional[str]:
        """
        Start recording. Should be extended with module-specific implementation.
        
        Args:
            experiment_name: Optional experiment name to prefix the filename
            duration: Optional duration parameter (not currently used)
            experiment_folder: Optional experiment folder name for export
            controller_share_path: Optional controller share path for export
            
        Returns the filename if setup was successful, None otherwise.
        """
        # Check not already recording
        if self.is_recording:
            self.logger.info("Already recording")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_start_failed",
                    "error": "Already recording"
                })
            return None
        
        # Store experiment folder information for export
        self.current_experiment_folder = experiment_folder
        self.controller_share_path = controller_share_path
        
        # Set up recording - filename and folder
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

        # TODO: Start generating health metadata to go with file
        
        return self.current_filename  # Just return filename, let child class handle status

    def stop_recording(self) -> bool:
        """
        Stop recording. Should be extended with module-specific implementation.
        Returns True if ready to stop, False otherwise.
        """
        try:
            # Check if recording
            if not self.is_recording:
                self.logger.info("Already stopped recording")
                self.communication.send_status({
                    "type": "recording_stop_failed",
                    "error": "Not recording"
                })
                return False
            
            # Get session files
            self._get_session_files()

        except Exception as e:
            self.logger.error(f"Error in stop_recording: {e}")
            return False
        return True  # Just return True, let child class handle the rest

    def _get_session_files(self):
        # Find files that belong to the current recording session
        self.session_files = []
        self.logger.info(f"About to check for session files, program running in {os.getcwd()}")
        self.logger.info(f"Looking for recordings in {self.recording_folder}")
        self.logger.info(f"Recordings are as follows: {os.listdir(self.recording_folder)}")
        for filename in os.listdir(self.recording_folder):
            self.logger.info(f"Examining file {filename} for auto export, trying to match {self.recording_session_id}")
            if self.recording_session_id in filename:
                self.session_files.append(filename)
                self.logger.info(f"Found session file to export: {filename}")

    def _auto_export(self):
        if self.config.get("auto_export", True) and self.current_filename:
            self.logger.info("Auto-export enabled, exporting recording using export manager")
            try:
                # Use the export manager's method for consistency
                if self.export.export_current_session_files(
                    session_files=self.session_files,
                    recording_folder=self.recording_folder,
                    recording_session_id=self.recording_session_id,
                    experiment_name=self.current_experiment_name
                ):
                    self.logger.info("Auto-export completed successfully")

                    if self.config.get("auto_delete_on_export", True):
                        self._clear_recordings(filenames=self.session_files)
                else:
                    self.logger.warning("Auto-export failed, but recording was successful")
            except Exception as e:
                self.logger.error(f"Auto-export error: {e}")
        else:
            self.logger.info("Auto-export disabled, not exporting recording")

    def _get_recordings_list(self):
        """Internal method to get list of recordings with metadata"""
        try:
            recordings = []
            
            # Ensure recording folder exists
            if not os.path.exists(self.recording_folder):
                self.logger.info(f"Recording folder does not exist, creating: {self.recording_folder}")
                try:
                    os.makedirs(self.recording_folder, exist_ok=True)
                except Exception as e:
                    self.logger.error(f"Error creating recording folder {self.recording_folder}: {e}")
                    return []
            
            # Check if we can access the folder
            if not os.access(self.recording_folder, os.R_OK):
                self.logger.error(f"No read permission for recording folder: {self.recording_folder}")
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
                        self.logger.warning(f"Skipping file {filename} due to access error: {e}")
                        continue
                    except Exception as e:
                        # Skip files that cause other errors
                        self.logger.warning(f"Skipping file {filename} due to error: {e}")
                        continue
                
                # Sort by creation time, newest first
                recordings.sort(key=lambda x: x["created"], reverse=True)
                self.logger.info(f"Found {len(recordings)} recordings in {self.recording_folder}")
                return recordings
                
            except (OSError, IOError) as e:
                self.logger.error(f"Error accessing recording folder {self.recording_folder}: {e}")
                return []
                
        except Exception as e:
            self.logger.error(f"Unexpected error getting recordings list: {e}")
            # Don't re-raise the exception - return empty list instead
            return []

    def list_recordings(self):
        """List all recorded files with metadata and send to controller"""
        try:
            recordings = self._get_recordings_list()
            
            # Send status response
            self.communication.send_status({
                "type": "recordings_list",
                "recordings": recordings
            })
            
            return recordings
            
        except Exception as e:
            self.logger.error(f"Error listing recordings: {e}")
            # Send error status but don't re-raise the exception
            try:
                self.communication.send_status({
                    "type": "recordings_list_failed",
                    "error": str(e)
                })
            except Exception as send_error:
                self.logger.error(f"Error sending failure status: {send_error}")
            # Return empty list instead of re-raising
            return []

    def _clear_recordings(self, filename: str = None, filenames: list = None, older_than: int = None, keep_latest: int = 0):
        """Clear recordings
        
        Args:
            filename: Optional specific filename to delete
            filenames: Optional list of specific filenames to delete
            older_than: Optional timestamp - delete recordings older than this
            keep_latest: Optional number of latest recordings to keep
            
        Returns:
            dict with deleted_count and kept_count
        """
        self.logger.info("Attempting to clear recordings")
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
                            self.logger.info(f"Deleted file: {single_filename}")
                        else:
                            self.logger.warning(f"File not found: {single_filename}")
                    except Exception as e:
                        self.logger.error(f"Error deleting recording {single_filename}: {e}")
                return {"deleted_count": deleted_count, "kept_count": 0}
            
            # If specific filename is provided, delete just that file
            if filename:
                try:
                    filepath = os.path.join(self.recording_folder, filename)
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        return {"deleted_count": 1, "kept_count": 0}
                    else:
                        self.logger.warning(f"File not found: {filename}")
                        return {"deleted_count": 0, "kept_count": 0}
                except Exception as e:
                    self.logger.error(f"Error deleting recording {filename}: {e}")
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
                    self.logger.error(f"Error deleting recording {recording['filename']}: {e}")
            
            self.logger.info("Deleteted {deleted_count} recordings")

            return {
                "deleted_count": deleted_count,
                "kept_count": kept_count
            }
            
        except Exception as e:
            self.logger.error(f"Error clearing recordings: {e}")
            raise

    def export_recordings(self, filename: str, length: int = 0, destination: Union[str, Export.ExportDestination] = Export.ExportDestination.CONTROLLER, experiment_name: str = None):
        """Export a video to the specified destination
        
        Args:
            filename: Name of the file to export (can be comma-separated list)
            length: Optional length of the video
            destination: Where to export to - string or Export.ExportDestination enum
            experiment_name: Optional experiment name to include in export directory
            
        Returns:
            bool: True if export successful
        """
        try:
            # Use experiment folder if available from recording session
            if hasattr(self, 'current_experiment_folder') and self.current_experiment_folder:
                experiment_name = self.current_experiment_folder
                self.logger.info(f"Using experiment folder for export: {experiment_name}")
            
            # Convert string destination to enum if needed
            if isinstance(destination, str):
                try:
                    destination = Export.ExportDestination.from_string(destination)
                except ValueError as e:
                    self.logger.error(f"Invalid destination '{destination}': {e}")
                    self.communication.send_status({
                        "type": "export_failed",
                        "filename": filename,
                        "error": f"Invalid destination: {destination}"
                    })
                    return False
            
            if filename == "all":
                # Export all recordings in a single export session
                if not self.export.export_all_files(destination, experiment_name):
                    self.communication.send_status({
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
                if not self.export.export_file(latest_recording["filename"], destination, experiment_name):
                    self.communication.send_status({
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
                self.logger.info(f"Exporting multiple files: {filenames}")
                
                # Export each file individually
                for single_filename in filenames:
                    if not self.export.export_file(single_filename, destination, experiment_name):
                        self.communication.send_status({
                            "type": "export_failed",
                            "filename": single_filename,
                            "error": "Failed to export file",
                            "success": False
                        })
                        return False
                
                # Send success status for all files
                self.communication.send_status({
                    "type": "export_complete",
                    "filename": filename,  # Original comma-separated string
                    "session_id": self.recording_session_id,
                    "length": length,
                    "destination": destination.value,
                    "experiment_name": experiment_name,
                    "success": True
                })
                self._clear_recordings()
                return True
                
            # Export specific single file
            if not self.export.export_file(filename, destination, experiment_name):
                self.communication.send_status({
                    "type": "export_failed",
                    "filename": filename,
                    "error": "Failed to export file",
                    "success": False
                })
                return False
                
            # Send success status
            self.communication.send_status({
                "type": "export_complete",
                "filename": filename,
                "session_id": self.recording_session_id,
                "length": length,
                "destination": destination.value,
                "experiment_name": experiment_name,
                "has_timestamps": os.path.exists(f"{filename}_timestamps.txt"),
                "success": True
            })
            return True
            
        except Exception as e:
            self.logger.error(f"Error exporting recordings: {e}")
            self.communication.send_status({
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
        self.logger.info(f"Starting {self.module_type} module {self.module_id}")
        if self.is_running:
            self.logger.info("Module already running")
            return False
        
        # Wait for proper network connectivity (DHCP-assigned IP)
        if not self._wait_for_network_ready():
            self.logger.error("Failed to get proper network connectivity")
            return False
        
        # Register service with proper IP address
        if not self.network.register_service():
            self.logger.error("Failed to register service")
            return False
        
        self.is_running = True
        self.start_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info(f"Module started at {self.start_time}")
        
        # Update start time in command handler
        self.command.start_time = self.start_time
        
        # Start command listener thread if controller is discovered
        if self.network.controller_ip:
            self.logger.info(f"Attempting to connect to controller at {self.network.controller_ip}")
            # Connect to the controller
            self.communication.connect(
                self.network.controller_ip,
                self.network.controller_port
            )
            self.communication.start_command_listener()

            # Start PTP only if it's not already running
            if not self.ptp.running:
                time.sleep(0.1)
                self.ptp.start()

            # Start sending heartbeats
            time.sleep(0.1)
            self.health.start_heartbeats()
        
        return True

    def _wait_for_network_ready(self, check_interval: float = 2.0) -> bool:
        """
        Wait for proper network connectivity with DHCP-assigned IP address.
        Will keep trying indefinitely until a proper IP is obtained.
        
        Args:
            check_interval: Time between checks in seconds (default: 2.0)
            
        Returns:
            bool: True if proper IP is obtained (will always return True eventually)
        """
        self.logger.info(f"Waiting for proper network connectivity (will keep trying until IP is obtained)")
        
        attempts = 0
        
        while True:
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
                            self.logger.info(f"Network ready! Got IP: {ip} (attempt {attempts})")
                            return True
                    
                    # Log current IPs for debugging
                    if ip_addresses:
                        self.logger.info(f"Attempt {attempts}: Current IPs: {ip_addresses}")
                    else:
                        self.logger.info(f"Attempt {attempts}: No IP addresses found")
                        
                else:
                    self.logger.warning(f"Attempt {attempts}: hostname -I failed: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Attempt {attempts}: hostname -I timed out")
            except Exception as e:
                self.logger.warning(f"Attempt {attempts}: Error checking network: {e}")
            
            # Wait before next check
            time.sleep(check_interval)

    def stop(self) -> bool:
        """
        Stop the module.

        This method should be overridden by the subclass to implement specific module shutdown logic.

        Returns:
            bool: True if the module stopped successfully, False otherwise.
        """
        self.logger.info(f"Stopping {self.module_type} module {self.module_id} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if not self.is_running:
            self.logger.info("Module already stopped")
            return False

        try:
            # First: Clean up command handler (stops streaming and thread)
            self.logger.info("Cleaning up command handler...")
            self.command.cleanup()
            
            # Second: Stop the health manager (and its heartbeat thread)
            self.logger.info("Stopping health manager...")
            self.health.stop_heartbeats()

            # Third: Stop PTP manager
            self.logger.info("Stopping PTP manager...")
            self.ptp.stop()

            # Fourth: Stop the service manager (doesn't use ZMQ directly)
            self.logger.info("Cleaning up network manager...")
            self.network.cleanup()
            
            # Fifth: Stop the communication manager (ZMQ cleanup)
            self.logger.info("Cleaning up communication manager...")
            self.communication.cleanup()

            # Unmount any mounted destination
            if hasattr(self, 'export'):
                self.export.unmount()

        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False

        # Confirm the module is stopped
        self.is_running = False
        self.logger.info(f"Module stopped at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return True
    
    def generate_session_id(self, module_id="unknown"):
        """Start a new session for a module"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"REC_{timestamp}_{module_id}" # Generate a new session ID

    def _shutdown(self): 
        """Shut down the module"""
        try:
            # Stop the module gracefully
            if self.stop():
                # Only shutdown system if module stopped successfully
                subprocess.run(["sudo", "shutdown", "now"])
            else:
                self.logger.error("Failed to stop module, not shutting down system")
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")

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
                self.logger.error(f"set_config called with non-dict argument: {type(new_config)}")
                return False
            
            # Use the config manager's merge method to update the config
            self.config._merge_configs(self.config.config, new_config)
            
            # Persist to file if requested
            if persist:
                return self.config.save_config()
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting all config: {e}")
            return False


    def _get_required_disk_space_mb(self) -> float:
        """
        Get the required disk space in MB for this module.
        Reads from config with fallback to default.
        
        Returns:
            float: Required disk space in MB (default: 100MB)
        """
        return self.config.get("module.required_disk_space_mb", 100.0)
    
    def _get_ptp_offset_threshold_us(self) -> float:
        """
        Get the maximum acceptable PTP offset in microseconds.
        Reads from config with fallback to default.
        
        Returns:
            float: Maximum acceptable PTP offset in microseconds (default: 1000μs = 1ms)
        """
        return self.config.get("module.ptp_offset_threshold_us", 1000.0)
    
    def _perform_module_specific_checks(self, checks: dict) -> tuple[bool, str]:
        """
        Perform module-specific readiness checks.
        Subclasses should override this method to add their own validation.
        
        Args:
            checks: Dictionary to store check results
            
        Returns:
            tuple: (ready: bool, error_msg: str or None)
        """
        # Base implementation - no module-specific checks
        return True, None
    
    def validate_readiness(self) -> dict:
        """
        Validate that the module is ready for recording.
        
        This base implementation performs common checks that all modules should pass.
        Subclasses should override _perform_module_specific_checks() to add module-specific validation.
        
        Returns:
            dict: {
                'ready': bool,
                'timestamp': float,
                'checks': dict,  # Detailed results of each check
                'error': str     # Error message if not ready (optional)
            }
        """
        self.logger.info(f"Performing readiness validation for {self.module_type} module")
        
        checks = {}
        ready = True
        error_msg = None
        
        try:
            # Check 1: Module is running
            checks['module_running'] = self.is_running
            if not self.is_running:
                ready = False
                error_msg = "Module is not running"
            
            # Check 2: Recording folder exists and is writable
            if ready:
                try:
                    if not os.path.exists(self.recording_folder):
                        os.makedirs(self.recording_folder, exist_ok=True)
                    # Test write access
                    test_file = os.path.join(self.recording_folder, '.test_write')
                    with open(test_file, 'w') as f:
                        f.write('test')
                    os.remove(test_file)
                    checks['recording_folder_writable'] = True
                except Exception as e:
                    checks['recording_folder_writable'] = False
                    ready = False
                    error_msg = f"Recording folder not writable: {str(e)}"
            
            # Check 3: Sufficient disk space (configurable requirement)
            if ready:
                try:
                    statvfs = os.statvfs(self.recording_folder)
                    free_bytes = statvfs.f_frsize * statvfs.f_bavail
                    free_mb = free_bytes / (1024 * 1024)
                    required_mb = self._get_required_disk_space_mb()
                    
                    checks['disk_space_mb'] = free_mb
                    checks['required_disk_space_mb'] = required_mb
                    checks['sufficient_disk_space'] = free_mb >= required_mb
                    
                    if free_mb < required_mb:
                        ready = False
                        error_msg = f"Insufficient disk space: {free_mb:.1f}MB free (need at least {required_mb:.1f}MB)"
                except Exception as e:
                    checks['sufficient_disk_space'] = False
                    ready = False
                    error_msg = f"Cannot check disk space: {str(e)}"
            
            # Check 4: PTP time synchronization is working (configurable threshold)
            # TODO: Improve this check - don't think it's currently working
            if ready:
                try:
                    ptp_status = self.ptp.get_status()
                    checks['ptp_status'] = ptp_status
                    
                    # Check if PTP offset is reasonable (configurable threshold)
                    max_offset_us = self._get_ptp_offset_threshold_us()
                    if 'offset' in ptp_status and abs(ptp_status['offset']) > max_offset_us:
                        checks['ptp_synchronized'] = False
                        ready = False
                        error_msg = f"PTP not synchronized: offset {ptp_status['offset']}μs (max: {max_offset_us}μs)"
                    else:
                        checks['ptp_synchronized'] = True
                except Exception as e:
                    checks['ptp_synchronized'] = False
                    ready = False
                    error_msg = f"PTP check failed: {str(e)}"
            
            # Check 5: Not currently recording
            checks['not_recording'] = not self.is_recording
            if self.is_recording:
                ready = False
                error_msg = "Module is currently recording"
            
            # Check 6: Module-specific checks
            if ready:
                module_ready, module_error = self._perform_module_specific_checks(checks)
                if not module_ready:
                    ready = False
                    error_msg = module_error
            
            # Update module state
            self.is_ready = ready
            self.last_readiness_check = time.time()
            
            result = {
                'ready': ready,
                'timestamp': self.last_readiness_check,
                'checks': checks
            }
            
            if error_msg:
                result['error'] = error_msg
            
            # Log the result
            if ready:
                self.logger.info(f"Readiness validation PASSED for {self.module_type} module")
            else:
                self.logger.warning(f"Readiness validation FAILED for {self.module_type} module: {error_msg}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error during readiness validation: {e}")
            self.is_ready = False
            self.last_readiness_check = time.time()
            return {
                'ready': False,
                'timestamp': self.last_readiness_check,
                'checks': checks,
                'error': f"Validation exception: {str(e)}"
            }

    def generate_module_id(self, module_type: str) -> str:
        """Generate a module ID based on the module type and the MAC address"""
        mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
        short_id = mac[-4:]  # Takes last 4 characters
        return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"    

    def get_log_file_path(self) -> str:
        """Get the current log file path if file logging is enabled"""
        try:
            if self.config.get("logging.to_file", True):
                log_dir = self.config.get("logging.directory", "/var/log/habitat")
                log_filename = f"{self.module_type}_{self.module_id}.log"
                return os.path.join(log_dir, log_filename)
            else:
                return "File logging disabled"
        except Exception as e:
            return f"Error getting log path: {e}"
