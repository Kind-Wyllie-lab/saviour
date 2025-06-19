#!/usr/env/bin python
"""
After much difficulty on my own, David Plowman contributed me the following code which properly sets the pi framerate and sensor mode.
"""

import time
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput

picam2 = Picamera2()

mode = picam2.sensor_modes[0]

sensor = {'output_size': mode['size'], 'bit_depth': mode['bit_depth']}

main = {'size': (1280, 720), "format":"YUV420"}
controls = {'FrameRate': 120}
config = picam2.create_video_configuration(main, sensor=sensor, controls=controls, buffer_count=16)
picam2.configure(config)

encoder = H264Encoder(bitrate=10000000)
output = PyavOutput("test.mp4")

picam2.start_recording(encoder, output)

time.sleep(5)

picam2.stop_recording()