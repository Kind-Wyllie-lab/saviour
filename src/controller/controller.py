#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat Controller - Modular Synchronised Data Capture

This script serves as the main controller for the habitat system, providing:
- Precise time synchronisation (PTP master) for all connected modules
- Module discovery, monitoring, and health checks
- Recording session management and control
- Data collection and packaging in NWB format
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
from typing import List, Dict, Any # for type hinting
import asyncio # for asyncio

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
from src.controller.buffer import Buffer
from src.controller.config import Config
from src.controller.ptp import PTP, PTPRole
from src.controller.web import Web
    
# Habitat Controller Class
class Controller:
    """Main controller class for the habitat system"""
    
    def __init__(self, config_file_path: str = None):
        """Initialize the controller with default values

        Instantiates the following:
        - A logger
        - A config manager, which initially loads the config file and sets up the config object which the controller can use to get parameters
        - A network manager, which initially registers a zeroconf network and (passively, as part of zeroconf object) starts a thread to browse for module networks
        - A communication manager, which initially starts a thread to listen for status and data updates from modules
        - A PTP manager, which initally defines the ptp4l and phc2sys arguments based on the role of the controller and will later start a thread
        - A buffer manager, which initially sets the max buffer size
        - A web manager, which hosts a flask based webapp GUI
        - A health monitor, which then has it's start monitoring method called to start a thread to monitor the health of the modules

        Args:
            config_file_path: Path to the config file
            
        Returns:
            None
        """

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initializing managers")

        # Initialize config manager
        self.config = Config(config_file_path)

        # Add logging file handler if none exists
        if not self.logger.handlers:
            # Add file handler for persistent logging (useful when running as systemd network)
            try:
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

            except Exception as e:
                # If file logging fails, log the error but don't crash
                self.logger.warning(f"Failed to setup file logging: {e}")
                self.logger.info("Continuing with console logging only")
            

        # Module state managemenet
        self.module_config = {} # To store module config information
        self.max_buffer_size = self.config.get("controller.max_buffer_size")
        
        # Control flags 
        self.is_running = True  # Add flag for listener thread

        # Managers
        self.network = Network(self.config) 
        self.network.on_module_discovered = self.on_module_discovered
        self.network.on_module_removed = self.on_module_removed              

        self.logger.info(f"Module discovery callback registered early")
        
        self.communication = Communication(
            status_callback=self.handle_status_update,
            data_callback=self.handle_data_update
        )
        self.buffer = Buffer(self.max_buffer_size)
        # self.database = database.ControllerDatabaseManager(self.config)
        self.ptp = PTP(role=PTPRole.MASTER)
        self.web = Web(self.config)

        # Initialize health monitor with configuration
        heartbeat_interval = self.config.get("health_monitor.heartbeat_interval")
        heartbeat_timeout = self.config.get("health_monitor.heartbeat_timeout")
        self.health = Health(
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout
        )

        # Start health monitoring
        self.logger.info("Starting health monitoring thread")
        self.health.start_monitoring()

        # Register callbacks
        self.register_callbacks()
    
    def register_callbacks(self):
        """Register callbacks for getting data from other managers"""
        # Web interface
        if self.web:
            self.web.register_callbacks({
                "get_modules": self.network.get_modules,
                "get_ptp_history": self.buffer.get_ptp_history,
                "send_command": self.communication.send_command,
                "get_module_health": self.health.get_module_health
            })
        
        # Register status change callback with health monitor
        self.health.set_callbacks({
            "on_status_change": self.on_module_status_change,
            "send_command": self.communication.send_command
        })
        self.logger.info(f"Status change callback registered with health monitor")

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        module_id = topic.split('/')[1] # get module id from topic
        try:
            import json
            status_data = json.loads(data)
            status_type = status_data.get('type', 'unknown')
            self.web.handle_module_status(module_id, status_data)
            match status_type:
                case 'heartbeat':
                    self.logger.info(f"Heartbeat received from {module_id}")
                    self.health.update_module_health(module_id, status_data)
                case 'ptp_status':
                    self.logger.info(f"PTP status received from {module_id}: {status_data}")
                    self.buffer.add_ptp_history(module_id, status_data)
                case 'recordings_list':
                    self.logger.info(f"Recordings list received from {module_id}")
                case 'ACK':
                    self.logger.info(f"Command acknowledged by {module_id}: {status_data['command']}")
                case 'status':
                    self.logger.info(f"{module_id} sent status type message likely response to get status command")
                    self.health.update_module_health(module_id, status_data)
                case 'get_config':
                    self.logger.info(f"Config dict received from {module_id}")
                    config_data = status_data.get('config', {})
                    # Extract the editable section if it exists, otherwise store the entire config
                    if isinstance(config_data, dict) and 'editable' in config_data:
                        self.module_config[module_id] = config_data['editable']
                        self.logger.info(f"Stored editable config for {module_id}")
                    else:
                        self.module_config[module_id] = config_data
                        self.logger.info(f"Stored full config for {module_id}")
                case 'set_config':
                    self.logger.info(f"Set config response received from {module_id}: {status_data}")
                    # If the set_config was successful, we should refresh the config
                    if status_data.get('status') == 'success':
                        # Request updated config from this module
                        self.communication.send_command(module_id, "get_config", {})
                    else:
                        self.logger.error(f"Set config failed for {module_id}: {status_data.get('message', 'Unknown error')}")
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
        self.logger.info(f"Module {module_id} status changed to: {status}")
        
        # TODO: What should happen when a module goes offline?
        if status=="offline":
            # TODO: Deregister it?
            self.logger.info(f"TODO: Deregister {module_id}")


        # Send status change event to web interface
        self.web.socketio.emit('module_status_change', {
            'module_id': module_id,
            'status': status
        })

    def handle_data_update(self, topic: str, data: str):
        """Handle a data update from a module"""
        # TODO: Implement this
        # Formerly data pipeline was envisioned as a stream of data from modules to the controller, which would then be buffered and exported to the database.
        # This is no longer the case. Modules record data locally, which controller then directs to be exported to either a NAS, database, or controller's own storage. 


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
            
            # Clean up module data buffer
            self.logger.info("Cleaning up module data buffer")
            self.buffer.clear_module_data()
            
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
        # Update local variable if applicable
        if key == "controller.max_buffer_size":
            self.max_buffer_size = value
            self.buffer.max_buffer_size = value

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

if __name__ == "__main__":
    controller = Controller(config_file_path="config.json")
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()