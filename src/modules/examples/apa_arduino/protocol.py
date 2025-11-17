"""
Python/computer side implementation of a serial protocol for communicating with an Arduino.
Features:
- Message delimiters <MESSAGE>
- Checksum
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

parser = argparse.ArgumentParser(
    prog="arduino_comms_test",
    description="To test arduino comms protocol"
)
parser.add_argument('-p', '--port', default="/dev/ttyACM0")
parser.add_argument('-b', '--baud', default=115200)
args = parser.parse_args()



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
        self.msg_id: int = 1
        self.checksum: bool = True
        self.last_ack: str = ""
        self.ack_timeout: int = 2 # Time to wait for ack in seconds
        self.sent_messages: dict = {}
        self.unacknowledged_messages: dict = {} # A dcit of msg_ids and commands that are still to be acked
        self.abandoned_messages: dict = {}
        self.delay: float = 0.01 # Amount of time to delay after sending a message
        self.retry_limit: int = 3 # Amount of times to retry if no ACK
        self.identity: str = "" # Identity of connected Arduino
        self.on_identity: Callable = on_identity        

        # Connection
        self.port: str = port
        self.baud: int = baud
        self.logger.info(self.port)
        self.conn: serial.Serial = serial.Serial(port=self.port, baudrate=self.baud, timeout=5)

        # Received data
        self.received_data = {}

        # Config
        self.cli_enabled: bool = False 

    def spam(self):
        """
        Function to send some spam - ten messages with a tiny delay.
        """
        spams = 10
        i = 0
        while i < spams:
            self.send_command(f"SPAM{i}")
            i+=1
            time.sleep(self.delay)

    def record(self, delay: float = 0.01, duration: float = 2):
        """
        Function to emulate recording - calling get data at a certain rate for a certain period.
        """
        start = time.time()
        while time.time() - start < duration:
            self.send_command("GET_DATA")
            time.sleep(delay)

    def listen(self):
        """
        Runs in a thread and listens for inbound messages from the arduino.
        Checks for errors and parses message.
        """
        while True:
            try:
                response = self.conn.readline().decode()
                matches = re.findall(r'<(.+?)>', response) # Look for any serial messages that match the <message> format
                if not matches:
                    continue
                cmd = matches[0] 
                # self.logger.info(cmd)

                if self.checksum == True:
                    if '|' not in cmd:
                        self.logger.info(f"Malformed (no checksum): {cmd}")
                        continue

                    payload, chk_str = cmd.rsplit('|', 1)

                    # Verify checksum
                    try:
                        chk_recv = int(chk_str, 16) # Hex string to int
                    except ValueError:
                        self.logger.info(f"Bad checksum format: {chk_str}")

                    chk_calc = self.compute_checksum(payload)

                    if chk_calc != chk_recv:
                        self.logger.info(f"Checksum mismatch: got {chk_recv:02X}, expected {chk_calc:02X}")
                        # Send NACK
                        continue
                    
                    # self.logger.info(f"Received from {port}: {payload}")

                    self.parse_message(payload)
                
            except Exception as e:
                self.logger.info(f"Error: {e}")

    def parse_message(self, payload: str) -> Dict:
        """
        Takes a verified message from the arduino in the form of a str. 
        Parses message for type, msg_id, msg_content (params, basically), sequence
        """
        # global last_ack
        parts = payload.rsplit(":")
        msg_type = parts[0]
        msg_id = int(parts[1][1:]) # Find the message ID
        msg_content = parts[2] # Content of the message e.g. "RPM=2.0, POSITION=180.27" or "Could not start motor"
        msg_sequence = parts[3][1:] # Sequence given by arduino to arrange arrived packets
        parsed_message = {"type": parts[0], "msg_id": msg_id}
        match parsed_message["type"]:
            case "ACK": # If this is an acknowledgement message
                with threading.Lock():
                    self.last_ack = "" # Reset last ack
                    if msg_id in self.unacknowledged_messages.keys(): # Check that we were waiting for an ACK
                        del self.unacknowledged_messages[msg_id] # Remove this message from unacknowledged messages list
                        # self.logger.info(f"ACK received for M{msg_id}")
                    for part in parts[2:]:
                        self.last_ack += f"{part}:"
                    self.last_ack = self.last_ack[:-1]
            case "ERROR": # If an error message
                self.logger.info(f"Error message: {msg_content}")
            case "IDENTITY":
                self.logger.info(f"Arduino identifies as: {msg_content}")
                self.identity = msg_content.lower()
                if self.on_identity:
                    self.on_identity(self, self.identity)
            case "DATA":
                # self.logger.info(f"DATA received for msg_id: {msg_content} sequence {msg_sequence}")
                rpm, position = msg_content.rsplit(',', 1)
                self.received_data[msg_sequence[1:]] = {"rpm": rpm, "position": position, "time": time.time(), "msg_id": msg_id}
            case "SUCCESS":
                self.logger.info(f"Success response for msg {msg_id}: {msg_content}")
                # Optionally store it somewhere for later use

        return parsed_message

    def compute_checksum(self, payload: str) -> int:
        """
        Take a formatted payload (delimiters and checksum stripped) and return calculated checksum.
        Checksum is a bitwise XOR. For each char in the payload, calculate its unicode value, then perform bitwise XOR on it.
        """
        chk = 0
        for c in payload:
            chk ^= ord(c)
        return chk

    def command_line_interface(self):
        """
        A set of CLI commands for a user running this program, protocol.py.
        A user can enter a single char, which will be matched against some options.
        Alternatively, a user can send a custom command in the form COMMAND:PARAM e.g. CURRENT:0.5
        """
        while True:
            command = input()
            match command:
                case "t":
                    self.setup()
                case "k":
                    self.logger.info(f"Non acked: {self.unacknowledged_messages}")
                    continue
                case "j": 
                    self.spam()
                    continue
                case "l":
                    self.logger.info(f"Sent {self.sent_messages}")
                    continue
                case "p":
                    record()
                    continue
                case "o":
                    self.logger.info(f"Data: {self.received_data}")
                    continue
            self.send_command(command)

    def send_command(self, command):
        """
        Takes a command in the format CMD:PARAM and sends it to the arduino.
        Uses global variales msg_id and delay.
        """
        # global msg_id, delay
        payload = f"M{self.msg_id}:{command}"
        chk = self.compute_checksum(payload)
        msg = f"<{payload}|{chk:02x}>"
        self.logger.info(f"Sending command: {msg}")
        self.conn.write(msg.encode())
        self.sent_messages[self.msg_id] =  {"command": command, "sent_time": time.time(), "retries": 0}
        self.unacknowledged_messages[self.msg_id] = {"command": command, "sent_time": time.time(), "retries": 0}
        # self.logger.info(f"Updated unacknowledged messages: {unacknowledged_messages}")
        self.msg_id += 1
        time.sleep(self.delay)

    def resend_command(self, command, msg_id, retries):
        # global delay
        payload = f"M{msg_id}:{command}"
        chk = self.compute_checksum(payload)
        msg = f"<{payload}|{chk:02x}>"
        self.logger.info(f"Sending command: {msg}")
        self.conn.write(msg.encode())
        self.sent_messages[msg_id] =  {"command": command, "sent_time": time.time(), "retries": 0}
        self.unacknowledged_messages[msg_id] = {"command": command, "sent_time": time.time(), "retries": retries+1}
        time.sleep(self.delay)
        # self.logger.info(f"Updated unacknowledged messages: {unacknowledged_messages}")

    def monitor_commands(self):
        """
        Check if ACKs received for each message and if not retry sending message with exponential delay
        """
        while True:
            with threading.Lock():
                for msg_id in list(self.unacknowledged_messages.keys()):
                    msg_data = self.unacknowledged_messages.get(msg_id) # Get the data incase it gets acked
                    if not msg_data:
                        # It got acked while looping
                        continue
                    if (time.time() - msg_data["sent_time"]) > (2**msg_data["retries"] * self.ack_timeout): # Exponential growth in retry duration
                        if msg_data["retries"] < self.retry_limit:
                            self.logger.info(f"No ACK received in {self.ack_timeout}s, retrying")
                            self.resend_command(msg_data["command"], msg_id, msg_data["retries"])
                        else:
                            self.logger.info(f"Have attempted {self.retry_limit} retries, abandonding msg M{msg_id}")
                            self.abandoned_messages[msg_id] = msg_data
                            del self.unacknowledged_messages[msg_id]
                    else:
                        # Keep waiting for ack to come
                        continue

    def setup(self):
        self.send_command("TIME_ON:0.5")
        time.sleep(0.2)
        self.send_command("CURRENT:0.1")
        time.sleep(0.2)
        self.send_command("TIME_OFF:1.0")
        time.sleep(0.2)
        self.send_command("PULSES:50")
        time.sleep(0.2)
        self.send_command("RESET_PULSE_COUNTER")
        time.sleep(0.2)

    def start(self):
        self.logger.info(f"Starting Protocol on {self.port}")
        self.listen_thread = threading.Thread(target=self.listen)
        self.listen_thread.start()

        self.monitor_commands_thread = threading.Thread(target=self.monitor_commands)
        self.monitor_commands_thread.start()

        if self.cli_enabled == True:
            self.command_thread = threading.Thread(target=self.command_line_interface)
            self.command_thread.start()

def main():
    p = Protocol(port=args.port, baud=args.baud)
    p.start()
    time.sleep(2) # Give it some time to start







if (__name__ == '__main__'):
    self.logger.info("Let us begin")
    try:
        main()
    except KeyboardInterrupt:
        self.logger.info("Exiting")
        exit(0)