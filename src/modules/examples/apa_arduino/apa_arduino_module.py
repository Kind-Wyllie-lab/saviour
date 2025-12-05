#!/usr/env/bin python
"""
SAVIOUR System - APA Arduino Module Class

This class extends the base Module class to handle arduino-specific functionality for the APA test rig.

It is used to control a Pololu G2 Motor Controller with encoder for speed control and a shock generator.

@author: Andrew SG
@date: 03/07/2025
"""

import logging
import sys
import os
import time
import json
import threading
import csv
from datetime import datetime
from typing import Optional
import serial.tools.list_ports

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check
from protocol import Protocol
from motor import Motor
from shock import Shocker

class APAModule(Module):
    def __init__(self, module_type="apa_arduino"):
        super().__init__(module_type)
        self.description = "Module for controlling the APA rig, including rotating the arena and using the shock grid."

        # Update config 
        self.config.load_module_config("apa_arduino_config.json")

        # List of arduino types we expect to find
        self.arduino_types: list[str] = ["motor_arduino", "shock_arduino"]
        
        # Store found arduinos and their ports
        self.arduino_ports: Dict[str, str] = {}  # Maps arduino_type to port
        self.connected_arduinos: Dict[str, Protocol] = {} # Maps arduino_type to a Protocol which implements a protocol around a serial connection
        self.motor: Motor = None
        self.shock: Shocker = None
        self._find_arduino_ports()

        # Sending state to controller
        self.send_state_period: float = 0.2 # Send state every this many seconds
        self.last_sent_state: int = time.time()
        self.send_state_thread: threading.Thread = None

        # Recording-specific variables
        self._shock_file_handle = None
        self._time_series_file_handle = None
        self.recording_shocks: bool = False
        self.recording_thread: threading.Thread = None
        self.recording_stop_flag: threading.Event = threading.Event()
        self.recording_start_time: int = None
        self.data_sampling_rate: int = 1  # Hz - how often to sample motor data

        self.module_checks = [
            self._check_motor,
            self._check_shocker,
            self._check_shock_grid_fault,
            self._check_shock_grid_active,
            self._check_shocks_not_above_50
        ]

        self.apa_arduino_commands = {
            "activate_shock": self._activate_shock,
            "deactivate_shock": self._deactivate_shock,
            "start_motor": self._start_motor,
            "stop_motor": self._stop_motor,
            "reset_pulse_counter": self._reset_pulse_counter
        }

        self.command.set_commands(self.apa_arduino_commands)
    

    """Arduino Discovery methods"""
    def _initialize_arduino(self, arduino_type: str, protocol_instance: Protocol) -> None:
        """Initialize the specified arduino"""
        self.logger.info(f"Initializing {arduino_type}")
        if arduino_type.lower() == "motor": # TODO: Use an ENUM for type? Maybe rename it arduino_role as well?
            self.motor = Motor(protocol_instance, self.config)
            self.motor.start()
                
        if arduino_type.lower() == "shock": 
            self.shock = Shocker(protocol_instance, self.config)
            self.shock.start()

        if self.motor and self.shock:
            self.handle_system_ready()


    def handle_system_ready(self):
        """Called when both arduino are discovered."""
        self.logger.info("Both arduino initialized.")
        self.set_arduino_callbacks()
        self.send_state_thread = threading.Thread(target=self.send_state_loop, daemon=True)
        self.send_state_thread.start()
        self.configure_module([])


    def _find_arduino_ports(self):
        self.logger.info("Searching for connected Arduino.")
        available_ports = self._get_available_ports()
        if not available_ports:
            self.logger.info("No serial ports found!")
            return
        available_ports = self._validate_available_ports(available_ports) # Validate that ports begin with ttyACM
        for port_info in available_ports:
            self._test_port_identity(port_info)


    def _get_available_ports(self) -> list:
        """Return the available serial ports."""
        return list(serial.tools.list_ports.comports())
                

    def _validate_available_ports(self, ports: list) -> list:
        """Remove any ports that do not begin with /dev/ttyACM"""
        for port in ports:
            self.logger.info(f"  - {port.device}: {port.description}")
            if not port.device.startswith("/dev/ttyACM"):
                self.logger.info(f"Removing port {port} as it does not match format /dev/ttyACM")
                ports.remove(port)
        return ports


    def _test_port_identity(self, port_info) -> None:
        """Create a protocol object to find identity of arduino"""
        self.logger.info(f"Checking {port_info} for an Arduino")
        test_protocol = Protocol(port=port_info.device, on_identity=self.handle_identity).start()

            
    def handle_identity(self, protocol: Protocol, identity: str) -> None:
        """
        Callback to be registered with a Protocol object. 
        Once identity has been discovered, return it here.
        """
        self.logger.info(f"{identity} found on {protocol.port}")
        self.arduino_ports[identity] = protocol.port
        self.connected_arduinos[identity] = protocol
        self._initialize_arduino(identity, protocol)
        self.logger.info(f"Connected arduinos: {list(self.connected_arduinos.keys())}")


    """Commands from controller"""
    @command()
    def _activate_shock(self):
        if self.shock:
            self.shock.activate_shock()
        else:
            self.logger.warning("Activate shock called but no shocker connected!")


    @command()
    def _deactivate_shock(self):
        if self.shock:
            self.shock.deactivate_shock()
        else:
            self.logger.warning("Deactivate shock called but no shocker connected!")


    @command()
    def _start_motor(self):
        if self.motor:
            self.motor.start_motor()
        else:
            self.logger.warning("Start motor called but no motor connected!")


    @command()
    def _stop_motor(self):
        if self.motor:
            self.motor.stop_motor()
        else:
            self.logger.warning("Stop motor called but no motor connected!")
    

    def _reset_pulse_counter(self):
        if self.shock:
            self.shock.reset_pulse_counter()
        else: 
            self.logger.warning("Reset pulse counter called but no shocker connected!")

    """Self Check"""
    def _perform_module_specific_checks(self) -> tuple[bool, str]:
        self.logger.info(f"Performing {self.module_type} specific checks")
        for check in self.module_checks:
            self.logger.info(f"Running {check.__name__}")
            result, message = check()
            if result == False:
                self.logger.info(f"A check failed: {check.__name__}, {message}")
                return False, message
                break # Exit loop on first failed check
        if result == False:
            return result, message
        else:
            return True, "No implementation yet..."


    @check()
    def _check_motor(self) -> tuple[bool, str]:
        if not self.motor:
            return False, "No motor found"
        else:
            return True, "Motor connected"
    

    @check()
    def _check_shocker(self) -> tuple[bool, str]:
        if not self.shock:
            return False, "No shocker found"
        else:
            return True, "Shocker connected" 


    @check()
    def _check_shock_grid_fault(self) -> tuple[bool, str]:
        try:
            t0 = time.time()
            status, message = self.shock.run_grid_test()
            self.logger.info(f"Shock grid test completed in {time.time() - t0}s")
            if status == True:
                return True, "No grid fault detected"
            else:
                return False, message

        except Exception as e:
            return False, f"Error checking grid fault: {e}"


    @check()
    def _check_shock_grid_active(self) -> tuple[bool, str]:
        if self.shock.shock_activated:
            return False, "Shocks are active! Please deactivate and try again."
        else:
            return True, "No shock sequence active."
    

    @check()
    def _check_shocks_not_above_50(self) -> tuple[bool, str]:
        if self.shock.attempted_shocks >= 50 or self.shock.attempted_shocks_from_arduino >= 50:
            return False, "Have already delivered limit of 50 shocks per trial - please manually reset pulse counter (GUI button)"
        else:
            return True, ""
            

    # TODO: Checks to make sure RPM, shocks etc are set?


    """Handle grid state and communicate it to frontend"""
    def set_arduino_callbacks(self):
        self.logger.info("Setting arduino callbacks")
        self.shock.on_shock_started_being_attempted = self.on_shock_started_being_attempted
        self.shock.on_shock_stopped_being_attempted = self.on_shock_stopped_being_attempted
        self.shock.on_shock_started_being_delivered = self.on_shock_started_being_delivered
        self.shock.on_shock_stopped_being_delivered = self.on_shock_stopped_being_delivered
        
    def on_shock_started_being_attempted(self, timestamp: int):
        if self.recording_shocks:
            self._write_shock_event(timestamp, "SENDING_SHOCK")
        # self.logger.info(f"Attempting shock at {time.time()}, total attempted: {self.shock.attempted_shocks} arduino reports {self.shock.attempted_shocks_from_arduino}")

    def on_shock_stopped_being_attempted(self, timestamp: int):
        if self.recording_shocks:
            self._write_shock_event(timestamp, "STOPPING_SHOCK")
        self.logger.info(f"Stopped attempting shock at {time.time()}")


    def on_shock_started_being_delivered(self, timestamp: int):
        if self.recording_shocks:
            self._write_shock_event(timestamp, "SHOCK_DELIVERY")
        self.logger.info(f"Delivered shock at {time.time()}, total delivered {self.shock.delivered_shocks}")
        status = {
            "type": "shock_started_being_delivered"
        }
        self.communication.send_status(status)

    
    def on_shock_stopped_being_delivered(self, timestamp: int):
        if self.recording_shocks:
            self._write_shock_event(timestamp, "SHOCK_STOP_DELIVERY")
        self.logger.info(f"Shock stopped being delivered at {time.time()}")
        status = {
            "type": "shock_stopped_being_delivered"
        }
        self.communication.send_status(status)

    
    def send_controller_arduino_state(self):
        state = {
            "shock_activated": self.shock.shock_activated,
            "grid_live": self.shock.grid_is_live,
            "attempted_shocks": self.shock.attempted_shocks,
            "attemped_shocks_from_arduino": self.shock.attempted_shocks_from_arduino,
            "delivered_shocks": self.shock.delivered_shocks,
            "rpm": self.motor.speed_from_arduino,
            "rotating": self.motor.rotating
        }
        status = {
            "type": "arduino_state",
            "state": state
        }
        self.communication.send_status(status)

    
    def send_state_loop(self):
        while True:
            if (time.time() - self.last_sent_state) > self.send_state_period:
                self.send_controller_arduino_state()
                self.last_sent_state = time.time()


    """Recording Methods"""
    def _start_recording(self):
        """Start APA recording - motor rotation and data collection"""      
        try:
            # Initialize recording variables
            self.recording_data = []
            self.shock_events = []
            self.shock_stop_events = []  # New: track stop_shock events
            self.shock_verification_events = []  # New: track verified shock deliveries
            self.should_stop_recording = False
            self.recording_start_time = time.time()
            self._create_shock_event_file()

            # Start motor
            self.motor.start_motor()


            # Start data recording thread
            self.recording_stop_flag.clear()
            self.recording_thread = threading.Thread(target=self._record_data_loop)
            self.recording_thread.daemon = True
            self.recording_thread.start()
            
            # Set recording flag - this happens in module.py too but just to be safe
            self.recording_shocks = True
            
            # Send status response after successful recording start
            self.communication.send_status({
                "type": "recording_started",
                "recording": True,
                "session_id": self.recording_session_id,
                "message": f"APA recording started"
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            self.communication.send_status({
                "type": "recording_start_failed",
                "error": str(e)
            })
            return False


    def _write_shock_event(self, timestamp_ns: int, event: str): 
        """Write a shock event to file"""
        if self._shock_file_handle:
            self._shock_file_handle.write(f'{timestamp_ns},{event},{self.motor.speed_from_arduino}\n')

    def _create_shock_event_file(self) ->  bool:
        filename = f"{self.current_filename_prefix}_shock_events.csv"
        self.logger.info(f"Creating shock events file {filename}")
        self.add_session_file(filename)
        try:
            self._shock_file_handle = open(filename, "w", buffering=1)  # line-buffered
            # Write header with metadata
            f = self._shock_file_handle
            f.write("# Shock Events Recording\n")
            f.write(f"# Session ID: {self.recording_session_id}\n")
            f.write(f"# Recording Start: {self.recording_start_time}\n")
            f.write("#\n")
            f.write("Timestamp_nanoseconds, event, rotation speed (rpm)\n")
        except Exception as e:
            self.logger.error(f"Failed to open shock events file: {e}")
            self._shock_file_handle = None


    def _close_shock_event_file(self) -> bool:
        """Close shock eventsf file"""
        try:
            self._shock_file_handle = None
            self.logger.info(f"Closed shock events file")
        except Exception as e:
            self.logger.warning(f"Error closing shock events file: {e}")

    def _stop_recording(self) -> bool:
        """Stop APA recording and save data"""       
        try:
            # Stop motor
            self.motor.stop_motor()
            
            # Set recording flag to false
            self.recording_stop_flag.set()
            self.recording_shocks = False


            # self.add_session_file(events_file)
            self._close_shock_event_file()
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                
                # Send status response after successful recording stop
                self.communication.send_status({
                    "type": "recording_stopped",
                    "duration": duration,
                    "status": "success",
                    "recording": False,
                    "message": f"APA recording completed successfully"
                })
                
                return True
            else:
                self.logger.error("Error: recording_start_time was None")
                self.communication.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": "Recording start time was not set"
                })
                return False
            
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            self.communication_manager.send_status({
                "type": "recording_stopped",
                "status": "error",
                "error": str(e)
            })
            return False


    def _record_data_loop(self):
        """Background thread to continuously record motor data"""
        self.logger.info("Starting data recording loop")
        
        while not self.recording_stop_flag.is_set():
            try:
                # Write to time series data file?
                pass
                
            except Exception as e:
                self.logger.error(f"Error in data recording loop: {e}")
                time.sleep(0.1)  # Brief pause on error
        
        self.logger.info("Data recording loop stopped")
    

    """Configuration"""
    def configure_module(self, updated_keys: Optional[list[str]]):
        self.logger.info("Configuring APA ARDUINO module...")
        if self.shock.shock_activated:
            self.logger.warning("Cannot configure APA rig while shocks are active!")
            return False
        self.shock.configure_shocker()
        self.motor.configure_motor()





    def cleanup(self):
        """Clean up resources"""
        # TODO: close serial connections?

        self.logger.info("APA system shutdown complete")


if __name__ == "__main__":
    apa = APAModule()
    apa.start()
    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        apa.stop()
