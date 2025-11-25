#!/usr/env/bin python
"""
Program to convert a video of the APA rig with a rat in it into training data.

@author: Andrew SG
@created: 14/11/25
"""
import cv2
import os
import argparse
import time

parser = argparse.ArgumentParser()
parser.add_argument('-i', '--input_file', type=str, required=True, help="The input video to be converted")
parser.add_argument('--max_files', type=int, default=200, help="The maximum amount of images to be created")
parser.add_argument('--interval', type=int, default=10, help="Amount of frames to use")
args = parser.parse_args()
# Inputs
# video_name = "2024-10-24 15-53-23_1805_ORLTM_sample_trim.ts"
video_name = args.input_file
max_files = args.max_files
interval = args.interval

print(f"Will generate {max_files} images from {video_name}")

parts = video_name.split(".")
video_name = parts[0]
video_filetype = parts[1]
video_folder = "videos/"
video_path = video_folder + video_name + "." + video_filetype

# Set up outputs
save_folder = "training_images/"
save_path = save_folder + video_name
os.makedirs(save_path, exist_ok=True)

# Open video and check metadata
cap = cv2.VideoCapture(video_path)
fps = round(cap.get(cv2.CAP_PROP_FPS), 1)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
channels = 3 # number of channels

jpeg_bytes = width * height * channels * 0.2 # estimate 0.2 bytes per channel per pixel
jpeg_kb = jpeg_bytes / 1024
print(f"Estimated filesize per image: {round(jpeg_kb,2)}kB")

print(f"Video has fps {fps}, size ({width}, {height}) with total {n_frames} frames or {round(n_frames/fps, 2)}s")

"""Find total files / space to be used"""
if max_files < (n_frames/interval):
    total_files = max_files
    interval = int(n_frames / total_files)
else:
    total_files = n_frames/interval

total_mb = (jpeg_kb * total_files) / 1024
print(f"Current settings will generate {total_files} images at {round(total_mb,2)}mB (one image every {round(interval/fps, 2)}s)")

"""Check if user wants to proceed"""
def check_proceed():
    proceed = False
    go = input("Are you happy to proceed? (y/n) ")
    if go == "y":
        return True
    if go == "n":
        return False
    else:
        return check_proceed()

go = check_proceed()
if not go:
    print("Exiting program...")
    quit()

"""Generate the images"""
frame_idx = 0 
n_files = 0
t0 = time.time()
print("Beginning to generate images")
while True:
    ret, frame = cap.read()
    if n_files > max_files:
        break
    if not ret:
        break
    if frame_idx % interval == 0: 
        n_files += 1
        print(f"Generating file {n_files} of {total_files}")
        cv2.imwrite(f'{save_path}/{video_name}_frame{frame_idx:04d}.jpg', frame)
    frame_idx += 1

t1 = time.time()
print(f"Generated {n_files} images in {t1-t0}s")
cap.release()