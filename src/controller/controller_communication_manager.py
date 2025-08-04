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
import json

class ControllerCommunicationManager:
    def __init__(self, logger: logging.Logger, 
                 status_callback: Callable[[str, str], None] = None, # Use callbacks to handle status updates in the controller.py program, not here.
                 data_callback: Callable[[str, str], None] = None): # Use callbacks to handle data updates in the controller.py program, not here.
        """Initialize the communication manager"""
        self.logger = logger
        self.is_running = True
        self.status_callback = None
        self.data_callback = None
        
        # ZeroMQ setup
        # for sending commands to modules
        # TODO: Change this to a REQ socket
        self.context = zmq.Context() # context object to contain all sockets
        self.command_socket = self.context.socket(zmq.PUB) # publisher socket for sending commands
        self.command_socket.bind("tcp://*:5555") # bind the socket to a port

        # for receiving status updates from modules
        self.status_socket = self.context.socket(zmq.SUB) # subscriber socket for receiving status updates
        self.status_socket.subscribe("status/") # subscribe to status updates
        self.status_socket.subscribe("data/") # subscribe to data updates - is this necessary?
        self.status_socket.bind("tcp://*:5556") # bind the socket to a port - modules will connect to this

        # Start the zmq listener thread
        self.listener_thread = threading.Thread(target=self.listen_for_updates, daemon=True)
        self.listener_thread.start()

        # Register callbacks
        self.register_callbacks(status_callback, data_callback)

    def register_callbacks(self, status_callback: Callable[[str, str], None], data_callback: Callable[[str, str], None]):
        """Register callbacks for status and data updates"""
        self.status_callback = status_callback
        self.data_callback = data_callback

    # ZeroMQ methods
    def send_command(self, module_id: str, command: str, params: Dict):
        """Send a command to a specific module"""
        # Handle params
        if not params:
            self.logger.info(f"(COMMUNICATION MANAGER) No params provided - was this a mistake?")
            params = {}
        json_params = json.dumps(params)

        # Send message
        message = f"cmd/{module_id} {command} {json_params}"
        self.command_socket.send_string(message)
        self.logger.info(f"(COMMUNICATION MANAGER) Command sent: {message}")

    def listen_for_updates(self):
        """Listen for status and data updates from modules"""
        while self.is_running:  # Check is_running flag
            try:
                # Use a timeout on recv to allow checking is_running flag
                message = self.status_socket.recv_string(zmq.NOBLOCK)
                topic, data = message.split(' ', 1)
                self.logger.debug(f"(COMMUNICATION MANAGER) Received update: {message}")
                
                if topic.startswith('status/'): # If status message, pass it to the status callback
                    self.handle_status_update(topic, data)
                elif topic.startswith('data/'): # If data message, pass it to the data callback
                    self.handle_data_update(topic, data)
                    
            except zmq.Again:
                # No message available, continue to check is_running
                time.sleep(0.1)
            except zmq.error.ContextTerminated:
                # Context was terminated, exit gracefully
                break
            except Exception as e:
                if self.is_running:  # Only log errors if we're still running
                    self.logger.error(f"(COMMUNICATION MANAGER) Error handling update: {e}")
                break

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module, and pass it to the callback, which passes it to the controller"""
        if self.status_callback:
            self.status_callback(topic, data)

    def handle_data_update(self, topic: str, data: str):
        """Handle a data update from a module, and pass it to the callback, which passes it to the controller"""
        if self.data_callback:
            self.data_callback(topic, data)

    def cleanup(self):
        """Clean up ZMQ connections and export any remaining data"""
        self.logger.info("(COMMUNICATION MANAGER) Cleaning up controller communication manager...")
        
        # First, stop the listener thread
        self.is_running = False
        
        # Wait for listener thread to finish
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2)  # Wait up to 2 seconds
        
        # Now clean up ZeroMQ sockets
        try:
            if hasattr(self, 'command_socket'):
                self.logger.info("(COMMUNICATION MANAGER) Closing command socket")
                self.command_socket.setsockopt(zmq.LINGER, 1000)
                self.command_socket.close()
            if hasattr(self, 'status_socket'):
                self.logger.info("(COMMUNICATION MANAGER) Closing status socket")
                self.status_socket.setsockopt(zmq.LINGER, 1000)
                self.status_socket.close()
            if hasattr(self, 'context'):
                self.logger.info("(COMMUNICATION MANAGER) Terminating ZeroMQ context")
                self.context.term()
        except Exception as e:
            self.logger.error(f"(COMMUNICATION MANAGER) Error during ZeroMQ cleanup: {e}")

        self.logger.info("(COMMUNICATION MANAGER) Controller communication manager cleanup complete")
