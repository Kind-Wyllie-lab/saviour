#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Service Manager

The service manager is responsible for discovering, registering and unregistering services (modules) with the controller.

"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging
import threading

@dataclass
class Module:
    """Dataclass to represent a module in the habitat system - used by zeroconf to discover modules"""
    id: str
    name: str
    type: str
    ip: str
    port: int
    properties: Dict[str, Any]

class ControllerServiceManager():
    def __init__(self, logger: logging.Logger, config_manager=None):
        self.logger = logger
        self.config_manager = config_manager

        # Module tracking
        self.modules = []
        self.module_health = {}

        # Get the ip address of the controller
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            self.ip = os.popen('hostname -I').read().split()[0]

        # Get service configuration from config manager if available
        service_port = 5000  # Default value
        service_type = "_controller._tcp.local."
        service_name = "controller._controller._tcp.local."
        
        if self.config_manager:
            service_port = self.config_manager.get("service.port", service_port)
            service_type = self.config_manager.get("service.service_type", service_type)
            service_name = self.config_manager.get("service.service_name", service_name)

        # Initialize zeroconf
        self.zeroconf = Zeroconf()
        self.service_info = ServiceInfo(
            service_type, # the service type - tcp protocol, local domain
            service_name, # a unique name for the service to advertise itself
            addresses=[socket.inet_aton(self.ip)], # the ip address of the controller
            port=service_port, # the port number of the controller
            properties={'type': 'controller'} # the properties of the service
        )
        self.zeroconf.register_service(self.service_info) # register the service with the above info
        self.browser = ServiceBrowser(self.zeroconf, "_module._tcp.local.", self) # Browse for habitat_module services"

    def cleanup(self):
        """Cleanup zeroconf resources"""
        if hasattr(self, 'zeroconf'):
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.browser.cancel()
                self.zeroconf.close()
            except:
                pass # Ignore errors during cleanup
    
    # zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Add a service to the list of discovered modules"""
        self.logger.info(f"(SERVICE MANAGER) Discovered module: {name}")
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
            self.logger.info(f"(SERVICE MANAGER) Discovered module: {module}")

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"(SERVICE MANAGER) Service updated: {name}")

    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules"""
        self.logger.info(f"(SERVICE MANAGER) Removing module: {name}")
        # Find the module being removed
        module_to_remove = next((module for module in self.modules if module.name == name), None)
        if module_to_remove:
            # Clean up health tracking
            if module_to_remove.id in self.module_health:
                self.logger.info(f"(SERVICE MANAGER) Removing health tracking for module {module_to_remove.id}")
                del self.module_health[module_to_remove.id]
            # Remove from modules list
            self.modules = [module for module in self.modules if module.name != name]
            self.logger.info(f"(SERVICE MANAGER) Module {module_to_remove.id} removed from tracking")




