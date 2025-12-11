#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Database Manager

Handles all database interactions, currently using supabase.
#TODO: Switch to mariadb

Author: Andrew SG
Created: ?
"""

import os
import time
import threading
import logging
import supabase
from typing import Dict, List, Any, Optional

# Optional: For NWB format support
try:
    import pynwb
    from pynwb import NWBFile, NWBHDF5IO
    NWB_AVAILABLE = True
except ImportError:
    NWB_AVAILABLE = False
    logging.warning("PyNWB not available. NWB file export will be disabled.")

class Database:
    def __init__(self, config_manager=None):
        """
        Initialize the database manager
        
        Args:
            logger: Logger instance
            config_manager: Optional configuration manager
        """
        self.logger = logging.getLogger(__name__)
        self.config_manager = config_manager
        
        # Initialize database client internally
        SUPABASE_URL = os.getenv("SUPABASE_URL")
        SUPABASE_KEY = os.getenv("SUPABASE_KEY")
        
        # Try to get database credentials from config manager if available
        if self.config_manager:
            # These will be None if not found in config
            db_url = self.config_manager.get("database.url")
            db_key = self.config_manager.get("database.key")
            
            if db_url:
                SUPABASE_URL = db_url
            if db_key:
                SUPABASE_KEY = db_key
        
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables or configuration")
            
        self.db_client = supabase.create_client(SUPABASE_URL, SUPABASE_KEY)
        self.logger.info("(DATABASE MANAGER) Initialized database connection")
        
        # Export control flags
        self.is_exporting_data = False
        self.is_health_exporting = False
        
        # Export intervals from config or defaults
        if self.config_manager:
            self.export_interval = self.config_manager.get("data_export.export_interval", 10)
            self.health_export_interval = self.config_manager.get("data_export.health_export_interval", 10)
        else:
            self.export_interval = 10
            self.health_export_interval = 10
        
        # Thread references
        self.data_export_thread = None
        self.health_export_thread = None
    
    def export_module_data(self, module_data: Dict, service_manager) -> bool:
        """Export the local buffer to the database"""
        try:
            for module_id, data_list in module_data.items():
                if data_list:  # If there's data to upload
                    # Find the module type by searching through the modules list
                    module_type = next((module.type for module in service_manager.modules if module.id == module_id), 'unknown')
                    self.logger.debug(f"(DATABASE MANAGER) Preparing to upload for module: {module_id}, type: {module_type}")
                    self.logger.debug(f"(DATABASE MANAGER) Data to upload: {data_list}")

                    # Format data for upload    
                    records = [{
                        "module_id": module_id,
                        "module_type": module_type,
                        "timestamp": item['timestamp'],
                        "data": item['data']
                    } for item in data_list]
                    self.logger.debug(f"(DATABASE MANAGER) Records formatted for upload: {records}")

                    # Upload data to database
                    response = self.db_client.table("controller_test").insert(records).execute()
                    self.logger.debug(f"(DATABASE MANAGER) Database response: {response}")

                    # Clear uploaded data from buffer
                    data_list.clear()
            return True
            
        except Exception as e:
            self.logger.error(f"(DATABASE MANAGER) Failed to upload module data: {e}")
            return False
    
    def export_health_data(self, module_health: Dict) -> bool:
        """Export the local health data to the database"""
        self.logger.debug("(DATABASE MANAGER) Export health data function called")
        if not module_health:
            self.logger.debug("(DATABASE MANAGER) Export health data function saw that module_health is empty")
            self.logger.info("(DATABASE MANAGER) No health data to export")
            return True

        try:
            self.logger.debug("(DATABASE MANAGER) Export health data function saw that module_health is not empty")
            records = [
                {
                    "module_id": module_id,
                    "timestamp": health["last_heartbeat"],
                    "status": health["status"],
                    "cpu_temp": health["cpu_temp"],
                    "cpu_usage": health["cpu_usage"],
                    "memory_usage": health["memory_usage"],
                    "disk_space": health["disk_space"],
                    "uptime": health["uptime"]
                }
                for module_id, health in module_health.items()
            ]
        
            response = self.db_client.table("module_health").insert(records).execute()
            self.logger.debug(f"(DATABASE MANAGER) Uploaded {len(records)} health records to database")
            self.logger.debug(f"(DATABASE MANAGER) Database response: {response}")
            self.logger.debug("(DATABASE MANAGER) Export health data function completed successfully")
            return True

        except Exception as e:
            self.logger.debug("(DATABASE MANAGER) Export health data function saw that there was an error exporting health data")
            self.logger.error(f"(DATABASE MANAGER) Error exporting health data: {e}")
            return False
    
    def start_periodic_data_export(self, buffer_manager_ref, service_manager, export_interval: int = None):
        """
        Start periodic export of module data
        
        Args:
            buffer_manager_ref: Either the buffer manager instance or a dict with module data
            service_manager: Service manager instance for module lookup
            export_interval: Optional interval in seconds between exports
        """
        if export_interval:
            self.export_interval = export_interval
            
        # Store references for the thread
        self.buffer_manager_ref = buffer_manager_ref
        self.service_manager_ref = service_manager
            
        def periodic_export():
            """Periodically export the local buffer to the database"""
            self.logger.debug("(DATABASE MANAGER) Periodic export function called")
            while self.is_exporting_data:
                self.logger.debug("(DATABASE MANAGER) Periodic export function saw that is_exporting_data is true")
                try:
                    self.logger.info("(DATABASE MANAGER) Starting periodic export from buffer...")
                    # Check if we're passed a buffer manager or data dict
                    if hasattr(self.buffer_manager_ref, 'get_module_data'):
                        # It's a buffer manager instance
                        current_data = self.buffer_manager_ref.get_module_data()
                    else:
                        # It's a data dictionary directly
                        current_data = self.buffer_manager_ref
                        
                    self.export_module_data(current_data, self.service_manager_ref)
                    self.logger.info("(DATABASE MANAGER) Periodic export completed successfully")
                except Exception as e:
                    self.logger.error(f"(DATABASE MANAGER) Error during periodic export: {e}")
                time.sleep(self.export_interval)
        
        self.is_exporting_data = True
        self.data_export_thread = threading.Thread(target=periodic_export, daemon=True)
        self.data_export_thread.start()
        self.logger.info(f"(DATABASE MANAGER) Started periodic data export with interval {self.export_interval}s")
    
    def start_periodic_health_export(self, module_health: Dict, health_export_interval: int = None):
        """Start periodic export of health data"""
        if health_export_interval:
            self.health_export_interval = health_export_interval
            
        def periodic_health_export():
            """Periodically export the local health data to the database"""
            self.logger.debug("(DATABASE MANAGER) Periodic health export function called")
            while self.is_health_exporting:
                self.logger.debug("(DATABASE MANAGER) Periodic health export function saw is_health_exporting is true")
                try:
                    self.export_health_data(module_health)
                except Exception as e:
                    self.logger.error(f"(DATABASE MANAGER) Error exporting health data: {e}")
                time.sleep(self.health_export_interval)
        
        self.is_health_exporting = True
        self.health_export_thread = threading.Thread(target=periodic_health_export, daemon=True)
        self.health_export_thread.start()
        self.logger.info(f"(DATABASE MANAGER) Started periodic health export with interval {self.health_export_interval}s")
    
    def stop_periodic_data_export(self):
        """Stop periodic export of module data"""
        self.is_exporting_data = False
        self.logger.info("(DATABASE MANAGER) Stopped periodic data export")
    
    def stop_periodic_health_export(self):
        """Stop periodic export of health data"""
        self.is_health_exporting = False
        self.logger.info("(DATABASE MANAGER) Stopped periodic health export")
    
    def stop_all_exports(self):
        """Stop all periodic exports"""
        self.stop_periodic_data_export()
        self.stop_periodic_health_export()
        self.logger.info("(DATABASE MANAGER) Stopped all periodic exports")
    
    def get_export_status(self) -> Dict[str, bool]:
        """Get the current export status"""
        return {
            "data_exporting": self.is_exporting_data,
            "health_exporting": self.is_health_exporting
        }
    
    def cleanup(self):
        """Clean up the database manager"""
        self.stop_all_exports()
        self.logger.info("(DATABASE MANAGER) Cleaned up database manager")