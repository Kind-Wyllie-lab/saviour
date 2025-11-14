import cv2
import os
import argparse

# Inputs
video_name = "2024-10-24 15-53-23_1805_ORLTM_sample_trim.ts"
parts = video_name.split(".")
video_name = parts[0]
video_filetype = parts[1]
video_folder = "videos/"
video_path = video_folder + video_name + "." + video_filetype
print(video_path)

# Outputs
save_folder = "training_images/"
save_path = save_folder + video_name

cap = cv2.VideoCapture(video_path)
os.makedirs(save_path, exist_ok=True)

frame_idx = 0
save_every = 1000 # Save every x frames
max_files = 100 # Max files to save
n_files = 0
while True:
    ret, frame = cap.read()
    if n_files > max_files:
        break
    if not ret:
        break
    if frame_idx % 5 == 0:  # save every 5th frame
        n_files += 1