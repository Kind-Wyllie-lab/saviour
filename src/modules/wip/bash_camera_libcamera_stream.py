import os
import subprocess
import datetime

ip_addr = "192.168.0.11"
port = 5600

# For UDP
# rpicam-vid -t 0 -n --inline -o udp://<ip-addr>:<port>

# For TCP
#rpicam-vid -t 0 --inline --listen -o tcp://0.0.0.0:5600 --codec h264 --profile high --level 4.2 --intra 30 --width 640 --height 480 --framerate 30 --nopreview

# cmd = [
#     "rpicam-vid",
#     "--framerate", "120",
#     "--width", "1280",
#     "--height", "720",
#     "-t", "0",
#     "-o", f"udp://{ip_addr}:{port}",
#     "--inline",
#     "-n",
#     # "--nopreview",
#     "--level", "4.2", # H.264 level
#     "--codec", "h264",
#     "--profile", "high", # what is this?
#     "--intra", "30", # what is this?
# ]

cmd = [
    "rpicam-vid",
    "-t", "0",
    "-o", f"tcp://0.0.0.0:{port}",
    "--inline",
    "--listen",
    "--codec", "h264",
    "--profile", "high",
    "--level", "4.2",
    "--intra", "30",
    "--width", "1280",
    "--height", "720",
    "--framerate", "120",
    "--nopreview",
]

subprocess.run(cmd)

