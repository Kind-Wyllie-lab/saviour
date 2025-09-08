#!/usr/bin/python3
"""
Monitor a GPIO pin and record timestamps when it goes low.
The pin should be normally high (pulled up) and go low when triggered.
"""

import time
from gpiozero import Button
import logging

class GPIOMonitor:
    def __init__(self, pin=16, pull_up=True):
        self.pin = pin
        self.pull_up = pull_up
        self.running = False
        self.timestamps = []  # Keep track of all timestamps
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        
        # Create button with pull-up, no debounce
        self.button = Button(
            pin,
            pull_up=pull_up,
            bounce_time=None  # Disable debouncing
        )
        
    def _on_pin_low(self):
        """Callback when pin goes low"""
        timestamp = time.time_ns()
        self.timestamps.append(timestamp)  # Store timestamp
        print(f"Pin {self.pin} went low at {timestamp}")  # Print in real-time
        self.logger.info(f"Pin {self.pin} went low at {timestamp}")
        
    def start(self):
        """Start monitoring the pin"""
        self.running = True
        self.button.when_pressed = self._on_pin_low
        self.logger.info(f"Started monitoring pin {self.pin}")
        
    def stop(self):
        """Stop monitoring the pin"""
        self.running = False
        self.button.when_pressed = None
        self.logger.info(f"Stopped monitoring pin {self.pin}")
        
    def get_timestamps(self):
        """Get list of recorded timestamps"""
        return self.timestamps.copy()

def main():
    # Example usage
    monitor = GPIOMonitor(pin=16)  # Using GPIO16
    
    try:
        monitor.start()
        print("Monitoring pin 16. Press Ctrl+C to stop.")
        
        # Keep the program running
        while monitor.running:
            time.sleep(0.1)  # Just to prevent CPU spinning
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        monitor.stop()
        
        # Print all recorded timestamps
        print("\nRecorded timestamps:")
        for ts in monitor.get_timestamps():
            print(ts)

if __name__ == "__main__":
    main() 