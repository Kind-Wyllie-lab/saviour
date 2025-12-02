
import logging
import threading
from collections import deque
from protocol import Protocol

class Shocker:
    def __init__(self, protocol_instance: Protocol):
        self.logger = logging.getLogger(__name__)
        self.arduino = protocol_instance # The connection to the arduino
        self.arduino.handle_command = self.handle_command

        self.stop_flag = threading.Event()

        self.cli_enabled = False

        self.state_buffer = deque(maxlen=10)


    def handle_command(self, cmd: str, param: str) -> None:
        match cmd:
            case "D":
                self.interpret_shock(param)
            case _:
                self.logger.info(f"No logic for {cmd}")


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
            self.logger.info("Cannot run grid test with current set to 0.")
            return
        
        # Initiate test by writing to pin
        self.send_command(MSG_WRITE_PIN_LOW, SELF_TEST_OUT)
        time.sleep(0.2) # Give some time for it to update

        # Check sefl test in
        val = self.state_buffer[-1][PIN_MAP.index(SELF_TEST_IN)]
        if val == 0:
            self.logger.info("No grid short detected")
        elif val == 1:
            self.logger.info("Grid short detected!")
        else:
            self.logger.info(f"Something went wrong - pin reads {val}")

        # Conclude test by putting self test out high again
        self.send_command(MSG_WRITE_PIN_HIGH, SELF_TEST_OUT)

        self.logger.info("Grid test complete.")


    def activate_shock(self):
        if not self.check_shock_set():
            self.logger.info("Cannot activate shock with current set to 0.")
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
                self.logger.info("CANNOT RUN GRID TEST WITHOUT CURRENT BEING SET.")
            # if self_test_in == 0:
            #     self.logger.info("No grid short detected")
            # if self_test_in == 1:
            #     self.logger.info("Grid short detected")
        elif trigger_out == 0:
            if sum(shock_settings) == len(shock_settings): # Nothing changed
                self.logger.info("CANNOT DELIVER SHOCKS WITHOUT CURRENT BEING SET.")
            if self_test_in == 0:
                self.logger.info("Shocker active but no shock being delivered...")
            if self_test_in == 1:
                self.logger.info("SHOCK BEING DELIVERED!")


        # self.logger.info(f"Self test out: {self_test_out}, self_test_in {self_test_in}, trigger_out {trigger_out}")
        # self.logger.info(f"Shock val: {calculate_shock(shock_settings)}mA")


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