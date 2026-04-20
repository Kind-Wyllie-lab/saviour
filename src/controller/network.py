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

        # Module tracking with timestamps for reconnection detection
        self.module_discovery_times = {}
        self.module_last_seen = {}
        
        # Get the ip address of the controller
        self.interface = "eth0" # The interface connected to the SAVIOUR network
        self.ip_is_valid = False
        self.ip = self._wait_for_proper_ip()

        self.logger.info(f"Controller IP address: {self.ip}")
        
        self.service_port = self.config.get("zeroconf.port", 5353)
        self.service_type = self.config.get("zeroconf.service_type", "_controller._tcp.local.")
        self.service_name = self.config.get("zeroconf.service_name", f"controller_{socket.gethostname()}._controller._tcp.local.")

        # Initialize zeroconf but don't register service yet
        self.zeroconf = Zeroconf(interfaces=[self.ip])
        self.service_info = None
        self.browser = None
        self.service_registered = False

        self.logger.info(f"Controller service manager initialized (service not yet registered)")

    def _wait_for_proper_ip(self):
        """Wait for the proper network IP (192.168.1.1) to be available"""
        self.logger.info("Waiting for proper network IP on eth0...")
        
        attempt = 0
        
        while True:
            # Try multiple methods to get the actual network IP address
            ip = None
            
            # Method 1: Try ifconfig eth0 (most reliable for eth0 IP)
            try:
                ip = self._get_eth0_ip_nm()
                if not self._validate_ip(ip):
                    self.logger.warning(f"{ip} could not be validated")
                    return False
                return ip
            except Exception as e:
                self.logger.warning(f"Failed to get IP from ifconfig eth0: {e}")


    def _get_eth0_ip_nm(self) -> str:
        """
        SAVIOUR Controllers currently get assigned a static IP during setup, and act as DHCP servers (290126)
        This method gets the static IP on interface eth0.
        """
        interface = "eth0"
        cmd = ["nmcli", "-g", "IP4.ADDRESS", "device", "show", interface]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        ip = result.stdout.strip().split("/")[0]
        return ip


    def _validate_ip(self, potential_ip: str) -> bool:
        """Check that the ip belongs to valid ranges"""
        if potential_ip.startswith('192.168.1.') or potential_ip.startswith("10.0."):
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
                self.module_discovery_times.clear()
                self.module_last_seen.clear()
                self.logger.info("Cleared module tracking")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        finally:
            self.logger.info("Service manager cleanup complete")

    
    """Zeroconf Required Methods"""
    @staticmethod
    def _prop(properties, key, default=b'unknown') -> str:
        """Decode a zeroconf property, treating missing or None values as default."""
        val = properties.get(key)
        return (val if val is not None else default).decode()

    def add_service(self, zeroconf, service_type, name):
        """Add a service to the list of discovered modules"""
        self.logger.info(f"Discovered module: {name}")
        info = zeroconf.get_service_info(service_type, name)
        if not info:
            self.logger.warning("add_service was called with no service info")
            return

        module_id = self._prop(info.properties, b'id')

        module = Module(
            id = module_id,
            name = self._prop(info.properties, b'name'),
            version = self._prop(info.properties, b'version'),
            zeroconf_name = name,
            type = self._prop(info.properties, b'type'),
            ip = socket.inet_ntoa(info.addresses[0]),
            port = info.port,
        )

        self.logger.info(f"Module {module.id} {module.version} discovered")
        
        # Update tracking information
        current_time = time.time()
        self.module_discovery_times[module.id] = current_time
        self.module_last_seen[module.id] = current_time
    
        self.facade.module_discovery(module)


    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")
        # Update the last seen time for this module
        info = zeroconf.get_service_info(service_type, name)
        if not info: 
            self.logger.warning("update_service was called with no service info") 
            return

        module_id = self._prop(info.properties, b'id')

        module = Module(
            id = module_id,
            name = self._prop(info.properties, b'name'),
            version = self._prop(info.properties, b'version'),
            zeroconf_name = name,
            type = self._prop(info.properties, b'type'),
            ip = socket.inet_ntoa(info.addresses[0]),
            port = info.port,
        )

        self.module_last_seen[module_id] = time.time()
        self.logger.info(f"Updated last seen time for module: {module_id}")
        self.facade.module_rediscovered(module_id)
        self.facade.module_discovery(module)


    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules.
        This will only trigger if a module broadcasts it's "remove service" message, which it is unlikely to do unless it is shutting down gracefully.
        """
        self.logger.info(f"Removing module: {name}")
        try:
            # Find the module being removed
            info = zeroconf.get_service_info(service_type, name)
            if not info: 
                self.logger.warning("update_service was called with no service info") 
                return
            module_to_remove = str(info.properties.get(b'id', b'unknown').decode())
            
            # Call the callback if it exists
            if self.on_module_removed:
                self.logger.info(f"Calling module removal callback")
                self.on_module_removed(module_to_remove)
            else:
                self.logger.warning(f"No callback to remove module: {module_to_remove}")
        except Exception as e:
            self.logger.error(f"Error removing module {name}: {e}")
    

    def get_own_ip(self):
        if self.ip_is_valid:
            return self.ip
        else:
            self.logger.warning("Own IP requested but not known to be valid, scanning for own ip again")
            self._wait_for_proper_ip()


    def get_module_status(self, module_id: str) -> Optional[Dict]:
        """Get detailed status for a specific module"""
        current_time = time.time()
        last_seen = self.module_last_seen.get(module_id, None)
        discovery_time = self.module_discovery_times.get(module_id, None)
        
        return {
            'last_seen': last_seen,
            'discovery_time': discovery_time,
            'uptime': current_time - discovery_time if discovery_time > 0 else None
        }