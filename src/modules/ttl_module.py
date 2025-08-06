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
from habitat.src.modules.command import Command
import logging
import threading
import gpiozero
import datetime
import json
from typing import Dict, Any, Optional, Callable

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

class TTLCommandHandler(Command):
    """Command handler specific to TTL functionality"""
    def __init__(self, logger, module_id, module_type, config=None, start_time=None):
        super().__init__(logger, module_id, module_type, config, start_time)
        self.logger.info("(TTL COMMAND HANDLER) Initialised")

    def handle_command(self, command: str):
        """Handle TTL-specific commands while preserving base functionality"""
        # Handle TTL specific commands
        try:
            # Parse command and parameters
            parts = command.split()
            cmd = parts[0]
            params = parts[1:] if len(parts) > 1 else []
            
            # Handle TTL-specific commands
            match cmd:
                case "update_pins":
                    self._handle_update_pins(params)
                case "get_pin_config":
                    self._handle_get_pin_config()
                case _:
                    # If not a TTL-specific command, pass to parent class
                    super().handle_command(command)
                    
        except Exception as e:
            self._handle_error(e)
    
    def _handle_update_pins(self, params):
        """Handle update_pins command"""
        self.logger.info("(TTL HANDLER) Command identified as update_pins")
        try:
            if 'update_pins' in self.callbacks:
                success = self.callbacks['update_pins'](params)
                if not success:
                    self.callbacks["send_status"]({
                        "type": "update_pins_failed",
                        "error": "Failed to update pins"
                    })
            else:
                self.logger.error("(TTL HANDLER) No update_pins callback provided")
                self.callbacks["send_status"]({
                    "type": "update_pins_failed",
                    "error": "Module not configured for updating pins"
                })
        except Exception as e:
            self.logger.error(f"(TTL HANDLER) Error updating pins: {e}")
            self.callbacks["send_status"]({
                "type": "update_pins_failed",
                "error": str(e)
            })

    def _handle_get_pin_config(self):
        """Handle get_pin_config command"""
        self.logger.info("(TTL HANDLER) Command identified as get_pin_config")
        try:
            if 'get_pin_config' in self.callbacks:
                pin_config = self.callbacks['get_pin_config']()
                if pin_config:
                    self.callbacks["send_status"]({
                        "type": "pin_config_retrieved",
                        "pin_config": pin_config
                    })
                else:
                    self.logger.error("(TTL HANDLER) No get_pin_config callback provided")
                    self.callbacks["send_status"]({
                        "type": "pin_config_retrieval_failed",
                        "error": "Module not configured for retrieving pin configuration"
                    })
            else:
                self.logger.error("(TTL HANDLER) No get_pin_config callback provided")
                self.callbacks["send_status"]({
                    "type": "pin_config_retrieval_failed",
                    "error": "Module not configured for retrieving pin configuration"
                })
        except Exception as e:
            self.logger.error(f"(TTL HANDLER) Error retrieving pin configuration: {e}")
            self.callbacks["send_status"]({
                "type": "pin_config_retrieval_failed",
                "error": str(e)
            })

class TTLModule(Module):
    def __init__(self, module_type="ttl", config=None, config_file_path=None):
        # Call the parent class constructor first
        super().__init__(module_type, config, config_file_path)
        
        # Initialize command handler after parent class
        self.command = TTLCommandHandler(
            logger=self.logger,
            module_id=self.module_id,
            module_type=module_type,
            config=self.config,
            start_time=self.start_time
        )
        
        # Set up callbacks
        self.callbacks = {}
        
        # Set up export manager callbacks
        self.export.set_callbacks({
            'get_controller_ip': lambda: self.service.controller_ip
        })

        # TTL specific variables
        self.ttl_input_pins = self.config.get("digital_inputs.pins")
        self.ttl_output_pins = self.config.get("digital_outputs.pins")

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
        self.recording_folder = self.config.get("recording_folder")
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

        # State flags (matching camera module pattern)
        self.is_recording = False
        self.is_streaming = False  # TTL doesn't stream, but keep for consistency

        # Set up TTL-specific callbacks for the command handler
        self.command.set_callbacks({
            'generate_session_id': lambda module_id: self.generate_session_id(module_id),
            'get_samplerate': lambda: self.config.get("module.samplerate", 200),
            'get_ptp_status': self.ptp.get_status,
            'get_streaming_status': lambda: self.is_streaming,
            'get_recording_status': lambda: self.is_recording,
            'send_status': lambda status: self.communication.send_status(status),
            'get_health': self.health.get_health,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'list_recordings': super().list_recordings,
            'clear_recordings': self.clear_recordings,
            'export_recordings': self.export_recordings,
            'update_pins': self.update_pins,
            'get_pin_config': self.get_pin_config,
            'get_controller_ip': lambda: self.service.controller_ip,
            'shutdown': self._shutdown,
        })

        self.logger.info(f"(TTL MODULE) Command handler callbacks: {self.command.callbacks}")
        self.logger.info(f"Initialized TTL module with {len(self.input_pins)} input pins and {len(self.output_pins)} output pins")

    def handle_command(self, command: str, **kwargs):
        return self.command.handle_command(command, **kwargs)

    def start_recording(self, experiment_name: str = None, duration: str = None, experiment_folder: str = None, controller_share_path: str = None) -> bool:
        """Start TTL event recording"""
        # Store experiment name for use in timestamps filename
        self.current_experiment_name = experiment_name
        
        # First call parent class to handle common recording setup
        filename = super().start_recording(experiment_name=experiment_name, duration=duration, experiment_folder=experiment_folder, controller_share_path=controller_share_path)
        if not filename:
            return False
        
        try:
            # Reset recording state
            self.recording_start_time = time.time()
            self.recording_stop_time = None
            self.recording = True
            self.is_recording = True
            self.ttl_event_buffer = []  # Reset event buffer
            
            # Start experiment clock and pseudorandom generators
            self._start_pin_generators()
            
            # Start monitoring all input pins
            self._start_recording_all_input_pins()

            # Send status response after successful recording start
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    "filename": filename,
                    "recording": True,
                    "session_id": self.recording_session_id
                })
            
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error starting recording: {e}")
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

    def stop_recording(self) -> bool:
        """Stop TTL event recording"""
        # First check if recording using parent class
        if not super().stop_recording():
            return False
        
        try:
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
            
            # Save TTL events to file
            if hasattr(self, 'current_experiment_name') and self.current_experiment_name:
                # Sanitize experiment name for filename (remove special characters)
                safe_experiment_name = "".join(c for c in self.current_experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_experiment_name = safe_experiment_name.replace(' ', '_')
                events_file = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_events.txt"
            else:
                events_file = f"{self.recording_folder}/{self.recording_session_id}_events.txt"
            
            self._save_ttl_event_buffer_to_file(filename=events_file)
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                # Send status response after successful recording stop
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "filename": self.current_filename,
                        "session_id": self.recording_session_id,
                        "duration": duration,
                        "event_count": len(self.ttl_event_buffer),
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully with {len(self.ttl_event_buffer)} events"
                    })
                
                return True
            else:
                self.logger.error("(MODULE) Error: recording_start_time was None")
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "status": "error",
                        "error": "Recording start time was not set."
                    })
                return False
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping recording: {e}")
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
        self.ttl_event_buffer.append((time.time_ns(), pin.pin.number, "low"))
        self.logger.info(f"(MODULE) Input pin {pin.pin} went low (pressed)")

    def _handle_input_pin_high(self, pin):
        """Handle input pin going high (released)"""
        self.ttl_event_buffer.append((time.time_ns(), pin.pin.number, "high"))
        self.logger.info(f"(MODULE) Input pin {pin.pin} went high (released)") 

    def _print_ttl_event_buffer(self):
        self.logger.info(f"(MODULE) Logging TTL event buffer:")
        for event in self.ttl_event_buffer:
            self.logger.info(f"(MODULE) TTL event: {event}")

    def _save_ttl_event_buffer_to_file(self, filename="ttl_event_buffer.txt"):
        """Save TTL event buffer to file with Excel-friendly format"""
        try:
            with open(filename, "w") as f:
                # Write header with metadata
                f.write("# TTL Event Recording\n")
                f.write(f"# Session ID: {getattr(self, 'recording_session_id', 'unknown')}\n")
                f.write(f"# Recording Start: {getattr(self, 'recording_start_time', 'unknown')}\n")
                f.write(f"# Recording Stop: {getattr(self, 'recording_stop_time', 'unknown')}\n")
                f.write(f"# Total Events: {len(self.ttl_event_buffer)}\n")
                f.write("#\n")
                
                # Write CSV header for Excel compatibility (no comment prefix)
                f.write("Timestamp_Nanoseconds,Timestamp_Seconds,Timestamp_ISO,Pin_Number,Pin_State,Event_Type,Pin_Description\n")
                
                # Write events in CSV format
                for event in self.ttl_event_buffer:
                    timestamp_ns, pin_number, state = event
                    timestamp_s = timestamp_ns / 1e9
                    
                    # Convert to ISO format for human readability
                    timestamp_iso = datetime.datetime.fromtimestamp(timestamp_s).isoformat()
                    
                    # Normalize pin number (remove 'GPIO' prefix if present)
                    if isinstance(pin_number, str) and pin_number.startswith('GPIO'):
                        pin_number = pin_number[4:]  # Remove 'GPIO' prefix
                    
                    # Determine event type and description based on pin configuration
                    event_type = "unknown"
                    pin_description = "Unknown pin"
                    
                    if str(pin_number) in self.pin_configs:
                        pin_config = self.pin_configs[str(pin_number)]
                        pin_description = pin_config.get('description', 'No description')
                        
                        if pin_config.get('type') == 'input':
                            event_type = "input"
                        elif pin_config.get('type') == 'output':
                            output_type = pin_config.get('output_type', 'standard')
                            event_type = output_type
                    else:
                        # Fallback for legacy pins
                        if pin_number in self.experiment_clock_pins:
                            event_type = "experiment_clock"
                            pin_description = "Experiment clock pin"
                        elif pin_number in self.pseudorandom_pins:
                            event_type = "pseudorandom"
                            pin_description = "Pseudorandom pin"
                        else:
                            # Check if it's an input pin
                            for pin in self.input_pins:
                                if pin.pin.number == pin_number:
                                    event_type = "input"
                                    pin_description = "Input pin"
                                    break
                            if event_type == "unknown":
                                event_type = "output"
                                pin_description = "Output pin"
                    
                    f.write(f"{timestamp_ns},{timestamp_s:.6f},{timestamp_iso},{pin_number},{state},{event_type},{pin_description}\n")
                
            self.logger.info(f"Saved {len(self.ttl_event_buffer)} TTL events to {filename}")
            
        except Exception as e:
            self.logger.error(f"Error saving TTL event buffer to {filename}: {e}")
            raise
    
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

    def update_pins(self, params):
        """Update pin configuration dynamically"""
        try:
            if not params:
                self.logger.error("No pin configuration provided for update_pins")
                return False
            
            # Parse the pin configuration from params
            # Expected format: "pin_config={\"18\":{\"type\":\"output\",\"output_type\":\"experiment_clock\"}}"
            pin_config_str = None
            for param in params:
                if param.startswith('pin_config='):
                    pin_config_str = param.split('=', 1)[1]
                    break
            
            if not pin_config_str:
                self.logger.error("No pin_config parameter found in update_pins command")
                return False
            
            # Parse JSON configuration
            try:
                pins_config = json.loads(pin_config_str)
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON in pin_config: {e}")
                return False
            
            # Update the config manager with new pin configuration
            self.config.set("pins", pins_config)
            
            # Reassign pins with new configuration
            self.assign_pins()
            
            self.logger.info(f"(TTL MODULE) Successfully updated pin configuration: {pins_config}")
            
            # Send status response
            self.communication.send_status({
                "type": "pins_updated",
                "pins_config": pins_config,
                "input_pins": self.ttl_input_pins,
                "output_pins": self.ttl_output_pins
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating pins: {e}")
            self.communication.send_status({
                "type": "pins_update_failed",
                "error": str(e)
            })
            return False

    def get_pin_config(self):
        """Get current pin configuration"""
        try:
            pins_config = self.config.get("pins", {})
            return {
                "pins_config": pins_config,
                "input_pins": self.ttl_input_pins,
                "output_pins": self.ttl_output_pins,
                "input_pin_count": len(self.input_pins),
                "output_pin_count": len(self.output_pins)
            }
        except Exception as e:
            self.logger.error(f"Error getting pin config: {e}")
            return {
                "error": str(e),
                "input_pins": self.ttl_input_pins,
                "output_pins": self.ttl_output_pins
            }

    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        super().when_controller_discovered(controller_ip, controller_port)

    def assign_pins(self):
        """Assign pins based on configuration from config file"""
        try:
            # Clean up any existing GPIO resources first
            self._cleanup_gpio()
            
            # Get pin configuration from config
            pins_config = self.config.get("pins", {})
            
            if not pins_config:
                self.logger.warning("(TTL MODULE) No pin configuration found in config file")
                # Fall back to old method if no config
                self._assign_pins_legacy()
                return
            
            self.logger.info(f"(TTL MODULE) Assigning pins from config: {pins_config}")
            
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
                    pin_type = pin_config.get("type", "input")
                    
                    # Store pin configuration
                    self.pin_configs[pin_number] = pin_config
                    
                    if pin_type == "input":
                        # Create input pin (Button object) with proper error handling
                        try:
                            pin_obj = gpiozero.Button(pin_number, bounce_time=0, pull_up=True)
                            self.input_pins.append(pin_obj)
                            input_pins_assigned.append(pin_number)
                            self.logger.info(f"(TTL MODULE) Assigned input pin {pin_number}")
                        except Exception as e:
                            self.logger.error(f"(TTL MODULE) Failed to assign input pin {pin_number}: {e}")
                            # Try to clean up and retry once
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.Button(pin_number, bounce_time=0, pull_up=True)
                                self.input_pins.append(pin_obj)
                                input_pins_assigned.append(pin_number)
                                self.logger.info(f"(TTL MODULE) Successfully assigned input pin {pin_number} after retry")
                            except Exception as e2:
                                self.logger.error(f"(TTL MODULE) Failed to assign input pin {pin_number} after retry: {e2}")
                        
                    elif pin_type == "output":
                        # Create output pin (LED object) with proper error handling
                        try:
                            pin_obj = gpiozero.LED(pin_number)
                            self.output_pins.append(pin_obj)
                            output_pins_assigned.append(pin_number)
                            
                            # Set initial state based on output type
                            output_type = pin_config.get("output_type", "standard")
                            if output_type == "experiment_clock":
                                # For experiment clock, start in low state (will go high when recording starts)
                                pin_obj.off()
                                self.experiment_clock_pins.append(pin_number)
                                self.logger.info(f"(TTL MODULE) Assigned output pin {pin_number} as experiment_clock (initial state: LOW)")
                            elif output_type == "pseudorandom":
                                # For pseudorandom, start in low state
                                pin_obj.off()
                                self.pseudorandom_pins.append(pin_number)
                                self.logger.info(f"(TTL MODULE) Assigned output pin {pin_number} as pseudorandom (initial state: LOW)")
                            else:
                                # Standard output, start in low state
                                pin_obj.off()
                                self.logger.info(f"(TTL MODULE) Assigned output pin {pin_number} as standard (initial state: LOW)")
                        except Exception as e:
                            self.logger.error(f"(TTL MODULE) Failed to assign output pin {pin_number}: {e}")
                            # Try to clean up and retry once
                            self._cleanup_gpio()
                            try:
                                pin_obj = gpiozero.LED(pin_number)
                                self.output_pins.append(pin_obj)
                                output_pins_assigned.append(pin_number)
                                pin_obj.off()
                                self.logger.info(f"(TTL MODULE) Successfully assigned output pin {pin_number} after retry")
                            except Exception as e2:
                                self.logger.error(f"(TTL MODULE) Failed to assign output pin {pin_number} after retry: {e2}")
                            
                    else:
                        self.logger.warning(f"(TTL MODULE) Unknown pin type '{pin_type}' for pin {pin_number}")
                        
                except ValueError as e:
                    self.logger.error(f"(TTL MODULE) Invalid pin number '{pin_number}': {e}")
                except Exception as e:
                    self.logger.error(f"(TTL MODULE) Error assigning pin {pin_number}: {e}")
            
            # Update the pin lists for backward compatibility
            self.ttl_input_pins = input_pins_assigned
            self.ttl_output_pins = output_pins_assigned
            
            self.logger.info(f"(TTL MODULE) Pin assignment complete: {len(self.input_pins)} input pins, {len(self.output_pins)} output pins")
            
        except Exception as e:
            self.logger.error(f"(TTL MODULE) Error in assign_pins: {e}")
            # Fall back to legacy method
            self._assign_pins_legacy()
    
    def _cleanup_gpio(self):
        """Clean up GPIO resources to prevent conflicts"""
        try:
            # Close existing pin factory
            gpiozero.Device.pin_factory.close()
            # Small delay to ensure cleanup
            time.sleep(0.1)
        except Exception as e:
            self.logger.debug(f"(TTL MODULE) GPIO cleanup error (non-critical): {e}")
    
    def _assign_pins_legacy(self):
        """Legacy pin assignment - fallback when no config file is available"""
        self.logger.info("(TTL MODULE) Using legacy pin assignment")
        
        # Clean up GPIO first
        self._cleanup_gpio()
        
        # Handle None values from config
        if self.ttl_input_pins is None:
            self.ttl_input_pins = []
        if self.ttl_output_pins is None:
            self.ttl_output_pins = []
        
        # Assign input pins
        for pin in self.ttl_input_pins:
            try:
                pin_obj = gpiozero.Button(pin, bounce_time=0, pull_up=True)
                self.input_pins.append(pin_obj)
                self.logger.info(f"Legacy: Assigned input pin {pin}")
            except Exception as e:
                self.logger.error(f"Legacy: Failed to assign input pin {pin}: {e}")
        
        # Assign output pins
        for pin in self.ttl_output_pins:
            try:
                pin_obj = gpiozero.LED(pin)
                pin_obj.off()  # Start in low state
                self.output_pins.append(pin_obj)
                self.logger.info(f"Legacy: Assigned output pin {pin} (initial state: LOW)")
            except Exception as e:
                self.logger.error(f"Legacy: Failed to assign output pin {pin}: {e}")

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
            duty_cycle_str = pin_config.get("duty_cycle", "50%")
            
            # Parse duty cycle (e.g., "75%" -> 0.75)
            duty_cycle = float(duty_cycle_str.replace("%", "")) / 100.0
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
            self.ttl_event_buffer.append((time.time_ns(), pin_number, "high"))
            self.logger.info(f"Started experiment clock on pin {pin_number} with {duty_cycle_str} duty cycle")
            
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
                self.ttl_event_buffer.append((time.time_ns(), pin_number, "high"))
                time.sleep(high_time)
                
                # Check if still recording
                if not self.recording:
                    break
                
                # Low phase
                pin_obj.off()
                self.ttl_event_buffer.append((time.time_ns(), pin_number, "low"))
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
                self.ttl_event_buffer.append((time.time_ns(), pin_number, "low"))
                time.sleep(pulse_duration)
                pin_obj.on()
                self.ttl_event_buffer.append((time.time_ns(), pin_number, "high"))
                
        except Exception as e:
            self.logger.error(f"Error in pseudorandom worker for pin {pin_number}: {e}")
        finally:
            self.logger.info(f"Pseudorandom worker stopped for pin {pin_number}")
    
    def cleanup(self):
        """Clean up TTL module resources"""
        try:
            self.logger.info("(TTL MODULE) Cleaning up TTL module resources")
            
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
            
            self.logger.info("(TTL MODULE) TTL module cleanup complete")
            
        except Exception as e:
            self.logger.error(f"(TTL MODULE) Error during cleanup: {e}")
    
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