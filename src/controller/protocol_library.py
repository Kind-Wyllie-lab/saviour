"""Named session protocols saved on the controller (the protocol library).

A protocol (src/shared/session_protocol.py format) is designed once, saved
here under a name, copied, edited and picked by TTL modules. Picking one
copies it into the module's own config (`ttl.protocol`, its `name` kept),
so the module runs and resumes it without the controller; the library is
where protocols are kept and edited, not what runs.

One JSON file, written atomically: {"protocols": {name: entry}} where entry
is {"protocol": {...}, "created_at": ts, "updated_at": ts}. A protocol
that doesn't validate can still be saved (a draft); the editor and the
module's start check say what's wrong.

Socket.IO (registered by web.py, every variant):
  get_protocols                      anyone   -> protocols
  save_protocol {name, protocol, previous_name?, overwrite?}   admin
  copy_protocol {name, new_name}     admin
  delete_protocol {name}             admin
Changes broadcast `protocols` to everyone; failures reply `protocol_error`.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable

LIBRARY_FILE = "/var/lib/saviour/controller/protocols.json"
MAX_NAME_LEN = 80
# A protocol is a few KB; this only stops a runaway paste filling the disk.
MAX_PROTOCOL_BYTES = 512 * 1024


class ProtocolLibraryError(ValueError):
    """A request the library refuses (bad name, clash, missing)."""


def clean_name(name: object) -> str:
    """Trimmed, single-spaced name; raises on empty, too long or a control
    character (names go in file names on export later)."""
    if not isinstance(name, str):
        raise ProtocolLibraryError("A protocol needs a name")
    text = " ".join(name.split())
    if not text:
        raise ProtocolLibraryError("A protocol needs a name")
    if len(text) > MAX_NAME_LEN:
        raise ProtocolLibraryError(f"Names are at most {MAX_NAME_LEN} characters")
    if any(ord(c) < 32 for c in text) or "/" in text or "\\" in text:
        raise ProtocolLibraryError("Names can't contain slashes or control characters")
    return text


class ProtocolLibrary:
    def __init__(self, path: str = LIBRARY_FILE,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._items: dict[str, dict] = self._load()

    # ── storage ─────────────────────────────────────────────────────────────
    def _load(self) -> dict[str, dict]:
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            # Keep the unreadable file for inspection; start empty rather
            # than refusing every protocol call.
            self._logger.error(f"Protocol library {self.path} unreadable ({e}); "
                               f"starting empty, file left in place")
            return {}
        items = data.get("protocols") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            return {}
        return {k: v for k, v in items.items()
                if isinstance(v, dict) and isinstance(v.get("protocol"), dict)}

    def _write(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump({"protocols": self._items}, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ── reads ───────────────────────────────────────────────────────────────
    def list(self) -> list[dict]:
        """Every protocol, by name (case-insensitive), with its content."""
        with self._lock:
            return [
                {"name": name, "protocol": copy.deepcopy(e["protocol"]),
                 "created_at": e.get("created_at"), "updated_at": e.get("updated_at")}
                for name, e in sorted(self._items.items(), key=lambda kv: kv[0].lower())
            ]

    def get(self, name: str) -> dict | None:
        with self._lock:
            e = self._items.get(name)
            return copy.deepcopy(e["protocol"]) if e else None

    # ── writes ──────────────────────────────────────────────────────────────
    def save(self, name: object, protocol: object, *,
             previous_name: object = None, overwrite: bool = False) -> str:
        """Save *protocol* as *name*. With *previous_name* (an edit that
        renamed it) the old entry goes. A different protocol already under
        *name* is refused unless *overwrite*. Returns the stored name."""
        name = clean_name(name)
        if not isinstance(protocol, dict):
            raise ProtocolLibraryError("A protocol must be a JSON object")
        if len(json.dumps(protocol)) > MAX_PROTOCOL_BYTES:
            raise ProtocolLibraryError("That protocol is too large to save")
        prev = clean_name(previous_name) if previous_name else None
        stored = copy.deepcopy(protocol)
        stored["name"] = name
        now = self._clock()
        with self._lock:
            clash = name in self._items and name != prev
            if clash and not overwrite:
                raise ProtocolLibraryError(f"A protocol called “{name}” already exists")
            created = (self._items.get(prev) or self._items.get(name) or {}).get(
                "created_at", now)
            if prev and prev != name:
                self._items.pop(prev, None)
            self._items[name] = {"protocol": stored, "created_at": created,
                                 "updated_at": now}
            self._write()
        return name

    def copy(self, name: object, new_name: object = None) -> str:
        """Duplicate *name* as *new_name* (default "<name> (copy)", numbered
        until free). Returns the new name."""
        name = clean_name(name)
        with self._lock:
            src = self._items.get(name)
            if src is None:
                raise ProtocolLibraryError(f"No protocol called “{name}”")
            if new_name:
                target = clean_name(new_name)
                if target in self._items:
                    raise ProtocolLibraryError(f"A protocol called “{target}” already exists")
            else:
                base = f"{name} (copy)"[:MAX_NAME_LEN]
                target, n = base, 2
                while target in self._items:
                    target = f"{name} (copy {n})"[:MAX_NAME_LEN]
                    n += 1
            stored = copy.deepcopy(src["protocol"])
            stored["name"] = target
            now = self._clock()
            self._items[target] = {"protocol": stored, "created_at": now, "updated_at": now}
            self._write()
        return target

    def delete(self, name: object) -> None:
        name = clean_name(name)
        with self._lock:
            if self._items.pop(name, None) is None:
                raise ProtocolLibraryError(f"No protocol called “{name}”")
            self._write()


def register_socket_handlers(sio, library: ProtocolLibrary, *,
                             require_auth: Callable[..., bool],
                             logger: logging.Logger) -> None:
    from flask import request

    def reply(event: str, data: dict) -> None:
        sio.emit(event, data, room=request.sid)

    def broadcast(**extra) -> None:
        sio.emit("protocols", {"items": library.list(), **extra})

    def guarded(fn, *, admin: bool):
        def wrapped(data=None):
            if admin and not require_auth(
                    "protocol_error", {"error": "Sign in as admin to change protocols"}):
                return
            try:
                fn(data if isinstance(data, dict) else {})
            except ProtocolLibraryError as e:
                reply("protocol_error", {"error": str(e)})
            except Exception as e:                          # noqa: BLE001 -- reported to the caller
                logger.exception("Protocol library handler failed")
                reply("protocol_error", {"error": f"Unexpected error: {e}"})
        return wrapped

    def save(d: dict) -> None:
        name = library.save(d.get("name"), d.get("protocol"),
                            previous_name=d.get("previous_name"),
                            overwrite=bool(d.get("overwrite")))
        broadcast(saved=name, previous_name=d.get("previous_name"))

    def copy_(d: dict) -> None:
        broadcast(saved=library.copy(d.get("name"), d.get("new_name")))

    def delete(d: dict) -> None:
        library.delete(d.get("name"))
        broadcast(deleted=d.get("name"))

    events = {
        "get_protocols": (lambda d: reply("protocols", {"items": library.list()}), False),
        "save_protocol": (save, True),
        "copy_protocol": (copy_, True),
        "delete_protocol": (delete, True),
    }
    for name, (fn, admin) in events.items():
        sio.on_event(name, guarded(fn, admin=admin))
