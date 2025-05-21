#!/usr/bin/python3
"""
Demo of a picamera2 stream to a socket.

To play it on another pi use 
ffplay -i tcp://192.168.0.14:10001 -fflags nobuffer -flags low_delay -probesize 32 -sync ext -vf "scale=1280:720:flags=fast_bilinear"
"""

import socket
import time
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from libcamera import controls

# Initialize camera with low latency settings
picam2 = Picamera2()

# Configure for low latency
video_config = picam2.create_video_configuration(
    main={"size": (1280, 720)},
    controls={
        "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Fast,
        "FrameDurationLimits": (33333, 33333),  # 30fps
        "AwbMode": controls.AwbModeEnum.Indoor,  # Faster AWB
        "AeEnable": False,  # Disable auto exposure for lower latency
        "AnalogueGain": 2.0  # Fixed gain
    }
)
picam2.configure(video_config)

# Configure encoder for better quality while maintaining reasonable latency
encoder = H264Encoder(
    bitrate=4000000,  # Increased from 1M to 4M for better quality
    framerate=30,
    profile="main",  # Changed from baseline to main for better quality
    iperiod=30,  # Changed from 1 to 30 for better compression
    repeat=True
)

# Configure socket for low latency
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # Disable Nagle's algorithm
    sock.bind(("0.0.0.0", 10001))
    sock.listen(1)  # Only allow one connection

    picam2.encoders = encoder

    conn, addr = sock.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # Disable Nagle's on client socket too
    
    # Increased buffer size for better quality
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)  # 128KB buffer
    
    # Create a buffered file object with larger buffer
    stream = conn.makefile("wb", buffering=4096)  # Increased buffer size
    encoder.output = FileOutput(stream)
    
    picam2.start_encoder(encoder)
    picam2.start()
    
    try:
        while True:
            time.sleep(0.1)  # Small sleep to prevent CPU spinning
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        picam2.stop()
        picam2.stop_encoder()
        conn.close()