#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - TTL Module 

This class extends the base Module class to handle TTL-specific functionality.

Assumes input pins are normally high and go low when triggered.

Author: Andrew SG
Created: 23/05/2025
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
import random
from src.modules.module import Module
from src.modules.command import Command
import logging
import threading
import gpiozero
import datetime
import json
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum

# Add GPIO cleanup at module level
import atexit
import signal

# Global GPIO cleanup function
def cleanup_gpio():
    """Clean up all GPIO resources"""
    try:
        gpiozero.Device.pin_factory.close()
    except:
        pass

# Register cleanup function
atexit.register(cleanup_gpio)

# Handle signals for cleanup
def signal_handler(signum, frame):
    cleanup_gpio()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

class TTLValue(Enum):
    LOW = 0
    HIGH = 1


@dataclass
class TTLEvent:
    """Class for representing a TTL event"""
    timestamp_ns: int
    pin_number: int
    pin_mode: str
    event: str
    pin_description: str = ""


class TTLModule(Module):
    def __init__(self, module_type="ttl"):
        # Call the parent class constructor first
        super().__init__(module_type)
        
        # Load TTL Config
        self.config.load_module_config("ttl_config.json")

        # TTL specific variables
        self._ttl_file_handle = None # The open .csv file for storing events

        # Initialize GPIO
        self.output_pins = []
        self.input_pins = []

        # Pin type tracking
        self.pin_configs = {}  # Store pin configurations
        self.experiment_clock_pins = []  # Pins configured as experiment clock
        self.pseudorandom_pins = []  # Pins configured as pseudorandom
        self.generator_threads = {}  # Store generator threads

        # Assign pins from config
        self.assign_pins()

        # Recording variables
        self.recording_start_time = None
        self.recording_stop_time = None
        self.recording = False

        # Pulse generation variables
        self.pulse_generation_active = False
        self.pulse_generation_thread = None
        self.pulse_generation_pin = None
        self.pulse_generation_config = {
            'min_interval': 1.0,  # Minimum interval between pulses in seconds
            'max_interval': 10.0,  # Maximum interval between pulses in seconds
            'pulse_duration': 0.01  # Duration of low pulse in seconds
        }

        # State flags (matching camera module pattern)
        self.is_recording = False
        self.is_streaming = False

        # Set up TTL-specific callbacks for the command handler
        self.ttl_callbacks = {}
        self.command.set_callbacks(self.ttl_callbacks) # Append new TTL callbacks
        self.logger.info(f"Command handler callbacks: {self.command.callbacks}")

        self.logger.info(f"Initialized TTL module with {len(self.input_pins)} input pins and {len(self.output_pins)} output pins")


    # def handle_command(self, command: str, **kwargs):
    #     return self.command.handle_command(command, **kwargs)


    def _start_recording(self) -> bool:
        """Start TTL event recording"""
        # Store experiment name for use in timestamps filename
        
        try:
            # Reset recording state
            self.recording_start_time = time.time()
            self.recording_stop_time = None
            self.recording = True
            self.is_recording = True
            # Save TTL events to file
            self._create_ttl_file()
            
            # Start experiment clock and pseudorandom generators
            self._start_pin_generators()
            
            # Start monitoring all input pins
            self._start_recording_all_input_pins()

            # Send status response after successful recording start
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    # "filename": filename,
                    "recording": True,
                    "session_id": self.recording_session_id
                })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_start_failed",
                    "error": str(e)
                })
            return False


    def _start_recording_all_input_pins(self):
        self.logger.info(f"Starting to record all input pins")
        for pin in self.input_pins:
            self._start_recording_on_output_pin(pin)
    

    def _start_recording_on_output_pin(self, pin):
        pin.when_pressed = self._handle_input_pin_low
        pin.when_released = self._handle_input_pin_high
        self.logger.info(f"Started monitoring output pin {pin.pin}")


    def _stop_recording(self) -> bool:
        """Stop TTL event recording"""
        try:
            self.logger.info("Attempting to stop TTL specific recording")

            # Stop monitoring all input pins
            self.stop_recording_all_input_pins()
            
            # Stop all pin generators
            self._stop_pin_generators()

            # Set all output pins to low to signal experiment stopped
            self._set_all_output_pins_low()
            
            # Update recording state
            self.recording_stop_time = time.time()
            self.recording = False
            self.is_recording = False

            # self.add_session_file(events_file)
            self._close_ttl_event_file(filename=self.current_ttl_events_filename)
            
            # Calculate duration
            if self.recording_start_time is not None:
                
                # Send status response after successful recording stop
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "session_id": self.recording_session_id,
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully"
                    })
                return True
            else:
                self.logger.error("Error: recording_start_time was None")
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "status": "error",
                        "error": "Recording start time was not set."
                    })
                return False
            
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": str(e)
                })
            return False
    

    def configure_module(self):
        self.logger.info(f"Something changed in TTL configuration")
        self.assign_pins()


    def stop_recording_all_input_pins(self):
        self.logger.info(f"Stopping to record all input pins")
        for pin in self.input_pins:
            self.stop_recording_on_input_pin(pin)
    

    def stop_recording_on_input_pin(self, pin):
        pin.when_pressed = None
        pin.when_released = None
        self.logger.info(f"Stopped monitoring input pin {pin.pin}")
    

    def stop_recording_on_output_pin(self, pin):
        pin.when_pressed = None
        pin.when_released = None
        self.logger.info(f"Stopped monitoring output pin {pin.pin}")


    def _handle_input_pin_low(self, pin):
        """Handle input pin going low (pressed)"""
        self._write_ttl_event(time.time_ns(), pin.pin.number, TTLValue.LOW)
        self.logger.info(f"Input pin {pin.pin} went low (pressed)")


    def _handle_input_pin_high(self, pin):
        """Handle input pin going high (released)"""
        self._write_ttl_event(time.time_ns(), pin.pin.number, TTLValue.HIGH)
        self.logger.info(f"Input pin {pin.pin} went high (released)") 


    def _create_ttl_file(self):
        """Create the initial CSV file to save TTL events"""
        self.current_ttl_events_filename = f"{self.current_filename_prefix}_events.csv"
        filename = self.current_ttl_events_filename
        self.logger.info(f"Creating ttl file {filename}")
        self.add_session_file(filename)
        try:
            self._ttl_file_handle = open(filename, "w", buffering=1)  # line-buffered
            # Write header with metadata
            f = self._ttl_file_handle
            f.write("# TTL Event Recording\n")
            f.write(f"# Session ID: {self.recording_session_id}\n")
            f.write(f"# Recording Start: {self.recording_start_time}\n")
            f.write("#\n")
            f.write("Timestamp_nanoseconds, pin_number, pin_mode, pin_state, pin_description\n")
        except Exception as e:
            self.logger.error(f"Failed to open TTL events file: {e}")
            self._ttl_file_handle = None


    def _write_ttl_event(self, timestamp_ns: int, pin_number: int, state: TTLValue): 
        """Write a TTL event to file"""
        if self._ttl_file_handle:
            self._ttl_file_handle.write(f'{timestamp_ns},{pin_number},{self.pin_configs[pin_number].get("mode")},{state},{self.pin_configs[pin_number].get("description")}\n')


    def _close_ttl_event_file(self, filename="ttl_event_buffer.csv"):
        """Close ttl file"""
        try:
            self._ttl_file_handle = None
            self.logger.info(f"Closed ttl file {filename}")
        except Exception as e:
            self.logger.warning(f"Error closing ttl file: {e}")


    def start_pseudo_random_pulses(self, pin_number, min_interval=1.0, max_interval=10.0, pulse_duration=0.01):
        """
        Start generating pseudo-random pulses on a specified output pin.
        
        Args:
            pin_number: GPIO pin number to generate pulses on
            min_interval: Minimum interval between pulses in seconds (default: 1.0)
            max_interval: Maximum interval between pulses in seconds (default: 10.0)
            pulse_duration: Duration of low pulse in seconds (default: 0.01)
            
        Returns:
            bool: True if started successfully, False otherwise
        """
        try:
            # Check if pin is valid
            if pin_number not in self.ttl_output_pins:
                self.logger.error(f"Pin {pin_number} is not configured as an output pin")
                return False
            
            # Stop any existing pulse generation
            if self.pulse_generation_active:
                self.stop_pseudo_random_pulses()
            
            # Find the pin object
            pin_obj = None
            for pin in self.output_pins:
                if pin.pin.number == pin_number:
                    pin_obj = pin
                    break
            
            if not pin_obj:
                self.logger.error(f"Could not find pin object for pin {pin_number}")
                return False
            
            # Update configuration
            self.pulse_generation_config.update({
                'min_interval': min_interval,
                'max_interval': max_interval,
                'pulse_duration': pulse_duration
            })
            
            # Set initial state to low
            pin_obj.off()
            self.logger.info(f"Set pin {pin_number} to initial low state")
            
            # Start pulse generation thread
            self.pulse_generation_active = True
            self.pulse_generation_pin = pin_obj
            
            self.pulse_generation_thread = threading.Thread(
                target=self._pulse_generation_worker,
                args=(pin_obj,),
                daemon=True
            )
            self.pulse_generation_thread.start()
            
            self.logger.info(f"Started pseudo-random pulse generation on pin {pin_number}")
            self.logger.info(f"Configuration: min_interval={min_interval}s, max_interval={max_interval}s, pulse_duration={pulse_duration}s")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting pseudo-random pulses: {e}")
            return False
    

    def stop_pseudo_random_pulses(self):
        """
        Stop generating pseudo-random pulses.
        
        Returns:
            bool: True if stopped successfully, False otherwise
        """
        try:
            if not self.pulse_generation_active:
                self.logger.info("Pulse generation was not active")
                return True
            
            # Stop the thread
            self.pulse_generation_active = False
            
            # Wait for thread to finish
            if self.pulse_generation_thread and self.pulse_generation_thread.is_alive():
                self.pulse_generation_thread.join(timeout=2.0)
            
            # Set pin back to high state
            if self.pulse_generation_pin:
                self.pulse_generation_pin.on()
                self.logger.info(f"Set pin {self.pulse_generation_pin.pin.number} back to high state")
            
            self.pulse_generation_pin = None
            self.pulse_generation_thread = None
            
            self.logger.info("Stopped pseudo-random pulse generation")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping pseudo-random pulses: {e}")
            return False
    

    def _pulse_generation_worker(self, pin_obj):
        """
        Worker thread for generating pseudo-random pulses.
        
        Args:
            pin_obj: GPIO pin object to generate pulses on
        """
        try:
            # Set pin to high initially
            pin_obj.on()
            self.logger.info(f"Pulse generation worker started on pin {pin_obj.pin.number}")
            
            while self.pulse_generation_active:
                # Generate random interval
                interval = random.uniform(
                    self.pulse_generation_config['min_interval'],
                    self.pulse_generation_config['max_interval']
                )
                
                # Wait for the interval
                time.sleep(interval)
                
                # Check if we should still be running
                if not self.pulse_generation_active:
                    break
                
                # Generate pulse (set low, then high)
                pin_obj.off()
                time.sleep(self.pulse_generation_config['pulse_duration'])
                pin_obj.on()
                
                self.logger.debug(f"Generated pulse on pin {pin_obj.pin.number} after {interval:.3f}s interval")
                
        except Exception as e:
            self.logger.error(f"Error in pulse generation worker: {e}")
        finally:
            pin_obj.off()
            self.logger.info(f"Pulse generation worker stopped for pin {pin_obj.pin.number} and pin set to low")
    

    def get_pulse_generation_status(self):
        """
        Get the current status of pulse generation.
        
        Returns:
            dict: Status information about pulse generation
        """
        return {
            'active': self.pulse_generation_active,
            'pin': self.pulse_generation_pin.pin.number if self.pulse_generation_pin else None,
            'config': self.pulse_generation_config.copy()
        }


    def assign_pins(self):
        """Assign pins based on configuration from config file"""
        try:
            # Clean up any existing GPIO resources first
            self._cleanup_gpio()
            
            # Get pin configuration from config
            pins_config = self.config.get("ttl.pins", {})
            
            if not pins_config:
                self.logger.warning("No pin configuration found in config file")
                return
            
            self.logger.info(f"Assigning pins from config: {pins_config}")
            
            # Clear existing pin lists
            self.input_pins = []
            self.output_pins = []
            
            # Clear pin type tracking
            self.pin_configs = {}
            self.experiment_clock_pins = []
            self.pseudorandom_pins = []
            
            # Track pin assignments for logging
            input_pins_assigned = []
            output_pins_assigned = []
            
            for pin_number, pin_config in pins_config.items():
                try:
                    pin_number = int(pin_number)
                    pin_type = pin_config.get("mode")
                    
                    # Store pin configuration
                    self.pin_configs[pin_number] = pin_config
                    
                    if pin_type == "input":
                        # Create input pin (Button object) with proper error handling
                        try:
                            pin_obj = gpiozero.Button(pin_number, bounce_time=0, pull_up=True)
                            self.input_pins.append(pin_obj)
                            input_pins_assigned.append(pin_number)
                            self.logger.info(f"Assigned input pin {pin_number}")
                        except Exception as e:
                            self.logger.error(f"Failed to assign input pin {pin_number}: {e}")
                            # Try to clean up and retry once
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.Button(pin_number, bounce_time=0, pull_up=True)
                                self.input_pins.append(pin_obj)
                                input_pins_assigned.append(pin_number)
                                self.logger.info(f"Successfully assigned input pin {pin_number} after retry")
                            except Exception as e2:
                                self.logger.error(f"Failed to assign input pin {pin_number} after retry: {e2}")
                        
                    elif pin_type == "pseudorandom":
                        # Create output pin (LED object) with proper error handling
                        try:
                            pin_obj = gpiozero.LED(pin_number)
                            self.output_pins.append(pin_obj)
                            output_pins_assigned.append(pin_number)
                            # For pseudorandom, start in low state
                            pin_obj.off()
                            self.pseudorandom_pins.append(pin_number)
                            self.logger.info(f"Assigned output pin {pin_number} as pseudorandom (initial state: LOW)")
                        except Exception as e:
                            self.logger.error(f"Failed to assign output pin {pin_number}: {e}")
                            # Try to clean up and retry once
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.LED(pin_number)
                                self.output_pins.append(pin_obj)
                                output_pins_assigned.append(pin_number)
                                pin_obj.off()
                                self.logger.info(f"Successfully assigned output pin {pin_number} after retry")
                            except Exception as e2:
                                self.logger.error(f"Failed to assign output pin {pin_number} after retry: {e2}")
                            
                    elif pin_type == "experiment_clock":
                        try:
                            pin_obj = gpiozero.LED(pin_number)
                            self.output_pins.append(pin_obj)
                            output_pins_assigned.append(pin_number)
                            # For experiment clock, start in low state (will go high when recording starts)
                            pin_obj.off()
                            self.experiment_clock_pins.append(pin_number)
                            self.logger.info(f"Assigned output pin {pin_number} as experiment_clock (initial state: LOW)")
                        except Exception as e:
                            self.logger.error(f"Failed to assign output pin {pin_number}: {e}")
                            # Try to clean up and retry once
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.LED(pin_number)
                                self.output_pins.append(pin_obj)
                                output_pins_assigned.append(pin_number)
                                pin_obj.off()
                                self.logger.info(f"Successfully assigned output pin {pin_number} after retry")
                            except Exception as e2:
                                self.logger.error(f"Failed to assign output pin {pin_number} after retry: {e2}")
                            
                    else:
                        self.logger.warning(f"Unknown pin type '{pin_type}' for pin {pin_number}")
                        
                except ValueError as e:
                    self.logger.error(f"Invalid pin number '{pin_number}': {e}")
                except Exception as e:
                    self.logger.error(f"Error assigning pin {pin_number}: {e}")
            
            # Update the pin lists for backward compatibility
            self.ttl_input_pins = input_pins_assigned
            self.ttl_output_pins = output_pins_assigned
            
            self.logger.info(f"Pin assignment complete: {len(self.input_pins)} input pins, {len(self.output_pins)} output pins")
            
        except Exception as e:
            self.logger.error(f"Error in assign_pins: {e}")
    

    def _cleanup_gpio(self):
        """Clean up GPIO resources to prevent conflicts"""
        try:
            # Close existing pin factory
            # gpiozero.Device.pin_factory.close()
            # Clean up any gpiozero objects
            for pin_list in [getattr(self, 'input_pins', []), getattr(self, 'output_pins', [])]:
                for pin in pin_list:
                    try:
                        pin.close()
                    except Exception as e:
                        self.logger.debug(f"Failed to close pin {pin}: {e}")
            self.input_pins.clear()
            self.output_pins.clear()
            time.sleep(0.1) # Small delay to ensure cleanup
        except Exception as e:
            self.logger.debug(f"GPIO cleanup error (non-critical): {e}")


    def _start_pin_generators(self):
        """Start experiment clock and pseudorandom generators for all configured pins"""
        try:
            # Start experiment clock generators
            for pin_number in self.experiment_clock_pins:
                self._start_experiment_clock(pin_number)
            
            # Start pseudorandom generators
            for pin_number in self.pseudorandom_pins:
                self._start_pseudorandom_generator(pin_number)
                
            self.logger.info(f"Started {len(self.experiment_clock_pins)} experiment clock generators and {len(self.pseudorandom_pins)} pseudorandom generators")
            
        except Exception as e:
            self.logger.error(f"Error starting pin generators: {e}")
    
    
    def _stop_pin_generators(self):
        """Stop all pin generators"""
        try:
            # Set recording flag to False to signal threads to stop
            self.recording = False
            
            # Give threads a moment to exit naturally and execute finally blocks
            time.sleep(0.1)  # 100ms should be enough for daemon threads to check the flag and cleanup
            
            # Clear thread references
            self.generator_threads.clear()
            self.logger.info("Stopped all pin generators")
            
        except Exception as e:
            self.logger.error(f"Error stopping pin generators: {e}")
    

    def _set_all_output_pins_low(self):
        """Set all output pins to low state"""
        try:
            for pin in self.output_pins:
                try:
                    pin.off()
                    self.logger.info(f"Set output pin {pin.pin.number} to low state")
                except Exception as e:
                    self.logger.error(f"Error setting pin {pin.pin.number} to low: {e}")
            self.logger.info(f"Set all {len(self.output_pins)} output pins to low state")
        except Exception as e:
            self.logger.error(f"Error setting output pins to low: {e}")
    

    def _start_experiment_clock(self, pin_number):
        """Start experiment clock generator for a specific pin"""
        try:
            # Get pin configuration
            pin_config = self.pin_configs.get(pin_number, {})
            duty_cycle = pin_config.get("duty_cycle", 0.5)
            self.logger.info(f"Duty cycle for experiment clock {duty_cycle} from config")

            period = 1.0  # 1 second period
            
            # Find the pin object
            pin_obj = None
            for pin in self.output_pins:
                if pin.pin.number == pin_number:
                    pin_obj = pin
                    break
            
            if not pin_obj:
                self.logger.error(f"Could not find pin object for experiment clock pin {pin_number}")
                return False
            
            # Set initial state to HIGH (experiment clock starts high when recording begins)
            pin_obj.on()
            self._write_ttl_event(time.time_ns(), pin_number, TTLValue.HIGH)
            self.logger.info(f"Started experiment clock on pin {pin_number} with {duty_cycle} duty cycle")
            
            # Start generator thread
            thread = threading.Thread(
                target=self._experiment_clock_worker,
                args=(pin_obj, pin_number, duty_cycle, period),
                daemon=True
            )
            thread.start()
            self.generator_threads[pin_number] = thread
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting experiment clock on pin {pin_number}: {e}")
            return False
    

    def _experiment_clock_worker(self, pin_obj, pin_number, duty_cycle, period):
        """Worker thread for experiment clock generation"""
        try:
            self.logger.info(f"Experiment clock worker started on pin {pin_number}")
            
            while self.recording:
                # Calculate timing
                high_time = period * duty_cycle
                low_time = period * (1.0 - duty_cycle)
                
                # High phase
                pin_obj.on()
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.HIGH)
                time.sleep(high_time)
                
                # Check if still recording
                if not self.recording:
                    break
                
                # Low phase
                pin_obj.off()
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.LOW)
                time.sleep(low_time)
                
        except Exception as e:
            self.logger.error(f"Error in experiment clock worker for pin {pin_number}: {e}")
        finally:
            pin_obj.off()
            self.logger.info(f"Experiment clock worker stopped for pin {pin_number} and pin set to low")
    

    def _start_pseudorandom_generator(self, pin_number):
        """Start pseudorandom generator for a specific pin"""
        try:
            # Get pin configuration
            pin_config = self.pin_configs.get(pin_number, {})
            min_interval = pin_config.get("min_interval", 1.0)
            max_interval = pin_config.get("max_interval", 10.0)
            pulse_duration = pin_config.get("pulse_duration", 0.01)

            self.logger.info(f"Min interval {min_interval}, max interval {max_interval}, pulse_duration {pulse_duration}")
            
            # Find the pin object
            pin_obj = None
            for pin in self.output_pins:
                if pin.pin.number == pin_number:
                    pin_obj = pin
                    break
            
            if not pin_obj:
                self.logger.error(f"Could not find pin object for pseudorandom pin {pin_number}")
                return False
            
            self.logger.info(f"Started pseudorandom generator on pin {pin_number}")
            
            # Start generator thread
            thread = threading.Thread(
                target=self._pseudorandom_worker,
                args=(pin_obj, pin_number, min_interval, max_interval, pulse_duration),
                daemon=True
            )
            thread.start()
            self.generator_threads[pin_number] = thread
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting pseudorandom generator on pin {pin_number}: {e}")
            return False
    

    def _pseudorandom_worker(self, pin_obj, pin_number, min_interval, max_interval, pulse_duration):
        """Worker thread for pseudorandom pulse generation"""
        try:
            self.logger.info(f"Pseudorandom worker started on pin {pin_number}")
            
            while self.recording:
                # Generate random interval
                interval = random.uniform(min_interval, max_interval)
                
                # Wait for the interval
                time.sleep(interval)
                
                # Check if still recording
                if not self.recording:
                    break
                
                # Generate pulse (set low, then high)
                pin_obj.off()
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.LOW)
                time.sleep(pulse_duration)
                pin_obj.on()
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.HIGH)
                
        except Exception as e:
            self.logger.error(f"Error in pseudorandom worker for pin {pin_number}: {e}")
        finally:
            self.logger.info(f"Pseudorandom worker stopped for pin {pin_number}")
    

    def cleanup(self):
        """Clean up TTL module resources"""
        try:
            self.logger.info("Cleaning up TTL module resources")
            
            # Stop recording if active
            if self.recording:
                self.stop_recording()
            
            # Stop all pin generators
            self._stop_pin_generators()
            
            # Turn off all output pins
            for pin in self.output_pins:
                try:
                    pin.off()
                except:
                    pass
            
            # Clear pin lists
            self.input_pins.clear()
            self.output_pins.clear()
            
            # Clean up GPIO
            self._cleanup_gpio()
            
            self.logger.info("TTL module cleanup complete")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
    
    def __del__(self):
        """Destructor to ensure cleanup"""
        try:
            self.cleanup()
        except:
            pass


def main():
    ttl = TTLModule()
    ttl.start()
    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        ttl.stop()


if __name__ == '__main__':
    main()
