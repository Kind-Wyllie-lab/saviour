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
        print("Available commands:")
        print("  help - Show this help message")
        print("  quit - Quit the manual control loop")
        print("  list - List available modules discovered by zeroconf")
        print("  zmq send - Send a command to a specific module via zeromq")
        print("  health status - Print the health status of all modules")
        print("  health history - Show historical health data (usage: health history --module <id> [--metric <name>] [--limit <n>] [--window <seconds>])")
    
    def list_modules(self):
        """List all discovered modules"""
        # TODO: Implement this

    
    def handle_zmq_command(self):
        """Handle ZMQ command sending"""
        # Check if modules are available
        # TODO: Implement this
    
    def show_health_status(self):
        """Display health status of all modules"""
        # TODO: Implement this
    
    def handle_health_history(self):
        """Handle health history command"""
        # TODO: Implement this

    def _on_module_discovered(self, module):
        """Callback when a new module is discovered"""
        # TODO: Implement this

    def _on_ptp_update(self):
        """Callback when PTP data is updated"""
        # TODO: Implement this

    def _get_modules(self):
        """Callback to get module list"""
        # TODO: Implement this
        
    def _get_ptp_history(self):
        """Callback to get PTP history"""
        # TODO: Implement this