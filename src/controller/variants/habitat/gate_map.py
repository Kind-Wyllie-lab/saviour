"""
Habitat layout and RFID gate mapping (plans/rfid-gate-mapping.md, phases 1-2).

A habitat is a grid of boxes. Each internal wall between two neighbouring
boxes has up to four gates, each with one RFID antenna on its own reader unit
(an address on an ``rfid`` module's RS485 bus). This module holds:

* the **layout** (boxes, walls, gates, and which gates are fitted);
* the **gate map**: which ``(module_id, unit)`` antenna is which gate, or
  that a gate is closed / not fitted / faulty;
* an append-only **change log** of every mapping change;
* a **mapping run**: the guided routine that builds the map on the rig
  (register a probe tag, then pass it through one gate at a time), with the
  checks for a unit read at two gates, two units reading one pass, units on
  the bus but unmapped and the reverse, and a decoder swapped onto a mapped
  address.

Plain logic and files, no Socket.IO: ``habitat_controller.py`` wires it to
the web UI and to the RFID modules.

Naming (decided 2026-11-05): boxes are ``<column letter><row number>`` seen
from above, A-D left to right, 1-4 top to bottom. A wall is named by its two
boxes, left/top box first (``A1B1`` between side-by-side boxes, ``A1A2``
between stacked ones). Its gates are ``<wall>_a`` .. ``_d``, ordered top to
bottom along a vertical wall and left to right along a horizontal one.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_DIR = "/var/lib/saviour/controller/habitat"
LAYOUT_FILE = "layout.json"
MAP_FILE = "rfid_gate_map.json"
CHANGES_FILE = "rfid_gate_map_changes.jsonl"
DRAFT_FILE = "rfid_gate_map.draft.json"

GATE_LETTERS = "abcd"
MAX_COLS = 8
MAX_ROWS = 8

# Gate statuses in the map. A gate with no entry is unmapped.
MAPPED = "mapped"
CLOSED = "closed"
NOT_FITTED = "not_fitted"
FAULTY = "faulty"
STATUSES = (MAPPED, CLOSED, NOT_FITTED, FAULTY)

# How long reads keep being collected after the first one, to catch a
# neighbouring antenna also picking up the probe.
SETTLE_S = 1.5

_BOX_RE = re.compile(r"[A-Z][1-9]")
_OPERATOR_MAX = 60


class GateMapError(ValueError):
    """An invalid request; the message is safe to show a user."""


def _now() -> float:
    return time.time()


def unit_key(module_id: str, unit: int) -> str:
    return f"{module_id}:{int(unit)}"


def split_unit_key(key: str) -> tuple[str, int]:
    module_id, _, unit = key.rpartition(":")
    return module_id, int(unit)


# ── Layout ──────────────────────────────────────────────────────────────────

def box_id(col: int, row: int) -> str:
    return f"{chr(ord('A') + col)}{row + 1}"


def generate_layout(cols: int = 4, rows: int = 4,
                    gates_per_wall: int = 4) -> dict:
    """A grid layout with every internal wall's gates fitted."""
    if not (1 <= cols <= MAX_COLS and 1 <= rows <= MAX_ROWS):
        raise GateMapError(f"Grid must be 1-{MAX_COLS} x 1-{MAX_ROWS} boxes")
    if not 1 <= gates_per_wall <= len(GATE_LETTERS):
        raise GateMapError(f"Gates per wall must be 1-{len(GATE_LETTERS)}")
    boxes = [{"id": box_id(c, r), "col": c, "row": r}
             for r in range(rows) for c in range(cols)]
    walls = []
    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:   # side-by-side: a vertical wall
                walls.append({"id": box_id(c, r) + box_id(c + 1, r),
                              "boxes": [box_id(c, r), box_id(c + 1, r)],
                              "orientation": "vertical"})
            if r + 1 < rows:   # stacked: a horizontal wall
                walls.append({"id": box_id(c, r) + box_id(c, r + 1),
                              "boxes": [box_id(c, r), box_id(c, r + 1)],
                              "orientation": "horizontal"})
    gates = [{"id": f"{w['id']}_{GATE_LETTERS[i]}", "wall": w["id"],
              "index": i, "fitted": True}
             for w in walls for i in range(gates_per_wall)]
    return {"version": 1, "cols": cols, "rows": rows,
            "gates_per_wall": gates_per_wall,
            "boxes": boxes, "walls": walls, "gates": gates}


def validate_layout(layout: object) -> dict:
    """Check a layout from the web UI against the grid it claims to be:
    the boxes, walls and gate ids must be exactly what generate_layout makes
    for its size, so only ``fitted`` can differ. Returns a clean copy."""
    if not isinstance(layout, dict):
        raise GateMapError("Layout must be an object")
    try:
        cols = int(layout.get("cols"))
        rows = int(layout.get("rows"))
        per_wall = int(layout.get("gates_per_wall", len(GATE_LETTERS)))
    except (TypeError, ValueError):
        raise GateMapError(
            "Layout needs numeric cols, rows and gates_per_wall") from None
    clean = generate_layout(cols, rows, per_wall)
    given = layout.get("gates")
    if not isinstance(given, list):
        raise GateMapError("Layout gates must be a list")
    fitted = {}
    for g in given:
        if not isinstance(g, dict) or not isinstance(g.get("id"), str):
            raise GateMapError("Each gate needs an id")
        fitted[g["id"]] = bool(g.get("fitted", True))
    expected = {g["id"] for g in clean["gates"]}
    if set(fitted) != expected:
        raise GateMapError("Layout gates don't match its grid size")
    for g in clean["gates"]:
        g["fitted"] = fitted[g["id"]]
    return clean


def fitted_gates(layout: dict) -> list[str]:
    return [g["id"] for g in layout.get("gates", []) if g.get("fitted", True)]


def scope_gates(layout: dict, scope: object) -> list[str]:
    """Gate ids a mapping run covers, in layout order. ``scope`` is
    ``"all"``, ``{"wall": "A1B1"}`` or ``{"gates": [...]}``; only fitted
    gates are included."""
    order = fitted_gates(layout)
    if scope in (None, "all"):
        chosen = order
    elif isinstance(scope, dict) and isinstance(scope.get("wall"), str):
        chosen = [g for g in order if g.rpartition("_")[0] == scope["wall"]]
    elif isinstance(scope, dict) and isinstance(scope.get("gates"), list):
        wanted = {str(g) for g in scope["gates"]}
        unknown = wanted - set(order)
        if unknown:
            raise GateMapError(
                f"Not a fitted gate: {', '.join(sorted(unknown))}")
        chosen = [g for g in order if g in wanted]
    else:
        raise GateMapError("Scope must be all, a wall or a list of gates")
    if not chosen:
        raise GateMapError("No fitted gates in that scope")
    return chosen


# ── Files ───────────────────────────────────────────────────────────────────

def _write_json_atomic(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str) -> object | None:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


class GateMapStore:
    """The layout, map, change log and draft run on disk."""

    def __init__(self, directory: str = DEFAULT_DIR,
                 clock: Callable[[], float] = _now) -> None:
        self.dir = directory
        self.clock = clock
        self._lock = threading.Lock()

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def load_layout(self) -> dict:
        data = _read_json(self._path(LAYOUT_FILE))
        if data is None:
            return generate_layout()
        return validate_layout(data)

    def save_layout(self, layout: object) -> dict:
        clean = validate_layout(layout)
        with self._lock:
            _write_json_atomic(self._path(LAYOUT_FILE), clean)
        return clean

    def load_map(self) -> dict:
        data = _read_json(self._path(MAP_FILE))
        if not isinstance(data, dict):
            return {"version": 1, "updated_at": None, "entries": {}}
        data.setdefault("entries", {})
        return data

    def save_map(self, entries: dict, *, run_id: str | None, operator: str,
                 reason: str) -> list[dict]:
        """Replace the map with ``entries`` and append one change-log line
        per gate whose entry changed. Returns those lines."""
        ts = self.clock()
        with self._lock:
            old = self.load_map()["entries"]
            changes = []
            for gate in sorted(set(old) | set(entries)):
                before, after = old.get(gate), entries.get(gate)
                if before != after:
                    changes.append({"ts": ts, "run_id": run_id,
                                    "operator": operator, "gate": gate,
                                    "before": before, "after": after,
                                    "reason": reason})
            _write_json_atomic(self._path(MAP_FILE), {
                "version": 1, "updated_at": ts, "entries": entries})
            if changes:
                os.makedirs(self.dir, exist_ok=True)
                with open(self._path(CHANGES_FILE), "a") as f:
                    for c in changes:
                        f.write(json.dumps(c, sort_keys=True) + "\n")
            return changes

    def recent_changes(self, limit: int = 50) -> list[dict]:
        try:
            with open(self._path(CHANGES_FILE)) as f:
                lines = f.readlines()[-limit:]
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[::-1]

    def load_draft(self) -> dict | None:
        data = _read_json(self._path(DRAFT_FILE))
        return data if isinstance(data, dict) else None

    def save_draft(self, run: MappingRun) -> None:
        with self._lock:
            _write_json_atomic(self._path(DRAFT_FILE), run.to_dict())

    def delete_draft(self) -> None:
        try:
            os.unlink(self._path(DRAFT_FILE))
        except FileNotFoundError:
            pass


# ── Bus inventory (from the RFID modules) ───────────────────────────────────

@dataclass
class BusUnit:
    module_id: str
    unit: int
    serial: str | None = None
    firmware: str = ""

    @property
    def key(self) -> str:
        return unit_key(self.module_id, self.unit)


def units_from_ack(module_id: str, units: object) -> list[BusUnit]:
    """The ``units`` list of an ``rfid_units`` command reply."""
    out = []
    for u in units if isinstance(units, list) else []:
        if not isinstance(u, dict) or "address" not in u:
            continue
        try:
            out.append(BusUnit(module_id, int(u["address"]),
                               u.get("serial_number") or None,
                               str(u.get("firmware_version") or "")))
        except (TypeError, ValueError):
            continue
    return out


# ── Mapping run ─────────────────────────────────────────────────────────────

def mapped_units(entries: dict) -> dict[str, list[str]]:
    """unit key -> gates mapped to it."""
    out: dict[str, list[str]] = {}
    for gate, e in entries.items():
        if e.get("status") == MAPPED and e.get("module_id") is not None:
            out.setdefault(unit_key(e["module_id"], e["unit"]), []).append(gate)
    return out


@dataclass
class Capture:
    """Probe reads collected for the current gate."""
    first: str | None = None
    first_ts: float | None = None
    counts: dict[str, int] = field(default_factory=dict)
    settled: bool = False

    def to_dict(self) -> dict:
        return {"first": self.first, "first_ts": self.first_ts,
                "counts": dict(self.counts), "settled": self.settled}


@dataclass
class MappingRun:
    """The guided routine. Phases: ``probe`` (waiting for the probe tag),
    ``gate`` (prompting for ``scope[index]``), ``review``."""
    run_id: str
    operator: str
    scope: list[str]
    started_at: float
    base_entries: dict                 # the map when the run started
    phase: str = "probe"
    index: int = 0
    probe_tag: str | None = None
    prompt_ts: float | None = None
    results: dict = field(default_factory=dict)   # gate -> entry | None
    capture: Capture = field(default_factory=Capture)

    @classmethod
    def start(cls, operator: str, scope: list[str], base_entries: dict,
              now: float) -> MappingRun:
        operator = (operator or "").strip()[:_OPERATOR_MAX]
        if not operator:
            raise GateMapError("Enter your name or initials first")
        return cls(run_id=uuid.uuid4().hex[:12], operator=operator,
                   scope=list(scope), started_at=now,
                   base_entries=json.loads(json.dumps(base_entries)),
                   prompt_ts=now)

    # ── state ──

    @property
    def gate(self) -> str | None:
        if self.phase != "gate" or self.index >= len(self.scope):
            return None
        return self.scope[self.index]

    def entries(self) -> dict:
        """The map as it would be saved now: the base map with this run's
        decisions applied (``None`` = unmapped)."""
        out = dict(self.base_entries)
        for gate, entry in self.results.items():
            if entry is None:
                out.pop(gate, None)
            else:
                out[gate] = entry
        return out

    def issue(self) -> dict | None:
        """What the operator must resolve before confirming this gate."""
        c = self.capture
        if self.phase != "gate" or not c.settled or not c.counts:
            return None
        if len(c.counts) > 1:
            return {"kind": "multiple",
                    "units": sorted(c.counts, key=lambda k: -c.counts[k])}
        key = c.first
        others = [g for g in mapped_units(self.entries()).get(key, [])
                  if g != self.gate]
        if others:
            return {"kind": "already_mapped", "unit": key, "gates": others}
        return None

    # ── events ──

    def on_read(self, module_id: str, unit: int, tag: str, ts: float) -> bool:
        """A transponder read from any RFID module. Returns True if it
        changed the run (i.e. it was the probe, or registered it)."""
        tag = (tag or "").upper()
        if not tag or set(tag) == {"0"}:
            return False
        if self.phase == "probe":
            self.probe_tag = tag
            self.phase = "gate"
            self.index = 0
            self.prompt_ts = ts
            self.capture = Capture()
            return True
        if self.phase != "gate" or tag != self.probe_tag or self.capture.settled:
            return False
        key = unit_key(module_id, unit)
        c = self.capture
        if c.first is None:
            c.first, c.first_ts = key, ts
        c.counts[key] = c.counts.get(key, 0) + 1
        return True

    def settle_due(self, now: float) -> bool:
        c = self.capture
        return (self.phase == "gate" and c.first is not None and not c.settled
                and now - c.first_ts >= SETTLE_S)

    def settle(self) -> None:
        if self.capture.first is not None:
            self.capture.settled = True

    # ── operator actions ──

    def act(self, action: str, now: float, *, unit: str | None = None,
            resolve: str | None = None,
            serials: dict[str, str | None] | None = None) -> None:
        """``confirm`` (``unit`` picks one of several readers; ``resolve``
        is ``move`` or ``share`` when the unit is already another gate's),
        ``retry``, ``skip_closed``, ``skip_not_fitted``, ``faulty``,
        ``back``."""
        if action == "back":
            if self.phase == "review":
                self.phase, self.index = "gate", len(self.scope) - 1
            elif self.phase == "gate" and self.index > 0:
                self.index -= 1
            else:
                raise GateMapError("Already at the first gate")
            self._prompt(now)
            return
        gate = self.gate
        if gate is None:
            raise GateMapError("No gate is being mapped")
        if action == "retry":
            self._prompt(now)
            return
        if action == "confirm":
            self.results[gate] = self._confirm_entry(gate, now, unit, resolve,
                                                     serials or {})
        elif action in ("skip_closed", "skip_not_fitted", "faulty"):
            status = {"skip_closed": CLOSED, "skip_not_fitted": NOT_FITTED,
                      "faulty": FAULTY}[action]
            self.results[gate] = {"gate": gate, "status": status,
                                  "module_id": None, "unit": None,
                                  "unit_serial": None, "mapped_at": now,
                                  "run_id": self.run_id}
        else:
            raise GateMapError(f"Unknown action: {action}")
        self.index += 1
        if self.index >= len(self.scope):
            self.phase = "review"
        self._prompt(now)

    def _confirm_entry(self, gate, now, unit, resolve, serials) -> dict:
        c = self.capture
        if not c.settled or not c.counts:
            raise GateMapError("No antenna has read the probe at this gate yet")
        issue = self.issue()
        if issue and issue["kind"] == "multiple":
            if unit not in c.counts:
                raise GateMapError("Several units read the probe: pick one")
            key = unit
        else:
            key = c.first
        others = [g for g in mapped_units(self.entries()).get(key, [])
                  if g != gate]
        if others:
            if resolve == "move":
                for other in others:
                    self.results[other] = None
            elif resolve != "share":
                raise GateMapError(
                    f"{key} is already {', '.join(others)}: move it or share it")
        module_id, addr = split_unit_key(key)
        return {"gate": gate, "status": MAPPED, "module_id": module_id,
                "unit": addr, "unit_serial": serials.get(key),
                "mapped_at": now, "run_id": self.run_id}

    def _prompt(self, now: float) -> None:
        self.capture = Capture()
        self.prompt_ts = now

    # ── review ──

    def review(self, bus: dict[str, BusUnit] | None) -> dict:
        """Problems with the map as it would be saved, against the units
        the bus scan found (``None`` if no scan answered)."""
        return review_entries(self.entries(), bus)

    # ── persistence ──

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "operator": self.operator,
                "scope": self.scope, "started_at": self.started_at,
                "base_entries": self.base_entries, "phase": self.phase,
                "index": self.index, "probe_tag": self.probe_tag,
                "prompt_ts": self.prompt_ts, "results": self.results}

    @classmethod
    def from_dict(cls, d: dict, now: float) -> MappingRun:
        """Resume a draft. An in-progress capture is dropped (the operator
        passes the probe again)."""
        run = cls(run_id=str(d["run_id"]), operator=str(d["operator"]),
                  scope=list(d["scope"]), started_at=float(d["started_at"]),
                  base_entries=dict(d.get("base_entries") or {}),
                  phase=str(d.get("phase", "probe")),
                  index=int(d.get("index", 0)),
                  probe_tag=d.get("probe_tag"),
                  results=dict(d.get("results") or {}))
        if run.phase not in ("probe", "gate", "review"):
            raise GateMapError("Draft has an unknown phase")
        run.prompt_ts = now
        return run

    def view(self, bus: dict[str, BusUnit] | None) -> dict:
        """What the web UI shows."""
        d = self.to_dict()
        d.pop("base_entries")
        d.update(gate=self.gate, capture=self.capture.to_dict(),
                 issue=self.issue(), total=len(self.scope), settle_s=SETTLE_S)
        if self.phase == "review":
            d["review"] = self.review(bus)
            d["changes"] = diff_entries(self.base_entries, self.entries())
        return d


def diff_entries(before: dict, after: dict) -> list[dict]:
    return [{"gate": g, "before": before.get(g), "after": after.get(g)}
            for g in sorted(set(before) | set(after))
            if before.get(g) != after.get(g)]


def review_entries(entries: dict, bus: dict[str, BusUnit] | None) -> dict:
    """Checks on a whole map. ``bus`` maps unit key -> BusUnit from the
    latest scan; with no scan the bus checks are skipped."""
    by_unit = mapped_units(entries)
    shared = {k: g for k, g in by_unit.items() if len(g) > 1}
    out = {"shared_units": [{"unit": k, "gates": g}
                            for k, g in sorted(shared.items())],
           "scanned": bus is not None}
    if bus is None:
        out.update(on_bus_not_mapped=[], mapped_not_on_bus=[],
                   serial_changed=[])
        return out
    out["on_bus_not_mapped"] = sorted(k for k in bus if k not in by_unit)
    out["mapped_not_on_bus"] = [{"unit": k, "gates": g}
                                for k, g in sorted(by_unit.items())
                                if k not in bus]
    changed = []
    for gate, e in sorted(entries.items()):
        if e.get("status") != MAPPED:
            continue
        b = bus.get(unit_key(e["module_id"], e["unit"]))
        if b and b.serial and e.get("unit_serial") and b.serial != e["unit_serial"]:
            changed.append({"gate": gate, "unit": b.key,
                            "mapped_serial": e["unit_serial"],
                            "bus_serial": b.serial})
    out["serial_changed"] = changed
    return out


def gates_for_unit(entries: dict, module_id: str, unit: int) -> list[str]:
    """Walk-through lookup: the gates the current map says this unit is."""
    return mapped_units(entries).get(unit_key(module_id, unit), [])
