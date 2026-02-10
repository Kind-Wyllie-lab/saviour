#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Base Module Class

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 17/03/2025
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
import csv
from abc import ABC, abstractmethod 

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
from src.modules.recording import Recording
from src.modules.facade import ModuleFacade

def command(name=None):
    """
    Decorator to mark a method as a command.
    Can be used as @command() or @command(name="foo")
    Commands should return a dict response.

    """
    def decorator(func):
        func._is_command = True
        func._cmd_name = name or func.__name__
        return func
    return decorator


def check(name=None):
    """
    Decorator to mark a method as a ready check.
    Can be used as @command() or @command(name="foo")
    Commands should return a tuple response: bool for whether succeeded, str message describing the result.
    """
    def decorator(func):
        func._is_check = True
        func._cmd_name = name or func.__name__
        return func
    return decorator


class Module(ABC):
    """
    Base class for all modules in the Habitat Controller.

    This class provides common functionality that all hardware modules (camera, microphone, TTL IO, RFID) share.
    It handles network communication with the main controller, PTP synchronization, power management, health monitoring, and basic lifecycle operations.

    Attributes:
        module_id (str): Unique identifier for the module
        module_type (str): Type of module (camera, microphone, ttl_io, rfid)
        config (dict): Configuration parameters for the module

    """
    def __init__(self, module_type: str):
        # Module type
        self.module_type = module_type
        self.module_id = self.generate_module_id(self.module_type)
        self.description = "No description" # A human readable description to be overridden by child classes
        self.version = self._get_version()
        
        # Setup logging first
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initializing {self.module_type} module {self.module_id}, SAVIOUR {self.version}")

        # Manager objects
        self.config = Config()
        self.export = Export(module_id=self.module_id, config=self.config) # Export object - exports to samba share
        self.communication = Communication(self.module_id, config=self.config) # Communication object - handles ZMQ messaging
        self.health = Health(config=self.config) # Health object - monitors system health e.g. temperature, resource utilisation, ptp sync
        self.ptp = PTP(role=PTPRole.SLAVE) # PTP object - initialises ptp sync
        self.recording = Recording(config=self.config) # Recording object - sets up, maintains and manages recording sessions
        self.command = Command(config=self.config) # Command object - routes incoming commands
        self.network = Network(self.config, module_id=self.module_id, module_type=self.module_type) # Network object - registers zeroconf service, discovers controller
        self.facade = ModuleFacade(module=self) # API object - provides internal routing between objects e.g. network and recording

        # A registry of commands that the module can respond to
        self.command_callbacks = { 
            'restart_ptp': self.ptp.restart, # Restart PTP services
            'get_health': self.health.get_health,
            'start_recording': self.recording.start_recording,
            'stop_recording': self.recording.stop_recording,
            'list_recordings': self.list_recordings,
            'list_commands': self.list_commands,
            'get_config': self.get_config, # Gets the complete config from
            'set_config': lambda config: self.set_config(config, persist=True), # Uses a dict to update the config manager
            'validate_readiness': self.validate_readiness, # Validate module readiness for recording
            'shutdown': self._shutdown,
        }

        # Register callbacks and facade
        self.config.on_module_config_change = self.on_module_config_change
        self.network.facade = self.facade
        self.health.facade = self.facade
        self.communication.facade = self.facade
        self.command.facade = self.facade
        self.export.facade = self.facade
        self.recording.facade = self.facade
    
        # Register commands with command router
        self.command.set_commands(self.command_callbacks)
        
        # Recording management
        self.recording_session_id = None
        self.current_filename_prefix = None
        self.session_files = []

        # Control State flags
        self.is_running = False  # Start as False
        self.is_recording = False # Flag to indicate if the module is recording e.g. video, TTL, audio, etc.
        self.is_streaming = False # Flag to indicate if the module is streaming on a network port e.g. video, TTL, audio, etc.
        self.is_connected_to_controller = False
        self.is_ready = False  # Flag to indicate if module is ready for recording
        self.last_readiness_check = None  # Timestamp of last readiness check 

        # Ready checks
        self.checks = [
            self._check_running,
            self._check_readwrite,
            self._check_diskspace,
            self._check_ptp,
            self._check_recording
        ]

        # To be overriden by module?
        self.module_checks = []

        self.logger.info(f"Registered these readiness checks: {self.checks}")

        # Track when module started for uptime calculation
        self.start_time = None

        # Log to file if enabled
        if self.config.get("logging.to_file", True):
            self.setup_logger_file_handling() 


    def get_module_name(self) -> str:
        name = self.config.get("module.name")
        if name == "":
            name = self.module_id
        return name


    def setup_logger_file_handling(self) -> None:
        # Add file handler if none exists
        if not self.logger.handlers:
            # Add file handler for persistent logging (useful when running as systemd service)
            try:
                # Create logs directory if it doesn't exist
                log_dir = self.config.get("logging.directory", "/var/log/saviour")
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

            except Exception as e:
                # If file logging fails, log the error but don't crash
                self.logger.warning(f"Failed to setup file logging: {e}")
                self.logger.info("Continuing with console logging only")
        else:
            self.logger.info("Logger file handler was not set up as handlers already exist")


    def on_module_config_change(self, updated_keys: Optional[list[str]]) -> None:
        self.logger.info(f"Received notification that module config changed, calling configure_module() with keys {updated_keys}")
        self.configure_module(updated_keys)


    @abstractmethod
    def configure_module(self, updated_keys: Optional[list[str]]):
        """Gets called when module specific configuration changes e.g. framerate for a camera - allows modules to update their settings when they change"""
        self.logger.warning("No implementation provided for abstract method configure_module")


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
            self._stop_recording()
        
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


    """Recording methods"""
    @abstractmethod
    def _start_new_recording(self) -> bool:
        """To be implemented by subclasses"""
        pass

    
    @abstractmethod
    def _start_next_recording_segment(self) -> bool:
        """To be implemented by subclasses"""
        pass


    @abstractmethod
    def _stop_recording(self) -> bool:
        """To be implemented by subclasses"""
        pass


    """File IO"""
    def _check_file_exists(self, filename: str) -> bool:
        """Check if a file exists in the recording folder."""
        if filename in os.listdir(self.facade.get_recording_folder()):
            return True
        else:
            return False


    @command()
    def list_recordings(self):
        """List all recorded files with metadata and send to controller"""
        try:
            recordings = [] # TODO: Get recordings here
            
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
            time.sleep(1)
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
                        if ip.startswith('192.168.') or ip.startswith('10.0.'):
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


    """Config Methods"""
    @command()
    def get_config(self):
        response = {
            "config": self.config.get_all()
        }
        self.logger.info(f"Get config called, returning config with {len(response['config'])} keys")
        return response


    @command()
    def set_config(self, config: dict, persist: bool = True) -> bool:
        """
        Set the entire configuration from a dictionary
        
        Args:
            config: Dictionary containing the new configuration
            persist: Whether to persist the changes to the config file
            
        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Set config called with config {config} and persist {persist}")
        try:
            # Validate that config is a dictionary
            if not isinstance(config, dict):
                self.logger.error(f"set_config called with non-dict argument: {type(config)}")
                return False
            
            # Use the config manager's merge method to update the config
            self.config.set_all(config, persist=persist)
            return {"result": "success", "config": self.config.get_all()}
        except Exception as e:
            self.logger.error(f"Error setting all config: {e}")
            return {"result": f"Error setting all config: {e}"}


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
        return self.config.get("module.ptp_offset_threshold_us", 1000000.0)
    

    """Ready to record checks"""
    def _perform_module_specific_checks(self) -> tuple[bool, str]:
        """
        Perform module-specific readiness checks.
        Subclasses should override this method to add their own validation.
        
        Args:
            checks: Dictionary to store check results
            
        Returns:
            tuple: (ready: bool, error_msg: str or None)
        """
        self.logger.info(f"Performing {self.module_type} specific checks")
        for check in self.module_checks:
            self.logger.info(f"Running {check.__name__}")
            result, message = check()
            if result == False:
                self.logger.info(f"A check failed: {check.__name__}, {message}")
                return False, message
                break # Exit loop on first failed check
        return True, f"{self.module_type} checks passed"
    

    @check()
    def _check_running(self) -> tuple[bool, str]:
        if not self.is_running:
            return False, "Module is not running"
        else:
            return True, "Module is running"


    @check()
    def _check_readwrite(self) -> tuple[bool, str]:
        try:
            self.logger.debug(f"Checking can write to {self.facade.get_recording_folder()}")
            if not os.path.exists(self.facade.get_recording_folder()):
                os.makedirs(self.facade.get_recording_folder(), exist_ok=True)
            self.logger.debug("Created folder OK")
            # Test write access
            test_file = os.path.join(self.facade.get_recording_folder(), '.test_write')
            self.logger.debug(f"Going to write to test file {test_file}")
            with open(test_file, 'w') as f:
                self.logger.debug(f"Opened test file {f}")
                f.write('test')
            self.logger.debug("Removing test file")
            os.remove(test_file)
            return True, "Recording folder writable"
        except PermissionError as e:
            return False, f"Permission error: {e}"
        except OSError as e:
            return False, f"OSError during write/delete test: {e}"
        except Exception as e:
            return False, f"Recording folder not writable: {e}"


    @check()
    def _check_diskspace(self) -> tuple[bool, str]:
        try:
            statvfs = os.statvfs(self.facade.get_recording_folder())
            free_bytes = statvfs.f_frsize * statvfs.f_bavail
            free_mb = free_bytes / (1024 * 1024)
            required_mb = self._get_required_disk_space_mb()
            if free_mb > required_mb:
                return True, f"Sufficient disk space: {free_mb:.1f}MB free (need at least {required_mb:.1f}MB)"
            if free_mb < required_mb:
                return False, f"Insufficient disk space: {free_mb:.1f}MB free (need at least {required_mb:.1f}MB)"
        except Exception as e:
            return False, f"Cannot check disk space: {str(e)}"


    @check()
    def _check_ptp(self) -> tuple[bool, str]:
        # TODO: Add check for frequency offset as well as time offset
        try:
            ptp_status = self.ptp.get_status()
            # Check if PTP offset is reasonable (configurable threshold)
            max_offset_us = self._get_ptp_offset_threshold_us()
            last_offset = ptp_status["last_offset"]
            if last_offset is None:
                return False, f"PTP reporting Nonetype offsets - may need time to settle"
            if abs(last_offset) > max_offset_us:
                return False, f"PTP not synchronized: offset {last_offset}μs (max: {max_offset_us}μs)"
            else:
                return True, f"PTP synchronised to {ptp_status['last_offset']}μs"
        except Exception as e:
            return False, f"PTP check failed: {e}"


    @check()
    def _check_recording(self) -> tuple[bool, str]:
        if self.is_recording:
            return False, "Module is currently recording"
        else: 
            return True, "Module not currently recording"


    def _run_checks(self):
        self.logger.info("Running checks...")
        checks = {}
        for check in self.checks:
            self.logger.info(f"Running {check.__name__}")
            result, message = check()
            checks[check.__name__] = result, message
            if result == False:
                self.logger.info(f"A check failed: {check.__name__}, {message}")
                return False, message
                break # Exit loop on first failed check
        
        result, message = self._perform_module_specific_checks()
        if result == False:
            self.logger.info(f"Check failed {result} {message}")
            return result, message

        self.logger.info("ALL CHECKS PASSED")
        return True, "All tests passed" # Everything passed    
        

    def validate_readiness(self) -> dict:
        try:
            result, message = self._run_checks()
        except Exception as e:
            result, message = False, f"Error running readiness checks: {e}"
        return {
            'ready': result,
            'timestamp': time.time(),
            'message': message
        }



    """Helper functions"""
    def generate_module_id(self, module_type: str) -> str:
        """Generate a module ID based on the module type and the MAC address"""
        # mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix (old method, led to MAC changing)
        mac = self.get_mac_address("eth0")
        short_id = mac[-4:]  # Takes last 4 characters
        return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"    


    def get_mac_address(self, interface="eth0"):
        """Retreive mac address on specified interface, default eth0."""
        try:
            with open(f"/sys/class/net/{interface}/address") as f:
                return f.read().strip().replace(":", "")
        except FileNotFoundError:
            return None


    def get_utc_time(self, timestamp: int):
        strtime = datetime.datetime.utcfromtimestamp(timestamp).strftime("%Y%m%d_%H%M%S")
        return strtime

    
    def get_utc_date(self, timestamp: int):
        strdate = datetime.datetime.utcfromtimestamp(timestamp).strftime("%Y%m%d")
        return strdate

    
    def _get_version(self) -> str:
        """Get the current saviour version"""
        # TODO: Seems to return nothing - possibly due to the working directory of the systemd service not being the git repo?
        s = subprocess.run(["git", "describe", "--tags"], capture_output=True)
        vers = s.stdout.decode("utf-8")[:-1]
        if not vers:
            vers = "UNKNOWN_VERSION"
        return vers