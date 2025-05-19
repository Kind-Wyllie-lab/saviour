#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Config Manager

Centralizes configuration management for the habitat controller system, including:
- Loading configuration from environment variables, config files, and defaults
- Providing a consistent interface for accessing and modifying configuration values
- Validating configuration values
- Saving configuration changes
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Union

class ControllerConfigManager:
    """Manages configuration for the habitat controller system"""
    
    # Default configuration values
    DEFAULT_CONFIG = {
        # Controller parameters
        "controller": {
            "max_buffer_size": 1000,
            "manual_control": True,
            "print_received_data": False,
            "commands": ["get_status", "get_data", "start_stream", "stop_stream", "record_video"],
        },
        
        # Service parameters
        "service": {
            "port": 5000,
            "service_type": "_controller._tcp.local.",
            "service_name": "controller._controller._tcp.local.",
        },
        
        # Health monitoring parameters
        "health_monitor": {
            "heartbeat_interval": 30,
            "heartbeat_timeout": 90,
        },
        
        # Data export parameters
        "data_export": {
            "export_interval": 10,
            "health_export_interval": 10,
        },
        
        # Logging parameters
        "logging": {
            "level": "INFO",
            "format": "%(asctime)s - %(levelname)s - %(message)s",
        },
    }
    
    # Configuration that should be loaded from environment variables
    ENV_CONFIG_MAPPING = {
        "SUPABASE_URL": "database.url",
        "SUPABASE_KEY": "database.key",
        "CONTROLLER_PORT": "service.port",
        "CONTROLLER_MAX_BUFFER_SIZE": "controller.max_buffer_size",
        "CONTROLLER_MANUAL_CONTROL": "controller.manual_control",
        "CONTROLLER_LOG_LEVEL": "logging.level",
    }
    
    def __init__(self, logger: logging.Logger, config_file_path: Optional[str] = None):
        """
        Initialize the configuration manager
        
        Args:
            logger: Logger instance
            config_file_path: Path to configuration file (optional)
        """
        self.logger = logger
        self.config_file_path = config_file_path or os.path.join(os.path.dirname(__file__), "config.json")
        self.config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from default values, environment variables, and config file
        
        Returns:
            Dict containing merged configuration
        """
        # Start with default config
        config = self.DEFAULT_CONFIG.copy()
        
        # Override with environment variables
        for env_var, config_path in self.ENV_CONFIG_MAPPING.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                # Convert environment variable to appropriate type
                path_parts = config_path.split('.')
                
                # Navigate to the proper nested dictionary
                current = config
                for i, part in enumerate(path_parts[:-1]):
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                
                # Convert value to appropriate type based on default if present
                target_key = path_parts[-1]
                if target_key in current:
                    original_value = current[target_key]
                    if isinstance(original_value, bool):
                        env_value = env_value.lower() in ('true', 'yes', '1')
                    elif isinstance(original_value, int):
                        env_value = int(env_value)
                    elif isinstance(original_value, float):
                        env_value = float(env_value)
                
                # Set the value
                current[path_parts[-1]] = env_value
        
        # Override with config file if it exists
        if os.path.exists(self.config_file_path):
            try:
                with open(self.config_file_path, 'r') as f:
                    file_config = json.load(f)
                
                # Recursively merge file config into config
                self._merge_configs(config, file_config)
                self.logger.info(f"Loaded configuration from {self.config_file_path}")
            except Exception as e:
                self.logger.error(f"Error loading config file: {e}")
        
        return config
    
    def _merge_configs(self, target: Dict, source: Dict) -> None:
        """
        Recursively merge source config into target config
        
        Args:
            target: Target configuration dictionary
            source: Source configuration dictionary to merge from
        """
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._merge_configs(target[key], value)
            else:
                target[key] = value
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get a configuration value by its key path
        
        Args:
            key_path: Dot-separated path to the configuration value
            default: Default value to return if key doesn't exist
            
        Returns:
            Configuration value or default if not found
        """
        path_parts = key_path.split('.')
        
        # Navigate to the value
        current = self.config
        for part in path_parts:
            if part not in current:
                return default
            current = current[part]
        
        return current
    
    def set(self, key_path: str, value: Any, persist: bool = False) -> bool:
        """
        Set a configuration value by its key path
        
        Args:
            key_path: Dot-separated path to the configuration value
            value: Value to set
            persist: Whether to persist the change to the config file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            path_parts = key_path.split('.')
            
            # Navigate to the proper nested dictionary
            current = self.config
            for part in path_parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            
            # Set the value
            current[path_parts[-1]] = value
            
            # Persist to file if requested
            if persist:
                return self.save_config()
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting config value {key_path}: {e}")
            return False
    
    def save_config(self) -> bool:
        """
        Save the current configuration to the config file
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.config_file_path), exist_ok=True)
            
            # Write config to file
            with open(self.config_file_path, 'w') as f:
                json.dump(self.config, f, indent=4)
            
            self.logger.info(f"Configuration saved to {self.config_file_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving configuration: {e}")
            return False
    
    def get_all(self) -> Dict[str, Any]:
        """
        Get the entire configuration
        
        Returns:
            Dictionary containing the entire configuration
        """
        return self.config.copy()
    
    def validate(self) -> bool:
        """
        Validate the current configuration
        
        Returns:
            True if configuration is valid, False otherwise
        """
        # Implement validation logic here
        # For now, just check if required values are present
        try:
            # Check required database settings if database section exists
            if "database" in self.config:
                if not self.get("database.url"):
                    self.logger.warning("Missing database URL in configuration")
                if not self.get("database.key"):
                    self.logger.warning("Missing database API key in configuration")
            
            # More validation as needed
            
            return True
        except Exception as e:
            self.logger.error(f"Configuration validation error: {e}")
            return False 