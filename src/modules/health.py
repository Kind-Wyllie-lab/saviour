#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Health Manager

This class is responsible for monitoring module system resources and reporting status to the controller.

Author: Andrew SG
Created: 16/05/2025         
License: GPLv3
"""

import psutil
import time
import logging
from typing import Callable, Dict, Any, Optional
import threading
import os

class Health:
    """
    This class is responsible for monitoring module system resources and reporting status to the controller.
    """
    def __init__(self, logger: logging.Logger, 
                 config_manager=None,
                 start_time=None):
        
        # Imported managers from module.py
        self.logger = logger
        self.config_manager = config_manager
        if start_time is None:
            self.start_time = time.time()
        else:
            self.start_time = start_time

        # Heartbeat parameters
        self.heartbeat_interval = self.config_manager.get("module.heartbeat_interval", 30)
        self.heartbeats_active = False
    
    def start_heartbeats(self) -> bool:
        """Start sending periodic heartbeats to the controller
        
        Args:
            heartbeat_callback: Function that returns the heartbeat data to send
            interval: Time between heartbeats in seconds
            
        Returns:
            bool: True if heartbeats were started successfully
        """
        if self.heartbeats_active:
            self.logger.info("(HEALTH MANAGER) Heartbeats already active")
            return False
            
        if not self.callbacks["get_controller_ip"]:
            self.logger.error("(HEALTH MANAGER) Cannot start heartbeats: not connected to controller")
            return False
            
        self.heartbeats_active = True
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, 
            daemon=True
        )
        self.heartbeat_thread.start()
        return True

    def _heartbeat_loop(self):
        """Internal method: Loop sending heartbeats until stopped"""
        self.logger.info("(HEALTH MANAGER) Heartbeat thread started")
        last_heartbeat_time = 0
        check_interval = 0.1  # Check for stop flag every 10ms
        
        while self.heartbeats_active:
            current_time = time.time()
            # Check if it's time to send a heartbeat
            if current_time - last_heartbeat_time >= self.heartbeat_interval:
                try:
                    # Check if communication manager is still valid
                    if not self.callbacks["get_controller_ip"]:
                        self.logger.warning("(HEALTH MANAGER) Controller IP not available, stopping heartbeats")
                        self.heartbeats_active = False
                        break
                        
                    self.logger.info("(HEALTH MANAGER) Sending heartbeat")
                    status = self.get_health()
                    status['type'] = 'heartbeat' # Add type field to identify heartbeat status
                    self.callbacks["send_status"](status)
                    last_heartbeat_time = current_time
                except Exception as e:
                    self.logger.error(f"(HEALTH MANAGER) Error sending heartbeat: {e}")
                    # If we get an error sending the heartbeat, stop the heartbeats
                    self.heartbeats_active = False
                    break
            
            # Sleep for a short interval rather than the full heartbeat interval - this allows for quicker response to stop requests
            time.sleep(check_interval)
    
    def get_health(self):
        """Get health metrics for the module"""
        ptp_status = self.callbacks["get_ptp_status"]()
        return {
            "timestamp": time.time(),
            'cpu_temp': self.get_cpu_temp(),
            'cpu_usage': psutil.cpu_percent(),
            'memory_usage': psutil.virtual_memory().percent,
            'uptime': time.time() - self.start_time if self.start_time else 0,
            'disk_space': psutil.disk_usage('/').percent, # Free disk space
            'ptp4l_offset': ptp_status.get('ptp4l_offset'),
            'ptp4l_freq': ptp_status.get('ptp4l_freq'),
            'phc2sys_offset': ptp_status.get('phc2sys_offset'),
            'phc2sys_freq': ptp_status.get('phc2sys_freq'),
            'recording': self.callbacks["get_recording_status"](),
            'streaming': self.callbacks["get_streaming_status"]()
        }

    def get_cpu_temp(self):
        """Get CPU temperature"""
        try:
            temp = os.popen("vcgencmd measure_temp").readline()
            return float(temp.replace("temp=","").replace("'C\n",""))
        except:
            return None            

    def stop_heartbeats(self):
        """Stop sending heartbeats"""
        self.heartbeats_active = False
        self.logger.info("(HEALTH MANAGER) Heartbeat flag set to false")
        
        # Ensure the thread has stopped
        if hasattr(self, 'heartbeat_thread') and self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.logger.info("(HEALTH MANAGER) Waiting for heartbeat thread to stop...")
            self.heartbeat_thread.join(timeout=1.0)
            if self.heartbeat_thread.is_alive():
                self.logger.warning("(HEALTH MANAGER) Heartbeat thread did not stop cleanly - continuing shutdown")
        
        self.logger.info("(HEALTH MANAGER) Heartbeat thread stopped")
    
    def cleanup(self): # TODO: is this redundant with the stop_heartbeats method?
        """Clean up resources"""
        self.stop_heartbeats()

    def set_callbacks(self, callbacks: Dict[str, Callable]):
        """
        Receive a universal set of callbacks from the module
        """
        self.callbacks = callbacks
        
