#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Module Service Manager Class

This class is used to manage zeroconf service discovery and registration for modules.

Author: Andrew SG
Created: 15/05/2025
License: GPLv3
"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import socket
import threading
import time
import logging
import os

from src.modules.module_config_manager import ModuleConfigManager

from typing import Dict

class Service:
    def __init__(self, logger: logging.Logger, config_manager: ModuleConfigManager, module_id: str, module_type: str):
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
        self.config_manager = config_manager
        self.module_id = module_id
        self.module_type = module_type
        self.callbacks = {}

        # Controller connection params
        self.controller_ip = None
        self.controller_port = None
        
        # Reconnection tracking
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = self.config_manager.get("network.reconnect_attempts", 5) if config_manager else 5
        self.reconnect_delay = self.config_manager.get("network.reconnect_delay", 5) if config_manager else 5
        self.last_discovery_time = None
        
        # Service registration state
        self.service_registered = False
        self.zeroconf = None
        self.service_browser = None
        self.service_info = None
    
        # Get the ip address of the module
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            # Try multiple methods to get the actual network IP address
            import time
            max_wait = 60  # seconds
            wait_time = 0
            self.ip = None
            while wait_time < max_wait:
                # Method 1: Try hostname -I (most reliable on Linux)
                try:
                    hostname_output = os.popen('hostname -I').read().strip()
                    if hostname_output:
                        ips = hostname_output.split()
                        self.logger.info(f"(SERVICE MANAGER) Available IPs from hostname -I: {ips}")
                        for ip in ips:
                            if not ip.startswith('127.') and not ip.startswith('::1'):
                                self.ip = ip
                                self.logger.info(f"(SERVICE MANAGER) Selected non-loopback IP from hostname -I: {self.ip}")
                                break
                except Exception as e:
                    self.logger.warning(f"(SERVICE MANAGER) Failed to get IP from hostname -I: {e}")
                # Method 2: Try socket.getaddrinfo with a connection to get local IP
                if not self.ip or self.ip.startswith('127.'):
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.connect(("8.8.8.8", 80))
                        self.ip = s.getsockname()[0]
                        s.close()
                        self.logger.info(f"(SERVICE MANAGER) Selected IP from socket connection: {self.ip}")
                    except Exception as e:
                        self.logger.warning(f"(SERVICE MANAGER) Failed to get IP from socket connection: {e}")
                # Method 3: Try socket.gethostbyname but filter out loopback
                if not self.ip or self.ip.startswith('127.'):
                    try:
                        hostname_ip = socket.gethostbyname(socket.gethostname())
                        if not hostname_ip.startswith('127.'):
                            self.ip = hostname_ip
                            self.logger.info(f"(SERVICE MANAGER) Selected IP from hostname resolution: {self.ip}")
                        else:
                            self.logger.warning(f"(SERVICE MANAGER) Hostname resolves to loopback: {hostname_ip}")
                    except Exception as e:
                        self.logger.warning(f"(SERVICE MANAGER) Failed to get IP from hostname resolution: {e}")
                # Method 4: Try to get IP from network interfaces
                if not self.ip or self.ip.startswith('127.'):
                    try:
                        import subprocess
                        result = subprocess.run(['ip', 'route', 'get', '8.8.8.8'], 
                                              capture_output=True, text=True, timeout=5)
                        if result.returncode == 0:
                            lines = result.stdout.strip().split('\n')
                            for line in lines:
                                if 'src' in line:
                                    parts = line.split()
                                    src_index = parts.index('src')
                                    if src_index + 1 < len(parts):
                                        potential_ip = parts[src_index + 1]
                                        if not potential_ip.startswith('127.'):
                                            self.ip = potential_ip
                                            break
                    except Exception as e:
                        pass
                # If still no valid IP, wait and retry
                if not self.ip or self.ip.startswith('127.'):
                    self.logger.warning(f"(SERVICE MANAGER) No valid network IP found yet (current: {self.ip}). Retrying in 2s...")
                    time.sleep(2)
                    wait_time += 2
                else:
                    break
            if not self.ip or self.ip.startswith('127.'):
                raise RuntimeError("(SERVICE MANAGER) Could not obtain a valid network IP address after waiting. Aborting service registration.")
            self.logger.info(f"(SERVICE MANAGER) Registering service with IP: {self.ip}")
            # Service registration parameters
            self.service_type = "_module._tcp.local."
            self.service_name = f"{self.module_type}_{self.module_id}._module._tcp.local."
            self.service_port = self.config_manager.get("service.port", 5353) if config_manager else 5353
            # Initialize zeroconf
            self.zeroconf = Zeroconf()

    def register_service(self):
        """Register the service with current IP address"""
        try:
            # Clean up any existing service registration
            if self.service_registered:
                self.logger.info("(SERVICE MANAGER) Cleaning up existing service registration")
                self.cleanup()
            
            # Create service info with current IP
            self.service_info = ServiceInfo(
                self.service_type, # the service type - tcp protocol, local domain
                self.service_name, # a unique name for the service to advertise itself
                addresses=[socket.inet_aton(self.ip)], # the ip address of the controller
                port=self.service_port, # the port number of the controller
                properties={
                    'type': self.module_type,
                    'id': self.module_id  # Important: Add module_id to properties
                } # the properties of the service
            )
            
            # Register the service
            self.zeroconf.register_service(self.service_info)
            
            # Start browsing for controller services
            self.service_browser = ServiceBrowser(self.zeroconf, "_controller._tcp.local.", self)
            
            self.service_registered = True
            self.reconnect_attempts = 0  # Reset reconnection attempts on successful registration
            self.logger.info(f"(SERVICE MANAGER) Module service registered with service info: {self.service_info}")
            return True
            
        except Exception as e:
            self.logger.error(f"(SERVICE MANAGER) Error registering service: {e}")
            return False

    def add_service(self, zeroconf, service_type, name):
        """Called when controller is discovered"""
        # Ignore our own service
        if name == f"{self.module_type}_{self.module_id}._module._tcp.local.":
            return
            
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self.logger.info(f"(SERVICE MANAGER) Controller discovered. info={info}")
            controller_ip = socket.inet_ntoa(info.addresses[0])
            controller_port = info.port
            
            # Check if this is a new controller or the same one
            if (self.controller_ip == controller_ip and 
                self.controller_port == controller_port):
                self.logger.info("(SERVICE MANAGER) Same controller re-discovered, ignoring")
                return
            
            # Update controller connection info
            self.controller_ip = controller_ip
            self.controller_port = controller_port
            self.last_discovery_time = time.time()
            self.reconnect_attempts = 0  # Reset reconnection attempts on successful discovery
            
            self.logger.info(f"(SERVICE MANAGER) Found controller zeroconf service at {self.controller_ip}:{self.controller_port}")
            
            # Notify module that controller was discovered
            if "when_controller_discovered" in self.callbacks:
                self.callbacks["when_controller_discovered"](self.controller_ip, self.controller_port)
            else:
                self.logger.warning("(SERVICE MANAGER) No when_controller_discovered callback registered")

    def remove_service(self, zeroconf, service_type, name):
        """Called when controller disappears"""
        self.logger.warning("(SERVICE MANAGER) Lost connection to controller")
        
        # Only trigger disconnect if we were actually connected
        if self.controller_ip and self.controller_port:
            if "controller_disconnected" in self.callbacks:
                self.callbacks["controller_disconnected"]()
            else:
                self.logger.warning("(SERVICE MANAGER) No controller_disconnected callback registered")
            
            # Reset controller connection state
            self.controller_ip = None
            self.controller_port = None
            self.logger.info("(SERVICE MANAGER) Controller connection state reset")
            
            # Start reconnection attempts if configured
            if self.max_reconnect_attempts > 0:
                self._schedule_reconnection()

    def _schedule_reconnection(self):
        """Schedule a reconnection attempt"""
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            self.logger.info(f"(SERVICE MANAGER) Scheduling reconnection attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} in {self.reconnect_delay} seconds")
            
            # Schedule reconnection in a separate thread
            def delayed_reconnect():
                time.sleep(self.reconnect_delay)
                if not self.controller_ip:  # Only reconnect if still disconnected
                    self.logger.info(f"(SERVICE MANAGER) Attempting reconnection {self.reconnect_attempts}/{self.max_reconnect_attempts}")
                    self._attempt_reconnection()
            
            threading.Thread(target=delayed_reconnect, daemon=True).start()
        else:
            self.logger.warning(f"(SERVICE MANAGER) Max reconnection attempts ({self.max_reconnect_attempts}) reached")

    def _attempt_reconnection(self):
        """Attempt to reconnect to the controller"""
        try:
            # Re-register service to refresh discovery
            if self.register_service():
                self.logger.info("(SERVICE MANAGER) Service re-registered for reconnection attempt")
            else:
                self.logger.error("(SERVICE MANAGER) Failed to re-register service for reconnection")
        except Exception as e:
            self.logger.error(f"(SERVICE MANAGER) Error during reconnection attempt: {e}")

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"(SERVICE MANAGER) Service updated: {name}")
        
        # Treat service updates the same as new discoveries for controller services
        # This ensures we reconnect when the controller restarts
        if name.endswith('._controller._tcp.local.'):
            self.logger.info(f"(SERVICE MANAGER) Controller service updated, treating as new discovery")
            self.add_service(zeroconf, service_type, name)
    
    def set_callbacks(self, callbacks: Dict):
        self.callbacks = callbacks

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
        
        self.service_registered = False
        self.logger.info("(SERVICE MANAGER) Service cleanup complete")