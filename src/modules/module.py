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

import src.controller.controller_session_manager as controller_session_manager
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
import zmq

# Import managers
from src.modules.module_file_transfer import ModuleFileTransfer
from src.modules.module_config_manager import ModuleConfigManager

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
        self.config_manager = ModuleConfigManager(self.logger, self.module_type, config_file_path)
        self.config = config or {} # Store direct config reference for backwards compatibility
        self.file_transfer = None  # Will be initialized when controller is discovered
        
        # Lazy import and initialization of ServiceManager to avoid circular imports
        from src.modules.module_service_manager import ModuleServiceManager
        self.service_manager = ModuleServiceManager(self.logger, self)
        
        self.session_manager = controller_session_manager.SessionManager()

        # Session management
        self.stream_session_id = None

        # Parameters from config
        self.heartbeat_interval = self.config_manager.get("module.heartbeat_interval")
        self.samplerate = self.config_manager.get("module.samplerate")

        # Control flags
        self.is_running = False  # Start as False
        self.streaming = False
        self.heartbeats_active = False
        self.start_time = None
        self.command_listener_running = False  # Add flag for command listener

        # ZeroMQ setup
        self.context = zmq.Context()
        self.command_socket = self.context.socket(zmq.SUB)
        self.status_socket = self.context.socket(zmq.PUB)
        self.last_command = None


        # Data parameters - Thread
        self.stream_thread = None

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
        else:
            self.is_running = True
            self.start_time = time.time()
            
            # Start heartbeat thread
            self.logger.info("Starting heartbeat thread")
            threading.Thread(target=self.send_heartbeats, daemon=True).start()
            
            # Start command listener thread if we have a controller
            if self.service_manager.controller_ip:
                self.logger.info("Starting command listener thread")
                threading.Thread(target=self.listen_for_commands, daemon=True).start()
            
        return True

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

            # Stop the threads
            if self.stream_thread:
                self.stream_thread.join(timeout=2.0)
                self.stream_thread = None
        
            # Stop the service manager
            self.service_manager.cleanup()

            # Stop the heartbeat thread
            self.heartbeats_active = False
            self.logger.info("Heartbeat flag set to false")

        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False

        # Confirm the module is stopped
        self.is_running = False
        self.logger.info(f"Module stopped at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return True

    def generate_module_id(self, module_type: str) -> str:
        """Generate a module ID based on the module type and the MAC address"""
        mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
        short_id = mac[-4:]  # Takes last 4 characters
        return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"    
    

    # ZeroMQ methods
    def connect_to_controller(self):
        """Connect to controller once we have its IP"""
        try:
            # Get ports from config
            command_port = self.config_manager.get("communication.command_socket_port")
            status_port = self.config_manager.get("communication.status_socket_port")
            
            self.logger.info(f"Module ID: {self.module_id}")
            self.logger.info(f"Subscribing to topic: cmd/{self.module_id}")
            self.command_socket.subscribe(f"cmd/{self.module_id}") # Subscribe only to messages for this module, for the command topic.
            
            self.logger.info(f"Attempting to connect command socket to tcp://{self.service_manager.controller_ip}:{command_port}")
            self.command_socket.connect(f"tcp://{self.service_manager.controller_ip}:{command_port}")
            self.logger.info(f"Attempting to connect status socket to tcp://{self.service_manager.controller_ip}:{status_port}")
            self.status_socket.connect(f"tcp://{self.service_manager.controller_ip}:{status_port}")
            self.logger.info(f"Connected to controller command socket at {self.service_manager.controller_ip}:{command_port}, status socket at {self.service_manager.controller_ip}:{status_port}")
        except Exception as e:
            self.logger.error(f"Error connecting to controller: {e}")

    def listen_for_commands(self):
        """Listen for commands from controller"""
        self.logger.info("Starting command listener thread")
        self.command_listener_running = True
        while self.command_listener_running:
            try:
                self.logger.info("Waiting for command...")
                message = self.command_socket.recv_string()
                self.logger.info(f"Raw message received: {message}")
                topic, command = message.split(' ', 1)
                self.logger.info(f"Parsed topic: {topic}, command: {command}")
                
                # Store the command immediately after parsing
                self.last_command = command
                self.logger.info(f"Stored command: {self.last_command}")
                
                try:
                    self.handle_command(command)
                except Exception as e:
                    self.logger.error(f"Error handling command: {e}")
                    # Don't re-raise the exception, just log it and continue
            except Exception as e:
                if self.command_listener_running:  # Only log if we're still supposed to be running
                    self.logger.error(f"Error receiving command: {e}")
                time.sleep(0.1)  # Add small delay to prevent tight loop on error

    def send_status(self, status_data: str):
        """Send status to the controller"""
        message = f"status/{self.module_id} {status_data}"
        self.status_socket.send_string(message)
        self.logger.info(f"Status sent: {message}")

    def send_data(self, data: str):
        """Send data to the controller"""
        message = f"data/{self.module_id} {data}"
        self.status_socket.send_string(message)
        self.logger.info(f"Data sent: {message}")

    def handle_command(self, command: str):
        """Handle received commands"""
        self.logger.info(f"Handling command: {command}")
        print(f"Command: {command}")
        # Add command handling logic here
        match command:
            case "get_status":
                print("Command identified as get_status")
                try:
                    status = {
                        "timestamp": time.time(),
                        "cpu_temp": self.get_cpu_temp(),
                        "cpu_usage": psutil.cpu_percent(),
                        "memory_usage": psutil.virtual_memory().percent,
                        "uptime": time.time() - self.start_time if self.start_time else 0,
                        "disk_space": psutil.disk_usage('/').percent
                    }
                    self.send_status(status)
                except Exception as e:
                    self.logger.error(f"Error getting status: {e}")
                    # Send a minimal status if we can't get all metrics
                    status = {"timestamp": time.time(), "error": str(e)}
                    self.send_status(status)
            
            case "get_data":
                print("Command identified as get_data")
                data = str(self.read_fake_data())
                self.send_data(data)

            case "start_stream":
                print("Command identified as start_stream")
                if not self.streaming:  # Only start if not already streaming
                    self.streaming = True
                    self.stream_session_id = self.session_manager.generate_session_id(self.module_id)
                    self.logger.debug(f"Stream session ID generated as {self.stream_session_id}")
                    self.stream_thread = threading.Thread(target=self.stream_data, daemon=True)
                    self.stream_thread.start()
            
            case "stop_stream":
                print("Command identified as stop_stream")
                self.streaming = False  # Thread will stop on next loop
                if self.stream_thread: # If there is a thread still
                    self.stream_thread.join(timeout=1.0)  # Wait for thread to finish
                    self.stream_thread = None # Empty the thread
            
            case _:
                print(f"Command {command} not recognized")
                self.send_data("Command not recognized")

    # Health monitoring methods            
    def send_heartbeats(self):
        """Continuously send heartbeat messages to the controller"""
        self.logger.info("Heartbeat thread started")
        while self.is_running:
            if self.heartbeats_active:
                try:
                    self.logger.info("Sending heartbeat")
                    status = {
                        "timestamp": time.time(),
                        'cpu_temp': self.get_cpu_temp(),
                        'cpu_usage': psutil.cpu_percent(),
                        'memory_usage': psutil.virtual_memory().percent,
                        'uptime': time.time() - self.start_time,
                        'disk_space': psutil.disk_usage('/').percent # Free disk space
                    }
                    # Send on status/ topic
                    message = f"status/{self.module_id} {status}"
                    self.status_socket.send_string(message)
                    self.logger.info(f"Heartbeat sent: {message}")
                except Exception as e:
                    self.logger.error(f"Error sending heartbeat: {e}")
                time.sleep(self.heartbeat_interval)
            else:
                self.logger.debug("Heartbeats not active, waiting...")
                time.sleep(1)  # Sleep longer when not active

    def get_health(self):
        """Get health of the module"""
        health_data = {
            "timestamp": time.time(),
            "module_id": self.module_id,
            "type": "heartbeat",
        }
        return health_data

    def get_cpu_temp(self):
        """Get CPU temperature"""
        try:
            temp = os.popen("vcgencmd measure_temp").readline()
            return float(temp.replace("temp=","").replace("'C\n",""))
        except:
            return None            
                
    # Sensor data methods
    def stream_data(self):
        """Function to continuously read and transmit data"""
        while self.streaming:
            data=str(self.read_fake_data())
            self.send_data(data)
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
            
    
    # PTP methods
    def status_ptp(self) -> bool:
        """
        Get PTP status.
        """
        # TODO: Implement PTP status
        return True

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
            
    def get_config(self, key_path: str, default: Any = None) -> Any:
        """
        Get a configuration value
        
        Args:
            key_path: Configuration key path (e.g., "module.heartbeat_interval")
            default: Default value if key doesn't exist
            
        Returns:
            Configuration value
        """
        return self.config_manager.get(key_path, default)
        
    def set_config(self, key_path: str, value: Any, persist: bool = False) -> bool:
        """
        Set a configuration value
        
        Args:
            key_path: Configuration key path (e.g., "module.heartbeat_interval")
            value: Value to set
            persist: Whether to save to config file
            
        Returns:
            True if successful
        """
        # Update local variable if applicable
        if key_path == "module.heartbeat_interval":
            self.heartbeat_interval = value
        elif key_path == "module.samplerate":
            self.samplerate = value
        
        # Update in config manager
        return self.config_manager.set(key_path, value, persist)