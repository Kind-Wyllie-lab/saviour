"""
Tests for src/modules/variants/ttl/ttl_module.py.

TTLModule.__init__ builds a real Config, GPIO pins (gpiozero) and an
MJPEGStreamServer, so every test constructs via TTLModule.__new__ (same
pattern as test_rfid_module.py / test_camera_base.py) and sets only the
attributes the method under test touches. assign_pins() is exercised for
real (it's pure dispatch logic once gpiozero.Button/LED are patched to
MagicMocks), rather than hand-building pin_configs, so the debounce_ms
wiring and the "None"-mode manual-output-pin path are covered end to end.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from src.modules.variants.ttl.ttl_module import TTLModule, TTLValue


def _make_ttl(**attrs) -> TTLModule:
    m = TTLModule.__new__(TTLModule)
    m.logger = MagicMock()
    m.output_pins = []
    m.input_pins = []
    m.pin_configs = {}
    m.experiment_clock_pins = []
    m.pseudorandom_pins = []
    m.interval_pulse_pins = []
    m.generator_threads = {}
    m.pin_state_buffers = {}
    m.pin_state_lock = threading.Lock()
    m.MONITOR_COLS = 500
    m.is_recording = False
    m._ttl_file_handle = None
    m._ttl_file_lock = threading.RLock()
    m.config = MagicMock()
    m.facade = MagicMock()
    m.communication = MagicMock()
    m.communication.controller_ip = "10.0.0.1"
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _cfg(pins: dict, active_logic: str = "active_low"):
    """MagicMock config.get side_effect covering ttl.pins / ttl.active_logic."""
    def _get(key, default=None):
        if key == "ttl.pins":
            return pins
        if key == "ttl.active_logic":
            return active_logic
        return default
    return _get


# ---------------------------------------------------------------------------
# assign_pins: debounce_ms wiring + the "None"/manual-output-pin mode
# ---------------------------------------------------------------------------

class TestAssignPinsDebounce:
    def test_debounce_ms_reaches_bounce_time(self):
        m = _make_ttl()
        m.config.get.side_effect = _cfg({"4": {"mode": "input", "debounce_ms": 50}})
        with patch("src.modules.variants.ttl.ttl_module.gpiozero") as gz:
            gz.Button.return_value = MagicMock()
            m.assign_pins()
        gz.Button.assert_called_once_with(4, bounce_time=0.05, pull_up=True)

    def test_missing_debounce_ms_defaults_to_zero(self):
        """Preserves the prior hardcoded bounce_time=0 when the key is absent."""
        m = _make_ttl()
        m.config.get.side_effect = _cfg({"5": {"mode": "input"}})
        with patch("src.modules.variants.ttl.ttl_module.gpiozero") as gz:
            gz.Button.return_value = MagicMock()
            m.assign_pins()
        gz.Button.assert_called_once_with(5, bounce_time=0.0, pull_up=True)

    def test_active_high_disables_pull_up(self):
        m = _make_ttl()
        m.config.get.side_effect = _cfg(
            {"6": {"mode": "input", "debounce_ms": 10}}, active_logic="active_high")
        with patch("src.modules.variants.ttl.ttl_module.gpiozero") as gz:
            gz.Button.return_value = MagicMock()
            m.assign_pins()
        gz.Button.assert_called_once_with(6, bounce_time=0.01, pull_up=False)


class TestAssignPinsManualOutputMode:
    """mode "None" (or an absent/None mode on an otherwise-output pin) is a
    plain output pin with no generator -- for test_pin()/pulse_pin()."""

    @pytest.mark.parametrize("mode_value", ["None", "none", None])
    def test_registers_as_output_with_no_generator(self, mode_value):
        m = _make_ttl()
        m.config.get.side_effect = _cfg({"19": {"mode": mode_value}})
        with patch("src.modules.variants.ttl.ttl_module.gpiozero") as gz:
            gz.LED.return_value = MagicMock()
            m.assign_pins()
        assert len(m.output_pins) == 1
        assert 19 in m.pin_configs
        assert m.experiment_clock_pins == []
        assert m.pseudorandom_pins == []
        assert m.interval_pulse_pins == []

    def test_generator_modes_still_tracked(self):
        m = _make_ttl()
        m.config.get.side_effect = _cfg({
            "19": {"mode": "experiment_clock"},
            "26": {"mode": "pseudorandom"},
            "27": {"mode": "interval_pulse"},
        })
        with patch("src.modules.variants.ttl.ttl_module.gpiozero") as gz:
            gz.LED.return_value = MagicMock()
            m.assign_pins()
        assert m.experiment_clock_pins == [19]
        assert m.pseudorandom_pins == [26]
        assert m.interval_pulse_pins == [27]


# ---------------------------------------------------------------------------
# _check_recording_alive
# ---------------------------------------------------------------------------

class TestCheckRecordingAlive:
    def test_not_recording_is_always_healthy(self):
        dead = MagicMock(is_alive=lambda: False)
        m = _make_ttl(is_recording=False, generator_threads={1: dead})
        assert m._check_recording_alive() == (True, None)

    def test_recording_with_live_generators_is_healthy(self):
        alive = MagicMock()
        alive.is_alive.return_value = True
        m = _make_ttl(is_recording=True, generator_threads={19: alive, 26: alive})
        assert m._check_recording_alive() == (True, None)

    def test_recording_with_no_generator_pins_is_healthy(self):
        """Input-only recordings (no output/generator pins configured)."""
        m = _make_ttl(is_recording=True, generator_threads={})
        assert m._check_recording_alive() == (True, None)

    def test_dead_generator_thread_reported(self):
        alive = MagicMock()
        alive.is_alive.return_value = True
        dead = MagicMock()
        dead.is_alive.return_value = False
        m = _make_ttl(is_recording=True, generator_threads={19: alive, 26: dead})
        ok, detail = m._check_recording_alive()
        assert ok is False
        assert "26" in detail

    def test_finite_interval_pulse_finishing_is_not_a_fault(self):
        """A repeat_count > 0 interval_pulse pin (e.g. a single delayed
        "recording started" marker, or any bounded burst) is *supposed* to
        finish and its thread exit once it's delivered its configured
        pulses -- see _interval_pulse_worker's own break condition. That's
        success, not a crash."""
        finished = MagicMock(is_alive=lambda: False)
        m = _make_ttl(
            is_recording=True,
            generator_threads={19: finished},
            pin_configs={19: {"mode": "interval_pulse", "repeat_count": 1}},
        )
        assert m._check_recording_alive() == (True, None)

    def test_infinite_interval_pulse_dying_is_still_a_fault(self):
        """repeat_count == 0 means "run until the recording stops" -- that
        thread dying early is a real fault, same as any other generator."""
        dead = MagicMock(is_alive=lambda: False)
        m = _make_ttl(
            is_recording=True,
            generator_threads={19: dead},
            pin_configs={19: {"mode": "interval_pulse", "repeat_count": 0}},
        )
        ok, detail = m._check_recording_alive()
        assert ok is False
        assert "19" in detail

    def test_other_modes_dying_are_still_a_fault(self):
        """experiment_clock/pseudorandom are infinite for the life of the
        recording -- only interval_pulse gets the finite-completion pass."""
        dead = MagicMock(is_alive=lambda: False)
        m = _make_ttl(
            is_recording=True,
            generator_threads={19: dead},
            pin_configs={19: {"mode": "experiment_clock"}},
        )
        ok, detail = m._check_recording_alive()
        assert ok is False
        assert "19" in detail


# ---------------------------------------------------------------------------
# pulse_pin
# ---------------------------------------------------------------------------

def _output_pin(number: int):
    p = MagicMock()
    p.pin.number = number
    return p


class TestPulsePin:
    def test_unconfigured_pin_errors(self):
        m = _make_ttl()
        result = m.pulse_pin(99)
        assert result["result"] == "error"
        assert "not configured" in result["message"]

    def test_non_output_pin_errors(self):
        """A configured input pin has no entry in output_pins."""
        m = _make_ttl(pin_configs={4: {"mode": "input"}})
        result = m.pulse_pin(4)
        assert result["result"] == "error"
        assert "not an output pin" in result["message"]

    def test_pin_driven_by_live_generator_is_refused(self):
        gen = MagicMock()
        gen.is_alive.return_value = True
        m = _make_ttl(
            pin_configs={19: {"mode": "experiment_clock"}},
            output_pins=[_output_pin(19)],
            generator_threads={19: gen},
        )
        result = m.pulse_pin(19)
        assert result["result"] == "error"
        assert "experiment_clock" in result["message"]

    def test_stale_dead_generator_does_not_block(self):
        dead = MagicMock()
        dead.is_alive.return_value = False
        m = _make_ttl(
            pin_configs={19: {"mode": "None", "description": "marker"}},
            output_pins=[_output_pin(19)],
            generator_threads={19: dead},
        )
        m._ttl_file_handle = MagicMock()
        with patch("src.modules.variants.ttl.ttl_module.time.sleep"), \
             patch("src.modules.variants.ttl.ttl_module.time.time_ns",
                   side_effect=[1_000, 2_000]):
            result = m.pulse_pin(19, duration_ms=20)
        assert result["result"] == "success"

    def test_drives_active_then_inactive_and_reports_edges(self):
        pin_obj = _output_pin(19)
        m = _make_ttl(
            pin_configs={19: {"mode": "None", "description": "marker"}},
            output_pins=[pin_obj],
        )
        m._ttl_file_handle = MagicMock()
        with patch("src.modules.variants.ttl.ttl_module.time.sleep") as sleep_mock, \
             patch("src.modules.variants.ttl.ttl_module.time.time_ns",
                   side_effect=[1_000_000, 2_000_000]):
            result = m.pulse_pin(19, duration_ms=20)

        assert result == {
            "result": "success",
            "pin": 19,
            "duration_ms": 20.0,
            "onset_ns": 1_000_000,
            "offset_ns": 2_000_000,
        }
        # active_low default: active = off(), inactive = on()
        pin_obj.off.assert_called_once()
        pin_obj.on.assert_called_once()
        sleep_mock.assert_called_once_with(0.02)

        written = [c.args[0] for c in m._ttl_file_handle.write.call_args_list]
        assert len(written) == 2
        assert written[0].startswith("1000000,19,None,TTLValue.HIGH,marker [api]")
        assert written[1].startswith("2000000,19,None,TTLValue.LOW,marker [api]")

    def test_duration_clamped_to_max(self):
        pin_obj = _output_pin(19)
        m = _make_ttl(
            pin_configs={19: {"mode": "None"}},
            output_pins=[pin_obj],
        )
        m._ttl_file_handle = MagicMock()
        with patch("src.modules.variants.ttl.ttl_module.time.sleep") as sleep_mock, \
             patch("src.modules.variants.ttl.ttl_module.time.time_ns",
                   side_effect=[0, 1]):
            result = m.pulse_pin(19, duration_ms=999_999)
        assert result["duration_ms"] == TTLModule._MAX_PULSE_MS
        sleep_mock.assert_called_once_with(TTLModule._MAX_PULSE_MS / 1000.0)


# ---------------------------------------------------------------------------
# _write_ttl_event: source tag + missing-description handling
# ---------------------------------------------------------------------------

class TestWriteTtlEvent:
    def test_no_source_matches_prior_format(self):
        m = _make_ttl(pin_configs={4: {"mode": "input", "description": "lever"}})
        m._ttl_file_handle = MagicMock()
        m._write_ttl_event(123, 4, TTLValue.LOW)
        line = m._ttl_file_handle.write.call_args.args[0]
        assert line == "123,4,input,TTLValue.LOW,lever\n"

    def test_missing_description_writes_empty_not_literal_none(self):
        m = _make_ttl(pin_configs={4: {"mode": "input"}})
        m._ttl_file_handle = MagicMock()
        m._write_ttl_event(123, 4, TTLValue.LOW)
        line = m._ttl_file_handle.write.call_args.args[0]
        assert line == "123,4,input,TTLValue.LOW,\n"

    def test_source_appended_to_description(self):
        m = _make_ttl(pin_configs={19: {"mode": "None", "description": "marker"}})
        m._ttl_file_handle = MagicMock()
        m._write_ttl_event(1, 19, TTLValue.HIGH, source="api")
        line = m._ttl_file_handle.write.call_args.args[0]
        assert line == "1,19,None,TTLValue.HIGH,marker [api]\n"

    def test_source_with_no_description(self):
        m = _make_ttl(pin_configs={19: {"mode": "None"}})
        m._ttl_file_handle = MagicMock()
        m._write_ttl_event(1, 19, TTLValue.HIGH, source="api")
        line = m._ttl_file_handle.write.call_args.args[0]
        assert line == "1,19,None,TTLValue.HIGH,[api]\n"


# ---------------------------------------------------------------------------
# _send_edge_status: input edges pushed live to the controller
# ---------------------------------------------------------------------------

class TestSendEdgeStatus:
    def test_low_edge_sends_status_and_writes_csv(self):
        pin_obj = MagicMock()
        pin_obj.pin.number = 4
        m = _make_ttl(pin_configs={4: {"mode": "input", "description": "lever"}})
        m._ttl_file_handle = MagicMock()

        with patch(
            "src.modules.variants.ttl.ttl_module.time.time_ns", return_value=1_000
        ):
            m._handle_input_pin_low(pin_obj)

        line = m._ttl_file_handle.write.call_args.args[0]
        assert line == "1000,4,input,TTLValue.LOW,lever\n"
        m.communication.send_status.assert_called_once_with({
            "type": "ttl_edge",
            "pin": 4,
            "state": "LOW",
            "mode": "input",
            "description": "lever",
            "timestamp_ns": 1_000,
        })

    def test_high_edge_sends_status(self):
        pin_obj = MagicMock()
        pin_obj.pin.number = 4
        m = _make_ttl(pin_configs={4: {"mode": "input"}})
        m._ttl_file_handle = MagicMock()

        with patch(
            "src.modules.variants.ttl.ttl_module.time.time_ns", return_value=2_000
        ):
            m._handle_input_pin_high(pin_obj)

        sent = m.communication.send_status.call_args.args[0]
        assert sent["state"] == "HIGH"
        assert sent["timestamp_ns"] == 2_000
        assert sent["description"] == ""  # missing key -> "" not None

    def test_no_controller_no_status_sent(self):
        pin_obj = MagicMock()
        pin_obj.pin.number = 4
        m = _make_ttl(pin_configs={4: {"mode": "input"}})
        m._ttl_file_handle = MagicMock()
        m.communication.controller_ip = None

        m._handle_input_pin_low(pin_obj)

        m.communication.send_status.assert_not_called()

    def test_generator_pulses_do_not_send_edge_status(self):
        """_send_edge_status is only wired into the input-pin callbacks --
        generator-driven pulses go through _write_ttl_event directly and
        must not also flood the ZMQ status channel."""
        m = _make_ttl(pin_configs={19: {"mode": "experiment_clock"}})
        m._ttl_file_handle = MagicMock()
        m._write_ttl_event(1, 19, TTLValue.HIGH)
        m.communication.send_status.assert_not_called()


# ---------------------------------------------------------------------------
# Segment-rotation lock: no event dropped racing _start_next_recording_segment
# ---------------------------------------------------------------------------

class TestSegmentRotationLock:
    def test_lock_is_reentrant_rlock(self):
        m = _make_ttl()
        assert isinstance(m._ttl_file_lock, type(threading.RLock()))

    def test_rotation_closes_stages_and_reopens(self, tmp_path):
        m = _make_ttl(pin_configs={19: {"mode": "None"}})
        first = str(tmp_path / "seg0.csv")
        second = str(tmp_path / "seg1.csv")
        m._get_ttl_filename = MagicMock(return_value=second)
        m._open_ttl_file(first)
        m.current_ttl_events_filename = first

        m._start_next_recording_segment()

        m.facade.stage_file_for_export.assert_called_once_with(first)
        m.facade.add_session_file.assert_called_once_with(second)
        assert m.current_ttl_events_filename == second
        assert m._ttl_file_handle is not None
        m._ttl_file_handle.close()

    def test_no_event_lost_racing_concurrent_rotation(self, tmp_path):
        """Regression test for the TOCTOU this session's plan flagged:
        hammer _write_ttl_event from one thread while another repeatedly
        rotates the segment file, and confirm every single write lands in
        *some* file -- none silently dropped because _ttl_file_handle was
        briefly None, and no exception from writing to a closed handle."""
        m = _make_ttl(pin_configs={19: {"mode": "None"}})
        seg_counter = iter(range(10_000))
        m._get_ttl_filename = MagicMock(
            side_effect=lambda: str(tmp_path / f"seg{next(seg_counter)}.csv"))
        first = str(tmp_path / "seg_start.csv")
        m._open_ttl_file(first)
        m.current_ttl_events_filename = first

        n_writes = 500
        errors = []

        def writer():
            try:
                for i in range(n_writes):
                    m._write_ttl_event(i, 19, TTLValue.HIGH)
            except Exception as exc:  # pragma: no cover -- fails the test below
                errors.append(exc)

        def rotator():
            try:
                for _ in range(50):
                    m._start_next_recording_segment()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        t_write = threading.Thread(target=writer)
        t_rotate = threading.Thread(target=rotator)
        t_write.start()
        t_rotate.start()
        t_write.join(timeout=30)
        t_rotate.join(timeout=30)

        assert not errors, f"exception(s) during concurrent write/rotate: {errors}"
        m._close_ttl_event_file()

        total_rows = 0
        for csv_file in tmp_path.glob("*.csv"):
            with open(csv_file) as f:
                total_rows += sum(1 for line in f) - 1  # minus header
        assert total_rows == n_writes
