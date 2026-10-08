"""
Tests for src/modules/variants/apa_arduino/apa_arduino_module.py.

Scope: the shutdown / hardware-safety paths and the supervised state loop.
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
