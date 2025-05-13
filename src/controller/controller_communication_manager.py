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
    def __init__(self, logger: logging.Logger):
        """Initialize the communication manager"""
        self.logger = logger


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
        self.logger.info(f"Status update received from module {topic} with data: {data}")
        module_id = topic.split('/')[1] # get module id from topic
        try:
            status_data = eval(data) # Convert string data to dictionary

            # Update local health tracking
            self.module_health[module_id] = {
                'last_heartbeat': status_data['timestamp'],  # Use the module's timestamp
                'status': 'online',
                'cpu_temp': status_data['cpu_temp'],
                'cpu_usage': status_data['cpu_usage'],
                'memory_usage': status_data['memory_usage'],
                'uptime': status_data['uptime'],
                'disk_space': status_data['disk_space']
            }
            self.logger.info(f"Module {module_id} is online with status: {self.module_health[module_id]}")   

        except Exception as e:
            self.logger.error(f"Error parsing status data for module {module_id}: {e}")
            
    def handle_data_update(self, topic: str, data: str):
        """Buffer incoming data from modules"""
        self.logger.info(f"Data update received from module {topic} with data: {data}")
        module_id = topic.split('/')[1] # get module id from topic
        timestamp = time.time() # time at which the data was received
        
        # store in local buffer
        if module_id not in self.module_data: # if module id not in buffer, create a new buffer entry
            self.module_data[module_id] = []

        # append data to buffer
        self.module_data[module_id].append({
            "timestamp": timestamp,
            "data": data,
            #"type": self.modules[module_id].type
        })

        # prevent buffer from growing too large
        if len(self.module_data[module_id]) > self.max_buffer_size:
            self.logger.warning(f"Buffer for module {module_id} is too large. Exporting to database.")
            self.export_buffered_data(module_id)

        if self.print_received_data:
            print(f"Data update received from module {module_id} with data: {self.module_data[module_id]}")

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


        # Close ZMQ sockets
        if hasattr(self, 'subscriber'):
            self.subscriber.close()
        if hasattr(self, 'context'):
            self.context.term()
            
        self.logger.info("Controller communication manager cleanup complete")
