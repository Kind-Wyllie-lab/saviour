#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Controller Buffer Manager

Basic tests for the controller buffer manager
"""

import logging
import sys
import os
import time

# Add the parent directory to the path so we can import src modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controller.controller_buffer_manager import ControllerBufferManager

def test_basic_buffer_functionality():
    """Test basic buffer initialization and data operations"""
    # Setup
    logger = logging.getLogger()
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    
    # Initialize buffer manager
    buffer_mgr = ControllerBufferManager(logger, max_buffer_size=5)
    
    # Test adding data
    buffer_mgr.add_data("test_module_1", "sample data 1")
    buffer_mgr.add_data("test_module_1", "sample data 2")
    buffer_mgr.add_data("test_module_2", "other module data")
    
    # Check buffer size
    assert buffer_mgr.get_buffer_size() == 3
    assert buffer_mgr.get_buffer_size("test_module_1") == 2
    assert buffer_mgr.get_buffer_size("test_module_2") == 1
    
    # Check data retrieval
    data = buffer_mgr.get_module_data()
    assert len(data) == 2  # Two different modules
    assert len(data["test_module_1"]) == 2  # Two entries for module 1
    
    # Test buffer limits
    for i in range(5):
        buffer_mgr.add_data("test_module_3", f"data {i}")
    
    # Adding one more should return False (buffer full)
    assert not buffer_mgr.add_data("test_module_3", "overflow data")
    assert buffer_mgr.is_buffer_full("test_module_3")
    
    # Clear specific module data
    buffer_mgr.clear_module_data("test_module_1")
    assert buffer_mgr.get_buffer_size("test_module_1") == 0
    assert buffer_mgr.get_buffer_size() == 7  # Removed 2 entries
    
    # Clear all data
    buffer_mgr.clear_module_data()
    assert buffer_mgr.get_buffer_size() == 0
    
    print("ControllerBufferManager basic tests passed!")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_basic_buffer_functionality() 