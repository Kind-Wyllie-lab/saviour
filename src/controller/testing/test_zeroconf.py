#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test file for a controller using zeroconf to detect modules which are advertising themselves.

Author: Andrew SG
Created: 2025-05-05
License: GPLv3
"""
# Base imports
from dataclasses import dataclass # to define Module dataclass
from typing import List, Dict, Any # for type hinting
import logging
import time

# Networking and Synchronization
import socket
from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery

# Dataclasses
@dataclass
class Module:
    """Dataclass to represent a module in the habitat system - used by zeroconf to discover modules"""
    id: str
    name: str
    type: str
    ip: str
    port: int
    properties: Dict[str, Any]
    last_seen: float = 0.0  # Timestamp when module was last seen
    status: str = "unknown"  # Module status: "connected", "disconnected", "unknown"

# Habitat Controller class
class HabitatController:
    """A stripped back definition of the habitat controller class"""

    def __init__(self):
        """Initialize the controller with default values"""
        # module management
        self.modules: List[Module] = []

        # setup logging first
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)

        # zeroconf
        self.zeroconf = Zeroconf()
        self.service_info = ServiceInfo(
            "_controller._tcp.local.",  # the service type - tcp protocol, local domain
            "controller._controller._tcp.local.",  # a unique name for the service to advertise itself
            addresses=[socket.inet_aton("192.168.1.1")],  # the ip address of the controller
            port=5000,  # the port number of the controller
            properties={'type': 'controller'}  # the properties of the service
        )
        self.zeroconf.register_service(self.service_info)  # register the controller service with the above info
        self.browser = None  # Initialize browser as None
        self.scan_interval = 5 # the interval to scan for modules
        self.start_browser()  # Start the initial browser

    # Zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Add a service to the list of discovered modules"""
        self.logger.info(f"Discovered module: {name}")
        info = zeroconf.get_service_info(service_type, name)
        if info:
            module_id = str(info.properties.get(b'id', b'unknown').decode())
            # Check if module already exists
            if not any(m.id == module_id for m in self.modules):
                module = Module(
                    id=module_id,
                    name=name,
                    type=info.properties.get(b'type', b'unknown').decode(),
                    ip=socket.inet_ntoa(info.addresses[0]),
                    port=info.port,
                    properties=info.properties,
                    last_seen=time.time(),
                    status="connected"
                )
                self.modules.append(module)
                self.logger.info(f"Added new module: {module}")
            else:
                # Update existing module
                for module in self.modules:
                    if module.id == module_id:
                        module.last_seen = time.time()
                        module.status = "connected"
                        self.logger.info(f"Updated module {module_id} status to connected")
                        break

    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules"""
        self.logger.info(f"Module disconnected: {name}")
        # Find the module and mark it as disconnected
        for module in self.modules:
            if module.name == name:
                module.status = "disconnected"
                module.last_seen = time.time()
                self.logger.info(f"Module {module.id} ({module.type}) disconnected at {time.strftime('%Y-%m-%d %H:%M:%S')}")
                break

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")

    def start_browser(self):
        """Start or restart the service browser"""
        if self.browser:
            self.browser.cancel()  # Cancel existing browser
        self.browser = ServiceBrowser(self.zeroconf, "_module._tcp.local.", self)
        self.logger.info("Service browser started/restarted")

    # Main program
    def start(self):
        """Start the controller."""
        self.logger.info("Starting controller")
        last_scan_time = time.time()
        scan_interval = self.scan_interval

        while True:
            current_time = time.time()
            if current_time - last_scan_time >= scan_interval:
                self.logger.info("Performing periodic service scan...")
                self.start_browser()
                last_scan_time = current_time

            print("\nAvailable modules:")
            for module in self.modules:
                status_str = "✓" if module.status == "connected" else "✗"
                print(f"  {status_str} ID: {module.id}, Type: {module.type}, IP: {module.ip}, Status: {module.status}")
            time.sleep(1)

# Main entry point
def main():
    """Main entry point for the controller application"""
    controller = HabitatController()

    # Start the main loop
    controller.start()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()