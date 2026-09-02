"""
Tests for src/modules/variants/apa_camera/apa_camera_module.py.

Only the inference-accelerator (Hailo AI HAT) fault-state helpers are in
scope here: _sync_hardware_fault and _check_object_detector touch just
self.picam2 / self._inference_fault / self.hardware_fault, so __new__
construction (as in test_camera_base.py) is enough. The full CameraBase
subclass, the per-frame detection pipeline and shock-zone geometry are out
of scope -- they need a real/deeply-faked Picamera2.
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
