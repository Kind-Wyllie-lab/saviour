#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
import logging
import secrets
import subprocess
import time
import zipfile
from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
from flask_socketio import SocketIO
from typing import Any
import threading
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from abc import ABC
from dataclasses import asdict

from src.controller.config import Config


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

        # Flask setup
        self.app = Flask(__name__, static_folder="frontend/dist", static_url_path="/")
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


    def _check_nas_free_space(self) -> "str | None":
        """Mount the NAS and check free space against nas_min_free_pct.

        Returns None if the share is reachable with sufficient space, or an
        error string for surfacing to the user.  Returns None immediately if no
        NAS IP is configured.
        """
        import subprocess, shutil as _shutil
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
        from datetime import datetime, timezone

        metadata = {
            "session_name": session_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
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
        # Serve React app
        @self.app.route("/", defaults={"path": ""})
        @self.app.route("/<path>")
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

            # Send initial module list
            modules = self.facade.get_modules()
            self.logger.info(f"Page load get_modules() returned: {modules}, sending {len(modules)} modules to new client")
            self.socketio.emit('module_update', {"modules": modules})
            
            # Send current experiment name to new client
            if self.current_experiment_name:
                self.socketio.emit('experiment_name_update', {"experiment_name": self.current_experiment_name})
                self.logger.info(f"Sent current experiment name to new client: {self.current_experiment_name}")


        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.logger.info(f"Client disconnected")
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
                self.logger.error(f"Error handling command: {str(e)}")
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
                self.logger.error(f"Error starting recording: {str(e)}")
                self.socketio.emit('error', {'message': str(e)})

        @self.socketio.on("stop_recording")
        def stop_recording(data):
            if not self._require_auth("auth_required"):
                return
            try:
                target = data.get("target")
                self.facade.stop_recording(target)
            except Exception as e:
                self.logger.error(f"Error stopping recording: {str(e)}")
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
            target = data.get("target")
            self.facade.send_command(target, "get_health", {})
            self.facade.send_command(target, "validate_readiness", {})
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

        @self.app.route("/api/sessions/<session_name>/download/<path:filename>")
        def download_session_file(session_name, filename):
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
            if not os.path.isfile(safe_path):
                return "Not found", 404
            return send_file(safe_path, as_attachment=True, download_name=os.path.basename(safe_path))

        @self.app.route("/api/sessions/<session_name>/download")
        def download_session_zip(session_name):
            import re
            if not re.fullmatch(r"[A-Za-z0-9_\-]+", session_name):
                return "Invalid session name", 400
            share = os.path.realpath(self.config.get("export.mount_path", "/home/pi/controller_share"))
            session_dir = os.path.realpath(os.path.join(share, session_name))
            if not session_dir.startswith(share + os.sep):
                return "Forbidden", 403
            if not os.path.isdir(session_dir):
                return "Not found", 404

            q = _queue.SimpleQueue()

            def _build():
                try:
                    with zipfile.ZipFile(_QueueStream(q), 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
                        for root, dirs, filenames in os.walk(session_dir):
                            dirs.sort()
                            for fn in sorted(filenames):
                                full = os.path.join(root, fn)
                                zf.write(full, os.path.relpath(full, session_dir))
                except Exception as e:
                    self.logger.error(f"ZIP stream error for '{session_name}': {e}")
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
                    "Content-Disposition": f'attachment; filename="{session_name}.zip"',
                    "X-Accel-Buffering": "no",
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
            result = self.facade.force_start_scheduled_session(session_name)
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
            self.logger.info(f"Received request to delete session '{session_name}' (delete_files={delete_files})")
            result = self.facade.delete_session(session_name, delete_files)
            if "error" in result:
                self.socketio.emit("session_error", {"error": result["error"]})

        @self.socketio.on("clear_ended_sessions")
        def handle_clear_ended_sessions(data):
            if not self._require_auth("session_error", {"error": "Login required for this action"}):
                return
            delete_files = data.get("delete_files", False) if data else False
            self.logger.info(f"Received request to clear ended sessions (delete_files={delete_files})")
            self.facade.clear_ended_sessions(delete_files)

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
                self.logger.error(f"Error handling module status: {str(e)}")
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
            self.logger.info(f"Get module configs called")
            self.facade.get_module_configs()


        @self.socketio.on('save_module_config')
        def handle_save_module_config(data):
            """Handle save module config from frontend"""
            if not self._require_auth("auth_required"):
                return
            module_id = data['id']
            config = _filter_private_keys(data.get("config", {}))
            self.logger.info(f"Received request to save config to module {module_id} with data {config}")

            camera_section = config.get("camera", {})
            new_sync_mode = camera_section.get("sync_mode")

            # When configuring a camera as a sync client, pin its fps and sensor_mode_index
            # to match the sync server so frame synchronisation can work correctly.
            if new_sync_mode == "client":
                server_params = self.facade.get_sync_server_camera_params()
                if server_params:
                    camera_section["fps"] = server_params["fps"]
                    camera_section["sensor_mode_index"] = server_params["sensor_mode_index"]
                    config["camera"] = camera_section
                    self.logger.info(
                        f"Pinned {module_id} to sync server {server_params['module_id']}: "
                        f"fps={server_params['fps']} sensor_mode_index={server_params['sensor_mode_index']}"
                    )
                else:
                    self.logger.warning(
                        f"sync_mode=client set for {module_id} but no sync server found — "
                        "fps/sensor_mode_index not auto-pinned"
                    )

            # When saving the sync server, propagate its fps and sensor_mode_index to all clients.
            elif new_sync_mode == "server":
                fps = camera_section.get("fps")
                sensor_mode_index = camera_section.get("sensor_mode_index")
                if fps is not None or sensor_mode_index is not None:
                    client_ids = self.facade.get_sync_client_camera_ids()
                    all_configs = self.facade.get_module_configs()
                    for client_id in client_ids:
                        client_true = dict((all_configs.get(client_id) or {}).get("true_config") or {})
                        client_camera = dict(client_true.get("camera", {}))
                        if fps is not None:
                            client_camera["fps"] = fps
                        if sensor_mode_index is not None:
                            client_camera["sensor_mode_index"] = sensor_mode_index
                        client_true["camera"] = client_camera
                        self.logger.info(
                            f"Propagating server fps/sensor_mode_index to sync client {client_id}"
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
            else:
                creds = self.facade.get_export_credentials()

            modules = self.facade.get_modules()
            results = {}
            for module_id in modules:
                results[module_id] = self.facade.sync_export_with_creds(module_id, creds)
            success_count = sum(1 for r in results.values() if r.get("success"))
            self.socketio.emit("export_sync_all_result", {
                "results": results,
                "success_count": success_count,
                "total": len(results),
            })

        @self.socketio.on('get_controller_samba_info')
        def handle_get_controller_samba_info(data=None):
            """Return this controller's own Samba share info for the 'Controller Share' preset."""
            info = self.facade.get_controller_own_share_info()
            self.socketio.emit("controller_samba_info_response", info)

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
            self.facade.set_config(_filter_private_keys(data.get("config", {})))
            self.socketio.emit("controller_config_response", {
                "config": self.facade.get_config()
            })


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
            from datetime import datetime, timezone as _tz
            health['controller_time'] = datetime.now(_tz.utc).isoformat()
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
            _emit("update_info", {"running_version": running, "staged": staged})

        @self.socketio.on("upload_update_start")
        def handle_upload_update_start(data):
            from flask_socketio import emit as _emit
            if not self._require_auth("upload_update_error", {"error": "Login required for this action"}):
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
            from flask_socketio import emit as _emit
            import zipfile, io, re
            if not self._require_auth("upload_update_error", {"error": "Login required for this action"}):
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
            import zipfile, shutil, re
            if not self._require_auth("deploy_update_error", {"error": "Login required for this action"}):
                return
            if not os.path.exists(_UPDATE_ZIP):
                _emit("deploy_update_error", {"error": "No update staged — upload a package first"})
                return

            controller_ip = getattr(self.facade, 'get_controller_ip',
                                    lambda: None)() or "localhost"
            try:
                controller_ip = self.facade.controller.network.ip
            except Exception:
                pass
            controller_url = f"http://{controller_ip}:5000"

            # Send to modules first so they update in parallel while controller applies
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

            def _apply_to_controller():
                try:
                    extract_dir = "/tmp/saviour_update"
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    os.makedirs(extract_dir)
                    with zipfile.ZipFile(_UPDATE_ZIP) as z:
                        z.extractall(extract_dir)
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
                except Exception as e:
                    self.logger.error(f"Controller update failed: {e}")
                    self.socketio.emit("deploy_update_error", {"error": str(e)})
                    return
                self.logger.info("Update applied — restarting controller service")
                time.sleep(2)
                subprocess.Popen(["sudo", "systemctl", "restart", "saviour.service"])

            threading.Thread(target=_apply_to_controller, daemon=True,
                             name="saviour-deploy").start()

        @self.socketio.on("stage_current_version")
        def handle_stage_current_version(data=None):
            from flask_socketio import emit as _emit
            import zipfile as _zf
            if not self._require_auth("upload_update_error", {"error": "Login required for this action"}):
                return
            _SKIP_DIRS = {'.git', 'env', '__pycache__', 'node_modules',
                          '.pytest_cache', 'dist', '.eggs'}

            def _do_stage():
                src_root = "/usr/local/src/saviour"
                version = _read_running_version()
                self.logger.info(f"Staging current version {version} from {src_root}")
                try:
                    os.makedirs(_UPDATE_STORE, exist_ok=True)
                    tmp = _UPDATE_ZIP + ".tmp"
                    skipped = 0
                    with _zf.ZipFile(tmp, "w", _zf.ZIP_DEFLATED,
                                     compresslevel=1) as zf:
                        for dirpath, dirnames, filenames in os.walk(src_root):
                            dirnames[:] = [
                                d for d in dirnames
                                if d not in _SKIP_DIRS
                                and not d.endswith('.egg-info')
                            ]
                            for filename in filenames:
                                if filename.endswith('.pyc'):
                                    continue
                                abs_path = os.path.join(dirpath, filename)
                                rel_path = os.path.relpath(abs_path, src_root)
                                try:
                                    zf.write(abs_path, rel_path)
                                except Exception as _fe:
                                    self.logger.warning(
                                        f"Skipping {rel_path}: {_fe}"
                                    )
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
                    self.socketio.emit("upload_update_complete", meta)
                except Exception as e:
                    self.logger.error(f"Stage current version failed: {e}")
                    self.socketio.emit("upload_update_error", {"error": str(e)})

            threading.Thread(target=_do_stage, daemon=True,
                             name="saviour-stage").start()

        @self.socketio.on("deploy_update_to_module")
        def handle_deploy_update_to_module(data):
            from flask_socketio import emit as _emit
            if not self._require_auth("deploy_update_error", {"error": "Login required for this action"}):
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
            def _run():
                success, detail = self.facade.controller.notifier.send_test()
                self.socketio.emit("teams_test_result", {"success": success, "detail": detail})
            threading.Thread(target=_run, daemon=True, name="teams-test").start()

        @self.socketio.on("get_bug_report")
        def handle_get_bug_report(data=None):
            self.logger.info("Bug report requested")
            threading.Thread(target=self._collect_bug_report, daemon=True).start()

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
            from datetime import datetime, timezone as _tz
            if not self._require_auth("auth_required"):
                return
            self.logger.info("Set controller time requested")
            ntp_was_enabled = False
            try:
                iso = (data or {}).get("iso", "")
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_tz.utc)
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
                self.logger.error(f"Error getting exported recordings: {str(e)}")
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
            self.logger.info(f"Received request for debug data")
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

    def _collect_bug_report(self) -> None:
        """Background thread: gather logs from controller + all online modules, emit download token."""
        self.socketio.emit("bug_report_status", {"status": "collecting"})

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

        # Collect controller logs
        try:
            ctrl_log_result = subprocess.run(
                ["journalctl", "-u", "saviour.service", "-n", "500",
                 "--no-pager", "--output=short-precise"],
                capture_output=True, text=True, timeout=10,
            )
            ctrl_logs = ctrl_log_result.stdout if ctrl_log_result.returncode == 0 else ctrl_log_result.stderr
        except Exception as e:
            ctrl_logs = f"Could not collect controller logs: {e}"

        # Build ZIP in memory
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"saviour_diagnostics_{ts}/controller/logs.txt", ctrl_logs)

            ctrl_config = _sanitise_config_dict(self.facade.get_config() if self.facade else {})
            zf.writestr(f"saviour_diagnostics_{ts}/controller/config.json",
                        json.dumps(ctrl_config, indent=2, default=str))

            health = self.facade.get_module_health() if self.facade else {}
            zf.writestr(f"saviour_diagnostics_{ts}/controller/health.json",
                        json.dumps(health, indent=2, default=str))

            sessions = self.facade.get_recording_sessions() if self.facade else {}
            zf.writestr(f"saviour_diagnostics_{ts}/controller/sessions.json",
                        json.dumps(sessions, indent=2, default=str))

            offline_ids = [mid for mid, m in modules.items() if not m.get('online')]

            for mid in online_ids:
                data = pending[mid].get('data')
                if data:
                    zf.writestr(f"saviour_diagnostics_{ts}/modules/{mid}/logs.txt",
                                data.get('logs', '(no logs)'))
                    cfg = _sanitise_config_dict(data.get('config', {}))
                    zf.writestr(f"saviour_diagnostics_{ts}/modules/{mid}/config.json",
                                json.dumps(cfg, indent=2, default=str))
                else:
                    zf.writestr(f"saviour_diagnostics_{ts}/modules/{mid}/logs.txt",
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
        self.socketio.emit("bug_report_ready", {"token": token, "filename": f"saviour_diagnostics_{ts}.zip"})

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
                        'filename': f"controller/{str(file.relative_to(self.habitat_share_dir))}",
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
        
        self.logger.info(f"Scanning NAS for recordings...")
        
        # Try to mount NAS if not already mounted
        if not nas_mount_point.exists() or not nas_mount_point.is_mount():
            self.logger.info(f"NAS not mounted, attempting to mount...")
            if not self.mount_nas():
                self.logger.error(f"Failed to mount NAS, returning empty list")
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
                            'filename': f"nas/{dir_name}/{str(file.relative_to(scan_path))}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })
            else:
                self.logger.info(f"{dir_name} directory does not exist: {scan_path}")
        
        # Also scan for export directories (like export_20250624_220253) in the root
        self.logger.info(f"Scanning for export directories in root...")
        for item in nas_mount_point.iterdir():
            self.logger.info(f"Checking item: {item.name} (is_dir: {item.is_dir()}, starts_with_export: {item.name.startswith('export_')})")
            if item.is_dir() and item.name.startswith('export_'):
                self.logger.info(f"Found export directory: {item}")
                for file in item.glob('**/*'):
                    self.logger.info(f"Found file in export directory: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"Adding export file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{item.name}/{str(file.relative_to(item))}",
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
                    elif command == "shutdown":
                        self.socketio.emit("module_shutdown_ack", {"module_id": module_id})
                    else:
                        self.logger.debug(f"cmd_ack for '{command}' from {module_id} — no web-layer action")

                case _:
                    was_special_status = self.handle_special_module_status(module_id, status)
                    if not was_special_status:
                        pass
        except Exception as e:
            self.logger.error(f"Error handling module status: {str(e)}")


    def _register_rest_facade_routes(self):
        """
        REST API endpoints - for use by external services e.g. a Matlab script running an experiment that wants to start recordings
        """
        @self.app.route('/facade/list_modules', methods=['GET'])
        def list_modules():
            self.logger.info(f"/facade/list_modules endpoint called. Listing modules")
            modules = self.facade.get_modules()
            self.logger.info(f"Found {len(modules)} modules")
            return jsonify({"modules": modules})


        @self.app.route('/facade/send_command', methods=['POST'])
        def send_command():
            """
            Send a command to a module.
            
            Request format:
            {
                "command": "string",  # The command to execute
                "module_id": "string", # The module ID or "all"
                "params": {           # Optional parameters
                    "key": "value"
                }
            }
            
            Example:
            curl -X POST http://192.168.0.98:5000/facade/send_command -H "Content-Type: application/json" -d "{\"command\":\"start_recording\",\"module_id\":\"all\"}"
            """
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
                self.logger.error(f"Error in send_command endpoint: {str(e)}")
                return jsonify({
                    "error": str(e),
                    "status": "error"
                }), 500
                

        @self.app.route('/facade/module_health', methods=['GET'])
        def module_health():
            """Get the health status of all modules"""
            self.logger.info(f"/facade/module_health endpoint called. Getting module health")
            health = self.facade.get_module_health()
            self.logger.info(f"Got module health for {len(health)} modules")
            return jsonify(health)


        @self.app.route('/facade/exported_recordings', methods=['GET'])
        def get_exported_recordings_facade():
            """Get list of exported recordings"""
            self.logger.info("/facade/exported_recordings endpoint called")
            exported_recordings = self.get_exported_recordings()
            return jsonify({"exported_recordings": exported_recordings})
