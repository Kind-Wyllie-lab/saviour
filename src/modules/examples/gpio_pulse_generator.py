#!/usr/bin/python3
"""
Generate random low pulses on a GPIO pin and record when they're sent.
This is meant to work with gpio_timestamp.py on another Pi.
"""

import time
import random
from gpiozero import LED
from datetime import datetime
import logging

class PulseGenerator:
    def __init__(self, pin=17, min_delay=1.0, max_delay=5.0):
        self.pin = pin
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timestamps = []
        self.running = False
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        
        # Create LED output
        self.led = LED(pin)
        self.led.on()  # Start high
        
    def generate_pulse(self):
        """Generate a single low pulse"""
        # Record timestamp before going low
        timestamp = datetime.now()
        self.timestamps.append(timestamp)
        self.logger.info(f"Sending low pulse at {timestamp}")
        
        # Send the pulse
        self.led.off()
        time.sleep(0.1)  # Hold low for 100ms
        self.led.on()
        
    def start(self):
        """Start generating random pulses"""
        self.running = True
        self.logger.info(f"Started generating pulses on pin {self.pin}")
        
        while self.running:
            # Random delay between pulses
            delay = random.uniform(self.min_delay, self.max_delay)
            time.sleep(delay)
            
            if self.running:  # Check again in case we were stopped during sleep
                self.generate_pulse()
                
    def stop(self):
        """Stop generating pulses"""
        self.running = False
        self.led.on()  # Ensure pin is high when stopping
        self.logger.info(f"Stopped generating pulses on pin {self.pin}")
        
    def get_timestamps(self):
        """Get list of recorded timestamps"""
        return self.timestamps.copy()

def main():
    # Example usage
    generator = PulseGenerator(pin=17)  # Using GPIO17 as example
    
    try:
        print("Starting pulse generator. Press Ctrl+C to stop.")
        generator.start()
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        generator.stop()
        
        # Print all recorded timestamps
        print("\nSent pulses at:")
        for ts in generator.get_timestamps():
            print(ts)

if __name__ == "__main__":
    main() 