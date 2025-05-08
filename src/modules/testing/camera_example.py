import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camera_module import CameraModule

camera = CameraModule()
# camera.record_video(3)

def main():
    camera.start()

    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        camera.stop()

if __name__ == "__main__":
    main()