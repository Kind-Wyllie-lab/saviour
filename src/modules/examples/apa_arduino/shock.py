
import logging
import threading
from collections import deque
from protocol import Protocol
import sys
import os
import time

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.config import Config

# SHOCK COMMANDS
MSG_CURRENT = "C"
MSG_TIME_ON = "T"
MSG_TIME_OFF = "Y"
MSG_RESET_PULSE_COUNTER = "R"

# PROTOCOL
MSG_IDENTITY = "I"
MSG_DATA = "D"
MSG_WRITE_PIN_HIGH = "H"
MSG_WRITE_PIN_LOW = "L"
MSG_ACTIVATE = "Z"
MSG_DEACTIVATE = "X"


# Shock pnout etc
PIN_MAP = [17, 16, 15, 14, 4, 5, 6, 7, 12, 2, 9] 
SHOCK_VALS = [0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56] # Mapping for the shocker
SHOCK_PINS = [17, 16, 15, 14, 4, 5, 6, 7]
SELF_TEST_OUT = 12
SELF_TEST_IN = 2
TRIGGER_OUT = 9

class Shocker:
    def __init__(self, protocol_instance: Protocol, config: Config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self.arduino = protocol_instance # The connection to the arduino
        self.arduino.handle_command = self.handle_command

        self.stop_flag = threading.Event()

        self.cli_enabled = False

        self.state_buffer = deque(maxlen=10)

        # Keep track of shocker setpoints
        self.current = 0
        self.current_from_arduino = None
        self.time_on = 0.5
        self.time_on_from_arduino = None
        self.time_off = 1.5
        self.time_off_from_arduino = None

        # To keep track of when shocks get delivered
        self.self_test_in = None
        self.last_self_test_in = None

        self.trigger_out = None
        self.last_trigger_out = None

        self.self_test_out = None

        # Grid state
        self.grid_is_live = False
        self.shock_activated = False
        self.shock_being_attempted = False
        self.shock_being_delivered = False

        self.attempted_shocks = 0
        self.attempted_shocks_from_arduino = 0
        self.delivered_shocks = 0

        # Callbacks for event communication
        self.on_shock_stopped_being_attempted = None
        self.on_shock_stopped_being_attempted = None
        self.on_shock_started_being_delivered = None
        self.on_shock_stopped_being_delivered = None

        self.configure_shocker()


    """Comms related commands"""
    def handle_command(self, cmd: str, param: str) -> None:
        match cmd:
            case "D":
                self.interpret_shock(param)
            case _:
                self.logger.info(f"No logic for {cmd} with param {param}")


    def send_command(self, type: str, param):
        self.arduino.send_command(type, param)


    """Configuration"""
    def configure_shocker(self):
        # Set current from config
        self.current = self.config.get("arduino.shocker.current")
        self.set_shock(self.current)
        time.sleep(0.1) # small delay between sending commands

        # self.logger.info(f"Set current to {self.current}, actual setpoint {self.get_shock_current()}")

        # Set time_on from config
        self.time_on = self.config.get("arduino.shocker.duration")
        self.set_time_on(self.time_on)
        time.sleep(0.1)

        # Set time_off from config
        self.time_off = self.config.get("arduino.shocker.intershock_latency")
        self.set_time_off(self.time_off)    



    """SHOCK CONTROLLER SPECIFIC COMMANDS"""
    # Set methods
    def set_shock(self, current: float):
        if current > 5.1:
            self.logger.warning(f"Current too high: {current}")
            return
        self.send_command(MSG_CURRENT, current)


    def set_time_on(self, time_on: int):
        self.send_command(MSG_TIME_ON, time_on)


    def set_time_off(self, time_off: int):
        self.send_command(MSG_TIME_OFF, time_off)


    # Get methods
    def get_shock_current(self) -> float:
        return self.calculate_shock(self.state_buffer[0][0:8])


    def check_shock_set(self) -> bool:
        current = self.calculate_shock(self.state_buffer[-1][0:8])
        if current > 0:
            return True
        else:
            return False


    def run_grid_test(self) -> bool:
        """Check if there is a grid short."""
        # Check that shock is set
        if not self.check_shock_set():
            self.logger.info("Cannot run grid test with current set to 0.")
            return False, "Cannot run grid test with current set to 0"
        
        # Initiate test by writing to pin
        self.send_command(MSG_WRITE_PIN_LOW, SELF_TEST_OUT)
        time.sleep(0.2) # Give some time for it to update

        # Check sefl test in
        val = self.state_buffer[-1][PIN_MAP.index(SELF_TEST_IN)]
        if val == 0:
            self.logger.info("No grid short detected")
        elif val == 1:
            self.logger.info("Grid short detected!")
            # Conclude test by putting self test out high again
            self.send_command(MSG_WRITE_PIN_HIGH, SELF_TEST_OUT)
            return False, "Grid short detected"
        else:
            self.logger.info(f"Something went wrong - pin reads {val}")
            # Conclude test by putting self test out high again
            self.send_command(MSG_WRITE_PIN_HIGH, SELF_TEST_OUT)
            return False, f"Something went wrong - pin reads {val}"

        # Conclude test by putting self test out high again
        self.send_command(MSG_WRITE_PIN_HIGH, SELF_TEST_OUT)

        self.logger.info("Grid test complete.")
        return True, "No grid short detected"


    def activate_shock(self):
        if not self.check_shock_set():
            self.logger.info("Cannot activate shock with current set to 0.")
            return False
        # self.send_command(MSG_WRITE_PIN_LOW, TRIGGER_OUT)
        self.send_command(MSG_ACTIVATE, "")
        self.shock_activated = True
        return True


    def deactivate_shock(self):
        # self.send_command(MSG_WRITE_PIN_HIGH, TRIGGER_OUT)
        self.send_command(MSG_DEACTIVATE, "")
        self.shock_activated = False


    def reset_pulse_counter(self):
        """Used to reset the Arduino-side pulse counter, which prevents shocks exceeding 50 in a given session."""
        if not self.shock_activated:
            self.send_command(MSG_RESET_PULSE_COUNTER, "")
            self.attempted_shocks = 0
            self.delivered_shocks = 0
        else:
            self.logger.warning("CANNOT RESET PULSE COUNTER WHILE SHOCKER IS ACTIVE")
            return False


    def interpret_shock(self, state: list) -> None:
        state = state.split(",")
        self.state_buffer.append([ int(bit) for bit in state[0:11] ])
        shock_settings = [ int(bit) for bit in state[0:8] ]
        self.self_test_out = int(state[8])
        self.self_test_in = int(state[9])
        self.trigger_out = int(state[10])
        self.current_from_arduino = float(self.calculate_shock(shock_settings))
        self.time_on_from_arduino = float(state[11])
        self.time_off_from_arduino = float(state[12])
        self.attempted_shocks_from_arduino = int(state[13])

        self.check_shock_events()

        self.validate_state()

        self.last_self_test_in = self.self_test_in
        self.last_trigger_out = self.trigger_out

    def check_shock_events(self):
        """
        In this function we want to check for when shocks are delivered. 
        We may wish to distinguish between when shocks are sent (trigger_out==0) and delivered (self_test_in==1)
        This could be useful for debugging.
        We need to do this as after we activate a shock sequence, the arduino will indepdently turn grid on for time_on and off for time_off.
        """

        if self.shock_activated == True:
            # Establish whether shock being attempted
            if self.trigger_out == 0: # Shock started being sent
                if self.last_trigger_out == 1:
                    self._on_shock_started_being_attempted()

                # Establish whether shock being delivered
                if self.self_test_in == 1 and self.last_self_test_in == 0: # Grid is live and a shock just started being delivered
                    self._on_shock_started_being_delivered()
                if self.self_test_in == 0 and self.last_self_test_in == 1: # Grid is live and a shock just stopped being delivered e.g. the rat jumped, pulled finger away from grid
                    self._on_shock_stopped_being_delivered()


            # If shock is no longer being attempted
            if self.trigger_out == 1:
                if self.shock_being_delivered: # Stopped attempted shocks while shock was being delivered 
                    self._on_shock_stopped_being_delivered()
                if self.last_trigger_out == 0: # If we just stopped attempting shocks
                    self._on_shock_stopped_being_attempted()


        # Update grid is live state - is this necessary?
        if self.trigger_out == 0 or self.self_test_out == 0:
            self.grid_is_live = True
        else:
            self.grid_is_live = False


    def _on_shock_started_being_attempted(self):
        self.shock_being_attempted = True
        self.attempted_shocks += 1
        # self.logger.info(f"Attempting shock at {time.time()}, total attempted: {self.attempted_shocks}")
        self.on_shock_started_being_attempted()


    def _on_shock_stopped_being_attempted(self):
        self.shock_being_attempted = False
        # self.logger.info(f"Stopped attempting shock at {time.time()}")
        self.on_shock_stopped_being_attempted()


    def _on_shock_started_being_delivered(self):
        self.delivered_shocks += 1
        self.shock_being_delivered = True
        # self.logger.info(f"Delivered shock at {time.time()}, total delivered {self.delivered_shocks}")
        self.on_shock_started_being_delivered()

    
    def _on_shock_stopped_being_delivered(self):
        self.shock_being_delivered = False
        # self.logger.info(f"Shock stopped being delivered at {time.time()}")
        self.on_shock_stopped_being_delivered()


    def simple_check_shock_events(self, shock_settings: list):
        """A simple method for debugging shock delivery."""
        if self.trigger_out == 0: # If shock grid is active
            if sum(shock_settings) == len(shock_settings): # Nothing changed
                self.logger.info("CANNOT DELIVER SHOCKS WITHOUT CURRENT BEING SET.")
            if self.self_test_in == 0:
                self.logger.info("Shocker active but no shock being delivered...")
            if self.self_test_in == 1:
                self.logger.info("SHOCK BEING DELIVERED!")


        # if self.self_test_out == 0 :
        #     if sum(shock_settings) == len(shock_settings): # Nothing changed
        #         self.logger.info("CANNOT RUN GRID TEST WITHOUT CURRENT BEING SET.")

        # if self_test_out == 0 or trigger_out == 0:
        #     self.grid_is_live = True
        
            # if self_test_in == 0:
            #     self.logger.info("Shocker active but no shock being delivered...")
            # if self_test_in == 1:
            #     self.logger.info("SHOCK BEING DELIVERED!")

    def validate_state(self):
        valid = True
        if 100*self.current_from_arduino != (100*self.current - ((100*self.current) % 2)) :
            self.logger.warning(f"Current set to {self.current}mA, Arduino reports {self.current_from_arduino}mA")
            valid = False

        if self.time_on != self.time_on_from_arduino/1000:
            self.logger.warning(f"Time_on set to {self.time_on}s, Arduino reports {self.time_on_from_arduino}s")
            valid = False

        if self.time_off != self.time_off_from_arduino/1000:
            self.logger.warning(f"Time_off set to {self.time_off}s, Arduino reports {self.time_off_from_arduino}s") 
            valid = False

        if not valid:
            self.configure_shocker()

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
                    # self.logger.info(state_buffer)
                    self.logger.info(f"Shock set to {self.calculate_shock(self.state_buffer[-1][0:8])}mA")
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