"""
Integration test: a session's full lifecycle across the real controller
components (roadmap C3).

Real: Recording, the Modules tracker, ControllerFacade, and the controller's
own status routing (Controller.handle_status_update /
on_module_status_change). Simulated: only the module processes -- each
FakeModule answers start/stop_recording with the status messages a real
module sends -- plus a minimal health store and the web/notifier sinks.

Replies are queued and delivered by Fleet.pump(), like real async ZMQ: a
synchronous reply would re-enter Recording while it still holds its lock.

Covers the behaviour verified by hand in the 2026-09-30 desk soak: start ->
stop with one timed stop; a module dropout that must stay faulted until the
module is really back (no re-arm into the void), with a session_gaps.json
gap starting at the last heartbeat; a crashed-but-online module re-armed by
the liveness check; and the export/delete guard.
"""

import json
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.controller.recording as recording_module
from src.controller.controller import Controller
from src.controller.facade import ControllerFacade
from src.controller.modules import Module, Modules
from src.controller.recording import Recording, SessionState


class FakeHealth:
    """Just what Recording and the status routing read and write."""

    def __init__(self):
        self.h: dict[str, dict] = {}

    def seed(self, module_id):
        self.h[module_id] = {"status": "online", "last_heartbeat": time.time(),
                             "ptp4l_offset_ns": 900, "phc2sys_offset_ns": 400}

    def touch_heartbeat(self, module_id):
        self.h.setdefault(module_id, {})["last_heartbeat"] = time.time()

    def update_module_health(self, module_id, data):
        self.h.setdefault(module_id, {}).update(data)

    def get_module_health(self, module_id=None):
        return self.h if module_id is None else self.h.get(module_id, {})


class FakeModule:
    def __init__(self, fleet, module_id):
        self.fleet, self.id = fleet, module_id
        self.recording = False
        self.online = True
        self.commands: list[str] = []

    def handle(self, command, params):
        if not self.online:
            return  # unplugged: the command goes nowhere
        self.commands.append(command)
        if command == "start_recording":
            if self.recording:
                self.fleet.reply(self.id, {"type": "recording_start_failed",
                                           "error": "Already recording"})
            else:
                self.recording = True
                self.fleet.reply(self.id, {"type": "recording_started",
                                           "status": "success", "recording": True})
        elif command == "stop_recording":
            if self.recording:
                self.recording = False
                self.fleet.reply(self.id, {"type": "recording_stopped",
                                           "status": "success", "recording": False,
                                           "reason": "operator"})
            else:
                self.fleet.reply(self.id, {"type": "recording_stop_failed",
                                           "error": "Not recording"})


class Fleet:
    def __init__(self, tmpdir, module_ids=("cam1", "cam2")):
        recording_module.SESSIONS_FILE = os.path.join(tmpdir, "sessions.json")
        self.share = os.path.join(tmpdir, "share")
        os.makedirs(self.share)
        self.queue: list[tuple[str, dict]] = []
        self.modules = {m: FakeModule(self, m) for m in module_ids}

        ctl = SimpleNamespace()
        ctl.logger = MagicMock()
        ctl.web = MagicMock()
        ctl.notifier = MagicMock()
        ctl.export_queue = MagicMock()
        ctl.health = FakeHealth()
        ctl.config = MagicMock()
        ctl.config.get_all.return_value = {"recording": {"ptp_start_gate_us": 50.0}}
        ctl.config.get.side_effect = lambda key, default=None: (
            self.share if key == "export.mount_path" else default)
        ctl.communication = SimpleNamespace(send_command=self._send)
        ctl.modules = Modules()
        ctl.facade = ControllerFacade(ctl)
        ctl.modules.facade = None
        with patch("src.controller.recording.threading.Thread"):
            ctl.recording = Recording()
        ctl.recording.facade = ctl.facade
        ctl.recording._check_share_writable = lambda: None
        ctl.on_module_status_change = (
            lambda mid, st: Controller.on_module_status_change(ctl, mid, st))
        self.ctl = ctl
        self.rec = ctl.recording

        for m in module_ids:
            ctl.modules.add_module(Module(id=m, name=m, type="camera",
                                          version="1", ip="10.0.0.2"))
            ctl.modules._modules[m].online = True
            ctl.health.seed(m)

    # ----- plumbing -------------------------------------------------------
    def _send(self, module_id, command, params=None):
        targets = self.modules if module_id == "all" else [module_id]
        for m in targets:
            if m in self.modules:
                self.modules[m].handle(command, params or {})

    def reply(self, module_id, status):
        self.queue.append((module_id, status))

    def pump(self):
        while self.queue:
            module_id, status = self.queue.pop(0)
            Controller.handle_status_update(self.ctl, f"status/{module_id}",
                                            json.dumps(status))

    def monitor(self, passes=1):
        """Run the session monitor's per-session checks, as each 5 s pass does."""
        for _ in range(passes):
            for name, s in list(self.rec.sessions.items()):
                if s.state in (SessionState.ACTIVE, SessionState.ERROR):
                    self.rec._check_session_recording_liveness(name, s)
            self.pump()

    # ----- scenario helpers ----------------------------------------------
    def start(self, name="exp", **kw):
        created = self.rec.create_session(name, "all", raw_name=True, **kw)
        assert created.get("success"), created
        name = created["session_name"]
        started = self.rec.force_start_session(name)
        assert started.get("success"), started
        self.pump()
        return name

    def unplug(self, module_id):
        self.modules[module_id].online = False
        Controller.on_module_status_change(self.ctl, module_id, "offline")

    def replug(self, module_id, rebooted=True):
        mod = self.modules[module_id]
        mod.online = True
        if rebooted:
            mod.recording = False
        self.ctl.health.seed(module_id)
        Controller.on_module_status_change(self.ctl, module_id, "online")
        self.pump()

    def session(self, name):
        return self.rec.sessions[name]

    def events(self, name):
        path = os.path.join(self.share, name, "session_events.log")
        return open(path).read() if os.path.exists(path) else ""

    def gaps_file(self, name):
        with open(os.path.join(self.share, name, "session_gaps.json")) as f:
            return json.load(f)


@pytest.fixture
def fleet():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Fleet(tmpdir)


def test_start_record_stop(fleet):
    name = fleet.start()
    s = fleet.session(name)
    assert s.state == SessionState.ACTIVE
    assert all(m.recording for m in fleet.modules.values())

    fleet.rec.stop_session(name)
    fleet.pump()

    assert s.state == SessionState.STOPPED
    assert not any(m.recording for m in fleet.modules.values())
    assert "FAULT" not in fleet.events(name)
    assert s.gaps == []


def test_timed_stop_is_sent_once(fleet):
    name = fleet.start(duration_minutes=1)
    s = fleet.session(name)
    s.timed_stop_at = time.time() - 1
    s.recording_start_at = 0

    class _Passes:
        def __init__(self, n):
            self.n = n

        def wait(self, timeout=None):
            self.n -= 1
            return self.n < 0

    with patch.object(fleet.rec, "_check_session_recording_liveness"):
        fleet.rec._monitor_sessions(_Passes(3))
        fleet.pump()

    assert s.state == SessionState.STOPPED
    for m in fleet.modules.values():
        assert m.commands.count("stop_recording") == 1
    assert "Not recording" not in fleet.events(name)


def test_dropout_stays_faulted_until_the_module_is_really_back(fleet):
    """Desk soak 2026-09-30, bug #1 regression + gap record."""
    name = fleet.start()
    s = fleet.session(name)
    last_hb = fleet.ctl.health.h["cam1"]["last_heartbeat"]

    fleet.unplug("cam1")
    fleet.monitor(passes=6)   # 30 s of monitor passes while unplugged

    assert s.state == SessionState.ERROR
    assert "cam1" in s.error_message
    assert "RECOVERY" not in fleet.events(name)
    assert not fleet.ctl.modules.is_module_recording("cam1")
    gap = fleet.gaps_file(name)["gaps"][0]
    assert (gap["cause"], gap["modules"], gap["end_ns"]) == (
        "module_offline", ["cam1"], None)
    assert gap["start_ns"] == int(last_hb * 1e9)

    fleet.replug("cam1")      # power-cycled: comes back not recording
    fleet.monitor(passes=2)

    assert s.state == SessionState.ACTIVE
    assert fleet.modules["cam1"].recording
    assert fleet.modules["cam1"].commands.count("start_recording") == 2
    gap = fleet.gaps_file(name)["gaps"][0]
    assert gap["end_ns"] is not None and gap["recovered"] is True

    fleet.rec.stop_session(name)
    fleet.pump()
    assert s.state == SessionState.STOPPED


def test_crashed_but_online_module_is_rearmed_with_a_gap(fleet):
    name = fleet.start()
    s = fleet.session(name)
    fleet.monitor()           # liveness sees both recording

    # The module's service crashed and restarted: online, not recording.
    fleet.modules["cam2"].recording = False
    Controller.handle_status_update(fleet.ctl, "status/cam2", json.dumps(
        {"type": "heartbeat", "recording": False}))
    fleet.monitor(passes=fleet.rec._NOT_RECORDING_STRIKES_THRESHOLD + 1)

    assert fleet.modules["cam2"].recording
    assert s.state == SessionState.ACTIVE
    causes = [g["cause"] for g in fleet.gaps_file(name)["gaps"]]
    assert causes == ["not_recording"]
    assert fleet.gaps_file(name)["gaps"][0]["recovered"] is True


def test_export_guard_blocks_delete_until_exports_land(fleet):
    name = fleet.start()
    fleet.rec.stop_session(name)
    fleet.pump()
    path = f"{name}/20260930/cam1"

    fleet.rec.module_export_update("cam1", path, "pending")
    assert fleet.rec.delete_session(name, delete_files=False).get("export_warning")

    fleet.rec.module_export_update("cam1", path, "failed", final=True)
    result = fleet.rec.delete_session(name, delete_files=False)
    assert result["export_failed_modules"] == ["cam1"]

    fleet.rec.module_export_update("cam1", path, "pending")
    fleet.rec.module_export_update("cam1", path, "complete")
    assert fleet.rec.delete_session(name, delete_files=False) == {"success": True}
