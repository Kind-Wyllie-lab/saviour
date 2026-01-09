#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Config Manager

Centralizes configuration management for SAVIOUR modules, including:
- Loading configuration from environment variables, config files, and defaults
- Builds active_config.json from base_config.json and {module}_config.json
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
    # Configuration that should be loaded from environment variables
    ENV_CONFIG_MAPPING = {
        "MODULE_CMD_PORT": "_communication.command_socket_port",
        "MODULE_STATUS_PORT": "_communication.status_socket_port",
        "CONTROLLER_USERNAME": "_controller_username",
        "CONTROLLER_PASSWORD": "_controller_password",
        "NAS_USERNAME": "_nas_username",
        "NAS_PASSWORD": "_nas_password"
    }
    
    def __init__(
        self, 
        base_config_path: Optional[str] = "../../config/base_config.json", 
        active_config_path: Optional[str] = "../../config/active_config.json"
    ):
        """
        Initialize the configuration manager
        
        Args:
            config_file_path: Path to the base configuration file (optional)
            active_config_path: Path to the active configuration file (optional)
        """
        self.logger = logging.getLogger(__name__)

        self.base_config_path = os.path.abspath(base_config_path) # Base config, i.e. defaults for SAVIOUR framework
        self.active_config_path = os.path.abspath(active_config_path) # Active config, aggregates base config + module specific config
        self.config: Dict[str, Any] = {} # Where runtime config is stored in program

        # Load or build config
        if os.path.exists(self.active_config_path):
            self.logger.info(f"Loading existing active config: {self.active_config_path}")
            self.config = self._load_json(self.active_config_path)
        else:
            self.logger.info(f"No active config found - building from base config: {self.base_config_path}")
            self.config = self._load_json(self.base_config_path)

        self._apply_env_override()
        self.logger.info(f"Module config loaded - {len(self.config)} parameters")

    def _apply_env_override(self):
        """Override config values using environment variables"""
        for env_var, config_key in self.ENV_CONFIG_MAPPING.items():
            if env_var in os.environ:
                value = os.environ[env_var]
                self._set_nested(config_key, value)

    def _set_nested(self, dotted_key, value):
        """Set a nested config value given a dotted key like 'a.b.c'."""
        keys = dotted_key.split(".")
        d = self.config
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    def load_module_config(self, module_config_path: str) -> None:
        """
        Load and merge module-specific config, then persist active_config.json
        Behaviour:
        - If active config does not exist: full merge 
        - If active config does exist: only fill in missing keys 
        """

        module_path = os.path.abspath(module_config_path)
        if not os.path.exists(module_path):
            self.logger.warning(f"Module config not found: {module_path}")
            return

        module_config = self._load_json(module_path)

        # Extract keys from config and flatten to dot notation for easy comparison
        self.module_config_keys = self._flatten_keys(module_config)
        
        self.logger.info(f"Loading module config keys: {self.module_config_keys}")

        if os.path.exists(self.active_config_path):
            # Active config exists: fill missing keys only
            self.logger.info("Active config present — filling missing keys from module defaults")
            self._merge_defaults(self.config, module_config)
        else:
            # First-time run: perform full merge and create active config
            self.logger.info("No active config — performing full merge with module defaults")
            self._merge_dicts(self.config, module_config)

        self.logger.info(f"{len(self.module_config_keys)} new config parameters loaded from {module_config_path}")

        # Persist active config after merging defaults
        self.save_active()

    def _flatten_keys(self, config: Dict[str, Any], parent_key=""):
        """Return a set of all nested keys in dotted notation"""
        keys = set()
        for key, value in config.items():
            full_key = f"{parent_key}.{key}" if parent_key else key
            if isinstance(value, dict):
                keys |= self._flatten_keys(value, full_key)
            else:
                keys.add(full_key)
        return keys
    
    def _load_json(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load {path}: {e}")
            return {}

    def _merge_defaults(self, target: Dict[str, Any], defaults: Dict[str, Any]) -> None:
        """
        Recursively merge defualts into target only for missing keys
        Does not overwrite existing values in target
        """
        for key, val in defaults.items():
            if key not in target:
                # Missing key - copy the default
                target[key] = val
            else:
                # Both present and both dicts - recurse
                if isinstance(target[key], dict) and isinstance(val, dict):
                    self._merge_defaults(target[key], val)
                # Otherwise target has a value, do not overwrite

    def _merge_dicts(self, base: Dict[str, Any], override: Dict[str, Any]) -> None:
        """Recursive merge - override values in base with override."""
        for key, val in override.items():
            # Check if current value is a dict, and recursively merge if so
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(val, dict)
            ):
                self._merge_dicts(base[key], val)
            # If current value is not dict, override base value with new val
            else:
                base[key] = val

    def reset_to_defaults(self, module_config_path: Optional[str] = None) -> None:
        """
        Delete active config (if exists) and rebuild from base + optional module config.
        Use this to intentionally discard runtime changes and reinstall defaults.
        """
        try:
            if os.path.exists(self.active_config_path):
                os.remove(self.active_config_path)
                self.logger.info(f"Removed active config: {self.active_config_path}")
        except Exception as e:
            self.logger.error(f"Failed removing active config: {e}")

        # rebuild
        self.config = self._load_json(self.base_config_path)
        if module_config_path:
            module_config = self._load_json(module_config_path)
            self._merge_dicts(self.config, module_config)
        self.save_active()
 

    
    def _check_if_module_config_updated(self, key_path: str) -> bool:
        """
        Runs within set() and checks if a parameter which has been set belongs to the submodule

        args:
            key_path: dot separated path to the parameter 
        """
        if hasattr(self, "module_config_keys") and key_path in self.module_config_keys:
            self.logger.info(f"Module-specific param updated: {key_path}")
            return True
        else:
            return False

    def save_active(self) -> None:
        """
        Save the aggregated config to active_config.json
        """
        os.makedirs(os.path.dirname(self.active_config_path), exist_ok=True)
        with open(self.active_config_path, "w") as f:
            json.dump(self.config, f, indent=4)
        self.logger.info(f"Saved active config to {self.active_config_path}")

    """Get and Set methods"""
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by its key path
        
        Args:
            key_path: Dot-separated path to the configuration value
            default: Default value to return if key doesn't exist
            
        Returns:
            Configuration value or default if not found
        """
        parts = key.split('.') # Split the . separated param into parts
        config = self.config
        for part in parts:
            if part in config:
                config = config[part]
            elif f"_{part}" in config: # Check for leading underscore
                config = config[f"_{part}"]
            else:
                config = default
        if config == None:
            self.logger.warning(f"config.get() returning None for {key}")
        return config
    
    def set(self, key_path: str, value: Any, persist: bool = True) -> bool:
        """Set value unless key is private (starts with underscore)."""
        parts = key_path.split('.')
        current = self.config

        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        last = parts[-1]
        if last.startswith('_'):
            self.logger.warning(f"Attempt to modify read-only config key: {key_path}")
            return False

        if self._check_if_module_config_updated(key_path):
            self.on_module_config_change([key_path]) # Put the key in a list to match what the function expects

        current[last] = value
        if persist:
            self.save_active()
        return True

    def get_all(self) -> Dict[str, Any]:
        """
        Get the entire configuration
        
        Returns:
            Dictionary containing the entire configuration
        """
        return self.config.copy()
    
    def set_all(self, updates: dict, persist: bool = False) -> None:
        """
        Update multiple config keys at once.

        Args:
            updates: dict with new values (can be nested)
            persist: whether to save active_config after update
        """
    
        self.logger.info("Set_all called for config")
        module_config_updated = False # Flag to track if we should notify that the module settings were updated
        module_config_updated_keys = [] # Array to store updated module configs

        def _recursive_update(target, source, parent_key=""):
            nonlocal module_config_updated
            for k, v in source.items():
                full_key = f"{parent_key}.{k}" if parent_key else k
                if isinstance(v, dict) and isinstance(target.get(k), dict):
                    _recursive_update(target[k], v, full_key)
                else:
                    # Update the value
                    if target[k] != v:
                        target[k] = v
                        # Track module-specific changes
                        if hasattr(self, "module_config_keys") and full_key in self.module_config_keys:
                            self.logger.info(f"Module-specific config updated: {full_key}")
                            module_config_updated_keys.append(full_key)
                            module_config_updated = True
                    else:
                        pass

        _recursive_update(self.config, updates)

        if module_config_updated == True:
            self.on_module_config_change(module_config_updated_keys)

        if persist:
            self.save_active()

