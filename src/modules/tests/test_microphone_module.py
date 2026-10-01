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
