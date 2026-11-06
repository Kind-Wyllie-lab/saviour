"""habitat_camera's motion trigger state machine (_process_main_frame) and the
`habitat_motion.always_record` switch. Built with __new__ plus only the
attributes the trigger touches, as test_apa_camera_module.py does."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

from src.modules.variants.habitat_camera.habitat_camera_module import (
    HabitatCameraModule,
)


def _module(always_record=False, recording=True):
    m = HabitatCameraModule.__new__(HabitatCameraModule)
    m.logger = MagicMock()
    m.facade = MagicMock()
    m.facade.get_recording_status.return_value = recording
    m.config = MagicMock()
    m.config.get.side_effect = lambda key, default=None: {
        "habitat_motion.always_record": always_record,
        "habitat_motion.activity_threshold": 0.5,
        "habitat_motion.activity_min_duration_s": 1.0,
        "habitat_motion.inactivity_min_duration_s": 2.0,
    }.get(key, default)
    m._configure_habitat_motion()
    m._motion_detector = MagicMock()
    m._motion_detector.score.return_value = 0.0          # never any motion
    m._occupancy_detector = None
    m._diag_csv_writer = None
    m._update_ae_stability = lambda timing: True
    m._maybe_cache_occ_frame = lambda arr, timing: None
    m._clip_open = False
    m.opened, m.closed = [], []
    m._open_clip = lambda: (m.opened.append(1), setattr(m, "_clip_open", True))
    m._close_clip = lambda: (m.closed.append(1), setattr(m, "_clip_open", False))
    return m


def _frames(m, seconds, fps=10):
    frame = SimpleNamespace(array=np.zeros((4, 4), dtype=np.uint8))
    for i in range(int(seconds * fps)):
        m._process_main_frame(frame, SimpleNamespace(
            timestamp_ns=int(i * 1e9 / fps), timestamp_utc=""))


def test_without_motion_nothing_records():
    m = _module()
    _frames(m, 5)
    assert m.opened == [] and m._motion_state == "idle"


def test_always_record_opens_on_the_first_frame_and_never_closes():
    m = _module(always_record=True)
    _frames(m, 10)                       # far past inactivity_min_duration_s
    assert m.opened == [1] and m.closed == []
    assert m._motion_state == "active"


def test_always_record_unarmed_stays_active_without_a_clip():
    """The preview shows it triggered; no file until a session arms it."""
    m = _module(always_record=True, recording=False)
    _frames(m, 1)
    assert m._motion_state == "active" and m.opened == []
