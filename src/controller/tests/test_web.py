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
from unittest.mock import MagicMock, patch

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


class TestDownloadSessionFile:
    def _client_with_session(self, tmpdir):
        session_dir = os.path.join(tmpdir, "session1")
        os.makedirs(session_dir)
        with open(os.path.join(session_dir, "data.txt"), "w") as f:
            f.write("recorded data")
        web = _make_web(**{"export.mount_path": tmpdir})
        return web.app.test_client()

    def test_rejects_invalid_session_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_session(tmpdir).get(
                "/api/sessions/bad!name/download/data.txt"
            )
            assert resp.status_code == 400

    def test_downloads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_session(tmpdir).get(
                "/api/sessions/session1/download/data.txt"
            )
            try:
                assert resp.status_code == 200
                assert resp.data == b"recorded data"
            finally:
                resp.close()  # release the file handle before tmpdir cleanup (Windows)

    def test_missing_file_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self._client_with_session(tmpdir).get(
                "/api/sessions/session1/download/missing.txt"
            )
            assert resp.status_code == 404

    def test_path_traversal_outside_session_dir_is_forbidden(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "outside.txt"), "w") as f:
                f.write("should not be reachable")
            resp = self._client_with_session(tmpdir).get(
                "/api/sessions/session1/download/../outside.txt"
            )
            assert resp.status_code == 403


class TestDownloadSessionZip:
    def test_rejects_invalid_session_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = _make_web(**{"export.mount_path": tmpdir})
            resp = web.app.test_client().get("/api/sessions/bad!name/download")
            assert resp.status_code == 400

    def test_missing_session_returns_404(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            web = _make_web(**{"export.mount_path": tmpdir})
            resp = web.app.test_client().get("/api/sessions/does_not_exist/download")
            assert resp.status_code == 404

    def test_zips_session_directory_contents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = os.path.join(tmpdir, "session1")
            os.makedirs(session_dir)
            with open(os.path.join(session_dir, "data.txt"), "w") as f:
                f.write("recorded data")

            web = _make_web(**{"export.mount_path": tmpdir})
            resp = web.app.test_client().get("/api/sessions/session1/download")

            assert resp.status_code == 200
            assert resp.mimetype == "application/zip"
            with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
                assert zf.namelist() == ["data.txt"]
                assert zf.read("data.txt") == b"recorded data"


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
    (client_ip, module_update, experiment_name_update) so each test only
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


class TestDeployUpdateToModule:
    """Forwards an 'update_saviour' command to one module -- doesn't touch
    subprocess itself, just os.path.exists() on the staged package."""

    def test_blocked_without_login(self):
        web, facade = _make_web_with_facade()
        client = _connected_client(web)

        client.emit("deploy_update_to_module", {"module_id": "cam1"})

        facade.send_command.assert_not_called()

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
