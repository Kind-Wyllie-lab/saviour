import subprocess
from datetime import datetime
import os
# On the camera module (sender)
def start_streaming(port=8081, width=1280, height=720, fps=60):
    cmd = [
        "rpicam-vid",
        "-t", "0",
        "-n",
        "--inline",
        "--listen",
        "--width", str(width),
        "--height", str(height),
        "--framerate", str(fps),
        "-o", f"tcp://0.0.0.0:{port}"
    ]
    
    # Start the process
    process = subprocess.Popen(cmd)
    return process

def start_recording_and_streaming(output_file=None, port=8081, width=1280, height=720, fps=100):
    # Ensure the output directory exists
    # if output_file is None:
    #     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #     output_file = f"rec/{timestamp}_bashtest.h264"
    # os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # This command streams the video to stdout, and then pipes it to a file ("recording.ts") and to the network on port 8081.
    # rpicam-vid -t 0 -n --codec libav --libav-format mpegts -o - | tee recording.ts | nc -l 8081 

    # This command is more robust, it will keep running even if the client disconnects. It saves multiple videos.
    # while true; do filename="recording_$(date +%Y%m%d_%H%M%S).ts"; rpicam-vid -t 0 -n --codec libav --libav-format mpegts -o - | tee "$filename" | nc 8081 -l || sleep 1; done

    # Maybe this one saves it all to single video?
    #mkfifo stream.pipe && while true; do rpicam-vid -t 0 -n --codec libav --libav-format mpegts -o - | tee >(ffmpeg -i - -c copy -f mpegts recording.ts) stream.pipe && nc 8081 -l < stream.pipe || sleep 1; done

    cmd = [
        "rpicam-vid",
        "-t", "0",
        "-n",
        "--codec", "libav",
        "--libav-format", "mpegts",
        "-o", "-",
        "|",
        "tee", "recording.ts",
        "|",
        "nc", "-l", f"{port}"
    ]
    
    # Start the process
    process = subprocess.Popen(cmd)
    return process

# On the controller (receiver)
def start_viewer(ip="192.168.0.14", port=8081, fps=60):
    cmd = [
        "ffplay",
        f"tcp://{ip}:{port}",
        "-vf", f"setpts=N/{fps}",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-framedrop",
        "-infbuf",
        "-probesize", "32",
        "-analyzeduration", "0"
    ]
    
    # Start the process
    process = subprocess.Popen(cmd)
    return process

# To stop either process
def stop_process(process):
    if process:
        process.terminate()
        process.wait()

# Start the streaming process
if __name__ == "__main__":

    # Start the viewer process
    viewer_process = start_recording_and_streaming()
