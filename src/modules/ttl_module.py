#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Camera Module Class

This class extends the base Module class to handle TTL-specific functionality.

Assumes input pins are normally high and go low when triggered.

Author: Andrew SG
Created: 23/05/2025
License: GPLv3
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
import random
from src.modules.module import Module
from src.modules.module_command_handler import ModuleCommandHandler
import logging
import threading
import gpiozero
import datetime

class TTLCommandHandler(ModuleCommandHandler):
    """Command handler specific to TTL functionality"""

    def handle_command(self, command: str, **kwargs):
        """Handle TTL-specific commands while preserving base functionality"""
        # Handle TTL specific commands
        match command.split()[0]:  # Split and take first word to match command
            case "start_recording":
                if "start_recording" in self.callbacks:
                    result = self.callbacks['start_recording']()
                    self.communication_manager.send_status(result)
                else:
                    self.logger.error("(COMMAND HANDLER) No start_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
            case "stop_recording":  
                if "stop_recording" in self.callbacks:
                    result = self.callbacks['stop_recording']()
                    self.communication_manager.send_status(result)
                else:
                    self.logger.error("(COMMAND HANDLER) No stop_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
                return
            case "list_recordings":
                if 'list_recordings' in self.callbacks:
                    try:
                        recordings = self.callbacks['list_recordings']()
                        self.communication_manager.send_status({
                            "type": "recordings_list",
                            "recordings": recordings
                        })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error listing recordings: {e}")
                        self.communication_manager.send_status({
                            "type": "recordings_list_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No list_recordings callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for listing recordings"})
                return

        # If not a camera-specific command, pass to parent class
        super().handle_command(command, **kwargs)

class TTLModule(Module):
    def __init__(self, module_type="ttl", config=None, config_file_path=None):
        # Initialize command handler first
        self.command_handler = TTLCommandHandler(
            logging.getLogger(f"{module_type}"), # is this the same as self.logger? # TODO: check this
            None,  # module_id will be set after super().__init__
            module_type,
            None,  # communication_manager will be set after super().__init__
            None,  # health_manager will be set after super().__init__
            None,  # config_manager will be set after super().__init__
            None,  # ptp_manager will be set after super().__init__
            None   # start_time will be set after super().__init__
        )

        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)

        # Update command handler with proper references
        self.command_handler.module_id = self.module_id
        self.command_handler.communication_manager = self.communication_manager
        self.command_handler.health_manager = self.health_manager
        self.command_handler.config_manager = self.config_manager
        self.command_handler.ptp_manager = self.ptp_manager
        self.command_handler.start_time = self.start_time

        # Set up TTL-specific callbacks
        self.command_handler.set_callbacks({
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
        })

        # TTL specific variables
        self.ttl_input_pins = self.config_manager.get("digital_inputs.pins")
        self.ttl_output_pins = self.config_manager.get("digital_outputs.pins")

        # Initialize GPIO
        self.output_pins = []
        self.input_pins = []

        for pin in self.ttl_input_pins:
            self.input_pins.append(gpiozero.Button(pin, bounce_time=0)) # Use a Button object to represent the input pins, set bounce time to 0 to avoid debouncing
        for pin in self.ttl_output_pins:
            self.output_pins.append(gpiozero.LED(pin))  # Use an LED object to represent the output pins
        
        # Recording variables
        self.recording_folder = self.config_manager.get("recording_folder")
        self.ttl_event_buffer = [] # Buffer to record TTL eventss - List of tuples (timestamp, pin, state)
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

        self.logger.info(f"Initialized TTL module with {len(self.input_pins)} input pins and {len(self.output_pins)} output pins")

    def handle_command(self, command: str, **kwargs):
        return self.command_handler.handle_command(command, **kwargs)

    def start_recording(self, pins="all"):
        if pins == "all":
            self.logger.info(f"Starting to record all input pins")
            self.recording_stop_time = None
            self.recording_start_time = time.time()
            self.recording = True
            self.start_recording_all_input_pins()
            result = {
                "recording_started": True,
                "recording_start_time": self.recording_start_time,
                "recording_stop_time": self.recording_stop_time, 
                "filename": f"ttl_event_buffer_{self.recording_start_time}.txt"
            }
            return result
        else:
            self.logger.info(f"Starting to record on output pin {pins}")
            self.recording_stop_time = None
            self.recording_start_time = time.time()
            self.recording = True
            self.start_recording_on_output_pin(pins)
            result = {
                "recording_started": True,
                "recording_start_time": self.recording_start_time,
                "recording_stop_time": self.recording_stop_time, 
                "filename": f"ttl_event_buffer_{self.recording_start_time}.txt"
            }
            return result

    def start_recording_all_input_pins(self):
        self.logger.info(f"Starting to record all input pins")
        for pin in self.input_pins:
            self.start_recording_on_output_pin(pin)
    
    def start_recording_on_output_pin(self, pin):
        pin.when_pressed = self._handle_input_pin_low
        pin.when_released = self._handle_input_pin_high
        self.logger.info(f"Started monitoring output pin {pin.pin}")

    def stop_recording(self):
        if not self.recording:
            self.logger.error(f"No recording to stop")
            return {
                "recording_stopped": False,
                "error": "No recording to stop"
            }
        else:
            self.logger.info(f"Stopping recording")
            self.recording_stop_time = time.time()
            self.recording = False
            self.stop_recording_all_input_pins()
            self._save_ttl_event_buffer_to_file(filename=f"{self.recording_folder}/ttl_event_buffer_{self.recording_start_time}.txt")
            result = {
                "recording_stopped": True,
                "recording_start_time": self.recording_start_time,
                "recording_stop_time": self.recording_stop_time, 
                "filename": f"ttl_event_buffer_{self.recording_start_time}.txt"
            }
            return result
    
    def stop_recording_all_input_pins(self):
        self.logger.info(f"Stopping to record all input pins")
        for pin in self.input_pins:
            self.stop_recording_on_output_pin(pin)
    
    def stop_recording_on_output_pin(self, pin):
        pin.when_pressed = None
        pin.when_released = None
        self.logger.info(f"Stopped monitoring output pin {pin.pin}")

    def _handle_input_pin_low(self, pin):
        self.ttl_event_buffer.append((time.time_ns(), pin.pin, "low"))
        self.logger.info(f"(MODULE) Input pin {pin.pin} went low (pressed)")

    def _handle_input_pin_high(self, pin):
        self.ttl_event_buffer.append((time.time_ns(), pin.pin, "high"))
        self.logger.info(f"(MODULE) Input pin {pin.pin} went high (released)") 

    def _set_output_pin_low(self, pin):
        pin.off()

    def _set_output_pin_high(self, pin):
        pin.on()

    def _print_ttl_event_buffer(self):
        self.logger.info(f"(MODULE) Logging TTL event buffer:")
        for event in self.ttl_event_buffer:
            self.logger.info(f"(MODULE) TTL event: {event}")

    def _save_ttl_event_buffer_to_file(self, filename="ttl_event_buffer.txt"):
        with open(filename, "w") as f:
            for event in self.ttl_event_buffer:
                f.write(f"{event}\n")
    
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
            self.logger.info(f"Pulse generation worker stopped for pin {pin_obj.pin.number}")
    
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
    
    def list_recordings(self):
        """List all recorded videos with metadata"""
        try:
            recordings = []
            if not os.path.exists(self.recording_folder):
                return recordings
                
            for filename in os.listdir(self.recording_folder):
                if filename.endswith(f".txt"):
                    filepath = os.path.join(self.recording_folder, filename)
                    stat = os.stat(filepath)
                    recordings.append({
                        "filename": filename,
                        # "path": filepath,
                        "size": stat.st_size,
                        "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                        # "modified": stat.st_mtime,
                        # "session_id": filename.split('.')[0]  # Extract session ID from filename
                    })
            
            # Sort by creation time, newest first
            recordings.sort(key=lambda x: x["created"], reverse=True)
            return recordings
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error listing recordings: {e}")
            raise