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
from shock import Shocker
from motor import Motor

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


    def _initialize_arduino(self, arduino_type: str, protocol_instance: Protocol) -> None:
        """Initialize the specified arduino"""
        self.logger.info(f"Initializing {arduino_type}")
        if arduino_type.lower() == "motor": # TODO: Use an ENUM for type? Maybe rename it arduino_role as well?
            self.motor = Motor(protocol_instance)
            self.motor.start()
                
        if arduino_type.lower() == "shock": 
            self.shock = Shocker(protocol_instance)
            self.shock.start()


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


    def send_command(self, command: str, arduino_type: str):
        """Send a command to a specific arduino and wait for response."""
        self.logger.info(f"Sending {command} to {arduino_type}")
        if arduino_type not in self.connected_arduinos:
            raise ArduinoError(f"No connection for {arduino_type}")
        resp = self.connected_arduinos[arduino_type].send_command(command)
        self.logger.info(f"Sent command and got response: {resp}")
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


