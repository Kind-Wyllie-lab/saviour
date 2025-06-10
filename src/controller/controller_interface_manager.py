#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interface Manager

Handles user interaction with the habitat controller, including:
- Web interface
- CLI interface
- Command parsing and execution
"""

import logging
import time
from src.controller.controller_web_interface import WebInterfaceManager
from src.controller.controller_cli_interface import CLIInterface
import threading
from typing import Callable, List, Dict, Any
from src.controller.controller_service_manager import Module

class InterfaceManager:
    def __init__(self, logger, config_manager):
        """Initialize the controller interface"""
        self.logger = logger
        self.config_manager = config_manager
        self.zmq_commands = self.config_manager.get("controller.zmq_commands", [])
        self.send_command_callback = None
    
    def register_callbacks(self, get_modules=None, get_ptp_history=None, get_module_health=None, send_command=None):
        """Register callbacks for getting data from other managers"""
        self.get_modules_callback = get_modules
        self.get_ptp_history_callback = get_ptp_history
        self.get_module_health_callback = get_module_health
        self.send_command_callback = send_command

    def get_zmq_commands(self):
        """Get available ZMQ commands"""
        return self.zmq_commands

    def send_command(self, module_id: str, command: str) -> bool:
        """Send a command to a module
        
        Args:
            module_id: ID of the module to send command to
            command: Command string to send
            
        Returns:
            bool: True if command was sent successfully
        """
        if not self.send_command_callback:
            self.logger.error("(INTERFACE MANAGER) No send_command callback registered")
            return False
            
        try:
            self.logger.info(f"(INTERFACE MANAGER) Sending command '{command}' to module {module_id}")
            return self.send_command_callback(module_id, command)
        except Exception as e:
            self.logger.error(f"(INTERFACE MANAGER) Error sending command: {e}")
            return False

    def start(self):
        """Start the command handler"""
        self.logger.info(f"(INTERFACE MANAGER) Starting interface manager")
        # Refresh ZMQ commands from config
        self.zmq_commands = self.config_manager.get("controller.zmq_commands", [])
        self.logger.info(f"(INTERFACE MANAGER) Loaded {len(self.zmq_commands)} ZMQ commands")

    def list_modules(self):
        """List all discovered modules"""
        self.logger.info(f"(INTERFACE MANAGER) Listing modules")
        modules = self.get_modules()
        if not modules:
            self.logger.info(f"(INTERFACE MANAGER) No modules found")
            return
        for module in modules:
            self.logger.info(f"(INTERFACE MANAGER) Module: {module.id}, Type: {module.type}, IP: {module.ip}")
    
    def show_health_status(self):
        """Return health status of all modules"""
        self.logger.info(f"(INTERFACE MANAGER) Showing health status of all modules")
        module_health = self.get_module_health()
        if not module_health:
            self.logger.info(f"(INTERFACE MANAGER) No modules reporting health data")
            return
            
        for module_id, health in module_health.items():
            self.logger.info(f"(INTERFACE MANAGER) Module: {module_id}")
            self.logger.info(f"(INTERFACE MANAGER) Status: {health['status']}")
            self.logger.info(f"(INTERFACE MANAGER) CPU Usage: {health.get('cpu_usage', 'N/A')}%")
            self.logger.info(f"(INTERFACE MANAGER) Memory Usage: {health.get('memory_usage', 'N/A')}%")
            self.logger.info(f"(INTERFACE MANAGER) Temperature: {health.get('cpu_temp', 'N/A')}°C")
            self.logger.info(f"(INTERFACE MANAGER) Disk Space: {health.get('disk_space', 'N/A')}%")
            self.logger.info(f"(INTERFACE MANAGER) Uptime: {health.get('uptime', 'N/A')}s")
            self.logger.info(f"(INTERFACE MANAGER) Last Heartbeat: {time.strftime('%H:%M:%S', time.localtime(health['last_heartbeat']))}")


    def _on_module_discovered(self, module):
        """Callback when a new module is discovered"""
        self.logger.info(f"(INTERFACE MANAGER) Module discovered: {module.id}")
        if self.web_interface:
            self.logger.info(f"(INTERFACE MANAGER) Notifying web interface of module update")
            try:
                self.web_interface_manager.notify_module_update()
                self.logger.info(f"(INTERFACE MANAGER) Successfully notified web interface")
            except Exception as e:
                self.logger.error(f"(INTERFACE MANAGER) Error notifying web interface: {e}")
        else:
            self.logger.info(f"(INTERFACE MANAGER) Web interface disabled, skipping module update notification")
        
    def _on_module_removed(self, module):
        """Callback when a module is removed"""
        self.logger.info(f"(INTERFACE MANAGER) Module removed: {module.id}")
        if self.web_interface:
            self.logger.info(f"(INTERFACE MANAGER) Notifying web interface of module removal")
            try:
                self.web_interface_manager.notify_module_removal()
                self.logger.info(f"(INTERFACE MANAGER) Successfully notified web interface")
            except Exception as e:
                self.logger.error(f"(INTERFACE MANAGER) Error notifying web interface: {e}")
        else:
            self.logger.info(f"(INTERFACE MANAGER) Web interface disabled, skipping module removal notification")

    def _on_ptp_update(self):
        """Callback when PTP data is updated"""
        self.logger.info(f"(INTERFACE MANAGER) PTP data updated")
        if self.web_interface:
            self.logger.info(f"(INTERFACE MANAGER) Notifying web interface of PTP update")
            self.web_interface_manager.notify_ptp_update()
        else:
            self.logger.info(f"(INTERFACE MANAGER) Web interface disabled, skipping PTP update notification")

    def _get_modules(self):
        """Return module list"""
        modules = []
        for module in self.get_modules_callback():
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

    def _get_zmq_commands(self):
        """Callback to get ZMQ commands"""
        return self.get_zmq_commands_callback()

    def _get_ptp_history(self):
        """Callback to get PTP history"""
        return self.get_ptp_history_callback()

    def cleanup(self):
        """Clean up the command handler"""
        self.logger.info("(INTERFACE MANAGER) Cleaning up interface manager")

        # What is left to clean up?

        self.logger.info("(INTERFACE MANAGER) Interface manager cleaned up")