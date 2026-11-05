"""
Which camera and which AudioMoth is in which habitat box.

A box's camera is the camera module *named* after it (``module.name`` =
"B3"), and its AudioMoth is the one *labelled* after it
(``audiomoth_labels[serial]`` = "B3" on its microphone module). The name is
also the module's export folder, so recordings are filed by box. This
module reads those names/labels into one placement view, checks it, turns
assignments (from the dashboard's Placement panel or a pasted import) into
config patches for the modules, and logs every change.

The functions are pure (plain dicts in and out) so they test without a
controller; PlacementLog is the only file I/O and PlacementService the
live wiring.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable

CAMERA = "camera"
MIC = "mic"
AUDIOMOTH = "audiomoth"
KIND_NAMES = {CAMERA: "cameras", MIC: "microphone modules", AUDIOMOTH: "AudioMoths"}
CHANGES_FILE = "placement_changes.jsonl"
# An AudioMoth counts as live if its levels arrived this recently.
SEEN_S = 10.0


class PlacementError(Exception):
    pass


def is_camera(m: dict) -> bool:
    return "camera" in (m.get("type") or "")


def is_mic(m: dict) -> bool:
    return m.get("type") == "microphone"


def _online(m: dict) -> bool:
    return bool(m.get("online", True)) and m.get("status") != "OFFLINE"


def _norm(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).upper()


def normalise_box(text: object, boxes: list[str]) -> str | None:
    """'b 3' -> 'B3' if that's a box on the layout, else None."""
    t = _norm(text)
    return t if t in boxes else None


def _labels(m: dict) -> dict:
    labels = (m.get("config") or {}).get("audiomoth_labels") or {}
    return labels if isinstance(labels, dict) else {}


def columns_of(boxes: list[str]) -> list[str]:
    """Column letters of the layout's boxes, in order ('A1' -> 'A')."""
    return list(dict.fromkeys(_col(b) for b in boxes))


def _col(box: str) -> str:
    return re.match(r"[A-Z]+", box).group(0)


def normalise_column(text: object, columns: list[str]) -> str | None:
    t = _norm(text)
    return t if t in columns else None


def placement_state(modules: dict, boxes: list[str],
                    seen: dict[str, set[str]] | None = None) -> dict:
    """The current placement from the modules' names and labels.

    ``modules``: facade.get_modules() (id -> dict with type, name, online,
    status, config). ``seen``: module id -> AudioMoth serials heard from
    recently (audio_levels), which also lists ones not yet labelled.

    A microphone module's column is its name ("A"): its AudioMoths' cables
    only reach that column's boxes, so they're offered (and checked
    against) those boxes only. A module with no column may use any box.
    """
    seen = seen or {}
    columns = columns_of(boxes)
    cameras, mics, moths, problems = [], [], [], []

    for mid, m in sorted(modules.items()):
        if is_camera(m):
            name = m.get("name") or ""
            box = name if name in boxes else None
            cameras.append({"module_id": mid, "name": name, "box": box,
                            "online": _online(m), "recording": m.get("status") == "RECORDING"})
            if box is None and normalise_box(name, boxes):
                problems.append(f"Camera {mid} is named '{name}', not on the map "
                                f"(did you mean {normalise_box(name, boxes)}?)")
            elif box and not _online(m):
                problems.append(f"{box}: camera {mid} is offline")
        elif is_mic(m):
            name = m.get("name") or ""
            column = name if name in columns else None
            mics.append({"module_id": mid, "name": name, "column": column,
                         "online": _online(m), "recording": m.get("status") == "RECORDING"})
            if column is None and normalise_column(name, columns):
                problems.append(f"Microphone module {mid} is named '{name}', not a "
                                f"column (did you mean {normalise_column(name, columns)}?)")
            labels = _labels(m)
            heard = seen.get(mid, set())
            for serial in sorted(set(labels) | set(heard)):
                label = labels.get(serial) or ""
                box = label if label in boxes else None
                moths.append({"module_id": mid, "serial": serial, "label": label,
                              "box": box, "column": column, "live": serial in heard,
                              "online": _online(m)})
                if box is None and label and normalise_box(label, boxes):
                    problems.append(f"AudioMoth {serial} is labelled '{label}', not on "
                                    f"the map (did you mean {normalise_box(label, boxes)}?)")
                elif box and column and _col(box) != column:
                    problems.append(f"{box}: AudioMoth {serial} is on {mid}, which is "
                                    f"column {column}")
                elif box and _online(m) and serial not in heard:
                    problems.append(f"{box}: AudioMoth {serial} isn't sending levels "
                                    f"(unplugged, or monitoring off on {mid})")

    for col in columns:
        on = [x["module_id"] for x in mics if x["column"] == col]
        if len(on) > 1:
            problems.append(f"Column {col}: {len(on)} microphone modules ({', '.join(on)})")

    by_box = {b: {"id": b, "cameras": [], "audiomoths": []} for b in boxes}
    for c in cameras:
        if c["box"]:
            by_box[c["box"]]["cameras"].append(c["module_id"])
    for a in moths:
        if a["box"]:
            by_box[a["box"]]["audiomoths"].append(a["serial"])
    for b in by_box.values():
        if len(b["cameras"]) > 1:
            problems.append(f"{b['id']}: {len(b['cameras'])} cameras "
                            f"({', '.join(b['cameras'])})")
        if len(b["audiomoths"]) > 1:
            problems.append(f"{b['id']}: {len(b['audiomoths'])} AudioMoths "
                            f"({', '.join(b['audiomoths'])})")

    return {"boxes": [by_box[b] for b in boxes], "columns": columns,
            "cameras": cameras, "mics": mics, "audiomoths": moths,
            "problems": problems,
            "empty": {"cameras": [b for b in boxes if not by_box[b]["cameras"]],
                      "audiomoths": [b for b in boxes if not by_box[b]["audiomoths"]]}}


def plan(assignments: list[dict], state: dict, boxes: list[str]) -> dict:
    """Turn assignments into per-module config patches and change records.

    Assignments:
      ``{"kind": "camera", "module_id", "box"}``
      ``{"kind": "mic", "module_id", "column"}``
      ``{"kind": "audiomoth", "module_id", "serial", "box"}``
    An empty/None box or column unplaces it. Whatever already holds a target
    box or column (and isn't itself moving in this batch) is unplaced, so a
    box never has two cameras or AudioMoths and a column never two
    microphone modules. An AudioMoth must go in its module's column (as it
    is or as this batch sets it).
    Returns ``{"patches": {module_id: patch}, "changes": [...]}``.
    """
    columns = state.get("columns") or columns_of(boxes)
    cams = {c["module_id"]: c for c in state["cameras"]}
    mics = {x["module_id"]: x for x in state.get("mics", [])}
    moths = {(a["module_id"], a["serial"]): a for a in state["audiomoths"]}
    target: dict[tuple, str | None] = {}

    for a in assignments:
        kind = a.get("kind")
        if kind == MIC:
            raw = a.get("column")
            spot = None if raw in (None, "") else normalise_column(raw, columns)
            if raw not in (None, "") and spot is None:
                raise PlacementError(f"'{raw}' is not a column on the layout")
            if a.get("module_id") not in mics:
                raise PlacementError(f"No microphone module '{a.get('module_id')}'")
            key = (MIC, a["module_id"])
        else:
            raw = a.get("box")
            spot = None if raw in (None, "") else normalise_box(raw, boxes)
            if raw not in (None, "") and spot is None:
                raise PlacementError(f"'{raw}' is not a box on the layout")
            if kind == CAMERA:
                if a.get("module_id") not in cams:
                    raise PlacementError(f"No camera module '{a.get('module_id')}'")
                key = (CAMERA, a["module_id"])
            elif kind == AUDIOMOTH:
                k = (a.get("module_id"), a.get("serial"))
                if k not in moths:
                    raise PlacementError(f"No AudioMoth {a.get('serial')} on "
                                         f"{a.get('module_id')}")
                key = (AUDIOMOTH, *k)
            else:
                raise PlacementError(f"Unknown kind '{kind}'")
        if key in target:
            raise PlacementError(f"{key[-1]} is assigned twice")
        target[key] = spot

    for kind in (CAMERA, MIC, AUDIOMOTH):
        taken = [s for k, s in target.items() if k[0] == kind and s]
        dup = {s for s in taken if taken.count(s) > 1}
        if dup:
            raise PlacementError(f"Two {KIND_NAMES[kind]} assigned to {', '.join(sorted(dup))}")

    # Each microphone module's column after this batch.
    column_of = {mid: target.get((MIC, mid), x["column"]) for mid, x in mics.items()}
    for key, box in target.items():
        if key[0] == AUDIOMOTH and box:
            col = column_of.get(key[1])
            if col and _col(box) != col:
                raise PlacementError(f"{box} isn't in column {col}, where {key[1]} "
                                     f"(AudioMoth {key[2]}) is")

    # Displace current occupants of target spots that aren't moving.
    current = {
        CAMERA: {(CAMERA, mid): c["box"] for mid, c in cams.items()},
        MIC: {(MIC, mid): x["column"] for mid, x in mics.items()},
        AUDIOMOTH: {(AUDIOMOTH, *k): a["box"] for k, a in moths.items()},
    }
    for kind, spots in current.items():
        wanted = {s for k, s in target.items() if k[0] == kind and s}
        for key, spot in spots.items():
            if spot in wanted and key not in target:
                target[key] = None

    patches: dict[str, dict] = {}
    changes = []
    for key, spot in sorted(target.items(), key=lambda kv: tuple(map(str, kv[0]))):
        kind, mid = key[0], key[1]
        if kind in (CAMERA, MIC):
            item = cams[mid] if kind == CAMERA else mics[mid]
            placed = item["box"] if kind == CAMERA else item["column"]
            if item["name"] == (spot or "") or (not spot and placed is None):
                continue
            patches.setdefault(mid, {})["module"] = {"name": spot or ""}
            changes.append({"kind": kind, "module_id": mid, "serial": None,
                            "before": placed, "after": spot})
        else:
            serial = key[2]
            moth = moths[(mid, serial)]
            if moth["label"] == (spot or "") or (not spot and moth["box"] is None):
                continue
            patches.setdefault(mid, {}).setdefault("audiomoth_labels", {})[serial] = spot or ""
            changes.append({"kind": AUDIOMOTH, "module_id": mid, "serial": serial,
                            "before": moth["box"], "after": spot})
    return {"patches": patches, "changes": changes}


def parse_import(text: str, state: dict, boxes: list[str]) -> tuple[list[dict], list[str]]:
    """Pasted lines into assignments: ``<camera module id>, <box>``,
    ``<microphone module id>, <column>`` or ``<AudioMoth serial>, <box>``
    (comma, tab or spaces; '#' comments and a header line are skipped).
    Returns (assignments, errors)."""
    columns = state.get("columns") or columns_of(boxes)
    cams = {c["module_id"].upper(): c for c in state["cameras"]}
    mics = {x["module_id"].upper(): x for x in state.get("mics", [])}
    moths: dict[str, list[dict]] = {}
    for a in state["audiomoths"]:
        moths.setdefault(a["serial"].upper(), []).append(a)

    out, errors = [], []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"[,\t;]", line) if p.strip()]
        if len(parts) == 1:
            parts = line.split()
        if len(parts) != 2:
            errors.append(f"Line {n}: expected '<id or serial>, <box or column>', "
                          f"got '{line}'")
            continue
        key, raw = parts
        k = key.upper()
        if k in mics:
            column = normalise_column(raw, columns)
            if column is None:
                errors.append(f"Line {n}: '{raw}' is not a column on the layout")
            else:
                out.append({"kind": MIC, "module_id": mics[k]["module_id"],
                            "column": column})
            continue
        box = normalise_box(raw, boxes)
        if box is None:
            if not out and not errors and n == 1 and k not in cams and k not in moths:
                continue                      # a header line
            errors.append(f"Line {n}: '{raw}' is not a box on the layout")
            continue
        if k in cams:
            out.append({"kind": CAMERA, "module_id": cams[k]["module_id"], "box": box})
        elif k in moths:
            if len(moths[k]) > 1:
                errors.append(f"Line {n}: AudioMoth {key} is on more than one module")
                continue
            a = moths[k][0]
            out.append({"kind": AUDIOMOTH, "module_id": a["module_id"],
                        "serial": a["serial"], "box": box})
        else:
            errors.append(f"Line {n}: '{key}' is not a connected camera or microphone "
                          f"module, or a known AudioMoth serial")
    return out, errors


def _filter_private(d: dict) -> dict:
    return {k: (_filter_private(v) if isinstance(v, dict) else v)
            for k, v in d.items() if not str(k).startswith("_")}


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class PlacementLog:
    """Append-only placement_changes.jsonl next to the gate map files."""

    def __init__(self, directory: str, clock: Callable[[], float] = time.time) -> None:
        self.path = os.path.join(directory, CHANGES_FILE)
        self.clock = clock
        self._lock = threading.Lock()

    def append(self, changes: list[dict], *, reason: str, operator: str) -> None:
        if not changes:
            return
        ts = self.clock()
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as f:
                for c in changes:
                    f.write(json.dumps({**c, "ts": ts, "reason": reason,
                                        "operator": operator}, sort_keys=True) + "\n")

    def recent(self, limit: int = 20) -> list[dict]:
        try:
            with open(self.path) as f:
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


class PlacementService:
    """Placement panel wiring: state from the live module list, applies
    through the same set_config round trip as the Settings page."""

    def __init__(self, log: PlacementLog, *,
                 get_modules: Callable[[], dict],
                 get_boxes: Callable[[], list[str]],
                 set_target_config: Callable[[str, dict], None],
                 send_command: Callable[[str, str, dict], None],
                 is_recording: Callable[[str], bool],
                 emit: Callable[[str, dict], None],
                 logger: logging.Logger | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.log = log
        self.get_modules = get_modules
        self.get_boxes = get_boxes
        self.set_target_config = set_target_config
        self.send_command = send_command
        self.is_recording = is_recording
        self.emit = emit
        self.logger = logger or logging.getLogger(__name__)
        self.clock = clock
        self._seen: dict[str, dict[str, float]] = {}   # module -> serial -> ts
        self._lock = threading.Lock()

    def on_module_status(self, module_id: str, status: dict) -> None:
        if status.get("type") != "audio_levels":
            return
        now = self.clock()
        with self._lock:
            heard = self._seen.setdefault(module_id, {})
            for lv in status.get("levels") or []:
                if lv.get("serial"):
                    heard[str(lv["serial"])] = now

    def _seen_now(self) -> dict[str, set[str]]:
        now = self.clock()
        with self._lock:
            return {mid: {s for s, t in heard.items() if now - t < SEEN_S}
                    for mid, heard in self._seen.items()}

    def state(self) -> dict:
        s = placement_state(self.get_modules(), self.get_boxes(), self._seen_now())
        s["changes"] = self.log.recent(20)
        return s

    def preview_import(self, text: str) -> dict:
        boxes = self.get_boxes()
        state = placement_state(self.get_modules(), boxes, self._seen_now())
        assignments, errors = parse_import(text, state, boxes)
        changes = []
        if assignments:
            try:
                changes = plan(assignments, state, boxes)["changes"]
            except PlacementError as e:
                errors.append(str(e))
        return {"assignments": assignments, "changes": changes, "errors": errors}

    def apply(self, assignments: list[dict], *, reason: str, operator: str) -> dict:
        modules = self.get_modules()
        boxes = self.get_boxes()
        state = placement_state(modules, boxes, self._seen_now())
        result = plan(assignments, state, boxes)
        busy = [mid for mid in result["patches"] if self.is_recording(mid)]
        if busy:
            raise PlacementError("Can't change a module while it records: "
                                 + ", ".join(sorted(busy)))
        missing = [mid for mid in result["patches"]
                   if not (modules.get(mid) or {}).get("config")]
        if missing:
            raise PlacementError("No config reported yet by " + ", ".join(sorted(missing))
                                 + "; try again in a moment")
        for mid, patch in result["patches"].items():
            merged = deep_merge(_filter_private(modules[mid]["config"]), patch)
            self.set_target_config(mid, merged)
            self.send_command(mid, "set_config", merged)
        self.log.append(result["changes"], reason=reason, operator=operator)
        self.logger.info(f"Placement ({reason}): {len(result['changes'])} change(s) "
                         f"to {len(result['patches'])} module(s)")
        return result


def register_socket_handlers(sio, svc: PlacementService, *,
                             require_auth: Callable[[str, dict], bool],
                             logger: logging.Logger) -> None:
    """get_placement (anyone) and placement_preview_import / placement_apply
    (admins). Replies go to the caller: placement_state,
    placement_preview, placement_error."""
    from flask import request

    def reply(event: str, data: dict) -> None:
        sio.emit(event, data, room=request.sid)

    def guarded(fn, *, admin: bool):
        def wrapped(data=None):
            if admin and not require_auth(
                    "placement_error", {"error": "Login required to change placement"}):
                return
            try:
                fn(data if isinstance(data, dict) else {})
            except PlacementError as e:
                reply("placement_error", {"error": str(e)})
            except Exception as e:
                logger.error(f"Placement handler failed: {e}")
                reply("placement_error", {"error": f"Unexpected error: {e}"})
        return wrapped

    def operator() -> str:
        return request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
            or (request.remote_addr or "")

    def apply(d: dict) -> None:
        reason = "import" if d.get("reason") == "import" else "panel"
        result = svc.apply(d.get("assignments") or [], reason=reason, operator=operator())
        reply("placement_applied", {"changes": result["changes"]})
        sio.emit("placement_state", svc.state())

    events = {
        "get_placement": (lambda d: reply("placement_state", svc.state()), False),
        "placement_preview_import": (
            lambda d: reply("placement_preview", svc.preview_import(str(d.get("text") or ""))),
            True),
        "placement_apply": (apply, True),
    }
    for name, (fn, admin) in events.items():
        sio.on_event(name, guarded(fn, admin=admin))
