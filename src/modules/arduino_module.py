#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Arduino Module Class

This class extends the base Module class to handle a module interfacing one or more Arduinos.

Author: Andrew SG
Created: 24/07/2025
License: GPLv3
"""

import datetime
import subprocess
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
from src.modules.module import Module
from src.modules.module_command_handler import ModuleCommandHandler
import logging
import numpy as np
import threading
import json


class ArduinoCommandHandler(ModuleCommandHandler):
    """Command handler specific to Arduino functionality"""
    def __init__(self, logger, module_id, module_type, config_manager=None, start_time=None):
        super().__init__(logger, module_id, module_type, config_manager, start_time)
        self.logger.info("(ARDUINO COMMAND HANDLER) Initialised")

    def handle_command(self, command: str):
        """Handle arduino-specific commands while preserving base functionality"""
        self.logger.info("(ARDUINO COMMAND HANDLER) Checking for arduino specific commands.")
        
        try:
            # Parse command and parameters
            parts = command.split()
            cmd = parts[0]
            params = parts[1:] if len(parts) > 1 else []
            
            # Handle arduino-specific commands
            match cmd:
                case "update_arduino_settings":
                    self._handle_update_arduino_settings(params)
                case _:
                    # If not a arduino-specific command, pass to parent class
                    super().handle_command(command)
                    
        except Exception as e:
            self._handle_error(e)

    def _handle_update_arduino_settings(self, params: list):
        """Handle update_arduino_settings command"""
        self.logger.info("(ARDUINO COMMAND HANDLER) Command identified as update_arduino_settings")
        try:
            if not params:
                raise ValueError("No settings provided for update_arduino_settings")
            
            settings = json.loads(params[0])
            if 'handle_update_arduino_settings' in self.callbacks:
                success = self.callbacks['handle_update_arduino_settings'](settings)
                if success:
                    self.callbacks["send_status"]({
                        "type": "arduino_settings_updated",
                        "settings": settings,
                        "success": True
                    })
                else:
                    self.callbacks["send_status"]({
                        "type": "arduino_settings_update_failed",
                        "error": "Failed to update settings"
                    })
            else:
                self.logger.error("(ARDUINO COMMAND HANDLER) No handle_update_arduino_settings callback provided")
                self.callbacks["send_status"]({
                    "type": "arduino_settings_update_failed",
                    "error": "Module not configured for arduino settings"
                })
        except json.JSONDecodeError:
            self.logger.error("(COMMAND HANDLER) Invalid JSON in update_arduino_settings command")
            self.callbacks["send_status"]({
                "type": "arduino_settings_update_failed",
                "error": "Invalid JSON format"
            })
        except Exception as e:
            self.logger.error(f"(ARDUINO COMMAND HANDLER) Error updating arduino settings: {str(e)}")
            self.callbacks["send_status"]({
                "type": "arduino_settings_update_failed",
                "error": str(e)
            })

class ArduinoModule(Module):
    def __init__(self, module_type="arduino", config=None, config_file_path=None):
        # Initialize command handler before parent class
        self.command_handler = ArduinoCommandHandler(
            logger=logging.getLogger(f"{module_type}.{self.generate_module_id(module_type)}"),
            module_id=self.generate_module_id(module_type),
            module_type=module_type,
            config_manager=None,  # Will be set by parent class
            start_time=None  # Will be set during start()
        )
        
        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)
        
        # Set up callbacks
        self.callbacks = {}
        
        # Set up export manager callbacks
        self.export_manager.set_callbacks({
            'get_controller_ip': lambda: self.service_manager.controller_ip
        })

        # Default arduino config if not in config manager
        if not self.config_manager.get("arduino"):
            self.config_manager.set("arduino", {
                "fps": 100,
                "width": 1280,
                "height": 720,
                "codec": "h264",
                "profile": "high",
                "level": 4.2,
                "intra": 30,
                "file_format": "h264"
            })


        # Set up arduino-specific callbacks for the command handler
        self.command_handler.set_callbacks({
            'generate_session_id': lambda module_id: self.session_manager.generate_session_id(module_id),
            'get_samplerate': lambda: self.config_manager.get("module.samplerate", 200),
            'get_ptp_status': self.ptp_manager.get_status,
            'get_streaming_status': lambda: self.is_streaming,
            'get_recording_status': lambda: self.is_recording,
            'send_status': lambda status: self.communication_manager.send_status(status),
            'get_health': self.health_manager.get_health,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'list_recordings': self.list_recordings,
            'clear_recordings': self.clear_recordings,
            'export_recordings': self.export_recordings,
            'handle_update_arduino_settings': self.handle_update_arduino_settings,  # Camera specific
            'get_latest_recording': self.get_latest_recording,  # Camera specific
            'get_controller_ip': self.service_manager.controller_ip,
            'shutdown': self._shutdown,
        })

        self.logger.info(f"(ARDUINO MODULE) Command handler callbacks: {self.command_handler.callbacks}")

    def start_recording(self, experiment_name: str = None, duration: str = None) -> bool:
        """Start continuous video recording"""
        # Store experiment name for use in timestamps filename
        self.current_experiment_name = experiment_name
        
        # First call parent class to handle common recording setup
        filename = super().start_recording(experiment_name=experiment_name, duration=duration)
        if not filename:
            return False
        
        try:
            # TODO: Start recording here

            # Send status response after successful recording start
            self.communication_manager.send_status({
                "type": "recording_started",
                "filename": filename,
                "recording": True,
                "session_id": self.recording_session_id
            })
            return True
            
        except Exception as e:
            self.logger.error(f"(ARDUINO MODULE) Error starting recording: {e}")
            if hasattr(self, 'communication_manager') and self.communication_manager and self.communication_manager.controller_ip:
                self.communication_manager.send_status({
                    "type": "recording_start_failed",
                    "error": str(e)
                })
            return False

    def stop_recording(self) -> bool:
        """Stop continuous video recording"""
        # First check if recording using parent class
        if not super().stop_recording():
            return False
        
        try:
            # TODO: Stop recording with arduino-specific code
            
            # Stop frame capture thread
            self.is_recording = False
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                # Send status response after successful recording stop
                if hasattr(self, 'communication_manager') and self.communication_manager and self.communication_manager.controller_ip:
                    self.communication_manager.send_status({
                        "type": "recording_stopped",
                        "filename": self.current_filename,
                        "session_id": self.recording_session_id,
                        "duration": duration,
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully"
                    })
                
                return True
            else:
                self.logger.error("(ARDUINO MODULE) Error: recording_start_time was None")
                if hasattr(self, 'communication_manager') and self.communication_manager and self.communication_manager.controller_ip:
                    self.communication_manager.send_status({
                        "type": "recording_stopped",
                        "status": "error",
                        "error": "Recording start time was not set"
                    })
                return False
            
        except Exception as e:
            self.logger.error(f"(ARDUINO MODULE) Error stopping recording: {e}")
            if hasattr(self, 'communication_manager') and self.communication_manager and self.communication_manager.controller_ip:
                self.communication_manager.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": str(e)
                })
            return False
        
    def set_arduino_parameters(self, params: dict) -> bool:
        """
        Set arduino parameters and update config
        
        Args:
            params: Dictionary of arduino parameters to update
            
        Returns:
            bool: True if successful
        """
        try:
            for key, value in params.items():
                config_key = f"arduino.{key}"
                self.config_manager.set(config_key, value)
                
            # Update file format if it's in the params
            if 'file_format' in params:
                self.recording_filetype = params['file_format']
                
            self.logger.info(f"(ARDUINO MODULE) Camera parameters updated: {params}")
            return True
        except Exception as e:
            self.logger.error(f"(ARDUINO MODULE) Error setting arduino parameters: {e}")
            return False
        
    def handle_update_arduino_settings(self, params: dict) -> bool:
        """Handle update_arduino_settings command"""
        try:
            # Update arduino parameters
            success = self.set_arduino_parameters(params)
            
            # Send status update
            self.communication_manager.send_status({
                "type": "arduino_settings_updated",
                "settings": params,
                "success": success
            })
            
            return success
        except Exception as e:
            self.logger.error(f"(ARDUINO MODULE) Error updating arduino settings: {e}")
            self.communication_manager.send_status({
                "type": "arduino_settings_update_failed",
                "error": str(e)
            })
            return False

    def get_latest_recording(self):
        """Get the latest recording"""
        return self.latest_recording
    
    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        super().when_controller_discovered(controller_ip, controller_port)

    def start(self) -> bool:
        """Start the ARDUINO MODULE - including streaming"""
        try:
            # Start the parent module first
            if not super().start():
                return False

            return True

        except Exception as e:
            self.logger.error(f"(ARDUINO MODULE) Error starting module: {e}")
            return False

    def stop(self) -> bool:
        """Stop the module and cleanup"""
        try:
            # Stop streaming if active
            if self.is_streaming:
                self.stop_streaming()
                
            # Call parent stop
            return super().stop()
            
        except Exception as e:
            self.logger.error(f"(ARDUINO MODULE) Error stopping module: {e}")
            return False

def main():
    arduino = ArduinoModule()
    arduino.start()
    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        ttl.stop()

if __name__ == '__main__':
    main()