import logging
import os
import sys
import threading
import time
from collections import deque

from protocol import Protocol

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
MSG_SET_FEEDFORWARD = "F"
MSG_SET_PID = "SET_PID"

# Config section (a Config runtime-state sidecar) holding, per setpoint, the
# motor command that held it: {"2": 96.4}. Sent as the feedforward on
# configure, so a start heads straight for the target instead of waiting for
# the integral to build up from zero.
LEARNED_OUTPUT_SECTION = "motor_learned_output"

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
        self.speed_from_arduino = None
        self.position = None
        self.rotating = False
        self.time_started_rotating = None
        self.speed_error: str | None = None  # non-None when RPM is outside tolerance
        self.output_from_arduino: float | None = None  # firmware's motor command

        # Learning the feedforward: the run of state messages within
        # tolerance of the target, and whether this rotation saved one yet.
        self.learn_tolerance = self.config.get("arduino.motor._learn_tolerance_pct", 0.10)
        self.learn_after_s = self.config.get("arduino.motor._learn_after_stable_s", 30)
        self._stable_since: float | None = None
        self._stable_output_sum = 0.0
        self._stable_output_n = 0
        self._learned_this_run = False

        self.time_to_reach_target_speed = self.config.get("arduino.motor._validation_ramp_time_s", 30)
        self.rpm_error_lower_threshold  = self.config.get("arduino.motor._rpm_error_lower_pct", 0.20)
        self.rpm_error_upper_threshold  = self.config.get("arduino.motor._rpm_error_upper_pct", 0.10)

        self.configure_motor()


    """Communication"""
    def handle_command(self, cmd: str, param: str) -> None:
        match cmd:
            case "D":
                self.interpret_state(param)
            case _:
                self.logger.info(f"No logic for {cmd}: {param}")


    def send_command(self, type: str, param):
        self.arduino.send_command(type, param)


    """Configure Motor"""
    def configure_motor(self):
        # Set target speed
        self.speed = self.config.get("arduino.motor.motor_speed_rpm")
        self.send_command(MSG_SET_SPEED, self.speed)
        gains = self.config.get("arduino.motor._pid_gains")
        if gains:
            self.send_command(MSG_SET_PID, ",".join(str(g) for g in gains))
        feedforward = self.feedforward_for(self.speed)
        if feedforward is not None:
            self.send_command(MSG_SET_FEEDFORWARD, round(feedforward, 1))


    @staticmethod
    def _setpoint_key(rpm: float) -> str:
        return f"{float(rpm):g}"


    def feedforward_for(self, rpm) -> float | None:
        """The motor command learned for *rpm*, else the nearest learned one
        (or the hand-measured 2 rpm figure) scaled to it."""
        if not rpm:
            return None
        learned = self.config.get(LEARNED_OUTPUT_SECTION) or {}
        if self._setpoint_key(rpm) in learned:
            return float(learned[self._setpoint_key(rpm)])
        if learned:
            ref_key = min(learned, key=lambda k: abs(float(k) - rpm))
            return float(learned[ref_key]) * rpm / float(ref_key)
        manual = self.config.get("arduino.motor._manual_motor_speed_for_2rpm")
        return manual * rpm / 2 if manual else None


    def _track_operating_point(self) -> None:
        """Once the table has held the target for learn_after_s, save the mean
        motor command over that stretch as this setpoint's feedforward."""
        if (not self.rotating or not self.speed or self.output_from_arduino is None
                or self.speed_from_arduino is None):
            self._reset_stable_run()
            return
        if abs(self.speed_from_arduino - self.speed) > self.learn_tolerance * self.speed:
            self._reset_stable_run()
            return
        now = time.time()
        if self._stable_since is None:
            self._stable_since = now
        self._stable_output_sum += self.output_from_arduino
        self._stable_output_n += 1
        if not self._learned_this_run and now - self._stable_since >= self.learn_after_s:
            self._save_learned_output()
            self._learned_this_run = True


    def _reset_stable_run(self) -> None:
        self._stable_since = None
        self._stable_output_sum = 0.0
        self._stable_output_n = 0


    def _save_learned_output(self) -> None:
        if not self._stable_output_n or not self.speed:
            return
        value = round(self._stable_output_sum / self._stable_output_n, 1)
        if value <= 0:
            return
        key = self._setpoint_key(self.speed)
        learned = dict(self.config.get(LEARNED_OUTPUT_SECTION) or {})
        old = learned.get(key)
        if old is not None and abs(old - value) < 1:
            return
        learned[key] = value
        self.config.set(LEARNED_OUTPUT_SECTION, learned)
        self.logger.info("Motor: learned feedforward %s for %s rpm (was %s)", value, key, old)
        # Takes effect on the next start (the firmware zeroes its integral then).
        self.send_command(MSG_SET_FEEDFORWARD, value)


    def validate_state(self):
        if not self.rotating or self.speed is None or self.speed_from_arduino is None:
            return
        if time.time() - self.time_started_rotating < self.time_to_reach_target_speed:
            return  # still in ramp-up window

        if self.speed_from_arduino > (1 + self.rpm_error_upper_threshold) * self.speed:
            msg = f"overspeed: {self.speed_from_arduino:.1f} rpm vs {self.speed} rpm target"
            if self.speed_error != msg:
                self.logger.warning("Motor %s", msg)
                self.speed_error = msg
        elif self.speed_from_arduino < (1 - self.rpm_error_lower_threshold) * self.speed:
            msg = f"underspeed: {self.speed_from_arduino:.1f} rpm vs {self.speed} rpm target"
            if self.speed_error != msg:
                self.logger.warning("Motor %s", msg)
                self.speed_error = msg
        else:
            self.speed_error = None



    """Motor specific commands"""
    def interpret_state(self, system_state) -> None:
        # Interpret data sent back from motor
        # self.logger.info(f"Interpret state received {system_state} with type {type(system_state)}")
        system_state = system_state.split(",")
        self.state_buffer.append(system_state)
        try:
            self.speed_from_arduino = float(system_state[0])
            self.position = float(system_state[1])
            # Firmware older than the feedforward change sends no output.
            out = system_state[3].strip() if len(system_state) > 3 else ""
            self.output_from_arduino = float(out) if out else None
            self.validate_state()
            self._track_operating_point()
        except Exception as e:
            self.logger.warning(f"Could not parse update from motor {system_state}: {e}")
            # self.arduino.reset_serial()

    def set_speed(self, speed: float) -> None:
        self.send_command(MSG_SET_SPEED, speed)


    def start_motor(self) -> None:
        self.logger.info("Starting motor")
        self.send_command(MSG_START_MOTOR, "")
        if not self.rotating:
            # The firmware ignores a start while running, so only a real
            # start resets the ramp window and the learning run.
            self.time_started_rotating = time.time()
            self._learned_this_run = False
            self._reset_stable_run()
        self.rotating = True


    def stop_motor(self) -> None:
        self.logger.info("Stopping motor")
        self.send_command(MSG_STOP_MOTOR, "")
        # A long run may have drifted since the save at learn_after_s.
        if self._stable_since is not None and time.time() - self._stable_since >= self.learn_after_s:
            self._save_learned_output()
        self._reset_stable_run()
        self.time_started_rotating = None
        self.rotating = False
        self.speed_error = None


    def on_reconnect(self, protocol_instance: Protocol) -> bool:
        """The Arduino identified again (reset or serial reconnect): rebind and
        re-send the setpoint. A reset has stopped the motor; a reconnect without
        one may not have, so stop it explicitly and let the operator restart.
        Returns True if it was meant to be rotating."""
        self.arduino = protocol_instance
        self.arduino.handle_command = self.handle_command
        was_rotating = self.rotating
        if was_rotating:
            self.stop_motor()
        self.configure_motor()
        return was_rotating


    def get_speed(self) -> float:
        return self.state_buffer[-1][0]


    def handle_input(self, cmd: str):
        # cmd = int(cmd)
        try:
            match cmd:
                case "0":
                    # self.logger.info(state_buffer)
                    # self.logger.info(f"RPM={self.state_buffer[0][0]}, position={self.state_buffer[0][1]}deg")
                    self.logger.info(self.state_buffer[-1])
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
