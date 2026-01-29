#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Network Manager

This class is used to manage zeroconf service discovery and registration for modules.

Author: Andrew SG
Created: 15/05/2025
"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import socket
import threading
import time
import logging
import os

from src.modules.config import Config

from typing import Dict

class Network:
    def __init__(self, config: Config, module_id: str, module_type: str):
        """
        Initialize the module network manager

        Args:
            logger: The logger to use for logging
            config: The config manager to use for configuration
            module_id: The id of the module
            module_type: The type of module e.g. camera.
        """

        # Basic params
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.module_id = module_id
        self.module_type = module_type
        self.valid_ips = [
            "192.168.1.",
            "10.0.0."
        ]

        # Controller connection params
        self.controller_ip = None
        self.controller_port = None
        
        # Reconnection tracking
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = self.config.get("network.reconnect_attempts", 5) if config else 5
        self.reconnect_delay = self.config.get("network.reconnect_delay", 5) if config else 5
        self.last_discovery_time = None
        
        # Service registration state
        self.service_registered = False
        self.zeroconf = None
        self.service_browser = None
        self.service_info = None
        self.ip = None
        self._find_own_ip()  # Find own IP address on initialization
        self.logger.info(f"Registering service with IP: {self.ip}")
        
        # Service registration parameters
        self.service_type = self.config.get("network.zeroconf_service_type", "_module._tcp.local.")

        self.service_name = f"{self.module_type}_{self.module_id}._module._tcp.local."
        self.service_port = self.config.get("network._zeroconf_port", 5353)
        # Initialize zeroconf
        self.zeroconf = Zeroconf()


    def start(self):
        self.logger.info("Starting service registration")
        if not self.ip:
            self._find_own_ip()
        

    def register_service(self):
        """Register the service with current IP address"""
        self.logger.info(f"Starting service registration with ip {self.ip}")
        try:
            # Clean up any existing service registration
            if self.service_registered:
                self.logger.info("Cleaning up existing service registration")
                self.cleanup()
            
            # Create service info with current IP
            self.service_info = ServiceInfo(
                self.service_type, # the service type - tcp protocol, local domain
                self.service_name, # a unique name for the service to advertise itself
                addresses=[socket.inet_aton(self.ip)], # the ip address of the controller
                port=self.service_port, # the port number of the controller
                properties={
                    'type': self.module_type,
                    'id': self.module_id,  # Important: Add module_id to properties
                    'name': self.api.get_module_name()
                } # the properties of the service
            )
            
            self.logger.info(f"Registering {self.service_info}")

            # Register the service
            self.zeroconf.register_service(self.service_info)
            
            # Start browsing for controller services
            self.service_browser = ServiceBrowser(self.zeroconf, "_controller._tcp.local.", self)
            
            self.service_registered = True
            self.reconnect_attempts = 0  # Reset reconnection attempts on successful registration
            self.logger.info(f"Module service registered with service info: {self.service_info}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error registering service: {e}")
            return False

    def add_service(self, zeroconf, service_type, name):
        """Called when controller is discovered"""
        # Ignore our own service
        if name == f"{self.module_type}_{self.module_id}._module._tcp.local.":
            return
            
        info = zeroconf.get_service_info(service_type, name)
        if info:
            self.logger.info(f"Controller discovered. info={info}")
            controller_ip = socket.inet_ntoa(info.addresses[0])
            controller_port = info.port
            
            # Check if this is a new controller or the same one
            if (self.controller_ip == controller_ip and 
                self.controller_port == controller_port):
                self.logger.info("Same controller re-discovered, ignoring")
                return
            
            # Update controller connection info
            self.controller_ip = controller_ip
            self.controller_port = controller_port
            self.last_discovery_time = time.time()
            self.reconnect_attempts = 0  # Reset reconnection attempts on successful discovery
            
            self.logger.info(f"Found controller zeroconf service at {self.controller_ip}:{self.controller_port}")
            
            # Notify module that controller was discovered
            self.api.when_controller_discovered(self.controller_ip, self.controller_port)


    def remove_service(self, zeroconf, service_type, name):
        """Called when controller disappears"""
        self.logger.warning("Lost connection to controller")
        
        # Only trigger disconnect if we were actually connected
        if self.controller_ip and self.controller_port:
            self.api.controller_disconnected()
            
            # Reset controller connection state
            self.controller_ip = None
            self.controller_port = None
            self.logger.info("Controller connection state reset")
            
            # Start reconnection attempts if configured
            if self.max_reconnect_attempts > 0:
                self._schedule_reconnection()

    def _schedule_reconnection(self):
        """Schedule a reconnection attempt"""
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            self.logger.info(f"Scheduling reconnection attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} in {self.reconnect_delay} seconds")
            
            # Schedule reconnection in a separate thread
            def delayed_reconnect():
                time.sleep(self.reconnect_delay)
                if not self.controller_ip:  # Only reconnect if still disconnected
                    self.logger.info(f"Attempting reconnection {self.reconnect_attempts}/{self.max_reconnect_attempts}")
                    self._attempt_reconnection()
            
            threading.Thread(target=delayed_reconnect, daemon=True).start()
        else:
            self.logger.warning(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")

    def _attempt_reconnection(self):
        """Attempt to reconnect to the controller"""
        try:
            # Re-register service to refresh discovery
            if self.register_service():
                self.logger.info("Service re-registered for reconnection attempt")
            else:
                self.logger.error("Failed to re-register service for reconnection")
        except Exception as e:
            self.logger.error(f"Error during reconnection attempt: {e}")

    def update_service(self, zeroconf, service_type, name):
        """Called when a service is updated"""
        self.logger.info(f"Service updated: {name}")
        
        # Treat service updates the same as new discoveries for controller services
        # This ensures we reconnect when the controller restarts
        if name.endswith('._controller._tcp.local.'):
            self.logger.info(f"Controller service updated, treating as new discovery")
            self.add_service(zeroconf, service_type, name)
    
    
    def _find_own_ip(self):
        # Get the ip address of the module
        self.logger.info("Searching for own ip")
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            # Try multiple methods to get the actual network IP address
            import time
            self.ip = None
            attempt = 0
            while True:
                attempt += 1
                self.logger.info(f"Attempting to get eth0 IP (attempt {attempt})...")
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
                                        if potential_ip.startswith('192.168.1.') or potential_ip.startswith('10.0.0.'):
                                            self.ip = potential_ip
                                            self.backup_ip = potential_ip
                                            self.logger.info(f"Found eth0 IP from ifconfig: {self.ip}")
                                            break   
                                if self.ip:
                                    break
                        
                        if not self.ip:
                            self.logger.warning(f"No eth0 IP found in ifconfig output")
                    else:
                        self.logger.warning(f"ifconfig eth0 failed: {result.stderr}")
                except Exception as e:
                    self.logger.warning(f"Failed to get IP from ifconfig eth0: {e}")
                # Method 2: Try socket.getaddrinfo with a connection to get local IP
                # if not self.ip or self.ip.startswith('127.'):
                #     try:
                #         s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                #         s.connect(("8.8.8.8", 80))
                #         potential_ip = s.getsockname()[0]
                #         s.close()
                        
                #         # Only use eth0 IP
                #         if potential_ip.startswith('192.168.1.'):
                #             self.ip = potential_ip
                #             self.logger.info(f"Selected eth0 IP from socket connection: {self.ip}")
                #         else:
                #             self.logger.warning(f"Socket connection returned non-eth0 IP: {potential_ip}")
                #     except Exception as e:
                #         self.logger.warning(f"Failed to get IP from socket connection: {e}")
                # # Method 3: Try socket.gethostbyname but filter out loopback
                # if not self.ip or self.ip.startswith('127.'):
                #     try:
                #         hostname_ip = socket.gethostbyname(socket.gethostname())
                #         if not hostname_ip.startswith('127.') and hostname_ip.startswith('192.168.1.'):
                #             self.ip = hostname_ip
                #             self.logger.info(f"Selected eth0 IP from hostname resolution: {self.ip}")
                #         else:
                #             self.logger.warning(f"Hostname resolves to non-eth0 IP: {hostname_ip}")
                #     except Exception as e:
                #         self.logger.warning(f"Failed to get IP from hostname resolution: {e}")
                # # Method 4: Try to get IP from network interfaces
                # if not self.ip or self.ip.startswith('127.'):
                #     try:
                #         import subprocess
                #         result = subprocess.run(['ip', 'route', 'get', '8.8.8.8'], 
                #                               capture_output=True, text=True, timeout=5)
                #         if result.returncode == 0:
                #             lines = result.stdout.strip().split('\n')
                #             for line in lines:
                #                 if 'src' in line:
                #                     parts = line.split()
                #                     src_index = parts.index('src')
                #                     if src_index + 1 < len(parts):
                #                         potential_ip = parts[src_index + 1]
                #                         if not potential_ip.startswith('127.') and potential_ip.startswith('192.168.1.'):
                #                             self.ip = potential_ip
                #                             self.logger.info(f"Selected eth0 IP from ip route: {self.ip}")
                #                             break
                #                         else:
                #                             self.logger.warning(f"ip route returned non-eth0 IP: {potential_ip}")
                    # except Exception as e:
                        # pass
                # If still no valid IP, wait and retry indefinitely
                if not self.ip or self.ip.startswith('127.'):
                    self.logger.warning(f"No valid eth0 IP found yet (current: {self.ip}). Waiting for DHCP... (attempt {attempt})")
                    time.sleep(2)
                else:
                    break

    def cleanup(self):
        """Clean up the zeroconf service"""
        # Clean up zeroconf
        # destroy the service browser
        if self.service_browser:
            try:
                self.service_browser.cancel()
                self.logger.info("Service browser cancelled")
            except Exception as e:
                self.logger.error(f"Error canceling service browser: {e}")
            self.service_browser = None
        # unregister the service
        if self.zeroconf:
            try:
                self.zeroconf.unregister_service(self.service_info) # unregister the service
                time.sleep(1)
                self.zeroconf.close()
                self.logger.info("Zeroconf service unregistered and closed")
            except Exception as e:
                self.logger.error(f"Error unregistering service: {e}")
            self.zeroconf = None
        
        self.service_registered = False
        self.logger.info("Service cleanup complete")
