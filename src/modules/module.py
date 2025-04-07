"""
Habitat System - Base Module Class

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 17/03/2025
License: GPLv3
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import src.shared.ptp as ptp
import src.shared.network as network
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo

import subprocess
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

        # zeroconf setup
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, "_controller._tcp.local.", self)
         
    def add_service(self, zeroconf, service_type, name):
        """Called when controller is discovered"""
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self.controller_ip = socket.inet_ntoa(info.addresses[0])
            self.controller_port = info.port
            self.logger.info(f"Found controller at {self.controller_ip}:{self.controller_port}")

    def remove_service(self, zeroconf, service_type, name):
        """Called when controller disappears"""
        self.logger.warning("Lost connection to controller")

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
        service_info = ServiceInfo(
            "_module._tcp.local.",
            f"{self.module_type}_{self.module_id}._module._tcp.local.",
            addresses=[socket.inet_aton("192.168.1.2")],
            port=5000,
            properties={'type': self.module_type, 'id': self.module_id}
        )
        self.zeroconf.register_service(service_info)

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

        return True


# Main entry point
def main():
    """Main entry point for the controller application"""
    module = Module(module_id=1,
                    module_type="Generic",
                    config={})
    print("Habitat Controller initialized")

    # Start the main loop
    module.start()
    
    module.status_ptp()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
