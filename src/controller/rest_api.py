#!/usr/bin/env python3
"""
Controller REST API (v1)

A resource-oriented HTTP API for external programs on the lab LAN -- an
experiment controller (pyControl, MATLAB, a bespoke acquisition script)
that needs to read system state or start/stop recordings without driving
the Socket.IO web UI.

Mounted at /api/v1 as a Flask blueprint from web.py. Every route (reads
included) authenticates with a bearer token:

    Authorization: Bearer <secret>

where <secret> is either the shared admin password (the same credential
the web UI login uses -- `sudo cat /etc/saviour/admin_credentials`) or a
named API token minted via POST /api/v1/tokens. A token minted with
{"readonly": true} may call GET routes only (a fat-finger guard for
monitoring scripts). The /api/v1/tokens management routes themselves
require the admin password specifically, so a leaked token cannot mint
more. The controller serves plain HTTP, so the token crosses the LAN in
the clear, exactly as the web UI login already does; the project's
threat model treats the LAN as the trust boundary (see CLAUDE.md
"Project status & threat model"). Do not expose the controller off-LAN.

Both this API and the Socket.IO handlers call the same ControllerFacade
methods -- the REST layer is deliberately thin so the two entry points
cannot drift apart. Session creation additionally reuses web.py's
_check_nas_free_space() preflight and _write_session_metadata().

Response conventions:
  * Success -> the resource as bare JSON (200; 201 for a created session).
  * Failure -> {"error": {"code": <slug>, "message": <text>, ...}} with a
    matching HTTP status:
        400 invalid request      401 bad/missing bearer token
        404 unknown module/session
        409 conflict (module already recording, PTP not synced,
            session not in a state that allows the action)
        503 the export share is configured but unreachable

Scope: read state, a readiness gate, an SSE event stream, the recording
lifecycle for plain sessions, event markers (write + read-back), an
OpenAPI spec, and API-token management. Arbitrary module commands are
intentionally NOT here -- the pre-existing POST /facade/send_command
still covers that escape hatch. Scheduled and Habitat session
*creation*, config writes and module management are candidates for a
later version.
"""

import json
import logging
import os
import queue
from collections import Counter
from dataclasses import asdict
from functools import wraps

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def _running_version() -> str:
    """Running version string from src/__version__.py (pre-commit-hook
    written, travels inside ZIP deploys). Mirrors notify.py's helper."""
    try:
        from src.__version__ import __version__
        return __version__ or "unknown"
    except Exception:
        return "unknown"


def create_api_blueprint(web) -> Blueprint:
    """Build the /api/v1 blueprint bound to a Web instance.

    A fresh Blueprint per call (so multiple Web instances in a test run
    don't collide). `web` supplies:
      * web.facade                 -- the shared internal API
      * web.config                 -- for the PTP start-gate threshold
      * web._check_bearer_auth()   -- Authorization: Bearer check
      * web._check_nas_free_space()-- export-share preflight
      * web._write_session_metadata()
      * web.get_exported_recordings()
      * web._nas_health            -- last cached share probe (no mount)
    """
    bp = Blueprint("rest_api_v1", __name__, url_prefix=API_PREFIX)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _error(code: str, message: str, status: int, **extra):
        body = {"code": code, "message": message}
        body.update(extra)
        return jsonify({"error": body}), status

    def require_auth(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            kind = web._bearer_auth_kind()
            if kind is None:
                return _error(
                    "unauthorized",
                    "Provide the admin password or an API token as an "
                    "'Authorization: Bearer <secret>' header",
                    401,
                )
            if kind == "token_readonly" and request.method != "GET":
                return _error(
                    "forbidden", "This API token is read-only", 403)
            return fn(*args, **kwargs)
        return wrapper

    def require_admin(fn):
        """Admin password only -- not an API token. For the token
        management routes."""
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not web._check_admin_bearer():
                return _error(
                    "unauthorized",
                    "This route requires the admin password (not an API "
                    "token) as an 'Authorization: Bearer <password>' header",
                    401,
                )
            return fn(*args, **kwargs)
        return wrapper

    def _sessions() -> dict:
        return web.facade.get_recording_sessions()

    def _session_dict(name: str) -> dict:
        return asdict(_sessions()[name])

    def _ptp_gate_ns() -> int:
        return int(web.config.get("recording.ptp_start_gate_us", 50)) * 1000

    def _worst_ptp_ns():
        try:
            return web.facade.get_ptp_sync()
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # index (unauthenticated -- discloses no state)
    # ------------------------------------------------------------------ #

    @bp.get("/")
    def index():
        return jsonify({
            "name": "SAVIOUR controller REST API",
            "version": "v1",
            "auth": "Authorization: Bearer <admin password or API token>",
            "openapi": f"{API_PREFIX}/openapi.json",
            "docs": "docs/REST_API.md",
            "endpoints": sorted(
                rule.rule for rule in web.app.url_map.iter_rules()
                if rule.rule.startswith(API_PREFIX)
            ),
        })

    @bp.get("/openapi.json")
    def openapi():
        """The hand-maintained OpenAPI 3.1 spec (docs/openapi.yaml),
        rendered to JSON. Open, like the index."""
        spec_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docs", "openapi.yaml")
        try:
            import yaml
            with open(spec_path) as f:
                return jsonify(yaml.safe_load(f))
        except FileNotFoundError:
            return _error("not_found", "openapi.yaml not found", 404)
        except Exception as exc:  # PyYAML missing / parse error
            return _error(
                "unavailable",
                f"Could not render the spec ({exc}); see docs/openapi.yaml",
                503)

    # ------------------------------------------------------------------ #
    # state / health / ptp
    # ------------------------------------------------------------------ #

    @bp.get("/state")
    @require_auth
    def get_state():
        sessions = _sessions()
        counts = Counter(str(s.state) for s in sessions.values())
        summary = web.facade.get_health_summary()
        worst = _worst_ptp_ns()
        gate_ns = _ptp_gate_ns()
        return jsonify({
            "version": _running_version(),
            "uptime_s": web.facade.get_uptime(),
            "recording": web.facade.get_recording_status(),
            "sessions": {
                "total": len(sessions),
                "active": counts.get("active", 0),
                "pending": counts.get("pending", 0),
                "scheduled": counts.get("scheduled", 0),
                "paused": counts.get("paused", 0),
                "stopped": counts.get("stopped", 0),
                "error": counts.get("error", 0),
            },
            "modules": {
                "total": summary.get("total_modules", 0),
                "online": summary.get("online_modules", 0),
                "offline": summary.get("offline_modules", 0),
            },
            "ptp": {
                "worst_offset_ns": worst,
                "start_gate_ns": gate_ns,
                "synced": worst is not None and worst <= gate_ns,
            },
            "disk": web._nas_health,
        })

    @bp.get("/readiness")
    @require_auth
    def readiness():
        """One call answering "can I start recording on this target right
        now, and if not, why". Assembled from the same checks
        create_session runs. `?target=` (default "all"). The `share`
        check reads the controller's cached NAS health (no live mount);
        create_session still does an authoritative live probe."""
        target = request.args.get("target", "all")
        modules = web.facade.get_modules_by_target(target)
        mod_ids = list(modules.keys())
        checks: dict = {}

        checks["modules_present"] = {
            "ok": bool(mod_ids),
            "detail": (f"{len(mod_ids)} module(s) match '{target}'" if mod_ids
                       else f"no modules match target '{target}'"),
        }

        offline = sorted(
            m for m, d in modules.items()
            if not (d.get("online") or d.get("status") == "online"))
        checks["modules_online"] = {
            "ok": bool(mod_ids) and not offline,
            "detail": "all online" if not offline
                      else f"offline: {', '.join(offline)}",
        }

        ptp = web.facade.check_ptp_sync(target)
        checks["ptp"] = {
            "ok": bool(ptp.get("ok")),
            "worst_offset_us": ptp.get("max_offset_us"),
            "gate_us": ptp.get(
                "threshold_us",
                web.config.get("recording.ptp_start_gate_us", 50)),
            "detail": ptp.get("error"),
        }

        nas = web._nas_health or {}
        nas_status = nas.get("status", "unknown")
        checks["share"] = {
            "ok": nas_status in ("ok", "warn"),
            "status": nas_status,
            "free_pct": nas.get("free_pct"),
            "detail": nas.get("error") or (
                None if nas_status in ("ok", "warn")
                else "export share health not yet sampled"),
        }

        busy = web._recording_module_ids(mod_ids) if mod_ids else []
        checks["not_already_recording"] = {
            "ok": not busy,
            "detail": "idle" if not busy
                      else f"already recording: {', '.join(sorted(busy))}",
        }

        return jsonify({
            "target": target,
            "ready": all(c["ok"] for c in checks.values()),
            "checks": checks,
        })

    @bp.get("/health")
    @require_auth
    def get_health():
        return jsonify({
            "summary": web.facade.get_health_summary(),
            "modules": web.facade.get_module_health(),
        })

    @bp.get("/ptp")
    @require_auth
    def get_ptp():
        health = web.facade.get_module_health() or {}
        modules = {
            mid: {
                "ptp4l_offset_ns": h.get("ptp4l_offset_ns"),
                "phc2sys_offset_ns": h.get("phc2sys_offset_ns"),
                "ptp4l_freq": h.get("ptp4l_freq"),
            }
            for mid, h in health.items()
        }
        worst = _worst_ptp_ns()
        gate_ns = _ptp_gate_ns()
        return jsonify({
            "worst_offset_ns": worst,
            "start_gate_ns": gate_ns,
            "synced": worst is not None and worst <= gate_ns,
            "modules": modules,
        })

    # ------------------------------------------------------------------ #
    # event stream (Server-Sent Events)
    # ------------------------------------------------------------------ #

    @bp.get("/events")
    @require_auth
    def events():
        """A text/event-stream of typed controller events so a caller
        doesn't have to poll: `sessions` (full snapshot on any session
        change), `modules` (registry change), `alert` (typed fault --
        module offline, PTP degraded, export stall, low disk...),
        `marker` (an accepted marker). Optional `?types=a,b` filter.

        Each subscriber holds a worker thread for the life of the
        connection -- keep the number of concurrent subscribers small on
        a controller serving a lab.
        """
        wanted = None
        raw = request.args.get("types")
        if raw:
            wanted = {t.strip() for t in raw.split(",") if t.strip()}

        q = web._event_subscribe()

        def stream():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    if wanted and event.get("type") not in wanted:
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                web._event_unsubscribe(q)

        return Response(stream(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        })

    # ------------------------------------------------------------------ #
    # modules
    # ------------------------------------------------------------------ #

    @bp.get("/modules")
    @require_auth
    def list_modules():
        return jsonify({"modules": web.facade.get_modules()})

    @bp.get("/modules/<module_id>")
    @require_auth
    def get_module(module_id):
        modules = web.facade.get_modules()
        if module_id not in modules:
            return _error("not_found", f"Unknown module '{module_id}'", 404)
        return jsonify(modules[module_id])

    @bp.get("/modules/<module_id>/health")
    @require_auth
    def get_one_module_health(module_id):
        if module_id not in web.facade.get_modules():
            return _error("not_found", f"Unknown module '{module_id}'", 404)
        return jsonify(web.facade.get_module_health(module_id) or {})

    # ------------------------------------------------------------------ #
    # exports
    # ------------------------------------------------------------------ #

    @bp.get("/exports")
    @require_auth
    def get_exports():
        sessions = list(_sessions().values())
        return jsonify({
            "exported_recordings": web.get_exported_recordings(),
            "pending_exports": sum(
                getattr(s, "pending_exports", 0) for s in sessions),
            "failed_exports": sum(
                getattr(s, "total_exports_failed", 0) for s in sessions),
        })

    # ------------------------------------------------------------------ #
    # sessions -- read
    # ------------------------------------------------------------------ #

    @bp.get("/sessions")
    @require_auth
    def list_sessions():
        return jsonify(
            {"sessions": {n: asdict(s) for n, s in _sessions().items()}})

    @bp.get("/sessions/<session_name>")
    @require_auth
    def get_session(session_name):
        if session_name not in _sessions():
            return _error(
                "not_found", f"Unknown session '{session_name}'", 404)
        return jsonify(_session_dict(session_name))

    # ------------------------------------------------------------------ #
    # sessions -- lifecycle
    # ------------------------------------------------------------------ #

    @bp.post("/sessions")
    @require_auth
    def create_session():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return _error("invalid_request", "'name' is required", 400)
        target = data.get("target") or "all"

        nas_error = web._check_nas_free_space()
        if nas_error:
            return _error("share_unavailable", nas_error, 503)

        result = web.facade.create_session(
            name, target,
            data.get("duration_minutes"),
            data.get("researcher") or None,
            unattended=bool(data.get("unattended")),
        )
        if not result or not result.get("success"):
            return _error(
                "session_rejected",
                (result or {}).get("error", "Could not create session"),
                409,
            )

        session_name = result["session_name"]
        web._write_session_metadata(session_name, target)

        body = _session_dict(session_name)
        if data.get("autostart"):
            start = web.facade.force_start_session(session_name)
            body = _session_dict(session_name)
            if not start or not start.get("success"):
                body["autostart_error"] = (start or {}).get(
                    "error", "Could not start session")
        return jsonify(body), 201

    @bp.post("/sessions/<session_name>/stop")
    @require_auth
    def stop_session(session_name):
        if session_name not in _sessions():
            return _error(
                "not_found", f"Unknown session '{session_name}'", 404)
        web.facade.stop_session(session_name)
        return jsonify(_session_dict(session_name))

    @bp.post("/sessions/<session_name>/pause")
    @require_auth
    def pause_session(session_name):
        if session_name not in _sessions():
            return _error(
                "not_found", f"Unknown session '{session_name}'", 404)
        result = web.facade.pause_session(session_name)
        if not result or not result.get("success"):
            return _error(
                "pause_rejected",
                (result or {}).get("error", "Could not pause session"),
                409,
            )
        return jsonify(_session_dict(session_name))

    @bp.post("/sessions/<session_name>/resume")
    @require_auth
    def resume_session(session_name):
        if session_name not in _sessions():
            return _error(
                "not_found", f"Unknown session '{session_name}'", 404)
        result = web.facade.resume_session(session_name)
        if not result or not result.get("success"):
            return _error(
                "resume_rejected",
                (result or {}).get("error", "Could not resume session"),
                409,
            )
        return jsonify(_session_dict(session_name))

    @bp.post("/sessions/<session_name>/marker")
    @require_auth
    def add_marker(session_name):
        """Append a labelled event marker to the session's markers.csv.
        Body: {"label": str (required), "source"?: str, "t"?: number}
        where `t` is the caller's own epoch time for the event, in
        seconds (as from time.time()). The controller's receive time is
        always recorded too."""
        if session_name not in _sessions():
            return _error(
                "not_found", f"Unknown session '{session_name}'", 404)
        data = request.get_json(silent=True) or {}
        label = str(data.get("label") or "").strip()
        if not label:
            return _error("invalid_request", "'label' is required", 400)

        client_wall_ns = None
        if data.get("t") is not None:
            try:
                client_wall_ns = int(float(data["t"]) * 1_000_000_000)
            except (TypeError, ValueError):
                return _error(
                    "invalid_request",
                    "'t' must be a number (epoch seconds)", 400)

        result = web.facade.add_marker(
            session_name, label, data.get("source") or None, client_wall_ns)
        if not result.get("success"):
            return _error(
                "marker_rejected",
                result.get("error", "Could not record marker"), 409)
        web._publish_api_event("marker", {
            "session": session_name, "label": label,
            "recv_wall_ns": result["recv_wall_ns"],
        })
        return jsonify(result), 201

    @bp.get("/sessions/<session_name>/markers")
    @require_auth
    def get_markers(session_name):
        """Read back the session's markers.csv as JSON. `?since=` (epoch
        seconds) filters to markers at/after that time; `?limit=` keeps
        only the most recent N."""
        if session_name not in _sessions():
            return _error(
                "not_found", f"Unknown session '{session_name}'", 404)
        since_ns = None
        if request.args.get("since"):
            try:
                since_ns = int(float(request.args["since"]) * 1_000_000_000)
            except ValueError:
                return _error(
                    "invalid_request",
                    "'since' must be a number (epoch seconds)", 400)
        limit = None
        if request.args.get("limit"):
            try:
                limit = max(0, int(request.args["limit"]))
            except ValueError:
                return _error(
                    "invalid_request", "'limit' must be an integer", 400)
        result = web.facade.get_markers(session_name, since_ns, limit)
        if not result.get("success"):
            return _error(
                "markers_unavailable",
                result.get("error", "Could not read markers"), 500)
        return jsonify({
            "markers": result["markers"], "count": result["count"]})

    @bp.delete("/sessions/<session_name>")
    @require_auth
    def delete_session(session_name):
        files = request.args.get("files", "true").lower() != "false"
        force = request.args.get("force", "false").lower() == "true"
        result = web.facade.delete_session(session_name, files, force)
        if result.get("error"):
            msg = result["error"]
            status = 404 if msg.startswith("Unknown session") else 409
            extra = {}
            if result.get("export_warning"):
                extra = {
                    "export_warning": True,
                    "pending_exports": result.get("pending_exports"),
                    "total_exports_failed": result.get("total_exports_failed"),
                }
            return _error("delete_rejected", msg, status, **extra)
        return jsonify({"deleted": True, "session_name": session_name})

    # ------------------------------------------------------------------ #
    # API token management (admin password only, never an API token)
    # ------------------------------------------------------------------ #

    @bp.get("/tokens")
    @require_admin
    def list_tokens():
        return jsonify({"tokens": web.list_api_tokens()})

    @bp.post("/tokens")
    @require_admin
    def create_token():
        data = request.get_json(silent=True) or {}
        result = web.mint_api_token(
            str(data.get("name") or ""), readonly=bool(data.get("readonly")))
        if not result.get("success"):
            status = 409 if "already exists" in result.get("error", "") else 400
            return _error("token_rejected", result["error"], status)
        return jsonify({
            "name": result["name"],
            "token": result["token"],
            "created": result["created"],
            "readonly": result["readonly"],
        }), 201

    @bp.delete("/tokens/<name>")
    @require_admin
    def delete_token(name):
        result = web.revoke_api_token(name)
        if not result.get("success"):
            return _error("not_found", result["error"], 404)
        return jsonify({"revoked": True, "name": name})

    return bp
