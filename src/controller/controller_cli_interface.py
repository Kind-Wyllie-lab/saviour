#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller CLI Interface

# TODO: This is a work in progress. It is not yet functional.
# TODO: Use callbacks to get module list and PTP history.
"""

import logging
import time
import threading

class CLIInterface:
    def __init__(self, logger: logging.Logger):
        """Initialize the controller interface"""
        self.logger = logger
        self.cli_thread = None

        # Callbacks
        self.get_modules_callback = None
        self.get_ptp_history_callback = None
        self.get_zmq_commands_callback = None
        self.send_command_callback = None
        self.get_module_health_callback = None

    def register_callbacks(self, get_modules=None, get_ptp_history=None, get_zmq_commands=None, send_command=None, get_module_health=None):
        """Register callbacks for getting data from the command handler"""
        self.get_modules_callback = get_modules
        self.get_ptp_history_callback = get_ptp_history
        self.get_zmq_commands_callback = get_zmq_commands
        self.send_command_callback = send_command
        self.get_module_health_callback = get_module_health

    def start(self):
        """Start the CLI interface"""
        self.logger.info(f"(CLI INTERFACE) Starting CLI interface")
        self.cli_thread = threading.Thread(target=self.run_cli_interface, daemon=True)
        self.cli_thread.start()

    def run_cli_interface(self):
        """Run the manual control loop"""
        self.logger.info("(CLI INTERFACE) Starting manual CLI control loop")
        while True: # This needs to be in a while loop to continously print the prompt and await user input
            # Get user input
            print("\nEnter a command (type help for list of commands): ", end='', flush=True)
            try:
                user_input = input().strip()
                if not user_input:
                    continue
                        
                self.handle_command(user_input)
                    
            except Exception as e:
                self.logger.error(f"(CLI INTERFACE) Error handling input: {e}")
    
    def handle_command(self, command):
        """Handle a single command"""
        match command:
            case "help":
                self.show_help()
            case "quit":
                self.logger.info("(CLI INTERFACE) Quitting manual control loop")
                self.stop()
                return False  # Signal to exit
            case "list":
                self.list_modules()
            case "zmq send":
                self.handle_zmq_command()
            case "health status":
                self.show_health_status()
            case "health history":
                self.handle_health_history()
            case _:
                self.logger.error(f"(CLI INTERFACE) Unknown command: {command}. Type 'help' for available commands.")
    
    def show_help(self):
        """Display available commands"""
        print("\nAvailable commands:")
        print("  help - Show this help message")
        print("  quit - Quit the manual control loop")
        print("  list - List available modules discovered by zeroconf")
        print("  zmq send - Send a command to a specific module via zeromq")
        print("  health status - Print the health status of all modules")
        print("  health history - Show historical health data (usage: health history --module <id> [--metric <name>] [--limit <n>] [--window <seconds>])")
    
    def list_modules(self):
        """List all discovered modules"""
        self.logger.info(f"(CLI INTERFACE) Listing modules")
        modules = self.get_modules_callback()
        if not modules:
            print("No modules found")
            return
        print("\nDiscovered modules:")
        print("------------------")
        for module in modules:
            print(f"Module: {module['id']}")
            print(f"Type: {module['type']}")
            print(f"IP: {module['ip']}")
            print(f"Port: {module['port']}")
            print("------------------")
    
    def handle_zmq_command(self):
        """Handle ZMQ command sending"""
        # Get available modules
        modules = self.get_modules_callback()
        if not modules:
            print("No modules available")
            return
            
        # Display available modules
        print("\nAvailable modules:")
        print("0. All modules")
        for i, module in enumerate(modules, 1):
            print(f"{i}. {module['id']}")
        
        try:
            # Get module selection
            user_input = input("\nChosen module: ").strip()
            if user_input == "0":
                module_id = "all"
            else:
                module_idx = int(user_input) - 1
                if not 0 <= module_idx < len(modules):
                    print("Invalid module selection")
                    return
                module_id = modules[module_idx]['id']
            
            # Get available ZMQ commands
            if not self.get_zmq_commands_callback:
                print("ZMQ commands not available")
                return
                
            zmq_commands = self.get_zmq_commands_callback()
            if not zmq_commands:
                print("No ZMQ commands available")
                return
                
            # Display available commands
            print("\nAvailable commands:")
            for i, cmd in enumerate(zmq_commands, 1):
                print(f"{i}. {cmd}")
            
            cmd_idx = int(input("\nChosen command: ").strip()) - 1
            if not 0 <= cmd_idx < len(zmq_commands):
                print("Invalid command selection")
                return
                
            # Special commands
            if zmq_commands[cmd_idx] == "update_settings":
                # Check module type
                match modules[module_idx]['type']:
                    case "camera":
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
                        command_str = f"update_settings {str(params)}"
                        if self.send_command_callback:
                            self.send_command_callback(module_id, command_str)
                        else:
                            print("Command sending not available")
                    case "generic":
                        print(f"No update_settings logic has been implemented for module type '{modules[module_idx]['type']}'")
                    case "ttl":
                        print(f"No update_settings logic has been implemented for module type '{modules[module_idx]['type']}'")
                    case "mic":
                        print(f"No update_settings logic has been implemented for module type '{modules[module_idx]['type']}'")
                    case _:
                        print(f"Module type '{modules[module_idx]['type']}' not found in update_settings command")

            # Special handling for export_records command
            elif zmq_commands[cmd_idx] == "export_recordings":
                print("Export recordings not yet implemented")
                # TODO: Use callbacks to get interface manager to do this.
            else:
                # Handle other commands as before
                if self.send_command_callback:
                    self.send_command_callback(module_id, zmq_commands[cmd_idx])
                else:
                    print("Command sending not available")
        except ValueError:
            print("Invalid input - please enter a number")
        except Exception as e:
            self.logger.error(f"(CLI INTERFACE) Error in ZMQ command handling: {e}")
            print(f"Error: {e}")
    
    def show_health_status(self):
        """Display health status of all modules"""
        self.logger.info(f"(CLI INTERFACE) Showing health status of all modules")
        print("\nModule Health Status:")
        module_health = self.get_module_health_callback()
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
    
    def handle_health_history(self):
        """Handle health history command"""
        self.logger.warning(f"(CLI INTERFACE) NOT YET IMPLEMENTED.")
        # TODO: Implement this

    def _on_module_discovered(self, module):
        """Callback when a new module is discovered"""
        self.logger.info(f"(CLI INTERFACE) New module discovered:")
        self.logger.info(f"(CLI INTERFACE) ID: {module['id']}")
        self.logger.info(f"(CLI INTERFACE) Type: {module['type']}")
        self.logger.info(f"(CLI INTERFACE) IP: {module['ip']}")
        self.logger.info(f"(CLI INTERFACE) Port: {module['port']}")
        self.logger.info(f"(CLI INTERFACE) Properties: {module['properties']}")

    def _on_ptp_update(self):
        """Callback when PTP data is updated"""
        self.logger.warning(f"(CLI INTERFACE) NOT YET IMPLEMENTED.")
        # TODO: Implement this

    def _get_modules(self):
        """Callback to get module list"""
        self.logger.warning(f"(CLI INTERFACE) NOT YET IMPLEMENTED.")
        # TODO: Implement this
        
    def _get_ptp_history(self):
        """Callback to get PTP history"""
        self.logger.warning(f"(CLI INTERFACE) NOT YET IMPLEMENTED.")
        # TODO: Implement this

    def stop(self):
        """Stop the CLI interface"""
        self.logger.info(f"(CLI INTERFACE) Stopping CLI interface")
        if self.cli_thread:
            self.cli_thread.join()
        self.cli_thread = None