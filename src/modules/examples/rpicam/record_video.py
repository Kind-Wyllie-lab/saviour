#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Basic method to record video using an rpicam-vid subprocess.

To check the timestamps, use the following:
ffprobe rec/record_video.mp4 -hide_banner -select_streams v -show_entries frame | grep pts_time
"""

import subprocess
import time
import signal
import sys
import os

video_folder="rec"
filename="record_video"
filetype="mp4"
fps = 60

# Create recording directory if it doesn't exist
os.makedirs(video_folder, exist_ok=True)

# Global flag for signal handling
should_stop = False

def signal_handler(sig, frame):
    """Handle keyboard interrupt"""
    global should_stop
    print("\nKeyboard interrupt received, stopping recording...")
    should_stop = True
    # Don't exit immediately, let the subprocess finish

# Register the signal handler
signal.signal(signal.SIGINT, signal_handler)

# Build command
cmd = [
    "rpicam-vid",
    "--level", "4.2", # h264 target level
    "--framerate", f"{fps}",
    "--width", "1280",
    "--height", "720",
    "-o", f"{video_folder}/{filename}.{filetype}",
    "--codec", "libav",
    "-t", "3000"
]

# Start timing
time_start = time.time()
print(f"Time start: {time_start}")

try:
    # Run the subprocess and wait for it to complete
    process = subprocess.Popen(cmd)
    
    # Wait for either the process to complete or a keyboard interrupt
    while process.poll() is None and not should_stop:
        time.sleep(0.1)
    
    # If we got a keyboard interrupt, send SIGINT to the subprocess
    if should_stop:
        process.send_signal(signal.SIGINT)
        process.wait()  # Wait for the process to finish
    
except subprocess.CalledProcessError as e:
    print(f"Error running rpicam-vid: {e}")
    time_finish = time.time()
    print(f"Finished in {time_finish - time_start}s")
    sys.exit(1)

# If we get here, recording completed normally
time_finish = time.time()
print(f"Finished in {time_finish - time_start}s")

