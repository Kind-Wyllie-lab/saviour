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
        self.logger.info(f"Handling command: {command}")
        print(f"Command: {command}")
        
        match command:
            case "get_status":
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
        print("Command identified as get_status")
        try:
            status = {
                "timestamp": time.time(),
                "cpu_temp": self.health_manager.get_cpu_temp(),
                "cpu_usage": psutil.cpu_percent(),
                "memory_usage": psutil.virtual_memory().percent,
                "uptime": time.time() - self.start_time if self.start_time else 0,
                "disk_space": psutil.disk_usage('/').percent,
                "ptp_offset": self.ptp_manager.last_offset,
                "ptp_freq": self.ptp_manager.last_freq
            }
            self.communication_manager.send_status(status)
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            # Send a minimal status if we can't get all metrics
            status = {"timestamp": time.time(), "error": str(e)}
            self.communication_manager.send_status(status)
    
    def _handle_get_data(self):
        """Handle get_data command"""
        print("Command identified as get_data")
        if 'read_data' in self.callbacks:
            data = str(self.callbacks['read_data']())
            self.communication_manager.send_data(data)
        else:
            self.logger.error("No read_data callback provided")
            self.communication_manager.send_data("Error: Module not configured for data reading")
    
    def _handle_start_stream(self):
        """Handle start_stream command"""
        print("Command identified as start_stream")
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
        print("Command identified as stop_stream")
        self.streaming = False  # Thread will stop on next loop
        if self.stream_thread: # If there is a thread still
            self.stream_thread.join(timeout=1.0)  # Wait for thread to finish
            self.stream_thread = None # Empty the thread
    
    def _handle_unknown_command(self, command: str):
        """Handle unrecognized command"""
        print(f"Command {command} not recognized")
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
        print("Command identified as ptp_status")
        if 'ptp_status' in self.callbacks:
            ptp_status = str(self.callbacks['ptp_status']())
            print(ptp_status)
            # self.communication_manager.send_status(ptp_status)
        else:
            self.logger.error("No read_data callback provided")
            self.communication_manager.send_data("Error: Module not configured for data reading")
    
    def cleanup(self):
        """Clean up resources used by the command handler"""
        if self.streaming:
            self.streaming = False
        
        if self.stream_thread:
            self.logger.info("Waiting for stream thread to stop...")
            self.stream_thread.join(timeout=2.0)
            self.stream_thread = None