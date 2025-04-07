"""
Habitat System - Base Module Class

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 17/03/2025
License: GPLv3
"""

import sys
import os
from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import subprocess
import time
import socket
import logging
import uuid
import threading

import src.shared.ptp as ptp
import src.shared.network as network
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
import zmq

def generate_module_id(module_type: str) -> str:
    mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
    short_id = mac[-4:]  # Takes last 4 characters
    return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"

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
    def __init__(self, module_type: str, config: dict):
        self.module_type = module_type
        self.module_id = generate_module_id(module_type)
        self.config = config
        self.ip = os.popen('hostname -I').read().split()[0]

        # Setup logging
        self.logger = logging.getLogger(f"{self.module_type}.{self.module_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.info(f"Initializing {self.module_type} module {self.module_id}")

        # zeroconf setup
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, "_controller._tcp.local.", self)

        # ZeroMQ setup
        self.context = zmq.Context()
        self.command_socket = self.context.socket(zmq.SUB)
        self.command_socket.subscribe(self.module_id) # Subscribe only to messages for this module.
        
    # zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Called when controller is discovered"""
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self.controller_ip = socket.inet_ntoa(info.addresses[0]) # save the IP of the controller
            self.controller_port = info.port # save the port of the controller
            self.logger.info(f"Found controller at {self.controller_ip}:{self.controller_port}")
            # connect to zeroMQ
            self.connect_to_controller()
            threading.Thread(target=self.listen_for_commands, daemon=True).start()

    def remove_service(self, zeroconf, service_type, name):
        """Called when controller disappears"""
        self.logger.warning("Lost connection to controller")
    
    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")

    # ZeroMQ methods
    def connect_to_controller(self):
        """Connect to controller once we have its IP"""
        self.command_socket.connect(f"tcp://{self.controller_ip}:5555")
        self.logger.info(f"Connected to controller at {self.controller_ip}:5555")
    
    def listen_for_commands(self):
        """Listen for commands from controller"""
        while True:
            try:
                message = self.command_socket.recv_string()
                module_id, command = message.split(' ', 1)
                self.logger.info(f"Received command: {command}")
                self.handle_command(command)
            except Exception as e:
                self.logger.error(f"Error handling command: {e}")

    def handle_command(self, command: str):
        """Handle received commands"""
        self.logger.info(f"Handling command: {command}")
        print(f"Command: {command}")
        # Add command handling logic here

    def start(self) -> bool:
        """
        Start the module.

        This method should be overridden by the subclass to implement specific module initialization logic.
        
        Returns:
            bool: True if the module started successfully, False otherwise.
        """
        self.logger.info(f"Starting {self.module_type} module {self.module_id}")
        
        # Activate ptp
        self.logger.info("Starting ptp4l.service")
        ptp.stop_ptp4l()
        ptp.restart_ptp4l()
        time.sleep(1)
        self.logger.info("Starting phc2sys.service")
        ptp.stop_phc2sys()
        ptp.restart_phc2sys()

        # Advertise this module
        self.service_info = ServiceInfo(
            "_module._tcp.local.",
            f"{self.module_type}_{self.module_id}._module._tcp.local.",
            addresses=[socket.inet_aton(self.ip)],
            port=5000,
            properties={'type': self.module_type, 'id': self.module_id}
        )
        self.zeroconf.register_service(self.service_info)

        return True
    
    
    def status_ptp(self) -> bool:
        """
        Get PTP status.
        """
        ptp.status_ptp4l()
        ptp.status_phc2sys()

    def stop(self) -> bool:
        """
        Stop the module.

        This method should be overridden by the subclass to implement specific module shutdown logic.

        Returns:
            bool: True if the module stopped successfully, False otherwise.
        """
        self.logger.info(f"Stopping {self.module_type} module {self.module_id}")

        # Unregister from zeroconf
        self.zeroconf.unregister_service(self.service_info)
        self.zeroconf.close()

        return True


# Main entry point
def main():
    """Main entry point for the controller application"""
    module = Module(module_type="generic",
                    config={})
    print("Habitat Module initialized")

    # Start the main loop
    module.start()
    
    # module.status_ptp()

    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        module.stop()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
