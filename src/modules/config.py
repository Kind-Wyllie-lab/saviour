#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Config Manager

Centralizes configuration management for habitat modules, including:
- Loading configuration from environment variables, config files, and defaults
- Providing a consistent interface for accessing and modifying configuration values
- Validating configuration values
- Saving configuration changes
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Union

class Config:
    """Manages configuration for habitat modules"""
    
    # Default configuration values 
    # TODO: Consider deleting this
    DEFAULT_CONFIG = {
        # Module parameters
        "module": {
            "heartbeat_interval": 30,
            "samplerate": 200,
        },
        
        # Service parameters
        "service": {
            "port": 5000,
            "service_type": "_module._tcp.local.",
        },
        
        # Communication parameters
        "communication": {
            "command_socket_port": 5555,
            "status_socket_port": 5556,
            "data_format": "json",
        },
        
        # Health monitoring parameters
        "health_monitor": {
            "cpu_check_enabled": True,
            "memory_check_enabled": True,
            "disk_check_enabled": True,
            "warning_threshold_cpu": 80,
            "warning_threshold_memory": 80,
            "warning_threshold_disk": 80,
        },
        
        # File transfer parameters
        "file_transfer": {
            "max_retries": 3,
            "chunk_size": 65536,
            "timeout": 30,
        },
        
        # Logging parameters
        "logging": {
            "level": "INFO",
            "format": "%(asctime)s - %(levelname)s - %(message)s",
        },
    }
    
    # Configuration that should be loaded from environment variables
    ENV_CONFIG_MAPPING = {
        "MODULE_HEARTBEAT_INTERVAL": "module.heartbeat_interval",
        "MODULE_SAMPLERATE": "module.samplerate",
        "MODULE_PORT": "service.port",
        "MODULE_LOG_LEVEL": "logging.level",
        "MODULE_CMD_PORT": "communication.command_socket_port",
        "MODULE_STATUS_PORT": "communication.status_socket_port",
    }
    
    def __init__(self, module_type: str, config_file_path: Optional[str] = None):
        """
        Initialize the configuration manager
        
        Args:
            logger: Logger instance
            module_type: Type of the module (camera, microphone, etc.)
            config_file_path: Path to configuration file (optional)
        """
        self.logger = logging.getLogger(__name__)
        self.module_type = module_type
        
        # Set module-specific config file path if not provided
        if not config_file_path:
            config_file_path = os.path.join(os.path.dirname(__file__), f"config/{module_type}_config.json")
        
        self.logger.info(f"(CONFIG MANAGER) Module of type {module_type} set to use config file path: {config_file_path}")
        
        self.config_file_path = config_file_path
        
        # Path to the base module config file
        self.base_config_file_path = os.path.join(os.path.dirname(__file__), "base_module_config.json")
        
        self.config = self._load_config()

        self.logger.info(f"(CONFIG MANAGER) Module config loaded: {self.config}")
        
    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from default values, base config, environment variables, and module-specific config
        
        Returns:
            Dict containing merged configuration
        """
        # Start with default config
        config = self.DEFAULT_CONFIG.copy()
        
        # Add module type to config
        config["module"]["type"] = self.module_type
        
        # Load base configuration file if it exists
        if os.path.exists(self.base_config_file_path):
            try:
                with open(self.base_config_file_path, 'r') as f:
                    base_config = json.load(f)
                
                # Merge base config into config
                self._merge_configs(config, base_config)
                self.logger.info(f"(CONFIG MANAGER) Loaded base configuration from {self.base_config_file_path}")
            except Exception as e:
                self.logger.error(f"(CONFIG MANAGER) Error loading base config file: {e}")
        
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
        
        # Override with module-specific config file if it exists
        if os.path.exists(self.config_file_path):
            try:
                with open(self.config_file_path, 'r') as f:
                    file_config = json.load(f)
                
                # Recursively merge file config into config
                self._merge_configs(config, file_config)
                self.logger.info(f"(CONFIG MANAGER) Loaded module-specific configuration from {self.config_file_path}")
            except Exception as e:
                self.logger.error(f"(CONFIG MANAGER) Error loading module-specific config file: {e}")
        
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
        
        # First try the exact path
        current = self.config
        for part in path_parts:
            if part not in current:
                # If not found, try looking in editable and non-editable sections
                if "editable" in self.config:
                    # Try editable section
                    editable_current = self.config["editable"]
                    for editable_part in path_parts:
                        if editable_part not in editable_current:
                            break
                        editable_current = editable_current[editable_part]
                    else:
                        return editable_current
                
                if "non_editable" in self.config:
                    # Try non-editable section
                    non_editable_current = self.config["non_editable"]
                    for non_editable_part in path_parts:
                        if non_editable_part not in non_editable_current:
                            break
                        non_editable_current = non_editable_current[non_editable_part]
                    else:
                        return non_editable_current
                
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
            
            # First try to find if the key exists in editable or non-editable sections
            target_section = None
            target_path = None
            
            # Check if it exists in editable section
            if "editable" in self.config:
                editable_current = self.config["editable"]
                for i, part in enumerate(path_parts):
                    if part not in editable_current:
                        break
                    editable_current = editable_current[part]
                else:
                    target_section = "editable"
                    target_path = path_parts
            
            # Check if it exists in non-editable section
            if target_section is None and "non_editable" in self.config:
                non_editable_current = self.config["non_editable"]
                for i, part in enumerate(path_parts):
                    if part not in non_editable_current:
                        break
                    non_editable_current = non_editable_current[part]
                else:
                    target_section = "non_editable"
                    target_path = path_parts
            
            # If found in editable/non-editable, update there
            if target_section is not None:
                current = self.config[target_section]
                for part in target_path[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[target_path[-1]] = value
            else:
                # Otherwise, set in the root config (backward compatibility)
                current = self.config
                for part in path_parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
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
            # Create directory if it doesn't exist (handle case where config_file_path is just a filename)
            config_dir = os.path.dirname(self.config_file_path)
            if config_dir:  # Only create directory if there is a directory path
                os.makedirs(config_dir, exist_ok=True)
            
            # Write config to file
            with open(self.config_file_path, 'w') as f:
                json.dump(self.config, f, indent=4)
            
            self.logger.info(f"(MODULE CONFIG MANAGER) Configuration saved to {self.config_file_path}")
            return True
        except Exception as e:
            self.logger.error(f"(MODULE CONFIG MANAGER) Error saving configuration: {e}")
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
        # Basic validation
        try:
            # Check required module settings
            if not self.get("module.type"):
                self.logger.warning("Missing module type in configuration")
                return False
            
            # Check heartbeat interval is positive
            heartbeat_interval = self.get("module.heartbeat_interval")
            if heartbeat_interval <= 0:
                self.logger.warning(f"Invalid heartbeat interval: {heartbeat_interval}")
                return False
            
            # Check samplerate is positive
            samplerate = self.get("module.samplerate")
            if samplerate <= 0:
                self.logger.warning(f"Invalid samplerate: {samplerate}")
                return False
            
            # More validation as needed
            
            return True
        except Exception as e:
            self.logger.error(f"Configuration validation error: {e}")
            return False 