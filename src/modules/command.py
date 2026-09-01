#!/usr/bin/env python3
"""
Module Command Handler

This class is responsible for handling and processing commands sent to modules,
providing a central place for command parsing and execution.

Author: Andrew SG
Created: 16/05/2025         
"""

import json
import logging
import time
from collections.abc import Callable

from src.modules.config import Config


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

        self.commands = {}


    def set_commands(self, commands: dict[str, Callable]):
        """
        Set callbacks for commands that can be executed by the module

        Args:
            commands: Dictionary of commands
        """
        self.commands.update(commands)
        self.logger.info(
            f"Registered {len(commands)} command handler(s); "
            f"{len(self.commands)} total"
        )
        self.logger.debug(f"Command handlers: {sorted(self.commands)}")

    # Alias used by some module implementations
    set_callbacks = set_commands


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
                # Find the first '{' and last '}' to extract the JSON part
                start_idx = command.find('{')
                end_idx = command.rfind('}') + 1

                # Extract the command part (before the JSON)
                cmd_part = command[:start_idx].strip()
                json_part = command[start_idx:end_idx]

                # Parse the command part
                cmd_parts = cmd_part.split()
                cmd = cmd_parts[0] if cmd_parts else ""

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

            # 2. Find corresponding callback
            handler = self.commands.get(cmd) # Find the callback that matches the name of the commmand
            if not handler:
                return self._unknown_command(cmd)

            # 3. Execute callback and get response
            if not params:
                result = handler()
            else:
                result = handler(**params) # Unpack params into arguments

            if result == True:
                result = {"result": "success"}
            elif result == False:
                result = {"result": "error"}
            elif result == None:
                self.logger.warning(f"Make sure {cmd} returns a dict")
                result = {"result": f"error: NoneType result from {cmd} callback"}

            # 4. Send unified command acknowledgement to controller
            response = {"type": "cmd_ack", "command": cmd}
            response.update(result)
            self.facade.send_status(response)

        except Exception as e:
            self._handle_error(e)


    def _handle_error(self, error: Exception):
        """Standard error handling"""
        self.logger.exception(f"Error handling command: {error}")
        self.facade.send_status({
            "type": "error",
            "timestamp": time.time(),
            "error": str(error)
        })


    def _unknown_command(self, command: str):
        """Handle unrecognized command"""
        self.logger.warning(f"Command {command!r} not recognized")
        self.facade.send_status({
            "type": "error",
            "error": f"Command {command} not recognized"
        })


    def cleanup(self):
        """Clean up resources used by the command handler"""
        pass # I don't think anything needs cleaned up?
