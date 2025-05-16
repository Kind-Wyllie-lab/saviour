#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Health Manager

This manager is responsible for monitoring module system resources and reporting status to the controller.

Author: Andrew SG
Created: 16/05/2025         
License: GPLv3
"""

import psutil
import time
import logging
from typing import Callable, Dict, Any, Optional
import threading

class ModuleHealthManager:
    """
    This class is responsible for monitoring module system resources and reporting status to the controller.
    """
    def __init__(self, logger: logging.Logger, 
                 config_manager=None,
                 communication_manager=None):
        
        # Imported managers from module.py
        self.logger = logger
        self.config_manager = config_manager
        self.communication_manager = communication_manager

        # Heartbeat parameters
        self.heartbeat_interval = self.config_manager.get("module.heartbeat_interval", 30)
        self.heartbeats_active = False
    
    def start_heartbeats(self, heartbeat_callback: Callable[[], Dict[str, Any]], interval: float = 1.0) -> bool:
        """Start sending periodic heartbeats to the controller
        
        Args:
            heartbeat_callback: Function that returns the heartbeat data to send
            interval: Time between heartbeats in seconds
            
        Returns:
            bool: True if heartbeats were started successfully
        """
        if self.heartbeats_active:
            self.logger.info("Heartbeats already active")
            return False
            
        if not self.controller_ip:
            self.logger.error("Cannot start heartbeats: not connected to controller")
            return False
            
        self.heartbeats_active = True
        self.heartbeat_interval = interval
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, 
            args=(heartbeat_callback,),
            daemon=True
        )
        self.heartbeat_thread.start()
        return True

    def _heartbeat_loop(self, heartbeat_callback: Callable[[], Dict[str, Any]]):
        """Internal method: Loop sending heartbeats until stopped
        
        Args:
            heartbeat_callback: Function that returns the heartbeat data to send
        """
        self.logger.info("Heartbeat thread started")
        while self.heartbeats_active:
            try:
                self.logger.info("Sending heartbeat")
                status = heartbeat_callback()
                self.send_status(status)
            except Exception as e:
                self.logger.error(f"Error sending heartbeat: {e}")
            time.sleep(self.heartbeat_interval)

    def stop_heartbeats(self):
        """Stop sending heartbeats"""
        self.heartbeats_active = False
        self.logger.info("Heartbeat flag set to false")
        # No need to join the thread - it will exit by itself
    
    def cleanup(self): # TODO: is this redundant with the stop_heartbeats method?
        """Clean up resources"""
        self.heartbeats_active = False
        self.logger.info("Heartbeat flag set to false")
        # No need to join the thread - it will exit by itself
        
