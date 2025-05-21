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
from picamera2.encoders import H264Encoder
import time
import numpy as np

# Initialize the camera
picam2 = Picamera2()

# Configure the camera for video recording
video_config = picam2.create_video_configuration(main={"size": (1024, 768)})
picam2.configure(video_config)

# Set up the encoder for H.264 video
encoder = H264Encoder(bitrate=10000000)

# Start recording
output = "/home/pi/Desktop/habitat/src/modules/examples/test2.h264"
picam2.start_recording(encoder, output)

#
duration_in_sec = 3.23

# Record and capture FrameWallClock during recording
start_time = time.time()

#
frame_times = []
while time.time() - start_time < duration_in_sec:
    metadata = picam2.capture_metadata()
    frame_wall_clock = metadata.get('FrameWallClock', 'No data')
    frame_times.append(frame_wall_clock)

# Stop recording 
picam2.stop_recording()

#
np.savetxt('/home/pi/Desktop/habitat/src/modules/examples/frame_times.txt', frame_times)