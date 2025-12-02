"""
Python/computer side implementation of a serial protocol for communicating with an Arduino.
Features:
- Message delimiters <MESSAGE>
- Message format: <
"""
import serial
import time
import threading
import json
import re
import argparse
from typing import Dict, Callable, Optional
import logging
from collections import deque

parser = argparse.ArgumentParser(
    prog="arduino_comms_test",
    description="To test arduino comms protocol"
)
parser.add_argument('-p', '--port', default="/dev/ttyACM0")
parser.add_argument('-b', '--baud', default=115200)
args = parser.parse_args()


# PROTOCOL
MSG_IDENTITY = "I"
MSG_DATA = "D"
MSG_WRITE_PIN_HIGH = "H"
MSG_WRITE_PIN_LOW = "L"

# SHOCK COMMANDS
MSG_CURRENT = "C"
MSG_TIME_ON = "T"
MSG_TIME_OFF = "Y"

# MOTOR COMMANDS
MSG_SET_SPEED = "S"
MSG_START_MOTOR = "M"
MSG_STOP_MOTOR = "N"

PIN_MAP = [17, 16, 15, 14, 4, 5, 6, 7, 12, 2, 9] 
SHOCK_VALS = [0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56] # Mapping for the shocker
SHOCK_PINS = [17, 16, 15, 14, 4, 5, 6, 7]
SELF_TEST_OUT = 12
SELF_TEST_IN = 2
TRIGGER_OUT = 9

class Protocol:
    def __init__(
        self, 
        port: str, 
        baud: int = 115200, 
        on_identity: Optional[Callable["Protocol", str]] = None
    ) -> None:
        """
        Initialize the serial communication protocol for an Arduino-like device.

        Args:
            port: Serial port path (e.g. '/dev/ttyACM0' or 'COM3')
            baud: Baud rate for the serial connection
            on_identity: Optional callback triggered when the device identifies itself.
                         Signature: (protocol_instance, identity_str) -> None
        """
        self.logger = logging.getLogger(__name__)
        
        # Protocol
        self.identity: str = "" # Identity of connected Arduino
        self.on_identity: Callable = on_identity        

        # Connection
        self.port: str = port
        self.baud: int = baud
        self.logger.info(self.port)
        self.conn: serial.Serial = serial.Serial(port=self.port, baudrate=self.baud, timeout=5)

        # Thread management
        self.stop_flag = threading.Event()

        # Get identity
        self.send_command(MSG_IDENTITY, "")


    def listen(self):
        while not self.stop_flag.is_set():
            try:
                response = self.conn.readline().decode("utf-8")
                matches = re.findall(r'<(.+?)>', response) # Look for any serial messages that match the <message> format
                msg_type = ""
                if not matches:
                    continue
                response = matches[0]
                seps = response.split(":")
                if len(seps) == 3:
                    msg_type = seps[0]
                    msg = ":".join(seps[1:])
                elif len(seps) == 2:
                    msg_type = seps[0]
                    msg = seps[1]

                self._handle_command(msg_type, msg)
            except Exception as e:
                print(f"Exception listening on serial port {self.port}: {e}")
    

    def _handle_command(self, cmd: str, param: str) -> None:
        """Private version of handle command which can parse identity and then passes to callback for handling specific commands."""
        match cmd:
            case "I":
                print(f"Identity: {cmd}, {param}")
                self.identity = param.lower()
                if self.on_identity:
                    self.on_identity(self, self.identity)
            case _:
                # Pass it to callback
                self.handle_command(cmd, param)
        

    def handle_command(self, cmd: str, param: str) -> None:
        """Callback that allows for additional cmd types to be implemented"""
        self.send_command(MSG_IDENTITY, "")


    def send_command(self, cmd: str, param: str) -> None:
        self.conn.write(f"<{cmd}:{param}>".encode())


    def request_identity(self):
        self.send_command(MSG_IDENTITY, "")


    def start(self):
        self.logger.info(f"Starting Protocol on {self.port}")
        self.listen_thread = threading.Thread(target=self.listen)
        self.listen_thread.start()


    def stop(self):
        self.logger.info("Stopping Protocol")
        self.stop_flag.set()
        self.listen_thread.join()
        self.conn.close()


class Motor:
    def __init__(self, protocol_instance: Protocol):
        print("Initialising motor...")
        self.arduino = protocol_instance # The connection to the arduino
        self.arduino.handle_command = self.handle_command

        self.cli_enabled = True

        self.stop_flag = threading.Event()

        self.state_buffer = deque(maxlen=10) # What state do we want to capture form motor 0



    def handle_command(self, cmd: str, param: str) -> None:
        match cmd:
            case "D":
                self.interpret_state(param)
            case _:
                print(f"No logic for {cmd}")


    def send_command(self, type: str, param):
        self.arduino.send_command(type, param)


    """Motor specific commands"""
    def interpret_state(self, system_state) -> None:
        # Interpret data sent back from motor
        # print(f"Interpret state received {system_state} with type {type(system_state)}")
        system_state = system_state.split(",")
        self.state_buffer.append(system_state)


    def set_speed(self, speed: float) -> None:
        self.send_command(MSG_SET_SPEED, speed)


    def start_motor(self) -> None:
        self.send_command(MSG_START_MOTOR, "")
    

    def stop_motor(self) -> None:
        self.send_command(MSG_STOP_MOTOR, "")


    def handle_input(self, cmd: str):
        # cmd = int(cmd)
        try:
            match cmd:
                case "0": 
                    # print(state_buffer)
                    # print(f"RPM={self.state_buffer[0][0]}, position={self.state_buffer[0][1]}deg")
                    print(self.state_buffer[0])
                case "1": 
                    self.set_speed(2.0)
                case "2":
                    self.start_motor()
                case "3":
                    self.stop_motor()
                case _:
                    self.arduino.conn.write(f"<{cmd}>".encode())
        except Exception as e:
            print(f"Error handling input: {e}")


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
            print("Shutting down CLI")
            self.stop_flag.set()
        except Exception as e:
            print(f"Exception in CLI: {e}")
            self.stop_flag.set()    
    

    def start(self):
        if self.cli_enabled == True:
            self.cli_thread = threading.Thread(target=self.command_line_interface).start()


    def stop(self):
        if self.cli_enabled == True:
            self.cli.join()


class Shocker:
    def __init__(self, protocol_instance: Protocol):
        print("Initialising shocker...")
        self.arduino = protocol_instance # The connection to the arduino
        self.arduino.handle_command = self.handle_command

        self.stop_flag = threading.Event()

        self.cli_enabled = True

        self.state_buffer = deque(maxlen=10)


    def handle_command(self, cmd: str, param: str) -> None:
        match cmd:
            case "D":
                self.interpret_shock(param)
            case _:
                print(f"No logic for {cmd}")


    def send_command(self, type: str, param):
        self.arduino.send_command(type, param)


    """SHOCK CONTROLLER SPECIFIC COMMANDS"""
    def set_weak_shock(self):
        self.send_command(MSG_CURRENT, 0.5)    


    def set_strong_shock(self):
        self.send_command(MSG_CURRENT, 1)    


    def set_shock_zero(self):
        self.send_command(MSG_CURRENT, 0)


    def check_shock_set(self) -> bool:
        current = self.calculate_shock(self.state_buffer[-1][0:8])
        if current > 0:
            return True
        else:
            return False


    def run_grid_test(self):
        # Check that shock is set
        if not self.check_shock_set():
            print("Cannot run grid test with current set to 0.")
            return
        
        # Initiate test by writing to pin
        self.send_command(MSG_WRITE_PIN_LOW, SELF_TEST_OUT)
        time.sleep(0.2) # Give some time for it to update

        # Check sefl test in
        val = self.state_buffer[-1][PIN_MAP.index(SELF_TEST_IN)]
        if val == 0:
            print("No grid short detected")
        elif val == 1:
            print("Grid short detected!")
        else:
            print(f"Something went wrong - pin reads {val}")

        # Conclude test by putting self test out high again
        self.send_command(MSG_WRITE_PIN_HIGH, SELF_TEST_OUT)

        print("Grid test complete.")


    def activate_shock(self):
        if not self.check_shock_set():
            print("Cannot activate shock with current set to 0.")
            return
        self.send_command(MSG_WRITE_PIN_LOW, TRIGGER_OUT)


    def deactivate_shock(self):
        self.send_command(MSG_WRITE_PIN_HIGH, TRIGGER_OUT)


    def interpret_shock(self, state: list) -> None:
        state = state.split(",")
        self.state_buffer.append([ int(bit) for bit in state[0:11] ])
        shock_settings = [ int(bit) for bit in state[0:8] ]
        self_test_out = int(state[8])
        self_test_in = int(state[9])
        trigger_out = int(state[10])

        if self_test_out == 0 :
            if sum(shock_settings) == len(shock_settings): # Nothing changed
                print("CANNOT RUN GRID TEST WITHOUT CURRENT BEING SET.")
            # if self_test_in == 0:
            #     print("No grid short detected")
            # if self_test_in == 1:
            #     print("Grid short detected")
        elif trigger_out == 0:
            if sum(shock_settings) == len(shock_settings): # Nothing changed
                print("CANNOT DELIVER SHOCKS WITHOUT CURRENT BEING SET.")
            if self_test_in == 0:
                print("Shocker active but no shock being delivered...")
            if self_test_in == 1:
                print("SHOCK BEING DELIVERED!")


        # print(f"Self test out: {self_test_out}, self_test_in {self_test_in}, trigger_out {trigger_out}")
        # print(f"Shock val: {calculate_shock(shock_settings)}mA")


    def calculate_shock(self, shock_settings: list) -> float:
        """Take shock settings from db25 and calculate the current value in mA"""
        current = 0
        i = 0
        while i < len(shock_settings):
            if int(shock_settings[i]) == 0:
                current += SHOCK_VALS[i]
            i+=1
        return round(current, 3)


    def handle_input(self, cmd: str):
        # cmd = int(cmd)
        try:
            match cmd:
                case "0": 
                    # print(state_buffer)
                    print(f"Shock set to {self.calculate_shock(self.state_buffer[-1][0:8])}mA")
                case "1": 
                    self.set_weak_shock()
                case "2":
                    self.set_strong_shock()
                case "3":
                    t2 = threading.Thread(target=self.run_grid_test).start()
                case "4":
                    self.activate_shock()
                case "5":
                    self.deactivate_shock()
                case "6":
                    self.set_shock_zero()
                case "I":
                    self.request_identity()
                case _:
                    self.arduino.conn.write(f"<{cmd}>".encode())
        except Exception as e:
            print(f"Error handling input: {e}")

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
            print("Shutting down CLI")
            self.stop_flag.set()
        except Exception as e:
            print(f"Exception in CLI: {e}")
            self.stop_flag.set()    
    
    def start(self):
        if self.cli_enabled == True:
            self.cli_thread = threading.Thread(target=self.command_line_interface).start()

    def stop(self):
        if self.cli_enabled == True:
            self.cli.join()

def handle_identity(protocol_instance: Protocol, identity_str: str) -> None:
    print(f"Discovered a {identity_str}")
    if identity_str == "shock":
        print("Identified a shocker")
        s = Shocker(protocol_instance)
        s.start()

    elif identity_str == "motor":
        print("Identified a motor")
        m = Motor(protocol_instance)
        m.start()
    else:
        print(f"Unknown arduino: {identity_str}")

        


def main():
    p = Protocol(port=args.port, baud=args.baud, on_identity=handle_identity)
    try:
        p.start()   
    except KeyboardInterrupt:
        print("Exiting")
        p.stop()
        exit(0)
    except Exception as e:
        print(f"Exception in main: {e}")
        p.stop()
        exit(1)

if (__name__ == '__main__'):
    print("Let us begin")
    
    main()
