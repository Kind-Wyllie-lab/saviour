#!/usr/bin/python3
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
import time

# Initialize camera
picam2 = Picamera2()

# Create preview configuration for better quality
config = picam2.create_preview_configuration(
    main={"size": (640, 480)},
    lores={"size": (320, 240), "format": "YUV420"}
)
picam2.configure(config)

# Create high-quality encoder
encoder = H264Encoder(
    bitrate=20000000,  # 20 Mbps for better quality
    repeat=True,
    iperiod=30,
    framerate=59,
    profile="high"  # Use high profile for better quality
)

# Start recording
picam2.start_recording(encoder, 'test.h264')
time.sleep(5)  # Record for 5 seconds
picam2.stop_recording()

print("\nRecording complete. Try playing with:")
print("ffplay -vf \"setpts=2.0*PTS\" test.h264")  # Play at half speed
print("or")
print("vlc test.h264") 