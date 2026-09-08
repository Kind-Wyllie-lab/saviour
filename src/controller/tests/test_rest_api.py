"""
Tests for src/controller/rest_api.py -- the /api/v1 REST blueprint.

Exercised through Flask's test client with web.facade mocked (same
approach as test_web.py's Tier 2/3). Most routes take the admin password
(or an API token) as a bearer token; the index route is open, and the
/tokens routes require the admin password specifically. The SSE
/events stream is pulled single-threaded (the test client runs the
generator lazily on next()).
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from src.controller.recording import RecordingSession
from src.controller.tests.test_web import _make_web


def _web(**config_overrides):
    """A Web instance with a JSON-safe MagicMock facade and an isolated
    admin-credentials file. Returns (web, password)."""
    web = _make_web(**config_overrides)
    facade = MagicMock()
    facade.get_modules.return_value = {}
    facade.get_module_health.return_value = {}
    facade.get_health_summary.return_value = {
        "total_modules": 0, "online_modules": 0, "offline_modules": 0,
    }
    facade.get_recording_sessions.return_value = {}
    facade.get_recording_status.return_value = False
    facade.get_uptime.return_value = 123
    facade.get_ptp_sync.return_value = 0
    facade.get_modules_by_target.return_value = {}
    facade.is_module_recording.return_value = False
    facade.check_ptp_sync.return_value = {
        "ok": True, "max_offset_us": 5.0, "threshold_us": 50}
    web.facade = facade
    web._write_session_metadata = MagicMock()

    tmp = tempfile.mkdtemp()
    web._ADMIN_CREDENTIALS_FILE = os.path.join(tmp, "admin_credentials")
    web._API_TOKENS_FILE = os.path.join(tmp, "api_tokens.json")
    password = web._get_or_create_admin_password()
    return web, password


def _auth(password):
    return {"Authorization": f"Bearer {password}"}


def _session(name, **kw):
    kw.setdefault("target", "all")
    return RecordingSession(session_name=name, **kw)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_state_blocked_without_header(self):
        web, _ = _web()
        assert web.app.test_client().get("/api/v1/state").status_code == 401

    def test_state_blocked_with_wrong_password(self):
        web, _ = _web()
        resp = web.app.test_client().get(
            "/api/v1/state", headers=_auth("nope"))
        assert resp.status_code == 401
        assert resp.get_json()["error"]["code"] == "unauthorized"

    def test_index_needs_no_auth(self):
        web, _ = _web()
        resp = web.app.test_client().get("/api/v1/")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["version"] == "v1"
        assert "/api/v1/state" in body["endpoints"]
        assert "/api/v1/sessions" in body["endpoints"]


# ---------------------------------------------------------------------------
# state / health / ptp
# ---------------------------------------------------------------------------

class TestState:
    def test_state_rollup_shape(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "a": _session("a", state="active"),
            "b": _session("b", state="pending"),
            "c": _session("c", state="error"),
        }
        web.facade.get_recording_status.return_value = True
        web.facade.get_health_summary.return_value = {
            "total_modules": 3, "online_modules": 2, "offline_modules": 1,
        }
        web.facade.get_ptp_sync.return_value = 12_000  # ns

        resp = web.app.test_client().get(
            "/api/v1/state", headers=_auth(password))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["recording"] is True
        assert data["uptime_s"] == 123
        assert data["sessions"] == {
            "total": 3, "active": 1, "pending": 1, "scheduled": 0,
            "paused": 0, "stopped": 0, "error": 1,
        }
        assert data["modules"] == {"total": 3, "online": 2, "offline": 1}
        # default start gate 50 us = 50_000 ns; 12_000 <= that
        assert data["ptp"]["worst_offset_ns"] == 12_000
        assert data["ptp"]["start_gate_ns"] == 50_000
        assert data["ptp"]["synced"] is True
        assert data["disk"]["status"] == "unknown"

    def test_state_ptp_not_synced_when_over_gate(self):
        web, password = _web(**{"recording.ptp_start_gate_us": 50})
        web.facade.get_ptp_sync.return_value = 250_000
        data = web.app.test_client().get(
            "/api/v1/state", headers=_auth(password)).get_json()
        assert data["ptp"]["synced"] is False

    def test_state_ptp_unknown_is_not_synced(self):
        web, password = _web()
        web.facade.get_ptp_sync.side_effect = RuntimeError("no data")
        data = web.app.test_client().get(
            "/api/v1/state", headers=_auth(password)).get_json()
        assert data["ptp"]["worst_offset_ns"] is None
        assert data["ptp"]["synced"] is False

    def test_health_passthrough(self):
        web, password = _web()
        web.facade.get_module_health.return_value = {"cam1": {"cpu_temp": 40}}
        resp = web.app.test_client().get(
            "/api/v1/health", headers=_auth(password))
        assert resp.get_json()["modules"] == {"cam1": {"cpu_temp": 40}}

    def test_ptp_endpoint_projects_per_module_fields(self):
        web, password = _web()
        web.facade.get_module_health.return_value = {
            "cam1": {"ptp4l_offset_ns": 900, "phc2sys_offset_ns": 1200,
                     "ptp4l_freq": -3, "cpu_temp": 40},
        }
        web.facade.get_ptp_sync.return_value = 1200
        data = web.app.test_client().get(
            "/api/v1/ptp", headers=_auth(password)).get_json()
        assert data["modules"] == {
            "cam1": {"ptp4l_offset_ns": 900, "phc2sys_offset_ns": 1200,
                     "ptp4l_freq": -3},
        }
        assert data["worst_offset_ns"] == 1200


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------

class TestModules:
    def test_list_modules(self):
        web, password = _web()
        web.facade.get_modules.return_value = {"cam1": {"type": "camera"}}
        resp = web.app.test_client().get(
            "/api/v1/modules", headers=_auth(password))
        assert resp.get_json() == {"modules": {"cam1": {"type": "camera"}}}

    def test_get_module_404(self):
        web, password = _web()
        resp = web.app.test_client().get(
            "/api/v1/modules/ghost", headers=_auth(password))
        assert resp.status_code == 404
        assert resp.get_json()["error"]["code"] == "not_found"

    def test_get_module_ok(self):
        web, password = _web()
        web.facade.get_modules.return_value = {"cam1": {"type": "camera"}}
        resp = web.app.test_client().get(
            "/api/v1/modules/cam1", headers=_auth(password))
        assert resp.get_json() == {"type": "camera"}

    def test_module_health_404_for_unknown(self):
        web, password = _web()
        resp = web.app.test_client().get(
            "/api/v1/modules/ghost/health", headers=_auth(password))
        assert resp.status_code == 404

    def test_module_health_ok(self):
        web, password = _web()
        web.facade.get_modules.return_value = {"cam1": {}}
        web.facade.get_module_health.return_value = {"cpu_temp": 41}
        resp = web.app.test_client().get(
            "/api/v1/modules/cam1/health", headers=_auth(password))
        assert resp.get_json() == {"cpu_temp": 41}


# ---------------------------------------------------------------------------
# module config -- read + partial write
# ---------------------------------------------------------------------------

class TestModuleConfigRead:
    def _cfg_web(self, **states):
        web, password = _web()
        web.facade.get_modules.return_value = {"cam1": {"type": "camera"}}
        web.facade.get_module_configs.return_value = states
        return web, password

    def test_unknown_module_404(self):
        web, password = self._cfg_web()
        resp = web.app.test_client().get(
            "/api/v1/modules/ghost/config", headers=_auth(password))
        assert resp.status_code == 404

    def test_returns_shape_and_sync_state(self):
        web, password = self._cfg_web(cam1={
            "true_config": {"camera": {"fps": 30}, "_codec": "h264"},
            "target_config": {"camera": {"fps": 30}},
            "status": "SYNCED",
            "diffs": [],
        })
        data = web.app.test_client().get(
            "/api/v1/modules/cam1/config", headers=_auth(password)).get_json()
        assert data["config"] == {"camera": {"fps": 30}}  # _codec filtered
        assert data["config_sync_status"] == "SYNCED"
        assert data["target_config"] == {"camera": {"fps": 30}}

    def test_include_private_shows_underscore_keys(self):
        web, password = self._cfg_web(cam1={
            "true_config": {"camera": {"fps": 30}, "_codec": "h264"},
            "status": "SYNCED",
        })
        data = web.app.test_client().get(
            "/api/v1/modules/cam1/config?include_private=true",
            headers=_auth(password)).get_json()
        assert data["config"]["_codec"] == "h264"

    def test_no_config_state_yet_is_unknown(self):
        web, password = self._cfg_web()
        data = web.app.test_client().get(
            "/api/v1/modules/cam1/config", headers=_auth(password)).get_json()
        assert data["config"] == {}
        assert data["config_sync_status"] == "UNKNOWN"


class TestModuleConfigPatch:
    def _patch_web(self, true_config=None, status="SYNCED"):
        web, password = _web()
        web.facade.get_modules.return_value = {"cam1": {"type": "camera"}}
        web.facade.is_module_recording.return_value = False
        state = {
            "true_config": true_config if true_config is not None else {
                "camera": {"fps": 30, "sensor_mode_index": 0},
                "recording": {"segment_duration_s": 3600},
            },
            "target_config": {},
            "status": status,
            "diffs": [],
        }
        web.facade.get_module_configs.return_value = {"cam1": state}

        # Mirror the real set_target_module_config: it records intent and
        # flips the sync status to PENDING before the command goes out.
        def _record_intent(mid, cfg):
            state["target_config"] = cfg
            state["status"] = "PENDING"

        web.facade.set_target_module_config.side_effect = _record_intent
        return web, password, state

    def test_unknown_module_404(self):
        web, password, _ = self._patch_web()
        resp = web.app.test_client().patch(
            "/api/v1/modules/ghost/config", json={"camera": {"fps": 60}},
            headers=_auth(password))
        assert resp.status_code == 404

    def test_empty_body_is_400(self):
        web, password, _ = self._patch_web()
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config", json={}, headers=_auth(password))
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_request"

    def test_merges_partial_and_dispatches_full_config(self):
        web, password, _ = self._patch_web()
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config",
            json={"camera": {"fps": 90}}, headers=_auth(password))
        assert resp.status_code == 202
        assert resp.get_json()["config_sync_status"] == "PENDING"
        # full merged config reaches both the intent-record and the wire
        expected = {
            "camera": {"fps": 90, "sensor_mode_index": 0},
            "recording": {"segment_duration_s": 3600},
        }
        web.facade.set_target_module_config.assert_called_once_with(
            "cam1", expected)
        web.facade.send_command.assert_called_once_with(
            "cam1", "set_config", expected)

    def test_multiple_sections_and_keys_at_once(self):
        web, password, _ = self._patch_web()
        web.app.test_client().patch(
            "/api/v1/modules/cam1/config",
            json={"camera": {"fps": 120, "sensor_mode_index": 2},
                  "recording": {"segment_duration_s": 600}},
            headers=_auth(password))
        sent = web.facade.send_command.call_args[0][2]
        assert sent["camera"] == {"fps": 120, "sensor_mode_index": 2}
        assert sent["recording"] == {"segment_duration_s": 600}

    def test_private_keys_stripped_from_patch(self):
        web, password, _ = self._patch_web()
        web.app.test_client().patch(
            "/api/v1/modules/cam1/config",
            json={"camera": {"fps": 60}, "_codec": "hevc",
                  "recording": {"_secret": 1}},
            headers=_auth(password))
        sent = web.facade.send_command.call_args[0][2]
        assert "_codec" not in sent
        assert "_secret" not in sent.get("recording", {})

    def test_rejected_while_recording_409(self):
        web, password, _ = self._patch_web()
        web.facade.is_module_recording.return_value = True
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config",
            json={"camera": {"fps": 60}}, headers=_auth(password))
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "module_recording"
        web.facade.send_command.assert_not_called()

    def test_no_reported_config_is_409(self):
        web, password, _ = self._patch_web(true_config={})
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config",
            json={"camera": {"fps": 60}}, headers=_auth(password))
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "config_unavailable"

    def test_wait_returns_200_once_synced(self):
        web, password, state = self._patch_web(status="PENDING")

        calls = {"n": 0}

        def _configs():
            calls["n"] += 1
            st = dict(state)
            st["status"] = "SYNCED" if calls["n"] > 2 else "PENDING"
            return {"cam1": st}

        web.facade.get_module_configs.side_effect = _configs
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config?wait=5",
            json={"camera": {"fps": 60}}, headers=_auth(password))
        assert resp.status_code == 200
        assert resp.get_json()["config_sync_status"] == "SYNCED"

    def test_bad_wait_value_is_400(self):
        web, password, _ = self._patch_web()
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config?wait=soon",
            json={"camera": {"fps": 60}}, headers=_auth(password))
        assert resp.status_code == 400

    def test_readonly_token_blocked_on_patch(self):
        web, password, _ = self._patch_web()
        token = web.mint_api_token("dash", readonly=True)["token"]
        resp = web.app.test_client().patch(
            "/api/v1/modules/cam1/config",
            json={"camera": {"fps": 60}}, headers=_auth(token))
        assert resp.status_code == 403
        web.facade.send_command.assert_not_called()


# ---------------------------------------------------------------------------
# system -- controller self-update
# ---------------------------------------------------------------------------

class TestSystemUpdate:
    def _patch_su(self, **over):
        import src.controller.system_update as su_mod
        p = {
            "git_checkout_info": MagicMock(return_value={
                "available": True, "branch": "staging", "remote": "git@x:y.git"}),
            "snapshot": MagicMock(return_value={"ok": True, "name": "snap1"}),
            "pull_and_reset": MagicMock(return_value={
                "branch": "staging", "old_commit": "aaa", "new_commit": "bbb"}),
            "stage_zip": MagicMock(return_value={"version": "v1"}),
            "notify_modules": MagicMock(return_value=3),
            "build_and_restart": MagicMock(),
        }
        p.update(over)
        return [patch.object(su_mod, name, val) for name, val in p.items()], p

    def test_info_route_passthrough(self):
        web, password = _web()
        patches, mocks = self._patch_su()
        for pt in patches:
            pt.start()
        try:
            resp = web.app.test_client().get(
                "/api/v1/system/update", headers=_auth(password))
            assert resp.status_code == 200
            assert resp.get_json()["branch"] == "staging"
        finally:
            for pt in patches:
                pt.stop()

    def test_post_requires_admin_not_token(self):
        web, password = _web()
        token = web.mint_api_token("rig")["token"]
        resp = web.app.test_client().post(
            "/api/v1/system/update", json={}, headers=_auth(token))
        assert resp.status_code == 401

    def test_post_no_checkout_is_409(self):
        web, password = _web()
        patches, _ = self._patch_su(git_checkout_info=MagicMock(
            return_value={"available": False, "reason": "No git checkout"}))
        for pt in patches:
            pt.start()
        try:
            resp = web.app.test_client().post(
                "/api/v1/system/update", json={}, headers=_auth(password))
            assert resp.status_code == 409
            assert resp.get_json()["error"]["code"] == "update_unavailable"
        finally:
            for pt in patches:
                pt.stop()

    def test_post_apply_false_is_200_and_no_restart(self):
        web, password = _web()
        patches, mocks = self._patch_su()
        for pt in patches:
            pt.start()
        try:
            resp = web.app.test_client().post(
                "/api/v1/system/update",
                json={"apply_controller": False}, headers=_auth(password))
            assert resp.status_code == 200
            body = resp.get_json()
            assert body == {"branch": "staging", "old_commit": "aaa",
                            "new_commit": "bbb", "modules_notified": 0,
                            "applying": False}
            mocks["snapshot"].assert_called_once()
            mocks["pull_and_reset"].assert_called_once_with("staging")
            mocks["stage_zip"].assert_called_once()
            mocks["build_and_restart"].assert_not_called()
        finally:
            for pt in patches:
                pt.stop()

    def test_post_default_applies_and_returns_202(self):
        web, password = _web()
        patches, mocks = self._patch_su()
        for pt in patches:
            pt.start()
        try:
            resp = web.app.test_client().post(
                "/api/v1/system/update", json={}, headers=_auth(password))
            assert resp.status_code == 202
            assert resp.get_json()["applying"] is True
        finally:
            for pt in patches:
                pt.stop()
        # the restart thread was started (give it a moment to run the mock)
        import time as _t
        _t.sleep(0.1)
        mocks["build_and_restart"].assert_called_once()

    def test_post_deploy_modules_notifies(self):
        web, password = _web()
        web.facade.get_modules.return_value = {"cam_a": {}, "cam_b": {}}
        patches, mocks = self._patch_su()
        for pt in patches:
            pt.start()
        try:
            resp = web.app.test_client().post(
                "/api/v1/system/update",
                json={"apply_controller": False, "deploy_modules": True},
                headers=_auth(password))
            assert resp.status_code == 200
            assert resp.get_json()["modules_notified"] == 3
            mocks["notify_modules"].assert_called_once()
        finally:
            for pt in patches:
                pt.stop()

    def test_post_git_failure_is_500(self):
        web, password = _web()
        import subprocess as _sp
        patches, _ = self._patch_su(pull_and_reset=MagicMock(
            side_effect=_sp.CalledProcessError(1, "git", stderr="fatal: boom")))
        for pt in patches:
            pt.start()
        try:
            resp = web.app.test_client().post(
                "/api/v1/system/update", json={}, headers=_auth(password))
            assert resp.status_code == 500
            assert resp.get_json()["error"]["code"] == "git_failed"
            assert "boom" in resp.get_json()["error"]["message"]
        finally:
            for pt in patches:
                pt.stop()


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------

class TestExports:
    def test_exports_rollup(self):
        web, password = _web()
        web.get_exported_recordings = MagicMock(return_value=["s/clip.mp4"])
        web.facade.get_recording_sessions.return_value = {
            "a": _session("a", state="stopped", pending_exports=2,
                          total_exports_failed=1),
            "b": _session("b", state="active", pending_exports=3),
        }
        data = web.app.test_client().get(
            "/api/v1/exports", headers=_auth(password)).get_json()
        assert data["exported_recordings"] == ["s/clip.mp4"]
        assert data["pending_exports"] == 5
        assert data["failed_exports"] == 1


# ---------------------------------------------------------------------------
# sessions -- read
# ---------------------------------------------------------------------------

class TestSessionReads:
    def test_list_sessions_serialises_dataclasses(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "sess_a": _session("sess_a", state="active", modules=["cam1"]),
        }
        data = web.app.test_client().get(
            "/api/v1/sessions", headers=_auth(password)).get_json()
        assert data["sessions"]["sess_a"]["state"] == "active"
        assert data["sessions"]["sess_a"]["modules"] == ["cam1"]

    def test_get_session_404(self):
        web, password = _web()
        resp = web.app.test_client().get(
            "/api/v1/sessions/nope", headers=_auth(password))
        assert resp.status_code == 404

    def test_get_session_ok(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "sess_a": _session("sess_a", state="pending"),
        }
        resp = web.app.test_client().get(
            "/api/v1/sessions/sess_a", headers=_auth(password))
        assert resp.status_code == 200
        assert resp.get_json()["session_name"] == "sess_a"


# ---------------------------------------------------------------------------
# sessions -- lifecycle
# ---------------------------------------------------------------------------

class TestSessionCreate:
    def test_name_required(self):
        web, password = _web()
        resp = web.app.test_client().post(
            "/api/v1/sessions", json={"target": "all"}, headers=_auth(password))
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_request"

    def test_create_happy_path_pending(self):
        web, password = _web()
        web.facade.create_session.return_value = {
            "success": True, "session_name": "MyExp_all_20260907"}
        web.facade.get_recording_sessions.return_value = {
            "MyExp_all_20260907": _session(
                "MyExp_all_20260907", state="pending"),
        }
        resp = web.app.test_client().post(
            "/api/v1/sessions",
            json={"name": "MyExp", "target": "all", "researcher": "asg"},
            headers=_auth(password))
        assert resp.status_code == 201
        assert resp.get_json()["session_name"] == "MyExp_all_20260907"
        assert resp.get_json()["state"] == "pending"
        web.facade.create_session.assert_called_once_with(
            "MyExp", "all", None, "asg", unattended=False)
        web._write_session_metadata.assert_called_once()
        web.facade.force_start_session.assert_not_called()

    def test_create_with_autostart(self):
        web, password = _web()
        web.facade.create_session.return_value = {
            "success": True, "session_name": "s1"}
        web.facade.force_start_session.return_value = {"success": True}
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active"),
        }
        resp = web.app.test_client().post(
            "/api/v1/sessions",
            json={"name": "s1", "autostart": True},
            headers=_auth(password))
        assert resp.status_code == 201
        web.facade.force_start_session.assert_called_once_with("s1")
        assert "autostart_error" not in resp.get_json()

    def test_create_autostart_failure_still_201_with_warning(self):
        web, password = _web()
        web.facade.create_session.return_value = {
            "success": True, "session_name": "s1"}
        web.facade.force_start_session.return_value = {
            "success": False, "error": "PTP not synced"}
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="pending"),
        }
        resp = web.app.test_client().post(
            "/api/v1/sessions", json={"name": "s1", "autostart": True},
            headers=_auth(password))
        assert resp.status_code == 201
        assert resp.get_json()["autostart_error"] == "PTP not synced"

    def test_create_rejected_is_409(self):
        web, password = _web()
        web.facade.create_session.return_value = {
            "success": False, "error": "No online modules found for 'all'"}
        resp = web.app.test_client().post(
            "/api/v1/sessions", json={"name": "s1"}, headers=_auth(password))
        assert resp.status_code == 409
        assert resp.get_json()["error"]["message"].startswith("No online")
        web._write_session_metadata.assert_not_called()

    def test_create_share_unavailable_is_503(self):
        web, password = _web()
        web._check_nas_free_space = MagicMock(return_value="NAS unreachable")
        resp = web.app.test_client().post(
            "/api/v1/sessions", json={"name": "s1"}, headers=_auth(password))
        assert resp.status_code == 503
        assert resp.get_json()["error"]["code"] == "share_unavailable"
        web.facade.create_session.assert_not_called()


class TestSessionStopPauseResume:
    def test_stop_404(self):
        web, password = _web()
        resp = web.app.test_client().post(
            "/api/v1/sessions/nope/stop", headers=_auth(password))
        assert resp.status_code == 404

    def test_stop_ok(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active"),
        }
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/stop", headers=_auth(password))
        assert resp.status_code == 200
        web.facade.stop_session.assert_called_once_with("s1")

    def test_pause_conflict_for_plain_session(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active"),
        }
        web.facade.pause_session.return_value = {
            "success": False, "error": "Not a Habitat Session"}
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/pause", headers=_auth(password))
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "pause_rejected"

    def test_resume_ok(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="paused"),
        }
        web.facade.resume_session.return_value = {"success": True}
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/resume", headers=_auth(password))
        assert resp.status_code == 200
        web.facade.resume_session.assert_called_once_with("s1")


class TestSessionDelete:
    def test_delete_unknown_is_404(self):
        web, password = _web()
        web.facade.delete_session.return_value = {
            "error": "Unknown session 'nope'"}
        resp = web.app.test_client().delete(
            "/api/v1/sessions/nope", headers=_auth(password))
        assert resp.status_code == 404

    def test_delete_active_is_409(self):
        web, password = _web()
        web.facade.delete_session.return_value = {
            "error": "Cannot delete a session in state 'active' — stop it first"}
        resp = web.app.test_client().delete(
            "/api/v1/sessions/s1", headers=_auth(password))
        assert resp.status_code == 409

    def test_delete_export_warning_carries_extra_fields(self):
        web, password = _web()
        web.facade.delete_session.return_value = {
            "error": "has unresolved exports", "export_warning": True,
            "pending_exports": 2, "total_exports_failed": 1}
        resp = web.app.test_client().delete(
            "/api/v1/sessions/s1", headers=_auth(password))
        assert resp.status_code == 409
        err = resp.get_json()["error"]
        assert err["export_warning"] is True
        assert err["pending_exports"] == 2

    def test_delete_ok_and_flags_forwarded(self):
        web, password = _web()
        web.facade.delete_session.return_value = {"success": True}
        resp = web.app.test_client().delete(
            "/api/v1/sessions/s1?files=false&force=true",
            headers=_auth(password))
        assert resp.status_code == 200
        assert resp.get_json() == {"deleted": True, "session_name": "s1"}
        web.facade.delete_session.assert_called_once_with("s1", False, True)

    def test_delete_defaults_files_true_force_false(self):
        web, password = _web()
        web.facade.delete_session.return_value = {"success": True}
        web.app.test_client().delete(
            "/api/v1/sessions/s1", headers=_auth(password))
        web.facade.delete_session.assert_called_once_with("s1", True, False)


# ---------------------------------------------------------------------------
# markers
# ---------------------------------------------------------------------------

class TestMarkers:
    def _active(self, web):
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active"),
        }
        web.facade.add_marker.return_value = {
            "success": True, "session_name": "s1",
            "recv_wall_ns": 1_700_000_000_123_000_000,
            "recv_iso": "2026-09-07T12:00:00.123000", "label": "trial_1",
        }

    def test_marker_unknown_session_404(self):
        web, password = _web()
        resp = web.app.test_client().post(
            "/api/v1/sessions/nope/marker", json={"label": "x"},
            headers=_auth(password))
        assert resp.status_code == 404

    def test_marker_requires_label(self):
        web, password = _web()
        self._active(web)
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/marker", json={"source": "pyctl"},
            headers=_auth(password))
        assert resp.status_code == 400

    def test_marker_happy_path(self):
        web, password = _web()
        self._active(web)
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/marker",
            json={"label": "trial_1", "source": "pyControl"},
            headers=_auth(password))
        assert resp.status_code == 201
        assert resp.get_json()["recv_wall_ns"] == 1_700_000_000_123_000_000
        web.facade.add_marker.assert_called_once_with(
            "s1", "trial_1", "pyControl", None)

    def test_marker_converts_client_epoch_seconds_to_ns(self):
        web, password = _web()
        self._active(web)
        web.app.test_client().post(
            "/api/v1/sessions/s1/marker",
            json={"label": "reward", "t": 1700000000.5},
            headers=_auth(password))
        web.facade.add_marker.assert_called_once_with(
            "s1", "reward", None, 1_700_000_000_500_000_000)

    def test_marker_rejected_when_not_active(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="pending"),
        }
        web.facade.add_marker.return_value = {
            "success": False, "error": "Session is pending, not active"}
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/marker", json={"label": "x"},
            headers=_auth(password))
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "marker_rejected"

    def test_marker_bad_t_is_400(self):
        web, password = _web()
        self._active(web)
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/marker",
            json={"label": "x", "t": "not-a-number"},
            headers=_auth(password))
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------

class TestEventStream:
    def test_events_requires_auth(self):
        web, _ = _web()
        assert web.app.test_client().get(
            "/api/v1/events").status_code == 401

    def test_events_stream_delivers_published_events(self):
        web, password = _web()
        client = web.app.test_client()
        resp = client.get("/api/v1/events", headers=_auth(password),
                          buffered=False)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        stream = resp.response
        assert next(stream) == b": connected\n\n"

        web._publish_api_event("marker", {"label": "trial_1"})
        chunk = next(stream).decode()
        assert chunk.startswith("data: ")
        event = json.loads(chunk[len("data: "):])
        assert event["type"] == "marker"
        assert event["label"] == "trial_1"
        resp.close()

    def test_events_types_filter_skips_other_types(self):
        web, password = _web()
        client = web.app.test_client()
        resp = client.get("/api/v1/events?types=marker",
                          headers=_auth(password), buffered=False)
        stream = resp.response
        next(stream)  # ": connected"
        web._publish_api_event("modules", {"modules": {}})
        web._publish_api_event("marker", {"label": "keep"})
        chunk = next(stream).decode()
        assert json.loads(chunk[len("data: "):])["label"] == "keep"
        resp.close()

    def test_subscribe_unsubscribe_bookkeeping(self):
        web, _ = _web()
        q = web._event_subscribe()
        assert q in web._event_subscribers
        web._event_unsubscribe(q)
        assert q not in web._event_subscribers

    def test_publish_with_no_subscribers_is_noop(self):
        web, _ = _web()
        web._publish_api_event("alert", {"key": "x"})  # must not raise

    def test_stream_closing_removes_subscriber(self):
        web, password = _web()
        client = web.app.test_client()
        resp = client.get("/api/v1/events", headers=_auth(password),
                          buffered=False)
        next(resp.response)
        assert len(web._event_subscribers) == 1
        resp.close()
        assert web._event_subscribers == []


# ---------------------------------------------------------------------------
# API token management
# ---------------------------------------------------------------------------

class TestTokens:
    def test_list_requires_admin_password_not_token(self):
        web, password = _web()
        web.mint_api_token("rig3")
        token = web.mint_api_token("rig4")["token"]
        # a valid API token is rejected by the management routes
        resp = web.app.test_client().get(
            "/api/v1/tokens", headers=_auth(token))
        assert resp.status_code == 401
        resp = web.app.test_client().get(
            "/api/v1/tokens", headers=_auth(password))
        assert resp.status_code == 200
        names = {t["name"] for t in resp.get_json()["tokens"]}
        assert names == {"rig3", "rig4"}

    def test_mint_returns_token_once(self):
        web, password = _web()
        resp = web.app.test_client().post(
            "/api/v1/tokens", json={"name": "pyControl"},
            headers=_auth(password))
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["name"] == "pyControl"
        assert len(body["token"]) > 20

    def test_minted_token_works_on_data_routes(self):
        web, password = _web()
        token = web.app.test_client().post(
            "/api/v1/tokens", json={"name": "rig"},
            headers=_auth(password)).get_json()["token"]
        web.facade.get_modules.return_value = {"cam1": {}}
        resp = web.app.test_client().get(
            "/api/v1/modules", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json() == {"modules": {"cam1": {}}}

    def test_duplicate_name_is_409(self):
        web, password = _web()
        web.mint_api_token("dup")
        resp = web.app.test_client().post(
            "/api/v1/tokens", json={"name": "dup"}, headers=_auth(password))
        assert resp.status_code == 409

    def test_empty_name_is_400(self):
        web, password = _web()
        resp = web.app.test_client().post(
            "/api/v1/tokens", json={"name": "  "}, headers=_auth(password))
        assert resp.status_code == 400

    def test_revoke(self):
        web, password = _web()
        token = web.mint_api_token("gone")["token"]
        resp = web.app.test_client().delete(
            "/api/v1/tokens/gone", headers=_auth(password))
        assert resp.status_code == 200
        assert resp.get_json() == {"revoked": True, "name": "gone"}
        # the revoked token no longer authenticates
        assert web._api_token_matches(token) is False

    def test_revoke_unknown_is_404(self):
        web, password = _web()
        resp = web.app.test_client().delete(
            "/api/v1/tokens/ghost", headers=_auth(password))
        assert resp.status_code == 404

    def test_persist_is_atomic_no_temp_left_behind(self):
        web, password = _web()
        web.mint_api_token("a")
        web.mint_api_token("b")
        web.revoke_api_token("a")
        d = os.path.dirname(web._API_TOKENS_FILE)
        assert not [f for f in os.listdir(d) if f.endswith(".tmp")]
        # survives a fresh read from disk
        web._api_tokens_cache = (None, [])
        assert [t["name"] for t in web._load_api_tokens()] == ["b"]


class TestReadOnlyTokens:
    def test_readonly_token_can_get(self):
        web, password = _web()
        token = web.mint_api_token("dash", readonly=True)["token"]
        web.facade.get_modules.return_value = {"cam1": {}}
        resp = web.app.test_client().get(
            "/api/v1/modules", headers=_auth(token))
        assert resp.status_code == 200

    def test_readonly_token_blocked_on_post(self):
        web, password = _web()
        token = web.mint_api_token("dash", readonly=True)["token"]
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active")}
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/stop", headers=_auth(token))
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "forbidden"
        web.facade.stop_session.assert_not_called()

    def test_readonly_token_blocked_on_delete(self):
        web, password = _web()
        token = web.mint_api_token("dash", readonly=True)["token"]
        resp = web.app.test_client().delete(
            "/api/v1/sessions/s1", headers=_auth(token))
        assert resp.status_code == 403

    def test_full_token_still_allowed_on_post(self):
        web, password = _web()
        token = web.mint_api_token("rig", readonly=False)["token"]
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active")}
        resp = web.app.test_client().post(
            "/api/v1/sessions/s1/stop", headers=_auth(token))
        assert resp.status_code == 200

    def test_readonly_flag_surfaced_in_list(self):
        web, password = _web()
        web.mint_api_token("ro", readonly=True)
        web.mint_api_token("rw")
        resp = web.app.test_client().get(
            "/api/v1/tokens", headers=_auth(password))
        by_name = {t["name"]: t["readonly"]
                   for t in resp.get_json()["tokens"]}
        assert by_name == {"ro": True, "rw": False}


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

class TestReadiness:
    def _ready_web(self):
        web, password = _web()
        web.facade.get_modules_by_target.return_value = {
            "cam1": {"online": True}, "cam2": {"online": True},
        }
        web.facade.check_ptp_sync.return_value = {
            "ok": True, "max_offset_us": 6.2, "threshold_us": 50}
        web.facade.is_module_recording.return_value = False
        web._nas_health = {"status": "ok", "free_pct": 42.0}
        return web, password

    def test_all_checks_pass(self):
        web, password = self._ready_web()
        data = web.app.test_client().get(
            "/api/v1/readiness", headers=_auth(password)).get_json()
        assert data["ready"] is True
        assert set(data["checks"]) == {
            "modules_present", "modules_online", "ptp", "share",
            "not_already_recording"}
        assert data["checks"]["ptp"]["worst_offset_us"] == 6.2

    def test_offline_module_makes_not_ready(self):
        web, password = self._ready_web()
        web.facade.get_modules_by_target.return_value = {
            "cam1": {"online": True}, "cam2": {"online": False},
        }
        data = web.app.test_client().get(
            "/api/v1/readiness", headers=_auth(password)).get_json()
        assert data["ready"] is False
        assert data["checks"]["modules_online"]["ok"] is False
        assert "cam2" in data["checks"]["modules_online"]["detail"]

    def test_ptp_failure_surfaces_detail(self):
        web, password = self._ready_web()
        web.facade.check_ptp_sync.return_value = {
            "ok": False, "error": "PTP not synchronised on 1 module(s)"}
        data = web.app.test_client().get(
            "/api/v1/readiness", headers=_auth(password)).get_json()
        assert data["ready"] is False
        assert data["checks"]["ptp"]["ok"] is False
        assert data["checks"]["ptp"]["detail"].startswith("PTP not")

    def test_unknown_share_health_blocks(self):
        web, password = self._ready_web()
        web._nas_health = {"status": "unknown"}
        data = web.app.test_client().get(
            "/api/v1/readiness", headers=_auth(password)).get_json()
        assert data["checks"]["share"]["ok"] is False

    def test_already_recording_blocks(self):
        web, password = self._ready_web()
        web.facade.is_module_recording.side_effect = lambda m: m == "cam1"
        data = web.app.test_client().get(
            "/api/v1/readiness", headers=_auth(password)).get_json()
        assert data["ready"] is False
        assert "cam1" in data["checks"]["not_already_recording"]["detail"]

    def test_no_modules_for_target(self):
        web, password = self._ready_web()
        web.facade.get_modules_by_target.return_value = {}
        data = web.app.test_client().get(
            "/api/v1/readiness?target=ghost", headers=_auth(password)).get_json()
        assert data["ready"] is False
        assert data["checks"]["modules_present"]["ok"] is False


# ---------------------------------------------------------------------------
# marker read-back
# ---------------------------------------------------------------------------

class TestMarkerReadback:
    def test_markers_404_for_unknown_session(self):
        web, password = _web()
        resp = web.app.test_client().get(
            "/api/v1/sessions/nope/markers", headers=_auth(password))
        assert resp.status_code == 404

    def test_markers_happy(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active")}
        web.facade.get_markers.return_value = {
            "success": True, "count": 1,
            "markers": [{"recv_wall_ns": 1, "label": "trial_1",
                         "source": "", "client_wall_ns": None}]}
        resp = web.app.test_client().get(
            "/api/v1/sessions/s1/markers", headers=_auth(password))
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1
        web.facade.get_markers.assert_called_once_with("s1", None, None)

    def test_markers_since_and_limit_forwarded(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active")}
        web.facade.get_markers.return_value = {
            "success": True, "count": 0, "markers": []}
        web.app.test_client().get(
            "/api/v1/sessions/s1/markers?since=1700000000&limit=5",
            headers=_auth(password))
        web.facade.get_markers.assert_called_once_with(
            "s1", 1_700_000_000_000_000_000, 5)

    def test_markers_bad_since_is_400(self):
        web, password = _web()
        web.facade.get_recording_sessions.return_value = {
            "s1": _session("s1", state="active")}
        resp = web.app.test_client().get(
            "/api/v1/sessions/s1/markers?since=soon", headers=_auth(password))
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

class TestOpenAPI:
    def test_openapi_json_is_open_and_valid(self):
        web, _ = _web()
        resp = web.app.test_client().get("/api/v1/openapi.json")
        assert resp.status_code == 200
        spec = resp.get_json()
        assert spec["openapi"].startswith("3.")
        assert "/api/v1/sessions" in spec["paths"]
        assert "/api/v1/readiness" in spec["paths"]
        assert "/api/v1/sessions/{session_name}/marker" in spec["paths"]
