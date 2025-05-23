import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ttl_module import TTLModule

ttl = TTLModule()

def main():
    ttl.start()
    # ttl.start_recording_all_input_pins()
    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        ttl._print_ttl_event_buffer()
        ttl.stop()

if __name__ == "__main__":
    main()