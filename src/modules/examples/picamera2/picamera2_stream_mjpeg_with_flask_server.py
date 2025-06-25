#!/usr/bin/python3
"""
MJPEG stream from Picamera2 using Flask.
View in browser at http://<PI_A_IP>:5000
"""

from flask import Flask, Response
from picamera2 import Picamera2
import cv2
import io

app = Flask(__name__)
picam2 = Picamera2()
video_config = picam2.create_video_configuration(main={"size": (640, 480)})
picam2.configure(video_config)
picam2.start()

def generate():
    while True:
        frame = picam2.capture_array()
        ret, jpeg = cv2.imencode('.jpg', frame)
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
    app.run(host='0.0.0.0', port=8080)
