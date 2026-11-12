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

A module running a simulated bus (``rfid.mock.enabled``) reports it in its
``rfid_units`` / ``rfid_mock_*`` replies; the Gates page's Simulated bus
panel then drives it by hand through ``mock_read`` / ``mock_unit`` /
``mock_animals``, so a mapping run can be tested on the real code without
readers. Those are refused for any module that isn't a simulated bus.
"""

from __future__ import annotations

import logging
import re
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
# Serial numbers are queried one unit at a time, 0.25 s apart, after a scan,
# so on a big bus many are still missing at SCAN_REPLY_DELAY_S: while a
# module says queries are pending, ask for its units again (at most this
# many times per scan).
QUERY_GAP_S = 0.25
UNITS_RETRIES = 6
MOCK_COMMANDS = ("rfid_mock_state", "rfid_mock_read", "rfid_mock_unit",
                 "rfid_mock_animals")
_TAG_RE = re.compile(r"^[0-9A-Fa-f]{10}$")


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
        self._units_retries: dict[str, int] = {}
        # module_id -> simulated-bus state, from its replies; and the
        # modules already asked whether they run one.
        self.mock_buses: dict[str, dict] = {}
        self._mock_asked: set[str] = set()

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
                "mock_buses": [
                    {"module_id": mid, **s}
                    for mid, s in sorted(self.mock_buses.items())
                    if mid in self.rfid_module_ids()],
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
        with self._lock:
            self._units_retries = {}
        for mid in ids:
            self.send_command(mid, "rfid_scan", {})
        t = self.timer(SCAN_REPLY_DELAY_S, self._request_units, args=(ids,))
        t.daemon = True
        t.start()

    def _request_units(self, ids: list[str]) -> None:
        for mid in ids:
            self.send_command(mid, "rfid_units", {})

    # ── Simulated bus (testing without readers) ─────────────────────────────

    def refresh_mock(self) -> None:
        """Ask each RFID module not yet asked whether it runs a simulated
        bus (a real one answers with an error and is left alone)."""
        ids = self.rfid_module_ids()
        with self._lock:
            new = [mid for mid in ids if mid not in self._mock_asked]
            self._mock_asked.update(new)
        for mid in new:
            self.send_command(mid, "rfid_mock_state", {})

    def _mock_module(self, module_id: object) -> str:
        mid = str(module_id or "")
        with self._lock:
            known = mid in self.mock_buses
        if not known or mid not in self.rfid_module_ids():
            raise GateMapError(f"{mid or 'That module'} isn't running a simulated bus")
        return mid

    @staticmethod
    def _unit(unit: object) -> int:
        try:
            return int(unit)
        except (TypeError, ValueError):
            raise GateMapError("Pick a unit") from None

    def mock_read(self, module_id: object, unit: object, tag: object = None) -> None:
        """A tag held at one antenna of a simulated bus."""
        mid = self._mock_module(module_id)
        params = {"unit": self._unit(unit)}
        if tag:
            if not _TAG_RE.match(str(tag)):
                raise GateMapError("A tag is 10 hex digits")
            params["tag"] = str(tag).upper()
        self.send_command(mid, "rfid_mock_read", params)

    def mock_unit(self, module_id: object, unit: object,
                  plugged: object = None, swap: object = False) -> None:
        """Unplug / replug a simulated unit, or swap its decoder."""
        mid = self._mock_module(module_id)
        params: dict = {"unit": self._unit(unit), "swap": bool(swap)}
        if plugged is not None:
            params["plugged"] = bool(plugged)
        self.send_command(mid, "rfid_mock_unit", params)

    def mock_animals(self, module_id: object, running: object) -> None:
        """Start or stop a simulated bus's wandering tags."""
        mid = self._mock_module(module_id)
        self.send_command(mid, "rfid_mock_animals", {"running": bool(running)})

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
            return
        if kind != "cmd_ack":
            return
        command = status.get("command")
        if command == "rfid_units":
            with self._lock:
                bus = {k: b for k, b in (self.bus or {}).items()
                       if b.module_id != module_id}
                for b in units_from_ack(module_id, status.get("units")):
                    bus[b.key] = b
                self.bus = bus
                self.bus_scanned_at = self.clock()
                self._note_mock(module_id, status)
                retry = self._units_retry_delay(module_id, status)
            if retry is not None:
                t = self.timer(retry, self._request_units, args=([module_id],))
                t.daemon = True
                t.start()
            self.broadcast()
        elif command in MOCK_COMMANDS:
            with self._lock:
                self._note_mock(module_id, status)
            if command != "rfid_mock_state" and status.get("result") != "success":
                self.emit("gate_map_error", {"error": str(
                    status.get("message") or f"{command} failed on {module_id}")})
            self.broadcast()

    def _note_mock(self, module_id: str, status: dict) -> None:
        """Keep (or drop) a module's simulated-bus state from its reply."""
        if "mock_bus" not in status:
            return
        self._mock_asked.add(module_id)
        state = status.get("mock_bus")
        if isinstance(state, dict):
            self.mock_buses[module_id] = state
        else:
            self.mock_buses.pop(module_id, None)

    def _units_retry_delay(self, module_id: str, status: dict) -> float | None:
        """Seconds until asking ``module_id`` for its units again, while its
        serial-number queries are still going out; None when done."""
        try:
            pending = int(status.get("pending_queries") or 0)
        except (TypeError, ValueError):
            pending = 0
        tries = self._units_retries.get(module_id, 0)
        if pending <= 0:
            self._units_retries.pop(module_id, None)
            return None
        if tries >= UNITS_RETRIES:      # given up until the next scan
            return None
        self._units_retries[module_id] = tries + 1
        return min(30.0, pending * QUERY_GAP_S + 1.0)

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

    def get_gate_map(_d):
        sio.emit("gate_map_state", svc.state(), room=request.sid)
        svc.refresh_mock()

    events = {
        "get_gate_map": (get_gate_map, False),
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
        # Simulated bus only (refused for a real one): sign-in needed, as a
        # read during a run changes it.
        "gate_map_mock_read": (lambda d: svc.mock_read(
            d.get("module_id"), d.get("unit"), d.get("tag")), True),
        "gate_map_mock_unit": (lambda d: svc.mock_unit(
            d.get("module_id"), d.get("unit"), d.get("plugged"),
            d.get("swap", False)), True),
        "gate_map_mock_animals": (lambda d: svc.mock_animals(
            d.get("module_id"), d.get("running", True)), True),
    }
    for name, (fn, admin) in events.items():
        sio.on_event(name, handler(fn, admin=admin))
