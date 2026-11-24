"""
Tests for src/modules/variants/sound/sound_module.py.

SoundModule.__init__ probes real ALSA hardware, so tests construct via
SoundModule.__new__(SoundModule) and set only what the method under test
reads (same pattern as test_camera_base.py). Card detection itself is in
test_sound_hardware.py, the output stream in test_sound_stream.py.
"""

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile

from src.modules.variants.sound.sound_module import CSV_HEADER, SoundModule

_MOD = "src.modules.variants.sound.sound_module"

DAC8X = {"present": True, "board": "HiFiBerry DAC8x", "card_id": "sndrpihifiberry",
         "card_index": 2, "device": "hw:CARD=sndrpihifiberry,DEV=0", "channels": 8,
         "outputs": 4, "presence_verified": False, "channels_from": "alsa",
         "detail": "HiFiBerry DAC8x: 8 channels, 4 stereo outputs"}
NO_CARD = {"present": False, "board": None, "card_id": None, "card_index": None,
           "device": None, "channels": 0, "outputs": 0, "presence_verified": False,
           "channels_from": None, "detail": "No sound card (other than HDMI) found"}


def _make_sound(**attrs) -> SoundModule:
    m = SoundModule.__new__(SoundModule)
    m.logger = MagicMock()
    m._sounds_dir = "/nonexistent/sounds"
    m.hardware_fault = None
    m.hardware = {}
    m.sound_to_play = None
    m._stream = None
    m._stream_lock = threading.Lock()
    m._protocol_status = {"running": False}
    m._protocol_error = None
    m._sound_file_handle = None
    m._sound_file_lock = threading.Lock()
    m.current_sound_event_file = None
    m.module_id = "sound_9f00"
    m.config = MagicMock()
    m.config.get.side_effect = lambda k, d=None: {
        "sound.volume": 1, "sound.duration": 1,
        "sound.output_latency_offset_ms": 0}.get(k, d)
    m.facade = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestGetAvailableSounds:
    def test_missing_sounds_dir_returns_empty_list_not_crash(self):
        m = _make_sound()
        assert m._get_available_sounds() == []
        m.logger.warning.assert_called_once()

    def test_lists_only_files(self, tmp_path):
        (tmp_path / "a.wav").write_bytes(b"x")
        (tmp_path / "b.wav").write_bytes(b"x")
        (tmp_path / "subdir").mkdir()
        m = _make_sound(_sounds_dir=str(tmp_path))
        assert sorted(m._get_available_sounds()) == ["a.wav", "b.wav"]


class TestHardware:
    def test_reported_in_the_sound_hardware_section(self):
        m = _make_sound()
        with patch(f"{_MOD}.sound_hardware.detect", return_value=dict(DAC8X)), \
             patch(f"{_MOD}.sound_hardware.sound_files", return_value={"t.wav": 2.0}):
            ok, msg = m._check_hifiberry()
        assert ok is True and m.hardware_fault is None and "DAC8x" in msg
        m.config.set.assert_called_once()
        section, value = m.config.set.call_args.args
        assert section == "sound_hardware"
        assert value["outputs"] == 4 and value["files"] == {"t.wav": 2.0}

    def test_absent_sets_hardware_fault_and_blocks_readiness(self):
        m = _make_sound()
        with patch(f"{_MOD}.sound_hardware.detect", return_value=dict(NO_CARD)), \
             patch(f"{_MOD}.sound_hardware.sound_files", return_value={}):
            ok, msg = m._check_hifiberry()
        assert ok is False and m.hardware_fault == msg == NO_CARD["detail"]

    def test_channel_count_kept_while_a_stream_holds_the_card(self):
        m = _make_sound(hardware={**DAC8X, "files": {}}, _stream=MagicMock())
        board_guess = {**DAC8X, "channels": 2, "outputs": 1, "channels_from": "board"}
        with patch(f"{_MOD}.sound_hardware.detect", return_value=board_guess) as det, \
             patch(f"{_MOD}.sound_hardware.sound_files", return_value={}):
            hw = m._refresh_hardware(query_channels=True)
        assert det.call_args.kwargs["run"] is None   # didn't open the device
        assert hw["outputs"] == 4 and hw["channels_from"] == "alsa"

    def test_playback_device_uses_plughw(self):
        m = _make_sound(hardware=dict(DAC8X))
        assert m._playback_device() == "plughw:CARD=sndrpihifiberry,DEV=0"


class TestPlaySound:
    def test_returns_error_when_no_sound_selected(self):
        m = _make_sound(sound_to_play=None)
        assert m._play_sound() == {
            "result": "error", "message": "no sound file available",
        }

    def test_paths_and_missing_outputs_refused(self, tmp_path):
        (tmp_path / "t.wav").write_bytes(b"x")
        m = _make_sound(_sounds_dir=str(tmp_path), hardware={**DAC8X, "outputs": 1})
        assert m._play_sound("../t.wav")["result"] == "error"
        assert m._play_sound("missing.wav")["result"] == "error"
        r = m._play_sound("t.wav", output=2)
        assert r["result"] == "error" and "output 2" in r["message"]

    def test_mixed_into_the_session_stream_when_one_is_open(self, tmp_path):
        soundfile.write(tmp_path / "t.wav", np.zeros(4800, np.float32), 48_000)
        stream = MagicMock(running=True)
        m = _make_sound(_sounds_dir=str(tmp_path), hardware=dict(DAC8X), _stream=stream)
        r = m._play_sound("t.wav", output=3, volume=0.5, duration_s=0.05)
        assert r == {"result": "success", "via": "session stream"}
        target, samples, output, meta = stream.schedule.call_args.args
        assert target is None and output == 3 and len(samples) == 2400
        assert meta["source"] == "manual"


PROTOCOL = {"version": 1, "seed": 4711, "steps": [
    {"at_s": 30, "action": "repeat", "times": 5, "every_s": 5, "jitter_s": [0, 20],
     "steps": [
         {"at_s": 0, "action": "pulse", "params": {"pins": [4, 6], "width_s": 2}},
         {"at_s": 0, "action": "play",
          "params": {"file": "tone.wav", "duration_s": 2, "output": 2}}]}]}


class FakeStream:
    instances = []

    def __init__(self, device, channels, **kw):
        self.device, self.channels, self.kw = device, channels, kw
        self.latency_s = 0.025
        self.scheduled = []
        self.running = False
        self.error = None
        FakeStream.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def schedule(self, target, samples, output, meta=None):
        self.scheduled.append((target, len(samples), output, meta))

    def pending(self):
        return len(self.scheduled)


class TestSessionProtocol:
    def _module(self, tmp_path, protocol=PROTOCOL, t0=None):
        sounds = tmp_path / "sounds"
        sounds.mkdir()
        soundfile.write(sounds / "tone.wav", np.full(3 * 48_000, 0.1, np.float32), 48_000)
        rec = tmp_path / "rec"
        rec.mkdir()
        m = _make_sound(_sounds_dir=str(sounds))
        m.recording = SimpleNamespace(
            session_protocol=protocol,
            session_protocol_start_at=t0 if t0 is not None else time.time(),
            recording_intended_start_at=None, recording_start_time=None)
        m.facade.get_filename_prefix.return_value = str(rec / "exp1_sound_9f00")
        m.facade.get_segment_id.return_value = 0
        m.facade.get_utc_time.return_value = "20261124-120000"
        m.facade.get_current_session_name.return_value = "exp1"
        return m

    def _detect(self, hw=DAC8X):
        return patch(f"{_MOD}.sound_hardware.detect", return_value=dict(hw))

    def test_cues_scheduled_on_the_session_clock(self, tmp_path):
        m = self._module(tmp_path, t0=time.time())
        with self._detect(), patch(f"{_MOD}.SoundStream", FakeStream):
            assert m._start_new_recording() is True
        stream = FakeStream.instances[-1]
        assert stream.device == "plughw:CARD=sndrpihifiberry,DEV=0" and stream.channels == 8
        t0 = m.recording.session_protocol_start_at
        targets = [t - t0 for t, *_ in stream.scheduled]
        assert len(targets) == 5 and targets[0] == 30
        assert all(n == 2 * 48_000 and out == 2 for _, n, out, _ in stream.scheduled)
        record_path = m.current_sound_event_file[:-4] + "_protocol.json"
        record = json.load(open(record_path))
        assert record["format"] == "saviour.sound_protocol/1"
        assert [c["t_s"] for c in record["cues"]] == pytest.approx(targets, abs=1e-5)
        assert record["output_latency_ms"] == 25.0
        m._stop_recording()
        assert stream.running is False and m._protocol_status["running"] is False

    def test_fired_sounds_logged_with_lateness(self, tmp_path):
        m = self._module(tmp_path)
        with self._detect(), patch(f"{_MOD}.SoundStream", FakeStream):
            m._start_new_recording()
        meta = FakeStream.instances[-1].scheduled[0][3]
        m._on_sound_fired(meta, meta["planned_at"] + 0.012, 0.012)
        m._close_sound_event_file()
        lines = open(m.current_sound_event_file).read().splitlines()
        assert CSV_HEADER.strip() in lines
        row = lines[-1].split(",")
        assert row[1] == "tone.wav" and row[4] == "2"
        assert row[5].startswith("protocol step=0.0.1 play late_ms=12.0")
        assert m._protocol_status["late"] == 1

    def test_rearm_skips_past_sounds(self, tmp_path):
        m = self._module(tmp_path, t0=time.time() - 300)   # all trials are past
        with self._detect(), patch(f"{_MOD}.SoundStream", FakeStream):
            assert m._start_new_recording() is True
        assert FakeStream.instances[-1].scheduled == []
        assert m._protocol_status["missed"] == 5

    def test_invalid_protocol_refuses_the_recording(self, tmp_path):
        bad = {"version": 1, "steps": [
            {"at_s": 1, "action": "play", "params": {"file": "tone.wav", "output": 5}}]}
        m = self._module(tmp_path, protocol=bad)
        with self._detect():
            assert m._start_new_recording() is False
        status = m.facade.send_status.call_args.args[0]
        assert status["reason"] == "sound_protocol" and "output 5" in status["error"]

    def test_no_card_refuses_a_protocol_with_sounds(self, tmp_path):
        m = self._module(tmp_path)
        with self._detect(NO_CARD):
            assert m._start_new_recording() is False
        assert "can't start" in m.facade.send_status.call_args.args[0]["error"]

    def test_protocol_without_sounds_needs_no_card(self, tmp_path):
        ttl_only = {"version": 1, "steps": [
            {"at_s": 1, "action": "pulse", "params": {"pin": 4, "width_s": 1}}]}
        m = self._module(tmp_path, protocol=ttl_only)
        with self._detect(NO_CARD), patch(f"{_MOD}.SoundStream", FakeStream) as fs:
            n = len(FakeStream.instances)
            assert m._start_new_recording() is True
        assert len(fs.instances) == n   # no stream opened

    def test_stream_failure_fails_liveness(self):
        m = _make_sound()
        assert m._check_recording_alive() == (True, None)
        m._on_stream_error("aplay exited (1)")
        ok, detail = m._check_recording_alive()
        assert ok is False and "aplay exited" in detail
