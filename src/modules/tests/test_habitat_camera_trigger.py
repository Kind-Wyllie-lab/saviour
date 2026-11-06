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


def test_open_clip_enables_csv_rows_and_close_stops_them():
    """CameraBase writes per-frame CSV rows only while _encoder_active; a
    habitat clip must switch it (every clip CSV was header-only before)."""
    m = HabitatCameraModule.__new__(HabitatCameraModule)
    m.logger = MagicMock()
    m.facade = MagicMock()
    m.facade.get_filename_prefix.return_value = "/tmp/sess"
    m.facade.get_utc_time.return_value = "20261106-132015"
    m.config = MagicMock()
    m.config.get.side_effect = lambda key, default=None: default
    m._circular_output = MagicMock()
    m._clip_open, m._clip_counter, m._encoder_active = False, 0, False
    m._open_timestamp_csv = MagicMock()
    m._finish_clip = MagicMock()
    HabitatCameraModule._open_clip(m)
    assert m._encoder_active and m._clip_open
    HabitatCameraModule._close_clip(m)
    assert not m._encoder_active and not m._clip_open


def test_disarm_finishes_the_open_clip_before_returning():
    """So the clip is staged before the stop's export, not after it."""
    m = HabitatCameraModule.__new__(HabitatCameraModule)
    m.logger = MagicMock()
    m._circular_output = MagicMock()
    m._clip_open, m._encoder_active = True, True
    m._clip_h264_path, m.current_video_segment = "/tmp/c.h264", "/tmp/c.ts"
    m._finish_clip = MagicMock()
    m._stop_main_encoder = MagicMock()
    m._close_diagnostic_csv = MagicMock()
    assert HabitatCameraModule._stop_recording(m)
    m._finish_clip.assert_called_once_with("/tmp/c.h264", "/tmp/c.ts")


def _csv(path, rows):
    path.write_text("timestamp_ns,frame\n" + "".join(f"{i},{i}\n" for i in range(rows)))


def test_finish_clip_drops_csv_rows_past_the_last_frame(tmp_path):
    """Frames still in the encoder at stop never reach the .ts, but their
    rows were written; the clip CSV must end at the clip's last frame."""
    from unittest.mock import patch

    from src.modules.variants.habitat_camera import habitat_camera_module as hcm
    m = HabitatCameraModule.__new__(HabitatCameraModule)
    m.logger, m.facade = MagicMock(), MagicMock()
    csv_path = tmp_path / "clip_timestamps.csv"
    _csv(csv_path, 10)
    m._close_timestamp_csv = MagicMock(return_value=str(csv_path))
    m._remux_clip_to_ts = MagicMock(return_value=str(tmp_path / "clip.ts"))
    with patch.object(hcm, "_count_video_frames", return_value=7):
        HabitatCameraModule._finish_clip(m, "clip.h264", str(tmp_path / "clip.ts"))
    m._close_timestamp_csv.assert_called_once_with(stage=False)
    lines = csv_path.read_text().splitlines()
    assert len(lines) == 8 and lines[-1] == "6,6"
    staged = [c.args[0] for c in m.facade.stage_file_for_export.call_args_list]
    assert staged == [str(csv_path), str(tmp_path / "clip.ts")]


def test_csv_trim_leaves_odd_mismatches_alone(tmp_path):
    from unittest.mock import patch

    from src.modules.variants.habitat_camera import habitat_camera_module as hcm
    m = HabitatCameraModule.__new__(HabitatCameraModule)
    m.logger = MagicMock()
    for rows, frames in ((100, 10), (10, 0), (5, 9), (8, 8)):
        csv_path = tmp_path / f"c{rows}_{frames}.csv"
        _csv(csv_path, rows)
        with patch.object(hcm, "_count_video_frames", return_value=frames):
            assert m._trim_trailing_csv_rows(str(csv_path), "v.ts") == 0
        assert len(csv_path.read_text().splitlines()) == rows + 1
