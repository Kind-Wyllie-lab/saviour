#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for the Config

Run with: python -m pytest tests/test_module_config.py
"""

import os
import json
import logging
import pytest
import tempfile
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from habitat.src.modules.config import Config

# Setup logging for tests
logger = logging.getLogger("test")
logger.setLevel(logging.INFO)
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# Test default configuration values
def test_default_config():
    """Test that default configuration is loaded correctly"""
    config = Config(logger, "test")
    
    # Check some default values
    assert config.get("module.heartbeat_interval") == 30
    assert config.get("module.samplerate") == 200
    assert config.get("service.port") == 5000
    assert config.get("module.type") == "test"
    
    # Check that non-existent keys return None
    assert config.get("non.existent.key") is None
    
    # Check that default values are provided for non-existent keys
    assert config.get("non.existent.key", "default") == "default"

# Test config file loading
def test_config_file_loading():
    """Test loading configuration from a file"""
    # Create a temporary config file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
        config_data = {
            "module": {
                "heartbeat_interval": 60,
                "custom_setting": "test_value"
            },
            "service": {
                "port": 6000
            }
        }
        json.dump(config_data, temp_file)
        temp_file_path = temp_file.name
    
    try:
        # Create config manager with this file
        config = Config(logger, "test", temp_file_path)
        
        # Check values from the file override defaults
        assert config.get("module.heartbeat_interval") == 60
        assert config.get("service.port") == 6000
        
        # Check new values are added
        assert config.get("module.custom_setting") == "test_value"
        
        # Check other defaults are still available
        assert config.get("service.service_type") == "_module._tcp.local."
    finally:
        # Clean up the temporary file
        os.unlink(temp_file_path)

# Test setting and getting values
def test_set_get_values():
    """Test setting and getting configuration values"""
    config = Config(logger, "test")
    
    # Test setting a simple value
    config.set("test.value", 42)
    assert config.get("test.value") == 42
    
    # Test setting a nested value
    config.set("test.nested.value", "nested")
    assert config.get("test.nested.value") == "nested"
    
    # Test updating an existing value
    config.set("module.heartbeat_interval", 15)
    assert config.get("module.heartbeat_interval") == 15

# Test saving configuration
def test_save_config():
    """Test saving configuration to a file"""
    # Create a temporary directory for the test
    with tempfile.TemporaryDirectory() as temp_dir:
        config_file_path = os.path.join(temp_dir, "test_config.json")
        
        # Create config manager with this file path
        config = Config(logger, "test", config_file_path)
        
        # Set some values
        config.set("module.custom_setting", "test_value")
        config.set("test.nested.value", 42)
        
        # Save the config
        assert config.save_config() == True
        
        # Check that the file exists
        assert os.path.exists(config_file_path)
        
        # Load the file directly and check values
        with open(config_file_path, 'r') as f:
            loaded_config = json.load(f)
        
        assert loaded_config["module"]["custom_setting"] == "test_value"
        assert loaded_config["test"]["nested"]["value"] == 42

# Test environment variable overrides
def test_env_variables(monkeypatch):
    """Test that environment variables override defaults"""
    # Set environment variables
    monkeypatch.setenv("MODULE_HEARTBEAT_INTERVAL", "45")
    monkeypatch.setenv("MODULE_PORT", "7000")
    
    # Create config manager
    config = Config(logger, "test")
    
    # Check that env vars override defaults
    assert config.get("module.heartbeat_interval") == 45  # Should be converted to int
    assert config.get("service.port") == 7000

# Test validation
def test_validation():
    """Test configuration validation"""
    config = Config(logger, "test")
    
    # Should be valid with defaults
    assert config.validate() == True
    
    # Make it invalid by setting negative heartbeat interval
    config.set("module.heartbeat_interval", -1)
    assert config.validate() == False
    
    # Fix it and check again
    config.set("module.heartbeat_interval", 30)
    assert config.validate() == True 