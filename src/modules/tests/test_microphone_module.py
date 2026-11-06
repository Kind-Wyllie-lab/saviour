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


class _LatencyClockedRecorder(_ClockedRecorder):
    """Also reports a stream latency, as PipeWire does: how long ago the
    next unread sample was captured, plus a fixed per-device bias (what
    the self-test calibrates out)."""

    BIAS_S = -0.006

    @property
    def latency(self):
        return time.time() - (self.t0 + self.n / SYNC_RATE) + self.BIAS_S


def _run_selftest(m, recorder_cls):
    from src.controller import audio_align

    _FakePulser.instances, _FakePulser.all_edges = [], []
    m.facade.send_status.reset_mock()
    with patch.object(mm, "SyncPulser", _FakePulser), \
         patch.object(audio_align, "_probe_audio", _sf_probe), \
         patch.object(mm, "soundcard") as soundcard:
        soundcard.get_microphone.return_value.recorder.side_effect = (
            lambda **_kw: recorder_cls(_FakePulser.all_edges))
        assert m.sync_selftest(pulses=3, interval_s=1.0, pulse_ms=20,
                               lead_s=2.0)["result"] == "started"
        deadline = time.time() + 40
        while time.time() < deadline:
            sent = [c[0][0] for c in m.facade.send_status.call_args_list]
            done = [s for s in sent if s["type"] == "sync_selftest_result"]
            if done:
                return done[0], sent
            time.sleep(0.05)
    raise AssertionError("no sync_selftest_result")


def test_sync_selftest_calibrates_the_latency_anchor(tmp_path):
    """With LAT lines the self-test measures the AudioMoth's anchor offset
    and stores it; the next recording carries it in its sidecar and the
    same buzz then comes back at ~0 ms."""
    m = _sync_module(tmp_path)
    cfg = {k: m.config.get(k) for k in (
        "recording.mic_throughput_log_secs", "audiomoth.sample_rate",
        "microphone.frame_num", "microphone.block_size",
        "sync_pulse.buzzer_pin", "sync_pulse.led_pin")}
    m.config.get.side_effect = lambda key, default=None: cfg.get(key, default)
    m.config.set.side_effect = lambda key, value, persist=True: cfg.__setitem__(key, value)

    status, sent = _run_selftest(m, _LatencyClockedRecorder)
    r = status["microphones"]["audiomoth_AAAA"]
    assert r["anchor"] == "latency" and not r["latency_offset_calibrated"], r
    # truth 30 ms buzz delay; the anchor sits BIAS early -> offset 30 - BIAS
    want = SYNC_LATENCY_S * 1e3 - _LatencyClockedRecorder.BIAS_S * 1e3
    assert abs(cfg["audiomoth_sync_offsets_ms"]["AAAA"] - want) < 1.5, cfg
    assert status["calibrated"] == {"audiomoth_AAAA": r["measured_offset_ms"]}
    assert any(s["type"] == "module_config_updated" for s in sent)

    status, _ = _run_selftest(m, _LatencyClockedRecorder)
    r = status["microphones"]["audiomoth_AAAA"]
    assert r["latency_offset_calibrated"] and abs(r["median_ms"]) < 1.5, r


def test_sync_selftest_without_lat_lines_does_not_calibrate(tmp_path):
    m = _sync_module(tmp_path)
    status, sent = _run_selftest(m, _ClockedRecorder)   # no .latency
    r = status["microphones"]["audiomoth_AAAA"]
    assert r["anchor"] == "started" and r["measured_offset_ms"] is None
    assert "LAT" in r["calibration_skipped"] and status["calibrated"] == {}
    m.config.set.assert_not_called()
    assert not any(s["type"] == "module_config_updated" for s in sent)


def test_calibration_refuses_a_poor_self_test():
    from types import SimpleNamespace
    fit = SimpleNamespace(anchor="latency", latency_offset_ms=-4.8)
    good = [SimpleNamespace(offset_ms=10.0 + 0.1 * i, snr_db=33.0) for i in range(8)]
    value, why = mm.AudiomothModule._calibration(fit, good)
    assert why is None and abs(value - (-4.8 + 10.35)) < 1e-6
    assert "detected" in mm.AudiomothModule._calibration(fit, good[:2] + [None] * 6)[1]
    quiet = [SimpleNamespace(offset_ms=o.offset_ms, snr_db=18.0) for o in good]
    assert "quiet" in mm.AudiomothModule._calibration(fit, quiet)[1]
    spread = [SimpleNamespace(offset_ms=10.0 + 5 * i, snr_db=33.0) for i in range(8)]
    assert "spread" in mm.AudiomothModule._calibration(fit, spread)[1]


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


def test_sync_pulse_config_change_releases_gpio(tmp_path):
    m = _sync_module(tmp_path)
    pulser = MagicMock()
    m._sync_pulser = pulser
    m.configure_module_special({"sync_pulse.buzzer_pin"})
    pulser.close.assert_called_once()
    assert m._sync_pulser is None


def test_levels_snapshot_holds_peak_and_skips_stale(tmp_path):
    """audio_levels for the habitat dashboard: the loudest block since the
    last send (peak_acc), reset after each read; a silent thread is dropped."""
    m, _ = _make(tmp_path)
    m.monitor_data_lock = threading.Lock()
    now = time.time()
    m.monitor_data = {
        "AAAA": {"level_db": -50.0, "peak_db": -45.0, "peak_acc": -12.0,
                 "clip_pct": 0.0, "clipping": False, "timestamp": now},
        "BBBB": {"level_db": -50.0, "peak_db": -45.0, "peak_acc": -20.0,
                 "clip_pct": 0.0, "clipping": False, "timestamp": now - 10},
    }
    levels = m._levels_snapshot()
    assert [lv["serial"] for lv in levels] == ["AAAA"]
    assert levels[0]["label"] == "audiomoth_AAAA"
    assert levels[0]["peak_db"] == -12.0
    assert m.monitor_data["AAAA"]["peak_acc"] == -200.0


# ---------------------------------------------------------------------------
# Stream latency probe (LAT sidecar lines)
# ---------------------------------------------------------------------------

class _LatencyRecorder:
    def __init__(self, latency=0.104, held=0, channels=1, fail=False):
        self._pending_chunk = np.zeros(held * channels, dtype="float32")
        self.channels, self._lat, self._fail = channels, latency, fail

    @property
    def latency(self):
        if self._fail:
            raise RuntimeError("no timing info")
        return self._lat


def test_latency_line_counts_samples_soundcard_still_holds():
    line = mm.stream_latency_line(_LatencyRecorder(latency=0.1042, held=1000), 32768)
    tag, index, t0, t1, lat_us = line.split()
    assert tag == "LAT" and int(index) == 33768 and int(lat_us) == 104200
    assert float(t0) <= float(t1)


def test_latency_line_none_when_backend_fails():
    assert mm.stream_latency_line(_LatencyRecorder(fail=True), 32768) is None


def test_sync_commands_follow_the_recording_components_flag(tmp_path):
    """A real microphone module never sets its own is_recording; the
    Recording component's flag is what says a session is running (the
    2026-11-06 desk run: sync_pulses refused mid-session, nothing logged)."""
    m = _sync_module(tmp_path)
    m.is_recording = False
    m.recording = MagicMock(is_recording=True)
    assert m.sync_selftest()["result"] == "error"           # mid-session: refused
    m.facade.get_filename_prefix.return_value = str(tmp_path / "sess")
    m.facade.get_utc_time.return_value = "20261106-130348"
    m.facade.get_segment_id.return_value = 0
    with patch.object(mm, "SyncPulser", _FakePulser):
        assert m.sync_pulses(count=1, interval_s=1.0, pulse_ms=5)["result"] == "started"
        deadline = time.time() + 10
        while m._sync_lock.locked() and time.time() < deadline:
            time.sleep(0.02)
    m.recording = MagicMock(is_recording=False)
    assert m.sync_pulses()["result"] == "error"
