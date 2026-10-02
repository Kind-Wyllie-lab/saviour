"""
Tests for src/modules/variants/microphone/microphone_module.py.

Constructs via __new__ (AudiomothModule.__init__ builds the full Module
stack and scans for AudioMoths) and fakes the soundcard recorder + the
soundfile writer, so the real recording threads run against in-memory
stand-ins.
"""

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np

try:  # soundcard talks to PulseAudio at import time; CI has no audio server
    import soundcard  # noqa: F401
except Exception:  # pragma: no cover - depends on the host
    sys.modules["soundcard"] = MagicMock()

from src.modules.variants.microphone import microphone_module as mm  # noqa: E402

SERIALS = ("AAAA", "BBBB")


class _FakeRecorder:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        time.sleep(0.005)
        return np.zeros((numframes, 1), dtype=np.float32)


class _FakeSoundFile:
    """Counts blocks written per filename; tracks which files are open."""

    writes: dict = {}
    open_files: set = set()
    lock = threading.Lock()

    def __init__(self, filename, **_kw):
        self.filename = filename

    def __enter__(self):
        with self.lock:
            self.open_files.add(self.filename)
        return self

    def __exit__(self, *exc):
        with self.lock:
            self.open_files.discard(self.filename)
        return False

    def write(self, _data):
        with self.lock:
            self.writes[self.filename] = self.writes.get(self.filename, 0) + 1


def _make(tmp_path):
    m = mm.AudiomothModule.__new__(mm.AudiomothModule)
    m.logger = MagicMock()
    m.config = MagicMock()
    m.config.get.side_effect = lambda key, default=None: (
        0 if key == "recording.mic_throughput_log_secs" else default)
    m.facade = MagicMock()
    m.audiomoths = {s: f"mic-{s}" for s in SERIALS}
    m.audiomoth_threads = []
    m.current_recording_files = {}
    m._segment_stop_event = threading.Event()
    m._recording_stop_event = threading.Event()
    m.is_recording = False
    m._session_clipped_samples = 0
    m._session_total_samples = 0
    m._sync_pulser = None
    m._sync_lock = threading.Lock()
    m._sync_pulses_csv = None
    m.last_sync_selftest = None
    m._find_audiomoths = lambda: None
    m._label_for = lambda serial: f"audiomoth_{serial}"
    segment = {"n": 0}

    def filename(serial):
        return str(tmp_path / f"seg{segment['n']}_{serial}.flac")

    m._get_audio_filename = filename
    return m, segment


@patch.object(mm, "soundfile")
@patch.object(mm, "soundcard")
def test_rotation_stops_the_previous_segments_threads(soundcard, soundfile,
                                                      tmp_path):
    """Desk run 2026-10-01: rotation never signalled the outgoing threads,
    so each one kept writing to its (exported, deleted) file until the
    session ended and the mic's disk filled after ~70 min."""
    _FakeSoundFile.writes, _FakeSoundFile.open_files = {}, set()
    soundfile.SoundFile = _FakeSoundFile
    soundcard.get_microphone.return_value.recorder.return_value = _FakeRecorder()
    m, segment = _make(tmp_path)

    assert m._start_new_recording()
    first_threads = list(m.audiomoth_threads)
    first_files = set(m.current_recording_files.values())
    time.sleep(0.1)

    segment["n"] = 1
    m._start_next_recording_segment()
    try:
        assert not any(t.is_alive() for t in first_threads)
        assert not (first_files & _FakeSoundFile.open_files)
        frozen = {f: _FakeSoundFile.writes.get(f, 0) for f in first_files}
        time.sleep(0.1)
        assert {f: _FakeSoundFile.writes.get(f, 0) for f in first_files} == frozen
        # ...and the new segment is recording.
        assert all(t.is_alive() for t in m.audiomoth_threads)
        assert len(m.audiomoth_threads) == len(SERIALS)
    finally:
        m._stop_recording()
    assert not any(t.is_alive() for t in m.audiomoth_threads + first_threads)
    m.logger.error.assert_not_called()


@patch.object(mm, "soundfile")
@patch.object(mm, "soundcard")
def test_new_session_gets_fresh_stop_events(soundcard, soundfile, tmp_path):
    """Convention (supervised threads): never clear() a used Event; a thread
    still holding the old one must not be revived by the next start."""
    soundfile.SoundFile = _FakeSoundFile
    soundcard.get_microphone.return_value.recorder.return_value = _FakeRecorder()
    m, _segment = _make(tmp_path)
    old_seg, old_rec = m._segment_stop_event, m._recording_stop_event
    old_rec.set()

    assert m._start_new_recording()
    try:
        assert m._segment_stop_event is not old_seg
        assert m._recording_stop_event is not old_rec
        assert old_rec.is_set()
    finally:
        m._stop_recording()


# ---------------------------------------------------------------------------
# A/V sync test rig: sync_selftest / sync_pulses (docs/AV_SYNC_TEST.md)
# ---------------------------------------------------------------------------

SYNC_RATE = 48_000
SYNC_LATENCY_S = 0.030   # the fake audio path places each buzz 30 ms late


class _ClockedRecorder:
    """Real-time fake AudioMoth stream: sample k is captured at t0 + k/rate
    (t0 = stream open, which is where the module stamps STARTED), and a
    3 kHz burst lands SYNC_LATENCY_S after every pulse the fake pulser
    fires."""

    def __init__(self, edges):
        self.edges = edges

    def __enter__(self):
        self.t0 = time.time()
        self.n = 0
        self.rng = np.random.default_rng(0)
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        target = self.t0 + (self.n + numframes) / SYNC_RATE
        delay = target - time.time()
        if delay > 0:
            time.sleep(delay)
        x = self.rng.normal(0, 0.002, numframes)
        k = np.arange(int(0.05 * SYNC_RATE))
        burst = 0.5 * np.sin(2 * np.pi * 3000 * k / SYNC_RATE)
        for e in list(self.edges):
            at = round((e / 1e9 + SYNC_LATENCY_S - self.t0) * SYNC_RATE) - self.n
            lo, hi = max(0, at), min(numframes, at + burst.size)
            if lo < hi:
                x[lo:hi] += burst[lo - at:hi - at]
        self.n += numframes
        return x[:, None].astype(np.float32)


class _FakePulser:
    instances: list = []
    all_edges: list = []     # read by _ClockedRecorder as edges happen

    def __init__(self, buzzer_pin, led_pin, drive="dc", tone_hz=4000.0):
        self.buzzer_pin, self.led_pin = buzzer_pin, led_pin
        self.drive, self.tone_hz = drive, tone_hz
        self.edges: list = []
        _FakePulser.instances.append(self)

    def pulse(self, duration_ms):
        from src.modules.sync_pulser import PulseEdge
        t = time.time_ns()
        self.edges.append(t)
        _FakePulser.all_edges.append(t)
        time.sleep(duration_ms / 1000)
        return PulseEdge(on_ns=t, on_spread_ns=2000, off_ns=time.time_ns())

    def close(self):
        pass


def _sync_module(tmp_path, buzzer_pin=17):
    m, _ = _make(tmp_path)
    cfg = {
        "recording.mic_throughput_log_secs": 0,
        "audiomoth.sample_rate": SYNC_RATE,
        "microphone.frame_num": 4800,
        "microphone.block_size": 4800,
        "sync_pulse.buzzer_pin": buzzer_pin,
        "sync_pulse.led_pin": 27,
    }
    m.config.get.side_effect = lambda key, default=None: cfg.get(key, default)
    m.audiomoths = {"AAAA": "mic-AAAA"}
    return m


def _sf_probe(path):
    import soundfile
    info = soundfile.info(path)
    return int(info.frames), int(info.samplerate)


def test_sync_selftest_measures_buzz_to_audio_delay(tmp_path):
    """End to end through the real recorder thread, FLAC writer, sidecar,
    audio_align.parse_mic_sidecar anchor and onset detector: a buzz the
    fake stream delays by 30 ms must come back as ~+30 ms."""
    from src.controller import audio_align

    _FakePulser.instances, _FakePulser.all_edges = [], []
    m = _sync_module(tmp_path)
    with patch.object(mm, "SyncPulser", _FakePulser), \
         patch.object(audio_align, "_probe_audio", _sf_probe), \
         patch.object(mm, "soundcard") as soundcard:
        soundcard.get_microphone.return_value.recorder.side_effect = (
            lambda **_kw: _ClockedRecorder(_FakePulser.all_edges))
        ack = m.sync_selftest(pulses=3, interval_s=1.0, pulse_ms=20, lead_s=2.0)
        assert ack["result"] == "started"
        deadline = time.time() + 40
        while not m.facade.send_status.called and time.time() < deadline:
            time.sleep(0.05)

    status = m.facade.send_status.call_args[0][0]
    assert status["type"] == "sync_selftest_result"
    assert status["status"] == "ok", status
    r = status["microphones"]["audiomoth_AAAA"]
    assert r["detected"] == 3
    # +up to one GIL switch interval (5 ms): STARTED is stamped in Python
    # after the stream opens, and another thread can hold the GIL between
    # the two -- a real (small) jitter source on the device too.
    assert SYNC_LATENCY_S * 1e3 - 1 <= r["mean_ms"] <= SYNC_LATENCY_S * 1e3 + 7
    assert r["std_ms"] < 1.0
    assert m.last_sync_selftest is not None
    m.facade.add_session_file.assert_not_called()   # scratch, never exported


def test_sync_selftest_refuses_while_recording_or_unconfigured(tmp_path):
    m = _sync_module(tmp_path)
    m.is_recording = True
    assert m.sync_selftest()["result"] == "error"
    m = _sync_module(tmp_path, buzzer_pin=None)
    with patch.object(mm, "SyncPulser", _FakePulser):
        assert "buzzer_pin" in m.sync_selftest()["message"]


def test_sync_pulses_logs_edges_and_csv_is_staged_at_stop(tmp_path):
    _FakePulser.instances = []
    m = _sync_module(tmp_path)
    m.facade.get_filename_prefix.return_value = str(tmp_path / "sess_audiomoth_4703")
    m.facade.get_utc_time.return_value = "20261002-100000"
    m.facade.get_segment_id.return_value = 0
    assert m.sync_pulses()["result"] == "error"          # not recording

    m.is_recording = True
    with patch.object(mm, "SyncPulser", _FakePulser):
        ack = m.sync_pulses(count=2, interval_s=1.0, pulse_ms=10)
        assert ack["result"] == "started"
        assert m.sync_pulses()["result"] == "error"      # one train at a time
        deadline = time.time() + 10
        while m._sync_lock.locked() and time.time() < deadline:
            time.sleep(0.02)

    path = tmp_path / "sess_audiomoth_4703_sync_pulses_(0_20261002-100000).csv"
    rows = path.read_text().strip().splitlines()
    assert rows[0].startswith("edge_on_ns,")
    assert len(rows) == 3
    m.facade.add_session_file.assert_called_with(str(path))

    m.recording_start_time = time.time()
    m._stop_recording()
    staged = [c.args[0] for c in m.facade.stage_file_for_export.call_args_list]
    assert str(path) in staged
