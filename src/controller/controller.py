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
import supabase # for supabase client, the external database
import uuid # for unique id generation
from dataclasses import dataclass # to define Module dataclass
from typing import List, Dict, Any # for type hinting

# Networking and synchronization
import socket # for network communication
import threading # for concurrent operations
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import zmq # for zeromq communication

# Local modules
import src.shared.ptp as ptp
import src.shared.network as network

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(SUPABASE_URL, SUPABASE_KEY) # supabase client object which is used to interact with the database

# Optional: For NWB format support
try:
    import pynwb
    from pynwb import NWBFile, NWBHDF5IO
    NWB_AVAILABLE = True
except ImportError:
    NWB_AVAILABLE = False
    logging.warning("PyNWB not available. NWB file export will be disabled.")

@dataclass
class Module:
    """Dataclass to represent a module in the habitat system"""
    id: str
    name: str
    type: str
    ip: str
    port: int
    properties: Dict[str, Any]
    
# Habitat Controller Class
class HabitatController:
    """Main controller class for the habitat system"""
    
    def __init__(self):
        """Initialize the controller with default values"""

        # Parameters
        self.modules: List[Module] = [] # list of discovered modules
        self.module_data = {} # store data from modules before exporting to database
        self.manual_control = True # whether to run in manual control mode
        self.commands = ["get_status", "get_data", "start_stream", "stop_stream"] # list of commands
        
        # zeroconf
        self.zeroconf = Zeroconf()
        self.service_info = ServiceInfo(
            "_controller._tcp.local.", # the service type - tcp protocol, local domain
            "controller._controller._tcp.local.", # a unique name for the service to advertise itself
            addresses=[socket.inet_aton("192.168.1.1")], # the ip address of the controller
            port=5000, # the port number of the controller
            properties={'type': 'controller'} # the properties of the service
        )
        self.zeroconf.register_service(self.service_info) # register the service with the above info
        self.browser = ServiceBrowser(self.zeroconf, "_module._tcp.local.", self) # Browse for habitat_module services
        
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

        # Setup logging
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)

        # Start the zmq listener thread
        threading.Thread(target=self.listen_for_updates, daemon=True).start()

    # zeroconf methods
    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules"""
        self.logger.info(f"Removing module: {name}")
        self.modules = [module for module in self.modules if module.name != name] # remove the module from the list

    def add_service(self, zeroconf, service_type, name):
        """Add a service to the list of discovered modules"""
        self.logger.info(f"Discovered module: {name}")
        info = zeroconf.get_service_info(service_type, name)
        if info:
            module = Module(
                id=str(info.properties.get(b'id', b'unknown').decode()),
                name=name,
                type=info.properties.get(b'type', b'unknown').decode(),
                ip=socket.inet_ntoa(info.addresses[0]),
                port=info.port,
                properties=info.properties
            )
            self.modules.append(module)
            self.logger.info(f"Discovered module: {module}")

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")

    # ZeroMQ methods
    def send_command(self, module_id: str, command: str):
        """Send a command to a specific module"""
        message = f"cmd/{module_id} {command}"
        self.command_socket.send_string(message)
        self.logger.info(f"Command sent: {message}")

    def listen_for_updates(self):
        """Listen for status and data updates from modules"""
        while True:
            try:
                message = self.status_socket.recv_string()
                topic, data = message.split(' ', 1)
                self.logger.info(f"Received update: {message}")
                # Handle different topics
                if topic.startswith('status/'):
                    self.handle_status_update(topic, data)
                elif topic.startswith('data/'):
                    self.handle_data_update(topic, data)
            except Exception as e:
                self.logger.error(f"Error handling update: {e}")

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        self.logger.info(f"Status update received from module {topic} with data: {data}")
        print(f"Status update received from module {topic} with data: {data}")

    def handle_data_update(self, topic: str, data: str):
        """Handle a data update from a module"""
        self.logger.info(f"Data update received from module {topic} with data: {data}")
        module_id = topic.split('/')[1] # get module id from topic
        timestamp = time.time() # time at which the data was received
        # store locally
        if module_id not in self.module_data:
            self.module_data[module_id] = []
        self.module_data[module_id].append({
            "timestamp": timestamp,
            "data": data
        })

        print(f"Data update received from module {module_id} with data: {self.module_data[module_id]}")

        # TODO: Export to database

    # Main methods
    def start(self) -> bool:
        """
        Start the controller.
        
        Returns:
            bool: True if the controller started successfully, False otherwise.
        """
        self.logger.info("Starting controller")

        # Activate ptp
        self.logger.debug("Starting ptp4l.service")
        ptp.stop_ptp4l() # Stop
        ptp.restart_ptp4l() # Restart
        time.sleep(1) # Wait for 1 second
        self.logger.debug("Starting phc2sys.service")
        ptp.stop_phc2sys() # Stop
        ptp.restart_phc2sys() # Restart

        # Start the server
        if self.manual_control:
            self.logger.info("Starting manual control loop")
            while True:
                self.logger.info("Manual control loop running...")
                # Get user input
                user_input = input("Enter a command (type help for list of commands): ")
                match user_input:
                    case "quit":
                        self.logger.info("Quitting manual control loop")
                        break
                    case "help":
                        print("Available commands:")
                        print("  quit - Quit the manual control loop")
                        print("  help - Show this help message")
                        print("  list - List available modules")
                        print("  supabase get test - Get test data from supabase")
                        print("  supabase insert test - Insert test data into supabase")
                        print("  zeroconf add - Add a service to the list of discovered modules")
                        print("  zeroconf remove - Remove a service from the list of discovered modules")
                        print("  zmq send - Send a command to a specific module via zeromq")
                    case "list":
                        print("Available modules:")
                        for module in self.modules:
                            print(f"  ID: {module.id}, Type: {module.type}, IP: {module.ip}")
                        if not self.modules:
                            print("No modules found")
                    case "supabase get test":
                        # Get test data from supabase
                        response = (supabase_client.table("controller_test")
                            .select("*")
                            .execute()
                        )
                        print(response)
                    case "supabase insert test":
                        # Insert test data into supabase
                        response = (supabase_client.table("controller_test")
                            .insert({
                                "type": "test",
                                "value": "Test entry inserted from test_supabase.py script at " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })
                            .execute()
                        )
                        print(response)
                    case "zeroconf add":
                        # Add a service to the list of discovered modules
                        test_service = ServiceInfo(
                            "_module._tcp.local.",
                            "test_module._module._tcp.local.",
                            addresses=[socket.inet_aton("192.168.1.2")],
                            port=5000,
                            properties={'type': 'camera',
                            'id': uuid.uuid4()}
                        )
                        self.zeroconf.register_service(test_service)
                    case "zeroconf remove":
                        # Remove a service from the list of discovered modules
                        self.remove_service(self.zeroconf, "_habitat._tcp.local.", "test")
                    case "zmq send":
                        # send a command to module from list of modules
                        i=1
                        for module in self.modules:
                            print(f"{i}. {module.name}")
                            i+=1
                        module_id = input("Chosen module: ")
                        i=1
                        for command in self.commands:
                            print(f"{i}. {command}")
                            i+=1
                        command = input("Chosen command: ")
                        self.send_command(self.modules[int(module_id)-1].id, self.commands[int(command)-1])
                time.sleep(1)
        else:
            print("Starting automatic loop (not implemented yet)")

        return True
        
# Main entry point
def main():
    """Main entry point for the controller application"""
    controller = HabitatController()

    # Start the main loop
    controller.start()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
