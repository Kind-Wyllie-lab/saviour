#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Command Handler

This manager is responsible for handling and processing commands sent to modules,
providing a central place for command parsing and execution.

Author: Andrew SG
Created: 16/05/2025         
License: GPLv3
"""

import time
import psutil
import logging
import threading
from typing import Dict, Any, Optional, Callable


class ModuleCommandHandler:
    """
    Routes commands and params recieved by the communication manager to functionality in the main module and managers.

    It's crucial variable 
    """
    
    def __init__(self, 
                 logger: logging.Logger,
                 module_id: str,
                 module_type: str,
                 config_manager=None,
                 start_time=None):
        """
        Initialize the command handler
        
        Args:
            logger: Logger instance
            module_id: The unique identifier for the module
            module_type: The type of module (camera, microphone, etc.)
            config_manager: Manager for configuration
            start_time: When the module was started
        """
        self.logger = logger
        self.module_id = module_id
        self.module_type = module_type
        self.config_manager = config_manager
        self.start_time = start_time
    
        # Callback dictionary - will be set by set_callbacks method
        self.callbacks = {}
        
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
        missing_callbacks = [cb for cb in required_callbacks if cb not in callbacks]
        if missing_callbacks:
            raise ValueError(f"Missing required callbacks: {missing_callbacks}")
            
        self.callbacks.update(callbacks)
        
    def _parse_command(self, command: str):
        """
        Parse a command received from the controller into command and params

        Args:
            command: The command string to process

        Returns:
            cmd: The actual command (e.g. start_recording)
            params: a dict of params e.g. {"port":5000, "client_ip": 192.168.0.34}
        """
        self.logger.info(f"(COMMAND HANDLER) Parsing command {command}")
        try:
            # Check if the command contains a JSON object
            if '{' in command and '}' in command:
                self.logger.info(f"(COMMAND HANDLER) Found JSON in command")
                # Find the first '{' and last '}' to extract the JSON part
                start_idx = command.find('{')
                end_idx = command.rfind('}') + 1
                
                self.logger.info(f"(COMMAND HANDLER) JSON start: {start_idx}, end: {end_idx}")
                
                # Extract the command part (before the JSON)
                cmd_part = command[:start_idx].strip()
                json_part = command[start_idx:end_idx]
                
                self.logger.info(f"(COMMAND HANDLER) Command part: '{cmd_part}'")
                self.logger.info(f"(COMMAND HANDLER) JSON part: '{json_part}'")
                
                # Parse the command part
                cmd_parts = cmd_part.split()
                cmd = cmd_parts[0] if cmd_parts else ""
                
                self.logger.info(f"(COMMAND HANDLER) Extracted command: '{cmd}', JSON param: '{json_part}'")
                
                # Return the command and the JSON as a single parameter
                return cmd, [json_part]
            else:
                # Original parsing for non-JSON commands
                parts = command.split()
                cmd = parts[0]
                params = parts[1:] if len(parts) > 1 else []
                return cmd, params
        except Exception as e:
            self.logger.error(f"Error parsing command {command}: {e}")
            return "", []
    
    def handle_command(self, command: str):
        """
        Process a command received from the controller
        
        Args:
            command: The command string to process
        """
        self.logger.info(f"(COMMAND HANDLER) Handling command: {command}") 
        
        try:
            # Parse command and parameters
            cmd, params = self._parse_command(command)
            
            # Debug logging for command parsing
            self.logger.info(f"(COMMAND HANDLER) Parsed command: '{cmd}', parameters: {params}")
            
            # Handle command
            match cmd:
                case "get_status":
                    self._handle_get_status()
                case "start_recording":
                    self._handle_start_recording(params)
                case "stop_recording":  # Fixed from stop_stream
                    self._handle_stop_recording()
                case "list_recordings":
                    self._handle_list_recordings()
                case "clear_recordings":
                    self._handle_clear_recordings(params)
                case "export_recordings":
                    self._handle_export_recordings(params)
                case "ptp_status":
                    self._handle_ptp_status()
                case "list_commands":
                    self._handle_list_commands()
                case "test_communication":
                    self._handle_test_communication()
                case "get_config":
                    self._handle_get_config()
                case "set_config":
                    self._handle_set_config(params)
                case "shutdown":
                    self._handle_shutdown()
                case _:
                    self._handle_unknown_command(command)
                
        except Exception as e:
            self._handle_error(e)

    def _handle_error(self, error: Exception):
        """Standard error handling"""
        self.logger.error(f"(COMMAND HANDLER) Error handling command: {error}")
        self.callbacks["send_status"]({
            "type": "error",
            "timestamp": time.time(),
            "error": str(error)
        })

    def _handle_get_status(self):
        """Handle get_status command"""
        self.logger.info("(COMMAND HANDLER) _handle_get_status called")
        try:
            # Initialize status with proper structure
            status = {
                "type": "status",
                "timestamp": time.time(),
                "recording_status": None,
                "streaming_status": None
            }

            # Get recording and streaming status safely
            if "get_recording_status" in self.callbacks:
                status["recording_status"] = self.callbacks["get_recording_status"]()
            else:
                self.logger.warning("(COMMAND HANDLER) No get_recording_status in command handler callbacks!")

            if "get_streaming_status" in self.callbacks:
                status["streaming_status"] = self.callbacks["get_streaming_status"]()
            else:
                self.logger.warning("(COMMAND HANDLER) No get_streaming_status in command handler callbacks!")
            
            # Calculate uptime safely
            if self.start_time and isinstance(self.start_time, (int, float)):
                status["uptime"] = time.time() - float(self.start_time)
            
            # Get health metrics from callback
            if "get_health" in self.callbacks:
                health_data = self.callbacks["get_health"]()
                status.update(health_data)
            
            self.logger.info(f"(COMMAND HANDLER) Status: {status}")
            self.callbacks["send_status"](status)
            
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            # Send a minimal status if we can't get all metrics
            status = {
                "type": "status",
                "timestamp": time.time(),
                "error": str(e)
            }
            self.callbacks["send_status"](status)

    def _handle_start_recording(self, params: list = None):
        """Handle start_recording command with parameters"""
        self.logger.info(f"(COMMAND HANDLER) _handle_start_recording called with parameters: {params}")
        
        if "start_recording" not in self.callbacks:
            raise ValueError("Module not configured for recording")
        
        # Parse parameters
        experiment_name = None
        duration = None
        
        if params:
            # Check if we have JSON parameters
            if len(params) == 1 and params[0].startswith('{') and params[0].endswith('}'):
                try:
                    import json
                    json_params = json.loads(params[0])
                    experiment_name = json_params.get('experiment_name')
                    duration = json_params.get('duration')
                    self.logger.info(f"(COMMAND HANDLER) Parsed JSON parameters: {json_params}")
                except json.JSONDecodeError as e:
                    self.logger.error(f"(COMMAND HANDLER) Failed to parse JSON parameters: {e}")
            else:
                # Fallback to old key=value format
                for param in params:
                    if param.startswith('experiment_name='):
                        experiment_name = param.split('=', 1)[1]
                    elif param.startswith('duration='):
                        duration = param.split('=', 1)[1]
        
        self.logger.info(f"(COMMAND HANDLER) Start recording - experiment_name: '{experiment_name}', duration: '{duration}'")
        
        # Call the start_recording method with parsed parameters
        self.callbacks["start_recording"](experiment_name=experiment_name, duration=duration)

    def _handle_stop_recording(self):
        """Handle stop_recordings command"""
        self.logger.info("(COMMAND HANDLER) _handle_stop_recording called")
        
        if "stop_recording" not in self.callbacks:
            raise ValueError("Module not configured for recording")
        
        self.callbacks["stop_recording"]()  # Module will handle status response

    def _handle_list_recordings(self):
        """Handle list_recordings command"""
        self.logger.info("(COMMAND HANDLER) _handle_list_recordings called")
        if "list_recordings" in self.callbacks:
            self.callbacks["list_recordings"]()  # Just call the callback, let module handle status
        else:
            self.logger.error("(COMMAND HANDLER) No list_recordings callback provided")
            self.callbacks["send_status"]({
                "type": "recordings_list_failed",
                "error": "Module not configured for listing recordings"
            })

    def _handle_clear_recordings(self, params: list):
        """Handle clear_recordings command with parameters"""
        self.logger.info(f"(COMMAND HANDLER) _handle_clear_recordings called with parameters: {params}")
        
        # Parse parameters
        filename = None
        filenames = []
        older_than = None
        keep_latest = 0
        
        for param in params:
            if param.startswith('filename='):
                filename_param = param.split('=', 1)[1]
                # Check if multiple filenames are provided (comma-separated)
                if ',' in filename_param:
                    filenames = [f.strip() for f in filename_param.split(',')]
                    self.logger.info(f"(COMMAND HANDLER) Multiple filenames detected: {filenames}")
                else:
                    filename = filename_param
            elif param.startswith('older_than='):
                older_than = int(param.split('=', 1)[1])
            elif param.startswith('keep_latest='):
                keep_latest = int(param.split('=', 1)[1])
        
        self.logger.info(f"(COMMAND HANDLER) Clear recordings - filename: '{filename}', filenames: {filenames}, older_than: {older_than}, keep_latest: {keep_latest}")
        
        # Call the callback with the parsed parameters
        if "clear_recordings" in self.callbacks:
            if filenames:
                # Multiple filenames provided
                result = self.callbacks["clear_recordings"](filenames=filenames)
            else:
                # Single filename or other parameters
                result = self.callbacks["clear_recordings"](filename=filename, older_than=older_than, keep_latest=keep_latest)
        else:
            raise ValueError("Module not configured for clearing recordings")
        
        # Send status response
        if hasattr(self, 'communication_manager') and self.communication_manager and self.communication_manager.controller_ip:
            self.communication_manager.send_status({
                "type": "clear_recordings_complete",
                "result": result
            })

    def _handle_export_recordings(self, params: list):
        """Handle export_recordings command with parameters"""
        self.logger.info(f"(COMMAND HANDLER) _handle_export_recordings called with parameters: {params}")
        
        if "export_recordings" not in self.callbacks:
            raise ValueError("Module not configured for exporting recordings")
        
        # Parse parameters - support both positional and key-value formats
        filename = "all"  # Default to export all
        length = 0
        destination = "controller"  # Default destination
        experiment_name = None
        
        if params:
            # First, check if any parameters use key=value format
            has_key_value = any('=' in param for param in params)
            
            if has_key_value:
                # Parse key-value parameters
                for param in params:
                    if param.startswith('filename='):
                        filename = param.split('=', 1)[1]
                    elif param.startswith('length='):
                        length = int(param.split('=', 1)[1])
                    elif param.startswith('destination='):
                        destination = param.split('=', 1)[1]
                    elif param.startswith('experiment_name='):
                        experiment_name = param.split('=', 1)[1]
            else:
                # Parse positional parameters: filename length destination
                if len(params) >= 1:
                    filename = params[0]
                if len(params) >= 2:
                    try:
                        length = int(params[1])
                    except ValueError:
                        # If second parameter is not a number, it might be destination
                        destination = params[1]
                if len(params) >= 3:
                    # Third parameter is destination (if second was length) or experiment_name
                    if length == 0:  # Second parameter was not a number
                        destination = params[2]
                    else:
                        destination = params[2]
                if len(params) >= 4:
                    # Fourth parameter is experiment_name
                    experiment_name = params[3]
        
        self.logger.info(f"(COMMAND HANDLER) Export parameters - filename: '{filename}', length: {length}, destination: '{destination}', experiment_name: '{experiment_name}'")
        
        # Validate destination
        valid_destinations = ['controller', 'nas']
        if destination not in valid_destinations:
            self.logger.error(f"(COMMAND HANDLER) Invalid destination '{destination}'. Must be one of: {valid_destinations}")
            self.callbacks["send_status"]({
                "type": "export_failed",
                "filename": filename,
                "error": f"Invalid destination: {destination}. Must be one of: {valid_destinations}"
            })
            return
        
        result = self.callbacks["export_recordings"](filename, length, destination, experiment_name)
        
        # The module already sends the export_complete status, so we don't need to send another one
        # Just log the result for debugging
        self.logger.info(f"(COMMAND HANDLER) Export recordings result: {result}")

    def _handle_ptp_status(self):
        """Return PTP information to the controller"""
        self.logger.info("(COMMAND HANDLER) Command identified as ptp_status")
        if "get_ptp_status" in self.callbacks:
            ptp_status = self.callbacks["get_ptp_status"]()
            # add type to the status
            ptp_status['type'] = 'ptp_status'
            self.callbacks["send_status"](ptp_status)
        else:
            self.logger.error("(COMMAND HANDLER) No get_ptp_status callback was given to command handler")
            self.callbacks["send_status"]({"error": "No get_ptp_status callback given to command handler"})
    
    def _handle_list_commands(self):
        """Send the list of module commands to the controller"""

        self.logger.info("(COMMAND HANDLER) Command identified as list_commands")
        if "list_commands" in self.callbacks:
            commands = list(self.callbacks.keys())
            status = {"commands": commands, "type": "list_commands"}
            self.callbacks["send_status"](status)
            # TODO: Consider use of decorators to define commands
        else:
            self.logger.error("(COMMAND HANDLER) No list_commands callback was given to command handler")
            self.callbacks["send_status"]({"error": "No list_commands callback was given to command handler"})

    def _handle_shutdown(self):
        """Shutdown the module"""
        self.logger.info("(COMMAND HANDLER) Command identified as shutdown")
        if "shutdown" in self.callbacks:
            # Respond before shutting down
            self.callbacks["send_status"]({
                "type": "shutdown_initiated",
                "timestamp": time.time(),
                "status": 200
            })
            self.callbacks["shutdown"]()
        else:
            self.logger.error("(COMMAND HANDLER) No shutdown callback given to command handler")
            self.callbacks["send_status"]({"error": "No shutdown callback given to command handler"})

    def _handle_get_config(self):
        """Get the config dict"""
        self.logger.info("(COMMAND HANDLER) Command identified as get_config")
        if "get_config" in self.callbacks:
            config = self.callbacks["get_config"]()
            status = {"type": "get_config", "config": config}
            self.callbacks["send_status"](status)
        else:
            self.logger.error("(COMMAND HANDLER) No get_config callback was given to command handler")
            self.callbacks["send_status"]({"error": "No get_config callback was given to command handler"})
    
    def _handle_test_communication(self):
        """Handle test_communication command"""
        self.logger.info("(COMMAND HANDLER) Command identified as test_communication")
        try:
            # Send a simple response to confirm communication is working
            self.callbacks["send_status"]({
                "type": "test_communication",
                "status": "success",
                "message": "Communication test successful",
                "module_id": self.module_id,
                "timestamp": time.time()
            })
        except Exception as e:
            self.logger.error(f"(COMMAND HANDLER) Error in test_communication: {e}")
            self.callbacks["send_status"]({
                "type": "test_communication",
                "status": "error",
                "error": str(e),
                "module_id": self.module_id,
                "timestamp": time.time()
            })
    
    def _handle_set_config(self, params: list):
        """Update the config dict"""
        self.logger.info(f"(COMMAND HANDLER) Command identified as set_config with params: {params}")
        if "set_config" in self.callbacks:
            try:
                # If params is a list with a single JSON string, parse it directly
                if len(params) == 1 and params[0].startswith('{'):
                    import json
                    try:
                        new_config = json.loads(params[0])
                        self.logger.info(f"(COMMAND HANDLER) Successfully parsed JSON config: {new_config}")
                    except json.JSONDecodeError as e:
                        self.logger.error(f"(COMMAND HANDLER) Failed to parse JSON config: {e}")
                        self.callbacks["send_status"]({
                            "type": "error", 
                            "timestamp": time.time(),
                            "error": f"Failed to parse JSON config: {e}"
                        })
                        return
                else:
                    # Fallback to the original key=value parsing for backward compatibility
                    command_string = ' '.join(params)
                    self.logger.info(f"(COMMAND HANDLER) Reconstructed command string: {command_string}")
                    
                    # Look for the first '=' to find the key
                    if '=' in command_string:
                        first_equal = command_string.find('=')
                        key = command_string[:first_equal].strip()
                        value_string = command_string[first_equal + 1:].strip()
                        
                        # Try to evaluate the value as a Python literal (safe for dicts, lists, etc.)
                        import ast
                        try:
                            value = ast.literal_eval(value_string)
                            new_config = {key: value}
                            self.logger.info(f"(COMMAND HANDLER) Successfully parsed config: {new_config}")
                        except (ValueError, SyntaxError) as e:
                            self.logger.error(f"(COMMAND HANDLER) Failed to parse value '{value_string}': {e}")
                            # Fallback to simple key=value parsing
                            new_config = {}
                            for param in params:
                                if '=' in param:
                                    key, value = param.split('=', 1)
                                    # Try to convert value to appropriate type
                                    try:
                                        if value.lower() in ['true', 'false']:
                                            new_config[key] = value.lower() == 'true'
                                        elif '.' in value and value.replace('.', '').replace('-', '').isdigit():
                                            new_config[key] = float(value)
                                        elif value.replace('-', '').isdigit():
                                            new_config[key] = int(value)
                                        else:
                                            new_config[key] = value
                                    except ValueError:
                                        new_config[key] = value
                    else:
                        # No '=' found, treat as simple parameters
                        new_config = {}
                        for param in params:
                            if '=' in param:
                                key, value = param.split('=', 1)
                                # Try to convert value to appropriate type
                                try:
                                    if value.lower() in ['true', 'false']:
                                        new_config[key] = value.lower() == 'true'
                                    elif '.' in value and value.replace('.', '').replace('-', '').isdigit():
                                        new_config[key] = float(value)
                                    elif value.replace('-', '').isdigit():
                                        new_config[key] = int(value)
                                    else:
                                        new_config[key] = value
                                except ValueError:
                                    new_config[key] = value
                
                self.logger.info(f"(COMMAND HANDLER) Final parsed config: {new_config}")
                success = self.callbacks["set_config"](new_config)
                if success:
                    status = {"type": "set_config", "status": "success", "message": "Configuration updated successfully"}
                else:
                    status = {"type": "set_config", "status": "error", "message": "Failed to update configuration"}
                self.callbacks["send_status"](status)
                
            except Exception as e:
                self.logger.error(f"(COMMAND HANDLER) Error parsing set_config parameters: {e}")
                self.callbacks["send_status"]({
                    "type": "error", 
                    "timestamp": time.time(),
                    "error": f"Failed to parse config parameters: {e}"
                })
        else:
            self.logger.error("(COMMAND HANDLER) No set_config callback was given to command handler")
            self.callbacks["send_status"]({"error": "No set_config callback was given to command handler"})

    def _handle_unknown_command(self, command: str):
        """Handle unrecognized command"""
        self.logger.info(f"(COMMAND HANDLER) Command {command} not recognized")
        self.callbacks["send_status"]({"type": "error", "error": "Command not recognized"})
    
    def cleanup(self):
        """Clean up resources used by the command handler"""
        pass # I don't think anything needs cleaned up?