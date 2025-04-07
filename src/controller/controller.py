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
from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
import datetime
import logging # for logging and debugging
import supabase # for supabase client, the external database

# Networking and synchronization
import socket # for network communication
import threading # for concurrent operations
from zeroconf import ServiceBrowser, Zeroconf

# Local modules
import src.shared.ptp as ptp
import src.shared.network as network

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(SUPABASE_URL, SUPABASE_KEY)

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

        # Parameters
        self.modules = [] # list of discovered modules
        self.manual_control = True # whether to run in manual control mode
        
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
        self.logger.debug("Starting ptp4l.service")
        ptp.stop_ptp4l() # Stop
        ptp.restart_ptp4l() # Restart
        time.sleep(1) # Wait for 1 second
        self.logger.debug("Starting phc2sys.service")
        ptp.stop_phc2sys() # Stop
        ptp.restart_phc2sys() # Restart

        # Start the server
        if self.manual_control:
            self.logger.info("Starting manual control loop")
            while True:
                self.logger.info("Manual control loop running...")
                # Get user input
                user_input = input("Enter a command: ")
                match user_input:
                    case "quit":
                        self.logger.info("Quitting manual control loop")
                        break
                    case "help":
                        print("Available commands:")
                        print("  quit - Quit the manual control loop")
                        print("  help - Show this help message")
                        print("  list - List available modules")
                        print("  get test data - Get test data from supabase")
                        print("  insert test data - Insert test data into supabase")
                    case "list":
                        print("Available modules:")
                        for module in self.modules:
                            print(f"  {module.name}")
                    case "get test data":
                        # Get test data from supabase
                        response = (supabase_client.table("controller_test")
                            .select("*")
                            .execute()
                        )
                        print(response)
                    case "insert test data":
                        # Insert test data into supabase
                        response = (supabase_client.table("controller_test")
                            .insert({
                                "type": "test",
                                "value": "Test entry inserted from test_supabase.py script at " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })
                            .execute()
                        )
                        print(response)
                time.sleep(1)
        else:
            print("Starting automatic loop (not implemented yet)")

        return True
        
# Main entry point
def main():
    """Main entry point for the controller application"""
    controller = HabitatController()

    # Start the main loop
    controller.start()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
