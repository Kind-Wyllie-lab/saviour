"""
Python/computer side implementation of a serial protocol for communicating with an Arduino.
Features:
- Message delimiters <MESSAGE>
- Message format: <
"""
import serial
import time
import threading
from typing import Callable, Optional
import logging


# PROTOCOL
MSG_IDENTITY = "I"
MSG_ERROR = "E"
MSG_DATA = "D"
MSG_SUCCESS = "S" # TODO: Get rid of this
MSG_WRITE_PIN_HIGH = "H"
MSG_WRITE_PIN_LOW = "L"


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

        # Flush serial at start
        time.sleep(1)
        self.conn.flushInput()

        # Get identity
        self.send_command(MSG_IDENTITY, "")


    def listen(self):
        buf = ""
        while not self.stop_flag.is_set():
            try:
                data = self.conn.read(self.conn.in_waiting or 1).decode("utf-8", errors="ignore")
                buf += data
                while "<" in buf and ">" in buf:
                    start = buf.index("<")
                    end = buf.index(">", start)
                    packet = buf[start+1:end]
                    buf = buf[end+1:]
                    self._parse_message(packet)
            except serial.SerialException as e:
                self.logger.error("Serial error on %s: %s", self.port, e)
                buf = ""
                if not self.stop_flag.is_set():
                    self.reset_serial()
                    if not (self.conn and self.conn.is_open):
                        break
            except Exception as e:
                self.logger.error("Unexpected error in listen() on %s: %s", self.port, e)
                buf = ""
    

    def _parse_message(self, packet: str):
        parts = packet.split(":", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            self._handle_message(parts[0], parts[1])
        else:
            self.logger.warning("Cannot handle bad message: %r", packet)

    def reset_serial(self):
        self.logger.info("Resetting serial connection on %s", self.port)
        try:
            if self.conn and self.conn.is_open:
                self.conn.close()
        except Exception:
            pass
        for attempt in range(1, 6):
            try:
                time.sleep(1.0)
                self.conn = serial.Serial(port=self.port, baudrate=self.baud, timeout=5)
                time.sleep(1.0)
                self.conn.flushInput()
                self.send_command(MSG_IDENTITY, "")
                self.logger.info("Serial connection restored on %s", self.port)
                return
            except Exception as e:
                self.logger.warning("Reconnect attempt %d/5 on %s failed: %s", attempt, self.port, e)
        self.logger.error("Could not restore serial connection on %s after 5 attempts", self.port)
    

    def _handle_message(self, cmd: str, param: str) -> None:
        """Private version of handle command which can parse identity and then passes to callback for handling specific commands."""
        match cmd:
            case "I":
                self.logger.info(f"Identity: {cmd}, {param}")
                self.identity = param.lower()
                if self.on_identity:
                    self.on_identity(self, self.identity)
            case "E":
                self.logger.warning(f"ERROR on {self.port}: {param}")
            case "S":
                pass
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


# def main():
#     p = Protocol(port=args.port, baud=args.baud, on_identity=handle_identity)
#     try:
#         p.start()   
#     except KeyboardInterrupt:
#         print("Exiting")
#         p.stop()
#         exit(0)
#     except Exception as e:
#         print(f"Exception in main: {e}")
#         p.stop()
#         exit(1)

# if (__name__ == '__main__'):
#     print("Let us begin")
    
#     main()
