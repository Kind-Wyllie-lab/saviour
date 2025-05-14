#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Communication Manager

The communication manager is responsible for handling all ZMQ-based messaging between
the controller and modules, including:
- Command publishing to modules
- Status/data subscription from modules
- Message routing and handling
"""

import zmq
import threading
import logging
import time
from typing import Callable, Dict, Any

class ControllerCommunicationManager:
    def __init__(self, logger: logging.Logger, 
                 status_callback: Callable[[str, str], None] = None, # Use callbacks to handle status updates in the controller.py program, not here.
                 data_callback: Callable[[str, str], None] = None): # Use callbacks to handle data updates in the controller.py program, not here.
        """Initialize the communication manager"""
        self.logger = logger
        self.status_callback = status_callback
        self.data_callback = data_callback
        self.is_running = True
        
        # ZeroMQ setup
        # for sending commands to modules
        self.context = zmq.Context() # context object to contain all sockets
        self.command_socket = self.context.socket(zmq.PUB) # publisher socket for sending commands
        self.command_socket.bind("tcp://*:5555") # bind the socket to a port

        # for receiving status updates from modules
        self.status_socket = self.context.socket(zmq.SUB) # subscriber socket for receiving status updates
        self.status_socket.subscribe("status/") # subscribe to status updates
        self.status_socket.subscribe("data/") # subscribe to data updates
        self.status_socket.bind("tcp://*:5556") # bind the socket to a port

        # Start the zmq listener thread
        self.listener_thread = threading.Thread(target=self.listen_for_updates, daemon=True)
        self.listener_thread.start()
    

    # ZeroMQ methods
    def send_command(self, module_id: str, command: str):
        """Send a command to a specific module"""
        message = f"cmd/{module_id} {command}"
        self.command_socket.send_string(message)
        self.logger.info(f"Command sent: {message}")

    def listen_for_updates(self):
        """Listen for status and data updates from modules"""
        while self.is_running:  # Check is_running flag
            try:
                message = self.status_socket.recv_string()
                topic, data = message.split(' ', 1)
                self.logger.debug(f"Received update: {message}")
                # Handle different topics
                if topic.startswith('status/'):
                    self.handle_status_update(topic, data)
                elif topic.startswith('data/'):
                    self.handle_data_update(topic, data)
            except Exception as e:
                if self.is_running:  # Only log errors if we're still running
                    self.logger.error(f"Error handling update: {e}")
                time.sleep(0.1)  # Add small delay to prevent tight loop on error
    
    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        if self.status_callback:
            self.status_callback(topic, data)

    def handle_data_update(self, topic: str, data: str):
        """Buffer incoming data from modules"""
        if self.data_callback:
            self.data_callback(topic, data)

    def cleanup(self):
        """Clean up ZMQ connections and export any remaining data"""
        self.logger.info("Cleaning up controller communication manager...")
        
        # Now clean up ZeroMQ sockets
        try:
            if hasattr(self, 'command_socket'):
                self.logger.info("Closing command socket")
                # Set a reasonable linger time to allow messages to be sent
                self.command_socket.setsockopt(zmq.LINGER, 1000)  # 1 second
                self.command_socket.close()
            if hasattr(self, 'status_socket'):
                self.logger.info("Closing status socket")
                # Set a reasonable linger time to allow messages to be sent
                self.status_socket.setsockopt(zmq.LINGER, 1000)  # 1 second
                self.status_socket.close()
            if hasattr(self, 'context'):
                self.logger.info("Terminating ZeroMQ context")
                self.context.term()
        except Exception as e:
            self.logger.error(f"Error during ZeroMQ cleanup: {e}")

        self.logger.info("Controller communication manager cleanup complete")
