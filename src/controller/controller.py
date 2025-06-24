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

# Networking and synchronization
import threading # for concurrent operations

# Local managers
import src.controller.controller_service_manager as service_manager
import src.controller.controller_communication_manager as communication_manager
import src.controller.controller_session_manager as session_manager
import src.controller.controller_file_transfer_manager as file_transfer_manager
import src.controller.controller_database_manager as database_manager
import src.controller.controller_health_monitor as health_monitor
import src.controller.controller_buffer_manager as buffer_manager
import src.controller.controller_config_manager as config_manager
import src.controller.controller_ptp_manager as ptp_manager
import src.controller.controller_web_interface as web_interface_manager
import src.controller.controller_cli_interface as cli_interface
    
# Habitat Controller Class
class Controller:
    """Main controller class for the habitat system"""
    
    def __init__(self, config_file_path: str = None):
        """Initialize the controller with default values

        Instantiates the following:
        - A logger
        - A config manager, which initially loads the config file and sets up the config object which the controller can use to get parameters
        - A service manager, which initially registers a zeroconf service and (passively, as part of zeroconf object) starts a thread to browse for module services
        - A session manager, which has a method to generate a session id for a module
        - A communication manager, which initially starts a thread to listen for status and data updates from modules
        - A file transfer manager, which initially starts an aiohttp web server to receive files from modules
        - A data export manager, which initially creates a supabase client (is this a thread?)
        - A PTP manager, which initally defines the ptp4l and phc2sys arguments based on the role of the controller and will later start a thread
        - A buffer manager, which initially sets the max buffer size
        - An interface manager, which initially may instantiate a web interface manager and a CLI interface manager, and will later start a manual control loop thread if CLI interface is enabled. These are to be separated later.
        - A health monitor, which then has it's start monitoring method called to start a thread to monitor the health of the modules

        Args:
            config_file_path: Path to the config file
            
        Returns:
            None
        """

        # Setup logging
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)

        # Add console handler if none exists
        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
            
        self.logger.info(f"(CONTROLLER) Initializing managers")
        # Initialize config manager
        self.config_manager = config_manager.ControllerConfigManager(self.logger, config_file_path)

        # Parameters from config
        self.max_buffer_size = self.config_manager.get("controller.max_buffer_size")
        self.zmq_commands = self.config_manager.get("controller.zmq_commands")
        self.cli_commands = self.config_manager.get("controller.cli_commands")
        
        # Control flags from config
        self.manual_control = self.config_manager.get("controller.manual_control")
        self.print_received_data = self.config_manager.get("controller.print_received_data")
        self.is_running = True  # Add flag for listener thread

        # Managers
        self.service_manager = service_manager.ControllerServiceManager(self.logger, self.config_manager)
        self.session_manager = session_manager.SessionManager()
        self.communication_manager = communication_manager.ControllerCommunicationManager(
            self.logger,
            status_callback=self.handle_status_update,
            data_callback=self.handle_data_update
        )
        self.file_transfer = file_transfer_manager.ControllerFileTransfer(self.logger)
        self.buffer_manager = buffer_manager.ControllerBufferManager(self.logger, self.max_buffer_size)
        self.database_manager = database_manager.ControllerDatabaseManager(self.logger, self.config_manager)
        self.ptp_manager = ptp_manager.PTPManager(logger=self.logger,role=ptp_manager.PTPRole.MASTER)
        
        # Check which interfaces are enabled
        self.web_interface = None # Flag to indicate if the web interface is enabled
        self.cli_interface = None # Flag to indicate if the CLI interface is enabled
        if self.config_manager.get("interface.web_interface") == True:
            self.logger.info(f"(CONTROLLER) Web interface flag set to True")
            self.web_interface = True # Flag to indicate if the web interface is enabled
            self.web_interface_manager = web_interface_manager.WebInterfaceManager(self.logger, self.config_manager) # Should this be instantiated here or in controller.py? 
        else:
            self.logger.info(f"(CONTROLLER) Web interface flag set to False")
            self.web_interface = False

        if self.config_manager.get("interface.cli") == True:
            self.logger.info(f"(CONTROLLER) CLI interface flag set to True")
            self.cli_interface = cli_interface.CLIInterface(self.logger)
        else:
            self.logger.info(f"(CONTROLLER) CLI interface flag set to False")
            self.cli_interface = False

        # Initialize health monitor with configuration
        heartbeat_interval = self.config_manager.get("health_monitor.heartbeat_interval")
        heartbeat_timeout = self.config_manager.get("health_monitor.heartbeat_timeout")
        self.health_monitor = health_monitor.ControllerHealthMonitor(
            self.logger, 
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout
        )

        # Start health monitoring
        self.health_monitor.start_monitoring()

        # Register callbacks
        self.register_callbacks()
    
    def register_callbacks(self):
        """Register callbacks for getting data from other managers"""
        # Web interface
        if self.web_interface:
            self.web_interface_manager.register_callbacks(
                get_modules=self.service_manager.get_modules,
                get_ptp_history=self.buffer_manager.get_ptp_history,
                send_command=self.communication_manager.send_command,
                get_module_health=self.health_monitor.get_module_health
            )
            
            # Register callback for module discovery
            if hasattr(self, 'service_manager'):
                self.logger.info(f"(CONTROLLER) Registering module discovery callback")
                self.service_manager.on_module_discovered = lambda module: self.web_interface_manager.notify_module_update()
                self.service_manager.on_module_removed = lambda module: self.web_interface_manager.notify_module_update()
                self.logger.info(f"(CONTROLLER) Module discovery callback registered")
        
        # Register status change callback with health monitor
        self.health_monitor.register_status_change_callback(self.on_module_status_change)
        self.logger.info(f"(CONTROLLER) Status change callback registered with health monitor")
        
        # CLI 
        if self.cli_interface:
            self.cli_interface.register_callbacks(
                get_modules=self.service_manager.get_modules,
                get_ptp_history=self.buffer_manager.get_ptp_history,
                get_zmq_commands=self.get_zmq_commands,
                send_command=self.communication_manager.send_command,
                get_module_health=self.health_monitor.get_module_health
            )
    

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        print() # New line  
        module_id = topic.split('/')[1] # get module id from topic
        try:
            status_data = eval(data)
            status_type = status_data.get('type', 'unknown')
            self.web_interface_manager.handle_module_status(module_id, status_data)
            match status_type:
                case 'heartbeat':
                    self.logger.info(f"(CONTROLLER) Heartbeat received from {module_id}")
                    self.health_monitor.update_module_health(module_id, status_data)
                case 'ptp_status':
                    self.logger.info(f"(CONTROLLER) PTP status received from {module_id}: {status_data}")
                    self.buffer_manager.add_ptp_history(module_id, status_data)
                case _:
                    self.logger.info(f"(CONTROLLER) Unknown status type from {module_id}: {status_type}")
        except Exception as e:
            self.logger.error(f"(CONTROLLER) Error parsing status data for module {module_id}: {e}")

    def on_module_status_change(self, module_id: str, status: str):
        """Callback for when module status changes (online/offline)"""
        self.logger.info(f"(CONTROLLER) Module {module_id} status changed to: {status}")
        
        # Send status change event to web interface
        self.web_interface_manager.socketio.emit('module_status_change', {
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
        self.logger.info("(CONTROLLER) Stopping controller...")
        
        try:
            # Stop PTP
            self.logger.info("(CONTROLLER) Stopping PTP manager")
            self.ptp_manager.stop()

            # Stop all threads by setting flags
            self.is_running = False
            
            # Stop health monitoring
            self.logger.info("(CONTROLLER) Stopping health monitoring")
            self.health_monitor.stop_monitoring()
            
            # Clean up health monitoring
            self.logger.info("(CONTROLLER) Cleaning up module health tracking")
            self.health_monitor.clear_all_health()
            
            # Clean up module data buffer
            self.logger.info("(CONTROLLER) Cleaning up module data buffer")
            self.buffer_manager.clear_module_data()
            
            # Clean up modules list
            self.logger.info("(CONTROLLER) Cleaning up modules list")
            self.service_manager.modules.clear()

            # Clean up service manager
            self.logger.info("(CONTROLLER) Cleaning up service manager")
            self.service_manager.cleanup()
            
            # Clean up communication manager
            self.logger.info("(CONTROLLER) Cleaning up communication manager")
            self.communication_manager.cleanup()
            
            # Clean up database manager
            self.logger.info("(CONTROLLER) Cleaning up database manager")
            self.database_manager.cleanup()

            # Give modules time to detect the controller is gone
            time.sleep(1)
            
            self.logger.info("(CONTROLLER) Controller stopped successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"(CONTROLLER) Error stopping controller: {e}")
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
        self.logger.info("(CONTROLLER) Starting controller")

        # Start PTP
        self.logger.info("(CONTROLLER) Starting PTP manager...")
        self.ptp_manager.start() # This will start a thread to run ptp4l and phc2sys

        # Start the web interface
        if self.web_interface:
            self.logger.info("(CONTROLLER) Starting web interface")
            self.web_interface_manager.start() # This will start a thread to serve a webapp and listen for commands from user
            
            # Update web interface with initial module list
            if hasattr(self, 'service_manager'):
                self.web_interface_manager.update_modules(self.service_manager.modules)

        # Start the CLI interface
        if self.cli_interface:
            self.logger.info("(CONTROLLER) Starting CLI interface")
            self.cli_interface.start() # This will start a thread to listen for commands from the user

        # Keep the main thread alive
        try: 
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("(CONTROLLER) Keyboard interrupt received. Stopping controller...")
            self.stop()
            return False
        except Exception as e:
            self.logger.error(f"(CONTROLLER) Error in main thread: {e}")
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
        return self.config_manager.get(key, default)
        
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
            self.buffer_manager.max_buffer_size = value
        elif key == "controller.manual_control":
            self.manual_control = value
        elif key == "controller.print_received_data":
            self.print_received_data = value
        elif key == "controller.zmq_commands":
            self.zmq_commands = value
        elif key == "controller.cli_commands":
            self.cli_commands = value
        
        # Update in config manager
        return self.config_manager.set(key, value, persist) 

    def get_zmq_commands(self):
        """Get the list of available ZMQ commands"""
        return self.config_manager.get("controller.zmq_commands", []) 