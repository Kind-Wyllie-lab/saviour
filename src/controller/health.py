#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Health Monitor

Handles health monitoring for all modules in the habitat system, including:
- Module health status tracking
- Heartbeat monitoring
- Online/offline status detection
- Health data processing
- Historical health data tracking

Author: Andrew SG
Created: ?
"""

import time
import threading
import logging
from collections import deque
from typing import Dict, Any, Optional, List

class Health:
    def __init__(self, config):
        """Initialize the health monitor
        
        Args:
            heartbeat_interval: Interval between health checks in seconds
            heartbeat_timeout: Time in seconds before marking a module as offline
        """

        self.logger = logging.getLogger(__name__)
        self.config = config
        self.heartbeat_interval = self.config.get("health.heartbeat_interval", 30)
        self.heartbeat_timeout = self.config.get("health.heartbeat_timeout", 90)
        self.monitor_interval = 30
        
        # Health data storage
        self.module_health = {}  # Current health data. module_id as primary key.
        self.module_health_history = {}  # Historical health data
        self.controller_health = {} # Historical controller health data.

        # Module online/offline states
        self.module_states = {}
        
        # Control flags
        self.is_monitoring = False
        self.monitor_thread = None
        
        # Callback for status changes
        self.logger.info(f"Initialised health monitor with heartbeat interval {self.heartbeat_interval}s, timeout {self.heartbeat_timeout}s.")


    """Modify module health records"""
    def remove_module(self, module_id: str):
        if module_id in self.module_health.keys():
            self.module_health.pop(module_id)
    

    def update_module_health(self, module_id: str, status_data: Dict[str, Any]) -> bool:
        """
        Update health data for a specific module
        
        Args:
            module_id: ID of the module
            status_data: Dictionary containing health metrics
            
        Returns:
            bool: True if update was successful
        """
        try:
            was_new_module = module_id not in self.module_health
            if was_new_module:
                # New module - create full health record
                self.module_health[module_id] = {
                    'timestamp': time.time(),  # Use controller's timestamp
                    'last_heartbeat': time.time(),  # Use controller's timestamp
                    'status': 'online',
                    'cpu_temp': status_data.get('cpu_temp', 0),
                    'cpu_usage': status_data.get('cpu_usage', 0),
                    'memory_usage': status_data.get('memory_usage', 0),
                    'uptime': status_data.get('uptime', 0),
                    'disk_space': status_data.get('disk_space', 0),
                    'ptp4l_offset': status_data.get('ptp4l_offset'),
                    'ptp4l_freq': status_data.get('ptp4l_freq'),
                    'phc2sys_offset': status_data.get('phc2sys_offset'),
                    'phc2sys_freq': status_data.get('phc2sys_freq'),
                    'last_ptp_restart': time.time(),
                    'ptp_restarts': 1
                }
            else:
                # Existing module - update heartbeat and status, preserve other fields
                self.module_health[module_id]['last_heartbeat'] = time.time()  # Use controller's timestamp
                self.module_health[module_id]['status'] = 'online'
                # Update other metrics if provided
                if 'cpu_temp' in status_data:
                    self.module_health[module_id]['cpu_temp'] = status_data['cpu_temp']
                if 'cpu_usage' in status_data:
                    self.module_health[module_id]['cpu_usage'] = status_data['cpu_usage']
                if 'memory_usage' in status_data:
                    self.module_health[module_id]['memory_usage'] = status_data['memory_usage']
                if 'uptime' in status_data:
                    self.module_health[module_id]['uptime'] = status_data['uptime']
                if 'disk_space' in status_data:
                    self.module_health[module_id]['disk_space'] = status_data['disk_space']
                if 'ptp4l_offset' in status_data:
                    self.module_health[module_id]['ptp4l_offset'] = status_data['ptp4l_offset']
                if 'ptp4l_freq' in status_data:
                    self.module_health[module_id]['ptp4l_freq'] = status_data['ptp4l_freq']
                if 'phc2sys_offset' in status_data:
                    self.module_health[module_id]['phc2sys_offset'] = status_data['phc2sys_offset']
                if 'phc2sys_freq' in status_data:
                    self.module_health[module_id]['phc2sys_freq'] = status_data['phc2sys_freq']
                if "last_ptp_restart" not in self.module_health[module_id]:
                    self.module_health[module_id]["last_ptp_restart"] = time.time()
                if "ptp_restarts" not in self.module_health[module_id]:
                    self.module_health[module_id]["ptp_restarts"] = 1
            
            if was_new_module:
                self.logger.info(f"New module {module_id} added to health tracking")

            self.facade.on_status_change(module_id, self.module_health[module_id]['status'])
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating health for module {module_id}: {e}")
            return False


    def module_discovery(self, discovered_modules: dict):
        """Receive discovered modules from network manager
        Ensure health tracking is aware of all modules
        """
        self.logger.info(f"Received discovered modules from Network: {discovered_modules}")
        for module in discovered_modules:
            if module.id not in self.module_health:
                self.logger.info(f"Discovered new module {module.id}, adding to health tracking")
                self.module_health[module.id] = {
                    'timestamp': time.time(),
                    'last_heartbeat': 0,  # No heartbeat yet
                    'status': 'offline',  # Start as offline until first heartbeat
                    'cpu_temp': None,
                    'cpu_usage': None,
                    'memory_usage': None,
                    'uptime': None,
                    'disk_space': None,
                    'ptp4l_offset': None,
                    'ptp4l_freq': None,
                    'phc2sys_offset': None,
                    'phc2sys_freq': None
                }
    

    def module_id_changed(self, old_module_id, new_module_id):
        # Move the module data to the new key
        self.module_health[new_module_id] = self.module_health.pop(old_module_id)
        if old_module_id in self.module_health_history:
            self.module_health_history[new_module_id] = self.module_health_history.pop(old_module_id)


    """Get methods"""
    def get_module_health_history(self, module_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get historical health data for a specific module
        
        Args:
            module_id: ID of the module
            limit: Optional limit on number of historical records to return
            
        Returns:
            List of historical health records, most recent first
        """
        if module_id not in self.module_health_history:
            return []
        
        history = list(self.module_health_history[module_id])
        if limit:
            history = history[-limit:]
        return history
    

    def get_module_health(self, module_id: Optional[str] = None) -> Dict:
        """
        Get health data for a specific module or all modules
        
        Args:
            module_id: Specific module ID, or None for all modules
            
        Returns:
            Dictionary containing health data
        """
        if module_id:
            return self.module_health.get(module_id, {})
        return self.module_health.copy()
    
    
    def get_offline_modules(self) -> list:
        """
        Get list of modules that are currently offline
        
        Returns:
            List of module IDs that are offline
        """
        offline_modules = []
        current_time = time.time()
        
        for module_id, health in self.module_health.items():
            if self.module_health[module_id]["status"] == "offline": 
            # if (current_time - health['timestamp']) > self.heartbeat_timeout:
                offline_modules.append(module_id)
        
        return offline_modules
    
    def get_online_modules(self) -> list:
        """
        Get list of modules that are currently online
        
        Returns:
            List of module IDs that are online
        """
        online_modules = []
        current_time = time.time()
        
        for module_id, health in self.module_health.items():
            if self.module_health[module_id]["status"] == "online":
            # if (current_time - health['timestamp']) <= self.heartbeat_timeout: # optionally do another heartbeat check here just inacse monitor loop hasn't caught it yet
                online_modules.append(module_id) # It's online
        
        return online_modules
    

    def get_health_summary(self) -> Dict[str, Any]:
        """
        Get a summary of overall system health
        
        Returns:
            Dictionary with health statistics
        """
        online_modules = self.get_online_modules()
        offline_modules = self.get_offline_modules()
        
        # Calculate average health metrics across all online modules
        avg_metrics = {}
        if online_modules:
            metrics = ['cpu_usage', 'memory_usage', 'cpu_temp', 'ptp4l_offset', 'ptp4l_freq']
            for metric in metrics:
                values = []
                for module_id in online_modules:
                    if module_id in self.module_health and metric in self.module_health[module_id]:
                        values.append(self.module_health[module_id][metric])
                if values:
                    avg_metrics[f'avg_{metric}'] = sum(values) / len(values)
        
        return {
            'total_modules': len(self.module_health),
            'online_modules': len(online_modules),
            'offline_modules': len(offline_modules),
            'online_module_ids': online_modules,
            'offline_module_ids': offline_modules,
            'average_metrics': avg_metrics
        }

    
    def get_ptp_sync(self) -> int:
        max_ptp_sync = 0
        for module_id in self.module_health:
            ptp_sync = self.module_health[module_id]["ptp4l_offset"]
            if not ptp_sync: 
                return None
            if abs(ptp_sync) > max_ptp_sync:
                max_ptp_sync = abs(ptp_sync)
        return int(max_ptp_sync)


    """Health Methods"""
    def monitor_health(self):
        """Monitor the health of all modules (runs in separate thread)"""
        self.logger.info("Checking for offline modules via monitor_health()")
        cycle_count = 0
        while self.is_monitoring:
            current_time = time.time() # Get current time
            cycle_count += 1
            
            # Log every 10 cycles (50 seconds with 5s interval) to show the thread is alive
            if cycle_count % 10 == 0:
                self.logger.info(f"Monitor cycle {cycle_count}: monitoring {len(self.module_health)} modules")
            
            self.logger.info(f"Online modules: {self.get_online_modules()}, offline modules: {self.get_offline_modules()}")
            # self.logger.info(f"Module health: {self.module_health}")
            for module_id in list(self.module_health.keys()): # We will go through each module in the current module_health dict
                last_heartbeat = self.module_health[module_id]['last_heartbeat'] # Get the time of the last heartbeat
                time_diff = current_time - last_heartbeat
                 
                # Find offline modules
                if time_diff > self.heartbeat_timeout: # If heartbeat not received within timeout period
                    if self.module_health[module_id]['status'] == 'online': # If module is currently marked as online
                        self.logger.warning(
                            f"Module {module_id} has not sent a heartbeat in the last "
                            f"{time_diff:.2f} seconds (timeout: {self.heartbeat_timeout}s). Marking as offline."
                        )
                        self.module_health[module_id]['status'] = 'offline'
                        # Trigger callback for offline status
                        try:
                            self.facade.on_status_change(module_id, 'offline')
                            self.facade.module_offline(module_id)
                        except Exception as e:
                            self.logger.error(f"Error in status change callback: {e}")
                else:
                    # Module is responsive, ensure it's marked as online
                    if self.module_health[module_id]['status'] == 'offline':
                        self.logger.info(f"Module {module_id} is back online")
                        self.module_health[module_id]['status'] = 'online'
                        # Trigger callback for online status
                        try:
                            self.facade.on_status_change(module_id, 'online')
                        except Exception as e:
                            self.logger.error(f"Error in status change callback: {e}")
                
                self.facade.on_status_change(module_id, self.module_health[module_id]['status'])
            
            # Check PTP health periodically
            if cycle_count % 2 == 0:  # Check PTP health every couple cycles 
                self._check_ptp_health()
            
            time.sleep(self.monitor_interval)


    def _check_ptp_health(self):
        """
        Check received PTP stats and reset PTP if necessary
        """
        reset_flag = False
        for module in self.module_health:
            # TODO: Consider putting all ptp params in a nested dict here that we could loop through e.g. for param in self.module_health[module]["ptp"]:
            if self.module_health[module]["ptp4l_freq"] is not None:
                if abs(self.module_health[module]["ptp4l_freq"]) > 100000:
                    self.logger.warning(f"ptp4l_freq offset too high for module {module}: {self.module_health[module]['ptp4l_freq']}")           
                    reset_flag = True
            if self.module_health[module]["phc2sys_freq"] is not None:
                if abs(self.module_health[module]["phc2sys_freq"]) > 100000:
                    self.logger.warning(f"phc2sys_freq offset too high for module {module}: {self.module_health[module]['phc2sys_freq']}")             
                    reset_flag = True
            if self.module_health[module]["ptp4l_offset"] is not None:
                if abs(self.module_health[module]["ptp4l_offset"]) > 10000:
                    self.logger.warning(f"ptp4l_offset too high for module {module}: {self.module_health[module]['ptp4l_offset']}")             
                    reset_flag = True
            if self.module_health[module]["phc2sys_offset"] is not None:
                if abs(self.module_health[module]["phc2sys_offset"]) > 10000:
                    self.logger.warning(f"phc2sys_offset too high for module {module}: {self.module_health[module]['phc2sys_offset']}")         
                    reset_flag = True
            if reset_flag == True:
                if (time.time() - self.module_health[module]["last_ptp_restart"]) > (2**self.module_health[module]["ptp_restarts"]) * 60: # Exponential backoff? Sort of.
                    self.logger.info(f"Telling {module} to restart_ptp")
                    self.module_health[module]["last_ptp_restart"] = time.time()
                    self.module_health[module]["ptp_restarts"] += 1
                    if self.module_health[module]["ptp_restarts"] >= 5:
                        self.module_health[module]["ptp_restarts"] = 5
                    self.facade.send_command(module, "restart_ptp", {})


    def start_monitoring(self):
        """Start the health monitoring thread"""
        if self.is_monitoring:
            self.logger.warning("Health monitoring is already running")
            return
        
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(target=self.monitor_health, daemon=True)
        self.monitor_thread.start()
        self.logger.info(f"Started health monitoring with {self.heartbeat_interval}s interval")
    

    def stop_monitoring(self):
        """Stop the health monitoring thread"""
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        self.logger.info("Stopped health monitoring")
    

    def clear_all_health(self):
        """Clear all health data"""
        self.module_health.clear()
        self.module_health_history.clear()
        self.logger.info("Cleared all health data")


    def mark_module_offline(self, module_id: str, reason: str = "Communication test failed"):
        """Mark a module as offline due to communication failure
        
        Args:
            module_id: ID of the module to mark offline
            reason: Reason for marking the module offline
        """
        if module_id in self.module_health:
            if self.module_health[module_id]['status'] == 'online':
                self.logger.warning(f"Module {module_id} marked offline: {reason}")
                self.module_health[module_id]['status'] = 'offline'
                
                # Trigger callback for offline status
                try:
                    self.facade.on_status_change(module_id, 'offline')
                except Exception as e:
                    self.logger.error(f"Error in status change callback: {e}")
            else:
                self.logger.info(f"Module {module_id} already offline: {reason}")
        else:
            self.logger.warning(f"Attempted to mark unknown module {module_id} as offline: {reason}")
    
    
    def handle_communication_test_response(self, module_id: str, success: bool):
        """Handle communication test response from a module
        
        Args:
            module_id: ID of the module that responded
            success: Whether the communication test was successful
        """
        if module_id in self.module_health:
            if success:
                # Communication test successful - ensure module is marked online
                if self.module_health[module_id]['status'] == 'offline':
                    self.logger.info(f"Module {module_id} communication test successful - marking online")
                    self.module_health[module_id]['status'] = 'online'
                    # Trigger callback for online status
                    try:
                        self.facade.on_status_change(module_id, 'online')
                    except Exception as e:
                        self.logger.error(f"Error in status change callback: {e}")
                else:
                    self.logger.info(f"Module {module_id} communication test successful - already online")
            else:
                # Communication test failed - mark module as offline
                self.mark_module_offline(module_id, "Communication test failed")
        else:
            self.logger.warning(f"Communication test response from unknown module {module_id}")