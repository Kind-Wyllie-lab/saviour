#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Network Manager

The network manager is responsible for discovering, registering and unregistering zeroconf services (modules) with the controller, as well as discovering controller's own ip.

Author: Andrew SG
Created: ?
"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import os
import socket
import uuid
from typing import Dict, Any, Optional
import logging
import threading
import time
import subprocess
from src.controller.models import Module # Import the dataclass for Modules

class Network():
    def __init__(self, config=None):
        self.logger = logging.getLogger(__name__)
        self.config = config

        # Module tracking
        self.discovered_modules = []

        # Module tracking with timestamps for reconnection detection
        self.module_discovery_times = {}
        self.module_last_seen = {}
        
        # Get the ip address of the controller
        self.ip_is_valid = False
        self.ip = self._wait_for_proper_ip()

        self.logger.info(f"Controller IP address: {self.ip}")
        
        self.service_port = self.config.get("zeroconf.port", 5353)
        self.service_type = self.config.get("zeroconf.service_type", "_controller._tcp.local.")
        self.service_name = self.config.get("zeroconf.service_name", f"controller_{socket.gethostname()}._controller._tcp.local.")

        # Initialize zeroconf but don't register service yet
        self.zeroconf = Zeroconf()
        self.service_info = None
        self.browser = None
        self.service_registered = False

        self.logger.info(f"Controller service manager initialized (service not yet registered)")

    def _wait_for_proper_ip(self):
        """Wait for the proper network IP (192.168.1.1) to be available"""
        self.logger.info("Waiting for proper network IP (192.168.1.1)...")
        
        attempt = 0
        
        while True:
            # Try multiple methods to get the actual network IP address
            ip = None
            
            # Method 1: Try ifconfig eth0 (most reliable for eth0 IP)
            try:
                ip = self._get_eth0_ip_ifconfig()
                if not self._validate_ip(ip):
                    self.logger.warning(f"{potential_ip} could not be validated")
                    return False
                return ip
            except Exception as e:
                self.logger.warning(f"Failed to get IP from ifconfig eth0: {e}")


    def _get_eth0_ip_ifconfig(self) -> str:
        result = subprocess.run(['ifconfig', 'eth0'], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return

        # Parse ifconfig output to find eth0 IP
        for line in result.stdout.split('\n'):
            if 'inet ' in line:
                # Extract IP from "inet 192.168.1.197" format
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == 'inet' and i + 1 < len(parts):
                        potential_ip = parts[i + 1]
                        return potential_ip


    def _validate_ip(self, potential_ip: str) -> bool:
        """Check that the ip belongs to valid ranges"""
        if potential_ip.startswith('192.168.1.') or potential_ip.startswith("10.0.0."):
            ip = potential_ip
            self.ip_is_valid = True
            self.logger.info(f"Found eth0 IP from ifconfig: {ip}")
            return True
        else:
            return False


    def register_service(self):
        """Register the controller service"""
        if self.service_registered:
            self.logger.info("Service already registered")
            return True
            
        try:
            # Create service info with current IP
            self.service_info = ServiceInfo(
                self.service_type, # the service type - tcp protocol, local domain
                self.service_name, # a unique name for the service to advertise itself
                addresses=[socket.inet_aton(self.ip)], # the ip address of the controller
                port=self.service_port, # the port number of the controller
                properties={
                    'type': 'controller',
                    'id': socket.gethostname()
                } # the properties of the service
            )
            self.zeroconf.register_service(self.service_info) # register the service with the above info
            self.browser = ServiceBrowser(self.zeroconf, "_module._tcp.local.", self) # Browse for habitat_module services"

            self.service_registered = True
            self.logger.info(f"Controller service registered with service info: {self.service_info}")
            return True
        except Exception as e:
            self.logger.error(f"Error registering service: {e}")
            return False

    def cleanup(self):
        """Cleanup zeroconf resources"""
        self.logger.info("Cleaning up service manager")
        try:
            if hasattr(self, 'zeroconf'):
                # Unregister our own service
                self.zeroconf.unregister_service(self.service_info)
                self.logger.info("Unregistered controller service")
                
                # Cancel browser
                if hasattr(self, 'browser'):
                    self.browser.cancel()
                    self.logger.info("Cancelled service browser")
                
                # Close zeroconf
                self.zeroconf.close()
                self.logger.info("Closed zeroconf")
                
                # Clear module list
                self.discovered_modules.clear()
                self.module_discovery_times.clear()
                self.module_last_seen.clear()
                self.logger.info("Cleared module tracking")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        finally:
            self.logger.info("Service manager cleanup complete")
    
    def _validate_discovered_module(self, module):
        self.logger.info(f"Validating module {module.id}")
        if self.discovered_modules:
            self.logger.info(f"Validing against {self.discovered_modules}")
            valid_module = True # Flag which will get set false if module turns out to be a duplicate
            for existing_module in self.discovered_modules:
                self.logger.info(f"Comparing {existing_module.id} to {module.id}")
                if existing_module.id == module.id:
                    self.logger.info(f"ID {module.id} is already in known modules, updating service info")
                    existing_module.ip = module.ip
                    existing_module.port = module.port
                    existing_module.properties = module.properties
                    valid_module = False
                    self.logger.info(f"IP changed for module {module.id}, new IP: {module.ip}")
                    self.api.notify_module_ip_change(module.id, module.ip)
                    # self.notify_module_update(self.discovered_modules)
                if existing_module.ip == module.ip:
                    self.logger.info(f"IP {module.ip} is already in known modules, updating service info")
                    old_module_id = existing_module.id
                    existing_module.id = module.id
                    existing_module.port = module.port
                    existing_module.properties = module.properties
                    valid_module = False
                    self.logger.info(f"ID changed for module at IP {module.ip}, old ID: {existing_module} new ID: {module.id}")
                    self.api.notify_module_id_change(old_module_id, module.id)
                    # self.notify_module_update(self.discovered_modules)
                else:
                    continue
            # Finish looping and return whether module was valid or not
            return valid_module
        else:
            self.logger.info("No modules yet discovered, adding this as first module")
            return True

    # zeroconf methods
    def add_service(self, zeroconf, service_type, name):
        """Add a service to the list of discovered modules"""
        self.logger.info(f"Discovered module: {name}")
        info = zeroconf.get_service_info(service_type, name)
        if not info:
            self.logger.warning("add_service was called with no service info") 
            return

        module = Module(
            id = info.properties.get(b'id', b'unknown').decode(),
            name = info.properties.get(b'name', b'unknown').decode(),
            zeroconf_name = name,
            type = info.properties.get(b'type', b'unknown').decode(),
            ip = socket.inet_ntoa(info.addresses[0]),
            port = info.port,
        )
        
        if self._validate_discovered_module(module) == True:
            self.discovered_modules.append(module)
            self.logger.info(f"Added new module: {module}")
        
        self.logger.info(f"New module list: {self.discovered_modules}")
        
        # Update tracking information
        current_time = time.time()
        self.module_discovery_times[module.id] = current_time
        self.module_last_seen[module.id] = current_time
        
        # Call the callback if it exists
        self.logger.info(f"Calling module discovery callback")
        self.api.notify_module_update(self.discovered_modules)

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")
        # Update the last seen time for this module
        info = zeroconf.get_service_info(service_type, name)
        if info:
            module_id = str(info.properties.get(b'id', b'unknown').decode())
            self.module_last_seen[module_id] = time.time()
            self.logger.info(f"Updated last seen time for module: {module_id}")
            self.api.notify_module_update(self.discovered_modules)

    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules.
        This will only trigger if a module broadcasts it's "remove service" message, which it is unlikely to do unless it is shutting down gracefully.
        """
        self.logger.info(f"Removing module: {name}")
        try:
            # Find the module being removed
            module_to_remove = next((module for module in self.discovered_modules if module.name == name), None)
            if module_to_remove:
                
                # Clean up tracking information
                if module_to_remove.id in self.module_discovery_times:
                    del self.module_discovery_times[module_to_remove.id]
                if module_to_remove.id in self.module_last_seen:
                    del self.module_last_seen[module_to_remove.id]
                
                # Remove from modules list
                self.discovered_modules = [module for module in self.discovered_modules if module.name != name]
                self.logger.info(f"Module {module_to_remove.id} removed from tracking")

                # Call the callback if it exists
                if self.on_module_removed:
                    self.logger.info(f"Calling module removal callback")
                    self.on_module_removed(module_to_remove)
            else:
                self.logger.warning(f"Attempted to remove unknown module: {name}")
        except Exception as e:
            self.logger.error(f"Error removing module {name}: {e}")

    def get_modules(self):
        """Return module list"""
        modules = []
        for module in self.discovered_modules:
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
    
    def get_own_ip(self):
        if self.ip_is_valid:
            return self.ip
        else:
            self.logger.warning("Own IP requested but not known to be valid, scanning for own ip again")
            self._wait_for_proper_ip()

    def get_module_status(self, module_id: str) -> Optional[Dict]:
        """Get detailed status for a specific module"""
        module = next((m for m in self.discovered_modules if m.id == module_id), None)
        if module:
            current_time = time.time()
            last_seen = self.module_last_seen.get(module_id, 0)
            discovery_time = self.module_discovery_times.get(module_id, 0)
            
            return {
                'id': module.id,
                'type': module.type,
                'ip': module.ip,
                'port': module.port,
                'last_seen': last_seen,
                'discovery_time': discovery_time,
                'uptime': current_time - discovery_time if discovery_time > 0 else 0
            }
        return None