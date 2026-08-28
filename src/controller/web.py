#!/usr/bin/env python3
"""
Controller Web Interface

Handles user interaction with the habitat controller, including:
- Web based GUI
- Command parsing and execution
- Help system and module listing

Author: Andrew SG
Created: ?
"""


import hmac
import io
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import zipfile
from abc import ABC
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_file,
    send_from_directory,
)
from flask_socketio import SocketIO

from src.controller.config import Config
from src.shared.zip_extract import extract_preserving_permissions

_SENSITIVE_KEY_FRAGMENTS = {"password", "credential", "secret", "token"}

import queue as _queue


class _QueueStream(io.RawIOBase):
    """Write-only, non-seekable stream that feeds chunks into a SimpleQueue.
    Used to stream a ZipFile to an HTTP response without buffering in RAM
    or writing to a temp file. zipfile detects seekable()=False and switches
    to data-descriptor mode (writes CRC/sizes after data, no back-seeking).
    """
    def __init__(self, q: "_queue.SimpleQueue"):
        self._q = q
    def write(self, b: bytes) -> int:
        self._q.put(bytes(b))
        return len(b)
    def writable(self) -> bool: return True
    def seekable(self) -> bool: return False
    def readable(self) -> bool: return False

def _sanitise_config_dict(cfg: dict) -> dict:
    """Recursively redact values whose key contains a sensitive word."""
    out = {}
    for k, v in cfg.items():
        if any(s in k.lower() for s in _SENSITIVE_KEY_FRAGMENTS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _sanitise_config_dict(v)
        else:
            out[k] = v
    return out


def _journalctl(args: list, timeout: int = 20) -> str:
    """Run a journalctl query, returning stdout or a short error string.
    Never raises — a bug report should collect whatever it can."""
    try:
        r = subprocess.run(
            ["journalctl", "--no-pager", *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if r.returncode == 0:
            return r.stdout or "(no output)"
        return f"journalctl {' '.join(args)} -> rc={r.returncode}: {r.stderr.strip()}"
    except Exception as e:
        return f"journalctl {' '.join(args)} failed: {e}"


def _filter_private_keys(d: dict) -> dict:
    """Return a deep copy of *d* with all keys starting with '_' removed.

    Prevents the frontend from overwriting internal config keys such as
    _communication.*, _codec, etc. that are managed server-side only.
    """
    result = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        result[k] = _filter_private_keys(v) if isinstance(v, dict) else v
    return result


class Web(ABC):
    # Outside the JSON config files on purpose -- those are readable/mergeable
    # via the config-sync socket events, and a credential has no business
    # sitting somewhere "get_controller_config" could ever echo back.
    _ADMIN_CREDENTIALS_FILE = "/etc/saviour/admin_credentials"

    def __init__(self, config: Config):
        self.logger = logging.getLogger(__name__)
        self.config = config

        # Get the port from the config
        self.port = self.config.get("interface.web_interface_port")

        # Flask setup. static_folder=None here deliberately disables Flask's
        # own auto-registered '/<path:filename>' static route -- it and our
        # own catch-all serve() route below (also path-based, so a deep
        # client-side route like /recording/sessions/<name> matches it) tie
        # on routing weight, and Werkzeug resolves ties by registration
        # order, so Flask's rule (registered first, during this
        # constructor) always won and shadowed serve()'s index.html
        # fallback entirely -- a nonexistent static path 404'd outright
        # instead of falling through to the SPA shell. static_folder is
        # still set as a plain attribute right after construction so
        # serve() below (self.app.static_folder) keeps working exactly as
        # it already assumed.
        self.app = Flask(__name__, static_folder=None)
        self.app.static_folder = "frontend/dist"
        self.socketio = SocketIO(self.app, host="0.0.0.0", cors_allowed_origins="*", async_mode='threading')

        # Default experiment metadata
        self.experiment_metadata = {
            'experimenter': '',
            'experiment': '',
            'rat_id': '',
            'strain': '',
            'batch': '',
            'stage': '',
            'trial': ''
        }
        self.current_experiment_name = self._generate_experiment_name() # To be constructed from metadata, or overriden

        # Register routes and webhooks
        self._register_routes()
        self._register_socketio_events()

        # Store module readiness state in memory
        self.module_readiness = {}  # {module_id: {'ready': bool, 'timestamp': float, 'checks': dict, 'error': str}}

        self.rest_facade = True
        if self.rest_facade:
            self._register_rest_facade_routes()

        # NAS health state
        self._nas_health = {"status": "unknown", "error": None, "checked_at": None}
        self._nas_monitor_stop = threading.Event()

        # Running flag
        self._running = False

        # Set up paths
        self.habitat_share_dir = Path(self.config.get("export.mount_path", "/home/pi/controller_share"))

        # Upload state for chunked update package uploads
        self._upload_chunks: dict = {}
        self._upload_meta: dict = {}
        self._upload_lock = threading.Lock()

        # Bug report state
        self._diag_pending: dict = {}   # module_id → {'event': Event, 'data': None}
        self._diag_lock = threading.Lock()
        self._bug_report_store: dict = {}  # token → bytes (at most one kept)

        # Authenticated Socket.IO connections (by request.sid). Guests can
        # connect and read state; anything mutating/destructive requires the
        # connection to be in this set. Membership is per-connection, not
        # per-browser -- a reconnect must re-authenticate (the client resends
        # stored credentials via the Socket.IO auth handshake, see
        # handle_connect below).
        self._authenticated_sids: set = set()
        self._auth_lock = threading.Lock()

        # Short-lived tokens authorizing a plain browser GET (session file/
        # zip downloads) -- a Socket.IO connection being in
        # _authenticated_sids doesn't help those routes at all, since a
        # plain <a>/window.location download carries no Socket.IO session
        # and can't attach a custom Authorization header the way an
        # external script hitting /facade/send_command can. Minted only
        # over an already-authenticated socket (see request_download_token
        # below), token → expiry epoch.
        self._download_tokens: dict = {}
        self._download_token_lock = threading.Lock()


    def _generate_experiment_name(self) -> str:
        """Generate experiment name from metadata, skipping empty fields."""
        md = self.experiment_metadata
        parts = []

        # Iterate through metadata keys in desired order
        # strain and batch are omitted from the path (they are still saved in session_metadata.json)
        for key in ['experiment', 'rat_id', 'stage', 'trial']:
            value = str(md.get(key, "")).strip()
            if value:  # Only append non-empty strings
                parts.append(value)

        # Join non-empty parts with underscores
        name = "-".join(parts)

        if name == "":
            name = "NO-NAME"

        return name


    def _write_admin_password(self, password: str) -> None:
        """Write the admin password to disk, mode 600."""
        os.makedirs(os.path.dirname(self._ADMIN_CREDENTIALS_FILE), exist_ok=True)
        fd = os.open(self._ADMIN_CREDENTIALS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password)


    def _get_or_create_admin_password(self) -> str:
        """Return the shared admin password, generating one on first use.
        Single shared credential (no per-user accounts) -- proportionate to
        a closed single-lab network; gates mutating/destructive actions
        while read-only status stays open to any guest connection."""
        try:
            with open(self._ADMIN_CREDENTIALS_FILE) as f:
                password = f.read().strip()
                if password:
                    return password
        except FileNotFoundError:
            pass
        password = secrets.token_hex(16)
        self._write_admin_password(password)
        self.logger.warning(
            f"Generated new admin password at {self._ADMIN_CREDENTIALS_FILE} -- "
            f"required to log in and perform any mutating/destructive action. "
            f"Run `sudo cat {self._ADMIN_CREDENTIALS_FILE}` to retrieve it."
        )
        return password


    def _check_admin_password(self, password) -> bool:
        """Constant-time check of a client-supplied password against the
        admin credential."""
        expected = self._get_or_create_admin_password()
        return hmac.compare_digest(str(password or ""), expected)


    def _check_bearer_auth(self) -> bool:
        """Check the admin password against this request's `Authorization:
        Bearer <password>` header -- for the /facade/* REST routes, which
        are for external scripts (e.g. a Matlab experiment controller)
        rather than the browser frontend, so they have no Socket.IO session
        to check via _is_authenticated/_require_auth."""
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        return self._check_admin_password(token)


    def _is_authenticated(self) -> bool:
        """Whether the current Socket.IO connection (request.sid) has logged
        in. Gates every mutating/destructive handler."""
        return request.sid in self._authenticated_sids


    def _require_auth(self, error_event: str, error_payload=None) -> bool:
        """Check auth for the current connection; emit an error and return
        False if not logged in. Call at the top of every handler that
        mutates state or takes a destructive/consequential action."""
        if self._is_authenticated():
            return True
        from flask_socketio import emit as _emit
        _emit(error_event, error_payload if error_payload is not None
              else {"error": "Login required for this action"})
        return False


    def _recording_module_ids(self, candidates=None) -> list:
        """Return the subset of *candidates* (default: all known modules) whose
        status is RECORDING. Used to reject config changes that would hit a
        module mid-recording -- the same protection save_module_config already
        applies per-module, extended to the fleet-wide apply/reset paths."""
        ids = candidates if candidates is not None else list(self.facade.get_modules().keys())
        return [mid for mid in ids if self.facade.is_module_recording(mid)]

    def _reject_config_change_if_recording(self, candidates=None, module_id=None) -> bool:
        """If any target module is recording, emit module_config_error and
        return True (caller should return). Returns False when it's safe to
        proceed."""
        busy = self._recording_module_ids(candidates)
        if not busy:
            return False
        self.logger.warning(f"Rejected config change: modules recording: {busy}")
        from flask_socketio import emit as _emit
        _emit("module_config_error", {
            "module_id": module_id,
            "error": (
                "Cannot change settings while "
                + (", ".join(busy) if len(busy) <= 4 else f"{len(busy)} modules")
                + " recording — stop the session first."
            ),
        })
        return True


    _DOWNLOAD_TOKEN_TTL_SECS = 300

    def _issue_download_token(self) -> str:
        """Mint a short-lived token authorizing session-file downloads over
        plain HTTP GET. Must only be called from a handler already gated by
        _require_auth."""
        token = secrets.token_urlsafe(32)
        expires = time.time() + self._DOWNLOAD_TOKEN_TTL_SECS
        with self._download_token_lock:
            # Opportunistic cleanup so this dict doesn't grow unbounded on a
            # long-running controller -- cheap since it only iterates on
            # mint, not on every check.
            now = time.time()
            expired = [t for t, exp in self._download_tokens.items() if exp < now]
            for t in expired:
                del self._download_tokens[t]
            self._download_tokens[token] = expires
        return token


    def _check_download_token(self, token) -> bool:
        """Constant-time-ish validity check for a download token minted by
        _issue_download_token. Not consumed on use -- a single page visit
        can trigger several downloads (per-folder zips, the whole-session
        zip, individual files) within the same short window."""
        if not token:
            return False
        with self._download_token_lock:
            expires = self._download_tokens.get(token)
        return expires is not None and expires >= time.time()


    def _check_nas_free_space(self) -> "str | None":
        """Mount the NAS and check free space against nas_min_free_pct.

        Returns None if the share is reachable with sufficient space, or an
        error string for surfacing to the user.  Returns None immediately if no
        NAS IP is configured.
        """
        import shutil as _shutil
        import subprocess
        nas_ip = self.config.get("export.share_ip", "")
        if not nas_ip:
            return None
        share_path  = self.config.get("export.share_path", "controller_share")
        username    = self.config.get("export.share_username", "")
        password    = self.config.get("export.share_password", "")
        min_free_pct = self.config.get("recording.nas_min_free_pct", 5)
        mount_point = Path("/mnt/nas_probe")
        try:
            mount_point.mkdir(parents=True, exist_ok=True)
            if mount_point.is_mount():
                subprocess.run(["sudo", "umount", str(mount_point)], check=False, timeout=10)
            auth_opts = f"username={username},password={password}" if username else "guest"
            result = subprocess.run(
                ["sudo", "mount", "-t", "cifs",
                 f"//{nas_ip}/{share_path}", str(mount_point),
                 "-o", f"{auth_opts},uid=pi,gid=pi,file_mode=0664,dir_mode=0775,cache=none"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if "Permission denied" in stderr or "error(13)" in stderr:
                    return f"NAS at {nas_ip} rejected the credentials — check share username/password in Settings"
                return f"Cannot reach NAS at {nas_ip}: {stderr or 'mount failed'}"
            usage = _shutil.disk_usage(str(mount_point))
            free_pct = usage.free / usage.total * 100 if usage.total else 100
            free_gb  = usage.free / 1_073_741_824
            if free_pct < min_free_pct:
                return (
                    f"NAS has only {free_pct:.1f}% free ({free_gb:.1f} GB) — "
                    f"need at least {min_free_pct}% before starting a new session"
                )
            import uuid as _uuid
            probe = mount_point / f".saviour_probe_{_uuid.uuid4().hex}"
            try:
                probe.write_text("probe")
                probe.unlink()
            except Exception as e:
                return f"NAS at {nas_ip} is mounted but not writable: {e}"
            return None
        except subprocess.TimeoutExpired:
            return f"Timed out connecting to NAS at {nas_ip}"
        except Exception as e:
            return f"NAS check failed: {e}"
        finally:
            subprocess.run(["sudo", "umount", str(mount_point)], check=False, timeout=10)


    def _try_write_metadata(self, session_name: str, metadata: dict) -> bool:
        """Attempt one write of session_metadata.json.  Returns True on success."""
        import subprocess

        nas_ip = self.config.get("export.share_ip", "")
        if nas_ip:
            share_path = self.config.get("export.share_path", "controller_share")
            username   = self.config.get("export.share_username", "")
            password   = self.config.get("export.share_password", "")
            mount_point = Path("/mnt/controller_export")
            try:
                mount_point.mkdir(parents=True, exist_ok=True)
                if mount_point.is_mount():
                    subprocess.run(["sudo", "umount", str(mount_point)], check=False, timeout=10)
                auth_opts = f"username={username},password={password}" if username else "guest"
                result = subprocess.run(
                    ["sudo", "mount", "-t", "cifs",
                     f"//{nas_ip}/{share_path}", str(mount_point),
                     "-o", f"{auth_opts},uid=pi,gid=pi,file_mode=0664,dir_mode=0775,cache=none"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode != 0:
                    self.logger.warning(
                        f"Metadata write: cannot mount NAS {nas_ip}: "
                        f"{result.stderr.strip() or 'mount failed'}"
                    )
                    return False
                share_dir = mount_point / session_name
                share_dir.mkdir(parents=True, exist_ok=True)
                with open(share_dir / "session_metadata.json", "w") as f:
                    json.dump(metadata, f, indent=2)
                self.logger.info(f"Wrote session_metadata.json for '{session_name}' to NAS {nas_ip}")
                return True
            except Exception as e:
                self.logger.warning(f"Metadata write failed for '{session_name}': {e}")
                return False
            finally:
                subprocess.run(["sudo", "umount", str(mount_point)], check=False, timeout=10)
        else:
            share_dir = self.habitat_share_dir / session_name
            try:
                share_dir.mkdir(parents=True, exist_ok=True)
                share_dir.chmod(0o777)
                with open(share_dir / "session_metadata.json", "w") as f:
                    json.dump(metadata, f, indent=2)
                self.logger.info(f"Wrote session_metadata.json for '{session_name}'")
                return True
            except Exception as e:
                self.logger.warning(f"Metadata write failed for '{session_name}': {e}")
                return False

    def _retry_write_metadata(self, session_name: str, metadata: dict) -> None:
        """Background thread: retry session_metadata.json with exponential backoff.

        Attempts at 30 s, 1 min, 2 min, 5 min, then 10 min intervals.  Gives up
        after the final attempt and logs an error so the operator is aware.
        """
        for delay in (30, 60, 120, 300, 600):
            time.sleep(delay)
            self.logger.info(f"Retrying session_metadata.json write for '{session_name}'…")
            if self._try_write_metadata(session_name, metadata):
                return
        self.logger.error(
            f"Gave up writing session_metadata.json for '{session_name}' after all retries — "
            f"NAS may be permanently unavailable"
        )

    def _write_session_metadata(self, session_name: str, target: str) -> None:
        """Write session_metadata.json to the NAS or local share.

        If the initial attempt fails (NAS temporarily unavailable), a background
        thread retries with exponential backoff.
        """
        from datetime import datetime

        metadata = {
            "session_name": session_name,
            "created_at": datetime.now(UTC).isoformat(),
            "target": target,
            **self.experiment_metadata,
        }

        if not self._try_write_metadata(session_name, metadata):
            self.logger.warning(
                f"Initial metadata write failed for '{session_name}' — scheduling retries"
            )
            threading.Thread(
                target=self._retry_write_metadata,
                args=(session_name, metadata),
                daemon=True,
                name=f"metadata-retry-{session_name}",
            ).start()



    def register_additional_socketio_events(self, handler_func):
        """Allow extra socketio event handlers to be registered dynamically"""
        handler_func(self.socketio)


    def notify_module_update(self):
        """Function that can be used externally by controller.py to notify frontend when modules updated"""
        modules = self.facade.get_modules()
        self.socketio.emit('module_update', {"modules": modules}) # Use socketio.emit instead of individual handlers to ensure proper context


    def push_module_update(self, modules: dict):
        self.socketio.emit('modules_update', modules)


    def _register_routes(self):
        # Serve React app. Must use the <path:path> converter (matches
        # slashes), not <path> (single segment only) -- otherwise a direct
        # load/refresh on a multi-segment client-side route (e.g.
        # /recording/sessions/<name>) 404s instead of falling through to
        # index.html for react-router to handle.
        @self.app.route("/", defaults={"path": ""})
        @self.app.route("/<path:path>")
        def serve(path):
            self.logger.info(f"Received request to access {path}")
            static_folder = self.app.static_folder
            file_path = os.path.join(static_folder, path)

            if os.path.exists(file_path) and not os.path.isdir(file_path):
                # If it's a real file, serve it
                return send_from_directory(static_folder, path)

            return send_from_directory(self.app.static_folder, "index.html")


    def _register_socketio_events(self):
        # Single source of truth for the running version — reads __version__.py
        # which is updated by the pre-commit hook and travels inside ZIP deploys.
        # git describe is NOT used because .git is excluded from rsync, so it
        # is stale on any device updated via the ZIP mechanism.
        _VERSION_FILE = "/usr/local/src/saviour/src/__version__.py"

        def _read_running_version() -> str:
            try:
                import re as _re
                with open(_VERSION_FILE) as _vf:
                    _m = _re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _vf.read())
                    if _m:
                        return _m.group(1)
            except Exception:
                pass
            return "unknown"

        # WebSocket event handlers - for use by the web interface
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            client_ip = request.remote_addr
            self.socketio.emit('client_ip', client_ip)
            self.logger.info(f"Client connected from {client_ip}")

            # Silently re-authenticate a returning connection that already
            # has a remembered password (e.g. a reconnect after a network
            # blip) -- no explicit "login" event needed for this path, since
            # the frontend already knows it was logged in and just wants the
            # new connection to carry the same privilege.
            if auth and self._check_admin_password(auth.get("password")):
                with self._auth_lock:
                    self._authenticated_sids.add(request.sid)

            # Send initial module list -- event name/payload shape must match
            # what useModules.js actually listens for (modules_update, raw
            # dict) and what the 'get_modules' handler below already sends;
            # this previously emitted a differently-named, differently-shaped
            # 'module_update' event that no frontend code has ever listened
            # for, so a reconnect (e.g. after a brief network blip) never
            # proactively refreshed a client's module/readiness state -- it
            # silently depended on some future real change to trigger a
            # fresh broadcast, until the operator did a full page reload.
            modules = self.facade.get_modules()
            self.logger.info(f"Page load get_modules() returned: {modules}, sending {len(modules)} modules to new client")
            self.socketio.emit('modules_update', modules)

            # Send current experiment name to new client
            if self.current_experiment_name:
                self.socketio.emit('experiment_name_update', {"experiment_name": self.current_experiment_name})
                self.logger.info(f"Sent current experiment name to new client: {self.current_experiment_name}")


        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.logger.info("Client disconnected")
            with self._auth_lock:
                self._authenticated_sids.discard(request.sid)


        @self.socketio.on('send_command')
        def handle_command(data):
            """
            Handle command from frontend.
            Command will be formatted as command_name param1=value1 param2=value2 etc
            For example, start_streaming client_ip=192.168.0.55 port=8080
            Communication manager will format this as cmd/<module_id> <command_name> <param1=value1> <param2=value2> etc 
            
            Args:
                command (json): The command received from the frontend. Should contain type, module_id (may be "all" or a specific module), and params field
            """
            if not self._require_auth("auth_required"):
                return
            try:
                command = data.get('type')
                module_id = data.get('module_id')
                params = data.get('params', {})

                if command == "start_recording":
                    params["experiment_name"] += ("-" + datetime.now().strftime("%Y%m%d_%H%M%S"))

                # Broadcast to every connected module when module_id is "all"
                if module_id == "all":
                    for mid in list(self.facade.get_modules().keys()):
                        self.facade.send_command(mid, command, params)
                else:
                    self.facade.send_command(module_id, command, params)

            except Exception as e:
                self.logger.error(f"Error handling command: {e!s}")
                self.socketio.emit('error', {'message': str(e)})


        @self.socketio.on("start_recording")
        def start_recording(data):
            """
            Start a new recording session.

            """
            if not self._require_auth("auth_required"):
                return
            try:
                self.logger.info(f"Start recording called with {data}")
                target = data.get("target")
                session_name = data.get("session_name")
                duration = data.get("duration")
                self.facade.start_recording(target, session_name, duration)
            except Exception as e:
                self.logger.error(f"Error starting recording: {e!s}")
                self.socketio.emit('error', {'message': str(e)})

        @self.socketio.on("stop_recording")
        def stop_recording(data):
            if not self._require_auth("auth_required"):
                return
            try:
                target = data.get("target")
                self.facade.stop_recording(target)
            except Exception as e:
                self.logger.error(f"Error stopping recording: {e!s}")
                self.socketio.emit('error', {'message': str(e)})


        """ Get Modules """
        @self.socketio.on('get_modules')
        def handle_module_update():
            """Handle request for module data"""
            # Get current modules from callback
            modules = self.facade.get_modules()
            self.logger.info(f"{len(modules)} modules connected")

            # Send module update to all clients
            self.socketio.emit('modules_update', modules)
            self.logger.info(f"Sent module update to all clients: {modules}")


        @self.socketio.on("check_ready")
        def handle_check_ready(data):
            if not self._require_auth("auth_required"):
                return
            target = data.get("target")
            modules = list(self.facade.get_modules_by_target(target).keys())
            # get_health is a cheap in-memory read on the module side — fire it at
            # every module immediately, no staggering needed.
            for mid in modules:
                self.facade.send_command(mid, "get_health", {})
            # validate_readiness makes each module mount+write+unmount against the
            # shared export share (module.py's _check_export()). Dispatching that
            # to every module within the same instant is a thundering herd against
            # the NAS's SMB server — confirmed live 2026-08-24 on a 20-module
            # habitat deployment, where most of the fleet failed readiness with a
            # mix of I/O error / device busy / no-such-file even though the share
            # was healthy throughout. Stagger dispatch to spread the resulting
            # mount/write/unmount cycles out over time instead.
            _READINESS_STAGGER_S = 0.3
            for i, mid in enumerate(modules):
                if i > 0:
                    self.socketio.sleep(_READINESS_STAGGER_S)
                self.facade.send_command(mid, "validate_readiness", {})
            # Yield to let get_health responses arrive and update the health cache
            # before running the PTP check.  get_health is an in-memory read on the
            # module side; ZMQ round-trip on a PoE LAN is < 5 ms, so 750 ms is ample.
            self.socketio.sleep(0.75)
            ptp = self.facade.check_ptp_sync(target)
            self.socketio.emit("ptp_sync_status", ptp)


        @self.socketio.on('get_sessions')
        def handle_get_sessions():
            sessions = self.facade.get_recording_sessions()
            self.logger.info(f"{len(sessions)} recording sessions")

            serializable_sessions = {k: asdict(v) for k, v in sessions.items()}

            self.socketio.emit("sessions_update", serializable_sessions)
            self.logger.info(f"Send sessions to clients: {serializable_sessions}")

        @self.socketio.on('get_session_log')
        def handle_get_session_log(data=None):
            from flask_socketio import emit as _emit
            session_name = (data or {}).get('session_name', '')
            if not session_name:
                _emit('session_log_response', {'session_name': '', 'lines': []})
                return
            mount = self.config.get("export.mount_path", "/home/pi/controller_share")
            log_path = os.path.join(mount, session_name, "session_events.log")
            try:
                with open(log_path) as f:
                    lines = [l.rstrip() for l in f.readlines()]
                total = len(lines)
                tail = lines[-200:]
                _emit('session_log_response', {
                    'session_name': session_name,
                    'lines': tail,
                    'total': total,
                    'truncated': total > 200,
                })
            except FileNotFoundError:
                _emit('session_log_response', {'session_name': session_name, 'lines': []})
            except Exception as e:
                _emit('session_log_response', {'session_name': session_name, 'lines': [], 'error': str(e)})


        @self.socketio.on("get_session_file_info")
        def handle_get_session_file_info(data=None):
            import re

            from flask_socketio import emit as _emit
            session_name = (data or {}).get("session_name", "")
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", session_name):
                _emit("session_file_info_response", {"session_name": session_name, "error": "invalid name"})
                return
            share = self.config.get("export.mount_path", "/home/pi/controller_share")
            session_dir = os.path.join(share, session_name)
            if not os.path.isdir(session_dir):
                _emit("session_file_info_response", {
                    "session_name": session_name,
                    "dir": session_dir,
                    "files": [],
                    "total_bytes": 0,
                })
                return
            files = []
            total = 0
            for root, dirs, filenames in os.walk(session_dir):
                dirs.sort()
                for fn in sorted(filenames):
                    full = os.path.join(root, fn)
                    try:
                        sz = os.path.getsize(full)
                    except OSError:
                        sz = 0
                    rel = os.path.relpath(full, session_dir)
                    files.append({"name": fn, "path": rel, "size_bytes": sz})
                    total += sz
            _emit("session_file_info_response", {
                "session_name": session_name,
                "dir": session_dir,
                "files": files,
                "total_bytes": total,
            })

        def _stream_zip_response(dir_path: str, zip_filename: str):
            """Stream a ZIP of every file under dir_path (built incrementally
            in a background thread via _QueueStream, not buffered in memory
            or on disk first) as a Flask response. Shared by the whole-
            session zip and the per-folder zip below -- same shape, only the
            root directory and the download filename differ."""
            q = _queue.SimpleQueue()

            def _build():
                try:
                    with zipfile.ZipFile(_QueueStream(q), 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
                        for root, dirs, filenames in os.walk(dir_path):
                            dirs.sort()
                            for fn in sorted(filenames):
                                full = os.path.join(root, fn)
                                zf.write(full, os.path.relpath(full, dir_path))
                except Exception as e:
                    self.logger.error(f"ZIP stream error for '{dir_path}': {e}")
                finally:
                    q.put(None)

            threading.Thread(target=_build, daemon=True).start()

            def _generate():
                while (chunk := q.get()) is not None:
                    yield chunk

            return self.app.response_class(
                _generate(),
                mimetype="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{zip_filename}"',
                    "X-Accel-Buffering": "no",
                },
            )

        @self.app.route("/api/sessions/<session_name>/download/<path:filename>")
        def download_session_file(session_name, filename):
            if not self._check_download_token(request.args.get("token")):
                return "Unauthorized -- request a download token first", 401
            import re
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", session_name):
                return "Invalid session name", 400
            share = os.path.realpath(self.config.get("export.mount_path", "/home/pi/controller_share"))
            session_dir = os.path.realpath(os.path.join(share, session_name))
            if not session_dir.startswith(share + os.sep):
                return "Forbidden", 403
            safe_path = os.path.realpath(os.path.join(session_dir, filename))
            if not safe_path.startswith(session_dir + os.sep):
                return "Forbidden", 403
            if os.path.isdir(safe_path):
                # A folder in the file-tree browser (e.g. a date or module
                # folder) -- zip that subtree rather than 404ing. Named
                # session-folder.zip (not just folder.zip) since multiple
                # sessions can have same-named module subfolders and the
                # browser would otherwise show several indistinguishable
                # download filenames.
                zip_name = f"{session_name}-{'-'.join(filename.rstrip('/').split('/'))}.zip"
                return _stream_zip_response(safe_path, zip_name)
            if not os.path.isfile(safe_path):
                return "Not found", 404
            return send_file(safe_path, as_attachment=True, download_name=os.path.basename(safe_path))

        @self.app.route("/api/sessions/<session_name>/download")
        def download_session_zip(session_name):
            if not self._check_download_token(request.args.get("token")):
                return "Unauthorized -- request a download token first", 401
            import re
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", session_name):
                return "Invalid session name", 400
            share = os.path.realpath(self.config.get("export.mount_path", "/home/pi/controller_share"))
            session_dir = os.path.realpath(os.path.join(share, session_name))
            if not session_dir.startswith(share + os.sep):
                return "Forbidden", 403
            if not os.path.isdir(session_dir):
                return "Not found", 404
            return _stream_zip_response(session_dir, f"{session_name}.zip")

        @self.app.route("/api/ptp_history.csv")
        def download_ptp_history():
            if not self._check_download_token(request.args.get("token")):
                return "Unauthorized -- request a download token first", 401
            # ?hours=N restricts to the last N hours (default 24, matching
            # export_ptp_history_csv's own default); ?hours=all requests
            # the entire retained buffer instead.
            hours_param = request.args.get("hours")
            if hours_param is None:
                hours, filename_part = 24.0, "24h"
            elif hours_param.lower() == "all":
                hours, filename_part = None, "all"
            else:
                try:
                    hours = float(hours_param)
                except ValueError:
                    return "Invalid hours parameter", 400
                if hours <= 0:
                    return "hours must be positive, or 'all'", 400
                filename_part = f"{hours_param}h"
            # export_ptp_history_csv() is a generator (one CSV row per
            # yield) -- Response streams it directly rather than buffering
            # the whole export in memory first, same reasoning as
            # _stream_zip_response above for session downloads.
            return Response(
                self.facade.export_ptp_history_csv(hours),
                mimetype="text/csv",
                headers={
                    "Content-Disposition":
                        f"attachment; filename=ptp_history_{filename_part}.csv"
                },
            )

        @self.socketio.on("create_session")
        def handle_create_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            target = data.get("target")
            session_name = data.get("session_name")
            duration_minutes = data.get("duration_minutes")  # None = infinite
            researcher = data.get("researcher") or None
            self.logger.info(f"Received request to create session {session_name} targeting {target} (duration_minutes={duration_minutes})")
            nas_error = self._check_nas_free_space()
            if nas_error:
                self.logger.error(f"NAS pre-check failed: {nas_error}")
                self.socketio.emit("session_error", {"error": f"NAS unreachable — {nas_error}"})
                return
            result = self.facade.create_session(session_name, target, duration_minutes, researcher)
            if result and not result.get("success"):
                self.socketio.emit("session_error", {"error": result.get("error")})
            elif result and result.get("success"):
                self._write_session_metadata(result["session_name"], target)
                # Sessions are created PENDING now, not auto-started -- the
                # frontend needs a direct signal (not just waiting on
                # sessions_update to eventually reflect it) to know exactly
                # which session to close the drawer and navigate to.
                self.socketio.emit("create_session_result", {
                    "success": True,
                    "session_name": result["session_name"],
                })


        @self.socketio.on("update_pending_session")
        def handle_update_pending_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = data.get("session_name")
            new_session_name = data.get("new_session_name")
            duration_minutes = data.get("duration_minutes")
            self.logger.info(f"Received request to update pending session '{session_name}'")
            result = self.facade.update_pending_session(session_name, new_session_name, duration_minutes)
            if result and not result.get("success"):
                self.socketio.emit("session_error", {"error": result.get("error")})
            elif result and result.get("success"):
                # Echoed back even when the name didn't change, so the
                # frontend has one consistent event to listen for -- it
                # only needs to act (navigate) when session_name differs
                # from what it sent.
                self.socketio.emit("update_pending_session_result", {
                    "success": True,
                    "session_name": result["session_name"],
                })


        @self.socketio.on("create_scheduled_session")
        def handle_create_scheduled_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            target = data.get("target")
            session_name = data.get("session_name")
            start_time = data.get("start_time")
            end_time = data.get("end_time")
            days = data.get("days")  # list of ints (0=Mon…6=Sun), None/[] = every day
            researcher = data.get("researcher") or None
            self.logger.info(f"Received request to create scheduled session {session_name} targeting {target} between {start_time} and {end_time} on days={days}")
            result = self.facade.create_scheduled_session(session_name, target, start_time, end_time, days, researcher)
            if result and not result.get("success"):
                self.socketio.emit("session_error", {"error": result.get("error")})
            elif result and result.get("success"):
                self._write_session_metadata(result["session_name"], target)


        @self.socketio.on("force_start_session")
        def handle_force_start_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = data.get("session_name")
            self.logger.info(f"Received force-start request for session '{session_name}'")
            result = self.facade.force_start_session(session_name)
            self.socketio.emit("force_start_result", {
                "session_name": session_name,
                "success": bool(result and result.get("success")),
                "error": result.get("error") if result and not result.get("success") else None,
            })

        @self.socketio.on("stop_session")
        def handle_stop_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = data.get("session_name")
            self.logger.info(f"Received request to stop session {session_name}")
            self.facade.stop_session(session_name)

        @self.socketio.on("delete_session")
        def handle_delete_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = data.get("session_name")
            delete_files = data.get("delete_files", True)
            force = data.get("force", False)
            self.logger.info(
                f"Received request to delete session '{session_name}' "
                f"(delete_files={delete_files}, force={force})"
            )
            result = self.facade.delete_session(session_name, delete_files, force)
            if "error" in result:
                self.socketio.emit("session_error", result)

        @self.socketio.on("retry_failed_exports")
        def handle_retry_failed_exports(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = data.get("session_name")
            self.logger.info(f"Received request to retry failed exports for session '{session_name}'")
            result = self.facade.retry_failed_exports(session_name)
            if "error" in result:
                self.socketio.emit("session_error", result)

        @self.socketio.on("request_recording_state_refresh")
        def handle_request_recording_state_refresh(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = (data or {}).get("session_name")
            self.logger.info(f"On-demand recording-state refresh requested for session '{session_name}'")
            result = self.facade.request_recording_state_refresh(session_name)
            if "error" in result:
                self.socketio.emit("session_error", result)

        @self.socketio.on("clear_ended_sessions")
        def handle_clear_ended_sessions(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            delete_files = data.get("delete_files", False) if data else False
            force = data.get("force", False) if data else False
            self.logger.info(
                f"Received request to clear ended sessions (delete_files={delete_files}, force={force})"
            )
            result = self.facade.clear_ended_sessions(delete_files, force)
            if result.get("skipped"):
                names = ", ".join(result["skipped_sessions"])
                self.socketio.emit("session_error", {
                    "error": (
                        f"Cleared {result['cleared']} session(s). Skipped "
                        f"{result['skipped']} with unresolved or failed exports: {names}. "
                        f"They'll stay listed until exports resolve, or force-clear them."
                    ),
                    "export_warning": True,
                    "skipped_sessions": result["skipped_sessions"],
                })

        @self.socketio.on("add_module_to_session")
        def handle_add_module_to_session(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            session_name = data.get("session_name")
            module_id = data.get("module_id")
            self.logger.info(f"Received request to add module '{module_id}' to session '{session_name}'")
            result = self.facade.add_module_to_session(session_name, module_id)
            if not result.get("success"):
                self.socketio.emit("session_error", {"error": result.get("error")})


        @self.socketio.on('module_status') # TODO: Does this make sense? Frontend shouldn't be sending module status
        def handle_module_status(data):
            """Handle module status update"""
            self.logger.info("IN WEB HANDLE_MODULE_STATUS")
            try:
                # self.logger.info(f"Received module status: {data}")
                if not isinstance(data, dict):
                    raise ValueError("Status data must be a dictionary")

                module_id = data.get('module_id')
                status = data.get('status')

                if not module_id or not status:
                    raise ValueError("Status must include 'module_id' and 'status'")

                # Handle recordings list response
                if status.get('type') == 'recordings_list':
                    self.logger.info(f"Broadcasting module recordings for module {module_id}")
                    module_recordings = status.get('recordings', [])

                    # Send individual module recordings response
                    self.socketio.emit('module_recordings', {
                        'module_id': module_id,
                        'recordings': module_recordings
                    })
                    return

                # Handle export complete response
                if status.get('type') == 'export_complete':
                    self.logger.info(f"Broadcasting export complete for module {module_id}")
                    self.socketio.emit('export_complete', {
                        'module_id': module_id,
                        'success': status.get('success', False),
                        'error': status.get('error'),
                        'filename': status.get('filename')
                    })
                    return

                # Handle recording started/stopped status
                if status.get('type') in ['recording_started', 'recording_stopped']:
                    self.logger.info(f"Broadcasting recording status for module {module_id}")
                    self.socketio.emit('module_status', {
                        'module_id': module_id,
                        'status': status
                    })
                    return

                # For heartbeat and other status types
                if 'recording_status' not in status:
                    self.logger.warning("Recording status not in received status update.")

                # Broadcast status to all clients
                self.socketio.emit('module_status', {
                    'module_id': module_id,
                    'status': status
                })

            except Exception as e:
                self.logger.error(f"Error handling module status: {e!s}")
                # Optionally emit error back to client
                # self.socketio.emit('error', {'message': str(e)})

        """ Experiment Metadata """
        # Experiment metadata
        @self.socketio.on('update_experiment_metadata')
        def handle_update_experiment_metadata(data):
            """Handle experiment metadata updates from frontend"""
            if not self._require_auth("auth_required"):
                return
            # Update stored metadata
            for key in ('experimenter', 'experiment', 'rat_id', 'strain', 'batch', 'stage', 'trial'):
                if key in data:
                    self.experiment_metadata[key] = data[key]

            # Rebuild experiment name
            self.current_experiment_name = self._generate_experiment_name()

            # Send confirmation back to client
            self.socketio.emit('experiment_metadata_updated', {
                'status': 'success',
                'metadata': self.experiment_metadata,
                'experiment_name': self.current_experiment_name
            })


        @self.socketio.on('get_experiment_metadata')
        def handle_get_experiment_metadata(data=None):
            """Handle request for experiment metadata from frontend"""
            # Send current metadata to client
            self.socketio.emit('experiment_metadata_response', {
                'status': 'success',
                'metadata': self.experiment_metadata,
                'experiment_name': self.current_experiment_name
            })


        """Settings Page"""
        @self.socketio.on("get_module_config")
        def handle_get_module_config(data):
            module_id = data.get("module_id")
            self.facade.get_module_config(module_id)


        @self.socketio.on('get_module_configs')
        def handle_get_module_configs(data=None):
            """Handle request for module configuration data"""
            self.logger.info("Get module configs called")
            self.facade.get_module_configs()


        @self.socketio.on('save_module_config')
        def handle_save_module_config(data):
            """Handle save module config from frontend"""
            if not self._require_auth("auth_required"):
                return
            module_id = data['id']
            config = _filter_private_keys(data.get("config", {}))

            if self.facade.is_module_recording(module_id):
                self.logger.warning(
                    f"Rejected config save for {module_id}: module is recording"
                )
                self.socketio.emit("module_config_error", {
                    "module_id": module_id,
                    "error": (
                        "Cannot change settings while this module is recording — "
                        "stop the session first."
                    ),
                })
                return

            self.logger.info(
                f"Received request to save config to module {module_id} "
                f"with data {config}"
            )

            # FrameSync role (sync_mode) is no longer set directly by the operator —
            # it's derived by facade.reconcile_framesync() from every camera's
            # framesync_enabled flag once this save is confirmed (modules.py calls
            # it from received_module_config()). If this module currently *is* the
            # elected transmitter, still propagate any fps/sensor_mode_index change
            # in this save to its clients immediately, so they stay pinned even when
            # the transmitter's own settings change after election, not just at it.
            camera_section = config.get("camera", {})
            module_configs = self.facade.get_module_configs()
            current_state = module_configs.get(module_id) or {}
            current_true = current_state.get("true_config") or {}
            if current_true.get("camera", {}).get("sync_mode") == "server":
                fps = camera_section.get("fps")
                sensor_mode_index = camera_section.get("sensor_mode_index")
                if fps is not None or sensor_mode_index is not None:
                    all_configs = self.facade.get_module_configs()
                    for client_id, client_cfg in all_configs.items():
                        if client_id == module_id:
                            continue
                        client_true = dict((client_cfg or {}).get("true_config") or {})
                        if client_true.get("camera", {}).get("sync_mode") != "client":
                            continue
                        client_camera = dict(client_true.get("camera", {}))
                        if fps is not None:
                            client_camera["fps"] = fps
                        if sensor_mode_index is not None:
                            client_camera["sensor_mode_index"] = sensor_mode_index
                        client_true["camera"] = client_camera
                        self.logger.info(
                            "Propagating server fps/sensor_mode_index to "
                            f"sync client {client_id}"
                        )
                        self.facade.set_target_module_config(client_id, client_true)
                        self.facade.send_command(client_id, "set_config", client_true)

            # Record intent on controller before sending - this sets status to PENDING
            # and stores the target so we can verify the round-trip when the module responds
            self.facade.set_target_module_config(module_id, config)
            # Send the config update command to the module
            self.facade.send_command(module_id, "set_config", config)

        @self.socketio.on('reset_module_config')
        def handle_reset_module_config(data):
            """Handle reset-to-defaults request from frontend"""
            if not self._require_auth("auth_required"):
                return
            module_id = data.get('module_id')
            if self._reject_config_change_if_recording([module_id], module_id=module_id):
                return
            self.logger.info(f"Received reset_module_config request for {module_id}")
            self.facade.send_command(module_id, "reset_config", {})


        @self.socketio.on('apply_section_to_cameras')
        def handle_apply_section_to_cameras(data):
            """Apply one config section from a source camera to all camera modules."""
            if not self._require_auth("auth_required"):
                return
            section = data.get("section")
            section_data = data.get("data", {})
            if not section or not isinstance(section_data, dict) or not section_data:
                self.logger.warning(f"apply_section_to_cameras: invalid payload {data}")
                return
            cameras = [mid for mid, m in self.facade.get_modules().items()
                       if "camera" in (m.get("type") or "")]
            if self._reject_config_change_if_recording(cameras):
                return
            self.logger.info(f"Applying section '{section}' to all camera modules")
            self.facade.apply_section_to_cameras(section, section_data)

        @self.socketio.on('apply_section_to_type')
        def handle_apply_section_to_type(data):
            """Apply one config section to all modules of a given type.
            module_type=None targets all modules regardless of type."""
            if not self._require_auth("auth_required"):
                return
            module_type = data.get("module_type")  # None means all modules
            section = data.get("section")
            section_data = data.get("data", {})
            if not section or not isinstance(section_data, dict) or not section_data:
                self.logger.warning(f"apply_section_to_type: invalid payload {data}")
                return
            targets = [mid for mid, m in self.facade.get_modules().items()
                       if module_type is None or module_type in (m.get("type") or "")]
            if self._reject_config_change_if_recording(targets):
                return
            label = module_type if module_type else "all"
            self.logger.info(f"Applying section '{section}' to all {label} modules")
            self.facade.apply_section_to_type(module_type, section, section_data)

        @self.socketio.on('sync_export_credentials')
        def handle_sync_export_credentials(data):
            """Push this controller's Samba credentials to a single module's export config."""
            if not self._require_auth("auth_required"):
                return
            module_id = data.get("module_id")
            if not module_id:
                return
            result = self.facade.sync_export_to_module(module_id)
            self.socketio.emit("export_sync_result", {"module_id": module_id, **result})

        def _sync_export_to_all_modules(creds: dict) -> None:
            """Push export credentials to every connected module and report
            the result. Shared by the manual 'sync_export_to_all' handler
            below and the auto-sync-on-save path in save_controller_config."""
            modules = self.facade.get_modules()
            results = {
                module_id: self.facade.sync_export_with_creds(module_id, creds)
                for module_id in modules
            }
            success_count = sum(1 for r in results.values() if r.get("success"))
            self.socketio.emit("export_sync_all_result", {
                "results": results,
                "success_count": success_count,
                "total": len(results),
            })

        @self.socketio.on('sync_export_to_all')
        def handle_sync_export_to_all(data=None):
            """Push export credentials to every connected module.

            If the frontend sends share_ip/share_path/share_username/share_password
            in the payload, those values are used and also persisted to the controller
            config.  Otherwise falls back to the currently saved controller config.
            """
            if not self._require_auth("auth_required"):
                return
            data = data or {}
            if "share_ip" in data:
                creds = {
                    "share_ip":       data.get("share_ip", ""),
                    "share_path":     data.get("share_path", "controller_share"),
                    "share_username": data.get("share_username", ""),
                    "share_password": data.get("share_password", ""),
                }
                # Persist so future auto-pushes (on module discovery) use the same values
                current = self.facade.get_config()
                current.setdefault("export", {}).update(creds)
                self.facade.set_config(current)
                self.ensure_export_share_mounted()
            else:
                creds = self.facade.get_export_credentials()

            _sync_export_to_all_modules(creds)

        @self.socketio.on('get_controller_samba_info')
        def handle_get_controller_samba_info(data=None):
            """Return this controller's own Samba share info for the 'Controller Share' preset."""
            # Found while adding get_export_destination below: this handler
            # carries a plaintext Samba password in its response and had no
            # _require_auth at all, unlike every sibling handler in this
            # section (see e.g. sync_export_credentials above). Fixed here
            # rather than left for the new handler to copy.
            if not self._require_auth("auth_required"):
                return
            info = self.facade.get_controller_own_share_info()
            self.socketio.emit("controller_samba_info_response", info)

        @self.socketio.on('get_export_destination')
        def handle_get_export_destination(data=None):
            """Return where module exports are *actually* going right now --
            distinct from get_controller_samba_info above, which always
            reports the controller's own address for the Settings page's
            "Controller Share" preset regardless of whether an external NAS
            override (export.share_ip) is configured. A habitat deployment
            commonly does export to a separate NAS, not the controller
            itself -- session-detail's "here's where your files are" notice
            needs the real answer, not the preset."""
            if not self._require_auth("auth_required"):
                return
            info = self.facade.get_export_credentials()
            self.socketio.emit("export_destination_response", info)

        """Controller System State"""
        @self.socketio.on("get_system_state")
        def handle_get_system_state(data=None):
            """Handle a request for information about controller system state e.g. recording status ."""
            state = self.facade.get_system_state()
            self.socketio.emit("system_state", state)


        """Controller Level Config"""
        @self.socketio.on('get_controller_config')
        def handle_get_controller_config(data=None):
            self.logger.info("Received request for controller config")
            config = self.facade.get_config()
            self.socketio.emit("controller_config_response", {
                "config": config
            })


        @self.socketio.on('save_controller_config')
        def handle_save_controller_config(data):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Saving controller config")
            # Snapshot the export section before overwriting it, so we can
            # tell below whether this particular save actually changed the
            # share credentials (vs. e.g. an unrelated controller.name edit)
            # -- auto-sync should fire on a real credential change, not on
            # every save regardless of section.
            old_export = self.facade.get_config().get("export", {})
            new_config = _filter_private_keys(data.get("config", {}))
            self.facade.set_config(new_config)
            self.socketio.emit("controller_config_response", {
                "config": self.facade.get_config()
            })

            new_export = new_config.get("export", {})
            share_keys = ("share_ip", "share_path", "share_username", "share_password")
            export_changed = any(
                old_export.get(k) != new_export.get(k) for k in share_keys
            )
            # The controller is the single authority for the export destination
            # (the per-module "manual" override was removed 2026-08-28), so a
            # changed share config is always pushed to every connected module
            # here rather than leaving them on stale credentials until they
            # reconnect. The "Sync to All Modules" button remains as a manual
            # re-push. ensure_export_share_mounted() is for the controller's own
            # file browser and is independent of the module push.
            if export_changed:
                self.ensure_export_share_mounted()
                creds = self.facade.get_export_credentials()
                if creds:
                    _sync_export_to_all_modules(creds)


        @self.socketio.on("get_controller_info")
        def handle_get_controller_info(data=None):
            import socket as _socket
            version = _read_running_version()
            try:
                nm = subprocess.run(
                    ["nmcli", "-g", "IP4.ADDRESS", "device", "show", "eth0"],
                    capture_output=True, text=True, timeout=5
                )
                ip = nm.stdout.strip().split("/")[0] if nm.returncode == 0 else "unknown"
            except Exception:
                ip = "unknown"
            name = self.config.get("controller.name", _socket.gethostname())
            self.socketio.emit("controller_info_response", {"ip": ip, "version": version, "hostname": name})


        @self.socketio.on("get_controller_health")
        def handle_get_controller_health(data=None):
            import shutil
            health = {}
            # IP — read eth0 directly so wlan0 is never returned
            try:
                nm = subprocess.run(
                    ["nmcli", "-g", "IP4.ADDRESS", "device", "show", "eth0"],
                    capture_output=True, text=True, timeout=5
                )
                health['ip'] = nm.stdout.strip().split("/")[0] if nm.returncode == 0 else None
            except Exception:
                health['ip'] = None
            # CPU temperature
            try:
                with open('/sys/class/thermal/thermal_zone0/temp') as f:
                    health['cpu_temp'] = round(int(f.read().strip()) / 1000, 1)
            except Exception:
                health['cpu_temp'] = None
            # CPU usage — read /proc/stat twice with a short sleep for accuracy
            try:
                def _read_cpu_stat():
                    with open('/proc/stat') as f:
                        fields = f.readline().split()
                    vals = list(map(int, fields[1:]))
                    idle = vals[3]
                    total = sum(vals)
                    return idle, total
                idle1, total1 = _read_cpu_stat()
                import time as _time
                _time.sleep(0.5)
                idle2, total2 = _read_cpu_stat()
                delta_total = total2 - total1
                delta_idle  = idle2  - idle1
                health['cpu_usage'] = round((1 - delta_idle / delta_total) * 100, 1) if delta_total else 0.0
            except Exception:
                health['cpu_usage'] = None
            # Memory
            try:
                import psutil
                mem = psutil.virtual_memory()
                health['memory_usage'] = round(mem.percent, 1)
                health['memory_total_gb'] = round(mem.total / (1024 ** 3), 1)
            except ImportError:
                try:
                    with open('/proc/meminfo') as f:
                        lines = f.readlines()
                    info = {l.split(':')[0]: int(l.split()[1]) for l in lines if ':' in l}
                    total = info.get('MemTotal', 0)
                    available = info.get('MemAvailable', 0)
                    health['memory_usage'] = round((total - available) / total * 100, 1) if total else None
                    health['memory_total_gb'] = round(total / (1024 ** 2), 1) if total else None  # kB → GB
                except Exception:
                    health['memory_usage'] = None
                    health['memory_total_gb'] = None
            # Disk
            try:
                usage = shutil.disk_usage('/var/lib/saviour')
                health['disk_used_pct'] = round(usage.used / usage.total * 100, 1)
                health['disk_free_gb'] = round(usage.free / (1024 ** 3), 1)
                health['disk_used_gb'] = round(usage.used / (1024 ** 3), 1)
                health['disk_total_gb'] = round(usage.total / (1024 ** 3), 1)
            except Exception:
                try:
                    usage = shutil.disk_usage('/')
                    health['disk_used_pct'] = round(usage.used / usage.total * 100, 1)
                    health['disk_free_gb'] = round(usage.free / (1024 ** 3), 1)
                    health['disk_used_gb'] = round(usage.used / (1024 ** 3), 1)
                    health['disk_total_gb'] = round(usage.total / (1024 ** 3), 1)
                except Exception:
                    health['disk_used_pct'] = None
                    health['disk_free_gb'] = None
                    health['disk_used_gb'] = None
                    health['disk_total_gb'] = None
            # Version
            health['version'] = _read_running_version() or None
            # Controller clock (UTC ISO-8601) — lets the frontend detect gross clock drift
            from datetime import datetime
            health['controller_time'] = datetime.now(UTC).isoformat()
            # Controller uptime in seconds
            health['uptime'] = round(self.facade.get_uptime())
            self.socketio.emit("controller_health_response", health)


        @self.socketio.on("get_health_summary")
        def handle_get_health_summary(data=None):
            summary = self.facade.get_health_summary()
            self.socketio.emit("health_summary_response", summary)

        @self.socketio.on("get_nas_health")
        def handle_get_nas_health(data=None):
            self.socketio.emit("nas_health_update", self._nas_health)


        # ── Update package store ──────────────────────────────────────────────
        _UPDATE_STORE = "/var/lib/saviour/updates"
        _UPDATE_ZIP   = os.path.join(_UPDATE_STORE, "saviour-latest.zip")
        _UPDATE_META  = os.path.join(_UPDATE_STORE, "update_meta.json")
        _SRC_ROOT     = "/usr/local/src/saviour"
        _STAGE_SKIP_DIRS = {'.git', 'env', '__pycache__', 'node_modules',
                            '.pytest_cache', 'dist', '.eggs'}

        def _stage_current_version_zip() -> dict:
            """Zip up _SRC_ROOT's current working tree and write it as the
            staged update package + metadata. Shared by "Stage Current" (the
            ZIP tab, packages whatever is on disk right now) and "Git Pull"
            (packages the tree immediately after a pull lands new code)."""
            src_root = _SRC_ROOT
            version = _read_running_version()
            self.logger.info(f"Staging current version {version} from {src_root}")
            os.makedirs(_UPDATE_STORE, exist_ok=True)
            tmp = _UPDATE_ZIP + ".tmp"
            skipped = 0
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
                for dirpath, dirnames, filenames in os.walk(src_root):
                    dirnames[:] = [
                        d for d in dirnames
                        if d not in _STAGE_SKIP_DIRS and not d.endswith('.egg-info')
                    ]
                    for filename in filenames:
                        if filename.endswith('.pyc'):
                            continue
                        abs_path = os.path.join(dirpath, filename)
                        rel_path = os.path.relpath(abs_path, src_root)
                        try:
                            zf.write(abs_path, rel_path)
                        except Exception as _fe:
                            self.logger.warning(f"Skipping {rel_path}: {_fe}")
                            skipped += 1
            size = os.path.getsize(tmp)
            os.replace(tmp, _UPDATE_ZIP)
            meta = {
                "version":     version,
                "filename":    f"saviour-{version}.zip",
                "size_bytes":  size,
                "uploaded_at": datetime.now().isoformat(),
            }
            with open(_UPDATE_META, "w") as f:
                json.dump(meta, f, indent=2)
            self.logger.info(
                f"Staged current version {version} "
                f"({size // 1024} KiB, {skipped} files skipped)"
            )
            return meta

        def _git_checkout_info() -> dict:
            """Whether _SRC_ROOT is a usable git checkout to pull updates
            from, and which branch/remote it would use. Gates the "Git Pull"
            update option -- most deployed devices only ever receive code via
            the ZIP update path, which explicitly excludes .git from the
            rsync (see _read_running_version's comment above), so their
            .git, if present at all, is stale relative to the actual deployed
            content until a pull resyncs it."""
            git_dir = os.path.join(_SRC_ROOT, ".git")
            if not os.path.isdir(git_dir):
                return {"available": False, "reason": "No git checkout on this device"}
            try:
                branch = subprocess.run(
                    ["git", "-C", _SRC_ROOT, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=10, check=False,
                ).stdout.strip()
                if not branch or branch == "HEAD":
                    return {
                        "available": False,
                        "reason": "Detached HEAD — checkout a branch first",
                    }
                remote = subprocess.run(
                    ["git", "-C", _SRC_ROOT, "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=10, check=False,
                ).stdout.strip()
                if not remote:
                    return {
                        "available": False,
                        "reason": "No 'origin' remote configured",
                    }
                return {"available": True, "branch": branch, "remote": remote}
            except Exception as e:
                return {"available": False, "reason": str(e)}

        @self.app.route("/update/package")
        def serve_update_package():
            if not os.path.exists(_UPDATE_ZIP):
                return "No update staged", 404
            return send_file(_UPDATE_ZIP, as_attachment=True,
                             download_name="saviour-update.zip",
                             mimetype="application/zip")

        @self.socketio.on("get_update_info")
        def handle_get_update_info(data=None):
            from flask_socketio import emit as _emit
            running = _read_running_version()
            staged = None
            if os.path.exists(_UPDATE_META):
                try:
                    with open(_UPDATE_META) as f:
                        staged = json.load(f)
                except Exception:
                    pass
            _emit("update_info", {
                "running_version": running,
                "staged": staged,
                "git": _git_checkout_info(),
            })

        @self.socketio.on("upload_update_start")
        def handle_upload_update_start(data):
            from flask_socketio import emit as _emit
            if not self._require_auth("auth_required"):
                return
            with self._upload_lock:
                self._upload_chunks = {}
                self._upload_meta = {
                    "filename":     data.get("filename", "saviour-update.zip"),
                    "total_chunks": int(data.get("total_chunks", 0)),
                    "total_bytes":  int(data.get("total_bytes", 0)),
                }
            _emit("upload_update_ack", {"status": "ready"})

        @self.socketio.on("upload_update_chunk")
        def handle_upload_update_chunk(data):
            import io
            import re
            import zipfile

            from flask_socketio import emit as _emit
            if not self._require_auth("auth_required"):
                return
            chunk_index = data.get("index")
            chunk_data  = data.get("data")   # bytes from Socket.IO binary frame
            if chunk_data is None or chunk_index is None:
                return
            with self._upload_lock:
                self._upload_chunks[chunk_index] = (
                    chunk_data if isinstance(chunk_data, (bytes, bytearray))
                    else bytes(chunk_data)
                )
                received = len(self._upload_chunks)
                total    = self._upload_meta.get("total_chunks", 0)
                filename = self._upload_meta.get("filename", "")
            _emit("upload_update_progress", {"received": received, "total": total})
            if received < total:
                return
            # All chunks received — assemble and validate
            try:
                assembled = b"".join(
                    self._upload_chunks[i] for i in range(total)
                )
                if not zipfile.is_zipfile(io.BytesIO(assembled)):
                    _emit("upload_update_error", {"error": "File is not a valid ZIP archive"})
                    return
                # Try version sources in order of reliability:
                # 1. v<digits> tag in the filename (release ZIPs from GitHub)
                # 2. src/__version__.py inside the ZIP (tracked file, updated at tag time)
                # 3. Filename stem as a last resort
                m = re.search(r'v(\d[\d\.\-\w]*)', filename)
                if m:
                    version = f"v{m.group(1)}"
                else:
                    version = None
                    try:
                        with zipfile.ZipFile(io.BytesIO(assembled)) as z:
                            for name in z.namelist():
                                if name.split('/')[-1] == '__version__.py':
                                    src = z.read(name).decode()
                                    vm = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', src)
                                    if vm:
                                        version = vm.group(1)
                                        break
                    except Exception:
                        pass
                    if not version:
                        version = os.path.splitext(filename)[0]
                os.makedirs(_UPDATE_STORE, exist_ok=True)
                tmp = _UPDATE_ZIP + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(assembled)
                os.replace(tmp, _UPDATE_ZIP)
                meta = {
                    "version":     version,
                    "filename":    filename,
                    "size_bytes":  len(assembled),
                    "uploaded_at": datetime.now().isoformat(),
                }
                with open(_UPDATE_META, "w") as f:
                    json.dump(meta, f, indent=2)
                self.logger.info(
                    f"Update package staged: {filename} ({version}, "
                    f"{len(assembled) // 1024} KiB)"
                )
                _emit("upload_update_complete", meta)
            except Exception as e:
                self.logger.error(f"Upload assembly failed: {e}")
                _emit("upload_update_error", {"error": str(e)})

        @self.socketio.on("deploy_update")
        def handle_deploy_update(data=None):
            from flask_socketio import emit as _emit
            if not self._require_auth("auth_required"):
                return
            if not os.path.exists(_UPDATE_ZIP):
                _emit("deploy_update_error",
                     {"error": "No update staged — upload a package first"})
                return

            controller_ip = getattr(self.facade, 'get_controller_ip',
                                    lambda: None)() or "localhost"
            try:
                controller_ip = self.facade.controller.network.ip
            except Exception:
                pass
            controller_url = f"http://{controller_ip}:5000"

            # Modules only -- the controller is never swept into this
            # broadcast. Updating the controller itself is a separate,
            # deliberate action (deploy_update_to_controller, below),
            # matching how reboot/shutdown already distinguish "all modules"
            # from the controller's own dedicated actions.
            modules = list(self.facade.get_modules().keys())
            for mid in modules:
                try:
                    self.facade.send_command(
                        mid, "update_saviour",
                        {"controller_url": controller_url}
                    )
                except Exception as e:
                    self.logger.error(f"Failed to send update to {mid}: {e}")
            self.socketio.emit("deploy_update_status",
                               {"stage": "modules_notified", "count": len(modules)})

        @self.socketio.on("deploy_update_to_controller")
        def handle_deploy_update_to_controller(data=None):
            import shutil

            from flask_socketio import emit as _emit
            if not self._require_auth("auth_required"):
                return
            if not os.path.exists(_UPDATE_ZIP):
                _emit("deploy_update_error",
                     {"error": "No update staged — upload a package first"})
                return

            def _apply_to_controller():
                try:
                    extract_dir = "/tmp/saviour_update"
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    os.makedirs(extract_dir)
                    extract_preserving_permissions(_UPDATE_ZIP, extract_dir)
                    contents = os.listdir(extract_dir)
                    source = extract_dir
                    if (len(contents) == 1
                            and os.path.isdir(os.path.join(extract_dir, contents[0]))):
                        source = os.path.join(extract_dir, contents[0])
                    subprocess.run([
                        "rsync", "-a",
                        "--chown=pi:pi",
                        "--exclude=env/",
                        "--exclude=.git/",
                        f"{source}/",
                        "/usr/local/src/saviour/",
                    ], check=True)
                    # pip install is best-effort — devices may be offline.
                    # The rsync above is the critical step; a failed dependency
                    # install is logged but must not block the service restart.
                    pip_result = subprocess.run([
                        "/usr/local/src/saviour/env/bin/pip", "install", "-q",
                        "--no-index",
                        "/usr/local/src/saviour/",
                    ])
                    if pip_result.returncode != 0:
                        self.logger.warning(
                            "pip install --no-index failed (new dependencies may need "
                            "a manual `pip install .` with internet access)"
                        )
                    # Rebuild frontend so JSX changes ship with the update.
                    frontend_dir = "/usr/local/src/saviour/src/controller/frontend"
                    npm_bin = shutil.which("npm")
                    if not npm_bin:
                        import glob as _glob
                        candidates = sorted(_glob.glob("/home/pi/.nvm/versions/node/*/bin/npm"))
                        npm_bin = candidates[-1] if candidates else None
                    if npm_bin and os.path.isdir(frontend_dir):
                        self.socketio.emit("deploy_update_status", {"stage": "building_frontend"})
                        self.logger.info("Rebuilding frontend after update...")
                        subprocess.run([npm_bin, "install", "--silent"],
                                       cwd=frontend_dir, capture_output=True)
                        build = subprocess.run([npm_bin, "run", "build"],
                                               cwd=frontend_dir, capture_output=True)
                        if build.returncode == 0:
                            self.logger.info("Frontend rebuilt successfully")
                        else:
                            self.logger.warning(
                                "Frontend build failed after update: "
                                + build.stderr.decode(errors="replace")
                            )
                    else:
                        self.logger.warning("npm not found — frontend not rebuilt after update")
                except Exception as e:
                    self.logger.error(f"Controller update failed: {e}")
                    self.socketio.emit("deploy_update_error", {"error": str(e)})
                    return
                self.logger.info("Update applied — restarting controller service")
                time.sleep(2)
                subprocess.Popen(["sudo", "systemctl", "restart", "saviour.service"])

            threading.Thread(target=_apply_to_controller, daemon=True,
                             name="saviour-deploy-controller").start()

        @self.socketio.on("stage_current_version")
        def handle_stage_current_version(data=None):
            if not self._require_auth("auth_required"):
                return

            def _do_stage():
                try:
                    meta = _stage_current_version_zip()
                    self.socketio.emit("upload_update_complete", meta)
                except Exception as e:
                    self.logger.error(f"Stage current version failed: {e}")
                    self.socketio.emit("upload_update_error", {"error": str(e)})

            threading.Thread(target=_do_stage, daemon=True,
                             name="saviour-stage").start()

        @self.socketio.on("git_pull_update")
        def handle_git_pull_update(data=None):
            """Fetch + hard-reset the current branch from 'origin', then
            stage the result the same way "Stage Current" does. Deliberately
            only ever pulls from the checkout's own already-configured
            origin/branch -- never a caller-supplied URL or ref -- so this
            doesn't add a new untrusted-input path the way update_saviour's
            caller-supplied controller_url does (see CLAUDE.md's security
            notes on that command).

            A hard reset (not a merge-based pull) is deliberate: a device
            that has ever received a ZIP-based update has a working tree
            that no longer matches what git last checked out (ZIP deploys
            explicitly rsync over .git-tracked files without going through
            git at all), so a merge could spuriously conflict against drift
            git never asked for. Hard reset always lands exactly on
            origin/<branch> regardless of that drift.
            """
            if not self._require_auth("auth_required"):
                return
            info = _git_checkout_info()
            if not info.get("available"):
                self.socketio.emit("upload_update_error", {
                    "error": info.get("reason", "Git checkout not available"),
                })
                return
            branch = info["branch"]

            def _do_pull():
                try:
                    self.socketio.emit(
                        "git_pull_status", {"stage": "fetching", "branch": branch},
                    )
                    subprocess.run(
                        ["git", "-C", _SRC_ROOT, "fetch", "--prune", "origin", branch],
                        check=True, capture_output=True, text=True, timeout=120,
                    )
                    self.socketio.emit(
                        "git_pull_status", {"stage": "resetting", "branch": branch},
                    )
                    subprocess.run(
                        ["git", "-C", _SRC_ROOT, "reset", "--hard", f"origin/{branch}"],
                        check=True, capture_output=True, text=True, timeout=30,
                    )
                    commit = subprocess.run(
                        ["git", "-C", _SRC_ROOT, "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True, timeout=10, check=False,
                    ).stdout.strip()
                    self.logger.info(f"Git pull update: {branch} now at {commit}")
                    self.socketio.emit("git_pull_status", {
                        "stage": "staging", "branch": branch, "commit": commit,
                    })
                    meta = _stage_current_version_zip()
                    self.socketio.emit("upload_update_complete", meta)
                except subprocess.CalledProcessError as e:
                    err = (e.stderr or str(e)).strip()
                    self.logger.error(f"Git pull update failed: {err}")
                    self.socketio.emit(
                        "upload_update_error", {"error": f"git failed: {err}"},
                    )
                except Exception as e:
                    self.logger.error(f"Git pull update failed: {e}")
                    self.socketio.emit("upload_update_error", {"error": str(e)})

            threading.Thread(target=_do_pull, daemon=True,
                             name="saviour-git-pull").start()

        @self.socketio.on("deploy_update_to_module")
        def handle_deploy_update_to_module(data):
            from flask_socketio import emit as _emit
            if not self._require_auth("auth_required"):
                return
            module_id = data.get("module_id") if data else None
            if not module_id:
                _emit("deploy_update_error", {"error": "module_id required"})
                return
            if not os.path.exists(_UPDATE_ZIP):
                _emit("deploy_update_error", {"error": "No update staged — upload a package first"})
                return
            controller_ip = "localhost"
            try:
                controller_ip = self.facade.controller.network.ip
            except Exception:
                pass
            controller_url = f"http://{controller_ip}:5000"
            try:
                self.facade.send_command(module_id, "update_saviour",
                                         {"controller_url": controller_url})
            except Exception as e:
                _emit("deploy_update_error", {"error": str(e)})

        @self.socketio.on('shutdown_saviour')
        def handle_shutdown_saviour(data=None):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Shutdown SAVIOUR requested — sending shutdown to all modules then shutting down controller")
            for mid in list(self.facade.get_modules().keys()):
                try:
                    self.facade.send_command(mid, "shutdown", {})
                except Exception as e:
                    self.logger.error(f"Failed to send shutdown to module {mid}: {e}")
            self.socketio.emit("shutdown_saviour_ack", {})
            def _shutdown():
                time.sleep(5)
                subprocess.Popen(['sudo', 'shutdown', 'now'])
            threading.Thread(target=_shutdown, daemon=True).start()


        @self.socketio.on('reboot_saviour')
        def handle_reboot_saviour(data=None):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Reboot SAVIOUR requested — sending reboot to all modules then rebooting controller")
            for mid in list(self.facade.get_modules().keys()):
                try:
                    self.facade.send_command(mid, "reboot", {})
                except Exception as e:
                    self.logger.error(f"Failed to send reboot to module {mid}: {e}")
            self.socketio.emit("reboot_saviour_initiated", {})
            def _reboot():
                time.sleep(3)
                subprocess.Popen(['sudo', 'reboot'])
            threading.Thread(target=_reboot, daemon=True).start()


        @self.socketio.on('restart_saviour_controller_service')
        def handle_restart_controller_service(data=None):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Controller service restart requested")
            self.socketio.emit("controller_action_ack", {"action": "restart_service"})
            def _restart():
                time.sleep(1)
                subprocess.Popen(['sudo', 'systemctl', 'restart', 'saviour.service'])
            threading.Thread(target=_restart, daemon=True).start()


        @self.socketio.on('reboot_controller')
        def handle_reboot_controller(data=None):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Controller reboot requested")
            self.socketio.emit("controller_action_ack", {"action": "reboot"})
            def _reboot():
                time.sleep(2)
                subprocess.Popen(['sudo', 'reboot'])
            threading.Thread(target=_reboot, daemon=True).start()


        @self.socketio.on('shutdown_controller')
        def handle_shutdown_controller(data=None):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Controller shutdown requested")
            self.socketio.emit("controller_action_ack", {"action": "shutdown"})
            def _shutdown():
                time.sleep(2)
                subprocess.Popen(['sudo', 'shutdown', 'now'])
            threading.Thread(target=_shutdown, daemon=True).start()


        @self.socketio.on("test_teams_webhook")
        def handle_test_teams_webhook(data=None):
            if not self._require_auth("auth_required"):
                return
            # Optional: test the URL the operator has typed into the Alerts tab
            # (unsaved) rather than the saved config value.
            override_url = (data or {}).get("webhook_url") if isinstance(data, dict) else None
            def _run():
                success, detail = self.facade.controller.notifier.send_test(webhook_url=override_url)
                self.socketio.emit("teams_test_result", {"success": success, "detail": detail})
            threading.Thread(target=_run, daemon=True, name="teams-test").start()

        @self.socketio.on("get_bug_report")
        def handle_get_bug_report(data=None):
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Bug report requested")
            # Capture sid here, on the request thread -- it's not available
            # inside the background thread, and the diagnostics zip (raw
            # journalctl output, unredacted config) should only ever reach
            # the socket that asked for it, not every connected guest.
            requester_sid = request.sid
            threading.Thread(
                target=self._collect_bug_report, args=(requester_sid,), daemon=True
            ).start()

        @self.app.route("/api/bug_report/<token>")
        def download_bug_report(token):
            entry = self._bug_report_store.get(token)
            if not entry:
                return "Not found", 404
            data_bytes, filename = entry
            return send_file(
                io.BytesIO(data_bytes),
                mimetype="application/zip",
                as_attachment=True,
                download_name=filename,
            )

        @self.socketio.on("set_controller_time")
        def handle_set_controller_time(data=None):
            from datetime import datetime
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Set controller time requested")
            ntp_was_enabled = False
            try:
                iso = (data or {}).get("iso", "")
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")

                # timedatectl set-time refuses to run while NTP sync is active.
                # Check current state, disable if needed, and restore afterwards.
                ntp_check = subprocess.run(
                    ["timedatectl", "show", "--property=NTP"],
                    capture_output=True, text=True, timeout=5
                )
                ntp_was_enabled = ntp_check.stdout.strip() == "NTP=yes"
                if ntp_was_enabled:
                    subprocess.run(["timedatectl", "set-ntp", "false"],
                                   capture_output=True, timeout=5)

                result = subprocess.run(
                    ["timedatectl", "set-time", time_str],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    self.logger.info(f"Controller time set to {time_str} UTC")
                    self.socketio.emit("set_time_result", {"success": True})
                else:
                    err = result.stderr.strip() or result.stdout.strip() or "timedatectl returned non-zero"
                    self.logger.error(f"timedatectl set-time failed: {err}")
                    self.socketio.emit("set_time_result", {"success": False, "error": err})
            except Exception as e:
                self.logger.error(f"set_controller_time error: {e}")
                self.socketio.emit("set_time_result", {"success": False, "error": str(e)})
            finally:
                if ntp_was_enabled:
                    subprocess.run(["timedatectl", "set-ntp", "true"],
                                   capture_output=True, timeout=5)


        """Viewing exported recordings on the share"""
        @self.socketio.on('get_exported_recordings')
        def handle_get_exported_recordings():
            """Handle request for exported recordings"""
            try:
                recordings = self.get_exported_recordings()
                self.socketio.emit('exported_recordings_list', {
                    'exported_recordings': recordings
                })
            except Exception as e:
                self.logger.error(f"Error getting exported recordings: {e!s}")
                self.socketio.emit('exported_recordings_list', {
                    'exported_recordings': [],
                    'error': str(e)
                })

        @self.socketio.on('get_module_health')
        def handle_get_module_health():
            """Handle request for module health status"""
            health = self.facade.get_module_health()

            self.socketio.emit('module_health_update', {
                'module_health': health
            })


        """ Recording """
        @self.socketio.on("get_recording_sessions")
        def handle_get_recording_sessions():
            sessions = self.facade.get_recording_sessions()
            serializable = {k: asdict(v) for k, v in sessions.items()}
            self.socketio.emit("recording_sessions", serializable)


        """ Debug """
        @self.socketio.on('get_debug_data')
        def handle_get_debug_info():
            self.logger.info("Received request for debug data")
            debug_data = {}
            debug_data["modules"] = self.facade.get_modules()
            debug_data["module_health"] = self.facade.get_module_health()
            debug_data["module_configs"] = self.facade.get_module_configs()
            self.socketio.emit("debug_data", debug_data)

        """ Login """
        @self.socketio.on("login")
        def handle_login(data):
            password = (data or {}).get("password")
            if self._check_admin_password(password):
                with self._auth_lock:
                    self._authenticated_sids.add(request.sid)
                self.socketio.emit("login_success", room=request.sid)
            else:
                self.socketio.emit("login_error", "Wrong password", room=request.sid)


        @self.socketio.on("request_download_token")
        def handle_request_download_token(data=None):
            # Session file/zip downloads are plain <a>/window.location GETs
            # with no Socket.IO session of their own, so being in
            # _authenticated_sids doesn't reach them -- this hands the
            # already-authenticated socket a short-lived token to put on
            # the download URL instead. See _issue_download_token.
            if not self._require_auth("auth_required"):
                return
            token = self._issue_download_token()
            self.socketio.emit("download_token", {"token": token}, room=request.sid)


        @self.socketio.on("change_admin_password")
        def handle_change_admin_password(data):
            # Requires the *current* password, not just an existing
            # authenticated connection -- otherwise a session left logged in
            # on a shared screen could silently lock everyone else out.
            if not self._require_auth("change_password_error", {"error": "Login required for this action"}):
                return
            data = data or {}
            current_password = data.get("current_password")
            new_password = data.get("new_password", "")
            if not self._check_admin_password(current_password):
                self.socketio.emit("change_password_error", {"error": "Current password is incorrect"}, room=request.sid)
                return
            if len(new_password) < 8:
                self.socketio.emit("change_password_error", {"error": "New password must be at least 8 characters"}, room=request.sid)
                return
            self._write_admin_password(new_password)
            self.logger.warning(f"Admin password changed at {self._ADMIN_CREDENTIALS_FILE}")
            self.socketio.emit("change_password_success", room=request.sid)


        """ Commands and utility """
        @self.socketio.on('remove_module')
        def handle_remove_module(module):
            if not self._require_auth("auth_required"):
                return
            self.logger.info(f"Received request to remove module: {module['id']}")
            self.facade.remove_module(module['id'])


    def broadcast_module_health(self):
        """Push current module health to all connected frontend clients."""
        self.socketio.emit('module_health_update', {
            'module_health': self.facade.get_module_health()
        })


    def broadcast_recording_state_update(self, module_id: str, status_data: dict):
        """Push a module's latest local recording-pipeline summary (pending/
        to_export/exported) to the frontend as it arrives. See
        Modules.update_recording_state() for why only the folder-summary
        keys are kept, not the raw cmd_ack envelope."""
        summary = {
            k: v for k, v in status_data.items() if k in ("pending", "to_export", "exported")
        }
        self.socketio.emit('module_recording_state_update', {
            'module_id': module_id,
            'summary': summary,
            'last_reported': time.time(),
        })


    def update_modules(self, modules: list):
        """Update the list of modules from the controller service manager"""
        self._modules = modules


    def update_module_readiness(self, module_id: str, ready_status: dict):
        """Update module readiness state and broadcast to all clients"""
        import time

        # Store the readiness status with timestamp
        self.module_readiness[module_id] = {
            'ready': ready_status.get('ready', False),
            'timestamp': time.time(),
            'checks': ready_status.get('checks', {}),
            'error': ready_status.get('error')
        }

        self.logger.info(f"Updated readiness for {module_id}: {'ready' if ready_status.get('ready') else 'not ready'}")

        # Broadcast to all connected clients
        self.socketio.emit('update_module_readiness', {
            'module_id': module_id,
            'ready': ready_status.get('ready', False),
            'timestamp': self.module_readiness[module_id]['timestamp'],
            'checks': ready_status.get('checks', {}),
            'error': ready_status.get('error')
        })


    # ── Bug report ────────────────────────────────────────────────────────────

    def handle_diagnostics_ack(self, module_id: str, data: dict) -> None:
        """Called by controller when a get_diagnostics cmd_ack arrives from a module."""
        with self._diag_lock:
            entry = self._diag_pending.get(module_id)
        if entry:
            entry['data'] = data
            entry['event'].set()

    def _collect_bug_report(self, requester_sid: str) -> None:
        """Background thread: gather logs from controller + all online modules, emit download token.

        Both emits below are scoped to `requester_sid` (the socket that
        asked), not broadcast -- the finished zip carries raw journalctl
        output and sanitised-but-still-sensitive config for every module,
        not something every connected guest should be handed a link to.
        """
        self.socketio.emit(
            "bug_report_status", {"status": "collecting"}, room=requester_sid
        )

        modules = self.facade.get_modules() if self.facade else {}
        online_ids = [mid for mid, m in modules.items() if m.get('online')]

        # Register pending entries before sending commands (avoid race)
        pending = {}
        for mid in online_ids:
            entry = {'event': threading.Event(), 'data': None}
            pending[mid] = entry
        with self._diag_lock:
            self._diag_pending.update(pending)

        # Fire get_diagnostics to every online module
        for mid in online_ids:
            self.facade.send_command(mid, "get_diagnostics", {})

        # Wait up to 15 s for each module
        TIMEOUT = 15
        for mid in online_ids:
            pending[mid]['event'].wait(timeout=TIMEOUT)

        with self._diag_lock:
            for mid in online_ids:
                self._diag_pending.pop(mid, None)

        # Collect controller logs, including the previous boot's service +
        # kernel journal so a controller reboot/hang is diagnosable after the
        # fact (needs persistent journald — setup.sh / mend.sh enable it).
        ctrl_journals = {
            "logs.txt": _journalctl(
                ["-u", "saviour.service", "-n", "5000", "--output=short-precise"]),
            "logs_prevboot.txt": _journalctl(
                ["-u", "saviour.service", "-b", "-1", "-n", "2000",
                 "--output=short-precise"]),
            "kernel.txt": _journalctl(["-k", "-b", "0", "-n", "3000"]),
            "kernel_prevboot.txt": _journalctl(["-k", "-b", "-1", "-n", "3000"]),
            "boots.txt": _journalctl(["--list-boots"]),
        }

        # Build ZIP in memory
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, content in ctrl_journals.items():
                zf.writestr(f"saviour_diagnostics_{ts}/controller/{fname}", content)

            ctrl_config = _sanitise_config_dict(self.facade.get_config() if self.facade else {})
            zf.writestr(f"saviour_diagnostics_{ts}/controller/config.json",
                        json.dumps(ctrl_config, indent=2, default=str))

            health = self.facade.get_module_health() if self.facade else {}
            zf.writestr(f"saviour_diagnostics_{ts}/controller/health.json",
                        json.dumps(health, indent=2, default=str))

            # PTP offset history, bounded to the last 24 h so the bundle
            # stays small (the standalone /api/ptp_history.csv route can
            # pull a longer / full-retention window for plotting).
            try:
                ptp_csv = ""
                if self.facade:
                    ptp_csv = "".join(self.facade.export_ptp_history_csv(24.0))
                zf.writestr(
                    f"saviour_diagnostics_{ts}/controller/ptp_history_24h.csv",
                    ptp_csv,
                )
            except Exception as e:
                zf.writestr(
                    f"saviour_diagnostics_{ts}/controller/ptp_history_24h.csv",
                    f"Could not collect PTP history: {e}",
                )

            sessions = self.facade.get_recording_sessions() if self.facade else {}
            zf.writestr(f"saviour_diagnostics_{ts}/controller/sessions.json",
                        json.dumps(sessions, indent=2, default=str))

            offline_ids = [mid for mid, m in modules.items() if not m.get('online')]

            for mid in online_ids:
                data = pending[mid].get('data')
                base = f"saviour_diagnostics_{ts}/modules/{mid}"
                if data:
                    zf.writestr(f"{base}/logs.txt", data.get('logs', '(no logs)'))
                    # Previous-boot service + kernel journal (added 2026-08-27):
                    # the field is absent from an older module that predates
                    # this, so only write what's actually present.
                    for key, fname in (
                        ("logs_prevboot", "logs_prevboot.txt"),
                        ("kernel_prevboot", "kernel_prevboot.txt"),
                        ("boots", "boots.txt"),
                    ):
                        if data.get(key):
                            zf.writestr(f"{base}/{fname}", data[key])
                    cfg = _sanitise_config_dict(data.get('config', {}))
                    zf.writestr(f"{base}/config.json",
                                json.dumps(cfg, indent=2, default=str))
                else:
                    zf.writestr(f"{base}/logs.txt",
                                "(no response within timeout)")

            manifest = {
                "generated_at": ts,
                "online_modules": online_ids,
                "offline_modules": offline_ids,
                "modules_that_responded": [mid for mid in online_ids if pending[mid].get('data')],
            }
            zf.writestr(f"saviour_diagnostics_{ts}/manifest.json",
                        json.dumps(manifest, indent=2))

        token = secrets.token_urlsafe(16)
        self._bug_report_store = {token: (buf.getvalue(), f"saviour_diagnostics_{ts}.zip")}
        self.socketio.emit(
            "bug_report_ready",
            {"token": token, "filename": f"saviour_diagnostics_{ts}.zip"},
            room=requester_sid,
        )

    def _nas_monitor_loop(self):
        NAS_CHECK_INTERVAL_S = self.config.get("export.nas_health_interval_s", 300)
        # Brief initial delay so the server is fully up before the first probe.
        self._nas_monitor_stop.wait(30)
        while not self._nas_monitor_stop.is_set():
            self._run_nas_health_check()
            self._nas_monitor_stop.wait(NAS_CHECK_INTERVAL_S)

    def _run_nas_health_check(self):
        nas_ip = self.config.get("export.share_ip", "")
        if not nas_ip:
            new = {"status": "unconfigured", "error": None, "checked_at": time.time()}
        else:
            error = self._check_nas_free_space()
            new = {
                "status": "ok" if error is None else "error",
                "error": error,
                "checked_at": time.time(),
            }
        prev_status = self._nas_health.get("status")
        self._nas_health = new
        if new["status"] != prev_status:
            self.logger.warning(f"NAS health: {prev_status} → {new['status']}"
                                + (f" ({new['error']})" if new.get("error") else ""))
        self.socketio.emit("nas_health_update", new)

    def start(self):
        """Start the web interface in a separate thread"""
        if not self._running:
            self.logger.info(f"Starting web interface on port {self.port}")
            self._running = True
            self.web_thread = threading.Thread(
                target=self._run_server,
                daemon=True
            )
            self.web_thread.start()
            self._nas_monitor_stop.clear()
            threading.Thread(target=self._nas_monitor_loop, daemon=True).start()
            return self.web_thread


    def _run_server(self):
        """Internal method to run the Flask server"""
        self.socketio.run(self.app, host='0.0.0.0', port=self.port, debug=False, allow_unsafe_werkzeug=True)


    def stop(self):
        """Stop the web interface"""
        if self._running:
            self._running = False
            self.socketio.stop()


    def list_modules(self):
        """List all discovered modules"""
        self.logger.info("Listing modules")
        modules = self.facade.get_modules()
        return jsonify({"modules": modules})


    def get_exported_recordings(self):
        """Get list of exported recordings from controller share and NAS directories"""
        recordings = []

        # Get controller share recordings
        if self.habitat_share_dir.exists():
            for file in self.habitat_share_dir.glob('**/*'):
                if file.is_file() and file.suffix in ['.mp4', '.txt']:
                    recordings.append({
                        'filename': f"controller/{file.relative_to(self.habitat_share_dir)!s}",
                        'size': file.stat().st_size,
                        'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                        'is_exported': True,
                        'destination': 'controller'
                    })

        # Get NAS recordings (if mounted)
        nas_recordings = self.get_nas_recordings()
        recordings.extend(nas_recordings)

        return recordings


    def get_nas_recordings(self):
        """Get list of exported recordings from NAS"""
        recordings = []
        nas_mount_point = Path("/mnt/nas")

        self.logger.info("Scanning NAS for recordings...")

        # Try to mount NAS if not already mounted
        if not nas_mount_point.exists() or not nas_mount_point.is_mount():
            self.logger.info("NAS not mounted, attempting to mount...")
            if not self.mount_nas():
                self.logger.error("Failed to mount NAS, returning empty list")
                return recordings  # Return empty list if mounting failed

        self.logger.info(f"NAS is mounted at {nas_mount_point}")

        # Check what's in the root NAS directory
        if nas_mount_point.exists():
            root_contents = list(nas_mount_point.iterdir())
            self.logger.info(f"NAS root contents: {[item.name for item in root_contents]}")

            # Look specifically for export directories
            export_dirs = [item for item in root_contents if item.is_dir() and item.name.startswith('export_')]
            self.logger.info(f"Found export directories: {[item.name for item in export_dirs]}")
        else:
            self.logger.error(f"NAS mount point does not exist: {nas_mount_point}")
            return recordings

        # Scan multiple directories for recordings
        directories_to_scan = ["recordings", "videos", "ttl"]

        for dir_name in directories_to_scan:
            scan_path = nas_mount_point / dir_name
            self.logger.info(f"Looking for recordings in: {scan_path}")

            if scan_path.exists():
                self.logger.info(f"{dir_name} directory exists, scanning for files...")
                for file in scan_path.glob('**/*'):
                    self.logger.info(f"Found file: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"Adding file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{dir_name}/{file.relative_to(scan_path)!s}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })
            else:
                self.logger.info(f"{dir_name} directory does not exist: {scan_path}")

        # Also scan for export directories (like export_20250624_220253) in the root
        self.logger.info("Scanning for export directories in root...")
        for item in nas_mount_point.iterdir():
            self.logger.info(f"Checking item: {item.name} (is_dir: {item.is_dir()}, starts_with_export: {item.name.startswith('export_')})")
            if item.is_dir() and item.name.startswith('export_'):
                self.logger.info(f"Found export directory: {item}")
                for file in item.glob('**/*'):
                    self.logger.info(f"Found file in export directory: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"Adding export file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{item.name}/{file.relative_to(item)!s}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })

        self.logger.info(f"Found {len(recordings)} NAS recordings")
        return recordings


    def mount_nas(self):
        """Mount the NAS/export share defined in export.* controller config."""
        try:
            import subprocess

            nas_ip = self.config.get("export.share_ip", "")
            if not nas_ip:
                self.logger.warning("mount_nas: export.share_ip not configured")
                return False
            share_path = self.config.get("export.share_path", "controller_share")
            username = self.config.get("export.share_username", "")
            password = self.config.get("export.share_password", "")
            mount_point = Path("/mnt/controller_export")

            mount_point.mkdir(parents=True, exist_ok=True)
            if mount_point.is_mount():
                subprocess.run(["sudo", "umount", str(mount_point)], check=False)

            auth_opts = f"username={username},password={password}" if username else "guest"
            mount_cmd = [
                "sudo", "mount", "-t", "cifs",
                f"//{nas_ip}/{share_path}",
                str(mount_point),
                "-o", f"{auth_opts},uid=pi,gid=pi,file_mode=0664,dir_mode=0775,cache=none",
            ]

            result = subprocess.run(mount_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.logger.error(f"Failed to mount NAS: {result.stderr}")
                return False

            self.logger.info(f"Successfully mounted //{nas_ip}/{share_path} at {mount_point}")
            return True

        except Exception as e:
            self.logger.error(f"NAS mount failed: {e}")
            return False


    def _get_own_ip(self) -> str:
        """The controller's own eth0 address, read the same synchronous way
        handle_get_controller_info/handle_get_controller_health already do
        -- deliberately not self.network.ip (Network's zeroconf-driven IP
        detection), so this has no dependency on that timing and can be
        called immediately at startup."""
        try:
            result = subprocess.run(
                ["nmcli", "-g", "IP4.ADDRESS", "device", "show", "eth0"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return result.stdout.strip().split("/")[0] if result.returncode == 0 else ""
        except Exception:
            return ""

    def ensure_export_share_mounted(self) -> bool:
        """Keep export.mount_path in sync with wherever export.share_ip
        currently points, so the session-detail file browser/downloads --
        get_session_file_info/download_session_file/download_session_zip,
        all of which just read export.mount_path as a plain local directory
        -- see the same files modules actually exported, even when that's a
        remote NAS (e.g. habitat's //192.168.1.2/habitat_recording) rather
        than the controller's own share. Call whenever export.* changes
        (see save_controller_config/sync_export_to_all below) and once at
        startup so a reboot doesn't lose the mount.

        No-op (after unmounting any previous NAS mount) when export.share_ip
        is unset or is the controller's own address -- export.mount_path
        should then stay the controller's own local Samba-served directory
        ([controller_share] in smb.conf), not a CIFS mount on top of it;
        mounting a share onto its own backing directory would break the
        controller's own Samba server, not just be redundant.
        """
        default_mount_path = "/home/pi/controller_share"
        mount_point = Path(self.config.get("export.mount_path", default_mount_path))
        nas_ip = self.config.get("export.share_ip", "")
        is_remote = bool(nas_ip) and nas_ip != self._get_own_ip()

        if mount_point.is_mount():
            result = subprocess.run(
                ["sudo", "umount", str(mount_point)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                self.logger.warning(
                    f"Could not unmount {mount_point} before remount: {result.stderr}"
                )

        if not is_remote:
            return True

        share_path = self.config.get("export.share_path", "controller_share")
        username   = self.config.get("export.share_username", "")
        password   = self.config.get("export.share_password", "")
        mount_point.mkdir(parents=True, exist_ok=True)
        auth_opts = f"username={username},password={password}" if username else "guest"
        mount_opts = (
            f"{auth_opts},uid=pi,gid=pi,file_mode=0664,dir_mode=0775,cache=none"
        )
        result = subprocess.run(
            ["sudo", "mount", "-t", "cifs",
             f"//{nas_ip}/{share_path}", str(mount_point), "-o", mount_opts],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            self.logger.error(
                f"Failed to mount export share //{nas_ip}/{share_path} "
                f"at {mount_point}: {result.stderr}"
            )
            return False
        self.logger.info(
            f"Mounted export share //{nas_ip}/{share_path} "
            f"at {mount_point} for local browsing"
        )
        return True


    def handle_special_module_status(self, module_id, status):
        """To be overriden by rig specific functionality"""
        pass


    def handle_module_status(self, module_id, status):
        """Handle status update from a module and emit to frontend"""
        try:
            # Ensure status has required fields
            if not isinstance(status, dict):
                raise ValueError("Status must be a dictionary")

            status_type = status.get('type')
            if not status_type:
                self.logger.warning(f"Bad status type: {status}")

            match status_type:
                # Handle recordings list response
                case 'recordings_list':
                    self.logger.info(f"Broadcasting module recordings for module {module_id}")
                    module_recordings = status.get('recordings', [])

                    # Send individual module recordings response
                    self.socketio.emit('module_recordings', {
                        'module_id': module_id,
                        'recordings': module_recordings
                    })

                # Handle export complete response
                case 'export_complete':
                    self.logger.info(f"Broadcasting export complete for module {module_id}")
                    self.socketio.emit('export_complete', {
                        'module_id': module_id,
                        'success': status.get('success', False),
                        'error': status.get('error'),
                        'filename': status.get('filename')
                    })

                # Handle recording started/stopped status
                case ('recording_started' | 'recording_stopped'):
                    self.logger.info(f"Broadcasting recording status for module {module_id}")
                    self.socketio.emit('module_status', {
                        'module_id': module_id,
                        'status': status
                    })

                # The module itself detected it couldn't start/stop recording (e.g. a
                # racing double-start, or a module-specific stop failure). Previously
                # unmatched here — fell through to handle_special_module_status(),
                # which every non-APA variant treats as a no-op — so this was silently
                # dropped instead of reaching the operator. Route it into the same
                # session-fault path used for offline-module detection, which already
                # drives FaultAlertModal and the Teams alert.
                case ('recording_start_failed' | 'recording_stop_failed'):
                    error = status.get("error", "unknown error")
                    self.logger.warning(f"{status_type} for module {module_id}: {error}")
                    self.facade.report_module_fault(module_id, f"{status_type}: {error}")

                # A module's own self-monitor thread (Recording._monitor_recording_health)
                # reports its capture (e.g. a dead AudioMoth thread, a stalled camera
                # pipeline) looks unhealthy or has recovered. Softer than the fault path
                # above — surfaced as a session warning, not an ERROR.
                case "recording_health_warning":
                    health_status = status.get("status", "unhealthy")
                    message = status.get("message")
                    self.facade.handle_recording_health_status(module_id, health_status, message)

                # Generic failure path: Command._handle_error() sends this on any
                # unhandled exception (or unknown command) while executing a command.
                # Previously silently dropped the same way as above. Not escalated to
                # a session fault here — "error" covers every command, not just
                # recording ones, so blindly faulting the session would misfire on
                # unrelated failures (e.g. a failed trigger_autofocus). At minimum it
                # must stop disappearing: log it and hand it to the frontend.
                case "error":
                    error = status.get("error", "unknown error")
                    self.logger.warning(f"Module {module_id} reported an error: {error}")
                    self.socketio.emit('module_error', {
                        'module_id': module_id,
                        'error': error,
                    })

                # Camera-family status, common to every camera variant (CameraBase
                # itself, not a variant subclass) -- handled here directly rather
                # than delegated to handle_special_module_status, same reasoning as
                # recording_started/stopped above. Without this it fell to
                # case _ -> handle_special_module_status(), which every variant
                # (loom_controller.py etc.) either doesn't override or logs
                # "No logic for ..." and drops -- the crop editor's "Saving..."
                # status would never resolve to saved/failed.
                case "camera_crop_updated":
                    self.socketio.emit('module_status', {**status, 'module_id': module_id})

                # loom_camera_module.py's set_loom_roi() sends this directly via
                # communication.send_status() on a successful save. Previously
                # unmatched here -- fell to case _ -> handle_special_module_status(),
                # which loom_controller.py doesn't override for this type (logs
                # "No logic for loom_roi_updated" and drops), so
                # LoomRoiLineEditorModal.jsx's "Saving..." status never resolved to
                # "Saved" even on success.
                case "loom_roi_updated":
                    self.socketio.emit('module_status', {**status, 'module_id': module_id})

                case "heartbeat":
                    version = status.get("version")
                    if version:
                        self.facade.update_module_version(module_id, version)
                    self.facade.send_command(module_id, "heartbeat_ack", {})

                case "cmd_ack":
                    command = status.get("command")
                    if command == "get_sensor_modes":
                        self.socketio.emit("sensor_modes_response", {
                            "module_id": module_id,
                            "sensor_modes": status.get("sensor_modes", []),
                            "sensor_model": status.get("sensor_model", ""),
                            "has_autofocus": status.get("has_autofocus", False),
                        })
                    elif command == "list_audiomoths":
                        self.socketio.emit("audiomoth_list_response", {
                            "module_id": module_id,
                            "audiomoths": status.get("audiomoths", {}),
                        })
                    elif command == "update_saviour":
                        result = status.get("result")
                        if result in ("success", "error"):
                            self.socketio.emit("module_update_result", {
                                "module_id": module_id,
                                "success": result == "success",
                                "output": status.get("output", ""),
                            })
                    elif command == "run_mend":
                        result = status.get("result")
                        if result in ("success", "error", "reboot_required"):
                            self.socketio.emit("module_mend_result", {
                                "module_id": module_id,
                                "success": result != "error",
                                "reboot_required": result == "reboot_required",
                                "output": status.get("output", ""),
                            })
                    elif command == "shutdown":
                        self.socketio.emit("module_shutdown_ack", {"module_id": module_id})
                    elif command == "set_loom_roi" and status.get("status") == "error":
                        # set_loom_roi's validation failures (bad polygon/line, or a
                        # write error) come back as the command's own return value,
                        # not a communication.send_status() call, so they surface
                        # here as a cmd_ack rather than through the
                        # loom_roi_updated case above. Previously fell to the
                        # "no web-layer action" debug log below and was silently
                        # dropped -- translated into the shape
                        # LoomRoiLineEditorModal.jsx already listens for.
                        self.socketio.emit('module_status', {
                            'type': 'loom_roi_update_failed',
                            'module_id': module_id,
                            'error': status.get('error', 'unknown error'),
                        })
                    else:
                        self.logger.debug(f"cmd_ack for '{command}' from {module_id} — no web-layer action")

                case _:
                    was_special_status = self.handle_special_module_status(module_id, status)
                    if not was_special_status:
                        pass
        except Exception as e:
            self.logger.error(f"Error handling module status: {e!s}")


    def _register_rest_facade_routes(self):
        """
        REST API endpoints - for use by external services e.g. a Matlab script running an experiment that wants to start recordings
        """
        @self.app.route('/facade/list_modules', methods=['GET'])
        def list_modules():
            if not self._check_bearer_auth():
                return jsonify({
                    "error": "Unauthorized -- provide the admin password via "
                             "an 'Authorization: Bearer <password>' header"
                }), 401
            self.logger.info("/facade/list_modules endpoint called. Listing modules")
            modules = self.facade.get_modules()
            self.logger.info(f"Found {len(modules)} modules")
            return jsonify({"modules": modules})


        @self.app.route('/facade/send_command', methods=['POST'])
        def send_command():
            """
            Send a command to a module.  Requires the admin password (the
            same shared credential used to log into the web UI) via an
            Authorization header -- this endpoint can dispatch arbitrary
            commands including shutdown/reboot/start_recording, and unlike
            the Socket.IO handlers it has no browser session to check.

            Request format:
            {
                "command": "string",  # The command to execute
                "module_id": "string", # The module ID or "all"
                "params": {           # Optional parameters
                    "key": "value"
                }
            }

            Example:
            curl -X POST http://192.168.0.98:5000/facade/send_command \\
                -H "Content-Type: application/json" \\
                -H "Authorization: Bearer <admin password>" \\
                -d "{\"command\":\"start_recording\",\"module_id\":\"all\"}"
            """
            if not self._check_bearer_auth():
                return jsonify({
                    "error": "Unauthorized -- provide the admin password via "
                             "an 'Authorization: Bearer <password>' header"
                }), 401

            try:
                if not request.is_json:
                    return jsonify({
                        "error": "Request must be JSON",
                        "content_type": request.content_type,
                        "example": {
                            "command": "start_recording",
                            "module_id": "all"
                        }
                    }), 400

                data = request.get_json(force=True)
                self.logger.info(f"Received command request: {data}")

                command = data.get('command')
                module_id = data.get('module_id')
                params = data.get('params', {})

                if not command or not module_id:
                    return jsonify({
                        "error": "Missing required fields",
                        "required": ["command", "module_id"],
                        "received": {
                            "command": command,
                            "module_id": module_id
                        }
                    }), 400

                self.logger.info(f"Processing command: {command} for module: {module_id}")

                result = self.facade.send_command(module_id, command, params)
                return jsonify({
                    "status": "success",
                    "message": "Command sent successfully",
                    "command": command,
                    "module_id": module_id
                })

            except Exception as e:
                self.logger.error(f"Error in send_command endpoint: {e!s}")
                return jsonify({
                    "error": str(e),
                    "status": "error"
                }), 500


        @self.app.route('/facade/module_health', methods=['GET'])
        def module_health():
            """Get the health status of all modules"""
            if not self._check_bearer_auth():
                return jsonify({
                    "error": "Unauthorized -- provide the admin password via "
                             "an 'Authorization: Bearer <password>' header"
                }), 401
            self.logger.info("/facade/module_health endpoint called. Getting module health")
            health = self.facade.get_module_health()
            self.logger.info(f"Got module health for {len(health)} modules")
            return jsonify(health)


        @self.app.route('/facade/exported_recordings', methods=['GET'])
        def get_exported_recordings_facade():
            """Get list of exported recordings"""
            if not self._check_bearer_auth():
                return jsonify({
                    "error": "Unauthorized -- provide the admin password via "
                             "an 'Authorization: Bearer <password>' header"
                }), 401
            self.logger.info("/facade/exported_recordings endpoint called")
            exported_recordings = self.get_exported_recordings()
            return jsonify({"exported_recordings": exported_recordings})
