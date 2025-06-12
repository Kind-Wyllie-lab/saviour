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
    Manages commands for habitat modules.
    
    This class provides a centralized command handling interface, decoupling
    command processing from the module itself.
    """
    
    def __init__(self, 
                 logger: logging.Logger,
                 module_id: str,
                 module_type: str,
                 communication_manager=None,
                 health_manager=None,
                 config_manager=None,
                 ptp_manager=None,
                 start_time=None):
        """
        Initialize the command handler
        
        Args:
            logger: Logger instance
            module_id: The unique identifier for the module
            module_type: The type of module (camera, microphone, etc.)
            communication_manager: Manager for sending/receiving messages
            health_manager: Manager for health monitoring
            config_manager: Manager for configuration
            start_time: When the module was started
        """
        self.logger = logger
        self.module_id = module_id
        self.module_type = module_type
        self.communication_manager = communication_manager
        self.health_manager = health_manager
        self.config_manager = config_manager
        self.ptp_manager = ptp_manager
        self.start_time = start_time
        
        # Control flags and parameters
        self.streaming = False
        self.stream_thread = None
        self.stream_session_id = None
        self.samplerate = 200
        
        # Callback dictionary - will be set by set_callbacks method
        self.callbacks = {}
        self.get_recording_status_callback = None
        self.get_streaming_status_callback = None

    def register_callbacks(self, get_recording_status, get_streaming_status):
        self.get_recording_status_callback = get_recording_status
        self.get_streaming_status_callback = get_streaming_status
        
    def set_callbacks(self, callbacks: Dict[str, Callable]):
        """
        Set callbacks for data operations that can't be directly handled by the command handler
        
        Args:
            callbacks: Dictionary of callback functions
                - 'read_data': Callback to read data from module
                - 'stream_data': Callback to stream data
                - 'generate_session_id': Callback to generate session ID
        """
        self.callbacks = callbacks
        if 'samplerate' in callbacks:
            self.samplerate = callbacks['samplerate']
    
    def handle_command(self, command: str):
        """
        Process a command received from the controller
        
        Args:
            command: The command string to process
        """
        self.logger.info(f"(COMMAND HANDLER) Handling command: {command}") 
        
        match command:
            case "get_status":
                self.logger.info("(COMMAND HANDLER) Command identified as get_status, calling _handle_get_status")
                self._handle_get_status()
            
            case "get_data":
                self._handle_get_data()

            case "start_stream":
                self._handle_start_stream()
            
            case "stop_stream":
                self._handle_stop_stream()

            case "ptp_status":
                self._handle_ptp_status()

            case _:
                self._handle_unknown_command(command)

    def _handle_get_status(self):
        """Handle get_status command"""
        self.logger.info("(COMMAND HANDLER) _handle_get_status called")
        try:
            # Get PTP status first
            ptp_status = self.ptp_manager.get_status() if self.ptp_manager else {}

            # Get recording and streaming status safely
            recording_status = False
            streaming_status = False
            if self.get_recording_status_callback:
                recording_status = self.get_recording_status_callback()
            if self.get_streaming_status_callback:
                streaming_status = self.get_streaming_status_callback()
            
            # Calculate uptime safely
            current_time = time.time()
            uptime = 0.0
            if self.start_time and isinstance(self.start_time, (int, float)):
                uptime = current_time - float(self.start_time)
            
            status = {
                "type": "status",  # Always include type field
                "timestamp": current_time,
                "cpu_temp": self.health_manager.get_cpu_temp() if self.health_manager else None,
                "cpu_usage": psutil.cpu_percent(),
                "memory_usage": psutil.virtual_memory().percent,
                "uptime": uptime,
                "disk_space": psutil.disk_usage('/').percent,
                "ptp4l_offset": ptp_status.get('ptp4l_offset'),
                "ptp4l_freq": ptp_status.get('ptp4l_freq'),
                "phc2sys_offset": ptp_status.get('phc2sys_offset'),
                "phc2sys_freq": ptp_status.get('phc2sys_freq'),
                "recording": recording_status,
                "streaming": streaming_status,
            }
            self.logger.info(f"(COMMAND HANDLER) Status: {status}")
            self.communication_manager.send_status(status)
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            # Send a minimal status if we can't get all metrics, but always include type
            status = {
                "type": "status",
                "timestamp": time.time(),
                "error": str(e)
            }
            self.communication_manager.send_status(status)
    
    def _handle_get_data(self):
        """Handle get_data command"""
        self.logger.info("(COMMAND HANDLER) Command identified as get_data")
        if 'read_data' in self.callbacks:
            data = str(self.callbacks['read_data']())
            self.communication_manager.send_data(data)
        else:
            self.logger.error("No read_data callback provided")
            self.communication_manager.send_data("Error: Module not configured for data reading")
    
    def _handle_start_stream(self):
        """Handle start_stream command"""
        self.logger.info("(COMMAND HANDLER) Command identified as start_stream")
        if not self.streaming:  # Only start if not already streaming
            if 'stream_data' in self.callbacks and 'generate_session_id' in self.callbacks:
                self.streaming = True
                self.stream_session_id = self.callbacks['generate_session_id'](self.module_id)
                self.logger.debug(f"Stream session ID generated as {self.stream_session_id}")
                self.stream_thread = threading.Thread(target=self._stream_data_thread, daemon=True)
                self.stream_thread.start()
            else:
                self.logger.error("Missing required callbacks for streaming")
                self.communication_manager.send_data("Error: Module not configured for streaming")
    
    def _handle_stop_stream(self):
        """Handle stop_stream command"""
        self.logger.info("(COMMAND HANDLER) Command identified as stop_stream")
        self.streaming = False  # Thread will stop on next loop
        if self.stream_thread: # If there is a thread still
            self.stream_thread.join(timeout=1.0)  # Wait for thread to finish
            self.stream_thread = None # Empty the thread
    
    def _handle_unknown_command(self, command: str):
        """Handle unrecognized command"""
        self.logger.info(f"(COMMAND HANDLER) Command {command} not recognized")
        self.communication_manager.send_data("Command not recognized")
    
    def _stream_data_thread(self):
        """Thread function for streaming data"""
        while self.streaming:
            if 'stream_data' in self.callbacks:
                # Use the dedicated stream_data callback if provided
                self.callbacks['stream_data']()
            elif 'read_data' in self.callbacks:
                # Fall back to using read_data if stream_data isn't available
                data = str(self.callbacks['read_data']())
                self.communication_manager.send_data(data)
            else:
                self.logger.error("No streaming callbacks available")
                self.streaming = False
                break
            
            time.sleep(self.samplerate/1000)

    def _handle_ptp_status(self):
        """Return PTP information to the controller"""
        self.logger.info("(COMMAND HANDLER) Command identified as ptp_status")
        if self.ptp_manager:
            ptp_status = self.ptp_manager.get_status()
            # add type to the status
            ptp_status['type'] = 'ptp_status'
            self.communication_manager.send_status(ptp_status)
        else:
            self.logger.error("No PTP manager available")
            self.communication_manager.send_status({"error": "PTP manager not available"})
    
    def cleanup(self):
        """Clean up resources used by the command handler"""
        if self.streaming:
            self.streaming = False
        
        if self.stream_thread:
            self.logger.info("Waiting for stream thread to stop...")
            self.stream_thread.join(timeout=2.0)
            self.stream_thread = None