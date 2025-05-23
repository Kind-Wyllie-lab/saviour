#!/usr/bin/python3
"""
Monitor a GPIO pin and record timestamps when it goes low.
The pin should be normally high (pulled up) and go low when triggered.
"""

import time
from gpiozero import Button
from datetime import datetime
import threading
import queue
import logging

class GPIOMonitor:
    def __init__(self, pin=17, pull_up=True, bouncetime=0.1):
        self.pin = pin
        self.pull_up = pull_up
        self.bouncetime = bouncetime
        self.timestamps = []
        self.running = False
        self.event_queue = queue.Queue()
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        
        # Create button with pull-up and debounce
        self.button = Button(
            pin,
            pull_up=pull_up,
            bounce_time=bouncetime
        )
        
    def _on_pin_low(self):
        """Callback when pin goes low"""
        timestamp = datetime.now()
        self.timestamps.append(timestamp)
        self.event_queue.put(timestamp)
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
        
    def get_latest_event(self):
        """Get the most recent event from the queue"""
        try:
            return self.event_queue.get_nowait()
        except queue.Empty:
            return None

def main():
    # Example usage
    monitor = GPIOMonitor(pin=17)  # Using GPIO17 as example
    
    try:
        monitor.start()
        print("Monitoring pin 17. Press Ctrl+C to stop.")
        
        while True:
            # Check for new events
            event = monitor.get_latest_event()
            if event:
                print(f"New event at: {event}")
            time.sleep(0.1)
            
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