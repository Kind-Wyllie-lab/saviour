import threading
import logging
from collections import deque
from protocol import Protocol
import sys
import os
import time

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.config import Config

# PROTOCOL
MSG_IDENTITY = "I"
MSG_DATA = "D"
MSG_WRITE_PIN_HIGH = "H"
MSG_WRITE_PIN_LOW = "L"

# MOTOR COMMANDS
MSG_SET_SPEED = "S"
MSG_START_MOTOR = "M"
MSG_STOP_MOTOR = "N"

class Motor:
    def __init__(self, protocol_instance: Protocol, config: Config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.arduino = protocol_instance # The connection to the arduino
        self.arduino.handle_command = self.handle_command

        self.cli_enabled = False

        self.stop_flag = threading.Event()

        self.state_buffer = deque(maxlen=10) # What state do we want to capture form motor 0

        self.speed = None

        self.configure_motor()


    """Communication"""
    def handle_command(self, cmd: str, param: str) -> None:
        match cmd:
            case "D":
                self.interpret_state(param)
            case _:
                self.logger.info(f"No logic for {cmd}")


    def send_command(self, type: str, param):
        self.arduino.send_command(type, param)


    """Configure Motor"""
    def configure_motor(self):
        # Set target speed
        self.speed = self.config.get("arduino.motor.motor_speed_rpm")
        self.send_command(MSG_SET_SPEED, self.speed)

    """Motor specific commands"""
    def interpret_state(self, system_state) -> None:
        # Interpret data sent back from motor
        # self.logger.info(f"Interpret state received {system_state} with type {type(system_state)}")
        system_state = system_state.split(",")
        self.state_buffer.append(system_state)


    def set_speed(self, speed: float) -> None:
        self.send_command(MSG_SET_SPEED, speed)


    def start_motor(self) -> None:
        self.logger.info("Starting motor")
        self.send_command(MSG_START_MOTOR, "")
    

    def stop_motor(self) -> None:
        self.logger.info("Stopping motor")
        self.send_command(MSG_STOP_MOTOR, "")


    def get_speed(self) -> float: 
        return self.state_buffer[0][0]


    def handle_input(self, cmd: str):
        # cmd = int(cmd)
        try:
            match cmd:
                case "0": 
                    # self.logger.info(state_buffer)
                    # self.logger.info(f"RPM={self.state_buffer[0][0]}, position={self.state_buffer[0][1]}deg")
                    self.logger.info(self.state_buffer[0])
                case "1": 
                    self.set_speed(2.0)
                case "2":
                    self.start_motor()
                case "3":
                    self.stop_motor()
                case "4":
                    self.logger.info(f"Current speed: {self.get_speed()}rpm")
                case _:
                    self.arduino.conn.write(f"<{cmd}>".encode())
        except Exception as e:
            self.logger.info(f"Error handling input: {e}")


    def command_line_interface(self):
        """
        A set of CLI commands for a user running this program, protocol.py.
        A user can enter a single char, which will be matched against some options.
        Alternatively, a user can send a custom command in the form COMMAND:PARAM e.g. CURRENT:0.5
        """
        try:
            while not self.stop_flag.is_set():
                cmd = input()
                self.handle_input(cmd)
        except KeyboardInterrupt:
            self.logger.info("Shutting down CLI")
            self.stop_flag.set()
        except Exception as e:
            self.logger.info(f"Exception in CLI: {e}")
            self.stop_flag.set()    
    

    def start(self):
        if self.cli_enabled == True:
            self.cli_thread = threading.Thread(target=self.command_line_interface).start()


    def stop(self):
        if self.cli_enabled == True:
            self.cli.join()
