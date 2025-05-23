#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Camera Module Class

This class extends the base Module class to handle TTL-specific functionality.

Assumes input pins are normally high and go low when triggered.

Author: Andrew SG
Created: 23/05/2025
License: GPLv3
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
from src.modules.module import Module
from src.modules.module_command_handler import ModuleCommandHandler
import logging
import threading
import gpiozero
import datetime

class TTLCommandHandler(ModuleCommandHandler):
    """Command handler specific to TTL functionality"""

    def handle_command(self, command: str, **kwargs):
        """Handle TTL-specific commands while preserving base functionality"""
        # Handle TTL specific commands
        match command.split()[0]:  # Split and take first word to match command
            case "start_recording":
                if "start_recording" in self.callbacks:
                    result = self.callbacks['start_recording']()
                    self.communication_manager.send_status({"recording_started": result})
                else:
                    self.logger.error("(COMMAND HANDLER) No start_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
            case "stop_recording":  
                if "stop_recording" in self.callbacks:
                    result = self.callbacks['stop_recording']()
                    self.communication_manager.send_status({"recording_stopped": result})
                else:
                    self.logger.error("(COMMAND HANDLER) No stop_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
                return
            case "list_recordings":
                if 'list_recordings' in self.callbacks:
                    try:
                        recordings = self.callbacks['list_recordings']()
                        self.communication_manager.send_status({
                            "type": "recordings_list",
                            "recordings": recordings
                        })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error listing recordings: {e}")
                        self.communication_manager.send_status({
                            "type": "recordings_list_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No list_recordings callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for listing recordings"})
                return

        # If not a camera-specific command, pass to parent class
        super().handle_command(command, **kwargs)

class TTLModule(Module):
    def __init__(self, module_type="ttl", config=None, config_file_path=None):
        # Initialize command handler first
        self.command_handler = TTLCommandHandler(
            logging.getLogger(f"{module_type}"), # is this the same as self.logger? # TODO: check this
            None,  # module_id will be set after super().__init__
            module_type,
            None,  # communication_manager will be set after super().__init__
            None,  # health_manager will be set after super().__init__
            None,  # config_manager will be set after super().__init__
            None,  # ptp_manager will be set after super().__init__
            None   # start_time will be set after super().__init__
        )

        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)

        # Update command handler with proper references
        self.command_handler.module_id = self.module_id
        self.command_handler.communication_manager = self.communication_manager
        self.command_handler.health_manager = self.health_manager
        self.command_handler.config_manager = self.config_manager
        self.command_handler.ptp_manager = self.ptp_manager
        self.command_handler.start_time = self.start_time

        # Set up TTL-specific callbacks
        self.command_handler.set_callbacks({
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
        })

        # TTL specific variables
        self.ttl_input_pins = self.config_manager.get("digital_inputs.pins")
        self.ttl_output_pins = self.config_manager.get("digital_outputs.pins")

        # Initialize GPIO
        self.output_pins = []
        self.input_pins = []

        for pin in self.ttl_input_pins:
            self.input_pins.append(gpiozero.Button(pin, bounce_time=0)) # Use a Button object to represent the input pins, set bounce time to 0 to avoid debouncing
        for pin in self.ttl_output_pins:
            self.output_pins.append(gpiozero.LED(pin))  # Use an LED object to represent the output pins
        
        # Recording variables
        self.recording_folder = self.config_manager.get("recording_folder")
        self.ttl_event_buffer = [] # Buffer to record TTL eventss - List of tuples (timestamp, pin, state)
        self.recording_start_time = None
        self.recording_stop_time = None
        self.recording = False

        self.logger.info(f"Initialized TTL module with {len(self.input_pins)} input pins and {len(self.output_pins)} output pins")

    def handle_command(self, command: str, **kwargs):
        return self.command_handler.handle_command(command, **kwargs)

    def start_recording(self, pins="all"):
        if pins == "all":
            self.logger.info(f"Starting to record all input pins")
            self.recording_stop_time = None
            self.recording_start_time = time.time()
            self.recording = True
            self.start_recording_all_input_pins()
            return True
        else:
            self.logger.info(f"Starting to record on output pin {pins}")
            self.recording_stop_time = None
            self.recording_start_time = time.time()
            self.recording = True
            self.start_recording_on_output_pin(pins)
            return True

    def start_recording_all_input_pins(self):
        self.logger.info(f"Starting to record all input pins")
        for pin in self.input_pins:
            self.start_recording_on_output_pin(pin)
    
    def start_recording_on_output_pin(self, pin):
        pin.when_pressed = self._handle_input_pin_low
        pin.when_released = self._handle_input_pin_high
        self.logger.info(f"Started monitoring output pin {pin.pin}")

    def stop_recording(self):
        self.logger.info(f"Stopping recording")
        self.recording_stop_time = time.time()
        self.recording = False
        self.stop_recording_all_input_pins()
        self._save_ttl_event_buffer_to_file(filename=f"ttl_event_buffer_{self.recording_start_time}.txt")
        return True
    
    def stop_recording_all_input_pins(self):
        self.logger.info(f"Stopping to record all input pins")
        for pin in self.input_pins:
            self.stop_recording_on_output_pin(pin)
    
    def stop_recording_on_output_pin(self, pin):
        pin.when_pressed = None
        pin.when_released = None
        self.logger.info(f"Stopped monitoring output pin {pin.pin}")

    def _handle_input_pin_low(self, pin):
        self.ttl_event_buffer.append((time.time_ns(), pin.pin, "low"))
        self.logger.info(f"(MODULE) Input pin {pin.pin} went low (pressed)")

    def _handle_input_pin_high(self, pin):
        self.ttl_event_buffer.append((time.time_ns(), pin.pin, "high"))
        self.logger.info(f"(MODULE) Input pin {pin.pin} went high (released)") 

    def _set_output_pin_low(self, pin):
        pin.off()

    def _set_output_pin_high(self, pin):
        pin.on()

    def _print_ttl_event_buffer(self):
        self.logger.info(f"(MODULE) Logging TTL event buffer:")
        for event in self.ttl_event_buffer:
            self.logger.info(f"(MODULE) TTL event: {event}")

    def _save_ttl_event_buffer_to_file(self, filename="ttl_event_buffer.txt"):
        with open(filename, "w") as f:
            for event in self.ttl_event_buffer:
                f.write(f"{event}\n")
    
    def list_recordings(self):
        """List all recorded videos with metadata"""
        try:
            recordings = []
            if not os.path.exists(self.recording_folder):
                return recordings
                
            for filename in os.listdir(self.recording_folder):
                if filename.endswith(f".txt"):
                    filepath = os.path.join(self.recording_folder, filename)
                    stat = os.stat(filepath)
                    recordings.append({
                        "filename": filename,
                        # "path": filepath,
                        "size": stat.st_size,
                        "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                        # "modified": stat.st_mtime,
                        # "session_id": filename.split('.')[0]  # Extract session ID from filename
                    })
            
            # Sort by creation time, newest first
            recordings.sort(key=lambda x: x["created"], reverse=True)
            return recordings
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error listing recordings: {e}")
            raise