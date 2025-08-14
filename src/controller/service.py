#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Service Manager

The service manager is responsible for discovering, registering and unregistering zeroconf services (modules) with the controller.

"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging
import threading
import time

@dataclass
class Module:
    """Dataclass to represent a module in the habitat system - used by zeroconf to discover modules"""
    id: str
    name: str
    type: str
    ip: str
    port: int
    properties: Dict[str, Any]

class Service():
    def __init__(self, config_manager=None):
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager

        # Module tracking
        self.modules = []
        self.module_health = {}
        self.on_module_discovered = None  # Callback for module discovery. Means that controller can do things with other managers when we discover a module here.
        self.on_module_removed = None  # Callback for module removal. Means that controller can do things with other managers when we remove a module here.#
        self.callbacks = {} # Callbacks dict
        
        # Module tracking with timestamps for reconnection detection
        self.module_discovery_times = {}
        self.module_last_seen = {}
        
        # Get the ip address of the controller
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            # For controller, wait for proper network IP (192.168.1.1)
            self.ip = self._wait_for_proper_ip()
        
        self.logger.info(f"Controller IP address: {self.ip}")
        
        # Get service configuration from config manager if available
        self.service_port = 5353 # Default value
        self.service_type = "_controller._tcp.local."  # Use standard service type format
        self.service_name = f"controller_{socket.gethostname()}._controller._tcp.local."
        
        if self.config_manager:
            self.service_port = self.config_manager.get("service.port", self.service_port)
            self.service_type = self.config_manager.get("service.service_type", self.service_type)
            self.service_name = self.config_manager.get("service.service_name", self.service_name)

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
            attempt += 1
            self.logger.info(f"IP detection attempt {attempt} (waiting for DHCP)...")
            
            # Try multiple methods to get the actual network IP address
            ip = None
            
            # Method 1: Try ifconfig eth0 (most reliable for eth0 IP)
            try:
                import subprocess
                result = subprocess.run(['ifconfig', 'eth0'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # Parse ifconfig output to find eth0 IP
                    for line in result.stdout.split('\n'):
                        if 'inet ' in line:
                            # Extract IP from "inet 192.168.1.197" format
                            parts = line.strip().split()
                            for i, part in enumerate(parts):
                                if part == 'inet' and i + 1 < len(parts):
                                    potential_ip = parts[i + 1]
                                    if potential_ip.startswith('192.168.1.'):
                                        ip = potential_ip
                                        self.logger.info(f"Found eth0 IP from ifconfig: {ip}")
                                        break
                            if ip:
                                break
                    
                    if not ip:
                        self.logger.warning(f"No eth0 IP found in ifconfig output")
                else:
                    self.logger.warning(f"ifconfig eth0 failed: {result.stderr}")
            except Exception as e:
                self.logger.warning(f"Failed to get IP from ifconfig eth0: {e}")
            
            # Method 2: Try socket.getaddrinfo with a connection to get local IP
            if not ip or ip.startswith('127.'):
                try:
                    # Create a socket and connect to a remote address to get local IP
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    potential_ip = s.getsockname()[0]
                    s.close()
                    
                    # Only use eth0 IP
                    if potential_ip.startswith('192.168.1.'):
                        ip = potential_ip
                        self.logger.info(f"Selected eth0 IP from socket connection: {ip}")
                    else:
                        self.logger.warning(f"Socket connection returned non-eth0 IP: {potential_ip}")
                except Exception as e:
                    self.logger.warning(f"Failed to get IP from socket connection: {e}")
            
            # Method 3: Try socket.gethostbyname but filter out loopback
            if not ip or ip.startswith('127.'):
                try:
                    hostname_ip = socket.gethostbyname(socket.gethostname())
                    if not hostname_ip.startswith('127.') and hostname_ip.startswith('192.168.1.'):
                        ip = hostname_ip
                        self.logger.info(f"Selected eth0 IP from hostname resolution: {ip}")
                    else:
                        self.logger.warning(f"Hostname resolves to non-eth0 IP: {hostname_ip}")
                except Exception as e:
                    self.logger.warning(f"Failed to get IP from hostname resolution: {e}")
            
            # Method 4: Try to get IP from network interfaces
            if not ip or ip.startswith('127.'):
                try:
                    import subprocess
                    result = subprocess.run(['ip', 'route', 'get', '8.8.8.8'], 
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        # Parse the output to get the source IP
                        lines = result.stdout.strip().split('\n')
                        for line in lines:
                            if 'src' in line:
                                parts = line.split()
                                src_index = parts.index('src')
                                if src_index + 1 < len(parts):
                                    potential_ip = parts[src_index + 1]
                                    if not potential_ip.startswith('127.') and potential_ip.startswith('192.168.1.'):
                                        ip = potential_ip
                                        self.logger.info(f"Selected eth0 IP from ip route: {ip}")
                                        break
                                    else:
                                        self.logger.warning(f"ip route returned non-eth0 IP: {potential_ip}")
                except Exception as e:
                    self.logger.warning(f"Failed to get IP from ip route: {e}")
            
            # Check if we got a proper IP (eth0 only)
            if ip and ip.startswith('192.168.1.'):
                self.logger.info(f"Found proper eth0 IP: {ip}")
                return ip
            else:
                self.logger.warning(f"No proper eth0 IP found yet (attempt {attempt}). Waiting for DHCP...")
                time.sleep(5)  # Wait 5 seconds before next attempt

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
                self.modules.clear()
                self.module_health.clear()
                self.module_discovery_times.clear()
                self.module_last_seen.clear()
                self.logger.info("Cleared module tracking")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
        finally:
            self.logger.info("Service manager cleanup complete")
    
    # zeroconf methods
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
            
            # Check if this module already exists
            existing_module = next((m for m in self.modules if m.id == module.id), None)
            if existing_module:
                # Update existing module with new information
                self.logger.info(f"Updating existing module: {module.id}")
                existing_module.ip = module.ip
                existing_module.port = module.port
                existing_module.properties = module.properties
                module = existing_module
            else:
                # Add new module
                self.modules.append(module)
                self.logger.info(f"Added new module: {module}")
            
            # Update tracking information
            current_time = time.time()
            self.module_discovery_times[module.id] = current_time
            self.module_last_seen[module.id] = current_time
            
            # Call the callback if it exists
            if self.on_module_discovered:
                self.logger.info(f"Calling module discovery callback")
                self.on_module_discovered(module)

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")
        # Update the last seen time for this module
        info = zeroconf.get_service_info(service_type, name)
        if info:
            module_id = str(info.properties.get(b'id', b'unknown').decode())
            self.module_last_seen[module_id] = time.time()
            self.logger.info(f"Updated last seen time for module: {module_id}")

    def remove_service(self, zeroconf, service_type, name):
        """Remove a service from the list of discovered modules.
        This will only trigger if a module broadcasts it's "remove service" message, which it is unlikely to do unless it is shutting down gracefully.
        """
        self.logger.info(f"Removing module: {name}")
        try:
            # Find the module being removed
            module_to_remove = next((module for module in self.modules if module.name == name), None)
            if module_to_remove:
                # Clean up health tracking
                if module_to_remove.id in self.module_health:
                    self.logger.info(f"Removing health tracking for module {module_to_remove.id}")
                    del self.module_health[module_to_remove.id]
                
                # Clean up tracking information
                if module_to_remove.id in self.module_discovery_times:
                    del self.module_discovery_times[module_to_remove.id]
                if module_to_remove.id in self.module_last_seen:
                    del self.module_last_seen[module_to_remove.id]
                
                # Remove from modules list
                self.modules = [module for module in self.modules if module.name != name]
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
    
    def get_module_status(self, module_id: str) -> Optional[Dict]:
        """Get detailed status for a specific module"""
        module = next((m for m in self.modules if m.id == module_id), None)
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
                'uptime': current_time - discovery_time if discovery_time > 0 else 0,
                'health': self.module_health.get(module_id, {})
            }
        return None