#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Program to demonstrate the original method of recording timestamps.
"""

import threading
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
import os
import time
import numpy as np

picam2 = Picamera2()

width = 1280
height = 720

config = picam2.create_video_configuration(
    main={"size": (width, height)} 
)

# Apply configuration
picam2.configure(config)
encoder = H264Encoder(bitrate=1000000)

video_folder = "rec"
filename = "basic_method"
filetype = "mp4"

# Global variables
recording_start_time = None
capture_thread = None
is_recording = False
frame_times = []

def start_recording():
    """Start continuous video recording"""
    global recording_start_time, capture_thread, is_recording, frame_times

    # Create filename using just the session ID
    filepath = f"{video_folder}/{filename}.{filetype}"
    
    # Ensure recording directory exists
    os.makedirs(video_folder, exist_ok=True)
    
    try:
        # Start recording
        picam2.start_recording(encoder, filepath)
        is_recording = True
        recording_start_time = time.time()
        frame_times = []  # Reset frame times
        
        # Start frame capture thread
        capture_thread = threading.Thread(target=_capture_frames)
        capture_thread.daemon = True
        capture_thread.start()
        
        # Send status update
        print(f"Recording started: {filepath}.")
        
        return filepath
        
    except Exception as e:
        print(f"Error starting recording: {e}")
        return None

def stop_recording():
    """Stop continuous video recording"""
    global recording_start_time, capture_thread, is_recording, frame_times
    
    try:
        # Stop recording
        picam2.stop_recording()
        is_recording = False
        
        # Stop frame capture thread
        if capture_thread:
            capture_thread.join(timeout=1.0)
        
        # Calculate duration
        if recording_start_time is not None:
            duration = time.time() - recording_start_time
            
            # Save timestamps
            timestamps_file = f"{video_folder}/{filename}_timestamps.txt"
            np.savetxt(timestamps_file, frame_times)
            
            print(f"Recording stopped. Captured {len(frame_times)} frames over {duration:.2f} seconds")    
            return True
        else:
            print("Error: recording_start_time was None")
            return False

    except Exception as e:
        print(f"Error stopping recording: {e}")
        return False

def _capture_frames():
    """Background thread to capture frame timestamps"""
    global frame_times
    
    while is_recording:
        try:
            metadata = picam2.capture_metadata()
            frame_wall_clock = metadata.get('FrameWallClock', 'No data')
            if frame_wall_clock != 'No data':
                frame_times.append(frame_wall_clock)
        except Exception as e:
            print(f"Error capturing frame metadata: {e}")
            time.sleep(0.001)  # Small delay to prevent CPU spinning

# Start the camera
picam2.start()

# Record for 3 seconds
start_recording()
time.sleep(3)
stop_recording()

# Stop the camera
picam2.stop()