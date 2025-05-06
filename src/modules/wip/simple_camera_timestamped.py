#!/usr/bin/python3
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
import time
import datetime

# Initialize camera
picam2 = Picamera2()

# Create optimized configuration
config = picam2.create_preview_configuration(
    main={"size": (640, 480), "format": "YUV420"},  # Using YUV420 for better performance
    lores={"size": (320, 240), "format": "YUV420"}
)
picam2.configure(config)

# Create optimized encoder with lower bitrate
encoder = H264Encoder(
    bitrate=2000000,  # 2 Mbps - reduced for better performance
    framerate=30
)

# Start camera
picam2.start()

# Warm-up period - let the camera stabilize
print("Warming up camera...")
time.sleep(2)  # Give the camera time to initialize

# Variables for frame rate monitoring
frame_count = 0
start_time = time.time()
last_time = start_time

# Start recording
print("Starting recording...")
picam2.start_recording(encoder, 'test.h264')

# Additional warm-up period after recording starts
print("Initializing recording buffers...")
time.sleep(1)  # Give the encoder time to establish its buffers

print("Recording started... Press Ctrl+C to stop")

try:
    while True:
        # Calculate and display frame rate every second
        current_time = time.time()
        frame_count += 1
        
        if current_time - last_time >= 1.0:
            fps = frame_count / (current_time - last_time)
            print(f"FPS: {fps:.2f}")
            frame_count = 0
            last_time = current_time
            
        time.sleep(0.01)  # Small sleep to prevent CPU hogging

except KeyboardInterrupt:
    print("\nStopping recording...")
    picam2.stop_recording()
    picam2.stop()
    
    print("\nRecording complete!")
    print("Try playing with:")
    print("ffplay test.h264")
    print("or")
    print("vlc test.h264") 