#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Command Handler

This manager is responsible for handling and processing commands sent to modules,
providing a central place for command parsing and execution.

Author: Andrew SG
Created: 16/05/2025         
License: GPLv3

#TODO: This is really tightly coupled right now. Better to move it towards looser coupling.
"""

import time
import psutil
import logging
import threading
from typing import Dict, Any, Optional, Callable


class ModuleCommandHandler:
    """
    Manages commands for habitat modules.
    
    This class provides a centralized command handling interface, decoupling command processing from the module itself.
    It routes receives commands to the necessary methods.
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
        
        # Control flags and parameters
        self.streaming = False
        self.stream_thread = None
        self.stream_session_id = None
        self.samplerate = 200
        
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
            
        self.callbacks = callbacks
        if 'get_samplerate' in callbacks:
            self.samplerate = callbacks['get_samplerate']
    
    def handle_command(self, command: str):
        """
        Process a command received from the controller
        
        Args:
            command: The command string to process
        """
        self.logger.info(f"(COMMAND HANDLER) Handling command: {command}") 
        
        try:
            # Parse command and parameters
            parts = command.split()
            cmd = parts[0]
            params = parts[1:] if len(parts) > 1 else []
            
            # Handle command
            match cmd:
                case "get_status":
                    self._handle_get_status()
                case "start_recording":
                    self._handle_start_recording()
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

    def _handle_start_recording(self):
        """Handle start_recording command"""
        self.logger.info("(COMMAND HANDLER) _handle_start_recording called")
        
        if "start_recording" not in self.callbacks:
            raise ValueError("Module not configured for recording")
        
        self.callbacks["start_recording"]()  # Module will handle status response

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
        self.logger.info("(COMMAND HANDLER) _handle_clear_recordings called")
        self.callbacks["clear_recordings"](params)

    def _handle_export_recordings(self, params: list):
        """Handle export_recordings command with parameters"""
        self.logger.info("(COMMAND HANDLER) _handle_export_recordings called")
        
        if "export_recordings" not in self.callbacks:
            raise ValueError("Module not configured for exporting recordings")
        
        # Parse parameters
        filename = params[0] if params else "all"
        length = int(params[1]) if len(params) > 1 else 0
        destination = params[2] if len(params) > 2 else "controller"
        
        result = self.callbacks["export_recordings"](filename, length, destination)
        
        # Send status response
        self.callbacks["send_status"]({
            "type": "export_complete",
            "timestamp": time.time(),
            "filename": filename,
            "success": bool(result)
        })

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
    
    def _handle_unknown_command(self, command: str):
        """Handle unrecognized command"""
        self.logger.info(f"(COMMAND HANDLER) Command {command} not recognized")
        self.callbacks["send_status"]({"type": "error", "error": "Command not recognized"})


    
    def cleanup(self):
        """Clean up resources used by the command handler"""
        if self.streaming:
            self.streaming = False
        
        if self.stream_thread:
            self.logger.info("Waiting for stream thread to stop...")
            self.stream_thread.join(timeout=2.0)
            self.stream_thread = None