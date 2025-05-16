#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PiCamera2 Test Script

Simple test script to verify that PiCamera2 is working.
"""

import time
from picamera2 import Picamera2

def test_camera():
    print("Initializing camera...")
    picam2 = Picamera2()
    
    print("Creating camera configuration...")
    config = picam2.create_preview_configuration()
    picam2.configure(config)
    
    print("Starting camera...")
    picam2.start()
    print("Camera started successfully!")
    
    # Give the camera time to adjust
    time.sleep(2)
    
    # Capture a test image
    print("Capturing image...")
    metadata = picam2.capture_file("test_capture.jpg")
    print(f"Image captured! Metadata: {metadata}")
    
    # Wait a moment and stop the camera
    time.sleep(1)
    picam2.stop()
    print("Camera stopped. Test complete!")

if __name__ == "__main__":
    try:
        test_camera()
    except Exception as e:
        print(f"Error: {e}") 