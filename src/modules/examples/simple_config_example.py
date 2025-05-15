#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple Module Config Example

This example demonstrates how to use the ModuleConfigManager directly without
requiring other module imports. This makes it easier to understand the core functionality
and avoids potential import issues.
"""

import os
import sys
import json
import logging
import tempfile
import argparse

# Fix import path issue - add the modules directory to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
modules_dir = os.path.dirname(current_dir)  # /habitat/src/modules
sys.path.append(modules_dir)

# Direct import from the modules directory
from module_config_manager import ModuleConfigManager

def setup_logger():
    """Set up and return a logger"""
    logger = logging.getLogger("config_example")
    logger.setLevel(logging.INFO)
    
    # Add console handler if none exists
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger

def create_sample_config(module_type='camera'):
    """Create a sample configuration file and return its path"""
    # Create a temporary config file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
        if module_type == 'camera':
            config_data = {
                "camera": {
                    "width": 1280,
                    "height": 720,
                    "fps": 30,
                    "codec": "h264",
                    "file_format": "mp4"
                }
            }
        elif module_type == 'rfid':
            config_data = {
                "rfid": {
                    "reader_type": "rc522",
                    "bus": 0,
                    "device": 0,
                    "gpio_rst": 25,
                    "gpio_irq": 24
                }
            }
        elif module_type == 'microphone':
            config_data = {
                "microphone": {
                    "device_index": 0,
                    "type": "usb",
                    "sample_rate": 44100,
                    "channels": 1
                }
            }
        else:
            config_data = {
                "module": {
                    "custom_setting": "custom_value"
                }
            }
            
        json.dump(config_data, temp_file, indent=2)
        temp_file_path = temp_file.name
    
    return temp_file_path

def main():
    """Example of using the ModuleConfigManager"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Module Config Manager Example')
    parser.add_argument('--config', type=str, help='Path to config file')
    parser.add_argument('--create-sample', action='store_true', help='Create a sample config file')
    parser.add_argument('--module-type', type=str, default='example', 
                        choices=['example', 'camera', 'rfid', 'microphone', 'ttl_io'],
                        help='Type of module to simulate')
    args = parser.parse_args()
    
    # Set up logger
    logger = setup_logger()
    
    # Create a sample config file if requested
    if args.create_sample:
        sample_config_path = create_sample_config(args.module_type)
        logger.info(f"Created sample {args.module_type} config file at: {sample_config_path}")
        logger.info("You can use this file with the --config option next time")
        
        # Use the sample config if no other config specified
        if not args.config:
            args.config = sample_config_path
            logger.info(f"Using created sample config: {args.config}")
    
    # Initialize config manager
    module_type = args.module_type
    config_manager = ModuleConfigManager(logger, module_type, args.config)
    
    # Print loaded configuration
    logger.info("\nCurrent configuration values:")
    logger.info(f"  Module type: {config_manager.get('module.type')}")
    logger.info(f"  Heartbeat interval: {config_manager.get('module.heartbeat_interval')}s")
    logger.info(f"  Sample rate: {config_manager.get('module.samplerate')} Hz")
    logger.info(f"  Service port: {config_manager.get('service.port')}")
    
    # Print module-specific settings based on module type
    if args.module_type == 'camera' and config_manager.get('camera'):
        logger.info("\nCamera settings:")
        logger.info(f"  Resolution: {config_manager.get('camera.width')}x{config_manager.get('camera.height')}")
        logger.info(f"  Framerate: {config_manager.get('camera.fps')} fps")
        logger.info(f"  Codec: {config_manager.get('camera.codec')}")
        logger.info(f"  File format: {config_manager.get('camera.file_format')}")
    elif args.module_type == 'rfid' and config_manager.get('rfid'):
        logger.info("\nRFID settings:")
        logger.info(f"  Reader type: {config_manager.get('rfid.reader_type')}")
        logger.info(f"  GPIO reset pin: {config_manager.get('rfid.gpio_rst')}")
        logger.info(f"  GPIO interrupt pin: {config_manager.get('rfid.gpio_irq')}")
    elif args.module_type == 'microphone' and config_manager.get('microphone'):
        logger.info("\nMicrophone settings:")
        logger.info(f"  Device index: {config_manager.get('microphone.device_index')}")
        logger.info(f"  Type: {config_manager.get('microphone.type')}")
        logger.info(f"  Sample rate: {config_manager.get('microphone.sample_rate')} Hz")
        logger.info(f"  Channels: {config_manager.get('microphone.channels')}")
    elif args.module_type == 'ttl_io' and config_manager.get('ttl_io'):
        logger.info("\nTTL I/O settings:")
        logger.info(f"  Interface type: {config_manager.get('ttl_io.interface_type')}")
        if config_manager.get('digital_inputs.pins'):
            logger.info(f"  Digital input pins: {config_manager.get('digital_inputs.pins')}")
        if config_manager.get('digital_outputs.pins'):
            logger.info(f"  Digital output pins: {config_manager.get('digital_outputs.pins')}")
    
    # Show from which sources config values came from
    logger.info("\nConfiguration hierarchy example:")
    logger.info("  Values from default config: Module Type")
    logger.info("  Values from base config file: Base settings like power management")
    logger.info("  Values from module-specific config: Module-specific settings like 'camera.width'")
    logger.info("  Values from environment variables: Override specific settings")
    
    # Modify some values
    logger.info("\nUpdating configuration...")
    config_manager.set('module.heartbeat_interval', 60)
    config_manager.set(f'{args.module_type}.new_setting', "This is a new value")
    
    # Print updated values
    logger.info("\nUpdated configuration values:")
    logger.info(f"  Heartbeat interval: {config_manager.get('module.heartbeat_interval')}s")
    logger.info(f"  New setting: {config_manager.get(f'{args.module_type}.new_setting')}")
    
    # Save the config
    logger.info("\nSaving configuration to a new file...")
    new_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{args.module_type}_example_config.json")
    config_manager.config_file_path = new_config_path
    config_manager.save_config()
    logger.info(f"Configuration saved to: {new_config_path}")
    
    # Validate the config
    is_valid = config_manager.validate()
    logger.info(f"\nConfiguration is valid: {is_valid}")

if __name__ == "__main__":
    main() 