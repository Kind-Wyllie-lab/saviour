"""
TTL lines seen on the acquisition system (plans/ttl-applications-ephys-sync.md).

SAVIOUR can't see whether a sync line actually reaches Open Ephys (or a
neurologger): only the operator can, by pulsing the line and watching for it
in the acquisition software. This keeps that confirmation per module and pin,
tied to the wiring it was made with (pin, mode, channel), and the result of
the last successful post-hoc alignment, which is the one automatic proof.

One JSON file, written atomically::

    {"modules": {module_id: {
        "lines": {"26": {"key": "26:pseudorandom:1", "channel": 1,
                         "confirmed_at": 1763640000.0}},
        "last_verified": {"session": ..., "at": ..., "gpio": 26, "r2": ...,
                          "edges_used": ..., "residual_max_ms": ...}}}}

Line states: ``confirmed``; ``stale`` (older than
``confirm_max_age_days``); ``changed`` (the pin, mode or channel changed
since); ``unconfirmed`` (never).

Socket.IO: ``get_ttl_lines`` -> ``ttl_lines``; admin ``confirm_ttl_line`` /
``clear_ttl_line`` {module_id, gpio} broadcast ``ttl_lines``; failures reply
``ttl_lines_error``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable

from src.shared import ttl_application

STORE_FILE = "/var/lib/saviour/controller/ttl_lines.json"
# Applications whose lines feed an acquisition system the operator confirms.
SYNC_APPLICATIONS = ("ephys_sync", "neurologger_sync")


class TTLLinesError(ValueError):
    pass


class TTLLineStore:
    def __init__(self, path: str = STORE_FILE,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            self._logger.error(f"TTL line store {self.path} unreadable ({e}); "
                               f"starting empty, file left in place")
            return {}
        mods = data.get("modules") if isinstance(data, dict) else None
        return {k: v for k, v in (mods or {}).items() if isinstance(v, dict)}

    def _write(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump({"modules": self._data}, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ── writes ──────────────────────────────────────────────────────────────
    def confirm(self, module_id: str, ttl_config: dict, gpio: int) -> dict:
        """Record that ``gpio`` on ``module_id`` was seen on the acquisition
        system, with its current wiring."""
        line = next((ln for ln in ttl_application.lines(ttl_config)
                     if ln["gpio"] == int(gpio)), None)
        if line is None:
            raise TTLLinesError(f"GPIO {gpio} on {module_id} has no role to confirm")
        entry = {"key": ttl_application.wiring_key(line),
                 "channel": line.get("channel"), "confirmed_at": self._clock()}
        with self._lock:
            self._data.setdefault(module_id, {}).setdefault("lines", {})[
                str(line["gpio"])] = entry
            self._write()
        return dict(entry)

    def clear(self, module_id: str, gpio: int) -> None:
        with self._lock:
            lines = self._data.get(module_id, {}).get("lines", {})
            if lines.pop(str(int(gpio)), None) is not None:
                self._write()

    def record_alignment(self, module_id: str, session: str, gpio: int | None,
                         result: dict) -> None:
        """A successful post-hoc alignment of ``session``'s ephys data."""
        entry = {"session": session, "at": self._clock(), "gpio": gpio,
                 **{k: result.get(k) for k in ("r2", "edges_used",
                                                "residual_max_ms", "drift_ms")}}
        with self._lock:
            self._data.setdefault(module_id, {})["last_verified"] = entry
            self._write()

    # ── reads ───────────────────────────────────────────────────────────────
    def raw(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def line_state(self, module_id: str, line: dict) -> dict:
        """``line`` (from ttl_application.lines) plus its confirmation state."""
        with self._lock:
            entry = copy.deepcopy(self._data.get(module_id, {}).get("lines", {})
                                  .get(str(line["gpio"])))
        out = {**line, "confirmed_at": None, "state": "unconfirmed"}
        if not entry:
            return out
        out["confirmed_at"] = entry.get("confirmed_at")
        if entry.get("key") != ttl_application.wiring_key(line):
            out["state"] = "changed"
        elif (self._clock() - float(entry.get("confirmed_at") or 0)
              > ttl_application.CONFIRM_MAX_AGE_DAYS * 86400):
            out["state"] = "stale"
        else:
            out["state"] = "confirmed"
        return out

    def status(self, modules: dict) -> dict:
        """Every TTL module with a sync system, or with a line wired to
        another system: ``{module_id: {application, label, name, online, lines,
        problems, last_verified}}``."""
        out = {}
        for mid, m in sorted((modules or {}).items()):
            app = ttl_application.module_application(m)
            if app is None:
                continue
            ttl_cfg = (m.get("config") or {}).get("ttl") or {}
            lines = ttl_application.lines(ttl_cfg)
            # Listed when it has a line that's wired to another system (a
            # sync line, an experiment marker, a start pulse).
            if app == "mixed" and not any(
                    ttl_application.ROLES[ln["role"]].get("channel") for ln in lines):
                continue
            with self._lock:
                last = copy.deepcopy(self._data.get(mid, {}).get("last_verified"))
            out[mid] = {
                "application": app,
                "label": ttl_application.label(app),
                "name": m.get("name") or mid,
                "online": bool(m.get("online", True)),
                "lines": [self.line_state(mid, ln) for ln in lines],
                "problems": ttl_application.validate(ttl_cfg),
                "last_verified": last,
            }
        return out


def sync_wiring(status: dict) -> list[dict]:
    """The sync modules from ``TTLLineStore.status()``, for a session record:
    ``[{module_id, name, application, sync, lines, last_verified}]``."""
    out = []
    for mid, s in status.items():
        if s["application"] not in SYNC_APPLICATIONS:
            continue
        sync = next((ln for ln in s["lines"] if ln["role"] == "sync"), None)
        out.append({"module_id": mid, "name": s["name"],
                    "application": s["application"], "sync": sync,
                    "lines": s["lines"], "last_verified": s["last_verified"]})
    return out


def wiring_warnings(wiring: list[dict]) -> list[str]:
    """One sentence per sync line that isn't currently confirmed."""
    out = []
    for w in wiring:
        sync = w.get("sync")
        if sync is None:
            out.append(f"{w['name']} has no sync line")
            continue
        where = (f"GPIO {sync['gpio']}"
                 + (f" → channel {sync['channel']}" if sync.get("channel") else ""))
        state = sync.get("state")
        if state == "unconfirmed":
            out.append(f"{w['name']}: sync line ({where}) never confirmed on the "
                       f"acquisition system")
        elif state == "changed":
            out.append(f"{w['name']}: sync line ({where}) changed since it was "
                       f"last confirmed")
        elif state == "stale":
            days = ttl_application.CONFIRM_MAX_AGE_DAYS
            out.append(f"{w['name']}: sync line ({where}) last confirmed more than "
                       f"{days:g} days ago")
    return out


def register_socket_handlers(sio, store: TTLLineStore, *,
                             get_modules: Callable[[], dict],
                             require_auth: Callable[..., bool],
                             logger: logging.Logger) -> None:
    from flask import request

    def payload() -> dict:
        return {"modules": store.status(get_modules() or {}),
                "confirm_max_age_days": ttl_application.CONFIRM_MAX_AGE_DAYS}

    def module_ttl(module_id: str) -> dict:
        m = (get_modules() or {}).get(module_id)
        if not m or m.get("type") != "ttl":
            raise TTLLinesError(f"{module_id} isn't a TTL module")
        return (m.get("config") or {}).get("ttl") or {}

    def guarded(fn, *, admin: bool):
        def wrapped(data=None):
            if admin and not require_auth(
                    "ttl_lines_error", {"error": "Sign in as admin to do that"}):
                return
            d = data if isinstance(data, dict) else {}
            try:
                fn(d)
            except (TTLLinesError, TypeError, ValueError) as e:
                sio.emit("ttl_lines_error", {"error": str(e)}, room=request.sid)
            except Exception as e:                          # noqa: BLE001 -- reported to the caller
                logger.exception("TTL lines handler failed")
                sio.emit("ttl_lines_error", {"error": f"Unexpected error: {e}"},
                         room=request.sid)
        return wrapped

    def get(d: dict) -> None:
        sio.emit("ttl_lines", payload(), room=request.sid)

    def confirm(d: dict) -> None:
        mid = str(d.get("module_id") or "")
        store.confirm(mid, module_ttl(mid), int(d.get("gpio")))
        logger.info(f"TTL line confirmed on the acquisition system: {mid} "
                    f"GPIO {d.get('gpio')}")
        sio.emit("ttl_lines", payload())

    def clear(d: dict) -> None:
        store.clear(str(d.get("module_id") or ""), int(d.get("gpio")))
        sio.emit("ttl_lines", payload())

    for event, (fn, admin) in {
        "get_ttl_lines": (get, False),
        "confirm_ttl_line": (confirm, True),
        "clear_ttl_line": (clear, True),
    }.items():
        sio.on_event(event, guarded(fn, admin=admin))
