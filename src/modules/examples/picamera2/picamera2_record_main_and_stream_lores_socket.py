#!/usr/bin/python3
"""
Program to test simultaneous recording video and streaming over network.

The main stream is used for recording and the lores stream for network streaming.

Refer to p76 of the picamera2 manual.

use ffplay -i udp://192.168.0.13:10001 -fflags nobuffer -flags low_delay -probesize 32 -sync ext -vf "scale=1280:720:flags=fast_bilinear" on the receiving pi.
"""
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput, FfmpegOutput
import time
import datetime

picam2 = Picamera2()
video_config = picam2.create_video_configuration(
    main={"size": (1920, 1080)},
    lores={"size": (640, 360)}
)
picam2.configure(video_config)

receiver_ip = "192.168.0.98"
port = 10001
filename = "main_recording.h264"

main_encoder = H264Encoder()
lores_encoder = H264Encoder()

file_output = FileOutput(f"{filename}")
network_output = FfmpegOutput(f"-f mpegts udp://{receiver_ip}:{port}")

main_encoder.output = file_output
lores_encoder.output = network_output

picam2.start()

picam2.start_encoder(main_encoder, name="main")
picam2.start_encoder(lores_encoder, name="lores")

# Let them run for a bit
time.sleep(5)

# To stop an encoder independently:
print("Stopping lores encoder that streams over network!")
picam2.stop_encoder(lores_encoder)

# The main encoder (recording) can continue running
time.sleep(5)

# When done, stop the main encoder and camera
print(f"Stopping recording on {filename}")
picam2.stop_encoder(main_encoder)
picam2.stop()

