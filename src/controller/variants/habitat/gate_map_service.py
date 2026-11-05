"""
The habitat gate-mapping service: gate_map.py's layout, map and mapping run,
connected to the web UI (Socket.IO events) and the RFID modules (commands
out, rfid_read / cmd_ack in).

Kept apart from habitat_controller.py so it can be tested with fakes for
``emit``, ``send_command`` and the module list.

Module side (rfid_module.py): ``rfid_scan`` (bus logon), ``rfid_units``
(addresses + serial numbers, answered as a cmd_ack) and
``rfid_mapping_mode`` (forward every read for a while). Mapping mode is
renewed only while a browser has the Gates page open (``keepalive``) or
the dashboard's live gate view (``live_keepalive``), so a closed tab can't
leave it on.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from gate_map import (
    SETTLE_S,
    BusUnit,
    GateMapError,
    GateMapStore,
    MappingRun,
    gates_for_unit,
    review_entries,
    scope_gates,
    units_from_ack,
)

# Mapping mode on the modules lasts this long per renewal; the page renews
# it every KEEPALIVE_EVERY_S.
MAPPING_TIMEOUT_S = 90.0
KEEPALIVE_EVERY_S = 30.0
# A bus logon collects replies for 3 s (rfid_bus.scan_bus) and then each new
# unit's serial number is queried 0.25 s apart, so ask for the unit list a
# little after that.
SCAN_REPLY_DELAY_S = 4.0
HIT_HISTORY = 20
# The dashboard renews its live gate view every 20 s; it lapses this long
# after the last renewal (closed tab), and mapping mode times out on the
# modules MAPPING_TIMEOUT_S after that.
LIVE_TTL_S = 60.0


class GateMapService:
    def __init__(self, store: GateMapStore, *,
                 emit: Callable[[str, dict], None],
                 send_command: Callable[[str, str, dict], None],
                 rfid_module_ids: Callable[[], list[str]],
                 logger: logging.Logger | None = None,
                 clock: Callable[[], float] = time.time,
                 timer: Callable[..., threading.Timer] = threading.Timer) -> None:
        self.store = store
        self.emit = emit
        self.send_command = send_command
        self.rfid_module_ids = rfid_module_ids
        self.logger = logger or logging.getLogger(__name__)
        self.clock = clock
        self.timer = timer
        self._lock = threading.RLock()

        self.layout = store.load_layout()
        self.map = store.load_map()
        self.run: MappingRun | None = None
        self.walkthrough = False
        self.bus: dict[str, BusUnit] | None = None
        self.bus_scanned_at: float | None = None
        self.hits: deque[dict] = deque(maxlen=HIT_HISTORY)
        self._last_keepalive = 0.0
        self._live_until = 0.0
        self._settle_timer: threading.Timer | None = None

        draft = store.load_draft()
        if draft:
            try:
                self.run = MappingRun.from_dict(draft, self.clock())
                self.logger.info(f"Resumed gate-mapping run {self.run.run_id} "
                                 f"by {self.run.operator}")
            except (GateMapError, KeyError, TypeError, ValueError) as e:
                self.logger.warning(f"Ignoring unreadable gate-map draft: {e}")

    # ── State for the web UI ────────────────────────────────────────────────

    def state(self) -> dict:
        with self._lock:
            entries = self.map["entries"]
            known = {g["id"] for g in self.layout["gates"]}
            return {
                "layout": self.layout,
                "map": {"entries": entries,
                        "updated_at": self.map.get("updated_at")},
                # Entries for gates the layout no longer has (or lists as
                # not fitted), e.g. after shrinking the grid.
                "orphaned": sorted(
                    g for g in entries
                    if g not in known or not self._fitted(g)),
                "check": review_entries(entries, self.bus),
                "run": self.run.view(self.bus) if self.run else None,
                "walkthrough": self.walkthrough,
                "bus": None if self.bus is None else [
                    {"unit": b.key, "module_id": b.module_id,
                     "address": b.unit, "serial": b.serial,
                     "firmware": b.firmware}
                    for b in sorted(self.bus.values(), key=lambda b: b.key)],
                "bus_scanned_at": self.bus_scanned_at,
                "rfid_modules": self.rfid_module_ids(),
                "hits": list(self.hits),
                "changes": self.store.recent_changes(20),
                "settle_s": SETTLE_S,
            }

    def _fitted(self, gate: str) -> bool:
        return any(g["id"] == gate and g.get("fitted", True)
                   for g in self.layout["gates"])

    def broadcast(self) -> None:
        self.emit("gate_map_state", self.state())

    # ── Layout ──────────────────────────────────────────────────────────────

    def save_layout(self, layout: object) -> None:
        with self._lock:
            if self.run:
                raise GateMapError(
                    "Finish or discard the mapping run before changing the layout")
            self.layout = self.store.save_layout(layout)
        self.broadcast()

    # ── Mapping run ─────────────────────────────────────────────────────────

    def start_run(self, operator: str, scope: object) -> None:
        with self._lock:
            if self.run:
                raise GateMapError(
                    "A mapping run is already in progress: carry on with it "
                    "or discard it")
            gates = scope_gates(self.layout, scope)
            if not self.rfid_module_ids():
                raise GateMapError("No RFID module is connected")
            self.run = MappingRun.start(operator, gates, self.map["entries"],
                                        self.clock())
            self.store.save_draft(self.run)
            self.logger.info(f"Gate-mapping run {self.run.run_id} started by "
                             f"{self.run.operator}: {len(gates)} gate(s)")
        self.scan()
        self._set_mapping_mode(True)
        self.broadcast()

    def act(self, action: str, unit: str | None = None,
            resolve: str | None = None) -> None:
        with self._lock:
            if not self.run:
                raise GateMapError("No mapping run in progress")
            serials = {k: b.serial for k, b in (self.bus or {}).items()}
            self.run.act(action, self.clock(), unit=unit, resolve=resolve,
                         serials=serials)
            self._cancel_settle()
            self.store.save_draft(self.run)
        self.broadcast()

    def save_run(self) -> None:
        with self._lock:
            run = self.run
            if not run or run.phase != "review":
                raise GateMapError("Map every gate in the run before saving")
            changes = self.store.save_map(run.entries(), run_id=run.run_id,
                                          operator=run.operator,
                                          reason="mapping_run")
            self.map = self.store.load_map()
            self.store.delete_draft()
            self.run = None
            self._cancel_settle()
            self.logger.info(f"Gate-mapping run {run.run_id} saved: "
                             f"{len(changes)} gate(s) changed")
        self._release_mapping_mode()
        self.broadcast()

    def discard_run(self) -> None:
        with self._lock:
            if not self.run:
                raise GateMapError("No mapping run in progress")
            self.logger.info(f"Gate-mapping run {self.run.run_id} discarded")
            self.run = None
            self._cancel_settle()
            self.store.delete_draft()
        self._release_mapping_mode()
        self.broadcast()

    # ── Walk-through check ──────────────────────────────────────────────────

    def set_walkthrough(self, enabled: bool) -> None:
        with self._lock:
            self.walkthrough = bool(enabled)
            if self.walkthrough:
                self.hits.clear()
        if enabled:
            self._set_mapping_mode(True)
        else:
            self._release_mapping_mode()
        self.broadcast()

    # ── Modules ─────────────────────────────────────────────────────────────

    def scan(self) -> None:
        """Bus logon on every RFID module, then ask each for its units."""
        ids = self.rfid_module_ids()
        for mid in ids:
            self.send_command(mid, "rfid_scan", {})
        t = self.timer(SCAN_REPLY_DELAY_S, self._request_units, args=(ids,))
        t.daemon = True
        t.start()

    def _request_units(self, ids: list[str]) -> None:
        for mid in ids:
            self.send_command(mid, "rfid_units", {})

    def keepalive(self) -> None:
        """A Gates page is open: keep mapping mode on while it's needed."""
        with self._lock:
            needed = self._mapping_needed()
            due = self.clock() - self._last_keepalive >= KEEPALIVE_EVERY_S
        if needed and due:
            self._set_mapping_mode(True)

    def live_keepalive(self) -> None:
        """The dashboard's live gate view is open: forward every read (as
        gate_map_hit) for another LIVE_TTL_S."""
        with self._lock:
            self._live_until = self.clock() + LIVE_TTL_S
        self.keepalive()

    def _live(self) -> bool:
        return self.clock() < self._live_until

    def _mapping_needed(self) -> bool:
        return bool(self.run) or self.walkthrough or self._live()

    def _release_mapping_mode(self) -> None:
        """Turn mapping mode off unless something else still wants it."""
        with self._lock:
            needed = self._mapping_needed()
        if not needed:
            self._set_mapping_mode(False)

    def _set_mapping_mode(self, enabled: bool) -> None:
        with self._lock:
            self._last_keepalive = self.clock() if enabled else 0.0
        for mid in self.rfid_module_ids():
            self.send_command(mid, "rfid_mapping_mode",
                              {"enabled": enabled, "timeout_s": MAPPING_TIMEOUT_S})

    def on_module_status(self, module_id: str, status: dict) -> None:
        """web.py module_status_listeners hook."""
        kind = status.get("type")
        if kind == "rfid_read":
            self._on_read(module_id, status)
        elif kind == "cmd_ack" and status.get("command") == "rfid_units":
            with self._lock:
                bus = {k: b for k, b in (self.bus or {}).items()
                       if b.module_id != module_id}
                for b in units_from_ack(module_id, status.get("units")):
                    bus[b.key] = b
                self.bus = bus
                self.bus_scanned_at = self.clock()
            self.broadcast()

    def _on_read(self, module_id: str, status: dict) -> None:
        try:
            unit = int(status.get("unit_address"))
        except (TypeError, ValueError):
            return
        tag = str(status.get("transponder_id") or "")
        ts = float(status.get("packet_ts") or self.clock())
        changed = False
        with self._lock:
            if not self._mapping_needed():
                return
            hit = {"module_id": module_id, "unit": unit, "tag": tag, "ts": ts,
                   "gates": gates_for_unit(self.map["entries"], module_id, unit)}
            if self.run:
                was_first = self.run.capture.first
                was_phase = self.run.phase
                changed = self.run.on_read(module_id, unit, tag, ts)
                if changed and was_phase == "probe":
                    self.store.save_draft(self.run)
                elif changed and was_first is None:
                    self._start_settle()
                hit["probe"] = tag.upper() == (self.run.probe_tag or "")
            # The Gates page's recent-hits list; the dashboard's every-ping
            # view alone doesn't fill it.
            if self.run or self.walkthrough:
                self.hits.appendleft(hit)
        self.emit("gate_map_hit", hit)
        if changed:
            self.broadcast()

    def _start_settle(self) -> None:
        self._cancel_settle()
        t = self.timer(SETTLE_S, self._settle)
        t.daemon = True
        self._settle_timer = t
        t.start()

    def _cancel_settle(self) -> None:
        if self._settle_timer is not None:
            self._settle_timer.cancel()
            self._settle_timer = None

    def _settle(self) -> None:
        with self._lock:
            self._settle_timer = None
            if not self.run:
                return
            self.run.settle()
        self.broadcast()


def rfid_module_ids(modules: dict) -> list[str]:
    """Connected rfid modules, from ControllerFacade.get_modules()."""
    return sorted(mid for mid, m in modules.items()
                  if m.get("type") == "rfid" and m.get("status") != "OFFLINE")


def register_socket_handlers(sio, svc: GateMapService, *,
                             require_auth: Callable[[str, dict], bool],
                             logger: logging.Logger) -> None:
    """Socket.IO events for the Gates page. Reading the map, the walk-through
    check, the bus scan and the keepalive are open to guests (they change
    nothing); everything that edits the layout or the map needs a login
    (``require_auth`` is Web._require_auth). Errors go back to the caller
    as gate_map_error {error}."""
    from flask import request

    def reply_error(message: str) -> None:
        sio.emit("gate_map_error", {"error": message}, room=request.sid)

    def handler(fn, *, admin: bool):
        def wrapped(data=None):
            if admin and not require_auth(
                    "gate_map_error", {"error": "Login required for this action"}):
                return
            try:
                fn(data if isinstance(data, dict) else {})
            except GateMapError as e:
                reply_error(str(e))
            except Exception as e:
                logger.error(f"Gate map handler failed: {e}")
                reply_error(f"Unexpected error: {e}")
        return wrapped

    events = {
        "get_gate_map": (lambda d: sio.emit(
            "gate_map_state", svc.state(), room=request.sid), False),
        "gate_map_keepalive": (lambda d: svc.keepalive(), False),
        "gate_map_live": (lambda d: svc.live_keepalive(), False),
        "gate_map_walkthrough": (
            lambda d: svc.set_walkthrough(bool(d.get("enabled"))), False),
        "gate_map_scan": (lambda d: svc.scan(), False),
        "gate_map_save_layout": (lambda d: svc.save_layout(d.get("layout")), True),
        "gate_map_start_run": (lambda d: svc.start_run(
            d.get("operator", ""), d.get("scope", "all")), True),
        "gate_map_action": (lambda d: svc.act(
            str(d.get("action", "")), unit=d.get("unit"),
            resolve=d.get("resolve")), True),
        "gate_map_save_run": (lambda d: svc.save_run(), True),
        "gate_map_discard_run": (lambda d: svc.discard_run(), True),
    }
    for name, (fn, admin) in events.items():
        sio.on_event(name, handler(fn, admin=admin))
