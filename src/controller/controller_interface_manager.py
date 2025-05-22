#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Interface

Handles user interaction with the habitat controller, including:
- Manual control CLI
- Command parsing and execution
- Help system and module listing
"""

import logging
import time
from flask import Flask, render_template
from flask_socketio import SocketIO
from src.controller.web_interface_manager import WebInterfaceManager

class ControllerInterfaceManager:
    def __init__(self, controller):
        """Initialize the controller interface"""
        self.controller = controller # Pass through the controller object so we can access logger, config, module data etc
        self.logger = controller.logger

        # Check which interfaces are enabled
        if self.controller.config_manager.get("interface.web_interface") == True:
            self.logger.info(f"(INTERFACE MANAGER) Web interface flag set to True")
            self.web_interface = True # Flag to indicate if the web interface is enabled
            self.web_interface_manager = WebInterfaceManager(self.logger)
        else:
            self.logger.info(f"(INTERFACE MANAGER) Web interface flag set to False")
            self.web_interface = False

        if self.controller.config_manager.get("interface.cli") == True:
            self.logger.info(f"(INTERFACE MANAGER) CLI interface flag set to True")
            self.cli_interface = True
        else:
            self.logger.info(f"(INTERFACE MANAGER) CLI interface flag set to False")
            self.cli_interface = False
    
    def start(self):
        """Start the interface manager"""
        self.logger.info(f"(INTERFACE MANAGER) Starting interface manager")
        if self.web_interface == True:
            self.logger.info(f"(INTERFACE MANAGER) Starting web interface (NOT YET IMPLEMENTED...)")
            # self.web_interface_manager.start()
        
        if self.cli_interface == True:
            self.logger.info(f"(INTERFACE MANAGER) Starting manual control loop")
            self.run_manual_control()

    def run_manual_control(self):
        """Run the manual control loop"""
        self.logger.info("(INTERFACE MANAGER) Starting manual CLI control loop")
        while True:
            # Get user input
            print("\nEnter a command (type help for list of commands): ", end='', flush=True)
            try:
                user_input = input().strip()
                if not user_input:
                    continue
                        
                self.handle_command(user_input)
                    
            except Exception as e:
                self.logger.error(f"(INTERFACE MANAGER) Error handling input: {e}")
    
    def handle_command(self, command):
        """Handle a single command"""
        match command:
            case "help":
                self.show_help()
            case "quit":
                self.logger.info("(INTERFACE MANAGER) Quitting manual control loop")
                return False  # Signal to exit
            case "list":
                self.list_modules()
            case "zmq send":
                self.handle_zmq_command()
            case "health status":
                self.show_health_status()
            case "supabase export":
                self.handle_supabase_export()
            case "start export":
                self.handle_start_export()
            case "stop export":
                self.handle_stop_export()
            case "start health export":
                self.handle_start_health_export()
            case "stop health export":
                self.handle_stop_health_export()
            case "check export":
                self.handle_check_export()
            case "session_id":
                self.handle_session_id()
            case "show buffer":
                self.handle_show_buffer()
            case _:
                self.logger.error(f"(INTERFACE MANAGER) Unknown command: {command}. Type 'help' for available commands.")
    
    def show_help(self):
        """Display available commands"""
        print("Available commands:")
        print("  help - Show this help message")
        print("  quit - Quit the manual control loop")
        print("  list - List available modules discovered by zeroconf")
        print("  supabase export - Export the local buffer to the database")
        print("  zmq send - Send a command to a specific module via zeromq")
        print("  health status - Print the health status of all modules")
        print("  start export - Periodically export the local buffer to the database")
        print("  stop export - Stop the periodic export of the local buffer to the database")
        print("  start health export - Periodically export the local health data to the database")
        print("  stop health export - Stop the periodic export of the local health data to the database")
        print("  check export - Check if the controller is currently exporting data to the database")
        print("  session_id - Generate a session_id")
        print("  show buffer - Print the current contents of the data buffer")
    
    def list_modules(self):
        """List all discovered modules"""
        print("Available modules:")
        for module in self.controller.service_manager.modules:
            print(f"  ID: {module.id}, Type: {module.type}, IP: {module.ip}")
        if not self.controller.service_manager.modules:
            print("No modules found")
    
    def handle_zmq_command(self):
        """Handle ZMQ command sending"""
        if not self.controller.service_manager.modules:
            print("No modules available")
            return
            
        print("\nAvailable modules:")
        for i, module in enumerate(self.controller.service_manager.modules, 1):
            print(f"{i}. {module.name}")
        
        try:
            module_idx = int(input("\nChosen module: ").strip()) - 1
            if not 0 <= module_idx < len(self.controller.service_manager.modules):
                print("Invalid module selection")
                return
                
            print("\nAvailable commands:")
            for i, cmd in enumerate(self.controller.commands, 1):
                print(f"{i}. {cmd}")
                
            cmd_idx = int(input("\nChosen command: ").strip()) - 1
            if not 0 <= cmd_idx < len(self.controller.commands):
                print("Invalid command selection")
                return
                
            # Special commands

            # Special handling for update_camera_settings command
            if self.controller.commands[cmd_idx] == "update_camera_settings":
                print("\nEnter camera settings (one per line, format: key=value):")
                print("Available settings:")
                print("  width=<pixels>")
                print("  height=<pixels>")
                print("  fps=<frames_per_second>")
                print("  streaming.enabled=<true/false>")
                print("  streaming.port=<port_number>")
                print("Enter empty line when done")
                
                params = {}
                while True:
                    line = input().strip()
                    if not line:
                        break
                    try:
                        key, value = line.split('=')
                        # Convert value to appropriate type
                        if value.lower() in ('true', 'false'):
                            value = value.lower() == 'true'
                        elif value.isdigit():
                            value = int(value)
                        params[key.strip()] = value
                    except ValueError:
                        print("Invalid format. Use key=value")
                        continue
                
                # Send the command with parameters as a string
                command_str = f"update_camera_settings {str(params)}"
                self.controller.communication_manager.send_command(
                    self.controller.service_manager.modules[module_idx].id,
                    command_str
                )

            # Special handling for record_video command
            elif self.controller.commands[cmd_idx] == "record_video":
                try:
                    duration = int(input("\nEnter recording duration in seconds (0 for continuous): ").strip())
                    # Send the command with duration parameter
                    command_str = f"record_video {duration}"
                    self.controller.communication_manager.send_command(
                        self.controller.service_manager.modules[module_idx].id,
                        command_str
                    )
                except ValueError:
                    print("Invalid duration - please enter a number")
            # Special handling for export_video command
            elif self.controller.commands[cmd_idx] == "export_video":
                try:
                    filename = input("\nEnter the filename for the exported video: ").strip()
                    destination = input("Enter destination (controller/nas) [default: controller]: ").strip().lower()
                    if not destination:
                        destination = "controller"
                    elif destination not in ["controller", "nas"]:
                        print("Invalid destination. Using 'controller'")
                        destination = "controller"
                    
                    command_str = f'export_video {{"filename": "{filename}", "destination": "{destination}"}}'
                    self.controller.communication_manager.send_command(
                        self.controller.service_manager.modules[module_idx].id,
                        command_str
                    )
                except ValueError as e:
                    self.logger.error(f"(INTERFACE MANAGER) Invalid input: {e}")
                except Exception as e:
                    self.logger.error(f"(INTERFACE MANAGER) Error during export: {e}")
            else:
                # Handle other commands as before
                self.controller.communication_manager.send_command(
                    self.controller.service_manager.modules[module_idx].id, 
                    self.controller.commands[cmd_idx]
                )
        except ValueError:
            print("Invalid input - please enter a number")
    
    def show_health_status(self):
        """Display health status of all modules"""
        print("\nModule Health Status:")
        module_health = self.controller.health_monitor.get_module_health()
        if not module_health:
            print("No modules reporting health data")
            return
            
        for module_id, health in module_health.items():
            print(f"\nModule: {module_id}")
            print(f"Status: {health['status']}")
            print(f"CPU Usage: {health.get('cpu_usage', 'N/A')}%")
            print(f"Memory Usage: {health.get('memory_usage', 'N/A')}%")
            print(f"Temperature: {health.get('cpu_temp', 'N/A')}°C")
            print(f"Disk Space: {health.get('disk_space', 'N/A')}%")
            print(f"Uptime: {health.get('uptime', 'N/A')}s")
            print(f"Last Heartbeat: {time.strftime('%H:%M:%S', time.localtime(health['last_heartbeat']))}")
    
    def handle_supabase_export(self):
        """Handle manual supabase export"""
        success = self.controller.data_export_manager.export_module_data(
            self.controller.buffer_manager.get_module_data(), 
            self.controller.service_manager
        )
        if success:
            print("Data exported successfully")
        else:
            print("Failed to export data")
    
    def handle_start_export(self):
        """Start periodic data export"""
        self.controller.data_export_manager.start_periodic_data_export(
            self.controller.buffer_manager, 
            self.controller.service_manager, 
            10  # Default interval
        )
        print("Started periodic data export")
    
    def handle_stop_export(self):
        """Stop periodic data export"""
        self.controller.data_export_manager.stop_periodic_data_export()
        print("Stopped periodic data export")
    
    def handle_start_health_export(self):
        """Start periodic health export"""
        module_health = self.controller.health_monitor.get_module_health()
        self.controller.data_export_manager.start_periodic_health_export(
            module_health, 
            10  # Default interval
        )
        print("Started periodic health export")
    
    def handle_stop_health_export(self):
        """Stop periodic health export"""
        self.controller.data_export_manager.stop_periodic_health_export()
        print("Stopped periodic health export")
    
    def handle_check_export(self):
        """Check export status"""
        status = self.controller.data_export_manager.get_export_status()
        print(f"Data export active: {status['data_exporting']}")
        print(f"Health export active: {status['health_exporting']}")
    
    def handle_session_id(self):
        """Generate a session ID"""
        session_id = self.controller.session_manager.generate_session_id()
        print(f"Generated session ID: {session_id}")

    def handle_show_buffer(self):
        """Display the current contents of the data buffer"""
        buffer = self.controller.buffer_manager.get_module_data()
        if not buffer:
            print("Data buffer is empty.")
            return
        print("Current Data Buffer:")
        for module_id, data_list in buffer.items():
            print(f"Module {module_id}: {len(data_list)} entries")
            for entry in data_list:
                print(f"  {entry}")