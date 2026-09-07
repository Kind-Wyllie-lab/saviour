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
from unittest.mock import MagicMock

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
