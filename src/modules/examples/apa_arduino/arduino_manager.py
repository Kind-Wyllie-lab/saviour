#!/usr/bin/env python
"""
Alternate approach, a single arduino manager for a pi with multiple arduinos connected to it.

@author: Andrew SG
@date: 02/06/2025
"""

import time
import threading
import queue
import serial
import json
import os
import sys
from typing import Optional, Dict, List, Tuple
import serial.tools.list_ports
import logging

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.config import Config
from protocol import Protocol

class ArduinoError(Exception):
    """Custom exception for Arduino communication errors."""
    pass

class ArduinoManager:
    """Central manager for handling multiple Arduino arduinos."""
    def __init__(self, config: Config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        
        # List of arduino types we expect to find
        self.arduino_types = ["motor_arduino", "shock_arduino"]
        
        # Store found arduinos and their ports
        self.arduino_ports: Dict[str, str] = {}  # Maps arduino_type to port
        self.connected_arduinos: Dict[str, Protocol] = {} # Maps arduino_type to a Protocol which implements a protocol around a serial connection
        
        # Arduino instances
        self.motor = None
        self.shock = None
        
        # Find all arduinos
        self._find_arduino_ports()

    def _initialize_arduino(self, arduino_type: str) -> None:
        """Initialize the specified arduino"""
        self.logger.info(f"Initializing {arduino_type}")
        if arduino_type.lower() == "motor_arduino": # TODO: Use an ENUM for type? Maybe rename it arduino_role as well?
            self.motor = MotorArduino(self)
            self.motor.stop_motor() # Start with motor off
            if self.config.get("arduino.flip_direction"):
                self.motor.flip_motor(self.config.get("arduino.flip_direction"))
                
        if arduino_type.lower() == "shock_arduino": 
            self.shock = ShockArduino(self)
            self.shock.set_parameters() # Set initial shock parameters

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
        self._initialize_arduino(identity) # Initialize the discovered arduino
        self.logger.info(f"Connected arduinos: {list(self.connected_arduinos.keys())}")
    
    # def handle_success(self, identity: str, msg_id: int, msg_content: str) -> None:
    #     """
    #     Callback to handle a successful command response from an arduino.
    #     """
    #     self.logger.info(f"{msg_id}: {msg_content} received from {identity}")
            
    def send_command(self, command: str, arduino_type: str):
        """Send a command to a specific arduino and wait for response."""
        self.logger.info(f"Sending {command} to {arduino_type}")
        if arduino_type not in self.connected_arduinos:
            raise ArduinoError(f"No connection for {arduino_type}")
        resp = self.connected_arduinos[arduino_type].send_command(command)
        self.logger.info(f"Sent command and got response: {resp}")
        return resp

    def read_pin(self, pin: int, arduino_type: str):
        self.logger.info(f"Reading pin {pin} on {arduino_type}")
        if arduino_type not in self.connected_arduinos:
            raise ArduinoError(f"No connection for {arduino_type}")
        resp = self.connected_arduinos[arduino_type].read_pin(pin)
        return resp

    def set_pin_high(self, pin:int, arduino_type: str):
        self.logger.info(f"Setting {pin} high on {arduino_type}")
        if arduino_type not in self.connected_arduinos:
            raise ArduinoError(f"No connection for {arduino_type}")
        resp = self.connected_arduinos[arduino_type].set_pin_high(pin)
        return resp
    
    def set_pin_low(self, pin:int, arduino_type: str):
        self.logger.info(f"Setting {pin} low on {arduino_type}")
        if arduino_type not in self.connected_arduinos:
            raise ArduinoError(f"No connection for {arduino_type}")
        resp = self.connected_arduinos[arduino_type].set_pin_low(pin)
        return resp
        
    def cleanup(self):
        """Close all serial connections."""
        # Stop all arduinos first
        if self.motor:
            self.motor.stop_motor()  # Stop motor
        if self.shock:
            self.shock.stop()  # Stop any ongoing shocks
            
        # Close all connections
        for conn in self.arduino_connections.values():
            if conn.is_open:
                conn.close()
        self.arduino_connections.clear()
        self.arduino_ports.clear()
        
        # Clear arduino instances
        self.motor = None
        self.shock = None

class MotorArduino:
    """Arduino for the table motor."""
    def __init__(self, manager: ArduinoManager):
        self.manager = manager
        self.arduino_type = 'motor_arduino'
        
    def set_speed(self, speed=None) -> Tuple[str, str]:
        """Set the motor speed"""
        # Use provided speed or get from config
        if speed is None:
            speed = self.manager.config.get("arduino.motor_speed_rpm")
        else:
            # Convert speed to int if it's a string
            try:
                speed = int(speed)
            except (ValueError, TypeError):
                raise ValueError(f"Speed must be a valid integer, got: {speed}")

        min_speed = self.manager.config.get("arduino.min_motor_speed")
        max_speed = self.manager.config.get("arduino.max_motor_speed")
        if not min_speed <= speed <= max_speed:
            raise ValueError(f"Speed must be between {min_speed} and {max_speed}")
        command = f"SET_SPEED:{speed}"
        self.manager.logger.info(f"Sending command {command}")
        return self.manager.send_command(command, self.arduino_type)
    
    def pid_status(self) -> Tuple[str, str]:
        command = "PID_STATUS"
        return self.manager.send_command(command, self.arduino_type)
    
    def stop_motor(self) -> Tuple[str, str]:
        """Stop motor and reset PID state completely"""
        try:
            # First stop the motor
            self.manager.logger.info("Stopping motor with SET_SPEED:0")
            status1, message1 = self.manager.send_command("SET_SPEED:0", self.arduino_type)
            
            if status1 != "OK":
                self.manager.logger.error(f"Failed to stop motor: {status1} - {message1}")
                return status1, message1
            
            # Then reset PID state to ensure clean state
            self.manager.logger.info("Resetting PID state with RESET_PID")
            status2, message2 = self.manager.send_command("RESET_PID", self.arduino_type)
            
            if status2 != "OK":
                self.manager.logger.warning(f"PID reset failed: {status2} - {message2}")
                # Motor stopped successfully, but PID reset failed - still return success for motor stop
                return "OK", f"Motor stopped: {message1}, PID reset failed: {message2}"
            
            self.manager.logger.info("Motor stopped and PID reset successfully")
            return "OK", f"Motor stopped and PID reset: {message1}, {message2}"
            
        except Exception as e:
            self.manager.logger.error(f"Exception in stop_motor: {e}")
            return "ERROR", f"Exception: {str(e)}"

    def flip_motor(self, flip_direction: bool) -> Tuple[str, str]:
        """Flip the motor direction."""
        command = f"FLIP_MOTOR:{1 if flip_direction else 0}"
        return self.manager.send_command(command, self.arduino_type)

    def read_encoder(self) -> Tuple[str, str]:
        """Read rpm and position from motor encoder."""
        command = "READ_ENCODER"
        return self.manager.send_command(command, self.arduino_type)

    def update_pin(self) -> Tuple[str, str]:
        """Update encoder parameters from config."""
        pins = self.manager.config.get(f"arduino.{self.arduino_type}.pins")
        command = f"SET_PARAMS:ANALOG_PIN:{pins['analog_in']}"
        return self.manager.send_command(command, self.arduino_type)

class ShockArduino:
    """Arduino for the shock system."""
    def __init__(self, manager: ArduinoManager):
        self.manager = manager
        self.arduino_type = 'shock_arduino'

        # DB25 interface pins (LSB to MSB for current control)
        # Each pin represents a binary weight for current: 0.2mA, 0.4mA, 0.8mA, 1.6mA, 3.2mA, etc.
        # const int CURRENT_OUT[8] = {A3, A2, A1, A0, 4, 5, 6, 7};

        # Timing control pins
        # const int TRIGGER_OUT = 9;        // PWM output for precise timing control (must be pin 9 for TimerOne)
        # const int SELF_TEST_OUT = A4;     // Test signal output to shock generator
        # const int SELF_TEST_IN = 2;       // Test signal input from shock generator (active low)
        
    def send_shock(self, parameters: Dict) -> Tuple[str, str]:
        """Activate shocker. Assumes current etc. already set.
        """
        self.manager.logger.info("Send shock called")
        # # Debug: Log what we're getting from config
        # config_current = self.manager.config.get("arduino.shock_arduino.weak_shock.current", 0.2)
        # config_duration = self.manager.config.get("arduino.shock_arduino.weak_shock.duration", 0.5)
        # config_latency = self.manager.config.get("arduino.shock_arduino.weak_shock.intershock_latency", 1.0)
        
        # # Debug: Log the entire config to see what's available
        # self.manager.logger.info(f"Full config: {self.manager.config.get_all()}")
        
        # # Debug: Try different config paths to see what's available
        # self.manager.logger.info(f"Direct config path test:")
        # self.manager.logger.info(f"  - arduino.shock_arduino.weak_shock.current: {self.manager.config.get('arduino.shock_arduino.weak_shock.current', 'NOT_FOUND')}")
        # self.manager.logger.info(f"  - editable.arduino.shock_arduino.weak_shock.current: {self.manager.config.get('editable.arduino.shock_arduino.weak_shock.current', 'NOT_FOUND')}")
        # self.manager.logger.info(f"  - weak_shock.current: {self.manager.config.get('weak_shock.current', 'NOT_FOUND')}")
        
        # self.manager.logger.info(f"Config values - current: {config_current}, duration: {config_duration}, latency: {config_latency}")
        # self.manager.logger.info(f"Provided parameters: {parameters}")
        
        # # Use config values only (commented out parameter usage for now)
        # shock_params = {
        #     "current": config_current,  # Always use config value
        #     "time_on": config_duration,  # Always use config value
        #     "time_off": config_latency,  # Always use config value
        #     "pulses": 50  # Default to the maximum pulses if not specified
        # }
        # TODO: Re-enable parameter override functionality later if needed
        # shock_params = {
        #     "current": parameters.get("current", config_current),
        #     "time_on": parameters.get("time_on", config_duration),
        #     "time_off": parameters.get("time_off", config_latency),
        #     "pulses": parameters.get("pulses", 50)  # Default to the maximum pulses if not specified
        # }
        
        # Arduino expects time values in seconds (it converts to milliseconds internally)
        # No conversion needed - keep the original seconds values
        
        # self.manager.logger.info(f"Final shock parameters: {shock_params}")
        
        # Set individual parameters first
        # TODO: TEMPORARY FIX OF time.sleep(0.01) HERE. LATER, MAKE IT SO SEND SHOCK IS DECOUPLED FROM SETTING PARAMS.
        # if "current" in shock_params:
        #     self.manager.logger.info(f"Setting current to: {shock_params['current']}")
        #     status, msg = self.manager.send_command(f"CURRENT:{shock_params['current']}", self.arduino_type)
        #     if status == "ERROR":
        #         return status, msg
        # time.sleep(0.1)
        # if "time_on" in shock_params:
        #     self.manager.logger.info(f"Setting time_on to: {shock_params['time_on']}")
        #     status, msg = self.manager.send_command(f"TIME_ON:{shock_params['time_on']}", self.arduino_type)
        #     if status == "ERROR":
        #         return status, msg
        # time.sleep(0.1)
        # if "time_off" in shock_params:
        #     self.manager.logger.info(f"Setting time_off to: {shock_params['time_off']}")
        #     status, msg = self.manager.send_command(f"TIME_OFF:{shock_params['time_off']}", self.arduino_type)
        #     if status == "ERROR":
        #         return status, msg
        # time.sleep(0.1)
        # if "pulses" in shock_params: # The number of pulses to send, NOTE: 2 pulses is one on pulse and one off pulse.
        #     self.manager.logger.info(f"Setting pulses to: {shock_params['pulses']}")
        #     status, msg = self.manager.send_command(f"PULSES:{shock_params['pulses']}", self.arduino_type)
        #     if status == "ERROR":
        #         return status, msg
        # time.sleep(0.1)
        
        # Activate the shock sequence
        result = self.manager.send_command("ACTIVATE", self.arduino_type)
        self.manager.logger.info(f"send_shock final result: {result}")
        return result
        
    def set_parameters(self) -> Tuple[str, str]:
        """Set the default shock parameters.
        These must be set prior to sending shocks.
        """
        parameters = self.manager.config.get("arduino.shock_parameters")
        parameters.update(self.manager.config.get("editable.arduino.shock_parameters"))
        self.manager.logger.info(f"Setting parameters to {parameters}")
        # Parse and adjust wording to match expected by arduino
        parameters_formatted = {}
        parameters_formatted["current"] = parameters["current_(mA)"] 
        parameters_formatted["time_on"] = parameters["pulse_duration_(s)"] 
        parameters_formatted["time_off"] = parameters["pulse_gap_(s)"]
        parameters_formatted["pulses"] = parameters["max_shocks"] 
        self.manager.logger.info(f"Formatted parameters: {parameters_formatted}")
        # Set individual parameters
        for key, value in parameters_formatted.items():
            if key in ["current", "time_on", "time_off", "pulses"]:
                self.manager.send_command(f"{key.upper()}:{value}", self.arduino_type)
        return "OK", "Parameters set"
        
    def stop(self) -> Tuple[str, str]:
        """Stop any ongoing shocks."""
        try:
            self.manager.logger.info(f"Attempting to stop shocks")
            command = "DEACTIVATE"
            return self.manager.send_command(command, self.arduino_type)
        except Exception as e:
            # If there's no active sequence, that's fine
            if "No active shock sequence" in str(e):
                return "OK", "No active sequence to stop"
            else:
                raise e

    def get_verification_stats(self) -> Tuple[str, str]:
        """Get shock verification statistics from Arduino."""
        try:
            self.manager.logger.info(f"Requesting verification statistics")
            command = "VERIFICATION_STATS"
            return self.manager.send_command(command, self.arduino_type)
        except Exception as e:
            self.manager.logger.error(f"Error getting verification stats: {e}")
            return "ERROR", str(e)

    # TODO: Fix this
    def test_grid_fault(self) -> Tuple[str, str]:
        try:
            self.manager.logger.info(f"Testing for grid faults")
            resp = self.manager.send_command("TEST_GRID", self.arduino_type)
            content = resp.get("content")
            self.manager.logger.info(f"Response for TEST_GRID: {content}")
            if content == "PASSED":
                return True, "No grid fault detected"
            else:
                return False, "Grid test failed"
        except Exception as e:
            self.manager.logger.error(f"Error testing grid: {e}")
            return "ERROR", str(e)

    def test_grid_fault_manually(self) -> Tuple[str, str]:
        """Test for grid faults using the TEST_IN/TEST_OUT interface."""
        try:
            self.manager.logger.info(f"Testing for grid faults")
            self.manager.logger.info(f"Starting with a pin read")
            TEST_OUT = 12
            TEST_IN = 2
            delay = 0.005

            # Step 1: Assert TEST_IN is 1 at start
            time.sleep(delay)
            resp = self.manager.read_pin(TEST_IN, self.arduino_type)
            if resp == 0:
                return False, "TEST_IN is active while TEST_OUT is inactive"
            
            # Step 2: Set TEST_OUT to 0 (ACTIVE LOW)
            time.sleep(delay)
            resp = self.manager.set_pin_low(TEST_OUT, self.arduino_type)
            # Check if failed...

            # Step 3: Wait, and assert TEST_IN is now LOW
            time.sleep(delay)
            resp = self.manager.read_pin(TEST_IN, self.arduino_type)
            if resp == 1:
                return False, "TEST_IN is still HIGH while TEST_OUT is LOW"

            # Step 4: Set TEST_OUT back to 1 (end test)
            time.sleep(delay)
            resp = self.manager.set_pin_high(TEST_OUT, self.arduino_type)

            # Step 5: Verify TEST_IN is now back to 1
            time.sleep(delay)
            resp = self.manager.read_pin(TEST_IN, self.arduino_type)
            if resp == 0:
                return False, "TEST_IN has not returned to 1"

            return True, "PASSED"


        except Exception as e:
            self.manager.logger.error(f"Error testing grid: {e}")
            return "ERROR", str(e)

    def reset_verification_counters(self) -> Tuple[str, str]:
        """Reset verification counters for new experiment."""
        try:
            self.manager.logger.info(f"Resetting verification counters")
            command = "RESET_VERIFICATION"
            return self.manager.send_command(command, self.arduino_type)
        except Exception as e:
            self.manager.logger.error(f"Error resetting verification counters: {e}")
            return "ERROR", str(e)

