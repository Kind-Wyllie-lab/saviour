from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
import time
import subprocess

# Init camera
picam2 = Picamera2()

# Find the 100+ fps mode at 1332x990
sensor_mode = next(
    mode for mode in picam2.sensor_modes
    if mode['size'] == (1332, 990) and mode['fps'] >= 100
)

# Configure camera for high speed
config = picam2.create_video_configuration(
    main={"size": sensor_mode['size'], "format": "RGB888"},
    sensor={"output_size": sensor_mode['size'], "bit_depth": sensor_mode['bit_depth']},
    controls={"FrameDurationLimits": (10000, 10000)}  # 10000 µs = 100 fps
)

picam2.configure(config)

# Set up encoder and output
encoder = H264Encoder(bitrate=10000000)
raw_file = "output_100fps.h264"
picam2.encoder = encoder
picam2.output = FileOutput(raw_file)

# Start camera
picam2.start()
time.sleep(2)  # Warm-up time

# Record
print("🎥 Recording 5 seconds at 100fps...")
picam2.start_recording(encoder, picam2.output)
time.sleep(5)
picam2.stop_recording()
print("✅ Recording finished")

# Convert to mp4
mp4_file = "output_100fps.mp4"
subprocess.run([
    "ffmpeg", "-y", "-framerate", "100", "-i", raw_file,
    "-c:v", "copy", mp4_file
])
print(f"📁 Saved as {mp4_file}")
