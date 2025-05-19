#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Config Example

Example showing how to use the ModuleConfigManager with a camera module.
"""

import sys
import os
import time
import argparse

# Fix import paths by adding the modules directory to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
modules_dir = os.path.dirname(current_dir)  # /habitat/src/modules
sys.path.append(modules_dir)

# Direct imports from the modules directory
from camera_module import CameraModule

def main():
    """Example of using the ModuleConfigManager with a camera module"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Camera Module Example')
    parser.add_argument('--config', type=str, help='Path to config file')
    args = parser.parse_args()
    
    # Initialize camera module with optional config file
    camera = CameraModule(config_file_path=args.config)
    
    # Print current configuration
    print("\nCurrent camera configuration:")
    print(f"  Resolution: {camera.get_config('camera.width')}x{camera.get_config('camera.height')}")
    print(f"  Framerate: {camera.get_config('camera.fps')} fps")
    print(f"  Codec: {camera.get_config('camera.codec')}")
    print(f"  File format: {camera.get_config('camera.file_format')}")
    print(f"  Heartbeat interval: {camera.get_config('module.heartbeat_interval')}s")
    
    # Make config changes
    print("\nUpdating camera parameters...")
    camera.set_camera_parameters({
        "width": 1920,
        "height": 1080,
        "fps": 60
    })
    
    # Print updated configuration
    print("\nUpdated camera configuration:")
    print(f"  Resolution: {camera.get_config('camera.width')}x{camera.get_config('camera.height')}")
    print(f"  Framerate: {camera.get_config('camera.fps')} fps")
    
    # Save config to file
    print("\nSaving configuration to file...")
    camera.set_config("camera.exposure_mode", "night", persist=True)
    print(f"Configuration saved to {camera.config_manager.config_file_path}")
    
    # Start the module
    camera.start()
    
    try:
        # Run for a bit
        print("\nRunning camera module - press Ctrl+C to exit")
        for i in range(30):
            time.sleep(1)
            print(".", end="", flush=True)
            if i == 10:
                # Record a short video
                print("\nRecording a short video...")
                camera.record_video(length=3)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        # Stop the module
        camera.stop()
    
if __name__ == "__main__":
    main()