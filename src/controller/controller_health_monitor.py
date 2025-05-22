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
"""

import time
import threading
import logging
from collections import deque
from typing import Dict, Any, Optional, List

class ControllerHealthMonitor:
    def __init__(self, logger: logging.Logger, heartbeat_interval: int = 30, heartbeat_timeout: int = 90, history_size: int = 100):
        """Initialize the health monitor
        
        Args:
            logger: Logger instance
            heartbeat_interval: Interval between health checks in seconds
            heartbeat_timeout: Time in seconds before marking a module as offline
            history_size: Number of historical health records to keep per module
        """
        self.logger = logger
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.history_size = history_size
        
        # Health data storage
        self.module_health = {}  # Current health data
        self.module_health_history = {}  # Historical health data
        
        # Control flags
        self.is_monitoring = False
        self.monitor_thread = None
    
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
            self.module_health[module_id] = {
                'last_heartbeat': status_data['timestamp'],
                'status': 'online',
                'cpu_temp': status_data.get('cpu_temp', 0),
                'cpu_usage': status_data.get('cpu_usage', 0),
                'memory_usage': status_data.get('memory_usage', 0),
                'uptime': status_data.get('uptime', 0),
                'disk_space': status_data.get('disk_space', 0),
                'ptp_offset': status_data.get('ptp_offset'),
                'ptp_freq': status_data.get('ptp_freq')
            }
            
            self.logger.info(f"(HEALTH MONITOR) Module {module_id} health updated: {self.module_health[module_id]}")
            return True
            
        except Exception as e:
            self.logger.error(f"(HEALTH MONITOR) Error updating health for module {module_id}: {e}")
            return False
    
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
    
    def get_module_health_stats(self, module_id: str, metric: str, window: int = 3600) -> Dict[str, float]:
        """
        Get statistics for a specific health metric over a time window
        
        Args:
            module_id: ID of the module
            metric: Health metric to analyze (e.g., 'cpu_usage', 'ptp_offset')
            window: Time window in seconds to analyze
            
        Returns:
            Dictionary containing min, max, avg, and latest values
        """
        if module_id not in self.module_health_history:
            return {}
        
        current_time = time.time()
        relevant_records = [
            record for record in self.module_health_history[module_id]
            if current_time - record['timestamp'] <= window and metric in record
        ]
        
        if not relevant_records:
            return {}
        
        values = [record[metric] for record in relevant_records]
        return {
            'min': min(values),
            'max': max(values),
            'avg': sum(values) / len(values),
            'latest': values[-1],
            'samples': len(values)
        }
    
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
    
    def is_module_online(self, module_id: str) -> bool:
        """
        Check if a specific module is online
        
        Args:
            module_id: ID of the module to check
            
        Returns:
            bool: True if module is online
        """
        if module_id not in self.module_health:
            return False
        
        current_time = time.time()
        last_heartbeat = self.module_health[module_id]['timestamp']
        return (current_time - last_heartbeat) <= self.heartbeat_timeout
    
    def get_offline_modules(self) -> list:
        """
        Get list of modules that are currently offline
        
        Returns:
            List of module IDs that are offline
        """
        offline_modules = []
        current_time = time.time()
        
        for module_id, health in self.module_health.items():
            if (current_time - health['timestamp']) > self.heartbeat_timeout:
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
            if (current_time - health['timestamp']) <= self.heartbeat_timeout:
                online_modules.append(module_id)
        
        return online_modules
    
    def monitor_health(self):
        """Monitor the health of all modules (runs in separate thread)"""
        while self.is_monitoring:
            current_time = time.time()
            
            for module_id in list(self.module_health.keys()):
                last_heartbeat = self.module_health[module_id]['timestamp']
                
                if (current_time - last_heartbeat) > self.heartbeat_timeout:
                    if self.module_health[module_id]['status'] == 'online':
                        self.logger.warning(
                            f"Module {module_id} has not sent a heartbeat in the last "
                            f"{self.heartbeat_timeout} seconds. Marking as offline."
                        )
                        self.module_health[module_id]['status'] = 'offline'
                else:
                    # Module is responsive, ensure it's marked as online
                    if self.module_health[module_id]['status'] == 'offline':
                        self.logger.info(f"Module {module_id} is back online")
                        self.module_health[module_id]['status'] = 'online'
            
            time.sleep(self.heartbeat_interval)
    
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
        self.logger.info("(HEALTH MONITOR) Stopped health monitoring")
    
    def clear_all_health(self):
        """Clear all health data"""
        self.module_health.clear()
        self.module_health_history.clear()
        self.logger.info("(HEALTH MONITOR) Cleared all health data")
    
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
            metrics = ['cpu_usage', 'memory_usage', 'cpu_temp', 'ptp_offset', 'ptp_freq']
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