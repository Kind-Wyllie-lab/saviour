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
