"""
Quick setup: the last run of the on-demand rig walkthrough
(plans/rig-quick-setup.md).

The modal (frontend `basic/components/QuickSetup/`) walks through rig type,
external sync, expected modules and each module's preview, then saves what
was confirmed here. Only the last run is kept. It is informational: nothing
is enforced from it.

One JSON file, written atomically::

    {"last_run": {"at": 1763640000.0, "by": "admin" | "guest",
                  "researcher": "...", "variant": "apa",
                  "external_sync": "ephys" | null,
                  "rig": {"level": "ok", "summary": "..."},
                  "modules": {module_id: {"name": ..., "type": ...,
                                          "confirmed": bool}},
                  "sync_lines": [{"module_id", "name", "gpio", "role",
                                  "channel", "state"}]}}

Socket.IO: ``get_quick_setup`` -> ``quick_setup`` ({"last_run": ... | None});
``save_quick_setup`` {run} stores it and broadcasts ``quick_setup``; failures
reply ``quick_setup_error``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable

STORE_FILE = "/var/lib/saviour/controller/quick_setup.json"
# A run lists one entry per module; this only stops a runaway payload.
MAX_MODULES = 256
MAX_LINES = 256
MAX_TEXT = 200


class QuickSetupError(ValueError):
    pass


def _text(value: object, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def clean_run(run: object, *, now: float, by: str) -> dict:
    """The stored form of a run sent by the frontend: known keys only,
    strings trimmed, modules capped. ``at`` and ``by`` come from the
    controller, not the client."""
    if not isinstance(run, dict):
        raise QuickSetupError("run must be an object")
    modules_in = run.get("modules")
    if modules_in is None:
        modules_in = {}
    if not isinstance(modules_in, dict):
        raise QuickSetupError("modules must be an object")
    if len(modules_in) > MAX_MODULES:
        raise QuickSetupError(f"at most {MAX_MODULES} modules")
    modules = {}
    for mid, m in modules_in.items():
        m = m if isinstance(m, dict) else {}
        modules[_text(mid, 80)] = {
            "name": _text(m.get("name")),
            "type": _text(m.get("type"), 40),
            "confirmed": bool(m.get("confirmed")),
        }
    lines_in = run.get("sync_lines")
    if lines_in is None:
        lines_in = []
    if not isinstance(lines_in, list):
        raise QuickSetupError("sync_lines must be a list")
    if len(lines_in) > MAX_LINES:
        raise QuickSetupError(f"at most {MAX_LINES} sync lines")
    sync_lines = []
    for ln in lines_in:
        ln = ln if isinstance(ln, dict) else {}
        try:
            gpio = int(ln.get("gpio"))
        except (TypeError, ValueError):
            continue
        channel = ln.get("channel")
        sync_lines.append({
            "module_id": _text(ln.get("module_id"), 80),
            "name": _text(ln.get("name")),
            "gpio": gpio,
            "role": _text(ln.get("role"), 40),
            "channel": channel if isinstance(channel, int) else None,
            "state": _text(ln.get("state"), 20),
        })
    rig = run.get("rig") if isinstance(run.get("rig"), dict) else {}
    return {
        "at": now,
        "by": by,
        "researcher": _text(run.get("researcher")),
        "variant": _text(run.get("variant"), 40),
        "external_sync": _text(run.get("external_sync"), 40) or None,
        "rig": {"level": _text(rig.get("level"), 20),
                "summary": _text(rig.get("summary"), 500)},
        "modules": modules,
        "sync_lines": sync_lines,
    }


class QuickSetupStore:
    def __init__(self, path: str = STORE_FILE,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._last: dict | None = self._load()

    def _load(self) -> dict | None:
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            self._logger.error(f"Quick setup store {self.path} unreadable ({e}); "
                               f"starting empty, file left in place")
            return None
        last = data.get("last_run") if isinstance(data, dict) else None
        return last if isinstance(last, dict) else None

    def _write(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump({"last_run": self._last}, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def save(self, run: object, *, by: str) -> dict:
        cleaned = clean_run(run, now=self._clock(), by=by)
        with self._lock:
            self._last = cleaned
            self._write()
        return copy.deepcopy(cleaned)

    def last_run(self) -> dict | None:
        with self._lock:
            return copy.deepcopy(self._last)


def register_socket_handlers(sio, store: QuickSetupStore, *,
                             is_admin: Callable[[], bool],
                             logger: logging.Logger) -> None:
    from flask import request

    def get(data=None) -> None:
        sio.emit("quick_setup", {"last_run": store.last_run()}, room=request.sid)

    def save(data=None) -> None:
        d = data if isinstance(data, dict) else {}
        try:
            run = store.save(d.get("run"), by="admin" if is_admin() else "guest")
        except QuickSetupError as e:
            sio.emit("quick_setup_error", {"error": str(e)}, room=request.sid)
            return
        except Exception as e:                              # noqa: BLE001 -- reported to the caller
            logger.exception("Quick setup save failed")
            sio.emit("quick_setup_error", {"error": f"Unexpected error: {e}"},
                     room=request.sid)
            return
        unconfirmed = sum(1 for m in run["modules"].values() if not m["confirmed"])
        logger.info(f"Quick setup run saved ({run['by']}): {len(run['modules'])} "
                    f"modules, {unconfirmed} not confirmed, "
                    f"external sync {run['external_sync'] or 'none'}")
        sio.emit("quick_setup", {"last_run": run})

    sio.on_event("get_quick_setup", get)
    sio.on_event("save_quick_setup", save)
