#!/usr/bin/env python3
"""
Test script for camera module

"""
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.controller.controller import Controller


def test_controller():
    # c = Controller()
    # assert c
    pass


# ---------------------------------------------------------------------------
# on_module_status_change
#
# Controller is abstract (ABC) and Controller() itself pulls in real
# networking/config setup, so these call the unbound method directly against
# a minimal duck-typed stand-in rather than constructing a real instance.
# ---------------------------------------------------------------------------

class _Ctx:
    """Minimal stand-in duck-typing the bits of Controller that
    on_module_status_change / handle_status_update touch."""
    def __init__(self):
        self.logger = MagicMock()
        self.facade = MagicMock()
        self.modules = MagicMock()
        self.communication = MagicMock()
        self.web = MagicMock()
        self.health = MagicMock()


class TestOnModuleStatusChangeFramesync:
    def test_offline_triggers_reconcile(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "offline")
        ctx.facade.reconcile_framesync.assert_called_once()

    def test_online_triggers_reconcile(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "online")
        ctx.facade.reconcile_framesync.assert_called_once()

    def test_offline_calls_module_offline_before_reconciling(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "offline")
        ctx.facade.module_offline.assert_called_once_with("camera_a")

    def test_online_calls_module_back_online_before_reconciling(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "online")
        ctx.facade.module_back_online.assert_called_once_with("camera_a")


# ---------------------------------------------------------------------------
# handle_status_update -- cmd_ack routing for report_recording_state
# ---------------------------------------------------------------------------

class TestHandleStatusUpdateReportRecordingState:
    def _send(self, ctx, module_id: str, payload: dict):
        import json
        ctx.modules.is_removed.return_value = False
        data = json.dumps({"type": "cmd_ack", "command": "report_recording_state", **payload})
        Controller.handle_status_update(ctx, f"status/{module_id}", data)

    def test_routes_to_modules_update_recording_state(self):
        ctx = _Ctx()
        payload = {"pending": {"count": 1}, "to_export": {"count": 0}, "exported": {"count": 2}}
        self._send(ctx, "habitat_camera_a", payload)
        ctx.modules.update_recording_state.assert_called_once()
        called_module_id, called_payload = ctx.modules.update_recording_state.call_args.args
        assert called_module_id == "habitat_camera_a"
        assert called_payload["pending"] == {"count": 1}

    def test_broadcasts_to_frontend(self):
        ctx = _Ctx()
        payload = {"pending": {"count": 0}, "to_export": {"count": 0}, "exported": {"count": 0}}
        self._send(ctx, "habitat_camera_a", payload)
        ctx.web.broadcast_recording_state_update.assert_called_once()
        called_module_id = ctx.web.broadcast_recording_state_update.call_args.args[0]
        assert called_module_id == "habitat_camera_a"

    def test_other_cmd_acks_do_not_trigger_this_branch(self):
        ctx = _Ctx()
        import json
        ctx.modules.is_removed.return_value = False
        data = json.dumps({"type": "cmd_ack", "command": "get_diagnostics", "result": "ok"})
        Controller.handle_status_update(ctx, "status/habitat_camera_a", data)
        ctx.modules.update_recording_state.assert_not_called()
        ctx.web.broadcast_recording_state_update.assert_not_called()


# ---------------------------------------------------------------------------
# handle_status_update -- recording_stopped reason routing (roadmap A4)
# ---------------------------------------------------------------------------

class TestHandleStatusUpdateRecordingStopped:
    def _send(self, ctx, payload: dict):
        import json
        ctx.modules.is_removed.return_value = False
        data = json.dumps({"type": "recording_stopped", "status": "success",
                           "recording": False, **payload})
        Controller.handle_status_update(ctx, "status/cam1", data)

    def test_operator_stop_is_a_plain_stop(self):
        ctx = _Ctx()
        self._send(ctx, {"reason": "operator"})
        ctx.facade.module_stopped.assert_called_once_with("cam1")
        ctx.facade.module_self_stopped.assert_not_called()

    def test_legacy_stop_without_reason_is_a_plain_stop(self):
        ctx = _Ctx()
        self._send(ctx, {})
        ctx.facade.module_self_stopped.assert_not_called()

    def test_disk_critical_stop_is_routed_as_a_self_stop(self):
        ctx = _Ctx()
        self._send(ctx, {"reason": "disk_critical", "detail": "8% free"})
        ctx.facade.module_stopped.assert_called_once_with("cam1")
        module_id, data = ctx.facade.module_self_stopped.call_args.args
        assert module_id == "cam1"
        assert data["reason"] == "disk_critical"
