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
        on_identity: Optional[Callable["Protocol", str]] = None,
        on_success: Optional[Callable[[str, int, str], None]] = None # Optional callback for when a SUCCESS message received - takes identity, msg_id and msg_content as args.
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
        self.last_ack: str = ""
        self.ack_timeout: int = 100 # Time to wait for ack in seconds
        self.sent_messages: dict = {}
        self.unacknowledged_messages: dict = {} # A dcit of msg_ids and commands that are still to be acked
        self.abandoned_messages: dict = {}
        self.messages: dict = {} # Dict to store sent messages 
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

        # Response handling
        self.response_futures = {} # msg_id 0> {"event": threading.Event(), "response": None}
        self.lock = threading.Lock()
        self.on_success: Callable = on_success

        # Config
        self.cli_enabled: bool = False 


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

                payload = cmd

                self.parse_message(payload)
                
            except Exception as e:
                self.logger.info(f"Error: {e}")
    
    def mark_message_acked(self, msg_id: int) -> None:
        """Update that message has been acked."""
        if msg_id in self.unacknowledged_messages.keys(): # Check that we were waiting for an ACK
            del self.unacknowledged_messages[msg_id]
            self.messages[msg_id]["ack_time"] = time.time()
        else:
            self.logger.warning(f"{msg_id} received ACK but was not in unacknowledged messages")

    def parse_message(self, payload: str) -> Dict:
        """
        Takes a verified message from the arduino in the form of a str. 
        Parses message for type, msg_id, msg_content (params, basically), sequence
        """
        parts = payload.rsplit(":")
        msg_type = parts[0]
        msg_id = int(parts[1][1:]) # Find the message ID
        msg_content = parts[2] # Content of the message e.g. "RPM=2.0, POSITION=180.27" or "Could not start motor"
        msg_sequence = parts[3][1:] # Sequence given by arduino to arrange arrived packets
        parsed_message = {"type": parts[0], "msg_id": msg_id}
        self.logger.info(f"Message received from {self.identity}: {msg_type}, {msg_content}")
        match parsed_message["type"]:
            case "ACK": # If this is an acknowledgement message
                # with self.lock:
                self.mark_message_acked(msg_id)

            case "ERROR": # If an error message
                self.logger.warning(f"Error message: {msg_content}")
                # with self.lock:
                future = self.response_futures.get(msg_id)
                if future:
                    future["response"] = {"type": "ERROR", "msg_id": msg_id, "content": msg_content}
                    future["event"].set()

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
                # self.logger.info(f"Success response for msg {msg_id}: {msg_content}")

                # with self.lock:
                future = self.response_futures.get(msg_id)

                if future:
                    future["response"] = {"type": "SUCCESS", "msg_id": msg_id, "content": msg_content}
                    future["event"].set()

        return parsed_message

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

    def send_command(self, command: str, timeout: int=10) -> str:
        """
        Takes a command in the format CMD:PARAM and sends it to the arduino.
        Uses global variales msg_id and delay.
        """
        # global msg_id, delay
        msg_id = self.msg_id # Catch if i type it wrong # TODO: Change this
        payload = f"M{self.msg_id}:{command}"
        msg = f"<{payload}>"

        future = {"event": threading.Event(), "response": None} # Create a dict with a threading Event (used to make send_command wait for response) and a response which will later be filled in
        # with self.lock:
        self.response_futures[self.msg_id] = future # Add this future dict instance to our dict of them
        self.logger.debug(f"Send command registered future for {payload}")

        self.logger.info(f"Sending command: {msg}")
        self.conn.write(msg.encode())
        self.sent_messages[self.msg_id] =  {"command": command, "sent_time": time.time(), "retries": 0}
        self.unacknowledged_messages[self.msg_id] = {"command": command, "sent_time": time.time(), "retries": 0}
        self.messages[self.msg_id] = {
            "command": command,
            "sent_time": time.time(),
            "status": "WAITING",
            "ack_time": None,
            "resposne_time": None
        }
        self.msg_id += 1

        # Wait for SUCCESS / ERROR
        try:
            success = future["event"].wait(timeout)
        except Exception as e:
            self.logger.warning(f"send_command wait failed for {self.msg_id}: {e}")
            success = False
         
        # with self.lock:
        resp = future.get("response")
        try:
            del self.response_futures[self.msg_id]
        except KeyError:
            pass

        if not success:
            return None
        
        return future["response"]


    def resend_command(self, command, msg_id, retries):
        payload = f"M{msg_id}:{command}"
        msg = f"<{payload}>"
        self.logger.info(f"Resending command: {msg}")
        self.conn.write(msg.encode())
        self.sent_messages[msg_id] =  {"command": command, "sent_time": time.time(), "retries": 0}
        self.unacknowledged_messages[msg_id] = {"command": command, "sent_time": time.time(), "retries": retries+1}


    def monitor_commands(self):
        """
        Check if ACKs received for each message and if not retry sending message with exponential delay
        """
        while True:
            # with self.lock:
            for msg_id in list(self.unacknowledged_messages.keys()):
                msg_data = self.unacknowledged_messages.get(msg_id) # Get the data incase it gets acked
                if not msg_data:
                    # It got acked while looping
                    continue
                if (time.time() - msg_data["sent_time"]) > (2**msg_data["retries"] * self.ack_timeout): # Exponential growth in retry duration
                    if msg_data["retries"] < self.retry_limit:
                        # self.logger.info(f"No ACK received in {self.ack_timeout}s, retrying")
                        self.resend_command(msg_data["command"], msg_id, msg_data["retries"])
                    else:
                        self.logger.warning(f"Have attempted {self.retry_limit} retries, abandonding msg M{msg_id}")
                        self.abandoned_messages[msg_id] = msg_data
                        del self.unacknowledged_messages[msg_id]
                else:
                    # Keep waiting for ack to come
                    continue


    def start(self):
        self.logger.info(f"Starting Protocol on {self.port}")
        self.listen_thread = threading.Thread(target=self.listen)
        self.listen_thread.start()

        self.monitor_commands_thread = threading.Thread(target=self.monitor_commands)
        self.monitor_commands_thread.start()

        if self.cli_enabled == True:
            self.command_thread = threading.Thread(target=self.command_line_interface)
            self.command_thread.start()


    def read_pin(self, pin: int) -> Optional[int]:
        command = f"READ_PIN:{pin}"
        try:
            response = self.send_command(command)
            if response is None:
                self.logger.warning(f"read_pin timed out for pin {pin} on {self.identity}")
                return None
            val = response.get("content").split("=")[1]
            return int(val)
        except Exception as e:
            self.logger.error(f"Error reading pin {pin}: {e}")
            return None
            
    
    def set_pin_low(self, pin:int):
        command = f"SET_PIN_LOW:{pin}"
        try:
            response = self.send_command(command)
            if response is None:
                self.logger.warning(f"set_pin_low timed out for pin {pin} on {self.identity}")
                return None
            return response
        except Exception as e:
            self.logger.error(f"Error reading pin {pin}: {e}")
            return None


    def set_pin_high(self, pin:int):
        command = f"SET_PIN_HIGH:{pin}"
        try:
            response = self.send_command(command)
            if response is None:
                self.logger.warning(f"set_pin_high timed out for pin {pin} on {self.identity}")
                return None
            return response
        except Exception as e:
            self.logger.error(f"Error reading pin {pin}: {e}")
            return None

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