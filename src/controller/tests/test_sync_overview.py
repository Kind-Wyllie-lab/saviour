from types import SimpleNamespace
from unittest.mock import MagicMock

from src.controller import sync_overview as so
from src.controller.facade import ControllerFacade
from src.controller.models import Module
from src.controller.modules import ConfigSyncStatus, ModuleConfigState, Modules


def _sample(t, p4l, phc, p4l_rng=None, phc_rng=None):
    s = {"timestamp": t, "ptp4l_offset_ns": p4l, "phc2sys_offset_ns": phc}
    if p4l_rng:
        s["ptp4l_offset_ns_min"], s["ptp4l_offset_ns_max"] = p4l_rng
    if phc_rng:
        s["phc2sys_offset_ns_min"], s["phc2sys_offset_ns_max"] = phc_rng
    return s


class TestDownsamplePtp:
    def test_drops_samples_outside_window(self):
        hist = [_sample(10, 1, 1), _sample(150, 2, 2), _sample(400, 3, 3)]
        rows = so.downsample_ptp(hist, since=100, until=300)
        assert [r[1] for r in rows] == [2]

    def test_bucket_keeps_worst_and_full_envelope(self):
        hist = [
            _sample(100, 50, -10, p4l_rng=(-20, 60)),
            _sample(101, -900, 5, p4l_rng=(-950, 40)),
            _sample(102, 30, 7),  # no range: instantaneous value joins the band
        ]
        rows = so.downsample_ptp(hist, since=100, until=200, max_points=1)
        assert len(rows) == 1
        t, p4l, p4l_lo, p4l_hi, phc, phc_lo, phc_hi = rows[0]
        assert t == 102
        assert p4l == -900  # largest magnitude, sign kept
        assert (p4l_lo, p4l_hi) == (-950, 60)
        assert phc == -10
        assert (phc_lo, phc_hi) == (-10, 7)

    def test_caps_point_count(self):
        hist = [_sample(t, t, t) for t in range(0, 1000)]
        rows = so.downsample_ptp(hist, since=0, until=1000, max_points=50)
        assert len(rows) <= 50

    def test_all_none_offsets_give_none_not_crash(self):
        rows = so.downsample_ptp([_sample(5, None, None)], since=0, until=10)
        assert rows[0][1:] == [None] * 6


class TestFramesyncView:
    def _cam(self, **kw):
        base = {"online": True, "recording": False, "config_status": "SYNCED",
                "camera": {"framesync_enabled": True, "sync_mode": "client",
                           "fps": 30, "sensor_mode_index": 1},
                "target_camera": {}}
        base.update(kw)
        return base

    def test_expected_roles(self):
        view = so.framesync_view({
            "cam_a": self._cam(camera={"framesync_enabled": True,
                                       "sync_mode": "server"}),
            "cam_b": self._cam(),
            "cam_c": self._cam(camera={"framesync_enabled": False,
                                       "sync_mode": "none"}),
            "cam_d": self._cam(online=False),
        }, transmitter="cam_a", waiting=[])
        roles = {m: c["expected_role"] for m, c in view["cameras"].items()}
        assert roles == {"cam_a": "server", "cam_b": "client",
                         "cam_c": "none", "cam_d": None}
        assert view["transmitter"] == "cam_a"

    def test_deferred_only_when_mismatched_and_recording(self):
        view = so.framesync_view({
            "cam_a": self._cam(recording=True),  # client, should be server
            "cam_b": self._cam(recording=True),  # client, matches
        }, transmitter="cam_a", waiting=[])
        assert view["cameras"]["cam_a"]["deferred"] is True
        assert view["cameras"]["cam_b"]["deferred"] is False

    def test_pending_and_waiting(self):
        view = so.framesync_view({
            "cam_b": self._cam(config_status="PENDING", camera={
                "framesync_enabled": True, "sync_mode": "none"},
                target_camera={"sync_mode": "client"}),
        }, transmitter="cam_a", waiting=["cam_b"])
        cam = view["cameras"]["cam_b"]
        assert cam["pending_sync_mode"] == "client"
        assert cam["waiting_for_server"] is True


class TestRecentVerdicts:
    def test_newest_first_by_generated_at_and_skips_unchecked(self):
        sessions = {
            "old": SimpleNamespace(framesync_verdict={
                "status": "green", "generated_at": "2026-11-01T00:00:00Z"}),
            "none": SimpleNamespace(framesync_verdict=None),
            "new": SimpleNamespace(framesync_verdict={
                "status": "amber", "generated_at": "2026-11-09T00:00:00Z"}),
        }
        out = so.recent_verdicts(sessions)
        assert [v["session_name"] for v in out] == ["new", "old"]


def test_clamp_window():
    assert so.clamp_window("bogus") == so.DEFAULT_WINDOW_S
    assert so.clamp_window(10) == 300
    assert so.clamp_window(10**9) == so.MAX_WINDOW_S


def test_build_overview_shape():
    out = so.build_overview(
        module_history={"m1": [_sample(950, 100, 200)]},
        module_health={"m1": {"ptp4l_offset_ns": 100, "last_heartbeat": 950}},
        controller_ptp={"role": "master", "status": "synchronized",
                        "phc2sys_offset_ns": 3, "extra": "dropped"},
        controller_buffer=[{"timestamp": 990, "phc2sys_offset_ns": 3}],
        cameras={}, transmitter=None, waiting=[], sessions={},
        thresholds={"ptp_start_gate_us": 50.0, "ptp_threshold_us": 1000.0},
        window_s=600, now=1000,
    )
    assert out["modules"]["m1"]["history"][0][1] == 100
    assert out["controller"]["history"] == [[990, 3, 3, 3]]
    assert "extra" not in out["controller"]
    assert out["ptp_row_fields"][0] == "t"


def test_facade_get_sync_overview_survives_ptp_failure():
    modules = Modules()
    modules.facade = None
    controller = MagicMock()
    controller.modules = modules
    controller.ptp.get_status.side_effect = RuntimeError("systemctl gone")
    controller.health.module_health_history = {}
    controller.health.get_module_health.return_value = {}
    controller.recording.get_recording_sessions.return_value = {}
    controller.config.get.return_value = {"ptp_start_gate_us": 25}
    facade = ControllerFacade(controller)
    modules.add_module(Module(id="cam_a", name="cam_a", type="camera",
                              version="1", ip="10.0.0.2"))
    modules._config_states["cam_a"] = ModuleConfigState(
        true_config={"camera": {"framesync_enabled": True, "sync_mode": "server"}},
        status=ConfigSyncStatus.SYNCED)
    facade._framesync_transmitter = "cam_a"

    out = facade.get_sync_overview()
    assert out["controller"] is None
    assert out["thresholds"]["ptp_start_gate_us"] == 25.0
    assert out["framesync"]["cameras"]["cam_a"]["expected_role"] == "server"
