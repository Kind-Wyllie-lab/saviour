#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR Controller

This script serves as the main controller for the habitat system, providing:
- Precise time synchronisation (PTP master) for all connected modules
- Module discovery, monitoring, and health checks
- Recording session management and control
- Data collection and packaging in NWB format

Author: Andrew SG
Created: 11/11/2025
"""

import sys
import os
from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
import datetime
import logging # for logging and debugging
from dataclasses import dataclass # to define Module dataclass
from typing import List, Dict, Any, Optional # for type hinting
import asyncio # for asyncio
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
    format=format_string
)   

# Networking and synchronization
import threading # for concurrent operations

# Local managers
from src.controller.network import Network
from src.controller.communication import Communication
from src.controller.health import Health
from src.controller.config import Config
from src.controller.ptp import PTP, PTPRole
from src.controller.web import Web
from src.controller.modules import Modules
from src.controller.facade import ControllerFacade
from src.controller.recording import Recording

# Habitat Controller Class
class Controller(ABC):
    """
    Base class for SAVIOUR controller devices.
    """
    def __init__(self):
        """Initialize the controller with default values

        Instantiates the following:
        - A logger
        - A config manager, which initially loads the config file and sets up the config object which the controller can use to get parameters
        - A network manager, which initially registers a zeroconf network and (passively, as part of zeroconf object) starts a thread to browse for module networks
        - A communication manager, which initially starts a thread to listen for status and data updates from modules
        - A PTP manager, which initally defines the ptp4l and phc2sys arguments based on the role of the controller and will later start a thread
        - A web manager, which hosts a flask based webapp GUI
        - A health monitor, which then has it's start monitoring method called to start a thread to monitor the health of the modules
        """

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initializing managers")

        # Initialize config manager
        self.config = Config()

        self._setup_logging()
        
        # Control flags 
        self.is_running = True  # Add flag for listener thread

        # Managers
        self.network = Network(self.config) 
        self.network.on_module_discovered = self.on_module_discovered
        self.network.on_module_removed = self.on_module_removed              
        self.logger.info(f"Module discovery callback registered early")
        self.communication = Communication(
            status_callback=self.handle_status_update,
        )
        self.ptp = PTP(role=PTPRole.MASTER, config=self.config)
        self.web = Web(self.config)
        # Initialize health monitor with configuration
        heartbeat_interval = self.config.get("health_monitor.heartbeat_interval")
        heartbeat_timeout = self.config.get("health_monitor.heartbeat_timeout")
        self.health = Health(
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout
        )
        self.modules = Modules()
        self.recording = Recording()
        self.facade = ControllerFacade(self)

        # Register facade/callbacks
        self.network.facade = self.facade
        self.health.facade = self.facade
        self.communication.facade = self.facade
        self.web.facade = self.facade
        self.modules.facade = self.facade
        self.recording.facade = self.facade
        self.config.on_controller_config_change = self.on_controller_config_change

        # Controller state
        self.start_time = None

        # Start health monitoring
        self.logger.info("Starting health monitoring thread")
        self.health.start_monitoring()


    def on_controller_config_change(self, updated_keys: Optional[list[str]]) -> None:
        self.logger.info(f"Received notification that controller config changed, calling configure_controller() with keys {updated_keys}")
        self.configure_controller(updated_keys)


    @abstractmethod
    def configure_controller(self, updated_keys: Optional[list[str]]):
        """Gets called when controller specific configuration changes - allows controllers to update their specific settings when they change"""
        self.logger.warning("No implementation provided for abstract method configure_controller")
    

    def _register_special_socket_events(self, socketio):
        """
        Use this in __init__() to add new socketio event handlers.

        Example:
        @socketio.on("special_event")
        def handle_special_event(data):
            print("Received special event:", data)
            socketio.emit("special_response", {"status": "ok"})
        """
        raise NotImplementedError


    def _remove_module(self, module_id: str):
        self.logger.info(f"Removing {module_id}")
        self.modules.remove_module(module_id)
        self.health.remove_module(module_id)
        self.logger.info(f"New list: {self.modules.get_modules().keys()}")
        self.communication.send_command(module_id, "shutdown", {})


    def network_notify_module_update(self, discovered_modules: dict):
        """Observer callback for when network manager detects a module update
        Need to tell Modules and Health about this.
        """
        self.modules.network_notify_module_update(discovered_modules)
        self.health.network_notify_module_update(discovered_modules)
    
    
    def network_notify_module_id_change(self, old_id: str, new_id: str):
        """Observer callback for when network manager detects a module ID change
        Need to tell Modules and Health about this.
        """
        self.modules.network_notify_module_id_change(old_id, new_id)
        self.health.network_notify_module_id_change(old_id, new_id)
        # Also need to update module_config dict if old_id exists there
        if old_id in self.module_config:
            self.module_config[new_id] = self.module_config.pop(old_id)
            self.logger.info(f"Updated module_config key from {old_id} to {new_id}")


    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        module_id = topic.split('/')[1] # get module id from topic
        try:
            import json
            status_data = json.loads(data)
            status_type = status_data.get('type', 'unknown')
            self.web.handle_module_status(module_id, status_data) # Whatever web related functionality related to status update, process it # TODO: remove this
            match status_type:
                case 'heartbeat':
                    self.logger.info(f"Heartbeat received from {module_id}")
                    self.modules.check_status(module_id, status_data)
                    self.health.update_module_health(module_id, status_data)
                case 'recordings_list':
                    self.logger.info(f"Recordings list received from {module_id}")
                case 'status':
                    self.logger.info(f"{module_id} sent status type message likely response to get status command")
                    self.modules.check_status(module_id, status_data)
                    self.health.update_module_health(module_id, status_data)
                case 'get_config':
                    self.logger.info(f"Config dict received from {module_id}")
                    config_data = status_data.get('config', {})
                    # Extract the editable section if it exists, otherwise store the entire config
                    if isinstance(config_data, dict) and 'editable' in config_data:
                        self.modules.update_module_config(module_id, config_data["editable"])
                        # self.module_config[module_id] = config_data['editable']
                        self.logger.info(f"Stored editable config for {module_id}")
                    else:
                        self.modules.update_module_config(module_id, config_data)
                        # self.module_config[module_id] = config_data
                        self.logger.info(f"Stored full config for {module_id}")
                case 'set_config':
                    self.logger.info(f"Set config response received from {module_id}")
                    # If the set_config was successful, we should refresh the config
                    if status_data.get('result') == 'success':
                        if not status_data.get('config'):
                            self.communication.send_command(module_id, "get_config", {})
                        else:
                            self.modules.update_module_config(module_id, status_data.get('config'))
                    else:
                        self.logger.error(f"Set config failed for {module_id}: {status_data.get('message', 'Unknown error')}")
                case 'recording_started':
                    self.logger.info(f"{module_id} has started recording")
                    self.modules.notify_recording_started(module_id, status_data)
                case 'recording_stopped':
                    self.logger.info(f"{module_id} has stopped recording")
                    self.modules.notify_recording_stopped(module_id, status_data)
                case 'recording_stop_failed':
                    if status_data.get("error") == "Not recording":
                        self.modules.notify_recording_stopped(module_id, status_data)
                case 'validate_readiness':
                    # Handle readiness validation response
                    ready = status_data.get('ready', False)
                    message = status_data.get('message', 'No message...')
                    self.logger.info(f"Readiness validation response from {module_id}: {'ready' if ready else 'not ready'}")
                    # Tell Module object that module is ready
                    if not ready:
                        self.logger.info(f"Full message from non-ready module: {message}")
                    self.modules.notify_module_readiness_update(module_id, ready, message)
                case "error":
                    self.logger.warning(f"Received error from {module_id}: {message}")
                case _:
                    self.logger.info(f"Unknown status type from {module_id}: {status_type}")
        except Exception as e:
            self.logger.error(f"Error parsing status data for module {module_id}: {e}")


    def on_module_status_change(self, module_id: str, status: str):
        """Callback for when module status changes (online/offline)
        
        Args:
            module_id: String representing the module
            status: may be "online" or "offline"
        """
        self.logger.info(f"on_module_status_change called for {module_id} with status {status}")
        if status == "online":
            online = True
        elif status == "offline":
            online = False

        self.logger.info(f"Module {module_id} status is: {status}, online status: {online}")

        self.modules.notify_module_online_update(module_id, online)


    def _setup_logging(self): 
        # Add logging file handler if none exists
        if not self.config.get("logging.file_logging", False):
            return
            
        if not self.logger.handlers:
            # Add file handler for persistent logging (useful when running as systemd network)
            try:
                # Create logs directory if it doesn't exist
                log_dir = self.config.get("logging.directory", "/var/log/habitat")
                os.makedirs(log_dir, exist_ok=True)
                
                # Generate log filename with module info
                log_filename = f"controller.log"
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


    def stop(self) -> bool:
        """Stop the controller and clean up resources"""
        self.logger.info("Stopping controller...")
        
        try:
            # Stop PTP
            self.logger.info("Stopping PTP manager")
            self.ptp.stop()

            # Stop all threads by setting flags
            self.is_running = False
            
            # Stop health monitoring
            self.logger.info("Stopping health monitoring")
            self.health.stop_monitoring()
            
            # Clean up health monitoring
            self.logger.info("Cleaning up module health tracking")
            self.health.clear_all_health()
            
            # Clean up network manager
            self.logger.info("Cleaning up network manager")
            self.network.cleanup()
            
            # Clean up communication manager
            self.logger.info("Cleaning up communication manager")
            self.communication.cleanup()
            
            # Clean up database manager
            self.logger.info("Cleaning up database manager")
            # self.database.cleanup()

            # Give modules time to detect the controller is gone
            time.sleep(1)
            
            self.logger.info("Controller stopped successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping controller: {e}")
            return False


    # Main methods
    def start(self) -> bool:
        """
        Start the controller.
        
        Returns:
            bool: True if the controller started successfully, False otherwise.
            
        Starts the following:
        - A PTP manager, which starts a thread to run ptp4l and phc2sys
        - A interface manager, which receives input from CLI or web interface and handles it (and may also be used to send commands to modules)
        - A forever loop to keep the main controller thread alive
        """
        self.logger.info("Starting controller")
        self.start_time = time.time()

        # Register controller network for module discovery
        self.logger.info("Registering controller service...")
        if not self.network.register_service():
            self.logger.error("Failed to register controller network")
            return False
        self.logger.info("Controller network registered successfully")

        # Start PTP
        self.logger.info("Starting PTP manager...")
        self.ptp.start() # This will start a thread to run ptp4l and phc2sys

        # Start the web interface
        if self.web:
            self.logger.info("Starting web interface")
            self.web.start() # This will start a thread to serve a webapp and listen for commands from user
            
            # Update web interface with initial module list
            if hasattr(self, 'network'):
                self.web.update_modules(self.network.discovered_modules)


        # Start the modules manager
        self.modules.start()
        
        # Keep the main thread alive
        try: 
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received. Stopping controller...")
            self.stop()
            return False
        except Exception as e:
            self.logger.error(f"Error in main thread: {e}")
            self.stop()
            return False
        
        return True
        

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value
        
        Args:
            key: Configuration key path (e.g., "controller.max_buffer_size")
            default: Default value if key doesn't exist
            
        Returns:
            Configuration value
        """
        return self.config.get(key, default)
        

    def set_config(self, key: str, value: Any, persist: bool = False) -> bool:
        """
        Set a configuration value
        
        Args:
            key: Configuration key path (e.g., "controller.max_buffer_size")
            value: Value to set
            persist: Whether to save to config file
            
        Returns:
            True if successful
        """

        # Update in config manager
        return self.config.set(key, value, persist) 


    def on_module_discovered(self, module):
        """Callback for when a new module is discovered"""
        self.logger.info(f"Module discovered: {module.id}")
        
        # Add module to health monitor with initial offline status
        # This allows the health monitor to track the module even before it sends heartbeat
        initial_health_data = {
            'timestamp': time.time(),
            'status': 'online', 
            'cpu_temp': 0,
            'cpu_usage': 0,
            'memory_usage': 0,
            'uptime': 0,
            'disk_space': 0
        }
        self.health.update_module_health(module.id, initial_health_data)
        
        # Update web interface
        if hasattr(self, 'web'):
            self.web.update_modules(self.network.discovered_modules)
            self.web.notify_module_update()
            self.module_config[module.id] = {}


    def on_module_removed(self, module):
        """Callback for when a module network is removed"""
        self.web.notify_module_update()


    def get_module_config(self, module_id: str):
        self.communication.send_command(module_id, "get_config", {})


    def get_module_configs(self):
        """Get the module configuration data for online modules only"""
        # Request config from all modules - refresh the config stored on controller
        self.logger.info(f"Sending get_config command to all modules")
        self.communication.send_command("all", "get_config", {})


    def get_samba_info(self):
        """Get Samba share information from configuration"""
        try:
            # Get controller IP address from service manager (already detected and stored)
            controller_ip = self.service.ip
            
            # Get Samba configuration from config
            samba_config = {
                'share_name': self.config.get('samba.share_name', 'controller_share'),
                'username': self.config.get('samba.username', 'pi'),
                'password': self.config.get('samba.password', 'saviour'),
                'share_path': f'\\\\{controller_ip}\\{self.config.get("samba.share_name", "controller_share")}',
                'controller_ip': controller_ip
            }
            
            self.logger.info(f"Returning Samba info: {samba_config}")
            return samba_config
        except Exception as e:
            self.logger.error(f"Error getting Samba info: {e}")
            return {
                'share_name': 'controller_share',
                'username': 'pi',
                'password': 'saviour',
                'share_path': '\\\\192.168.1.1\\controller_share',
                'controller_ip': '192.168.1.1'
            }


if __name__ == "__main__":
    controller = Controller()
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
