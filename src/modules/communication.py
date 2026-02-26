#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Communication Manager

This class is responsible for handling all ZMQ-based messaging between
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

class Communication:
    def __init__(self,
                 config = None):
        """Initialize the communication manager
        
        Args:
            logger: Logger instance
            module_id: The unique identifier for this module
            config: Configuration manager for retrieving settings
        """
        self.logger = logging.getLogger(__name__)
        self.config = config
        
        self.group = self.config.get("module.group")
        
        # Control flags
        self.command_listener_running = False
        self.last_command = None
        
        # Controller connection info
        self.controller_ip = None
        self.controller_port = None
        
        # Connection state tracking
        self.connection_attempts = 0
        self.max_connection_attempts = self.config.get("network.reconnect_attempts", 5) if config else 5
        self.connection_delay = self.config.get("network.reconnect_delay", 5) if config else 5
        self.last_connection_time = None
        
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
            # Check if already connected to the same controller
            if (self.controller_ip == controller_ip and 
                self.controller_port == controller_port and
                self.command_listener_running):
                self.logger.info("Already connected to this controller")
                return True
            
            # Clean up existing connection if connecting to different controller
            if self.controller_ip and self.controller_ip != controller_ip:
                self.logger.info("Connecting to different controller, cleaning up existing connection")
                self.cleanup()
            
            # Store controller information
            self.controller_ip = controller_ip
            self.controller_port = controller_port
            
            # Get ports from config if available
            if self.config:
                command_port = self.config.get("communication.command_socket_port", 5555)
                status_port = self.config.get("communication.status_socket_port", 5556)
            else:
                command_port = 5555
                status_port = 5556
            
            # Set up command subscription
            self.subscribed_topics = []
            self.subscribe_to_topic(self.facade.get_module_id())
            self.subscribe_to_topic("all")
            #self.subscribe_to_topic(self.facade.get_module_type())
            group = self.group
            if group is not None and len(group) > 0:
                self.subscribe_to_topic(group)
            # self.logger.info(f"Module ID: {self.facade.get_module_id()}")
            # self.logger.info(f"Subscribing to topic: cmd/{self.facade.get_module_id()}")
            # self.command_socket.subscribe(f"cmd/{self.facade.get_module_id()}")
            # self.logger.info(f"Subscribing to topic: cmd/all")
            # self.command_socket.subscribe(f"cmd/all")
            
            # Connect sockets with timeout
            self.logger.info(f"Attempting to connect command socket to tcp://{controller_ip}:{command_port}")
            self.command_socket.connect(f"tcp://{controller_ip}:{command_port}")
            self.logger.info(f"Attempting to connect status socket to tcp://{controller_ip}:{status_port}")
            self.status_socket.connect(f"tcp://{controller_ip}:{status_port}")
            self.logger.info(f"Connected to controller command socket at {controller_ip}:{command_port}, status socket at {controller_ip}:{status_port}")
            
            # Reset connection tracking on successful connection
            self.connection_attempts = 0
            self.last_connection_time = time.time()
            
            return True
        except Exception as e:
            self.logger.error(f"Error connecting to controller: {e}")
            self.connection_attempts += 1
            return False

        
    def group_changed(self):
        old_group = self.group
        self.group = self.config.get("module.group")
        new_group = self.group

        if old_group is not None and len(old_group) > 0:
            self.unsubscribe_from_topic(old_group)

        if new_group is not None and len(new_group) > 0:
            self.subscribe_to_topic(new_group)


    def subscribe_to_topic(self, topic: str) -> None:
        full_topic = f"cmd/{topic}"
        self.logger.info(f"Subscribing to {full_topic}")
        self.command_socket.subscribe(full_topic)
        self.subscribed_topics.append(full_topic)

    
    def unsubscribe_from_topic(self, topic: str) -> None:
        if topic in self.subscribed_topics:
            self.subscribed_topics.pop(self.subscribed_topics.index(topic))
        self.command_socket.unsubscribe(topic)
        self.logger.info(f"Unsubscribed from {topic}")


    def start_command_listener(self) -> bool:
        """Start the command listener thread
        
        Returns:
            bool: True if the listener was started successfully
        """
        if self.command_listener_running:
            self.logger.info("Command listener already running")
            return False
        
        if not self.controller_ip:
            self.logger.error("Cannot start command listener: not connected to controller")
            return False
        
        self.command_listener_running = True
        self.command_thread = threading.Thread(target=self.listen_for_commands, daemon=True)
        self.command_thread.start()
        self.logger.info("Command listener thread started")
        return True


    def listen_for_commands(self):
        """Listen for commands from the controller"""
        # Commands look like cmd/<module_id> <command> <params>
        self.logger.info("Starting command listener thread")
        
        # Set socket timeout to prevent blocking indefinitely
        self.command_socket.setsockopt(zmq.RCVTIMEO, 5000)  # 5 second timeout
        
        while self.command_listener_running:
            try:
                # self.logger.info("Waiting for command...")
                message = self.command_socket.recv_string()
                # self.logger.info(f"Raw message received: {message} at {time.time()}")
                topic, command = message.split(' ', 1)
                # self.logger.info(f"Parsed topic: {topic}, command: {command}")
                
                # Store the command immediately after parsing
                self.last_command = command
                self.logger.info(f"Stored command: {self.last_command}")
                
                # Call the command handler
                try:
                    self.facade.handle_command(command)
                except Exception as e:
                    self.logger.error(f"Error handling command: {e}")

            except zmq.Again:
                # Timeout occurred, check if we should still be running
                if not self.command_listener_running:
                    break
                continue
            except Exception as e:
                if self.command_listener_running:  # Only log if we're still supposed to be running
                    self.logger.error(f"Error receiving command: {e}")
                    # Check if this is a connection error and attempt reconnection
                    if "Connection refused" in str(e) or "No route to host" in str(e):
                        self.logger.warning("Connection error detected, will attempt reconnection")
                        self._schedule_reconnection()
                time.sleep(0.1)  # Add small delay to prevent tight loop on error


    def _schedule_reconnection(self):
        """Schedule a reconnection attempt"""
        if self.connection_attempts < self.max_connection_attempts:
            self.connection_attempts += 1
            self.logger.info(f"Scheduling reconnection attempt {self.connection_attempts}/{self.max_connection_attempts} in {self.connection_delay} seconds")
            
            # Schedule reconnection in a separate thread
            def delayed_reconnect():
                time.sleep(self.connection_delay)
                if self.controller_ip and not self.command_listener_running:  # Only reconnect if we have controller info
                    self.logger.info(f"Attempting reconnection {self.connection_attempts}/{self.max_connection_attempts}")
                    self._attempt_reconnection()
            
            threading.Thread(target=delayed_reconnect, daemon=True).start()
        else:
            self.logger.warning(f"Max reconnection attempts ({self.max_connection_attempts}) reached")


    def _attempt_reconnection(self):
        """Attempt to reconnect to the controller"""
        try:
            if self.controller_ip and self.controller_port:
                # Attempt to reconnect
                if self.connect(self.controller_ip, self.controller_port):
                    # Restart command listener
                    if self.start_command_listener():
                        self.logger.info("Reconnection successful")
                    else:
                        self.logger.error("Failed to restart command listener after reconnection")
                else:
                    self.logger.error("Failed to reconnect to controller")
            else:
                self.logger.warning("No controller information available for reconnection")
        except Exception as e:
            self.logger.error(f"Error during reconnection attempt: {e}")


    def send_status(self, status_data: Dict[str, Any]) -> None:
        """Send status information to the controller
        
        Args:
            status_data: Dictionary containing status information
        """
        try:
            if not self.status_socket:
                self.logger.warning("Status socket not available")
                return
            
            # Add timestamp and module ID to status data
            status_data['timestamp'] = time.time()
            status_data['module_id'] = self.facade.get_module_id()
            status_data['module_name'] = self.facade.get_module_name()
            
            # Convert to JSON string
            import json
            message = json.dumps(status_data)
            
            # Send status
            self.status_socket.send_string(f"status/{self.facade.get_module_id()} {message}")
            # self.logger.info(f"Status sent: {message}")
            
        except Exception as e:
            self.logger.error(f"Error sending status: {e}")
            # Check if this is a connection error
            if "Connection refused" in str(e) or "No route to host" in str(e):
                self.logger.warning("Connection error while sending status, will attempt reconnection")
                self._schedule_reconnection()


    def cleanup(self):
        """Clean up ZMQ connections"""
        self.logger.info(f"Cleaning up communication manager for module {self.facade.get_module_id()}")
        
        # Stop threads
        self.command_listener_running = False
        
        # Give threads time to stop
        time.sleep(0.5)
        
        # Clean up ZeroMQ connections - important to do this in the right order
        try:
            # Step 1: Set all sockets to non-blocking with zero linger time
            if hasattr(self, 'command_socket') and self.command_socket:
                self.logger.info("Setting command socket linger to 0")
                self.command_socket.setsockopt(zmq.LINGER, 0)
                
            if hasattr(self, 'status_socket') and self.status_socket:
                self.logger.info("Setting status socket linger to 0")
                self.status_socket.setsockopt(zmq.LINGER, 0)
            
            # Step 2: Close all sockets
            if hasattr(self, 'command_socket') and self.command_socket:
                self.logger.info("Closing command socket")
                self.command_socket.close()
                self.command_socket = None
                
            if hasattr(self, 'status_socket') and self.status_socket:
                self.logger.info("Closing status socket")
                self.status_socket.close()
                self.status_socket = None
            
            # Step 3: Wait a bit for ZMQ to clean up internal resources
            time.sleep(0.1)
            
            # Step 4: Skip context termination to avoid hanging
            # Just close the context reference - ZeroMQ will clean up automatically
            if hasattr(self, 'context') and self.context:
                self.logger.info("Closing ZMQ context")
                self.context = None
            
            # Reset connection state
            self.controller_ip = None
            self.controller_port = None
            self.connection_attempts = 0
            
            # Add a small delay to ensure proper cleanup
            time.sleep(0.1)
            
            # Recreate sockets for future connections with error handling
            try:
                self.context = zmq.Context()
                self.command_socket = self.context.socket(zmq.SUB)
                self.status_socket = self.context.socket(zmq.PUB)
                self.logger.info("ZeroMQ resources cleaned up and recreated")
            except Exception as e:
                self.logger.error(f"Error recreating ZeroMQ resources: {e}")
                # Set to None to force recreation on next connection attempt
                self.context = None
                self.command_socket = None
                self.status_socket = None
            
        except Exception as e:
            self.logger.error(f"Error cleaning up ZeroMQ resources: {e}")
            # Reset connection state
            self.controller_ip = None
            self.controller_port = None
            self.connection_attempts = 0
            
            # Try to recreate the sockets even if cleanup fails
            try:
                time.sleep(0.1)  # Small delay before recreation
                self.context = zmq.Context()
                self.command_socket = self.context.socket(zmq.SUB)
                self.status_socket = self.context.socket(zmq.PUB)
                self.logger.info("ZeroMQ resources recreated after error")
            except Exception as e2:
                self.logger.error(f"Failed to recreate ZeroMQ resources: {e2}")
                # Set to None to force recreation on next connection attempt
                self.context = None
                self.command_socket = None
                self.status_socket = None
