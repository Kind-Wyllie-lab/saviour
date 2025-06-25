#!/usr/bin/python3
"""
Demo of a picamera2 stream to a socket using UDP.
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

fps = 25

# Configure for low latency
video_config = picam2.create_video_configuration(
    main={"size": (1280, 720)},
    lores={"size": (854, 480)},
    controls={
        "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Fast,
        "FrameDurationLimits": (int(1000000/fps), int(1000000/fps)),  # 30fps
        "AwbMode": controls.AwbModeEnum.Indoor,  # Faster AWB
        "AeEnable": False,  # Disable auto exposure for lower latency
        "AnalogueGain": 2.0  # Fixed gain
    },
    encode="lores"
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
receiver_ip = "192.168.0.98"

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.connect((receiver_ip, 10001))
    stream = sock.makefile("wb")
    picam2.start_recording(encoder, FileOutput(stream))
    time.sleep(20)
    picam2.stop_recording()