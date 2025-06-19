#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Module Service Manager Class

This class is used to manage zeroconf service discovery and registration for modules.

Author: Andrew SG
Created: 15/05/2025
License: GPLv3

#TODO: Use callbacks instead of passing through module
"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import socket
import threading
import time
import logging
import os

from src.modules.module_config_manager import ModuleConfigManager
from src.modules.module import Module

class ModuleServiceManager:
    def __init__(self, logger: logging.Logger, module: Module):
        """
        Initialize the module service manager

        Args:
            logger: The logger to use for logging
            config_manager: The config manager to use for configuration
            module_id: The id of the module
            module: The module itself.
        """

        # Basic params
        self.logger = logger
        self.module = module
        self.config_manager = module.config_manager
        self.module_id = module.module_id
        self.module_type = module.module_type

        # Controller connection params
        self.controller_ip = None
        self.controller_port = None
    
        # Get the ip address of the module
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            self.ip = os.popen('hostname -I').read().split()[0]
        
        # Get service configuration from config manager if available
        service_port = 5353 # Default value
        service_type = "_module._tcp.local."  # Use standard service type format
        service_name = f"{self.module_type}_{self.module_id}._module._tcp.local."
        
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
            properties={
                'type': self.module_type,
                'id': self.module_id  # Important: Add module_id to properties
            } # the properties of the service
        )
        self.zeroconf.register_service(self.service_info) # register the service with the above info
        self.service_browser = ServiceBrowser(self.zeroconf, "_controller._tcp.local.", self) # Browse for habitat_module services"

        self.logger.info(f"(SERVICE MANAGER) Module service registered with service info: {self.service_info}")
    
        # zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Called when controller is discovered"""
        # Ignore our own service
        if name == f"{self.module_type}_{self.module_id}._module._tcp.local.":
            return
            
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self.logger.info(f"(SERVICE MANAGER) Controller discovered. info={info}")
            self.controller_ip = socket.inet_ntoa(info.addresses[0])
            self.controller_port = info.port
            self.logger.info(f"(SERVICE MANAGER) Found controller zeroconf service at {self.controller_ip}:{self.controller_port}")
            
            # Notify module that controller was discovered
            self.module.when_controller_discovered(self.controller_ip, self.controller_port)

    def remove_service(self, zeroconf, service_type, name):
        """Called when controller disappears"""
        self.logger.warning("(SERVICE MANAGER) Lost connection to controller")
        self.module.controller_disconnected()
            
            # Reset controller connection state
        self.controller_ip = None
        self.controller_port = None
        self.logger.info("(SERVICE MANAGER) Controller connection state reset")

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"(SERVICE MANAGER) Service updated: {name}")
    

    def cleanup(self):
        """Clean up the zeroconf service"""
        # Clean up zeroconf
        # destroy the service browser
        if self.service_browser:
            try:
                self.service_browser.cancel()
                self.logger.info("(SERVICE MANAGER) Service browser cancelled")
            except Exception as e:
                self.logger.error(f"(SERVICE MANAGER) Error canceling service browser: {e}")
            self.service_browser = None
        # unregister the service
        if self.zeroconf:
            try:
                self.zeroconf.unregister_service(self.service_info) # unregister the service
                time.sleep(1)
                self.zeroconf.close()
                self.logger.info("(SERVICE MANAGER) Zeroconf service unregistered and closed")
            except Exception as e:
                self.logger.error(f"(SERVICE MANAGER) Error unregistering service: {e}")
            self.zeroconf = None