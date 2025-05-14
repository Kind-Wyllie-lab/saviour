from ptp_manager import PTPManager, DeviceType
import time
import signal
import sys
import logging

def main():
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create PTP manager
    ptp_manager = PTPManager(device_type=DeviceType.MASTER)
    
    # Handle graceful shutdown on CTRL+C
    def signal_handler(sig, frame):
        print("\nStopping PTP manager...")
        ptp_manager.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start PTP
    if not ptp_manager.start():
        print("Failed to start PTP manager")
        sys.exit(1)
    
    # Keep the program running
    print("PTP manager running. Press CTRL+C to stop.")
    while ptp_manager.is_running:
        time.sleep(1)

if __name__ == "__main__":
    main()