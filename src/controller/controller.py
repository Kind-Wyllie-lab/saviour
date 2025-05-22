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
import src.controller.controller_data_export_manager as data_export_manager
import src.controller.controller_interface_manager as interface_manager
import src.controller.controller_health_monitor as health_monitor
import src.controller.controller_buffer_manager as buffer_manager
import src.controller.controller_config_manager as config_manager
import src.controller.controller_ptp_manager as ptp_manager
    
# Habitat Controller Class
class Controller:
    """Main controller class for the habitat system"""
    
    def __init__(self, config_file_path: str = None):
        """Initialize the controller with default values"""

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
        self.commands = self.config_manager.get("controller.commands")
        
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
        self.data_export_manager = data_export_manager.ControllerDataExportManager(self.logger, self.config_manager)
        self.ptp_manager = ptp_manager.PTPManager(logger=self.logger,role=ptp_manager.PTPRole.MASTER)
        
        # Initialize health monitor with configuration
        heartbeat_interval = self.config_manager.get("health_monitor.heartbeat_interval")
        heartbeat_timeout = self.config_manager.get("health_monitor.heartbeat_timeout")
        self.health_monitor = health_monitor.ControllerHealthMonitor(
            self.logger, 
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout
        )
        
        self.buffer_manager = buffer_manager.ControllerBufferManager(self.logger, self.max_buffer_size)
        self.interface_manager = interface_manager.ControllerInterfaceManager(self)

        # Start health monitoring
        self.health_monitor.start_monitoring()

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        print() # New line  
        module_id = topic.split('/')[1] # get module id from topic
        try:
            status_data = eval(data)
            status_type = status_data.get('type', 'unknown')
            match status_type:
                case 'heartbeat':
                    self.logger.info(f"(CONTROLLER) Heartbeat received from {module_id}")
                    self.health_monitor.update_module_health(module_id, status_data)
                case 'camera_settings_updated':
                    self.logger.info(f"(CONTROLLER) Camera settings updated for {module_id}: {status_data['settings']}")
                case 'camera_settings_update_failed':
                    self.logger.error(f"(CONTROLLER) Failed to update camera settings for {module_id}: {status_data['error']}")
                case _:
                    self.logger.info(f"(CONTROLLER) Command status from {module_id}: {status_data}")
        except Exception as e:
            self.logger.error(f"(CONTROLLER) Error parsing status data for module {module_id}: {e}")
            
    def handle_data_update(self, topic: str, data: str):
        """Buffer incoming data from modules"""
        print() # New line  
        self.logger.info(f"(CONTROLLER) Data update received from module {topic} with data: {data}")
        module_id = topic.split('/')[1]
        
        # Add data to buffer
        buffer_ok = self.buffer_manager.add_data(module_id, data)
        
        # If buffer is getting full, export to database
        if not buffer_ok:
            self.logger.warning(f"(CONTROLLER) Buffer for module {module_id} is too large. Exporting to database.")
            self.data_export_manager.export_module_data(
                self.buffer_manager.get_module_data(), 
                self.service_manager
            )

        if self.print_received_data:
            print(f"Data update received from module {module_id} with data: {self.buffer_manager.get_module_data(module_id)}")

    def stop(self) -> bool:
        """Stop the controller and clean up resources"""
        self.logger.info("(CONTROLLER) Stopping controller...")
        
        try:
            # Stop PTP
            self.ptp_manager.stop()

            # Stop all threads by setting flags
            self.is_running = False
            
            # Stop health monitoring
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
            self.service_manager.cleanup()
            
            # Clean up communication manager
            self.communication_manager.cleanup()
            
            # Clean up data export manager
            self.data_export_manager.stop_all_exports()
            
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
        """
        self.logger.info("(CONTROLLER) Starting controller")

        # Start file transfer server
        try:
            # Create event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Start the file transfer server
            self.logger.info("(CONTROLLER) Starting file transfer server...")
            loop.run_until_complete(self.file_transfer.start())

            # Start PTP
            self.logger.info("(CONTROLLER) Starting PTP manager...")
            self.ptp_manager.start()

            # Keep the event loop running
            def run_event_loop():
                loop.run_forever()
            
            self.file_transfer_thread = threading.Thread(target=run_event_loop, daemon=True)
            self.file_transfer_thread.start()
            self.logger.info("(CONTROLLER) File transfer server started successfully")
            
        except Exception as e:
            self.logger.error(f"(CONTROLLER) Failed to start file transfer server: {e}")
            return False

        # Start the appropriate control mode
        if self.manual_control:
            self.interface_manager.run_manual_control()
        else:
            self.logger.info("(CONTROLLER) Starting automatic loop (not implemented yet)")
            # @TODO: Implement automatic loop

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
        elif key == "controller.commands":
            self.commands = value
        
        # Update in config manager
        return self.config_manager.set(key, value, persist) 