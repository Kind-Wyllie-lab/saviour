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
        self.on_module_discovered = None  # Callback for module discovery. Means that controller can do things with other managers when we discover a module here.
        self.on_module_removed = None  # Callback for module removal. Means that controller can do things with other managers when we remove a module here.
        
        # Get the ip address of the controller
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            try:
                # Try hostname -I first
                hostname_output = os.popen('hostname -I').read().strip()
                if hostname_output:
                    self.ip = hostname_output.split()[0]
                else:
                    # Fallback to socket method
                    self.ip = socket.gethostbyname(socket.gethostname())
            except (IndexError, Exception) as e:
                # Fallback to socket method if hostname -I fails
                self.logger.warning(f"(SERVICE MANAGER) Failed to get IP from hostname -I: {e}, using fallback method")
                try:
                    self.ip = socket.gethostbyname(socket.gethostname())
                except Exception as e2:
                    # Last resort fallback
                    self.logger.error(f"(SERVICE MANAGER) Failed to get IP address: {e2}, using localhost")
                    self.ip = "127.0.0.1"

        # Get service configuration from config manager if available
        service_port = 5353 # Default value #TODO: Read this from config_manager    
        service_type = "_controller._tcp.local."
        service_name = "controller._controller._tcp.local."
        
        if self.config_manager:
            service_port = self.config_manager.get("zeroconf.port", service_port)
            service_type = self.config_manager.get("zeroconf.service_type", service_type)
            service_name = self.config_manager.get("zeroconf.service_name", service_name)

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

        self.logger.info(f"(SERVICE MANAGER) Controller service registered with service info: {self.service_info}")

    def cleanup(self):
        """Cleanup zeroconf resources"""
        self.logger.info("(SERVICE MANAGER) Cleaning up service manager")
        try:
            if hasattr(self, 'zeroconf'):
                # Unregister our own service
                self.zeroconf.unregister_service(self.service_info)
                self.logger.info("(SERVICE MANAGER) Unregistered controller service")
                
                # Cancel browser
                if hasattr(self, 'browser'):
                    self.browser.cancel()
                    self.logger.info("(SERVICE MANAGER) Cancelled service browser")
                
                # Close zeroconf
                self.zeroconf.close()
                self.logger.info("(SERVICE MANAGER) Closed zeroconf")
                
                # Clear module list
                self.modules.clear()
                self.module_health.clear()
                self.logger.info("(SERVICE MANAGER) Cleared module tracking")
        except Exception as e:
            self.logger.error(f"(SERVICE MANAGER) Error during cleanup: {e}")
        finally:
            self.logger.info("(SERVICE MANAGER) Service manager cleanup complete")
    
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
            
            # Call the callback if it exists
            if self.on_module_discovered:
                self.logger.info(f"(SERVICE MANAGER) Calling module discovery callback")
                self.on_module_discovered(module)

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"(SERVICE MANAGER) Service updated: {name}")

    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules"""
        self.logger.info(f"(SERVICE MANAGER) Removing module: {name}")
        try:
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

                # Call the callback if it exists
                if self.on_module_removed:
                    self.logger.info(f"(SERVICE MANAGER) Calling module removal callback")
                    self.on_module_removed(module_to_remove)
            else:
                self.logger.warning(f"(SERVICE MANAGER) Attempted to remove unknown module: {name}")
        except Exception as e:
            self.logger.error(f"(SERVICE MANAGER) Error removing module {name}: {e}")

    def get_modules(self):
        """Return module list"""
        modules = []
        for module in self.modules:
            # Convert module to dict and ensure all keys are strings
            module_dict = {
                'id': module.id,
                'type': module.type,
                'ip': module.ip,
                'port': module.port,
                'properties': {k.decode() if isinstance(k, bytes) else k: 
                             v.decode() if isinstance(v, bytes) else v 
                             for k, v in module.properties.items()}
            }
            modules.append(module_dict)
        return modules