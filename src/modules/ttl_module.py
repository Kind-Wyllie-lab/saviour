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

class TTLCommandHandler(ModuleCommandHandler):
    """Command handler specific to TTL functionality"""

    def handle_command(self, command: str, **kwargs):
        pass

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

        # TTL specific variables
        # TODO: get these from config
        self.ttl_pins = {
            "ttl1": {"pin": 16, "mode": "in"},
            "ttl2": {"pin": 20, "mode": "out"},
            "ttl3": {"pin": 21, "mode": "out"},
        }

        # Initialize GPIO
        self.output_pins = []
        self.input_pins = []

        for pin in self.ttl_pins.values():
            if pin["mode"] == "in":
                self.input_pins.append(gpiozero.Button(pin["pin"], bounce_time=0)) # Use a Button object to represent the input pins, set bounce time to 0 to avoid debouncing
            elif pin["mode"] == "out":
                self.output_pins.append(gpiozero.LED(pin["pin"]))  # Use an LED object to represent the output pins
        
        # Buffer to timestamp TTL events
        self.ttl_event_buffer = [] # List of tuples (timestamp, pin)

        self.logger.info(f"Initialized TTL module with {len(self.input_pins)} input pins and {len(self.output_pins)} output pins")

    def handle_command(self, command: str, **kwargs):
        return self.command_handler.handle_command(command, **kwargs)

    def start_recording_all_input_pins(self):
        self.logger.info(f"Starting to record all input pins")
        for pin in self.input_pins:
            self.start_recording_on_output_pin(pin)
            self.logger.info(f"Started monitoring input pin {pin.pin}")
    
    def start_recording_on_output_pin(self, pin):
        self.logger.info(f"Starting to record on output pin {pin.pin}")
        pin.when_pressed = self._handle_input_pin_low
        pin.when_released = self._handle_input_pin_high
        self.logger.info(f"Started monitoring output pin {pin.pin}")

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

    def _print_ttl_event_buffer_to_file(self):
        with open("ttl_event_buffer.txt", "w") as f:
            for event in self.ttl_event_buffer:
                f.write(f"{event}\n")