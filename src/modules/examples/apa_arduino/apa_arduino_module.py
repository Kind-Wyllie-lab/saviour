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
        self.arduino_types = ["motor_arduino", "shock_arduino"]
        
        # Store found arduinos and their ports
        self.arduino_ports: Dict[str, str] = {}  # Maps arduino_type to port
        self.connected_arduinos: Dict[str, Protocol] = {} # Maps arduino_type to a Protocol which implements a protocol around a serial connection
        self.motor = None
        self.shock = None
        self._find_arduino_ports()

        # Recording-specific variables
        self._event_file_handle = None
        self.recording_thread = None
        self.should_stop_recording = False
        self.recording_start_time = None
        self.data_sampling_rate = 1  # Hz - how often to sample motor data

        self.module_checks = [
            self._check_motor,
            self._check_shocker,
            self._check_shock_grid_fault,
            self._check_shock_grid_active
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
        self.set_arduino_callbacks()
        self.configure_module()



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
    # @command
    def _activate_shock(self):
        if self.shock:
            self.shock.activate_shock()
        else:
            self.logger.warning("Activate shock called but no shocker connected!")


    # @command
    def _deactivate_shock(self):
        if self.shock:
            self.shock.deactivate_shock()
        else:
            self.logger.warning("Deactivate shock called but no shocker connected!")


    # @command
    def _start_motor(self):
        if self.motor:
            self.motor.start_motor()
        else:
            self.logger.warning("Start motor called but no motor connected!")


    # @command
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

    # @check()
    # def _check_shocks_not_above_50(self) -> tuple[bool, str]:
    #     if self.shock.attempted_shocks >= 50 or self.shock.attempted_shocks_from_arduino >= 50:
    #         return False, "Have already delivered limit of 50 shocks - please manually reset pulse counter (GUI button)"
    #     else:
    #         return True, ""
            

    # TODO: Checks to make sure RPM, shocks etc are set?


    """Handle grid state and communicate it to frontend"""
    def set_arduino_callbacks(self):
        self.logger.info("Setting arduino callbacks")
        self.shock.on_shock_started_being_attempted = self.on_shock_started_being_attempted
        self.shock.on_shock_stopped_being_attempted = self.on_shock_stopped_being_attempted
        self.shock.on_shock_started_being_delivered = self.on_shock_started_being_delivered
        self.shock.on_shock_stopped_being_delivered = self.on_shock_stopped_being_delivered
        
    def on_shock_started_being_attempted(self):
        self.logger.info(f"Attempting shock at {time.time()}, total attempted: {self.shock.attempted_shocks} arduino reports {self.shock.attempted_shocks_from_arduino}")


    def on_shock_stopped_being_attempted(self):
        self.logger.info(f"Stopped attempting shock at {time.time()}")


    def on_shock_started_being_delivered(self):
        self.logger.info(f"Delivered shock at {time.time()}, total delivered {self.shock.delivered_shocks}")
        status = {
            "type": "shock_started_being_delivered"
        }
        self.communication.send_status(status)

    
    def on_shock_stopped_being_delivered(self):
        self.logger.info(f"Shock stopped being delivered at {time.time()}")
        status = {
            "type": "shock_stopped_being_delivered"
        }
        self.communication.send_status(status)


    """Recording Methods"""
    def _start_recording(self):
        """Start APA recording - motor rotation and data collection"""      
        try:
            # Get preset motor speed from config
            self.motor.speed = self.motor.speed
            
            # Start motor rotation at preset speed
            self.logger.info(f"Starting motor at preset speed: {self.motor.speed}")
            
            # Check if motor controller is available
            if not self.motor:
                self.logger.warning("Motor controller not available")
                self.communication.send_status({
                "type": "recording_start_failed",
                "error": "Motor controller not available"
            })
                return False
            
            status, message = self.motor.set_speed(preset_speed)
            if status != "OK":
                self.logger.error(f"Failed to start motor: {message}")
                self.communication.send_status({
                "type": "recording_start_failed",
                "error": f"Failed to start motor: {message}"
            })
                return False
            
            # Verify motor is actually running by checking encoder movement
            self.logger.info(f"Motor speed set successfully. Verifying motor is running...")
            time.sleep(0.5)  # Wait a bit for motor to start
            
            # Check if encoder is reading movement
            try:
                encoder_status, encoder_message = self.motor.read_encoder()
                self.logger.info(f"Initial encoder reading: {encoder_message}")
            except Exception as e:
                self.logger.warning(f"Could not read encoder for motor verification: {e}")
            
            # Initialize recording variables
            self.recording_data = []
            self.shock_events = []
            self.shock_stop_events = []  # New: track stop_shock events
            self.shock_verification_events = []  # New: track verified shock deliveries
            self.should_stop_recording = False
            self.recording_start_time = time.time()
            
            # Start data recording thread
            self.recording_thread = threading.Thread(target=self._record_data_loop)
            self.recording_thread.daemon = True
            self.recording_thread.start()
            
            # Set recording flag
            self.is_recording = True
            
            # Send status response after successful recording start
            self.communication.send_status({
                "type": "recording_started",
                "filename": filename,
                "recording": True,
                "session_id": self.recording_session_id,
                "motor_speed": preset_speed,
                "message": f"APA recording started with motor speed {preset_speed}"
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            self.communication.send_status({
                "type": "recording_start_failed",
                "error": str(e)
            })
            return False


    def _stop_recording(self) -> bool:
        """Stop APA recording and save data"""       
        try:
            # Stop motor
            self.logger.info("Stopping motor")
            if self.motor:
                self.motor.stop_motor()
            else:
                self.logger.warning("Motor controller not available for stopping")
            
            # Set recording flag to false
            self.is_recording = False
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                # Save recorded data
                self._save_recording_data()
                
                # Send status response after successful recording stop
                self.communication.send_status({
                    "type": "recording_stopped",
                    "filename": self.current_filename,
                    "session_id": self.recording_session_id,
                    "duration": duration,
                    "data_points": len(self.recording_data),
                    "shock_events": len(self.shock_events),
                    "status": "success",
                    "recording": False,
                    "message": f"APA recording completed successfully with {len(self.recording_data)} data points and {len(self.shock_events)} shock events"
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

    

    """Configuration"""
    def configure_module(self, updated_keys: Optional[list[str]]):
        self.logger.info("Configuring APA ARDUINO module...")
        if self.shock.shock_activated:
            self.logger.warning("Cannot configure APA rig while shocks are active!")
            return False
        self.shock.configure_shocker()
        self.motor.configure_motor()


    def _record_data_loop(self):
        """Background thread to continuously record motor data"""
        self.logger.info("Starting data recording loop")
        
        while not self.should_stop_recording:
            try:
                # Get current timestamp
                timestamp = time.time()
                elapsed_time = timestamp - self.recording_start_time
                
                # Read motor data
                if self.motor:
                    encoder_status, encoder_message = self.motor.read_encoder()
                    pid_status, pid_message = self.motor.pid_status()
                else:
                    # If motor controller not available, use placeholder data
                    encoder_status, encoder_message = "error", "Motor controller not available"
                    pid_status, pid_message = "error", "Motor controller not available"
                
                # Parse encoder data (assuming format like "RPM: 2.5, Position: 1234")
                rpm = None
                encoder_position = None
                if encoder_status == "OK":
                    try:
                        # Extract RPM value from message
                        if "RPM: " in encoder_message:
                            rpm_part = encoder_message.split("RPM: ")[1]
                            if "," in rpm_part:
                                rpm_str = rpm_part.split(",")[0].strip()
                            else:
                                rpm_str = rpm_part.strip()
                            rpm = float(rpm_str)
                        
                        # Extract encoder position if available
                        if "Position: " in encoder_message:
                            pos_part = encoder_message.split("Position: ")[1]
                            if "," in pos_part:
                                pos_str = pos_part.split(",")[0].strip()
                            else:
                                pos_str = pos_part.strip()
                            # Remove "deg" suffix if present and convert to float
                            if "deg" in pos_str:
                                pos_str = pos_str.replace("deg", "").strip()
                            try:
                                encoder_position = float(pos_str)
                            except ValueError:
                                encoder_position = None
                        elif "POS:" in encoder_message:
                            pos_part = encoder_message.split("POS:")[1]
                            if "," in pos_part:
                                pos_str = pos_part.split(",")[0].strip()
                            else:
                                pos_str = pos_part.strip()
                            try:
                                encoder_position = float(pos_str)
                            except ValueError:
                                encoder_position = None
                            
                    except (ValueError, IndexError):
                        self.logger.warning(f"Could not parse encoder data from: {encoder_message}")
                
                # Parse PID status (assuming format like "PID: target=2.0, actual=1.8, error=0.2")
                pid_data = {}
                if pid_status == "OK":
                    try:
                        # Extract PID values from message
                        if "PID:" in pid_message:
                            pid_parts = pid_message.split("PID:")[1].strip()
                            for part in pid_parts.split(","):
                                if "=" in part:
                                    key, value = part.strip().split("=")
                                    pid_data[key.strip()] = float(value)
                    except (ValueError, IndexError):
                        self.logger.warning(f"Could not parse PID from: {pid_message}")
                
                # Record comprehensive data point
                data_point = {
                    "timestamp": timestamp,
                    "elapsed_time": elapsed_time,
                    "rpm": rpm,
                    "encoder_position": encoder_position,
                    "motor_speed": self.motor.speed,
                    "pid_target": pid_data.get("target"),
                    "pid_actual": pid_data.get("actual"),
                    "pid_error": pid_data.get("error"),
                    "encoder_status": encoder_status,
                    "pid_status": pid_status,
                    "raw_encoder_message": encoder_message,
                    "raw_pid_message": pid_message
                }
                
                self.recording_data.append(data_point)
                
                # Log data collection progress (every 10 data points to avoid spam)
                if len(self.recording_data) % 10 == 0:
                    self.logger.info(f"Collected {len(self.recording_data)} data points. Latest: RPM={rpm}, Position={encoder_position}, Motor Speed={self.motor.speed}")
                
                # Sleep for sampling rate
                time.sleep(1.0 / self.data_sampling_rate)
                
            except Exception as e:
                self.logger.error(f"Error in data recording loop: {e}")
                time.sleep(0.1)  # Brief pause on error
        
        self.logger.info("Data recording loop stopped")

    def _send_shock_with_recording(self, shock_params: dict) -> tuple:
        """Send shock and record the event"""
        try:
            self.logger.info(f"Received shock parameters: {shock_params}")
            timestamp = time.time()
            elapsed_time = timestamp - self.recording_start_time if self.recording_start_time else 0
            shock_event = {
                "timestamp": timestamp,
                "elapsed_time": elapsed_time,
                "event_type": "start_shock",  # Add event type for clarity
                "shock_params": shock_params.copy(),
                "motor_speed": self.motor.speed,
                "verified": False  # Will be updated when verification is received
            }
            self.shock_events.append(shock_event)
            self.logger.info(f"Shock event recorded: {shock_params}")
            
            # Check if shock controller is available
            if not self.shock:
                self.logger.warning("Shock controller not available")
                return "error", "Shock controller not available"
            
            # Start monitoring for shock verification
            # self._start_shock_verification_monitoring(shock_event)
            
            return self.shock.send_shock(shock_params)
        except Exception as e:
            self.logger.error(f"Error recording shock event: {e}")
            return "error", str(e)

    def _start_shock_verification_monitoring(self, shock_event: dict):
        """Start monitoring for shock verification from Arduino"""
        def monitor_verification():
            try:
                # Check if shock controller is available
                if not self.shock:
                    self.logger.warning("Shock controller not available for verification monitoring")
                    return
                
                # Query Arduino for verification status
                status, message = self.shock.get_verification_stats()
                if status == "success":
                    # Parse verification data from Arduino response
                    verification_data = self._parse_verification_response(message)
                    if verification_data:
                        # Record verification event
                        timestamp = time.time()
                        elapsed_time = timestamp - self.recording_start_time if self.recording_start_time else 0
                        verification_event = {
                            "timestamp": timestamp,
                            "elapsed_time": elapsed_time,
                            "shock_event_index": len(self.shock_events) - 1,
                            "verification_data": verification_data,
                            "motor_speed": self.motor.speed
                        }
                        self.shock_verification_events.append(verification_event)
                        
                        # Update the shock event as verified
                        shock_event["verified"] = True
                        shock_event["verification_timestamp"] = timestamp
                        shock_event["verification_elapsed_time"] = elapsed_time
                        
                        self.logger.info(f"Shock verification recorded: {verification_data}")
                        
                        # Send verification status to controller
                        self.communication.send_status({
                            "type": "shock_verification",
                            "shock_event_index": len(self.shock_events) - 1,
                            "verification_data": verification_data,
                            "timestamp": timestamp,
                            "elapsed_time": elapsed_time
                        })
                
            except Exception as e:
                self.logger.error(f"Error monitoring shock verification: {e}")
        
        # Start verification monitoring in a separate thread
        verification_thread = threading.Thread(target=monitor_verification)
        verification_thread.daemon = True
        verification_thread.start()



    def _save_recording_data(self):
        """Save recorded data to files"""
        try:
            # Create data filename with experiment name if available
            if hasattr(self, 'current_experiment_name') and self.current_experiment_name:
                safe_experiment_name = "".join(c for c in self.current_experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_experiment_name = safe_experiment_name.replace(' ', '_')
                motor_data_file = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_motor_data.csv"
                shock_data_file = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_shock_events.csv"
            else:
                motor_data_file = f"{self.recording_folder}/{self.recording_session_id}_motor_data.csv"
                shock_data_file = f"{self.recording_folder}/{self.recording_session_id}_shock_events.csv"
            
            # Save motor data
            if self.recording_data:
                with open(motor_data_file, 'w', newline='') as csvfile:
                    fieldnames = ['timestamp', 'elapsed_time', 'rpm', 'encoder_position', 'motor_speed', 'pid_target', 'pid_actual', 'pid_error', 'encoder_status', 'pid_status', 'raw_encoder_message', 'raw_pid_message']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    for data_point in self.recording_data:
                        writer.writerow(data_point)
                
                self.logger.info(f"Saved {len(self.recording_data)} encoder data points to {motor_data_file}")
            
            # Save all shock events (start and stop) in chronological order
            all_shock_events = []
            
            # Define the shock data file path early
            shock_data_file = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_shock_events.csv" if hasattr(self, 'current_experiment_name') and self.current_experiment_name else f"{self.recording_folder}/{self.recording_session_id}_shock_events.csv"
            
            # Add start events
            for shock_event in self.shock_events:
                all_shock_events.append({
                    'timestamp': shock_event['timestamp'],
                    'elapsed_time': shock_event['elapsed_time'],
                    'event_type': 'start_shock',
                    'rpm': self._get_current_rpm_from_shock_event(shock_event),
                    'shock_current': shock_event['shock_params'].get('current'),
                    'shock_duration': shock_event['shock_params'].get('time_on'),
                    'shock_pulses': shock_event['shock_params'].get('pulses'),
                    'verified': shock_event.get('verified', False),
                    'verification_timestamp': shock_event.get('verification_timestamp', ''),
                    'verification_elapsed_time': shock_event.get('verification_elapsed_time', ''),
                    'encoder_position': None  # Start events don't have encoder position yet
                })
            
            # Add stop events
            for stop_event in self.shock_stop_events:
                all_shock_events.append({
                    'timestamp': stop_event['timestamp'],
                    'elapsed_time': stop_event['elapsed_time'],
                    'event_type': 'stop_shock',
                    'rpm': stop_event.get('rpm'),  # Use the RPM we captured in stop_event
                    'shock_current': None,  # Stop events don't have shock params
                    'shock_duration': None,  # Stop events don't have shock params
                    'shock_pulses': None,    # Stop events don't have shock params
                    'verified': None,        # Stop events don't have verification
                    'verification_timestamp': None,
                    'verification_elapsed_time': None,
                    'encoder_position': stop_event.get('encoder_position')
                })
            
            # Sort all events by timestamp
            all_shock_events.sort(key=lambda x: x['timestamp'])
            
            # Save combined shock events file
            if all_shock_events:
                with open(shock_data_file, 'w', newline='') as csvfile:
                    fieldnames = ['timestamp', 'elapsed_time', 'event_type', 'rpm', 'shock_current', 'shock_duration', 'shock_pulses', 'verified', 'verification_timestamp', 'verification_elapsed_time', 'encoder_position']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    for event in all_shock_events:
                        writer.writerow(event)
                
                self.logger.info(f"Saved {len(all_shock_events)} combined shock events (start + stop) to {shock_data_file}")
            else:
                self.logger.info(f"No shock events to save")
            
            # Save shock verification events
            if self.shock_verification_events:
                verification_data_file = f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_shock_verification.csv" if hasattr(self, 'current_experiment_name') and self.current_experiment_name else f"{self.recording_folder}/{self.recording_session_id}_shock_verification.csv"
                
                with open(verification_data_file, 'w', newline='') as csvfile:
                    fieldnames = ['timestamp', 'elapsed_time', 'shock_event_index', 'motor_speed', 'total_pulses', 'verified_shocks', 'verification_rate', 'current_session', 'session_verified']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    for verification_event in self.shock_verification_events:
                        verification_data = verification_event['verification_data']
                        row = {
                            'timestamp': verification_event['timestamp'],
                            'elapsed_time': verification_event['elapsed_time'],
                            'shock_event_index': verification_event['shock_event_index'],
                            'motor_speed': verification_event['motor_speed'],
                            'total_pulses': verification_data.get('TotalPulses', 0),
                            'verified_shocks': verification_data.get('VerifiedShocks', 0),
                            'verification_rate': verification_data.get('VerificationRate', 0.0),
                            'current_session': verification_data.get('CurrentSession', 0),
                            'session_verified': verification_data.get('SessionVerified', 0)
                        }
                        writer.writerow(row)
                
                self.logger.info(f"Saved {len(self.shock_verification_events)} verification events to {verification_data_file}")
            
            # Save metadata
            metadata_file = f"{self.recording_folder}/{self.recording_session_id}_metadata.json"
            metadata = {
                "session_id": self.recording_session_id,
                "experiment_name": self.current_experiment_name,
                "recording_start_time": self.recording_start_time,
                "recording_end_time": time.time(),
                "duration": time.time() - self.recording_start_time if self.recording_start_time else 0,
                "motor_speed": self.motor.speed,
                "encoder_data_points": len(self.recording_data),
                "shock_events": len(self.shock_events),
                "shock_stop_events": len(self.shock_stop_events),
                "shock_verification_events": len(self.shock_verification_events),
                "verified_shocks": sum(1 for event in self.shock_events if event.get('verified', False)),
                "verification_rate": (sum(1 for event in self.shock_events if event.get('verified', False)) / len(self.shock_events) * 100) if self.shock_events else 0.0,
                "sampling_rate": self.data_sampling_rate,
                "data_collection": {
                    "encoder_position": any(point.get('encoder_position') is not None for point in self.recording_data),
                    "rpm": any(point.get('rpm') is not None for point in self.recording_data),
                    "pid_data": any(point.get('pid_target') is not None for point in self.recording_data)
                },
                "files": {
                    "encoder_data": os.path.basename(motor_data_file) if self.recording_data else None,
                    "shock_events": os.path.basename(shock_data_file) if (self.shock_events or self.shock_stop_events) else None,
                    "shock_verification": os.path.basename(f"{self.recording_folder}/{safe_experiment_name}_{self.recording_session_id}_shock_verification.csv" if hasattr(self, 'current_experiment_name') and self.current_experiment_name else f"{self.recording_folder}/{self.recording_session_id}_shock_verification.csv") if self.shock_verification_events else None
                }
            }
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self.logger.info(f"Saved metadata to {metadata_file}")
            
            # Calculate and save actual shock durations
            self._calculate_shock_durations()
            
        except Exception as e:
            self.logger.error(f"Error saving recording data: {e}")


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
