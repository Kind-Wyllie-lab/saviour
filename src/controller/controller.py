#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat Controller - Modular Synchronised Data Capture

This script serves as the main controller for the habitat system, providing:
- Precise time synchronisation (PTP master) for all connected modules
- Module discovery, monitoring, and health checks
- Recording session management and control
- Data collection and packaging in NWB format
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import src.shared.ptp as ptp
import src.shared.network as network
import time
import datetime
import logging # for logging and debugging

# Networking and synchronization
import socket # for network communication
import threading # for concurrent operations

# Optional: For NWB format support
try:
    import pynwb
    from pynwb import NWBFile, NWBHDF5IO
    NWB_AVAILABLE = True
except ImportError:
    NWB_AVAILABLE = False
    logging.warning("PyNWB not available. NWB file export will be disabled.")
    
# Habitat Controller Class
class HabitatController:
    """Main controller class for the habitat system"""
    
    def __init__(self):
        """Initialize the controller with default values"""
        self.modules = [] # list of discovered modules
        
        # Setup logging
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)

    def start(self) -> bool:
        """
        Start the controller.
        
        Returns:
            bool: True if the controller started successfully, False otherwise.
        """
        self.logger.info("Starting controller")

        # Activate ptp
        self.logger.info("Starting ptp4l.service")
        ptp.stop_ptp4l()
        ptp.restart_ptp4l()
        time.sleep(1)
        self.logger.info("Starting phc2sys.service")
        ptp.stop_phc2sys()
        ptp.restart_phc2sys()

        # Start the server
        network.run_server()

        return True
        
# Main entry point
def main():
    """Main entry point for the controller application"""
    controller = HabitatController()
    print("Habitat Controller initialized")

    # Start the main loop
    controller.start()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
