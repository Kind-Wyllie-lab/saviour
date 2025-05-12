import subprocess

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
    viewer_process = start_streaming()
