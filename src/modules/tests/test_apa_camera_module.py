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
