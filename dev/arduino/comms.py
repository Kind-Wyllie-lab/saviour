"""
Demonstrated a basic messaging protocol between Pi and Arduino.
"""

# TODO: Message IDs
# TODO: Calculate round trip time

import time
import serial
import re
import threading
from collections import deque

# port = "/dev/ttyACM1"
port = "COM3"
baud = 115200

stop_flag = threading.Event()

# PROTOCOL
MSG_IDENTITY = "I"
MSG_DATA = "D"
MSG_WRITE_PIN_HIGH = "H"
MSG_WRITE_PIN_LOW = "L"
MSG_CURRENT = "C"
MSG_TIME_ON = "T"
MSG_TIME_OFF = "Y"

PIN_MAP = [17, 16, 15, 14, 4, 5, 6, 7, 12, 2, 9] 
SHOCK_VALS = [0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56] # Mapping for the shocker
SHOCK_PINS = [17, 16, 15, 14, 4, 5, 6, 7]
SELF_TEST_OUT = 12
SELF_TEST_IN = 2
TRIGGER_OUT = 9

state_buffer = deque(maxlen=10)

conn = serial.Serial(port=port, baudrate=baud, timeout=5)

def interpret_shock(state: list) -> None:
    state = state.split(",")
    state_buffer.append([ int(bit) for bit in state[0:11] ])
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


def calculate_shock(shock_settings: list) -> float:
    """Take shock settings from db25 and calculate the current value in mA"""
    global SHOCK_VALS
    current = 0
    i = 0
    while i < len(shock_settings):
        if int(shock_settings[i]) == 0:
            current += SHOCK_VALS[i]
        i+=1
    return round(current, 3)

# def calculate_settings_from_shock(current: float) -> list:


def listen():
    global conn
    while not stop_flag.is_set():
        try:
            response = conn.readline().decode("utf-8")
            matches = re.findall(r'<(.+?)>', response) # Look for any serial messages that match the <message> format
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

            handle_command(msg_type, msg)
        except Exception as e:
            print(f"Fuckin exception: {e}")


def handle_command(cmd: str, param: str) -> None:
    global state_buffer
    match cmd:
        case "I":
            print(f"Identity: {cmd}, {param}")
        case "D":
            interpret_shock(param)
        case "A": 
            pass
        case _:
            pass


def send_command(cmd: str, param: str) -> None:
    conn.write(f"<{cmd}:{param}>".encode())

t = threading.Thread(target=listen).start()



def set_weak_shock():
    send_command(MSG_CURRENT, 0.5)    

def set_strong_shock():
    send_command(MSG_CURRENT, 1)    

def set_shock_zero():
    send_command(MSG_CURRENT, 0)

def check_shock_set() -> bool:
    global state_buffer
    current = calculate_shock(state_buffer[-1][0:8])
    if current > 0:
        return True
    else:
        return False


def run_grid_test():
    global state_buffer
    # Check that shock is set
    if not check_shock_set():
        print("Cannot run grid test with current set to 0.")
        return
    
    # Initiate test by writing to pin
    send_command(MSG_WRITE_PIN_LOW, SELF_TEST_OUT)
    time.sleep(0.2) # Give some time for it to update

    # Check sefl test in
    val = state_buffer[-1][PIN_MAP.index(SELF_TEST_IN)]
    if val == 0:
        print("No grid short detected")
    elif val == 1:
        print("Grid short detected!")
    else:
        print(f"Something went wrong - pin reads {val}")

    # Conclude test by putting self test out high again
    send_command(MSG_WRITE_PIN_HIGH, SELF_TEST_OUT)

def activate_shock():
    if not check_shock_set():
        print("Cannot activate shock with current set to 0.")
        return
    send_command(MSG_WRITE_PIN_LOW, TRIGGER_OUT)

def deactivate_shock():
    send_command(MSG_WRITE_PIN_HIGH, TRIGGER_OUT)

def request_identity():
    send_command(MSG_IDENTITY, "")

def handle_input(cmd: str):
    global state_buffer
    # cmd = int(cmd)
    try:
        match cmd:
            case "0": 
                # print(state_buffer)
                print(f"Shock set to {calculate_shock(state_buffer[-1][0:8])}mA")
            case "1": 
                set_weak_shock()
            case "2":
                set_strong_shock()
            case "3":
                t2 = threading.Thread(target=run_grid_test).start()
            case "4":
                activate_shock()
            case "5":
                deactivate_shock()
            case "6":
                set_shock_zero()
            case "I":
                request_identity()
            case _:
                conn.write(f"<{cmd}>".encode())
    except Exception as e:
        print(f"Error handling input: {e}")

try:
    time.sleep(0.1)
    while True:
        cmd = input()
        handle_input(cmd)
        # conn.write(f"<{cmd}>".encode())
except KeyboardInterrupt:
    print("Shuttind down")
    stop_flag.set() 
    t.join()
    conn.close()