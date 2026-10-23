"""ShockAutomation (variants/apa/shock_automation.py): shock mode gate and the
controller's automated shocking decision, driven with a fake clock."""

import csv
import os

import pytest

from src.controller.variants.apa.shock_automation import ShockAutomation

ARDUINO = "apa_arduino_1"
CAMERA = "apa_camera_1"


class Rig:
    def __init__(self, tmp_path, settings=None):
        self.t = 100.0
        self.sent: list[tuple[str, str]] = []
        self.sessions = ["s1"]
        self.states: list[dict] = []
        self.share = str(tmp_path)
        self.auto = ShockAutomation(
            send_command=lambda mid, cmd, params: self.sent.append((mid, cmd)),
            get_modules=lambda: {
                ARDUINO: {"type": "apa_arduino", "online": True},
                CAMERA: {"type": "apa_camera", "online": True},
            },
            get_active_sessions=lambda: self.sessions,
            get_settings=lambda: settings or {},
            get_share_path=lambda: self.share,
            emit=self.states.append,
            clock=lambda: self.t,
        )

    def advance(self, s: float, *, in_zone: bool | None = None, step: float = 0.05):
        """Run ticks for s seconds, with the camera reporting every 0.2 s if
        in_zone is given."""
        end = self.t + s
        next_report = self.t
        while self.t < end - 1e-9:
            if in_zone is not None and self.t >= next_report - 1e-9:
                self.auto.on_zone_status(CAMERA, {
                    "type": "zone_state", "in_zone": in_zone,
                    "det_cx": 10, "det_cy": 20, "det_conf": 0.9})
                next_report += 0.2
            self.auto.tick()
            self.t += step

    def edge(self, entered: bool):
        self.auto.on_zone_status(CAMERA, {"type": "zone_entered" if entered else "zone_exited"})

    def cmds(self):
        return [c for _, c in self.sent]

    def log(self):
        path = os.path.join(self.share, "s1", "s1_shock_decisions.csv")
        with open(path) as f:
            return [r["event"] for r in csv.DictReader(f)]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


class TestModeGate:
    def test_starts_disarmed_and_refuses_manual(self, rig):
        assert rig.auto.mode == "disarmed"
        assert rig.auto.manual_allowed() is False

    @pytest.mark.parametrize("mode", ["manual", "auto", "dry_run"])
    def test_manual_allowed_when_armed(self, rig, mode):
        rig.auto.set_mode(mode)
        assert rig.auto.manual_allowed() is True

    def test_unknown_mode_rejected(self, rig):
        assert rig.auto.set_mode("on") is False
        assert rig.auto.mode == "disarmed"

    def test_disarming_stops_any_shock(self, rig):
        rig.auto.set_mode("manual")
        rig.sent.clear()
        rig.auto.set_mode("disarmed")
        assert rig.cmds() == ["deactivate_shock"]

    def test_stop_all_disarms_and_stops(self, rig):
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(1.0, in_zone=True)
        rig.sent.clear()
        rig.auto.stop_all()
        assert rig.auto.mode == "disarmed"
        assert rig.cmds() == ["deactivate_shock"]
        assert rig.log()[-1] == "stop_all"

    def test_refused_manual_logged_once_per_hold(self, rig):
        for _ in range(4):
            rig.auto.refuse_manual()
            rig.t += 0.25
        assert rig.log().count("manual_refused") == 1


class TestAuto:
    def test_shocks_after_entry_latency_and_holds_until_exit(self, rig):
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(0.4, in_zone=True)
        assert "activate_shock" not in rig.cmds()  # 0.5 s entry latency

        rig.advance(0.5, in_zone=True)
        assert rig.auto.shocking
        rig.sent.clear()
        rig.advance(1.0, in_zone=True)
        # Lease refreshed well inside 1 s.
        assert rig.cmds().count("activate_shock") >= 3

        rig.sent.clear()
        rig.edge(False)
        assert rig.cmds() == ["deactivate_shock"]
        assert not rig.auto.shocking
        assert rig.log()[-2:] == ["shock_start", "shock_stop"]

    def test_brief_entry_never_shocks(self, rig):
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(0.3, in_zone=True)
        rig.edge(False)
        rig.advance(1.0, in_zone=False)
        assert "activate_shock" not in rig.cmds()

    def test_stale_camera_stops_shock(self, rig):
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(1.0, in_zone=True)
        assert rig.auto.shocking
        rig.sent.clear()
        rig.advance(1.0)  # camera goes quiet
        assert not rig.auto.shocking
        assert rig.cmds()[-1] == "deactivate_shock"

    def test_no_session_no_auto_shock(self, rig):
        rig.sessions = []
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(2.0, in_zone=True)
        assert "activate_shock" not in rig.cmds()
        assert rig.auto.state()["session_recording"] is False

    def test_session_end_disarms(self, rig):
        rig.auto.set_mode("auto")
        rig.advance(0.2)
        rig.sessions = []
        rig.advance(0.2)
        assert rig.auto.mode == "disarmed"

    def test_per_minute_cap(self, tmp_path):
        rig = Rig(tmp_path, settings={"max_pulses_per_min": 3})
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(1.0, in_zone=True)
        assert rig.auto.shocking
        for n in (0, 1, 2, 3):
            rig.auto.on_arduino_state({"attempted_shocks": n})
            rig.t += 1
        rig.advance(0.3, in_zone=True)
        assert not rig.auto.shocking
        assert "capped" in rig.log()

    def test_leaving_auto_mid_shock_stops_it(self, rig):
        rig.auto.set_mode("auto")
        rig.edge(True)
        rig.advance(1.0, in_zone=True)
        rig.sent.clear()
        rig.auto.set_mode("manual")
        assert rig.cmds() == ["deactivate_shock"]
        assert not rig.auto.shocking


class TestDryRun:
    def test_logs_decisions_sends_nothing(self, rig):
        rig.auto.set_mode("dry_run")
        rig.edge(True)
        rig.advance(1.0, in_zone=True)
        rig.edge(False)
        assert "activate_shock" not in rig.cmds()
        assert "deactivate_shock" not in rig.cmds()
        assert rig.log()[-2:] == ["would_shock_start", "would_shock_stop"]


def test_state_reports_time_in_zone(rig):
    rig.auto.set_mode("auto")
    rig.edge(True)
    rig.advance(0.3, in_zone=True)
    st = rig.auto.state()
    assert st["in_zone"] is True
    assert 0.2 <= st["in_zone_s"] <= 0.4
    assert st["mode"] == "auto"
