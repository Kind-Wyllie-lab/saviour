"""
Tests for the Habitat Session (plan-driven) path in recording.py:
create / start / pause / resume, per-plan window evaluation with a pinned
clock, the full-stop guard, restart round-trip, and the volume estimate.
"""

import os
import tempfile
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import src.controller.recording as recording_module
from src.controller.recording import Recording, RecordingSession, SessionState

MON_9AM = datetime(2026, 6, 1, 9, 0)      # 2026-06-01 is a Monday
MON_11PM = datetime(2026, 6, 1, 23, 0)
TUE_7AM = datetime(2026, 6, 2, 7, 0)


def _rec(**module_types):
    recording_module.SESSIONS_FILE = os.path.join(tempfile.mkdtemp(), "sessions.json")
    with patch("src.controller.recording.threading.Thread"):
        rec = Recording()
    facade = MagicMock()
    facade.get_config.return_value = {}
    facade.get_modules.return_value = {
        m: {"type": t} for m, t in (module_types or {"c1": "camera"}).items()
    }
    facade.get_module_health.return_value = {
        "status": "online", "last_heartbeat": time.time(), "ptp4l_offset_ns": 1000,
    }
    facade.is_module_recording.return_value = False
    rec.facade = facade
    rec._check_share_writable = lambda: None
    rec._check_ptp_sync = lambda mods: {"ok": True}
    return rec, facade


_TWO_PLANS = [
    {"plan_id": "cams", "label": "Cameras", "modules": ["c1", "c2"]},
    {"plan_id": "mics", "label": "Night audio", "modules": ["m1"],
     "strategy": "windows", "windows": [{"start": "20:00", "end": "06:00"}]},
]


def _commands(facade, name="start_recording"):
    return [c for c in facade.send_command.call_args_list if c.args[1] == name]


# --------------------------------------------------------------------------- #
# create / validate                                                           #
# --------------------------------------------------------------------------- #


def test_create_habitat_session_pending_and_unattended():
    rec, _ = _rec(c1="camera", c2="camera", m1="microphone")
    res = rec.create_habitat_session("hab", _TWO_PLANS, researcher="ab")
    assert res["success"]
    s = rec.sessions[res["session_name"]]
    assert s.state == SessionState.PENDING
    assert s.unattended is True
    assert [p.plan_id for p in s.plans] == ["cams", "mics"]
    assert sorted(s.modules) == ["c1", "c2", "m1"]


def test_create_rejects_offline_module():
    rec, _ = _rec(c1="camera")  # c2 / m1 not online
    res = rec.create_habitat_session("hab", _TWO_PLANS)
    assert not res["success"] and "offline" in res["error"]


def test_create_rejects_module_in_two_plans():
    rec, _ = _rec(c1="camera")
    res = rec.create_habitat_session("hab", [
        {"plan_id": "a", "label": "A", "modules": ["c1"]},
        {"plan_id": "b", "label": "B", "modules": ["c1"]},
    ])
    assert not res["success"] and "more than one plan" in res["error"]


# --------------------------------------------------------------------------- #
# start / window evaluation                                                   #
# --------------------------------------------------------------------------- #


def test_start_records_only_in_window_plans():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_9AM
        rec.start_pending_session("hab")
    s = rec.sessions["hab"]
    assert s.state == SessionState.ACTIVE
    started = {c.args[0] for c in _commands(facade)}
    assert started == {"c1", "c2"}                 # continuous plan only
    assert s.plans[0].recording is True and s.plans[1].recording is False


def test_night_window_opens_and_closes():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    rec.sessions["hab"].state = SessionState.ACTIVE
    s = rec.sessions["hab"]

    rec._evaluate_plans("hab", s, now=MON_11PM)
    assert s.plans[1].recording is True
    assert "m1" in {c.args[0] for c in _commands(facade)}

    facade.is_module_recording.return_value = True
    rec._evaluate_plans("hab", s, now=TUE_7AM)     # window closed
    assert s.plans[1].recording is False
    assert any(c.args[0] == "m1" for c in _commands(facade, "stop_recording"))


# --------------------------------------------------------------------------- #
# pause / resume                                                              #
# --------------------------------------------------------------------------- #


def test_pause_stops_recording_plans_and_resume_re_evaluates():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_9AM
        rec.start_pending_session("hab")
    s = rec.sessions["hab"]
    facade.is_module_recording.return_value = True

    assert rec.pause_session("hab")["success"]
    assert s.state == SessionState.PAUSED
    assert all(not p.recording for p in s.plans)
    assert {c.args[0] for c in _commands(facade, "stop_recording")} == {"c1", "c2"}

    facade.is_module_recording.return_value = False
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_9AM
        assert rec.resume_session("hab")["success"]
    assert s.state == SessionState.ACTIVE
    assert s.plans[0].recording is True


def test_pause_rejected_when_not_active():
    rec, _ = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)          # PENDING
    assert not rec.pause_session("hab")["success"]


# --------------------------------------------------------------------------- #
# full stop guard + restart round-trip                                        #
# --------------------------------------------------------------------------- #


def test_stop_session_ends_the_whole_habitat_session():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_11PM
        rec.start_pending_session("hab")
    facade.is_module_recording.return_value = False   # all modules idle -> immediate
    rec.stop_session("hab")
    assert rec.sessions["hab"].state == SessionState.STOPPED
    assert "hab" not in rec._full_stopping


def test_window_close_does_not_end_the_session():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    rec.sessions["hab"].state = SessionState.ACTIVE
    s = rec.sessions["hab"]
    rec._evaluate_plans("hab", s, now=MON_11PM)       # mics on
    facade.is_module_recording.return_value = True
    rec._evaluate_plans("hab", s, now=TUE_7AM)        # mics window closes
    rec._check_all_stopped("hab")                     # would fire from module_stopped
    assert s.state == SessionState.ACTIVE             # cams still going


def test_reload_rehydrates_plans_and_keeps_active():
    from src.controller.recording_plans import RecordingPlan

    rec, _ = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    rec.sessions["hab"].state = SessionState.ACTIVE
    rec._save_sessions()
    saved_path = recording_module.SESSIONS_FILE

    with patch("src.controller.recording.threading.Thread"):
        recording_module.SESSIONS_FILE = saved_path
        rec2 = Recording()               # __init__ calls _load_sessions()
    rec2._check_share_writable = lambda: None

    s = rec2.sessions["hab"]
    assert isinstance(s, RecordingSession)
    # unattended -> not forced to ERROR on restart
    assert s.state == SessionState.ACTIVE
    assert all(isinstance(p, RecordingPlan) for p in s.plans)
    assert s.plans[1].windows == [{"start": "20:00", "end": "06:00"}]


# --------------------------------------------------------------------------- #
# volume estimate                                                             #
# --------------------------------------------------------------------------- #


def _space(rec, free_pct):
    rec._check_nas_space = lambda: {
        "ok": True, "free_pct": free_pct, "free_gb": free_pct * 2,
    }


def test_disk_autopause_then_autoresume():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_11PM
        rec.start_pending_session("hab")
    s = rec.sessions["hab"]
    facade.is_module_recording.return_value = True

    _space(rec, 3)                       # below nas_min_free_pct (5)
    rec._check_habitat_disk_autopause()
    assert s.state == SessionState.PAUSED
    assert s.pause_reason == "disk"
    assert all(not p.recording for p in s.plans)

    _space(rec, 8)                       # recovering but below resume (15)
    rec._check_habitat_disk_autopause()
    assert s.state == SessionState.PAUSED

    facade.is_module_recording.return_value = False
    _space(rec, 20)                      # above resume threshold
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_11PM
        rec._check_habitat_disk_autopause()
    assert s.state == SessionState.ACTIVE
    assert s.pause_reason is None


def test_operator_pause_is_not_auto_resumed_by_free_space():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_9AM
        rec.start_pending_session("hab")
    facade.is_module_recording.return_value = True
    rec.pause_session("hab")
    assert rec.sessions["hab"].pause_reason == "operator"

    _space(rec, 50)
    rec._check_habitat_disk_autopause()
    assert rec.sessions["hab"].state == SessionState.PAUSED   # stays paused


def test_disk_check_noop_when_space_healthy():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    rec.create_habitat_session("hab", _TWO_PLANS)
    with patch("src.controller.recording.datetime") as dt:
        dt.now.return_value = MON_11PM
        rec.start_pending_session("hab")
    _space(rec, 40)
    rec._check_habitat_disk_autopause()
    assert rec.sessions["hab"].state == SessionState.ACTIVE


def test_estimate_habitat_volume_projects_and_checks_space():
    rec, facade = _rec(c1="camera", c2="camera", m1="microphone")
    est = rec.estimate_habitat_volume(_TWO_PLANS, expected_minutes=24 * 60)
    assert est["success"]
    assert len(est["plans"]) == 2
    cams = next(p for p in est["plans"] if p["plan_id"] == "cams")
    mics = next(p for p in est["plans"] if p["plan_id"] == "mics")
    assert cams["duty_fraction"] == 1.0
    assert mics["duty_fraction"] < 0.5               # 10h/24h window
    assert est["projected_bytes_total"] > 0
    assert "share_free_bytes" in est
