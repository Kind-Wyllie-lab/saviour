"""
Habitat System - Base Module Class

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 2025-03-17
License: GPLv3
"""

import os
import sys
import time
import socket
import logging

class Module:
    """
    Base class for all modules in the Habitat Controller.

    This class provides common functionality that all hardware modules (camera, microphone, TTL IO, RFID) share.
    It handles network communication with the main controller, PTP synchronization, power management, health monitoring, and basic lifecycle operations.

    Attributes:
        module_id (str): Unique identifier for the module
        module_type (str): Type of module (camera, microphone, ttl_io, rfid)
        config (dict): Configuration parameters for the module

    """
    def __init__(self, module_id: str, module_type: str, config: dict):
        self.module_id = module_id
        self.module_type = module_type
        self.config = config

        # Setup logging
        self.logger = logging.getLogger(f"{module_type}.{module_id}")
        self.logger.setLevel(logging.INFO)

        self.logger.info(f"Initializing {module_type} module {module_id}")

    def start(self) -> bool:
        """
        Start the module.

        This method should be overridden by the subclass to implement specific module initialization logic.
        
        Returns:
            bool: True if the module started successfully, False otherwise.
        """
        self.logger.info(f"Starting {self.module_type} module {self.module_id}")

        return True

    def stop(self) -> bool:
        """
        Stop the module.

        This method should be overridden by the subclass to implement specific module shutdown logic.

        Returns:
            bool: True if the module stopped successfully, False otherwise.
        """
        self.logger.info(f"Stopping {self.module_type} module {self.module_id}")

        return True

