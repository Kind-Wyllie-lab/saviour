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
import re
from datetime import datetime

video_folder="rec"
filename="record_video"
filetype="mp4"
fps = 60

# Create recording directory if it doesn't exist
os.makedirs(video_folder, exist_ok=True)

# Global flag for signal handling
should_stop = False
last_frame_time = None

def signal_handler(sig, frame):
    """Handle keyboard interrupt"""
    global should_stop, last_frame_time
    print("\nKeyboard interrupt received, stopping recording...")
    should_stop = True
    last_frame_time = time.time()
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
    "--metadata",
    "--codec", "libav",
    "-t", "3000"
]

# Start timing
time_start = time.time()
print(f"Time start: {datetime.fromtimestamp(time_start)}")

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
    else:
        # If process completed normally, get the last frame time
        last_frame_time = time.time()
    
except subprocess.CalledProcessError as e:
    print(f"Error running rpicam-vid: {e}")
    time_finish = time.time()
    print(f"Finished in {time_finish - time_start}s")
    sys.exit(1)

# If we get here, recording completed normally
time_finish = time.time()
print(f"Finished in {time_finish - time_start}s")
print(f"Last frame system time: {datetime.fromtimestamp(last_frame_time)}")

# Using Python's pipe handling (more secure)
ffprobe_cmd = [
    "ffprobe",
    f"{video_folder}/{filename}.{filetype}",
    "-hide_banner",
    "-select_streams", "v",
    "-show_entries", "frame"
]
grep_cmd = ["grep", "pts_time"]

ffprobe_process = subprocess.Popen(ffprobe_cmd, stdout=subprocess.PIPE)
grep_process = subprocess.Popen(grep_cmd, stdin=ffprobe_process.stdout, stdout=subprocess.PIPE)
ffprobe_process.stdout.close()
output = grep_process.communicate()[0]

# Parse timestamps into a list
timestamps = []
for line in output.decode().splitlines():
    if match := re.search(r'pts_time=(\d+\.\d+)', line):
        timestamps.append(float(match.group(1)))

# Print results
print(f"Found {len(timestamps)} frames")
print("\nFirst 5 timestamps:")
for t in timestamps[:5]:
    print(f"{t:.6f}")

print("\nLast 5 timestamps:")
for t in timestamps[-5:]:
    print(f"{t:.6f}")

# Calculate average frame duration
if len(timestamps) > 1:
    frame_durations = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    avg_duration = sum(frame_durations) / len(frame_durations)
    print(f"\nAverage frame duration: {avg_duration:.6f} seconds")
    print(f"Average framerate: {1/avg_duration:.2f} fps")

# Calculate the time offset between video timestamps and system time
if timestamps and last_frame_time:
    video_duration = timestamps[-1] - timestamps[0]
    system_duration = last_frame_time - time_start
    time_offset = system_duration - video_duration
    print(f"\nTime offset between video and system time: {time_offset:.6f} seconds")
    
    # Adjust timestamps to system time
    adjusted_timestamps = [time_start + t + time_offset for t in timestamps]
    print("\nFirst 5 adjusted timestamps (system time):")
    for t in adjusted_timestamps[:5]:
        print(f"{datetime.fromtimestamp(t)}")

    print("\nLast 5 adjusted timestamps (system time):")
    for t in adjusted_timestamps[-5:]:
        print(f"{datetime.fromtimestamp(t)}")

