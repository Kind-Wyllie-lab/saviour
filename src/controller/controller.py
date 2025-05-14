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
import asyncio # for asyncio

# Networking and synchronization
import socket # for network communication
import threading # for concurrent operations
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import zmq # for zeromq communication

# Local modules
import src.controller.controller_service_manager as service_manager
import src.controller.controller_communication_manager as communication_manager
import src.controller.controller_session_manager as session_manager
import src.controller.controller_file_transfer_manager as file_transfer_manager
import src.controller.controller_data_export_manager as data_export_manager


# Optional: For NWB format support
try:
    import pynwb
    from pynwb import NWBFile, NWBHDF5IO
    NWB_AVAILABLE = True
except ImportError:
    NWB_AVAILABLE = False
    logging.warning("PyNWB not available. NWB file export will be disabled.")

    
# Habitat Controller Class
class Controller:
    """Main controller class for the habitat system"""
    
    def __init__(self):
        """Initialize the controller with default values"""

        # Parameters
        self.module_data = {} # store data from modules before exporting to database
        self.max_buffer_size = 1000 # the maximum size of the buffer before exporting to database
        self.commands = ["get_status", "get_data", "start_stream", "stop_stream", "record_video"] # list of commands
        
        # Control flags
        self.manual_control = True # whether to run in manual control mode
        self.print_received_data = False # whether to print received data
        self.is_running = True  # Add flag for listener thread

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
        self.heartbeat_interval = 30 # the interval at which to check the health of each module
        self.heartbeat_timeout = 3 * self.heartbeat_interval # the timeout for a heartbeat

        # Managers
        self.service_manager = service_manager.ControllerServiceManager(self.logger)
        self.session_manager = session_manager.SessionManager()
        self.communication_manager = communication_manager.ControllerCommunicationManager(
            self.logger,
            status_callback=self.handle_status_update,
            data_callback=self.handle_data_update
        )
        self.file_transfer = file_transfer_manager.ControllerFileTransfer(self.logger)
        self.data_export_manager = data_export_manager.ControllerDataExportManager(self.logger)

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
        module_id = topic.split('/')[1]
        timestamp = time.time()
        
        # store in local buffer
        if module_id not in self.module_data:
            self.module_data[module_id] = []

        # append data to buffer
        self.module_data[module_id].append({
            "timestamp": timestamp,
            "data": data,
        })

        # prevent buffer from growing too large
        if len(self.module_data[module_id]) > self.max_buffer_size:
            self.logger.warning(f"Buffer for module {module_id} is too large. Exporting to database.")
            self.data_export_manager.export_module_data(self.module_data, self.service_manager)

        if self.print_received_data:
            print(f"Data update received from module {module_id} with data: {self.module_data[module_id]}")

    def stop(self) -> bool:
        """Stop the controller and clean up resources"""
        self.logger.info("Stopping controller...")
        
        try:
            # Stop all threads by setting flags
            self.is_running = False
            
            # Clean up module health tracking
            self.logger.info("Cleaning up module health tracking")
            self.module_health.clear()
            
            # Clean up module data buffer
            self.logger.info("Cleaning up module data buffer")
            self.module_data.clear()
            
            # Clean up modules list
            self.logger.info("Cleaning up modules list")
            self.service_manager.modules.clear()

            # Clean up service manager
            self.service_manager.cleanup()
            
            # Clean up communication manager
            self.communication_manager.cleanup()
            
            # Clean up data export manager
            self.data_export_manager.stop_all_exports()
            
            # Give modules time to detect the controller is gone
            time.sleep(1)
            
            self.logger.info("Controller stopped successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping controller: {e}")
            return False

    # Main methods
    def start(self) -> bool:
        """
        Start the controller.
        
        Returns:
            bool: True if the controller started successfully, False otherwise.
        """
        self.logger.info("Starting controller")

        # Start file transfer server
        try:
            # Create event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Start the file transfer server
            self.logger.info("Starting file transfer server...")
            loop.run_until_complete(self.file_transfer.start())
            
            # Keep the event loop running
            def run_event_loop():
                loop.run_forever()
            
            self.file_transfer_thread = threading.Thread(target=run_event_loop, daemon=True)
            self.file_transfer_thread.start()
            self.logger.info("File transfer server started successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to start file transfer server: {e}")
            return False

        # Start the server
        if self.manual_control:
            self.logger.info("Starting manual control loop")
            while True:
                # Get user input
                print("\nEnter a command (type help for list of commands): ", end='', flush=True)
                try:
                    user_input = input().strip()
                    if not user_input:
                        continue
                        
                    match user_input:
                        case "help":
                            print("Available commands:")
                            print("  help - Show this help message")
                            print("  quit - Quit the manual control loop")
                            print("  list - List available modules discovered by zeroconf")
                            print("  supabase get test - Test retrieving a couple entries from supabase")
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
                            print("  session_id  - Generate a session_id")
                        case "quit":
                            self.logger.info("Quitting manual control loop")
                            break
                        case "list":
                            print("Available modules:")
                            for module in self.service_manager.modules:
                                print(f"  ID: {module.id}, Type: {module.type}, IP: {module.ip}")
                            if not self.service_manager.modules:
                                print("No modules found")
                        case "zmq send":
                            # send a command to module from list of modules
                            if not self.service_manager.modules:
                                print("No modules available")
                                continue
                                
                            print("\nAvailable modules:")
                            for i, module in enumerate(self.service_manager.modules, 1):
                                print(f"{i}. {module.name}")
                            
                            try:
                                module_idx = int(input("\nChosen module: ").strip()) - 1
                                if not 0 <= module_idx < len(self.service_manager.modules):
                                    print("Invalid module selection")
                                    continue
                                    
                                print("\nAvailable commands:")
                                for i, cmd in enumerate(self.commands, 1):
                                    print(f"{i}. {cmd}")
                                    
                                cmd_idx = int(input("\nChosen command: ").strip()) - 1
                                if not 0 <= cmd_idx < len(self.commands):
                                    print("Invalid command selection")
                                    continue
                                    
                                self.communication_manager.send_command(self.service_manager.modules[module_idx].id, self.commands[cmd_idx])
                            except ValueError:
                                print("Invalid input - please enter a number")
                                continue
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
                                print(f"Uptime: {health.get('uptime', 'N/A')}s")
                                print(f"Last Heartbeat: {time.strftime('%H:%M:%S', time.localtime(health['last_heartbeat']))}")
                        case "supabase export":
                            success = self.data_export_manager.export_module_data(self.module_data, self.service_manager)
                            if success:
                                print("Data exported successfully")
                            else:
                                print("Failed to export data")
                        case "start export":
                            self.data_export_manager.start_periodic_data_export(
                                self.module_data, 
                                self.service_manager, 
                                self.export_interval
                            )
                            print("Started periodic data export")
                        case "stop export":
                            self.data_export_manager.stop_periodic_data_export()
                            print("Stopped periodic data export")
                        case "start health export":
                            self.data_export_manager.start_periodic_health_export(
                                self.module_health, 
                                self.health_export_interval
                            )
                            print("Started periodic health export")
                        case "stop health export":
                            self.data_export_manager.stop_periodic_health_export()
                            print("Stopped periodic health export")
                        case "check export":
                            status = self.data_export_manager.get_export_status()
                            print(f"Data export active: {status['data_exporting']}")
                            print(f"Health export active: {status['health_exporting']}")
                except Exception as e:
                    self.logger.error(f"Error handling input: {e}")
        else:
            print("Starting automatic loop (not implemented yet)")
            # @TODO: Implement automatic loop

        return True
        
