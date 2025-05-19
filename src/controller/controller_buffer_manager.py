#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Buffer Manager

Handles module data buffering and management, including:
- Storage of incoming module data
- Buffer size monitoring
- Data retrieval operations
- Data pruning/cleanup
"""

import time
import logging
from typing import Dict, List, Any, Optional

class ControllerBufferManager:
    def __init__(self, logger: logging.Logger, max_buffer_size: int = 500):
        """Initialize the buffer manager"""
        self.logger = logger
        self.max_buffer_size = max_buffer_size
        
        # Module data storage
        self.module_data = {}
        
    def add_data(self, module_id: str, data: Any) -> bool:
        """
        Add data to the buffer for a specific module
        
        Args:
            module_id: ID of the module
            data: Raw data from the module
            
        Returns:
            bool: True if successful, False if buffer is full
        """
        timestamp = time.time()
        
        # Initialize buffer for this module if it doesn't exist
        if module_id not in self.module_data:
            self.module_data[module_id] = []
            
        # Append data to buffer
        self.module_data[module_id].append({
            "timestamp": timestamp,
            "data": data,
        })
        
        # Check if buffer is getting too large
        return len(self.module_data[module_id]) <= self.max_buffer_size
    
    def get_buffer_size(self, module_id: Optional[str] = None) -> int:
        """
        Get the current buffer size for a module or total size
        
        Args:
            module_id: Optional ID to check specific module buffer size
            
        Returns:
            int: Buffer size (entry count)
        """
        if module_id:
            return len(self.module_data.get(module_id, []))
        
        # If no module_id, return total entries across all modules
        return sum(len(data) for data in self.module_data.values())
    
    def get_module_data(self, module_id: Optional[str] = None) -> Dict:
        """
        Get data for a specific module or all modules
        
        Args:
            module_id: Optional ID to get data for specific module
            
        Returns:
            Dict: Module data dictionary
        """
        if module_id:
            return {module_id: self.module_data.get(module_id, [])}
        
        return self.module_data
        
    def clear_module_data(self, module_id: Optional[str] = None) -> bool:
        """
        Clear data for a specific module or all modules
        
        Args:
            module_id: Optional ID to clear specific module data
            
        Returns:
            bool: True if successful
        """
        try:
            if module_id:
                if module_id in self.module_data:
                    self.module_data[module_id].clear()
                    self.logger.debug(f"Cleared data for module {module_id}")
            else:
                for mid in self.module_data:
                    self.module_data[mid].clear()
                self.logger.debug("Cleared all module data")
            return True
        except Exception as e:
            self.logger.error(f"Error clearing module data: {e}")
            return False
    
    def is_buffer_full(self, module_id: str) -> bool:
        """
        Check if buffer for a module is full
        
        Args:
            module_id: ID of the module to check
            
        Returns:
            bool: True if buffer is full
        """
        if module_id not in self.module_data:
            return False
            
        return len(self.module_data[module_id]) >= self.max_buffer_size 