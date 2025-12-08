#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Command Handler

This class is responsible for handling and processing commands sent to modules,
providing a central place for command parsing and execution.

Author: Andrew SG
Created: 16/05/2025         
"""

import time
import logging
import threading
import json
from typing import Dict, Any, Optional, Callable
from config import Config

class Command:
    """
    Routes commands and params recieved by the communication manager to functionality in the main module and managers.
    """
    
    def __init__(self, config: Config=None):
        """
        Initialize the command router
        
        Args:
            config: Manager for configuration
        """
        self.logger = logging.getLogger(__name__)
    
        self.callbacks = {} # Dict of callbacks to enable routing commands
        self.commands = {}
        
    def set_callbacks(self, callbacks: Dict[str, Callable]):
        """
        Set callbacks for data operations that can't be directly handled by the command handler
        
        Args:
            callbacks: Dictionary of callback functions
                - 'read_data': Callback to read data from module
                - 'stream_data': Callback to stream data
                - 'generate_session_id': Callback to generate session ID
        """
        # Validate required callbacks
        required_callbacks = ['send_status']
        missing_callbacks = [cb for cb in required_callbacks if cb not in callbacks and cb not in self.callbacks]
        if missing_callbacks:
            raise ValueError(f"Missing required callbacks: {missing_callbacks}")
            
        self.callbacks.update(callbacks)
        self.logger.info(f"Command handler callbacks: {self.callbacks}")

    def set_commands(self, commands: Dict[str, Callable]):
        """
        Set callbacks for commands that can be executed by the module

        Args:
            commands: Dictionary of commands
        """
        self.commands.update(commands)
        self.logger.info(f"Command handler callbacks: {self.commands}")
        
    def _parse_command(self, command: str):
        # TODO: Migrate to zmq send and recv json
        """
        Parse a command received from the controller into command and params

        Args:
            command: The command string to process

        Returns:
            cmd: The actual command (e.g. start_recording)
            params: a dict of params e.g. {"port":5000, "client_ip": 192.168.0.34}
        """
        self.logger.debug(f"Parsing command {command}")
        try:
            # Check if the command contains a JSON object
            if '{' in command and '}' in command:
                # self.logger.info(f"Found JSON in command")
                # Find the first '{' and last '}' to extract the JSON part
                start_idx = command.find('{')
                end_idx = command.rfind('}') + 1
                
                # self.logger.info(f"JSON start: {start_idx}, end: {end_idx}")
                
                # Extract the command part (before the JSON)
                cmd_part = command[:start_idx].strip()
                json_part = command[start_idx:end_idx]
                
                # self.logger.info(f"Command part: '{cmd_part}'")
                # self.logger.info(f"JSON part: '{json_part}'")
                
                # Parse the command part
                cmd_parts = cmd_part.split()
                cmd = cmd_parts[0] if cmd_parts else ""
                
                # self.logger.info(f"Extracted command: '{cmd}', JSON param: '{json_part}'")
                params = json.loads(json_part)
                
                # Return the command and the JSON as a single parameter
                return cmd, params
            else:
                # Original parsing for non-JSON commands
                parts = command.split()
                cmd = parts[0]
                params = {}
                for p in parts[1:]:
                    if '=' in p:
                        k, v = p.split('=', 1)
                        params[k] = v
                return cmd, params
        except Exception as e:
            self.logger.error(f"Error parsing command {command}: {e}")
            return "", {}
    
    def handle_command(self, raw_command: str):
        """
        Process a command received from the controller
        
        Args:
            command: The command string to process
        """
        self.logger.debug(f"Handling command: {raw_command}") 
        
        try:
            # 1. Parse command and parameters
            cmd, params = self._parse_command(raw_command)
            
            # Debug logging for command parsing
            # self.logger.info(f"Parsed command: '{cmd}', parameters: {params}")

            # 2. Find corresponding callback
            handler = self.commands.get(cmd) # Find the callback that matches the name of the commmand
            if not handler:
                return self._unknown_command(cmd)    
            
            # 3. Execute callback and get response
            # self.logger.info(f"Executing command {cmd}")
            if not params:
                # self.logger.info(f"Executing without arguments")
                result = handler()
            else:
                result = handler(**params) # Unpack params into arguments 

            # self.logger.info(f"Command handler returned {result}")
            if result == None:
                self.logger.warning(f"Make sure {cmd} returns a dict")
                result = {"message": f"NoneType result from {cmd} callback"}

            # 4. Send response to controller
            response = {"type": cmd}
            response.update(result)
            self.callbacks["send_status"](response)

        except Exception as e:
            self._handle_error(e)

    def _handle_error(self, error: Exception):
        """Standard error handling"""
        self.logger.error(f"Error handling command: {error}")
        self.callbacks["send_status"]({
            "type": "error",
            "timestamp": time.time(),
            "error": str(error)
        })

    def _unknown_command(self, command: str):
        """Handle unrecognized command"""
        self.logger.info(f"Command {command} not recognized")
        self.callbacks["send_status"]({
            "type": "error", 
            "error": f"Command {command} not recognized"
        })
    
    def cleanup(self):
        """Clean up resources used by the command handler"""
        pass # I don't think anything needs cleaned up?