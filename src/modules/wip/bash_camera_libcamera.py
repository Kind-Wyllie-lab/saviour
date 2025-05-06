import os
import subprocess
import datetime

folder = "rec/"
filetype = "mp4" # h264, mp4
filename = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_test")

cmd = [
    "rpicam-vid",
    "--framerate", "120",
    "--width", "1280",
    "--height", "720",
    "-t", "5s",
    "-o", f"{folder}/{filename}.{filetype}",
    "--nopreview",
    "--level", "4.2", # H.264 level
    "--codec", "h264",
]

subprocess.run(cmd)