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


# Import managers
from src.modules.module_file_transfer_manager import ModuleFileTransfer
from src.modules.module_config_manager import ModuleConfigManager
from src.modules.module_communication_manager import ModuleCommunicationManager
import src.controller.controller_session_manager as controller_session_manager # TODO: Change this to a module manager
from src.modules.module_health_manager import ModuleHealthManager
from src.modules.module_command_handler import ModuleCommandHandler
from src.modules.module_ptp_manager import PTPManager, PTPRole

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
    def __init__(self, module_type: str, config: dict = None, config_file_path: str = None):
        # Module type
        self.module_type = module_type
        self.module_id = self.generate_module_id(self.module_type)
        
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
        self.logger.info(f"(MODULE) Initializing managers")
        self.config_manager = ModuleConfigManager(self.logger, self.module_type, config_file_path)
        self.config = config or {} # Store direct config reference for backwards compatibility
        self.file_transfer = None  # Will be initialized when controller is discovered
        self.communication_manager = ModuleCommunicationManager(         # Communication manager - handles ZMQ messaging
            self.logger, 
            self.module_id,
            # Command handling moved to command_handler
            config_manager=self.config_manager
        )
        self.health_manager = ModuleHealthManager(
            self.logger, 
            config_manager=self.config_manager,
            communication_manager=self.communication_manager
        )
        self.ptp_manager = PTPManager(
            logger=self.logger,
            role=PTPRole.SLAVE)

        # Initialize command handler if not already set
        if not hasattr(self, 'command_handler'):
            self.command_handler = ModuleCommandHandler(
                self.logger,
                self.module_id,
                self.module_type,
                communication_manager=self.communication_manager,
                health_manager=self.health_manager,
                config_manager=self.config_manager,
                ptp_manager=self.ptp_manager,
                start_time=None # Will be set during start()
            )

        # Bind health manager's callback to the ptp_manager method
        self.health_manager.get_ptp_offsets = self.ptp_manager.get_status

        # Set the callback in the communication manager to use the command handler
        self.communication_manager.command_callback = self.command_handler.handle_command
        
        # Define callbacks for the command handler
        self.command_handler.set_callbacks({
            'read_data': self.read_fake_data,
            'stream_data': self.stream_data,
            'generate_session_id': lambda module_id: self.session_manager.generate_session_id(module_id),
            'samplerate': self.config_manager.get("module.samplerate", 200),
            'ptp_status': self.ptp_manager.get_status()
        })
        
        # Lazy import and initialization of ServiceManager to avoid circular imports
        from src.modules.module_service_manager import ModuleServiceManager
        self.service_manager = ModuleServiceManager(self.logger, self)
        
        self.session_manager = controller_session_manager.SessionManager()

        # Session management
        self.stream_session_id = None

        # Parameters from config
        # self.heartbeat_interval = self.config_manager.get("module.heartbeat_interval")
        self.samplerate = self.config_manager.get("module.samplerate")

        # Control flags
        self.is_running = False  # Start as False
        self.streaming = False
        
        # Track when module started for uptime calculation
        self.start_time = None
        
        # Data parameters - Thread
        self.stream_thread = None

    def controller_discovered(self, controller_ip: str, controller_port: int):
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

                # Start PTP
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
    
    # Sensor data methods
    def stream_data(self):
        """Function to continuously read and transmit data"""
        while self.streaming:
            data=str(self.read_fake_data())
            self.communication_manager.send_data(data)
            time.sleep(self.samplerate/1000)

    def read_fake_data(self): 
        """Stand in for future sensor integration. Returns a random float between 0 and 1."""
        return random.random()
    
    def read_fake_camera_frame(self):
        """Generate fake camera frame data"""
        # Create random 640x480 RGB frame
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        return frame.tobytes()  # Convert to bytes for transmission

    def read_fake_audio(self):
        """Generate fake audio samples"""
        # Generate 1 second of fake audio at 192kHz
        samples = np.random.uniform(-1, 1, 192000)
        return samples.tobytes()

    def read_fake_rfid(self):
        """Generate fake RFID readings"""
        tags = ['A1B2C3D4', 'E5F6G7H8', 'I9J0K1L2']
        return random.choice(tags)

    def read_fake_ttl(self):
        """Generate fake TTL I/O readings"""
        digital_in = [random.choice([0, 1]) for _ in range(8)]  # 8 digital inputs
        analog_in = [random.uniform(0, 5) for _ in range(4)]    # 4 analog inputs (0-5V)
        return {
            'digital': digital_in,
            'analog': analog_in
        }


    def send_file(self, filepath: str, remote_path: str = None) -> bool:
        """Send a file to the controller"""
        if not self.file_transfer:
            self.logger.error("File transfer not initialized - controller not discovered")
            return False
            
        try:
            # Create a new event loop for this thread if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Run the file transfer
            success = loop.run_until_complete(self.file_transfer.send_file(filepath, remote_path))
            return success
        except Exception as e:
            self.logger.error(f"Error sending file: {e}")
            return False
            