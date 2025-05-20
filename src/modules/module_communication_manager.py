#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Communication Manager

The communication manager is responsible for handling all ZMQ-based messaging between
a module and the controller, including:
- Command subscription from the controller
- Status/data publishing to the controller
- Heartbeat mechanism
- Message handling and routing
"""

import zmq
import threading
import logging
import time
from typing import Callable, Dict, Any, Optional

class ModuleCommunicationManager:
    def __init__(self, logger: logging.Logger, 
                 module_id: str,
                 command_callback: Callable[[str], None] = None,
                 config_manager = None):
        """Initialize the communication manager
        
        Args:
            logger: Logger instance
            module_id: The unique identifier for this module
            command_callback: Callback function to handle received commands
            config_manager: Configuration manager for retrieving settings
        """
        self.logger = logger
        self.module_id = module_id
        self.command_callback = command_callback
        self.config_manager = config_manager
        
        # Control flags
        self.command_listener_running = False
        # self.heartbeats_active = False # TODO: move to health manager
        self.last_command = None
        
        # Controller connection info
        self.controller_ip = None
        self.controller_port = None
        
        # ZeroMQ setup - initialized but not connected
        self.context = zmq.Context()
        self.command_socket = self.context.socket(zmq.SUB)
        self.status_socket = self.context.socket(zmq.PUB)
        
        # Command listener thread
        self.command_thread = None

    def connect(self, controller_ip: str, controller_port: int) -> bool:
        """Connect to the controller's ZMQ sockets
        
        Args:
            controller_ip: IP address of the controller
            controller_port: Port number of the controller
            
        Returns:
            bool: True if connection was successful
        """
        try:
            # Store controller information
            self.controller_ip = controller_ip
            self.controller_port = controller_port
            
            # Get ports from config if available
            if self.config_manager:
                command_port = self.config_manager.get("communication.command_socket_port", 5555)
                status_port = self.config_manager.get("communication.status_socket_port", 5556)
            else:
                command_port = 5555
                status_port = 5556
            
            # Set up command subscription
            self.logger.info(f"(COMMUNICATION MANAGER) Module ID: {self.module_id}")
            self.logger.info(f"(COMMUNICATION MANAGER) Subscribing to topic: cmd/{self.module_id}")
            self.command_socket.subscribe(f"cmd/{self.module_id}")
            
            # Connect sockets
            self.logger.info(f"(COMMUNICATION MANAGER) Attempting to connect command socket to tcp://{controller_ip}:{command_port}")
            self.command_socket.connect(f"tcp://{controller_ip}:{command_port}")
            self.logger.info(f"(COMMUNICATION MANAGER) Attempting to connect status socket to tcp://{controller_ip}:{status_port}")
            self.status_socket.connect(f"tcp://{controller_ip}:{status_port}")
            self.logger.info(f"(COMMUNICATION MANAGER) Connected to controller command socket at {controller_ip}:{command_port}, status socket at {controller_ip}:{status_port}")
            
            return True
        except Exception as e:
            self.logger.error(f"Error connecting to controller: {e}")
            return False

    def start_command_listener(self) -> bool:
        """Start the command listener thread
        
        Returns:
            bool: True if the listener was started successfully
        """
        if self.command_listener_running:
            self.logger.info("(COMMUNICATION MANAGER) Command listener already running")
            return False
        
        if not self.controller_ip:
            self.logger.error("(COMMUNICATION MANAGER) Cannot start command listener: not connected to controller")
            return False
        
        self.command_listener_running = True
        self.command_thread = threading.Thread(target=self.listen_for_commands, daemon=True)
        self.command_thread.start()
        self.logger.info("(COMMUNICATION MANAGER) Command listener thread started")
        return True

    def listen_for_commands(self):
        """Listen for commands from the controller"""
        self.logger.info("(COMMUNICATION MANAGER) Starting command listener thread")
        while self.command_listener_running:
            try:
                self.logger.info("(COMMUNICATION MANAGER) Waiting for command...")
                message = self.command_socket.recv_string()
                self.logger.info(f"(COMMUNICATION MANAGER) Raw message received: {message}")
                topic, command = message.split(' ', 1)
                self.logger.info(f"(COMMUNICATION MANAGER) Parsed topic: {topic}, command: {command}")
                
                # Store the command immediately after parsing
                self.last_command = command
                self.logger.info(f"(COMMUNICATION MANAGER) Stored command: {self.last_command}")
                
                # Call the command handler if available
                if self.command_callback:
                    try:
                        self.command_callback(command)
                    except Exception as e:
                        self.logger.error(f"(COMMUNICATION MANAGER) Error handling command: {e}")
                        # Don't re-raise the exception, just log it and continue
            except Exception as e:
                if self.command_listener_running:  # Only log if we're still supposed to be running
                    self.logger.error(f"(COMMUNICATION MANAGER) Error receiving command: {e}")
                time.sleep(0.1)  # Add small delay to prevent tight loop on error

    def send_status(self, status_data: Dict[str, Any]):
        """Send status information to the controller
        
        Args:
            status_data: Dictionary containing status information
        """
        if not self.controller_ip:
            self.logger.warning("(COMMUNICATION MANAGER) Cannot send status: not connected to controller")
            return
            
        message = f"status/{self.module_id} {status_data}"
        self.status_socket.send_string(message)
        self.logger.info(f"(COMMUNICATION MANAGER) Status sent: {message}")

    def send_data(self, data: Any):
        """Send data to the controller
        
        Args:
            data: Data to send to the controller
        """
        if not self.controller_ip:
            self.logger.warning("(COMMUNICATION MANAGER) Cannot send data: not connected to controller")
            return
            
        message = f"data/{self.module_id} {data}"
        self.status_socket.send_string(message)
        self.logger.info(f"(COMMUNICATION MANAGER) Data sent: {message}")

    def cleanup(self):
        """Clean up ZMQ connections"""
        self.logger.info(f"(COMMUNICATION MANAGER) Cleaning up communication manager for module {self.module_id}")
        
        # Stop threads
        self.command_listener_running = False
        
        # Give threads time to stop
        time.sleep(0.5)
        
        # Clean up ZeroMQ connections - important to do this in the right order
        try:
            # Step 1: Set all sockets to non-blocking with zero linger time
            if hasattr(self, 'command_socket') and self.command_socket:
                self.logger.info("(COMMUNICATION MANAGER) Setting command socket linger to 0")
                self.command_socket.setsockopt(zmq.LINGER, 0)
                
            if hasattr(self, 'status_socket') and self.status_socket:
                self.logger.info("(COMMUNICATION MANAGER) Setting status socket linger to 0")
                self.status_socket.setsockopt(zmq.LINGER, 0)
            
            # Step 2: Close all sockets
            if hasattr(self, 'command_socket') and self.command_socket:
                self.logger.info("(COMMUNICATION MANAGER) Closing command socket")
                self.command_socket.close()
                self.command_socket = None
                
            if hasattr(self, 'status_socket') and self.status_socket:
                self.logger.info("(COMMUNICATION MANAGER) Closing status socket")
                self.status_socket.close()
                self.status_socket = None
            
            # Step 3: Wait a bit for ZMQ to clean up internal resources
            time.sleep(0.1)
            
            # Step 4: Terminate context
            if hasattr(self, 'context') and self.context:
                self.logger.info("(COMMUNICATION MANAGER) Terminating ZeroMQ context")
                # First try to terminate with timeout
                try:
                    self.context.term()
                except Exception as e:
                    self.logger.warning(f"(COMMUNICATION MANAGER) Normal context termination failed: {e}. Trying forced shutdown.")
                    # If term() hangs or fails, try destroy with timeout
                    try:
                        if hasattr(self.context, 'destroy'):
                            self.context.destroy(linger=0)
                    except Exception as e2:
                        self.logger.error(f"(COMMUNICATION MANAGER) Forced context shutdown failed: {e2}")
                
                self.context = None
            
            # Reset connection state
            self.controller_ip = None
            self.controller_port = None
            
            # Recreate sockets for future connections
            self.context = zmq.Context()
            self.command_socket = self.context.socket(zmq.SUB)
            self.status_socket = self.context.socket(zmq.PUB)
            
            self.logger.info("(COMMUNICATION MANAGER) ZeroMQ resources cleaned up and recreated")
            
        except Exception as e:
            self.logger.error(f"(COMMUNICATION MANAGER) Error cleaning up ZeroMQ resources: {e}")
            # Try to recreate the sockets even if cleanup fails
            try:
                self.context = zmq.Context()
                self.command_socket = self.context.socket(zmq.SUB)
                self.status_socket = self.context.socket(zmq.PUB)
                self.logger.info("(COMMUNICATION MANAGER) ZeroMQ resources recreated after error")
            except Exception as e2:
                self.logger.error(f"(COMMUNICATION MANAGER) Failed to recreate ZeroMQ resources: {e2}") 