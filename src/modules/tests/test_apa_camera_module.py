"""
Tests for src/modules/variants/apa_camera/apa_camera_module.py.

Built with __new__ (as in test_camera_base.py) plus just the attributes each
helper touches: the inference-fault helpers, shock-zone geometry, which
overlays go to the recording vs the livestream, and the HEF list/fetch
commands. Real capture and Hailo inference need a Picamera2 and are out of
scope.
"""

from src.modules.variants.apa_camera.apa_camera_module import APACameraModule


def _make_apa(**attrs) -> APACameraModule:
    m = APACameraModule.__new__(APACameraModule)
    m.picam2 = object()          # a sensor is present unless a test says otherwise
    m._inference_fault = None
    m.hardware_fault = None
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestInferenceFault:
    def test_sync_surfaces_inference_fault_as_hardware_fault(self):
        m = _make_apa(_inference_fault="Hailo AI HAT unavailable: boom")
        m._sync_hardware_fault()
        assert m.hardware_fault == "Hailo AI HAT unavailable: boom"

    def test_sync_clears_hardware_fault_when_inference_ok(self):
        m = _make_apa(_inference_fault=None, hardware_fault="stale")
        m._sync_hardware_fault()
        assert m.hardware_fault is None

    def test_sync_never_masks_a_camera_sensor_fault(self):
        # CameraBase owns hardware_fault while picam2 is None.
        m = _make_apa(picam2=None, hardware_fault="No camera sensor detected: x")
        m._inference_fault = None
        m._sync_hardware_fault()
        assert m.hardware_fault == "No camera sensor detected: x"

    def test_check_object_detector_passes_without_a_fault(self):
        # blob (CPU) backend or detection-disabled leave _inference_fault None.
        ok, _ = _make_apa(_inference_fault=None)._check_object_detector()
        assert ok is True

    def test_check_object_detector_blocks_on_a_failed_hailo_backend(self):
        ok, msg = _make_apa(_inference_fault="no HAT")._check_object_detector()
        assert ok is False and msg == "no HAT"


# --------------------------------------------------------------------------- #
# Shock-zone geometry
# --------------------------------------------------------------------------- #

def _zone_apa(start_deg, span_deg, inner=0.3):
    # 1000x1000 main stream, mask radius 1.0 -> outer_r 500, inner_r 150.
    # _configure_mask_and_shock_zone stores the config start angle - 90.
    return _make_apa(width=1000, height=1000, mask_radius=1.0,
                     mask_center_x_offset=0, mask_center_y_offset=0,
                     shock_zone_start_angle=start_deg - 90,
                     shock_zone_angle_span=span_deg,
                     shock_zone_inner_offset=inner)


def _point(deg, r=300, c=500):
    import math
    a = math.radians(deg)
    return int(c + r * math.cos(a)), int(c + r * math.sin(a))


class TestShockZone:
    def test_zone_crossing_zero_degrees(self):
        # Config 90 deg -> drawn centre 0 deg (image +x), span 60: -30..30.
        m = _zone_apa(90, 60)
        assert m._is_in_shock_zone(*_point(10))
        assert m._is_in_shock_zone(*_point(-20))
        assert not m._is_in_shock_zone(*_point(45))

    def test_ordinary_zone(self):
        m = _zone_apa(180, 60)   # centre 90 deg, 60..120
        assert m._is_in_shock_zone(*_point(100))
        assert not m._is_in_shock_zone(*_point(30))

    def test_radial_bounds(self):
        m = _zone_apa(180, 60)
        assert not m._is_in_shock_zone(*_point(90, r=100))   # inside inner_r
        assert not m._is_in_shock_zone(*_point(90, r=520))   # outside the arena


# --------------------------------------------------------------------------- #
# Overlays: livestream vs recording
# --------------------------------------------------------------------------- #

class _Cfg:
    def __init__(self, d):
        self.d = d

    def get(self, key, default=None):
        return self.d.get(key, default)


class _Arr:
    def __init__(self, arr):
        self.array = arr


def _overlay_apa(**flags):
    from collections import deque
    m = _make_apa(width=100, height=100, mask_radius=0.5,
                  mask_center_x_offset=0, mask_center_y_offset=0,
                  _mask_cache=None, detector=None, _last_known_det=None,
                  _detection_buffer=deque(maxlen=3), _prev_in_zone=False,
                  config=_Cfg({"object_detection.enabled": False}),
                  mask_display=True, mask_record=True,
                  shock_zone_display=True, shock_zone_record=False,
                  track_display=True, track_record=True)
    for k, v in flags.items():
        setattr(m, k, v)
    return m


class TestOverlayRouting:
    def _white(self):
        import numpy as np
        return np.full((100, 100, 3), 255, dtype=np.uint8)

    def test_mask_record_off_leaves_recording_unmasked(self):
        m = _overlay_apa(mask_record=False)
        frame = _Arr(self._white())
        m._process_main_frame(frame, None)
        assert frame.array[0, 0].tolist() == [255, 255, 255]

    def test_mask_record_on_masks_recording(self):
        m = _overlay_apa(mask_record=True)
        frame = _Arr(self._white())
        m._process_main_frame(frame, None)
        assert frame.array[0, 0].tolist() == [0, 0, 0]
        assert frame.array[50, 50].tolist() == [255, 255, 255]

    def test_shock_zone_drawn_only_where_enabled(self):
        calls = []
        m = _overlay_apa(shock_zone_display=False, shock_zone_record=True)
        m._apply_shock_zone = lambda arr: calls.append("zone")
        m._apply_mask = lambda arr: None
        m._process_lores_frame(_Arr(self._white()), None)
        assert calls == []
        m._process_main_frame(_Arr(self._white()), None)
        assert calls == ["zone"]

    def test_hq_preview_adds_only_display_only_overlays(self):
        calls = []
        m = _overlay_apa(mask_display=True, mask_record=True,
                         shock_zone_display=True, shock_zone_record=False)
        m._apply_mask = lambda arr: calls.append("mask")
        m._apply_shock_zone = lambda arr: calls.append("zone")
        m._process_main_preview_frame(_Arr(self._white()), None)
        assert calls == ["zone"]   # mask is already on the recorded frame


# --------------------------------------------------------------------------- #
# HEF listing / fetching
# --------------------------------------------------------------------------- #

class TestHefs:
    def test_list_hefs_merges_bundled_and_uploaded(self, tmp_path, monkeypatch):
        from src.modules.variants.apa_camera import apa_camera_module as mod
        bundled, uploaded = tmp_path / "b", tmp_path / "u"
        bundled.mkdir()
        uploaded.mkdir()
        (bundled / "a.hef").write_bytes(b"x")
        (bundled / "notes.txt").write_bytes(b"x")
        (uploaded / "b.hef").write_bytes(b"yy")
        monkeypatch.setattr(mod, "MODEL_DIR", str(bundled))
        monkeypatch.setattr(mod, "USER_HEF_DIR", str(uploaded))
        monkeypatch.setattr(mod, "HAILO_DEVICE", str(tmp_path / "nope"))
        m = _make_apa(config=_Cfg({"object_detection.model_path": str(uploaded / "b.hef")}))
        out = m.list_hefs()
        assert out["hailo_present"] is False
        assert [(h["name"], h["source"]) for h in out["hefs"]] == [
            ("a.hef", "bundled"), ("b.hef", "uploaded")]
        assert out["current"] == str(uploaded / "b.hef")

    def test_fetch_hef_rejects_path_tricks(self, tmp_path, monkeypatch):
        from src.modules.variants.apa_camera import apa_camera_module as mod
        monkeypatch.setattr(mod, "MODEL_DIR", str(tmp_path))
        monkeypatch.setattr(mod, "USER_HEF_DIR", str(tmp_path / "u"))
        m = _make_apa(config=_Cfg({}))
        for bad in ("../x.hef", "x.txt", "", ".hidden.hef"):
            assert "error" in m.fetch_hef("http://c:5000", bad)
        assert not (tmp_path / "u").exists()


# --------------------------------------------------------------------------- #
# Tracking: smoothing, jump rejection, lost timeout
# --------------------------------------------------------------------------- #

def _det(cx, cy, conf=0.9):
    from src.modules.hailo_infer import Detection
    return Detection(0, conf, (cx - 10, cy - 10, 20, 20))


def _tracker(at=(100, 100), seen=1.0, **attrs):
    """A 1000x1000 camera (jump gate 150 px at max_jump_frac 0.15) already
    tracking at `at`, last seen at `seen`; at=None for no track yet."""
    base = dict(width=1000, height=1000, _track_pos=at,
                _track_det=_det(*at) if at else None, _track_seen_t=seen,
                _jump_cand=None, smoothing=0.5, max_jump_frac=0.15,
                jump_confirm_frames=3, lost_timeout_s=0.5)
    base.update(attrs)
    return _make_apa(**base)


class TestTracking:
    def test_track_starts_once_confirmed(self):
        m = _tracker(at=None)
        m._update_track([_det(100, 100)], now=1.0)
        m._update_track([_det(102, 100)], now=1.03)
        assert m._track_pos is None             # 2 of 3 frames
        m._update_track([_det(104, 100)], now=1.06)
        assert m._track_pos == (104, 100)

    def test_smooths_towards_nearby_detection(self):
        m = _tracker()
        m._update_track([_det(140, 100)], now=1.03)
        assert m._track_pos == (120, 100)

    def test_single_frame_jump_is_ignored(self):
        m = _tracker()
        m._update_track([_det(900, 900)], now=1.03)   # false positive, one frame
        m._update_track([_det(105, 100)], now=1.06)
        assert m._track_pos == (102.5, 100)
        assert m._jump_cand is None

    def test_jump_followed_once_confirmed(self):
        m = _tracker()
        for i in range(3):
            m._update_track([_det(900, 900)], now=1.03 + i * 0.03)
        assert m._track_pos == (900, 900)

    def test_prefers_nearest_candidate_over_most_confident(self):
        m = _tracker(smoothing=0.0)
        m._update_track([_det(900, 900, conf=0.95), _det(110, 100, conf=0.6)], now=1.03)
        assert m._track_pos == (110, 100)

    def test_track_cleared_after_lost_timeout(self):
        m = _tracker()
        m._update_track([], now=1.4)
        assert m._track_pos == (100, 100)       # within the timeout: held
        m._update_track([], now=1.6)
        assert m._track_pos is None             # gone, not frozen

    def test_rejected_jumps_dont_keep_the_track_alive(self):
        m = _tracker(jump_confirm_frames=100)
        m._update_track([_det(900, 900)], now=1.3)
        m._update_track([_det(900, 900)], now=1.6)
        assert m._track_pos is None

    def test_one_frame_false_positive_after_loss_starts_nothing(self):
        m = _tracker()
        m._update_track([_det(900, 900)], now=1.6)   # lost, then a blip
        m._update_track([], now=1.63)
        assert m._track_pos is None


class TestFrameConfidence:
    def test_conf_only_on_frames_with_an_accepted_detection(self):
        m = _tracker()
        m._update_track([_det(110, 100, conf=0.81)], now=1.03)
        assert m._frame_conf == 0.81
        m._update_track([], now=1.06)            # position held, nothing new
        assert m._track_pos is not None and m._frame_conf is None
        m._update_track([_det(900, 900, conf=0.9)], now=1.09)   # rejected jump
        assert m._frame_conf is None


class TestZoneState:
    """zone_state at ~5 Hz while tracking: the controller's auto shocking
    holds a shock only while these keep arriving. Sent from the inference
    worker's _track_frame, stamped with the analysed frame's timestamp."""

    def _apa(self):
        from unittest.mock import MagicMock

        import numpy as np
        m = _worker_apa(at=(500, 500), communication=MagicMock())
        m._detect_objects = lambda frame: [_det(500, 500, conf=0.8)]
        m._is_in_shock_zone = lambda cx, cy: True
        return m, np.zeros((10, 10, 3), dtype=np.uint8)

    def _sent(self, m):
        return [c.args[0] for c in m.communication.send_status.call_args_list]

    def test_reports_throttled_to_period(self):
        from unittest.mock import patch
        m, frame = self._apa()
        with patch("src.modules.variants.apa_camera.apa_camera_module.time.monotonic") as mono:
            for t in (1.0, 1.03, 1.06, 1.1, 1.21):
                mono.return_value = t
                m._track_frame(frame, 123, 0)
        states = [s for s in self._sent(m) if s["type"] == "zone_state"]
        assert len(states) == 2                    # at 1.0 and 1.21
        assert states[0]["in_zone"] is True and states[0]["tracking"] is True
        assert states[0]["det_conf"] == 0.8
        assert states[0]["timestamp_ns"] == 123
        assert "infer_hz" in states[0]
        assert self._sent(m)[0]["type"] == "zone_entered"   # the edge goes first

    def test_edge_carries_the_analysed_frames_timestamp(self):
        m, frame = self._apa()
        m._track_frame(frame, 987654321, 42)
        assert self._sent(m)[0] == {"type": "zone_entered", "timestamp_ns": 987654321}

    def test_report_is_json_serialisable_in_zone(self):
        """The real zone test returns numpy types; json.dumps in send_status
        failed on every in-zone report (2026-10-23), so the controller saw
        the zone_entered edge and then nothing."""
        import json
        from unittest.mock import patch

        import numpy as np
        m, frame = self._apa()
        del m._is_in_shock_zone          # use the real geometry
        m.mask_radius = 0.5
        m.mask_center_x_offset = m.mask_center_y_offset = 0
        m.shock_zone_inner_offset = 0.0
        m.shock_zone_start_angle = -90
        m.shock_zone_angle_span = 360
        m._track_det = _det(500, 400, conf=np.float32(0.8))
        m._detect_objects = lambda frame: [_det(500, 400, conf=np.float32(0.8))]
        m._track_pos = (500, 400)
        m._track_seen_t = 5.0
        with patch("src.modules.variants.apa_camera.apa_camera_module.time.monotonic",
                   return_value=5.0):
            m._track_frame(frame, 1, 0)
        states = [s for s in self._sent(m) if s["type"] == "zone_state"]
        assert states and states[0]["in_zone"] is True
        json.dumps(states[0])
        json.dumps(m._track_snapshot.conf)


def _worker_apa(at=(500, 500), **attrs):
    """An APA module with the inference-worker state, tracking on, its track
    last seen just now (the worker uses the real monotonic clock)."""
    import queue
    import threading
    import time
    from collections import deque
    from unittest.mock import MagicMock
    m = _tracker(at=at, seen=time.monotonic(), _prev_in_zone=False, _last_zone_state_t=0.0,
                 mask_record=True, shock_zone_record=False, track_record=False,
                 mask_radius=None, _frame_conf=None, _frame_id=0,
                 config=_Cfg({"object_detection.enabled": True}),
                 communication=MagicMock(), logger=MagicMock(),
                 _det_lock=threading.Lock(), _infer_q=queue.Queue(maxsize=1),
                 _infer_stop=threading.Event(), _infer_worker=None,
                 _infer_every_n=1, _infer_counter=0, _track_snapshot=None,
                 _snapshot_seq=0, _csv_snapshot_seq=-1,
                 _infer_latency_s=deque(maxlen=250), _infer_done_t=deque(maxlen=250),
                 _infer_handed=0, _infer_stats_t=0.0)
    m._apply_mask = lambda arr: None
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class TestCaptureHandOff:
    """The capture callback never runs inference: it hands the newest frame
    to the worker and fills the CSV from the last published snapshot
    (2026-11-10: inline Hailo detection cost ~13% of frames at 25 fps)."""

    def _frame(self):
        import numpy as np
        return _Arr(np.zeros((10, 10, 3), dtype=np.uint8))

    def test_capture_never_calls_the_detector(self):
        from unittest.mock import MagicMock
        m = _worker_apa()
        m._detect_objects = MagicMock(side_effect=AssertionError("inline detect"))
        m._process_main_frame(self._frame(), MagicMock(timestamp_ns=5))
        assert m._infer_q.qsize() == 1

    def test_queue_keeps_only_the_newest_frame(self):
        from unittest.mock import MagicMock
        m = _worker_apa()
        for i in range(3):
            m._frame_id = i
            m._process_main_frame(self._frame(), MagicMock(timestamp_ns=100 + i))
        _, ts, fid = m._infer_q.get_nowait()
        assert (ts, fid) == (102, 2)
        assert m._infer_q.empty()

    def test_handed_frame_is_a_copy(self):
        from unittest.mock import MagicMock
        m = _worker_apa()
        f = self._frame()
        m._process_main_frame(f, MagicMock(timestamp_ns=1))
        work, _, _ = m._infer_q.get_nowait()
        f.array[:] = 255                 # later overlays / reuse of the buffer
        assert work.max() == 0

    def test_infer_every_n(self):
        from unittest.mock import MagicMock
        m = _worker_apa(_infer_every_n=3)
        handed = 0
        for _ in range(6):
            m._process_main_frame(self._frame(), MagicMock(timestamp_ns=1))
            if not m._infer_q.empty():
                m._infer_q.get_nowait()
                handed += 1
        assert handed == 2

    def test_csv_blank_until_first_result(self):
        from unittest.mock import MagicMock
        m = _worker_apa()
        row = m._process_main_frame(self._frame(), MagicMock(timestamp_ns=1))
        assert row == {"det_cx": "", "det_cy": "", "in_zone": "", "det_conf": "",
                       "det_frame_id": ""}

    def test_csv_repeats_position_but_conf_only_once_per_result(self):
        from unittest.mock import MagicMock

        from src.modules.variants.apa_camera.apa_camera_module import TrackSnapshot
        m = _worker_apa()
        m._track_snapshot = TrackSnapshot(seq=1, det_cx=10, det_cy=20, in_zone=True,
                                          conf=0.8123, det=_det(10, 20),
                                          frame_id=7)
        timing = MagicMock(timestamp_ns=1)
        first = m._process_main_frame(self._frame(), timing)
        second = m._process_main_frame(self._frame(), timing)
        assert first == {"det_cx": 10, "det_cy": 20, "in_zone": 1,
                         "det_conf": 0.812, "det_frame_id": 7}
        assert second == dict(first, det_conf="")

    def test_worker_publishes_snapshot(self):
        import numpy as np
        m = _worker_apa(at=(100, 100))
        m._detect_objects = lambda frame: [_det(104, 100, conf=0.7)]
        m._is_in_shock_zone = lambda cx, cy: False
        m._track_frame(np.zeros((10, 10, 3), dtype=np.uint8), 55, 9)
        snap = m._track_snapshot
        assert snap.seq == 1 and snap.frame_id == 9
        assert (snap.det_cx, snap.det_cy) == (102, 100)    # smoothing 0.5
        assert snap.conf == 0.7 and snap.in_zone is False

    def test_worker_thread_tracks_handed_frames(self):
        import time
        from unittest.mock import MagicMock
        m = _worker_apa(at=(100, 100))
        m._detect_objects = lambda frame: [_det(100, 100, conf=0.9)]
        m._is_in_shock_zone = lambda cx, cy: False
        m._start_inference_worker()
        try:
            m._frame_id = 3
            m._process_main_frame(self._frame(), MagicMock(timestamp_ns=1))
            deadline = time.monotonic() + 2
            while m._track_snapshot is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert m._track_snapshot is not None
            assert m._track_snapshot.frame_id == 3
        finally:
            assert m._stop_inference_worker() is True

    def test_stop_joins_worker_before_closing_detector(self):
        from unittest.mock import MagicMock, patch

        CameraBase = APACameraModule.__mro__[1]   # the module imports it as modules.camera_base
        order = []
        m = _worker_apa()
        m._stop_inference_worker = lambda: order.append("join") or True
        m.detector = MagicMock()
        m.detector.close.side_effect = lambda: order.append("close")
        with patch.object(CameraBase, "stop", return_value=True):
            assert m.stop() is True
        assert order == ["join", "close"]

    def test_stop_leaves_detector_open_if_worker_is_stuck(self):
        from unittest.mock import MagicMock, patch

        CameraBase = APACameraModule.__mro__[1]   # the module imports it as modules.camera_base
        m = _worker_apa()
        m._stop_inference_worker = lambda: False
        det = m.detector = MagicMock()
        with patch.object(CameraBase, "stop", return_value=True):
            m.stop()
        det.close.assert_not_called()
