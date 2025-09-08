#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Program to demonstrate recording timestamps using FileOutput method.
"""

import threading
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput, FileOutput
import os
import time
import numpy as np

picam2 = Picamera2()


config = picam2.create_video_configuration(
    main={"size": (1332,990)}
)

# Apply configuration
picam2.configure(config)

target_fps = 50
frame_duration = int(1000000/target_fps)
picam2.set_controls({"FrameDurationLimits": (frame_duration, frame_duration)})

# Create encoder with framerate settings
encoder = H264Encoder(
    bitrate=8000000,
    profile="high",
    framerate=target_fps
)

video_folder = "rec"
filename = "fileoutput_method"
filetype = "h264"  # Changed to mp4 for better container format

# Global variables
recording_start_time = None
capture_thread = None
is_recording = False
frame_times = []
file_output = None

def start_recording() -> bool:
    """Start continuous video recording"""
    global recording_start_time, capture_thread, is_recording, frame_times, file_output
    
    # Create filepath
    filepath = f"{video_folder}/{filename}.{filetype}"
    
    # Ensure recording directory exists
    os.makedirs(video_folder, exist_ok=True)
    
    try:
        # Create ffmpeg output with correct parameters
        file_output = FileOutput(filepath)
        encoder.output = file_output
        
        # Start recording
        picam2.start_encoder(encoder, name="main")
        is_recording = True
        recording_start_time = time.time()
        frame_times = []  # Reset frame times
        
        # Start frame capture thread
        capture_thread = threading.Thread(target=_capture_frames)
        capture_thread.daemon = True
        capture_thread.start()
        
        print(f"Recording started: {filepath}")
        return True
        
    except Exception as e:
        print(f"Error starting recording: {e}")
        return False

def stop_recording() -> bool:
    """Stop continuous video recording"""
    global recording_start_time, capture_thread, is_recording, frame_times, file_output
    
    try:
        # Stop recording with camera-specific code
        picam2.stop_encoder(encoder)
        
        # Stop frame capture thread
        is_recording = False
        if capture_thread:
            capture_thread.join(timeout=1.0)
        
        # Calculate duration
        if recording_start_time is not None:
            duration = time.time() - recording_start_time
            
            # Save timestamps
            timestamps_file = f"{video_folder}/{filename}_timestamps.txt"
            np.savetxt(timestamps_file, frame_times)
            
            print(f"Recording stopped. Captured {len(frame_times)} frames over {duration:.2f} seconds, fps={len(frame_times) / duration:.2f}")
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