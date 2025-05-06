"""
Testing module side zeroconf functionality

Author: Andrew SG
Created: 05/05/2025
License: GPLv3
"""

import time
import uuid
import logging
import socket
import os

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo

def generate_module_id(module_type: str) -> str:
    mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
    short_id = mac[-4:]  # Takes last 4 characters
    return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"

class Module:
    def __init__(self, module_type: str, config: dict):
        self.module_type = module_type
        self.module_id = generate_module_id(module_type)
        self.config = config
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            self.ip = os.popen('hostname -I').read().split()[0]
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, "_controller._tcp.local.", self)

        # Setup logging
        self.logger = logging.getLogger(f"{self.module_type}.{self.module_id}")
        self.logger.setLevel(logging.WARNING)
        self.logger.info(f"Initializing {self.module_type} module {self.module_id}")


    # zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Called when controller is discovered"""
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self.controller_ip = socket.inet_ntoa(info.addresses[0]) # save the IP of the controller
            self.controller_port = info.port # save the port of the controller
            self.logger.info(f"Found controller at {self.controller_ip}:{self.controller_port}")

    def remove_service(self, zeroconf, service_type, name):
        """Called when controller disappears"""
        self.logger.warning("Lost connection to controller")
    
    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")
        
        # Start and stop module methods
    def start(self) -> bool:
        """
        Start the module.

        This method should be overridden by the subclass to implement specific module initialization logic.
        
        Returns:
            bool: True if the module started successfully, False otherwise.
        """
        self.logger.info(f"Starting {self.module_type} module {self.module_id}")
        self.start_time = time.time()
       

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
        time.sleep(2)
        self.zeroconf.close()
        time.sleep(2)
        
        # stop the heartbeat thread
        self.is_running = False


        return True
        
# Main entry point
def main():
    """Main entry point for the controller application"""
    module = Module(module_type="generic",
                    config={})
    print("Habitat Module initialized")

    # Start the main loop
    module.start()

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
