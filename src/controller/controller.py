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
        self.export_interval = 10 # the interval at which to export data to the database
        self.max_buffer_size = 1000 # the maximum size of the buffer before exporting to database
        self.commands = ["get_status", "get_data", "start_stream", "stop_stream"] # list of commands
        
        # Control flags
        self.manual_control = True # whether to run in manual control mode
        self.print_received_data = False # whether to print received data
        self.is_exporting = False # whether the controller is currently exporting data to the database
        self.is_health_exporting = False # whether the controller is currently exporting health data to the database
        self.health_export_interval = 10 # the interval at which to export health data to the database

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
        self.logger.setLevel(logging.DEBUG)

        # Add console handler if none exists
        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

        # Health monitoring
        self.module_health = {} # dictionary to store the health of each module
        self.heartbeat_interval = 5 # the interval at which to check the health of each module
        self.heartbeat_timeout = 3 * self.heartbeat_interval # the timeout for a heartbeat

        # Start the zmq listener thread
        threading.Thread(target=self.listen_for_updates, daemon=True).start()

        # Start the data auto export thread
        threading.Thread(target=self.periodic_export, daemon=True).start()

        # Start the health monitoring thread
        threading.Thread(target=self.monitor_module_health, daemon=True).start()

        # Start the health data auto export thread
        threading.Thread(target=self.periodic_health_export, daemon=True).start()

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
                self.logger.debug(f"Received update: {message}")
                # Handle different topics
                if topic.startswith('status/'):
                    self.handle_status_update(topic, data)
                elif topic.startswith('data/'):
                    self.handle_data_update(topic, data)
            except Exception as e:
                self.logger.error(f"Error handling update: {e}")

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        self.logger.debug(f"Status update received from module {topic} with data: {data}")
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
            self.logger.debug(f"Module {module_id} is online with status: {self.module_health[module_id]}")   

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

    # Database methods
    def export_buffered_data(self):
        """Export the local buffer to the database"""
        try:
            for module_id, data_list in self.module_data.items(): # for each module in the buffer
                if data_list:  # If there's data to upload
                    # find the module type by seaching through the modules list
                    module_type = next((module.type for module in self.modules if module.id == module_id), 'unknown')
                    self.logger.debug(f"Preparing to upload for module: {module_id}, type: {module_type}")
                    self.logger.debug(f"Data to upload: {data_list}")

                    # format data for upload    
                    records = [{
                        "module_id": module_id,
                        "module_type": module_type,
                        "timestamp": item['timestamp'],
                        "data": item['data']
                    } for item in data_list]
                    self.logger.debug(f"Records formatted for upload: {records}")

                    # upload data to database
                    response = supabase_client.table("controller_test").insert(records).execute()
                    self.logger.debug(f"Supabase response: {response}")

                    # Clear uploaded data from buffer
                    self.module_data[module_id] = []
                    
        except Exception as e:
            self.logger.error(f"Failed to upload data: {e}")
            print(f"DEBUG - Full error: {str(e)}")
        
    def periodic_export(self):
        """Periodically export the local buffer to the database"""
        self.logger.debug("Periodic export function called")
        while True:
            while self.is_exporting:
                self.logger.debug("Periodic export function saw that is_exporting is true")
                try:
                    self.logger.info("Starting periodic export from buffer...")
                    self.export_buffered_data()
                    self.logger.info("Periodic export completed successfully")
                except Exception as e:
                    self.logger.error(f"Error during periodic export: {e}")
                time.sleep(self.export_interval)
        
    # Monitor health
    def monitor_module_health(self):
        """Monitor the health of each module"""
        while True: 
            current_time = time.time() # get current time to compare to last heartbeat
            for module_id in list(self.module_health.keys()): # iterate over all modules
                last_heartbeat = self.module_health[module_id]['last_heartbeat'] # get last heartbeat time
                if current_time - last_heartbeat > self.heartbeat_timeout: # if the last heartbeat was more than 3 intervals ago
                    self.logger.warning(f"Module {module_id} has not sent a heartbeat in the last {self.heartbeat_timeout} seconds. Marking as offline.")
                    self.module_health[module_id]['status'] = 'offline'
            time.sleep(1)
                    
    def export_health_data(self):
        """Export the local health data to the database"""
        self.logger.debug("Export health data function called")
        if not self.module_health:
            self.logger.debug("Export health data function saw that module_health is empty")
            self.logger.info("No health data to export")
            return

        try:
            self.logger.debug("Export health data function saw that module_health is not empty")
            records = [
                {
                    "module_id": module_id,
                    "timestamp": health["last_heartbeat"],
                    "status": health["status"],
                    "cpu_temp": health["cpu_temp"],
                    "cpu_usage": health["cpu_usage"],
                    "memory_usage": health["memory_usage"],
                    "disk_space": health["disk_space"],
                    "uptime": health["uptime"]
                }
                for module_id, health in self.module_health.items()
            ]
        
            response=supabase_client.table("module_health").insert(records).execute()
            self.logger.debug(f"Uploaded {len(records)} health records to Supabase")
            self.logger.debug(f"Supabase response: {response}")
            self.logger.debug("Export health data function saw that supabase response is good")

        except Exception as e:
            self.logger.debug("Export health data function saw that there was an error exporting health data")
            self.logger.error(f"Error exporting health data: {e}")
                
    def periodic_health_export(self):
        """Periodically export the local health data to the database"""
        self.logger.debug("Periodic health export function called")
        while True:
            if self.is_health_exporting:
                self.logger.debug("Periodic health export function saw is_health_exporting is true")
                try:
                    self.export_health_data()
                except Exception as e:
                    self.logger.error(f"Error exporting health data: {e}")
                time.sleep(self.health_export_interval)


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
                    case "help":
                        print("Available commands:")
                        print("  help - Show this help message")
                        print("  quit - Quit the manual control loop")
                        print("  list - List available modules")
                        print("  supabase get test - Test retrieving a couple entries from supabase")
                        print("  supabase insert test - Insert test data into supabase")
                        print("  supabase export - Export the local buffer to the database")
                        print("  zmq send - Send a command to a specific module via zeromq")
                        print("  read buffer - Read the local buffer for a given module")
                        print("  size buffer - Print the size of the local buffer for a given module")
                        print("  start export - Periodically export the local buffer to the database")
                        print("  stop export - Stop the periodic export of the local buffer to the database ")
                        print("  start health export - Periodically export the local health data to the database")
                        print("  stop health export - Stop the periodic export of the local health data to the database")
                        print("  health status - Print the health status of all modules")
                        print("  check export - Check if the controller is currently exporting data to the database")
                    case "quit":
                        self.logger.info("Quitting manual control loop")
                        break
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
                            .limit(2)
                            .execute()
                        )
                        print(response)
                    case "supabase export":
                        # Export the local buffer to the database
                        self.export_buffered_data()
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
                    case "read buffer":
                        # read local buffer
                        print(self.module_data)
                    case "size buffer":
                        # print the size of the local buffer for a given module
                        i=1
                        for module in self.modules:
                            print(f"{i}. {module.name}")
                            i+=1
                        module_id = input("Chosen module: ")
                        print(len(self.module_data[self.modules[int(module_id)-1].id]))
                    case "start export":
                        # Start auto exporting buffer data to databsae
                        print("Starting auto export...")
                        self.is_exporting = True
                    case "start health export":
                        # Start auto exporting health data to databsae
                        print("Start health export command called")
                        self.is_health_exporting = True
                    case "stop export":
                        # Stop auto exporting buffer data to databsae
                        print("Stopping auto export...")
                        self.is_exporting = False
                    case "stop health export":
                        # Stop auto exporting health data to databsae
                        print("Stopping health export...")
                        self.is_health_exporting = False
                    
                    case "check export":
                        print(f"Exporting is currently: {self.is_exporting}")
                        print(f"Health exporting is currently: {self.is_health_exporting}")

                    case "health status":
                        print("\nModule Health Status:")
                        if not self.module_health:
                            print("No modules reporting health data")
                        for module_id, health in self.module_health.items():
                            print(f"\nModule: {module_id}")
                            print(f"Status: {health['status']}")
                            print(f"CPU Usage: {health.get('cpu_usage', 'N/A')}%")
                            print(f"Memory Usage: {health.get('memory_usage', 'N/A')}%")
                            print(f"Temperature: {health.get('cpu_temp', 'N/A')}°C")
                            print(f"Disk Space: {health.get('disk_space', 'N/A')}%")
                            #print(f"PTP Offset: {health.get('ptp_offset', 'N/A')}ns")
                            print(f"Uptime: {health.get('uptime', 'N/A')}s")
                            print(f"Last Heartbeat: {time.strftime('%H:%M:%S', time.localtime(health['last_heartbeat']))}")
                time.sleep(0.1)
        else:
            print("Starting automatic loop (not implemented yet)")
            # @TODO: Implement automatic loop

        return True
        
# Main entry point
def main():
    """Main entry point for the controller application"""
    controller = HabitatController()

    # Start the main loop
    controller.start()

    # Keep running until interrupted
    # @TODO: Implement a proper shutdown. At present I don't think this is triggered, as it's already looping from controller.start()
    # try:
    #     while True:
    #         time.sleep(1)
    # except KeyboardInterrupt:
    #     print("\nShutting down...")
    #     controller.stop()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
