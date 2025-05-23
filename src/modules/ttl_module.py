#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Camera Module Class

This class extends the base Module class to handle TTL-specific functionality.

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
            "ttl2": {"pin": 20, "mode": "in"},
            "ttl3": {"pin": 21, "mode": "in"},
        }


        # Initialize GPIO
        self.chip = gpiod.Chip("gpiochip4")


    def handle_command(self, command: str, **kwargs):
        return self.command_handler.handle_command(command, **kwargs)

    def start(self):
        pass