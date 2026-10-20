"""
Tests for src/modules/variants/apa_arduino/apa_arduino_module.py.

Scope: the shutdown / hardware-safety paths, the supervised state loop and
Arduino discovery's hardware_fault reporting.
Instances are built with __new__ (as in test_apa_camera_module.py) so no
serial ports, ZMQ or Module.__init__ are involved. The module imports its
siblings bare (`from motor import Motor`), as it does when systemd runs the
file directly, so the variant dir goes on sys.path first.
"""

import io
import os
import sys
import threading
from unittest.mock import MagicMock, patch

_VARIANT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "variants", "apa_arduino",
)
if _VARIANT_DIR not in sys.path:
    sys.path.insert(0, _VARIANT_DIR)

from src.modules.variants.apa_arduino.apa_arduino_module import (  # noqa: E402
    APAModule,
)

# The variant imports its base as `modules.module.Module`, a different module
# object from `src.modules.module.Module`; patch the class it really inherits.
_BaseModule = APAModule.__bases__[0]


def _make_apa(**attrs) -> APAModule:
    m = APAModule.__new__(APAModule)
    m.logger = MagicMock()
    m.communication = MagicMock()
    m.facade = MagicMock()
    m.motor = None
    m.shock = None
    m.connected_arduinos = {}
    m.arduino_ports = {}
    m.found_ports = []
    m.unidentified_ports = {}
    m.port_errors = {}
    m._probes = []
    m.send_state_period = 0.01
    m.send_state_thread = None
    m._send_state_stop = None
    m._shock_file_handle = None
    m.current_shock_events_filename = None
    m.recording_shocks = False
    m.recording_start_time = None
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestStopMakesHardwareSafe:
    def test_stop_stops_motor_deactivates_shock_and_closes_serial(self):
        protocol = MagicMock()
        m = _make_apa(motor=MagicMock(), shock=MagicMock(),
                      connected_arduinos={"motor": protocol})
        with patch.object(_BaseModule, "stop", return_value=True) as base_stop:
            assert m.stop() is True
        m.motor.stop_motor.assert_called_once()
        m.shock.deactivate_shock.assert_called_once()
        protocol.stop.assert_called_once()
        base_stop.assert_called_once()

    def test_stop_still_tears_down_comms_if_cleanup_raises(self):
        m = _make_apa()
        with patch.object(APAModule, "cleanup", side_effect=RuntimeError("x")), \
                patch.object(_BaseModule, "stop", return_value=True) as base_stop:
            assert m.stop() is True
        base_stop.assert_called_once()

    def test_cleanup_stops_the_state_loop(self):
        m = _make_apa(_send_state_stop=threading.Event())
        m.cleanup()
        assert m._send_state_stop.is_set()

    def test_cleanup_with_no_arduinos_connected(self):
        _make_apa().cleanup()  # must not raise


class TestSendStateLoop:
    def test_loop_exits_when_stop_event_set(self):
        m = _make_apa()
        m.send_controller_arduino_state = MagicMock()
        stop = threading.Event()
        t = threading.Thread(target=m.send_state_loop, args=(stop,), daemon=True)
        t.start()
        stop.set()
        t.join(timeout=2)
        assert not t.is_alive()

    def test_system_ready_starts_one_supervised_loop_only(self):
        m = _make_apa(motor=MagicMock(), shock=MagicMock())
        m._refresh_hardware_fault = MagicMock()
        m.set_arduino_callbacks = MagicMock()
        m.configure_module = MagicMock()
        alive = MagicMock()
        alive.is_alive.return_value = True
        with patch("src.modules.variants.apa_arduino.apa_arduino_module.supervise",
                   return_value=alive) as sup:
            m.handle_system_ready()
            m.handle_system_ready()  # an Arduino re-sending its identity
        sup.assert_called_once()
        assert sup.call_args.kwargs["stop_event"] is m._send_state_stop


class TestStopRecordingWithoutMotor:
    def test_stop_recording_closes_and_exports_shock_file(self):
        fh = MagicMock()
        m = _make_apa(_shock_file_handle=fh, recording_shocks=True,
                      current_shock_events_filename="rec/x_shock_events.csv",
                      recording_start_time=0.0)
        assert m._stop_recording() is True
        fh.close.assert_called_once()
        m.facade.stage_file_for_export.assert_called_once_with("rec/x_shock_events.csv")
        assert m.recording_shocks is False

    def test_stop_recording_error_path_reports_status(self):
        m = _make_apa(motor=MagicMock())
        m.motor.stop_motor.side_effect = RuntimeError("serial gone")
        assert m._stop_recording() is False
        sent = m.communication.send_status.call_args.args[0]
        assert sent["type"] == "recording_stopped"
        assert sent["status"] == "error"

    def test_shock_event_written_without_motor(self):
        buf = io.StringIO()
        m = _make_apa(_shock_file_handle=buf)
        m._write_shock_event(123, "SHOCK_DELIVERY")
        assert buf.getvalue() == "123,SHOCK_DELIVERY,None\n"


class TestShockerActivateIdempotent:
    def _shocker(self):
        from shock import Shocker  # bare, as the module imports it
        config = MagicMock()
        config.get.return_value = 50
        with patch.object(Shocker, "configure_shocker"):
            s = Shocker(MagicMock(), config)
        s.check_shock_set = MagicMock(return_value=True)
        # Stand-in pulse loop: runs until deactivated, sends nothing.
        s.start_shocking = lambda: s.stop_shock_flag.wait(5)
        return s

    def test_second_activate_does_not_start_a_second_pulse_thread(self):
        s = self._shocker()
        assert s.activate_shock() is True
        first = s.shock_thread
        assert s.activate_shock() is True  # e.g. two UI instances
        assert s.shock_thread is first
        s.deactivate_shock()
        assert not first.is_alive()

    def test_activate_after_deactivate_starts_a_new_thread(self):
        s = self._shocker()
        s.activate_shock()
        first = s.shock_thread
        s.deactivate_shock()
        s.activate_shock()
        assert s.shock_thread is not first
        s.deactivate_shock()


class TestShockLease:
    """The pulse loop stops itself unless activate_shock keeps refreshing it."""

    def _shocker(self, time_on=0.02, time_off=0.02):
        import shock
        config = MagicMock()
        config.get.return_value = 50
        with patch.object(shock.Shocker, "configure_shocker"):
            s = shock.Shocker(MagicMock(), config)
        s.check_shock_set = MagicMock(return_value=True)
        s.time_on, s.time_off = time_on, time_off
        return s

    def test_unrefreshed_shock_stops_with_trigger_high(self):
        import shock
        s = self._shocker()
        with patch.object(shock, "SHOCK_LEASE_S", 0.1):
            s.activate_shock()
            s.shock_thread.join(timeout=2)
        assert not s.shock_thread.is_alive()
        assert s.shock_activated is False
        assert s.arduino.send_command.call_args.args == (shock.MSG_WRITE_PIN_HIGH, shock.TRIGGER_OUT)

    def test_refreshed_shock_keeps_running(self):
        import time

        import shock
        s = self._shocker()
        with patch.object(shock, "SHOCK_LEASE_S", 0.15):
            s.activate_shock()
            for _ in range(6):  # 0.3 s of refreshes, twice the lease
                time.sleep(0.05)
                s.activate_shock()
            assert s.shock_thread.is_alive()
            s.deactivate_shock()
        assert not s.shock_thread.is_alive()

    def test_deactivate_mid_pulse_ends_it_at_once(self):
        import time

        import shock
        s = self._shocker(time_on=5.0)
        s.activate_shock()
        time.sleep(0.05)
        t0 = time.monotonic()
        s.deactivate_shock()
        assert time.monotonic() - t0 < 1.0
        assert s.arduino.send_command.call_args.args == (shock.MSG_WRITE_PIN_HIGH, shock.TRIGGER_OUT)


class TestShockDeliveryEvents:
    """shock_being_delivered drives the dashboard's red banner."""

    def _shocker(self):
        import shock
        config = MagicMock()
        config.get.return_value = 50
        with patch.object(shock.Shocker, "configure_shocker"):
            s = shock.Shocker(MagicMock(), config)
        s.on_shock_started_being_attempted = MagicMock()
        s.on_shock_stopped_being_attempted = MagicMock()
        s.on_shock_started_being_delivered = MagicMock()
        s.on_shock_stopped_being_delivered = MagicMock()
        s.last_trigger_out, s.last_self_test_in = 1, 0
        return s

    def _step(self, s, trigger_out, self_test_in):
        s.trigger_out, s.self_test_in = trigger_out, self_test_in
        s.check_shock_events()
        s.last_trigger_out, s.last_self_test_in = trigger_out, self_test_in

    def test_delivery_clears_when_deactivated_mid_shock(self):
        s = self._shocker()
        s.shock_activated = True
        self._step(s, 0, 1)
        assert s.shock_being_delivered and s.shock_being_attempted
        s.shock_activated = False  # deactivate_shock() before the trigger goes high
        self._step(s, 0, 1)
        self._step(s, 1, 0)
        assert not s.shock_being_delivered
        assert not s.shock_being_attempted
        s.on_shock_stopped_being_delivered.assert_called_once()
        s.on_shock_stopped_being_attempted.assert_called_once()

    def test_delivery_clears_when_animal_leaves_grid(self):
        s = self._shocker()
        s.shock_activated = True
        self._step(s, 0, 1)
        self._step(s, 0, 0)
        assert not s.shock_being_delivered
        assert s.shock_being_attempted

    def test_no_start_while_inactive(self):
        s = self._shocker()
        self._step(s, 0, 1)
        assert not s.shock_being_delivered
        s.on_shock_started_being_delivered.assert_not_called()


class TestProtocolKeepalive:
    def _protocol(self):
        from protocol import Protocol
        p = Protocol.__new__(Protocol)
        p.logger = MagicMock()
        p.port = "/dev/ttyACM0"
        p.conn = MagicMock()
        p.stop_flag = threading.Event()
        p.keepalive_thread = None
        p._write_lock = threading.Lock()
        p._last_error = None
        p.on_identity = None
        p.identity = ""
        return p

    def test_keepalive_starts_on_identity_only(self):
        import protocol
        p = self._protocol()
        with patch.object(protocol, "KEEPALIVE_PERIOD_S", 0.01):
            assert p.keepalive_thread is None
            p._handle_message("I", "SHOCK")
            p._handle_message("I", "SHOCK")  # re-identify: still one thread
            first = p.keepalive_thread
            threading.Event().wait(0.1)
            p.stop_flag.set()
            first.join(timeout=1)
        assert p.keepalive_thread is first
        assert not first.is_alive()
        assert b"<K:>" in [c.args[0] for c in p.conn.write.call_args_list]

    def test_packets_parsed_byte_by_byte(self):
        p = self._protocol()
        p._parse_message = MagicMock()
        buf = ""
        for ch in "<D:1,0,>\r\n<D:2,0,>\r\n":
            buf = p._consume_packets(buf + ch)
        assert [c.args[0] for c in p._parse_message.call_args_list] == ["D:1,0,", "D:2,0,"]
        assert buf == ""

    def test_resyncs_after_starting_mid_packet(self):
        # Reading from the middle of a packet left '...>\r\n<D:' in the buffer;
        # the old loop raised on it and dropped every packet thereafter.
        p = self._protocol()
        p._parse_message = MagicMock()
        buf = ""
        for ch in "1,1,1,500,>\r\n<D:0,1,>\r\n<D:1,1,>\r\n":
            buf = p._consume_packets(buf + ch)
        assert [c.args[0] for c in p._parse_message.call_args_list] == ["D:0,1,", "D:1,1,"]

    def test_partial_packet_kept_for_next_read(self):
        p = self._protocol()
        p._parse_message = MagicMock()
        assert p._consume_packets("junk>\r\n<D:1,") == "<D:1,"
        p._parse_message.assert_not_called()

    def test_repeated_firmware_error_logged_once(self):
        p = self._protocol()
        for _ in range(3):
            p._handle_message("E", "No logic for K ")
        p._handle_message("E", "something else")
        assert p.logger.warning.call_count == 2


class TestArduinoReconnect:
    def test_reidentified_shocker_keeps_counts_and_stops_sequence(self):
        import shock
        config = MagicMock()
        config.get.return_value = 50
        with patch.object(shock.Shocker, "configure_shocker"):
            s = shock.Shocker(MagicMock(), config)
            s.attempted_shocks = 7
            s.shock_activated = True
            s.deactivate_shock = MagicMock()
            m = _make_apa(shock=s, arduino_ports={})
            m._refresh_hardware_fault = MagicMock()
            m._initialize_arduino = MagicMock()
            new_protocol = MagicMock(port="/dev/ttyACM1")
            m.handle_identity(new_protocol, "shock")
        m._initialize_arduino.assert_not_called()
        assert m.shock is s and s.attempted_shocks == 7
        assert s.arduino is new_protocol
        s.deactivate_shock.assert_called_once()
        assert m.communication.send_status.call_args.args[0]["type"] == "error"

    def test_reidentified_motor_is_stopped_if_it_was_rotating(self):
        from motor import Motor
        with patch.object(Motor, "configure_motor"):
            motor = Motor(MagicMock(), MagicMock())
            motor.rotating = True
            m = _make_apa(motor=motor, arduino_ports={})
            m._refresh_hardware_fault = MagicMock()
            m.handle_identity(motor.arduino, "motor")
        assert motor.rotating is False
        motor.arduino.send_command.assert_any_call("N", "")

    def test_startup_double_identity_is_quiet(self):
        from motor import Motor
        with patch.object(Motor, "configure_motor"):
            motor = Motor(MagicMock(), MagicMock())
            m = _make_apa(motor=motor, arduino_ports={})
            m._refresh_hardware_fault = MagicMock()
            m.handle_identity(motor.arduino, "motor")
        m.communication.send_status.assert_not_called()


def _port(device, description):
    info = MagicMock()
    info.device = device
    info.description = description
    return info


class TestDiscoveryReportsWhyArduinosAreMissing:
    """Unflashed boards or an unopenable port must leave the module running
    with a hardware_fault that says why, not crash-loop it."""

    _MOD = "src.modules.variants.apa_arduino.apa_arduino_module"

    def _discover(self, ports, protocol=None):
        m = _make_apa()
        m._get_available_ports = MagicMock(return_value=ports)
        with patch(f"{self._MOD}.Protocol", protocol or MagicMock()):
            m._find_arduino_ports()
        return m

    def test_no_ports(self):
        m = self._discover([])
        assert m.hardware_fault == (
            "motor and shock Arduino not connected - "
            "no Arduino serial ports (/dev/ttyACM*) found")

    def test_unflashed_boards_are_named(self):
        m = self._discover([_port("/dev/ttyACM0", "Arduino Leonardo"),
                            _port("/dev/ttyACM1", "Arduino Uno")])
        assert "/dev/ttyACM0 (Arduino Leonardo)" in m.hardware_fault
        assert "/dev/ttyACM1 (Arduino Uno)" in m.hardware_fault
        assert "firmware" in m.hardware_fault

    def test_port_that_wont_open_is_reported_not_raised(self):
        protocol = MagicMock(side_effect=OSError("Device or resource busy"))
        m = self._discover([_port("/dev/ttyACM0", "Arduino Uno")], protocol)
        assert "could not open /dev/ttyACM0 (Device or resource busy)" in m.hardware_fault
        assert "no Arduino serial ports" not in m.hardware_fault

    def test_non_acm_ports_are_ignored(self):
        m = self._discover([_port("/dev/ttyAMA10", "ttyAMA10")])
        assert "no Arduino serial ports" in m.hardware_fault

    def test_identified_port_drops_out_of_the_fault(self):
        m = self._discover([_port("/dev/ttyACM0", "Arduino Leonardo"),
                            _port("/dev/ttyACM1", "Arduino Uno")])
        m._initialize_arduino = MagicMock(side_effect=lambda i, p: setattr(m, "motor", MagicMock()))
        proto = MagicMock(port="/dev/ttyACM0")
        m.handle_identity(proto, "motor")
        m._refresh_hardware_fault()
        assert "ttyACM0" not in m.hardware_fault
        assert m.hardware_fault.startswith("shock Arduino not connected")
        assert "/dev/ttyACM1 (Arduino Uno)" in m.hardware_fault

    def test_unexpected_identity_is_reported_not_initialised(self):
        m = self._discover([_port("/dev/ttyACM0", "Arduino Uno")])
        m._initialize_arduino = MagicMock()
        m.handle_identity(MagicMock(port="/dev/ttyACM0"), "blink")
        m._initialize_arduino.assert_not_called()
        assert "/dev/ttyACM0 (identifies as 'blink')" in m.hardware_fault

    def test_all_connected_clears_fault(self):
        m = self._discover([])
        m.motor, m.shock = MagicMock(), MagicMock()
        m._refresh_hardware_fault()
        assert m.hardware_fault is None

    def test_cleanup_stops_unidentified_probes(self):
        probe = MagicMock()
        m = self._discover([_port("/dev/ttyACM0", "Arduino Uno")],
                           MagicMock(return_value=probe))
        m.cleanup()
        probe.stop.assert_called_once()


class TestArduinoStateMessage:
    def _shock(self, **kw):
        shock = MagicMock(
            shock_activated=False, shock_being_delivered=True, grid_is_live=True,
            attempted_shocks=3, attempted_shocks_from_arduino=3, delivered_shocks=2,
            _max_shocks=50, current_from_arduino=0.5,
            time_on_from_arduino=500.0, time_off_from_arduino=1000.0,
            _shock_lock=threading.Lock())
        for k, v in kw.items():
            setattr(shock, k, v)
        return shock

    def _state(self, shock):
        m = _make_apa(shock=shock, motor=MagicMock(speed_from_arduino=2.0, rotating=True,
                                                   speed=3, speed_error=None))
        m.send_controller_arduino_state()
        return m.communication.send_status.call_args.args[0]["state"]

    def test_reports_shock_settings_as_the_arduino_has_them(self):
        state = self._state(self._shock())
        assert state["current_ma"] == 0.5
        assert state["time_on_s"] == 0.5
        assert state["time_off_s"] == 1.0
        assert state["max_shocks"] == 50
        assert state["shock_being_delivered"] is True

    def test_reports_measured_and_target_rpm(self):
        state = self._state(self._shock())
        assert state["rpm"] == 2.0
        assert state["target_rpm"] == 3

    def test_unset_times_are_none(self):
        state = self._state(self._shock(current_from_arduino=None,
                                        time_on_from_arduino=-1.0,
                                        time_off_from_arduino=None))
        assert state["current_ma"] is None
        assert state["time_on_s"] is None
        assert state["time_off_s"] is None


class _DictConfig:
    """Just enough of Config for Motor: dotted get, and set() of a whole
    (sidecar) section."""

    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, persist=True):
        self.values[key] = value
        return True


class TestMotorFeedforward:
    def _motor(self, **config):
        from motor import Motor
        cfg = _DictConfig(**{
            "arduino.motor.motor_speed_rpm": 2,
            "arduino.motor._manual_motor_speed_for_2rpm": 95,
            "arduino.motor._pid_gains": [25.0, 2.0, 0.0],
            "arduino.motor._learn_after_stable_s": 30,
            **config,
        })
        return Motor(MagicMock(), cfg), cfg

    def test_configure_sends_setpoint_gains_and_default_feedforward(self):
        motor, _ = self._motor()
        send = motor.arduino.send_command
        send.assert_any_call("S", 2)
        send.assert_any_call("SET_PID", "25.0,2.0,0.0")
        send.assert_any_call("F", 95.0)

    def test_learned_value_wins_and_scales_to_other_setpoints(self):
        motor, _ = self._motor(motor_learned_output={"2": 100.0})
        assert motor.feedforward_for(2) == 100.0
        assert motor.feedforward_for(3) == 150.0

    def test_learns_after_holding_target_then_saves_once(self):
        motor, cfg = self._motor()
        motor.start_motor()
        with patch("motor.time.time", return_value=1000.0):
            motor.interpret_state("2.01,10.0,2.00,96.0,")
        with patch("motor.time.time", return_value=1031.0):
            motor.interpret_state("1.99,20.0,2.00,98.0,")
            motor.interpret_state("2.00,30.0,2.00,99.0,")
        assert cfg.values["motor_learned_output"] == {"2": 97.0}
        motor.arduino.send_command.assert_called_with("F", 97.0)

    def test_out_of_tolerance_restarts_the_stable_run(self):
        motor, cfg = self._motor()
        motor.start_motor()
        with patch("motor.time.time", return_value=1000.0):
            motor.interpret_state("2.00,10.0,2.00,96.0,")
        with patch("motor.time.time", return_value=1020.0):
            motor.interpret_state("1.50,10.0,2.00,130.0,")  # 25% under
        with patch("motor.time.time", return_value=1031.0):
            motor.interpret_state("2.00,10.0,2.00,96.0,")
        assert "motor_learned_output" not in cfg.values

    def test_old_firmware_state_without_output_learns_nothing(self):
        motor, cfg = self._motor()
        motor.start_motor()
        with patch("motor.time.time", return_value=1000.0):
            motor.interpret_state("2.00,10.0,2.00,")
        with patch("motor.time.time", return_value=1031.0):
            motor.interpret_state("2.00,10.0,2.00,")
        assert motor.output_from_arduino is None
        assert "motor_learned_output" not in cfg.values
