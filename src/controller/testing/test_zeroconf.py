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

# Habitat Controller class
class HabitatController:
    """A stripped back definition of the habitat controller class"""

    def __init__(self):
        """Initialize the controller with default values"""
        # module management
        self.modules: List[Module] = []

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
        self.browser = ServiceBrowser(self.zeroconf, "_module._tcp.local.", self)  # Browse for habitat_module services


        # setup logging
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)

    # Zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Add a service to the list of discovered modules"""
        self.logger.info(f"Discovered module: {name}")
        info = zeroconf.get_service_info(service_type, name)
        if info:
            module = Module(
                id=str(info.properties.get(b'id', b'unknown').decode()),
                name=name,
                type=info.properties.get(b'type', b'unknown').decode(),
                ip=socket.inet_ntoa(info.addresses[0]),
                port=info.port,
                properties=info.properties
            )
            self.modules.append(module)
            self.logger.info(f"Discovered module: {module}")

    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules"""
        self.logger.info(f"Removing module: {name}")
        self.modules = [module for module in self.modules if module.name != name] # remove the module from the list

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")

    # Main program
    def start(self):
        """Start the controller."""
        self.logger.info("Starting controller")
        while True:
            print("Available modules:")
            for module in self.modules:
                print(f"  ID: {module.id}, Type: {module.type}, IP: {module.ip}")
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