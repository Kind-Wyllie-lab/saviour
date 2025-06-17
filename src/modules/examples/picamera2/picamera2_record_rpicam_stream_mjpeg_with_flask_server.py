#!/usr/bin/python3
"""
MJPEG stream from Picamera2 using Flask.
View in browser at http://<PI_A_IP>:5000
"""

from flask import Flask, Response
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput, FfmpegOutput
import cv2
import io
import time
import datetime
import threading
import subprocess

# Initialise flask app
app = Flask(__name__)

# Configure picamera2
picam2 = Picamera2()
video_config = picam2.create_video_configuration(
    lores={"size": (640, 360)}
)
picam2.configure(video_config)
print("Lores stream config:", picam2.stream_configuration("lores"))


# Start picam2
picam2.start()

# Let it run for a moment
time.sleep(1)

# Function to record video
def record_video(duration=5):
    # Record with rpicam-vid
    fps = 60
    video_folder="rec"
    filename="rpicam_vid"
    filetype="mp4"
    cmd = [
        "rpicam-vid",
        "--level", "4.2", # h264 target level
        "--framerate", f"{fps}",
        "--width", "1280",
        "--height", "720",
        "-o", f"{video_folder}/{filename}.{filetype}",
        "--codec", "libav",
        "-t", f"{int(duration*1000)}"
    ]
    subprocess.run(cmd)

# Function to run flask app
def run_server():
    app.run(host='0.0.0.0', port=8080)

def generate():
    while True:
        frame_yuv = picam2.capture_array("lores")  # YUV420 format
        # Convert YUV420 (I420) to BGR (OpenCV default)
        frame_bgr = cv2.cvtColor(frame_yuv, cv2.COLOR_YUV2BGR_I420)
        ret, jpeg = cv2.imencode('.jpg', frame_bgr)
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')


@app.route('/')
def index():
    return '<h1>Live Camera</h1><img src="/video_feed">'

@app.route('/video_feed')
def video_feed():
    return Response(generate(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    server_thread = threading.Thread(target=run_server)
    recording_thread = threading.Thread(target=record_video)
    recording_thread.start()
    server_thread.start()
