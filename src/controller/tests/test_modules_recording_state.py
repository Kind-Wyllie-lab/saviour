"""
Tests for Modules' local recording-pipeline state tracking
(update_recording_state / get_recording_state / get_recording_states_for_session
in src/controller/modules.py).

This is the controller-side counterpart to Export.summarize_recording_state()
(src/modules/export.py) -- a module reports its pending/to_export/exported
summary via the report_recording_state command; Modules stores the latest
report per module_id so the Recordings page can show a session's state while
it's still running, not just after it stops.
"""

from src.controller.modules import Module, Modules, ModuleRecordingState


def _make_modules() -> Modules:
    m = Modules()
    m.facade = None
    return m


def _register(mgr: Modules, module_id: str = "habitat_camera_a") -> Module:
    module = Module(
        id=module_id, name=module_id, type="habitat_camera", version="1.0", ip="10.0.0.2"
    )
    mgr.add_module(module)
    return module


class TestUpdateRecordingState:
    def test_stores_summary_and_timestamp(self):
        mgr = _make_modules()
        _register(mgr, "cam1")
        status_data = {
            "type": "cmd_ack", "command": "report_recording_state",
            "pending": {"count": 1}, "to_export": {"count": 0}, "exported": {"count": 3},
        }
        mgr.update_recording_state("cam1", status_data)
        state = mgr.get_recording_state("cam1")
        assert state.summary == {
            "pending": {"count": 1}, "to_export": {"count": 0}, "exported": {"count": 3},
        }
        assert state.last_reported > 0

    def test_strips_cmd_ack_envelope_keys(self):
        """Only the folder-summary keys should be kept -- not 'type'/'command'."""
        mgr = _make_modules()
        _register(mgr, "cam1")
        mgr.update_recording_state("cam1", {
            "type": "cmd_ack", "command": "report_recording_state",
            "pending": {}, "to_export": {}, "exported": {},
        })
        state = mgr.get_recording_state("cam1")
        assert "type" not in state.summary
        assert "command" not in state.summary

    def test_unregistered_module_still_recorded(self):
        """update_recording_state shouldn't require the module to already be
        known -- a report arriving in an odd order (e.g. right at startup)
        should not be silently dropped."""
        mgr = _make_modules()
        mgr.update_recording_state("cam_unregistered", {"pending": {}, "to_export": {}, "exported": {}})
        assert mgr.get_recording_state("cam_unregistered") is not None


class TestGetRecordingState:
    def test_never_reported_module_has_no_summary_but_exists(self):
        """add_module() pre-seeds an empty ModuleRecordingState so callers
        never hit a bare KeyError -- but last_reported stays 0 until a real
        report arrives, which get_recording_states_for_session relies on to
        distinguish "no report yet" from "reported and empty"."""
        mgr = _make_modules()
        _register(mgr, "cam1")
        state = mgr.get_recording_state("cam1")
        assert state == ModuleRecordingState()
        assert state.last_reported == 0.0

    def test_unknown_module_returns_none(self):
        mgr = _make_modules()
        assert mgr.get_recording_state("never_seen") is None


class TestGetRecordingStatesForSession:
    def test_omits_modules_that_have_never_reported(self):
        mgr = _make_modules()
        _register(mgr, "cam1")
        _register(mgr, "cam2")
        mgr.update_recording_state("cam1", {"pending": {}, "to_export": {}, "exported": {}})
        result = mgr.get_recording_states_for_session(["cam1", "cam2"])
        assert list(result.keys()) == ["cam1"]

    def test_includes_summary_and_last_reported(self):
        mgr = _make_modules()
        _register(mgr, "cam1")
        mgr.update_recording_state("cam1", {"pending": {"count": 2}, "to_export": {}, "exported": {}})
        result = mgr.get_recording_states_for_session(["cam1"])
        assert result["cam1"]["summary"]["pending"] == {"count": 2}
        assert result["cam1"]["last_reported"] > 0

    def test_module_not_in_session_ignored_even_if_it_reported(self):
        mgr = _make_modules()
        _register(mgr, "cam1")
        _register(mgr, "cam_other")
        mgr.update_recording_state("cam1", {"pending": {}, "to_export": {}, "exported": {}})
        mgr.update_recording_state("cam_other", {"pending": {}, "to_export": {}, "exported": {}})
        result = mgr.get_recording_states_for_session(["cam1"])
        assert "cam_other" not in result


class TestRecordingStateLifecycle:
    def test_remove_module_clears_recording_state(self):
        mgr = _make_modules()
        _register(mgr, "cam1")
        mgr.update_recording_state("cam1", {"pending": {}, "to_export": {}, "exported": {}})
        mgr.remove_module("cam1")
        assert mgr.get_recording_state("cam1") is None

    def test_module_id_changed_carries_recording_state_to_new_id(self):
        mgr = _make_modules()
        _register(mgr, "cam_old")
        mgr.update_recording_state("cam_old", {"pending": {"count": 5}, "to_export": {}, "exported": {}})
        mgr.module_id_changed("cam_old", "cam_new")
        assert mgr.get_recording_state("cam_old") is None
        state = mgr.get_recording_state("cam_new")
        assert state.summary["pending"] == {"count": 5}
