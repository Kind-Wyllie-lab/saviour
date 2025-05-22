#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Camera Timestamp Demo using picamera2

Author: catubc / Andrew SG
Source: https://forums.raspberrypi.com/viewtopic.php?t=377442#p2258480

This script uses Picamera2 to record a video and capture FrameWallClock timestamps.
This means that frame times will use the system clock of the raspberry pi, which is hopefully time-synced via PTP.
"""
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, Quality
import time
import numpy as np
from libcamera import controls
import subprocess

# Initialize the camera
picam2 = Picamera2()

# Get camera modes
camera_modes = picam2.sensor_modes
print(f"Camera modes: {camera_modes}")

# Choose high fps mode
chosen_mode = camera_modes[0]

# Set time
time_str = time.strftime("%Y%m%d_%H%M%S")

# Most basic config
# picam2.configure(picam2.create_video_configuration())

# Configure the camera to use the chosen mode
mode_config = picam2.create_video_configuration(sensor={"output_size": chosen_mode["size"],
                                                         "bit_depth": chosen_mode["bit_depth"]})
picam2.configure(mode_config)
print(f"Chosen sensor configuration: {picam2.camera_configuration()['sensor']}")

# Configure video settings (p26 picamera2_manual)
# !!WARNING!! THIS METHOD SEEMS BROKEN - GENERATES SLOW VIDEOS
fps = 100
video_config = picam2.create_video_configuration(controls={"NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Fast,
                                                 "FrameDurationLimits": (int(1000000/fps), int(1000000/fps)),
                                                 "FrameRate": fps
                                                 })
picam2.configure(video_config)
print(f"Chosen video configuration: {picam2.camera_configuration()['controls']}")

# controls = {'FrameRate': fps}
# fps_config = picam2.create_video_configuration(controls=controls)
# picam2.configure(fps_config)

picam2.video_configuration.controls.FrameRate = fps

# Set up the encoder for H.264 video
encoder = H264Encoder(bitrate=40000000,
                      framerate=fps, # If we don't set this, it defaults to something like 25-30
                      profile="main",
                      iperiod=30,
                      repeat=True)

# Start recording
output = f"/home/pi/Desktop/habitat/src/modules/examples/rec/test_{time_str}.h264"
picam2.start_recording(encoder, output)

# # Record and capture FrameWallClock during recording
# start_time = time.time()
# frame_count = 0
# frame_times = []

# print("Starting recording...")
# while time.time() - start_time < 6:  # Record for 6 seconds
#     metadata = picam2.capture_metadata()
#     frame_wall_clock = metadata.get('FrameWallClock', 'No data')
#     frame_times.append(frame_wall_clock)
#     frame_count += 1
#     if frame_count % 100 == 0:  # Print progress every 100 frames
#         print(f"Recorded {frame_count} frames...")

# print(f"Recording complete. Total frames: {frame_count}")

time.sleep(10)

# Stop recording 
picam2.stop_recording()

# Save frame times for analysis
# np.savetxt(f'/home/pi/Desktop/habitat/src/modules/examples/rec/frame_times_{time_str}.txt', frame_times)

# Convert to MP4 with explicit frame rate
subprocess.run(['ffmpeg', '-i', output, '-c', 'copy', '-r', str(fps), output[:-5]+'.mp4'])