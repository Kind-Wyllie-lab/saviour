#!/usr/bin/python3
"""
Program to test simultaneous recording video and streaming over network.

Refer to p76 of the picamera2 manual.

use ffplay -i udp://192.168.0.13:10001 -fflags nobuffer -flags low_delay -probesize 32 -sync ext -vf "scale=1280:720:flags=fast_bilinear" on the receiving pi.
"""
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput, FfmpegOutput
import time
import datetime
picam2 = Picamera2()
video_config = picam2.create_video_configuration()
picam2.configure(video_config)
encoder = H264Encoder(repeat=True, iperiod=15)
receiver_ip = "192.168.0.98"
port = 10001
output1 = FfmpegOutput(f"-f mpegts udp://{receiver_ip}:{port}")
output2 = FileOutput()
encoder.output = [output1, output2]


# Start streaming to the network.
picam2.start_encoder(encoder)
picam2.start()
time.sleep(5)


# Start recording to a file.
output2.fileoutput = f"test_{time.time()}.h264"
output2.start()
time.sleep(5)
output2.stop()

# The file is closed, but carry on streaming to the network.
time.sleep(9999999)