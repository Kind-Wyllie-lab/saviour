"""
Tests for src/controller/web.py.

Covers Tier 1 (pure helper functions -- no Flask, no I/O), Tier 2 (plain HTTP
routes, exercised via Flask's test client), and Tier 3 (Socket.IO event
handlers, exercised via Flask-SocketIO's test client with self.facade
mocked). System-mutating code that shells out (NAS mount, deploy_update,
reboot/shutdown, timedatectl) is deliberately out of scope -- that needs
subprocess mocked too and belongs in a separate test module.
"""

import io
import os
import queue
import re
import tempfile
import zipfile
from unittest.mock import MagicMock, call, patch

from src.controller.recording import RecordingSession
from src.controller.web import (
    Web,
    _filter_private_keys,
    _QueueStream,
    _sanitise_config_dict,
)


def _make_config(**overrides) -> MagicMock:
    """MagicMock Config whose .get() resolves dotted keys from `overrides`,
    falling back to the caller's own default otherwise."""
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_web(**config_overrides) -> Web:
    """A Web instance with no facade attached -- fine for Tier 1/2 tests,
    none of which touch self.facade. Assign web.facade = MagicMock()
    yourself for anything that does."""
    config_overrides.setdefault("interface.web_interface_port", 5000)
    return Web(_make_config(**config_overrides))


def _download_qs(web) -> str:
    """A valid ?token=... query string for the session download routes,
    which require one minted via _issue_download_token (normally handed
    out over an authenticated Socket.IO connection -- see
    request_download_token)."""
    return f"?token={web._issue_download_token()}"


# ---------------------------------------------------------------------------
# Tier 1: pure helper functions
# ---------------------------------------------------------------------------

class TestSanitiseConfigDict:
    def test_redacts_sensitive_keys_by_substring(self):
        result = _sanitise_config_dict({
            "username": "bob",
            "share_password": "hunter2",
            "api_token": "abc123",
            "nested": {"credential": "xyz", "fps": 30},
        })
        assert result == {
            "username": "bob",
            "share_password": "***",
            "api_token": "***",
            "nested": {"credential": "***", "fps": 30},
        }


class TestFilterPrivateKeys:
    def test_strips_underscore_prefixed_keys_recursively(self):
        result = _filter_private_keys({
            "camera": {"fps": 30, "_codec": "h264"},
            "_communication": {"ip": "10.0.0.1"},
            "name": "cam1",
        })
        assert result == {"camera": {"fps": 30}, "name": "cam1"}


class TestGenerateExperimentName:
    def test_no_metadata_falls_back_to_no_name(self):
        web = _make_web()
        assert web._generate_experiment_name() == "NO-NAME"

    def test_joins_populated_fields_in_fixed_order(self):
        web = _make_web()
        web.experiment_metadata.update({
            "experiment": "startle", "rat_id": "R42", "stage": "1", "trial": "3",
            "strain": "C57BL/6",  # excluded from the name on purpose
        })
        assert web._generate_experiment_name() == "startle-R42-1-3"

    def test_skips_empty_fields(self):
        web = _make_web()
        web.experiment_metadata.update({"experiment": "startle", "trial": "2"})
        assert web._generate_experiment_name() == "startle-2"


class TestAdminPassword:
    def test_first_check_generates_and_persists_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = _make_web()
            web._ADMIN_CREDENTIALS_FILE = os.path.join(tmpdir, "admin_credentials")

            password = web._get_or_create_admin_password()

            assert web._check_admin_password(password) is True
            assert web._check_admin_password("wrong") is False
            # Second call must reuse the persisted password, not generate a new one
            assert web._get_or_create_admin_password() == password


class TestQueueStream:
    def test_write_feeds_bytes_into_queue_in_order(self):
        q = queue.SimpleQueue()
        stream = _QueueStream(q)

        stream.write(b"hello ")
        stream.write(b"world")

        assert q.get() == b"hello "
        assert q.get() == b"world"

    def test_writable_but_not_seekable_or_readable(self):
        stream = _QueueStream(queue.SimpleQueue())
        assert stream.writable() is True
        assert stream.seekable() is False
        assert stream.readable() is False


# ---------------------------------------------------------------------------
# Tier 2: plain HTTP routes, no facade required
# ---------------------------------------------------------------------------

class TestServeReactApp:
    def _client_with_static(self, tmpdir):
        web = _make_web()
        web.app.static_folder = tmpdir
        with open(os.path.join(tmpdir, "index.html"), "w") as f:
            f.write("<html>spa shell</html>")
        with open(os.path.join(tmpdir, "asset.js"), "w") as f:
            f.write("console.log('hi')")
        return web.app.test_client()

    def test_root_serves_index_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_static(tmpdir).get("/")
            try:
                assert resp.status_code == 200
                assert b"spa shell" in resp.data
            finally:
                resp.close()  # release the file handle before tmpdir cleanup (Windows)

    def test_existing_file_served_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_static(tmpdir).get("/asset.js")
            try:
                assert resp.status_code == 200
                assert b"console.log" in resp.data
            finally:
                resp.close()

    def test_unknown_path_falls_back_to_index_html(self):
        """Client-side routes (e.g. /settings) aren't real files -- the SPA
        shell must be served so the frontend router can take over."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_static(tmpdir).get("/settings")
            try:
                assert resp.status_code == 200
                assert b"spa shell" in resp.data
            finally:
                resp.close()

    def test_multi_segment_client_route_falls_back_to_index_html(self):
        """Regression test for a live bug report: refreshing/deep-linking a
        multi-segment client-side route (e.g. /recording/sessions/<name>)
        404'd. Flask's own auto-registered static route already matches
        multi-segment paths (it uses the <path:...> converter), and used to
        shadow this app's catch-all entirely for any 2+-segment URL since
        the catch-all only matched a single segment -- fixed by disabling
        Flask's auto static route (static_folder=None) so this app's own
        serve() is the only route handling anything under '/'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_static(tmpdir).get("/recording/sessions/Crumb-141343")
            try:
                assert resp.status_code == 200
                assert b"spa shell" in resp.data
            finally:
                resp.close()


class TestDownloadSessionFile:
    def _web_with_session(self, tmpdir):
        session_dir = os.path.join(tmpdir, "session1")
        os.makedirs(session_dir)
        with open(os.path.join(session_dir, "data.txt"), "w") as f:
            f.write("recorded data")
        return _make_web(**{"export.mount_path": tmpdir})

    def _client_with_session(self, tmpdir):
        return self._web_with_session(tmpdir).app.test_client()

    def test_missing_token_is_unauthorized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_session(tmpdir).get(
                "/api/sessions/session1/download/data.txt"
            )
            assert resp.status_code == 401

    def test_invalid_token_is_unauthorized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_session(tmpdir).get(
                "/api/sessions/session1/download/data.txt?token=not-a-real-token"
            )
            assert resp.status_code == 401

    def test_rejects_invalid_session_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = self._web_with_session(tmpdir)
            resp = web.app.test_client().get(
                f"/api/sessions/bad!name/download/data.txt{_download_qs(web)}"
            )
            assert resp.status_code == 400

    def test_downloads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = self._web_with_session(tmpdir)
            resp = web.app.test_client().get(
                f"/api/sessions/session1/download/data.txt{_download_qs(web)}"
            )
            try:
                assert resp.status_code == 200
                assert resp.data == b"recorded data"
            finally:
                resp.close()  # release the file handle before tmpdir cleanup (Windows)

    def test_missing_file_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = self._web_with_session(tmpdir)
            resp = web.app.test_client().get(
                f"/api/sessions/session1/download/missing.txt{_download_qs(web)}"
            )
            assert resp.status_code == 404

    def test_path_traversal_outside_session_dir_is_forbidden(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "outside.txt"), "w") as f:
                f.write("should not be reachable")
            web = self._web_with_session(tmpdir)
            resp = web.app.test_client().get(
                f"/api/sessions/session1/download/../outside.txt{_download_qs(web)}"
            )
            assert resp.status_code == 403

    def test_folder_path_returns_zip_of_that_subtree_only(self):
        """The FileTree browser lets an operator click a folder (e.g. a
        per-module folder) -- hitting the same download URL for a directory
        instead of a file must zip just that subtree, not the whole
        session, and not silently 404 like a bare-file lookup would."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = os.path.join(tmpdir, "session1")
            os.makedirs(os.path.join(session_dir, "20260821", "camera_a"))
            os.makedirs(os.path.join(session_dir, "20260821", "camera_b"))
            with open(os.path.join(session_dir, "20260821", "camera_a", "rec.ts"), "w") as f:
                f.write("cam a data")
            with open(os.path.join(session_dir, "20260821", "camera_b", "rec.ts"), "w") as f:
                f.write("cam b data")

            web = _make_web(**{"export.mount_path": tmpdir})
            resp = web.app.test_client().get(
                f"/api/sessions/session1/download/20260821/camera_a{_download_qs(web)}"
            )

            assert resp.status_code == 200
            assert resp.mimetype == "application/zip"
            assert resp.headers["Content-Disposition"] == \
                'attachment; filename="session1-20260821-camera_a.zip"'
            with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
                assert zf.namelist() == ["rec.ts"]
                assert zf.read("rec.ts") == b"cam a data"


class TestDownloadSessionZip:
    def test_missing_token_is_unauthorized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = _make_web(**{"export.mount_path": tmpdir})
            resp = web.app.test_client().get("/api/sessions/session1/download")
            assert resp.status_code == 401

    def test_rejects_invalid_session_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = _make_web(**{"export.mount_path": tmpdir})
            url = f"/api/sessions/bad!name/download{_download_qs(web)}"
            resp = web.app.test_client().get(url)
            assert resp.status_code == 400

    def test_missing_session_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = _make_web(**{"export.mount_path": tmpdir})
            url = f"/api/sessions/does_not_exist/download{_download_qs(web)}"
            resp = web.app.test_client().get(url)
            assert resp.status_code == 404

    def test_zips_session_directory_contents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = os.path.join(tmpdir, "session1")
            os.makedirs(session_dir)
            with open(os.path.join(session_dir, "data.txt"), "w") as f:
                f.write("recorded data")

            web = _make_web(**{"export.mount_path": tmpdir})
            url = f"/api/sessions/session1/download{_download_qs(web)}"
            resp = web.app.test_client().get(url)

            assert resp.status_code == 200
            assert resp.mimetype == "application/zip"
            with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
                assert zf.namelist() == ["data.txt"]
                assert zf.read("data.txt") == b"recorded data"


class TestFacadeRestRoutes:
    """GET routes under _register_rest_facade_routes -- for external
    scripts (e.g. a Matlab experiment controller), not the browser
    frontend, so they check an Authorization: Bearer <password> header
    rather than a Socket.IO login (see _check_bearer_auth)."""

    def _web_with_password(self, tmpdir):
        web = _make_web()
        web.facade = MagicMock()
        web._ADMIN_CREDENTIALS_FILE = os.path.join(tmpdir, "admin_credentials")
        password = web._get_or_create_admin_password()
        return web, password

    def test_list_modules_blocked_without_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _ = self._web_with_password(tmpdir)
            resp = web.app.test_client().get("/facade/list_modules")
            assert resp.status_code == 401

    def test_list_modules_blocked_with_wrong_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _ = self._web_with_password(tmpdir)
            resp = web.app.test_client().get(
                "/facade/list_modules", headers={"Authorization": "Bearer wrong"}
            )
            assert resp.status_code == 401

    def test_list_modules_succeeds_with_correct_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, password = self._web_with_password(tmpdir)
            web.facade.get_modules.return_value = {"cam1": {"type": "camera"}}
            resp = web.app.test_client().get(
                "/facade/list_modules", headers={"Authorization": f"Bearer {password}"}
            )
            assert resp.status_code == 200
            assert resp.get_json() == {"modules": {"cam1": {"type": "camera"}}}

    def test_module_health_blocked_without_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _ = self._web_with_password(tmpdir)
            resp = web.app.test_client().get("/facade/module_health")
            assert resp.status_code == 401

    def test_module_health_succeeds_with_correct_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, password = self._web_with_password(tmpdir)
            web.facade.get_module_health.return_value = {"cam1": "ok"}
            resp = web.app.test_client().get(
                "/facade/module_health", headers={"Authorization": f"Bearer {password}"}
            )
            assert resp.status_code == 200
            assert resp.get_json() == {"cam1": "ok"}

    def test_exported_recordings_blocked_without_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _ = self._web_with_password(tmpdir)
            resp = web.app.test_client().get("/facade/exported_recordings")
            assert resp.status_code == 401

    def test_exported_recordings_succeeds_with_correct_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, password = self._web_with_password(tmpdir)
            web.get_exported_recordings = MagicMock(return_value=["rec1.mp4"])
            headers = {"Authorization": f"Bearer {password}"}
            resp = web.app.test_client().get(
                "/facade/exported_recordings", headers=headers
            )
            assert resp.status_code == 200
            assert resp.get_json() == {"exported_recordings": ["rec1.mp4"]}


# ---------------------------------------------------------------------------
# Tier 3: Socket.IO event handlers -- self.facade mocked, no real modules
# ---------------------------------------------------------------------------

def _make_web_with_facade(**config_overrides):
    """A Web instance with a MagicMock facade wired up. get_modules() must
    return a real dict (not a bare MagicMock) since handle_connect iterates
    it as soon as a client connects."""
    web = _make_web(**config_overrides)
    facade = MagicMock()
    facade.get_modules.return_value = {}
    web.facade = facade
    return web, facade


def _connected_client(web):
    """Connect a Socket.IO test client and drain the connect-time emits
    (client_ip, modules_update, experiment_name_update) so each test only
    sees events produced by the action it's actually exercising."""
    client = web.socketio.test_client(web.app)
    client.get_received()
    return client


def _login(web, client, tmpdir) -> str:
    """Log the test client in and return the admin password used, so tests
    can also drive the authenticated branch of a handler."""
    web._ADMIN_CREDENTIALS_FILE = os.path.join(tmpdir, "admin_credentials")
    password = web._get_or_create_admin_password()
    client.emit("login", {"password": password})
    client.get_received()
    return password


class TestConnectHandler:
    """The 'connect' handler's own emits -- covers the event-name/payload-
    shape mismatch fixed 2026-08-26: it previously emitted a singular
    'module_update' event wrapped as {"modules": ...}, which no frontend
    code has ever listened for (useModules.js listens for plural
    'modules_update' with the raw dict), so a reconnecting client never got
    a proactive module/readiness refresh -- it silently depended on some
    later real state change to trigger a fresh broadcast."""

    def test_connect_emits_modules_update_with_current_modules(self):
        web, facade = _make_web_with_facade()
        facade.get_modules.return_value = {"cam1": {"type": "camera"}}

        client = web.socketio.test_client(web.app)
        received = client.get_received()

        modules_events = [e for e in received if e["name"] == "modules_update"]
        assert len(modules_events) == 1
        assert modules_events[0]["args"][0] == {"cam1": {"type": "camera"}}


class TestReadOnlySocketIOHandlers:
    """Handlers that only read facade state -- no auth required."""

    def test_get_system_state_emits_facade_state(self):
        web, facade = _make_web_with_facade()
        facade.get_system_state.return_value = {"recording": True, "sessions": 1}
        client = _connected_client(web)

        client.emit("get_system_state")

        received = client.get_received()
        assert received[0]["name"] == "system_state"
        assert received[0]["args"][0] == {"recording": True, "sessions": 1}

    def test_get_modules_emits_modules_update(self):
        web, facade = _make_web_with_facade()
        facade.get_modules.return_value = {"cam1": {"type": "camera"}}
        client = _connected_client(web)

        client.emit("get_modules")

        received = client.get_received()
        assert received[0]["name"] == "modules_update"
        assert received[0]["args"][0] == {"cam1": {"type": "camera"}}

    def test_get_module_health_emits_health_update(self):
        web, facade = _make_web_with_facade()
        facade.get_module_health.return_value = {"cam1": "ok"}
        client = _connected_client(web)

        client.emit("get_module_health")

        received = client.get_received()
        assert received[0]["name"] == "module_health_update"
        assert received[0]["args"][0] == {"module_health": {"cam1": "ok"}}

    def test_get_recording_sessions_emits_serialized_dataclasses(self):
        web, facade = _make_web_with_facade()
        facade.get_recording_sessions.return_value = {
            "sess1": RecordingSession(session_name="sess1", target="cam1"),
        }
        client = _connected_client(web)

        client.emit("get_recording_sessions")

        received = client.get_received()
        assert received[0]["name"] == "recording_sessions"
        assert received[0]["args"][0]["sess1"]["session_name"] == "sess1"
        assert received[0]["args"][0]["sess1"]["target"] == "cam1"

    def test_get_controller_config_emits_config(self):
        web, facade = _make_web_with_facade()
        facade.get_config.return_value = {"interface": {"web_interface_port": 5000}}
        client = _connected_client(web)

        client.emit("get_controller_config")

        received = client.get_received()
        assert received[0]["name"] == "controller_config_response"
        assert received[0]["args"][0]["config"] == {
            "interface": {"web_interface_port": 5000}
        }


class TestAuthGatedHandlers:
    """Mutating handlers must no-op (and tell the client) without a prior
    successful 'login' on the same connection."""

    def test_save_controller_config_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("save_controller_config", {"config": {"a": 1}})

        facade.set_config.assert_not_called()
        assert client.get_received()[0]["name"] == "auth_required"

    def test_save_controller_config_allowed_after_login_strips_private_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_config.return_value = {}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_controller_config", {
                "config": {"name": "hab1", "_internal": "should be dropped"}
            })

            facade.set_config.assert_called_once_with({"name": "hab1"})

    def test_save_controller_config_auto_syncs_export_when_share_changed(self):
        """export.sync_all_modules defaults True -- a changed share config
        should push out to every connected module without a separate manual
        'Sync to All Modules' click."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_config.side_effect = [
                {"export": {"share_ip": "10.0.0.1"}},
                {"export": {"share_ip": "10.0.0.2"}},
            ]
            facade.get_export_credentials.return_value = {"share_ip": "10.0.0.2"}
            facade.get_modules.return_value = {"cam1": {}, "cam2": {}}
            facade.sync_export_with_creds.return_value = {"success": True}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_controller_config", {
                "config": {"export": {"share_ip": "10.0.0.2"}}
            })

            assert facade.sync_export_with_creds.call_args_list == [
                call("cam1", {"share_ip": "10.0.0.2"}),
                call("cam2", {"share_ip": "10.0.0.2"}),
            ]

    def test_save_controller_config_no_sync_when_export_unchanged(self):
        """Saving an unrelated section (no share_ip/path/username/password
        change) should not re-push credentials to every module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_config.side_effect = [
                {"export": {"share_ip": "10.0.0.1"}, "name": "old"},
                {"export": {"share_ip": "10.0.0.1"}, "name": "new"},
            ]
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_controller_config", {
                "config": {"export": {"share_ip": "10.0.0.1"}, "name": "new"}
            })

            facade.sync_export_with_creds.assert_not_called()

    def test_save_controller_config_no_auto_sync_when_disabled(self):
        """export.sync_all_modules: false opts out of the auto-push -- the
        manual 'Sync to All Modules' button still works either way, it just
        isn't exercised by this handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade(**{"export.sync_all_modules": False})
            facade.get_config.side_effect = [
                {"export": {"share_ip": "10.0.0.1"}},
                {"export": {"share_ip": "10.0.0.2"}},
            ]
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_controller_config", {
                "config": {"export": {"share_ip": "10.0.0.2"}}
            })

            facade.sync_export_with_creds.assert_not_called()

    def test_save_controller_config_mounts_locally_even_when_module_sync_disabled(self):
        """The controller's own file-browser mount (ensure_export_share_mounted)
        is independent of export.sync_all_modules -- that flag only controls
        whether *other modules* get auto-pushed the new credentials, not
        whether the controller itself can browse/download from the share."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade(**{"export.sync_all_modules": False})
            web.ensure_export_share_mounted = MagicMock(return_value=True)
            facade.get_config.side_effect = [
                {"export": {"share_ip": "10.0.0.1"}},
                {"export": {"share_ip": "10.0.0.2"}},
            ]
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_controller_config", {
                "config": {"export": {"share_ip": "10.0.0.2"}}
            })

            web.ensure_export_share_mounted.assert_called_once()

    def test_start_recording_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("start_recording", {
            "target": "all", "session_name": "s1", "duration": None
        })

        facade.start_recording.assert_not_called()

    def test_start_recording_allowed_after_login(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("start_recording", {
                "target": "all", "session_name": "s1", "duration": 30
            })

            facade.start_recording.assert_called_once_with("all", "s1", 30)

    def test_remove_module_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("remove_module", {"id": "cam1"})

        facade.remove_module.assert_not_called()

    def test_remove_module_allowed_after_login(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("remove_module", {"id": "cam1"})

            facade.remove_module.assert_called_once_with("cam1")

    def test_get_controller_samba_info_blocked_without_login(self):
        """Carries a plaintext Samba password -- found missing _require_auth
        entirely while adding get_export_destination alongside it."""
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("get_controller_samba_info")

        facade.get_controller_own_share_info.assert_not_called()
        assert client.get_received()[0]["name"] == "auth_required"

    def test_get_controller_samba_info_allowed_after_login(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_controller_own_share_info.return_value = {"share_ip": "10.0.0.1"}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("get_controller_samba_info")

            received = client.get_received()
            assert received[0]["name"] == "controller_samba_info_response"
            assert received[0]["args"][0] == {"share_ip": "10.0.0.1"}

    def test_get_export_destination_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("get_export_destination")

        facade.get_export_credentials.assert_not_called()
        assert client.get_received()[0]["name"] == "auth_required"

    def test_get_export_destination_uses_export_credentials_not_controller_preset(self):
        """The whole point of this handler existing separately from
        get_controller_samba_info -- must call get_export_credentials()
        (respects an external-NAS export.share_ip override), not
        get_controller_own_share_info() (always the controller's own
        address, "ignoring any NAS override" per its own docstring)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_export_credentials.return_value = {"share_ip": "192.168.1.2", "share_path": "nas_share"}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("get_export_destination")

            received = client.get_received()
            assert received[0]["name"] == "export_destination_response"
            assert received[0]["args"][0] == {"share_ip": "192.168.1.2", "share_path": "nas_share"}
            facade.get_controller_own_share_info.assert_not_called()

    def test_update_pending_session_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("update_pending_session", {
            "session_name": "exp1", "new_session_name": "exp1_fixed", "duration_minutes": 30
        })

        facade.update_pending_session.assert_not_called()
        assert client.get_received()[0]["name"] == "session_error"

    def test_update_pending_session_allowed_after_login(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.update_pending_session.return_value = {
                "success": True, "session_name": "exp1_fixed"
            }
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("update_pending_session", {
                "session_name": "exp1", "new_session_name": "exp1_fixed", "duration_minutes": 30
            })

            facade.update_pending_session.assert_called_once_with("exp1", "exp1_fixed", 30)
            received = client.get_received()
            assert received[-1]["name"] == "update_pending_session_result"
            assert received[-1]["args"][0] == {"success": True, "session_name": "exp1_fixed"}

    def test_update_pending_session_error_surfaces_session_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.update_pending_session.return_value = {
                "success": False, "error": "Session is not pending (state: active)"
            }
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("update_pending_session", {
                "session_name": "exp1", "new_session_name": "exp1_fixed", "duration_minutes": 30
            })

            received = client.get_received()
            assert received[-1]["name"] == "session_error"
            assert received[-1]["args"][0] == {"error": "Session is not pending (state: active)"}

    def test_request_download_token_blocked_without_login(self):
        web, _ = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("request_download_token")

        assert client.get_received()[0]["name"] == "auth_required"
        assert web._download_tokens == {}

    def test_request_download_token_allowed_after_login_and_is_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _ = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("request_download_token")

            received = client.get_received()
            assert received[-1]["name"] == "download_token"
            token = received[-1]["args"][0]["token"]
            assert web._check_download_token(token) is True

    def test_get_bug_report_blocked_without_login(self):
        web, _ = _make_web_with_facade()
        web._collect_bug_report = MagicMock()
        client = _connected_client(web)

        client.emit("get_bug_report")

        assert client.get_received()[0]["name"] == "auth_required"
        web._collect_bug_report.assert_not_called()

    def test_get_bug_report_allowed_after_login_passes_requester_sid(self):
        """The background thread must be handed the requesting connection's
        sid -- bug_report_status/bug_report_ready scope their emits to it
        (room=requester_sid) rather than broadcasting the diagnostics
        download link to every connected guest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _ = _make_web_with_facade()
            web._collect_bug_report = MagicMock()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("get_bug_report")

            web._collect_bug_report.assert_called_once()
            (called_sid,), _ = web._collect_bug_report.call_args
            assert called_sid  # a real sid was captured, not None/empty


class TestCheckReady:
    """validate_readiness makes each module mount+write+unmount against the
    shared export share (module.py's _check_export()) -- firing that at every
    module within the same instant is a thundering herd against the NAS's SMB
    server, confirmed live 2026-08-24 on a 20-module habitat deployment where
    most of the fleet failed readiness with a mix of I/O error / device busy /
    no-such-file even though the share itself was healthy throughout."""

    def test_readiness_dispatch_is_staggered_across_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_modules_by_target.return_value = {
                "cam1": {}, "cam2": {}, "cam3": {},
            }
            facade.check_ptp_sync.return_value = {}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            with patch.object(web.socketio, "sleep") as mock_sleep:
                client.emit("check_ready", {"target": "all"})

            # get_health is a cheap in-memory read -- fired at every module
            # immediately, no staggering needed.
            get_health_calls = [
                c for c in facade.send_command.call_args_list if c.args[1] == "get_health"
            ]
            assert len(get_health_calls) == 3

            # validate_readiness is the one that touches the shared export
            # share -- one call per module, with a stagger sleep before every
            # send after the first, plus the existing trailing 0.75s PTP wait.
            readiness_calls = [
                c for c in facade.send_command.call_args_list
                if c.args[1] == "validate_readiness"
            ]
            assert [c.args[0] for c in readiness_calls] == ["cam1", "cam2", "cam3"]
            assert mock_sleep.call_args_list == [call(0.3), call(0.3), call(0.75)]

    def test_single_module_target_has_no_stagger_delay(self):
        """Only one module -- nothing to stagger against, so the only sleep
        should be the existing trailing 0.75s PTP wait."""
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_modules_by_target.return_value = {"cam1": {}}
            facade.check_ptp_sync.return_value = {}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            with patch.object(web.socketio, "sleep") as mock_sleep:
                client.emit("check_ready", {"target": "cam1"})

            assert mock_sleep.call_args_list == [call(0.75)]


class TestSaveModuleConfig:
    """save_module_config: blocked while the target module is recording,
    and propagates the transmitter's fps/sensor_mode_index to its FrameSync
    clients when saving the currently-elected transmitter's own config."""

    def test_blocked_while_module_is_recording(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.is_module_recording.return_value = True
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_module_config", {
                "id": "camera_a", "config": {"camera": {"framesync_enabled": True}}
            })

            facade.set_target_module_config.assert_not_called()
            facade.send_command.assert_not_called()
            received = client.get_received()
            assert received[-1]["name"] == "module_config_error"
            assert received[-1]["args"][0]["module_id"] == "camera_a"

    def test_allowed_when_not_recording(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.is_module_recording.return_value = False
            facade.get_module_configs.return_value = {}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_module_config", {
                "id": "camera_a", "config": {"camera": {"framesync_enabled": True}}
            })

            facade.set_target_module_config.assert_called_once_with(
                "camera_a", {"camera": {"framesync_enabled": True}}
            )
            facade.send_command.assert_called_once_with(
                "camera_a", "set_config", {"camera": {"framesync_enabled": True}}
            )

    def test_saving_current_transmitter_propagates_fps_to_clients(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.is_module_recording.return_value = False
            facade.get_module_configs.return_value = {
                "camera_a": {"true_config": {"camera": {"sync_mode": "server", "fps": 30}}},
                "camera_b": {"true_config": {"camera": {"sync_mode": "client", "fps": 30}}},
                "camera_c": {"true_config": {"camera": {"sync_mode": "none", "fps": 30}}},
            }
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_module_config", {
                "id": "camera_a", "config": {"camera": {"fps": 60, "sensor_mode_index": 3}}
            })

            propagate_calls = [
                c for c in facade.send_command.call_args_list if c.args[0] == "camera_b"
            ]
            assert len(propagate_calls) == 1
            pushed_config = propagate_calls[0].args[2]
            assert pushed_config["camera"]["fps"] == 60
            assert pushed_config["camera"]["sensor_mode_index"] == 3
            # camera_c is not a client -- must not receive a propagated push
            assert not any(c.args[0] == "camera_c" for c in facade.send_command.call_args_list)

    def test_saving_a_non_transmitter_does_not_propagate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.is_module_recording.return_value = False
            facade.get_module_configs.return_value = {
                "camera_a": {"true_config": {"camera": {"sync_mode": "client", "fps": 30}}},
                "camera_b": {"true_config": {"camera": {"sync_mode": "client", "fps": 30}}},
            }
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("save_module_config", {
                "id": "camera_a", "config": {"camera": {"fps": 60}}
            })

            assert not any(c.args[0] == "camera_b" for c in facade.send_command.call_args_list)


class TestSendCommandDispatch:
    """The generic 'send_command' event -- routes to one module or broadcasts
    to every connected module when module_id == 'all'."""

    def test_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("send_command", {
            "type": "get_health", "module_id": "cam1", "params": {}
        })

        facade.send_command.assert_not_called()

    def test_broadcasts_to_every_module_when_target_is_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_modules.return_value = {"cam1": {}, "cam2": {}}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("send_command", {
                "type": "get_health", "module_id": "all", "params": {}
            })

            called_module_ids = {c.args[0] for c in facade.send_command.call_args_list}
            assert called_module_ids == {"cam1", "cam2"}

    def test_sends_to_single_module_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_modules.return_value = {"cam1": {}, "cam2": {}}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("send_command", {
                "type": "get_health", "module_id": "cam1", "params": {}
            })

            facade.send_command.assert_called_once_with("cam1", "get_health", {})

    def test_start_recording_command_appends_timestamp_to_experiment_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("send_command", {
                "type": "start_recording", "module_id": "cam1",
                "params": {"experiment_name": "exp"},
            })

            _, _, params = facade.send_command.call_args[0]
            assert re.fullmatch(r"exp-\d{8}_\d{6}", params["experiment_name"])


class TestLogin:
    def test_correct_password_authenticates_and_acks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            web._ADMIN_CREDENTIALS_FILE = os.path.join(tmpdir, "admin_credentials")
            password = web._get_or_create_admin_password()
            client = _connected_client(web)

            client.emit("login", {"password": password})

            assert client.get_received()[0]["name"] == "login_success"

    def test_wrong_password_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            web._ADMIN_CREDENTIALS_FILE = os.path.join(tmpdir, "admin_credentials")
            web._get_or_create_admin_password()
            client = _connected_client(web)

            client.emit("login", {"password": "wrong"})

            assert client.get_received()[0]["name"] == "login_error"


class TestChangeAdminPassword:
    """Requires both an authenticated connection AND re-proof of the current
    password -- a session left logged in on a shared screen shouldn't be
    enough on its own to lock everyone else out."""

    def test_blocked_without_login(self):
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("change_admin_password", {
            "current_password": "x", "new_password": "newpassword123",
        })

        assert client.get_received()[0]["name"] == "change_password_error"

    def test_rejects_incorrect_current_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("change_admin_password", {
                "current_password": "wrong", "new_password": "newpassword123",
            })

            received = client.get_received()
            assert received[0]["name"] == "change_password_error"
            assert "incorrect" in received[0]["args"][0]["error"]

    def test_rejects_short_new_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            current = _login(web, client, tmpdir)

            client.emit("change_admin_password", {
                "current_password": current, "new_password": "short",
            })

            received = client.get_received()
            assert received[0]["name"] == "change_password_error"
            assert "8 characters" in received[0]["args"][0]["error"]

    def test_success_persists_new_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            current = _login(web, client, tmpdir)

            client.emit("change_admin_password", {
                "current_password": current, "new_password": "brandnewpw",
            })

            received = client.get_received()
            assert received[0]["name"] == "change_password_success"
            assert web._check_admin_password("brandnewpw") is True
            assert web._check_admin_password(current) is False


# ---------------------------------------------------------------------------
# Tier 4: system-mutating code -- subprocess/threading mocked, nothing real
# ever runs. These assert "the right command was built and issued", not
# real mount/reboot/deploy behaviour, which can't be unit-tested without
# actual hardware.
# ---------------------------------------------------------------------------

class TestCheckNasFreeSpace:
    def test_no_share_ip_configured_skips_check_entirely(self):
        """No mocking needed -- an unconfigured NAS returns None before
        touching subprocess or the filesystem at all."""
        web, _facade = _make_web_with_facade()
        assert web._check_nas_free_space() is None

    def test_mount_permission_denied_returns_friendly_error(self):
        web, _facade = _make_web_with_facade(**{
            "export.share_ip": "10.0.0.50",
            "export.share_path": "controller_share",
            "export.share_username": "pi",
            "export.share_password": "wrongpw",
        })
        mount_result = MagicMock(
            returncode=1, stderr="mount error(13): Permission denied"
        )

        with patch("src.controller.web.subprocess.run", return_value=mount_result), \
             patch("src.controller.web.Path.mkdir"), \
             patch("src.controller.web.Path.is_mount", return_value=False):
            error = web._check_nas_free_space()

        assert error is not None
        assert "rejected the credentials" in error


class TestEnsureExportShareMounted:
    """Keeps export.mount_path in sync with wherever export.share_ip
    currently points, so the session-detail file browser/downloads see the
    same files modules exported to a remote NAS rather than an empty local
    directory. subprocess/Path mocked throughout -- these assert the right
    commands are built, not real mount behaviour."""

    @staticmethod
    def _run_side_effect(own_ip, mount_returncode=0, mount_stderr=""):
        def _run(cmd, **kwargs):
            if cmd[0] == "nmcli":
                return MagicMock(returncode=0, stdout=f"{own_ip}/24\n")
            if cmd[:2] == ["sudo", "umount"]:
                return MagicMock(returncode=0, stderr="")
            if cmd[:2] == ["sudo", "mount"]:
                return MagicMock(returncode=mount_returncode, stderr=mount_stderr)
            raise AssertionError(f"unexpected subprocess call: {cmd}")
        return _run

    def test_no_share_ip_configured_unmounts_stale_mount_and_returns_true(self):
        """A previous session may have left an external-NAS mount in place;
        clearing export.share_ip back to 'use the controller itself' must
        unmount it so export.mount_path reverts to the plain local
        directory backing the controller's own Samba share."""
        web, _facade = _make_web_with_facade()
        run = MagicMock(side_effect=self._run_side_effect(own_ip="10.0.0.1"))

        with patch("src.controller.web.subprocess.run", run), \
             patch("src.controller.web.Path.is_mount", return_value=True), \
             patch("src.controller.web.Path.mkdir"):
            assert web.ensure_export_share_mounted() is True

        prefixes = [c.args[0][:2] for c in run.call_args_list]
        assert prefixes.count(["sudo", "umount"]) == 1
        assert not any(c.args[0][:2] == ["sudo", "mount"] for c in run.call_args_list)

    def test_share_ip_matching_own_address_treated_as_local(self):
        """export.share_ip happening to equal the controller's own address
        must never CIFS-mount the controller's own share directory onto
        itself -- that would break the controller's own Samba server, not
        just be redundant."""
        web, _facade = _make_web_with_facade(**{"export.share_ip": "10.0.0.1"})
        run = MagicMock(side_effect=self._run_side_effect(own_ip="10.0.0.1"))

        with patch("src.controller.web.subprocess.run", run), \
             patch("src.controller.web.Path.is_mount", return_value=False), \
             patch("src.controller.web.Path.mkdir"):
            assert web.ensure_export_share_mounted() is True

        assert not any(c.args[0][:2] == ["sudo", "mount"] for c in run.call_args_list)

    def test_remote_share_mounts_with_configured_credentials(self):
        web, _facade = _make_web_with_facade(**{
            "export.share_ip":       "192.168.1.2",
            "export.share_path":     "habitat_recording",
            "export.share_username": "saviour_module",
            "export.share_password": "hunter2",
            "export.mount_path":     "/home/pi/controller_share",
        })
        run = MagicMock(side_effect=self._run_side_effect(own_ip="10.0.0.1"))

        with patch("src.controller.web.subprocess.run", run), \
             patch("src.controller.web.Path.is_mount", return_value=False), \
             patch("src.controller.web.Path.mkdir"):
            assert web.ensure_export_share_mounted() is True

        mount_calls = [
            c.args[0] for c in run.call_args_list if c.args[0][:2] == ["sudo", "mount"]
        ]
        assert len(mount_calls) == 1
        cmd = mount_calls[0]
        assert cmd[4] == "//192.168.1.2/habitat_recording"
        assert cmd[5] == "/home/pi/controller_share"
        assert "username=saviour_module,password=hunter2" in cmd[7]

    def test_mount_failure_returns_false_without_raising(self):
        web, _facade = _make_web_with_facade(**{"export.share_ip": "192.168.1.2"})
        run = MagicMock(side_effect=self._run_side_effect(
            own_ip="10.0.0.1", mount_returncode=1,
            mount_stderr="mount error(13): Permission denied",
        ))

        with patch("src.controller.web.subprocess.run", run), \
             patch("src.controller.web.Path.is_mount", return_value=False), \
             patch("src.controller.web.Path.mkdir"):
            assert web.ensure_export_share_mounted() is False


class TestUpdateDeployHandlersAuthGate:
    """The whole update/deploy family (deploy_update, deploy_update_to_controller,
    stage_current_version, git_pull_update, upload_update_start/chunk) must reject
    an unauthenticated connection via the generic "auth_required" event, same as
    every other mutating handler -- see TestAuthGatedHandlers. These previously
    emitted handler-specific event names (deploy_update_error/upload_update_error)
    that AuthGate.jsx's generic re-login listener doesn't recognise, so a lapsed
    session (e.g. after a backend restart) looked like a silent, unrecoverable
    failure instead of reopening the login form."""

    def test_deploy_update_blocked_without_login(self):
        # Note: get_modules() is already called once by the "connect" handler
        # (broadcasts the initial module list), so only the *emitted event*
        # -- not call counts -- distinguishes a blocked vs. a processed command.
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)
        client.get_received()  # drain the connect-time modules_update

        client.emit("deploy_update")

        assert client.get_received()[0]["name"] == "auth_required"

    def test_deploy_update_to_controller_blocked_without_login(self):
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)

        with patch("src.controller.web.os.path.exists") as exists:
            client.emit("deploy_update_to_controller")
            exists.assert_not_called()

        assert client.get_received()[0]["name"] == "auth_required"

    def test_stage_current_version_blocked_without_login(self):
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("stage_current_version")

        assert client.get_received()[0]["name"] == "auth_required"

    def test_git_pull_update_blocked_without_login(self):
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("git_pull_update")

        assert client.get_received()[0]["name"] == "auth_required"

    def test_upload_update_start_blocked_without_login(self):
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("upload_update_start", {"filename": "x.zip"})

        assert client.get_received()[0]["name"] == "auth_required"

    def test_upload_update_chunk_blocked_without_login(self):
        web, _facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("upload_update_chunk", {"index": 0, "data": b""})

        assert client.get_received()[0]["name"] == "auth_required"


class TestDeployUpdateToModule:
    """Forwards an 'update_saviour' command to one module -- doesn't touch
    subprocess itself, just os.path.exists() on the staged package."""

    def test_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("deploy_update_to_module", {"module_id": "cam1"})

        facade.send_command.assert_not_called()
        # Must use the generic "auth_required" event -- it's the only one
        # AuthGate.jsx listens for to reopen the login modal. A stray
        # handler-specific event name here leaves the user stuck looking
        # logged-in with no way to actually re-authenticate.
        assert client.get_received()[0]["name"] == "auth_required"

    def test_missing_module_id_emits_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            client.emit("deploy_update_to_module", {})

            received = client.get_received()
            assert received[0]["name"] == "deploy_update_error"
            assert "module_id" in received[0]["args"][0]["error"]

    def test_no_update_staged_emits_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            with patch("src.controller.web.os.path.exists", return_value=False):
                client.emit("deploy_update_to_module", {"module_id": "cam1"})

            received = client.get_received()
            assert received[0]["name"] == "deploy_update_error"
            assert "No update staged" in received[0]["args"][0]["error"]

    def test_success_forwards_command_with_controller_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.controller.network.ip = "10.0.0.5"
            client = _connected_client(web)
            _login(web, client, tmpdir)

            with patch("src.controller.web.os.path.exists", return_value=True):
                client.emit("deploy_update_to_module", {"module_id": "cam1"})

            facade.send_command.assert_called_once_with(
                "cam1", "update_saviour", {"controller_url": "http://10.0.0.5:5000"}
            )


class TestDestructiveSystemActions:
    """shutdown/reboot handlers ack immediately, then do the actual
    subprocess.Popen call from a background thread after a short delay. We
    capture that thread's target and invoke it ourselves (with Popen and
    time.sleep mocked) instead of really spawning a thread and waiting --
    verifies the right command gets built without ever risking a real
    shutdown/reboot in the test run."""

    def test_shutdown_saviour_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("shutdown_saviour")

        facade.send_command.assert_not_called()

    def test_shutdown_saviour_notifies_modules_then_powers_off(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, facade = _make_web_with_facade()
            facade.get_modules.return_value = {"cam1": {}, "cam2": {}}
            client = _connected_client(web)
            _login(web, client, tmpdir)

            with patch("src.controller.web.threading.Thread") as mock_thread, \
                 patch("src.controller.web.subprocess.Popen") as mock_popen, \
                 patch("src.controller.web.time.sleep"):
                client.emit("shutdown_saviour")

                sent_ids = {c.args[0] for c in facade.send_command.call_args_list}
                assert sent_ids == {"cam1", "cam2"}
                assert all(
                    c.args[1] == "shutdown" for c in facade.send_command.call_args_list
                )
                assert client.get_received()[0]["name"] == "shutdown_saviour_ack"

                target = mock_thread.call_args.kwargs["target"]
                target()
                mock_popen.assert_called_once_with(["sudo", "shutdown", "now"])

    def test_reboot_controller_acks_then_reboots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            with patch("src.controller.web.threading.Thread") as mock_thread, \
                 patch("src.controller.web.subprocess.Popen") as mock_popen, \
                 patch("src.controller.web.time.sleep"):
                client.emit("reboot_controller")

                received = client.get_received()
                assert received[0]["name"] == "controller_action_ack"
                assert received[0]["args"][0]["action"] == "reboot"

                target = mock_thread.call_args.kwargs["target"]
                target()
                mock_popen.assert_called_once_with(["sudo", "reboot"])


class TestSetControllerTime:
    """Wraps `timedatectl set-time`, temporarily disabling NTP sync first if
    it's active -- neither of which can run in this test, so subprocess.run
    is mocked to hand back canned NTP-disabled / success-or-failure results."""

    def test_success_calls_set_time_with_formatted_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            show_result = MagicMock(stdout="NTP=no\n")
            set_time_result = MagicMock(returncode=0)
            with patch("src.controller.web.subprocess.run",
                       side_effect=[show_result, set_time_result]) as mock_run:
                client.emit("set_controller_time", {"iso": "2026-08-03T12:00:00Z"})

            received = client.get_received()
            assert received[0]["name"] == "set_time_result"
            assert received[0]["args"][0]["success"] is True
            assert mock_run.call_args_list[1].args[0] == [
                "timedatectl", "set-time", "2026-08-03 12:00:00"
            ]

    def test_failure_reports_stderr_in_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web, _facade = _make_web_with_facade()
            client = _connected_client(web)
            _login(web, client, tmpdir)

            show_result = MagicMock(stdout="NTP=no\n")
            set_time_result = MagicMock(
                returncode=1, stderr="Failed to set time", stdout=""
            )
            with patch("src.controller.web.subprocess.run",
                       side_effect=[show_result, set_time_result]):
                client.emit("set_controller_time", {"iso": "2026-08-03T12:00:00Z"})

            received = client.get_received()
            assert received[0]["name"] == "set_time_result"
            assert received[0]["args"][0]["success"] is False
            assert "Failed to set time" in received[0]["args"][0]["error"]
