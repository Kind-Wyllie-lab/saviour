#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - TTL Module 

This class extends the base Module class to handle TTL-specific functionality.

Assumes input pins are normally high and go low when triggered.

Author: Andrew SG
Created: 23/05/2025
"""

import collections
import os
import sys
import time
import random
import logging
import threading
import gpiozero
import datetime
import json
import numpy as np
import cv2
from flask import Flask, Response
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import atexit # Add GPIO cleanup at module level
import signal

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command


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

        # Rolling pin state buffers: {pin_number: deque of bool (True = electrical HIGH)}
        # Must be initialised before assign_pins() which populates them
        self.MONITOR_COLS = 500   # samples; at 25 Hz ≈ 20 s of history
        self.MONITOR_HZ   = 25
        self.pin_state_buffers = {}
        self.pin_state_lock = threading.Lock()

        # Assign pins from config
        self.assign_pins()

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

        # Per-pin test state: {pin_number: threading.Event (stop flag)}
        self._pin_test_stop_flags: dict = {}

        # Monitoring stream state
        self.monitoring_app = Flask(__name__)
        self.monitoring_server = None
        self.monitoring_server_thread = None
        self.should_stop_monitoring = False
        self.monitor_sample_thread = None

        self._register_monitoring_routes()

        # Set up TTL-specific callbacks for the command handler
        self.ttl_commands = {
            "test_pin": self.test_pin,
        }
        self.command.set_commands(self.ttl_commands) # Append new TTL callbacks

        self.logger.info(f"Initialized TTL module with {len(self.input_pins)} input pins and {len(self.output_pins)} output pins")


    # def handle_command(self, command: str, **kwargs):
    #     return self.command.handle_command(command, **kwargs)


    def _start_recording(self) -> bool:
        """Start TTL event recording"""
        # Store experiment name for use in timestamps filename
        
        try:
            # Cancel any in-progress pin tests before starting a real recording
            self._stop_all_pin_tests()

            # Reset recording state
            self.recording_start_time = time.time()
            self.recording_stop_time = None
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


    def _get_ttl_filename(self) -> str:
        """Build the CSV filename for the current recording segment."""
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        return f"{self.facade.get_filename_prefix()}_({self.facade.get_segment_id()}_{strtime}).csv"


    def _start_new_recording(self):
        """Open the initial TTL events CSV and start monitoring all pins."""
        # Set before starting generator threads so their while-loops don't exit immediately.
        # The base Recording class sets recording.is_recording=True only after this method
        # returns, but the workers need to see the flag as True when they first check it.
        self.is_recording = True

        filename = self._get_ttl_filename()
        self.current_ttl_events_filename = filename
        self.facade.add_session_file(filename)
        self._open_ttl_file(filename)
        self._start_pin_generators()
        self._start_recording_all_input_pins()


    def _start_next_recording_segment(self):
        """Stage and close the current CSV, then open a new one for the next segment.

        Input pin callbacks and generators keep running across segments — no restart needed.
        """
        self.facade.stage_file_for_export(self.current_ttl_events_filename)
        self._close_ttl_event_file()

        filename = self._get_ttl_filename()
        self.current_ttl_events_filename = filename
        self.facade.add_session_file(filename)
        self._open_ttl_file(filename)

    
    def configure_module_special(self, updated_keys: list):
        """Re-assign pins and refresh the MJPEG stream buffers when TTL config changes."""
        if any(k.startswith("ttl.") for k in updated_keys):
            self.logger.info("TTL config changed — re-assigning pins")
            self.assign_pins()


    def test_pin(self, pin: int, duration: float = 5.0) -> dict:
        """Run a 2 Hz pulse train on an output pin for *duration* seconds.

        Useful for validating that a signal can be seen on downstream hardware
        without starting a full recording session.

        Returns an error if the pin is not configured as an output.
        """
        pin_number = int(pin)
        duration = float(duration)

        if pin_number not in self.pin_configs:
            return {"result": "error", "message": f"Pin {pin_number} is not configured"}

        pin_obj = next((p for p in self.output_pins if p.pin.number == pin_number), None)
        if pin_obj is None:
            return {
                "result": "error",
                "message": f"Pin {pin_number} is not an output pin — cannot drive a test pulse",
            }

        # Cancel any in-progress test on this pin
        existing = self._pin_test_stop_flags.pop(pin_number, None)
        if existing is not None:
            existing.set()

        stop_event = threading.Event()
        self._pin_test_stop_flags[pin_number] = stop_event

        def _run_test(p, stop, dur):
            self.logger.info(f"test_pin: starting {dur}s pulse train on GPIO {pin_number}")
            try:
                deadline = time.monotonic() + dur
                while time.monotonic() < deadline and not stop.is_set():
                    p.on()
                    stop.wait(0.25)
                    if stop.is_set():
                        break
                    p.off()
                    stop.wait(0.25)
            finally:
                p.off()
                self._pin_test_stop_flags.pop(pin_number, None)
                self.logger.info(f"test_pin: finished pulse train on GPIO {pin_number}")

        t = threading.Thread(
            target=_run_test,
            args=(pin_obj, stop_event, duration),
            daemon=True,
            name=f"test-pin-{pin_number}",
        )
        t.start()
        return {"result": "success", "message": f"Running 2 Hz test pulse on GPIO {pin_number} for {duration}s"}


    def _stop_all_pin_tests(self):
        """Cancel any in-progress pin tests (called on recording start and cleanup)."""
        for stop_event in list(self._pin_test_stop_flags.values()):
            stop_event.set()
        self._pin_test_stop_flags.clear()


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

            # Return all output pins to resting (inactive) state
            self._set_all_output_pins_inactive()
            
            # Update recording state
            self.recording_stop_time = time.time()
            self.is_recording = False

            # self.add_session_file(events_file)
            self._close_ttl_event_file(filename=self.current_ttl_events_filename)
            
            if self.recording.recording_start_time is None:
                self.logger.warning("recording_start_time was None at stop — module may not have started recording")

            # Send status response after successful recording stop
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_stopped",
                    "session_id": self.recording_session_id,
                    "status": "success",
                    "recording": False,
                    "message": "Recording completed successfully"
                })
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": str(e)
                })
            return False
    



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


    def _open_ttl_file(self, filename: str):
        """Open a TTL events CSV file for writing and write the column header."""
        self.logger.info(f"Opening TTL events file: {filename}")
        try:
            self._ttl_file_handle = open(filename, "w", buffering=1)  # line-buffered
            self._ttl_file_handle.write("Timestamp_nanoseconds,pin_number,pin_mode,pin_state,pin_description\n")
        except Exception as e:
            self.logger.error(f"Failed to open TTL events file {filename}: {e}")
            self._ttl_file_handle = None


    def _create_ttl_file(self):
        """Legacy helper used by _start_recording. Delegates to _open_ttl_file."""
        self.current_ttl_events_filename = f"{self.facade.get_filename_prefix()}_events.csv"
        self.facade.add_session_file(self.current_ttl_events_filename)
        self._open_ttl_file(self.current_ttl_events_filename)


    def _write_ttl_event(self, timestamp_ns: int, pin_number: int, state: TTLValue): 
        """Write a TTL event to file"""
        if self._ttl_file_handle:
            self._ttl_file_handle.write(f'{timestamp_ns},{pin_number},{self.pin_configs[pin_number].get("mode")},{state},{self.pin_configs[pin_number].get("description")}\n')


    def _close_ttl_event_file(self, filename=None):
        """Flush and close the current TTL events file."""
        try:
            if self._ttl_file_handle:
                self._ttl_file_handle.flush()
                self._ttl_file_handle.close()
                self._ttl_file_handle = None
                self.logger.info(f"Closed TTL events file: {filename or self.current_ttl_events_filename}")
        except Exception as e:
            self.logger.warning(f"Error closing TTL events file: {e}")
            self._ttl_file_handle = None


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
            
            active_logic = self.config.get("ttl.active_logic", "active_low")
            pull_up = (active_logic == "active_low")
            self.logger.info(f"Assigning pins with active_logic='{active_logic}' (pull_up={pull_up})")

            for pin_number, pin_config in pins_config.items():
                try:
                    pin_number = int(pin_number)
                    pin_type = pin_config.get("mode")

                    # Store pin configuration
                    self.pin_configs[pin_number] = pin_config

                    if pin_type == "input":
                        # Create input pin (Button object) with proper error handling
                        try:
                            pin_obj = gpiozero.Button(pin_number, bounce_time=0, pull_up=pull_up)
                            self.input_pins.append(pin_obj)
                            input_pins_assigned.append(pin_number)
                            self.logger.info(f"Assigned input pin {pin_number}")
                        except Exception as e:
                            self.logger.error(f"Failed to assign input pin {pin_number}: {e}")
                            # Try to clean up and retry once
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.Button(pin_number, bounce_time=0, pull_up=pull_up)
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
                            self._set_output_inactive(pin_obj)
                            self.pseudorandom_pins.append(pin_number)
                            self.logger.info(f"Assigned output pin {pin_number} as pseudorandom (initial state: inactive)")
                        except Exception as e:
                            self.logger.error(f"Failed to assign output pin {pin_number}: {e}")
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.LED(pin_number)
                                self.output_pins.append(pin_obj)
                                output_pins_assigned.append(pin_number)
                                self._set_output_inactive(pin_obj)
                                self.logger.info(f"Successfully assigned output pin {pin_number} after retry")
                            except Exception as e2:
                                self.logger.error(f"Failed to assign output pin {pin_number} after retry: {e2}")

                    elif pin_type == "experiment_clock":
                        try:
                            pin_obj = gpiozero.LED(pin_number)
                            self.output_pins.append(pin_obj)
                            output_pins_assigned.append(pin_number)
                            self._set_output_inactive(pin_obj)
                            self.experiment_clock_pins.append(pin_number)
                            self.logger.info(f"Assigned output pin {pin_number} as experiment_clock (initial state: inactive)")
                        except Exception as e:
                            self.logger.error(f"Failed to assign output pin {pin_number}: {e}")
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.LED(pin_number)
                                self.output_pins.append(pin_obj)
                                output_pins_assigned.append(pin_number)
                                self._set_output_inactive(pin_obj)
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

            # Sync monitoring buffers: add new pins, remove deassigned ones
            all_assigned = set(input_pins_assigned + output_pins_assigned)
            with self.pin_state_lock:
                for pn in all_assigned:
                    if pn not in self.pin_state_buffers:
                        self.pin_state_buffers[pn] = collections.deque(maxlen=self.MONITOR_COLS)
                for pn in list(self.pin_state_buffers.keys()):
                    if pn not in all_assigned:
                        del self.pin_state_buffers[pn]

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
            self.is_recording = False
            
            # Give threads a moment to exit naturally and execute finally blocks
            time.sleep(0.1)  # 100ms should be enough for daemon threads to check the flag and cleanup
            
            # Clear thread references
            self.generator_threads.clear()
            self.logger.info("Stopped all pin generators")
            
        except Exception as e:
            self.logger.error(f"Error stopping pin generators: {e}")
    

    def _set_output_active(self, pin_obj) -> None:
        """Drive pin to its electrically active state."""
        active_logic = self.config.get("ttl.active_logic", "active_low")
        if active_logic == "active_low":
            pin_obj.off()   # active = LOW
        else:
            pin_obj.on()    # active = HIGH

    def _set_output_inactive(self, pin_obj) -> None:
        """Drive pin to its electrically inactive (resting) state."""
        active_logic = self.config.get("ttl.active_logic", "active_low")
        if active_logic == "active_low":
            pin_obj.on()    # inactive = HIGH
        else:
            pin_obj.off()   # inactive = LOW

    def _set_all_output_pins_inactive(self):
        """Set all output pins to their resting (inactive) electrical state."""
        try:
            for pin in self.output_pins:
                try:
                    self._set_output_inactive(pin)
                    self.logger.info(f"Set output pin {pin.pin.number} to inactive state")
                except Exception as e:
                    self.logger.error(f"Error setting pin {pin.pin.number} to inactive: {e}")
            self.logger.info(f"Set all {len(self.output_pins)} output pins to inactive state")
        except Exception as e:
            self.logger.error(f"Error setting output pins to inactive: {e}")
    

    def _start_experiment_clock(self, pin_number):
        """Start experiment clock generator for a specific pin"""
        try:
            # Get pin configuration
            pin_config = self.pin_configs.get(pin_number, {})
            duty_cycle = float(pin_config.get("duty_cycle", 0.5))
            period = float(pin_config.get("period", 1.0))
            self.logger.info(f"Duty cycle for experiment clock {duty_cycle} from config")
            
            # Find the pin object
            pin_obj = None
            for pin in self.output_pins:
                if pin.pin.number == pin_number:
                    pin_obj = pin
                    break
            
            if not pin_obj:
                self.logger.error(f"Could not find pin object for experiment clock pin {pin_number}")
                return False
            
            # Set initial state to active (clock is active as soon as recording begins)
            self._set_output_active(pin_obj)
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
            
            while self.is_recording:
                # Calculate timing
                high_time = period * duty_cycle
                low_time = period * (1.0 - duty_cycle)
                
                # Active phase
                self._set_output_active(pin_obj)
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.HIGH)
                time.sleep(high_time)

                if not self.is_recording:
                    break

                # Inactive phase
                self._set_output_inactive(pin_obj)
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.LOW)
                time.sleep(low_time)

        except Exception as e:
            self.logger.error(f"Error in experiment clock worker for pin {pin_number}: {e}")
        finally:
            self._set_output_inactive(pin_obj)
            self.logger.info(f"Experiment clock worker stopped for pin {pin_number}, returned to inactive state")
    

    def _start_pseudorandom_generator(self, pin_number):
        """Start pseudorandom generator for a specific pin"""
        try:
            # Get pin configuration
            pin_config = self.pin_configs.get(pin_number, {})
            min_interval = float(pin_config.get("min_interval", 1.0))
            max_interval = float(pin_config.get("max_interval", 10.0))
            pulse_duration = float(pin_config.get("pulse_duration", 0.01))

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
            
            while self.is_recording:
                # Generate random interval
                interval = random.uniform(min_interval, max_interval)
                
                # Wait for the interval
                time.sleep(interval)
                
                # Check if still recording
                if not self.is_recording:
                    break
                
                # Generate pulse: active for pulse_duration, then return to inactive
                self._set_output_active(pin_obj)
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.HIGH)
                time.sleep(pulse_duration)
                self._set_output_inactive(pin_obj)
                self._write_ttl_event(time.time_ns(), pin_number, TTLValue.LOW)

        except Exception as e:
            self.logger.error(f"Error in pseudorandom worker for pin {pin_number}: {e}")
        finally:
            self._set_output_inactive(pin_obj)
            self.logger.info(f"Pseudorandom worker stopped for pin {pin_number}, returned to inactive state")
    

    """Monitoring stream"""

    def _get_electrical_high(self, pin_obj, is_input: bool) -> bool:
        """Return True if the pin is electrically HIGH."""
        active_logic = self.config.get("ttl.active_logic", "active_low")
        if is_input:
            # Button.is_pressed = True when active.
            # active_low: active = LOW → electrical HIGH = not is_pressed
            # active_high: active = HIGH → electrical HIGH = is_pressed
            return not pin_obj.is_pressed if active_logic == "active_low" else pin_obj.is_pressed
        else:
            # LED.is_lit = True when HIGH
            return pin_obj.is_lit

    def _sample_pins(self) -> None:
        """Background thread: poll all pins at MONITOR_HZ, fill state buffers."""
        interval = 1.0 / self.MONITOR_HZ
        while not self.should_stop_monitoring:
            input_pairs  = [(p, True)  for p in self.input_pins]
            output_pairs = [(p, False) for p in self.output_pins]
            with self.pin_state_lock:
                for pin_obj, is_input in input_pairs + output_pairs:
                    pn = pin_obj.pin.number
                    if pn in self.pin_state_buffers:
                        try:
                            self.pin_state_buffers[pn].append(
                                self._get_electrical_high(pin_obj, is_input)
                            )
                        except Exception:
                            self.pin_state_buffers[pn].append(False)
            time.sleep(interval)

    def _render_monitor_frame(self) -> bytes | None:
        """Render a logic-analyser style MJPEG frame for all assigned pins."""
        try:
            active_logic = self.config.get("ttl.active_logic", "active_low")

            with self.pin_state_lock:
                snapshot = {pn: list(buf) for pn, buf in self.pin_state_buffers.items()}

            pin_numbers = sorted(snapshot.keys())
            n_pins = len(pin_numbers)

            WIDTH    = 800
            PADDING  = 12
            STATE_W  = 110   # right-side badge
            HEADER_H = 20
            TRACE_H  = 44
            GAP_H    = 14
            ROW_H    = HEADER_H + TRACE_H + GAP_H

            height = max(n_pins * ROW_H + PADDING, 120)
            frame = np.zeros((height, WIDTH, 3), dtype=np.uint8)

            if n_pins == 0:
                cv2.putText(frame, "No pins assigned",
                            (PADDING, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (140, 140, 140), 1)
                _, jpeg = cv2.imencode('.jpg', frame)
                return jpeg.tobytes()

            trace_x0 = PADDING
            trace_x1 = WIDTH - STATE_W - PADDING
            trace_w  = trace_x1 - trace_x0

            ACTIVE_COL   = (0, 200, 80)   # green  (BGR)
            INACTIVE_COL = (65, 65, 65)

            for idx, pin_num in enumerate(pin_numbers):
                cfg  = self.pin_configs.get(pin_num, {})
                mode = cfg.get("mode", "?")
                desc = cfg.get("description", "")
                buf  = snapshot[pin_num]

                y0 = PADDING // 2 + idx * ROW_H

                # Current state
                elec_high = buf[-1] if buf else False
                is_active = elec_high if active_logic == "active_high" else not elec_high

                # ── Header ────────────────────────────────────────────────
                parts = [f"GPIO {pin_num}", mode.upper()]
                if desc:
                    parts.append(desc)
                cv2.putText(frame, "  |  ".join(parts),
                            (trace_x0, y0 + 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 180, 180), 1)

                # ── Waveform ──────────────────────────────────────────────
                ty0  = y0 + HEADER_H
                ty1  = ty0 + TRACE_H
                ymid = (ty0 + ty1) // 2
                yhi  = ty0 + 6
                ylo  = ty1 - 6

                # Background and border
                cv2.rectangle(frame, (trace_x0, ty0), (trace_x1, ty1), (22, 22, 22), -1)
                cv2.rectangle(frame, (trace_x0, ty0), (trace_x1, ty1), (55, 55, 55),  1)
                cv2.line(frame, (trace_x0, ymid), (trace_x1, ymid), (38, 38, 38), 1)

                if buf:
                    N = len(buf)
                    px_per = trace_w / N

                    prev_y = yhi if buf[0] else ylo
                    prev_x = trace_x0

                    for i, high in enumerate(buf):
                        cur_y = yhi if high else ylo
                        cur_x = trace_x0 + int((i + 1) * px_per)
                        seg_active = high if active_logic == "active_high" else not high
                        col = ACTIVE_COL if seg_active else INACTIVE_COL

                        cv2.line(frame, (prev_x, prev_y), (cur_x, prev_y), col, 2)
                        if cur_y != prev_y:
                            cv2.line(frame, (cur_x, prev_y), (cur_x, cur_y), col, 2)
                        prev_y = cur_y
                        prev_x = cur_x

                # ── State badge ───────────────────────────────────────────
                bx0 = WIDTH - STATE_W
                bx1 = WIDTH - PADDING // 2
                badge_col = ACTIVE_COL if is_active else INACTIVE_COL
                cv2.rectangle(frame, (bx0, ty0), (bx1, ty1), badge_col, -1)
                badge_txt = "ACTIVE" if is_active else "IDLE"
                (tw, th), _ = cv2.getTextSize(badge_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                cv2.putText(frame, badge_txt,
                            (bx0 + (bx1 - bx0 - tw) // 2, ty0 + (TRACE_H + th) // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)

                # ── Row separator ─────────────────────────────────────────
                if idx < n_pins - 1:
                    sep_y = ty1 + GAP_H // 2
                    cv2.line(frame, (0, sep_y), (WIDTH, sep_y), (45, 45, 45), 1)

            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return jpeg.tobytes()

        except Exception as e:
            self.logger.error(f"TTL frame render error: {e}")
            return None

    def _generate_monitor_frames(self):
        """MJPEG generator — yields frames at ~20 fps."""
        while not self.should_stop_monitoring:
            frame = self._render_monitor_frame()
            if frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    frame +
                    b"\r\n"
                )
            time.sleep(0.05)

    def _register_monitoring_routes(self):
        @self.monitoring_app.route('/')
        def index():
            return "TTL Monitoring Server"

        @self.monitoring_app.route('/video_feed')
        def video_feed():
            return Response(
                self._generate_monitor_frames(),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )

    def run_monitoring_server(self, port: int = 8082) -> None:
        try:
            from werkzeug.serving import make_server
            self.monitoring_server = make_server('0.0.0.0', port, self.monitoring_app, threaded=True)
            self.logger.info(f"TTL monitoring server listening on port {port}")
            self.monitoring_server.serve_forever()
        except Exception as e:
            self.logger.error(f"TTL monitoring server error: {e}")
            self.monitoring_server = None
            self.is_streaming = False

    def start_streaming(self) -> bool:
        """Start the MJPEG monitoring stream and pin sampling thread."""
        try:
            if self.is_streaming:
                self.logger.warning("TTL monitoring stream already running")
                return False

            port = self.config.get("monitoring.port", 8082)
            self.should_stop_monitoring = False

            self.monitor_sample_thread = threading.Thread(
                target=self._sample_pins,
                daemon=True,
                name="ttl-monitor-sampler"
            )
            self.monitor_sample_thread.start()

            self.monitoring_server_thread = threading.Thread(
                target=self.run_monitoring_server,
                args=(port,),
                daemon=True,
                name="ttl-monitoring-server"
            )
            self.monitoring_server_thread.start()

            self.is_streaming = True
            self.logger.info(
                f"TTL monitoring stream started — "
                f"http://{getattr(self.network, 'ip', '?')}:{port}/video_feed"
            )
            return True

        except Exception as e:
            self.logger.error(f"Error starting TTL monitoring stream: {e}")
            return False

    def stop_streaming(self) -> bool:
        """Stop the MJPEG monitoring stream."""
        try:
            if not self.is_streaming:
                return False

            self.should_stop_monitoring = True

            if self.monitor_sample_thread:
                self.monitor_sample_thread.join(timeout=2)
                self.monitor_sample_thread = None

            if self.monitoring_server:
                self.monitoring_server.shutdown()
                self.monitoring_server = None

            self.is_streaming = False
            self.logger.info("TTL monitoring stream stopped")
            return True

        except Exception as e:
            self.logger.error(f"Error stopping TTL monitoring stream: {e}")
            return False

    def start(self) -> bool:
        """Start the TTL module."""
        try:
            if not super().start():
                return False
            self.start_streaming()
            return True
        except Exception as e:
            self.logger.error(f"Error starting TTL module: {e}")
            return False

    def stop(self) -> bool:
        """Stop the TTL module."""
        try:
            if self.is_streaming:
                self.stop_streaming()
            return super().stop()
        except Exception as e:
            self.logger.error(f"Error stopping TTL module: {e}")
            return False

    def cleanup(self):
        """Clean up TTL module resources"""
        try:
            self.logger.info("Cleaning up TTL module resources")

            # Cancel any in-progress pin tests
            self._stop_all_pin_tests()

            # Stop recording if active
            if self.is_recording:
                self.stop_recording()
            
            # Stop all pin generators
            self._stop_pin_generators()
            
            # Return all output pins to inactive state before cleanup
            for pin in self.output_pins:
                try:
                    self._set_output_inactive(pin)
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
