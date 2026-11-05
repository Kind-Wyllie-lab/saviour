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
AUDIOMOTH = "audiomoth"
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


def placement_state(modules: dict, boxes: list[str],
                    seen: dict[str, set[str]] | None = None) -> dict:
    """The current placement from the modules' names and labels.

    ``modules``: facade.get_modules() (id -> dict with type, name, online,
    status, config). ``seen``: module id -> AudioMoth serials heard from
    recently (audio_levels), which also lists ones not yet labelled.
    """
    seen = seen or {}
    cameras, moths, problems = [], [], []

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
            labels = _labels(m)
            heard = seen.get(mid, set())
            for serial in sorted(set(labels) | set(heard)):
                label = labels.get(serial) or ""
                box = label if label in boxes else None
                moths.append({"module_id": mid, "serial": serial, "label": label,
                              "box": box, "live": serial in heard,
                              "online": _online(m)})
                if box is None and label and normalise_box(label, boxes):
                    problems.append(f"AudioMoth {serial} is labelled '{label}', not on "
                                    f"the map (did you mean {normalise_box(label, boxes)}?)")
                elif box and _online(m) and serial not in heard:
                    problems.append(f"{box}: AudioMoth {serial} isn't sending levels "
                                    f"(unplugged, or monitoring off on {mid})")

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

    return {"boxes": [by_box[b] for b in boxes], "cameras": cameras,
            "audiomoths": moths, "problems": problems,
            "empty": {"cameras": [b for b in boxes if not by_box[b]["cameras"]],
                      "audiomoths": [b for b in boxes if not by_box[b]["audiomoths"]]}}


def plan(assignments: list[dict], state: dict, boxes: list[str]) -> dict:
    """Turn assignments into per-module config patches and change records.

    Each assignment: ``{"kind": "camera", "module_id", "box"}`` or
    ``{"kind": "audiomoth", "module_id", "serial", "box"}``; ``box`` None
    or "" unplaces it. Whatever already sits in a target box (and isn't
    itself being moved in this batch) is unplaced, so a box never ends up
    with two cameras or two AudioMoths.
    Returns ``{"patches": {module_id: patch}, "changes": [...]}``.
    """
    cams = {c["module_id"]: c for c in state["cameras"]}
    moths = {(a["module_id"], a["serial"]): a for a in state["audiomoths"]}
    target: dict[tuple, str | None] = {}

    for a in assignments:
        kind = a.get("kind")
        raw_box = a.get("box")
        box = None if raw_box in (None, "") else normalise_box(raw_box, boxes)
        if raw_box not in (None, "") and box is None:
            raise PlacementError(f"'{raw_box}' is not a box on the layout")
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
        target[key] = box

    for kind in (CAMERA, AUDIOMOTH):
        taken = [b for k, b in target.items() if k[0] == kind and b]
        dup = {b for b in taken if taken.count(b) > 1}
        if dup:
            raise PlacementError(f"Two {kind}s assigned to {', '.join(sorted(dup))}")

    # Displace current occupants of target boxes that aren't moving.
    for kind, current in ((CAMERA, {(CAMERA, c["module_id"]): c["box"] for c in cams.values()}),
                          (AUDIOMOTH, {(AUDIOMOTH, *k): a["box"] for k, a in moths.items()})):
        wanted = {b for k, b in target.items() if k[0] == kind and b}
        for key, box in current.items():
            if box in wanted and key not in target:
                target[key] = None

    patches: dict[str, dict] = {}
    changes = []
    for key, box in sorted(target.items(), key=lambda kv: tuple(map(str, kv[0]))):
        if key[0] == CAMERA:
            mid = key[1]
            before = cams[mid]["name"]
            after = box or ""
            if before == after or (not box and cams[mid]["box"] is None):
                continue
            patches.setdefault(mid, {})["module"] = {"name": after}
            changes.append({"kind": CAMERA, "module_id": mid, "serial": None,
                            "before": cams[mid]["box"], "after": box})
        else:
            mid, serial = key[1], key[2]
            moth = moths[(mid, serial)]
            after = box or ""
            if moth["label"] == after or (not box and moth["box"] is None):
                continue
            patches.setdefault(mid, {}).setdefault("audiomoth_labels", {})[serial] = after
            changes.append({"kind": AUDIOMOTH, "module_id": mid, "serial": serial,
                            "before": moth["box"], "after": box})
    return {"patches": patches, "changes": changes}


def parse_import(text: str, state: dict, boxes: list[str]) -> tuple[list[dict], list[str]]:
    """Pasted ``<camera module id | AudioMoth serial>, <box>`` lines (comma,
    tab or spaces; '#' comments and a header line are skipped) into
    assignments. Returns (assignments, errors)."""
    cams = {c["module_id"].upper(): c for c in state["cameras"]}
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
            errors.append(f"Line {n}: expected '<id or serial>, <box>', got '{line}'")
            continue
        key, raw_box = parts
        box = normalise_box(raw_box, boxes)
        if box is None:
            if not out and not errors and n == 1:
                continue                      # a header line
            errors.append(f"Line {n}: '{raw_box}' is not a box on the layout")
            continue
        k = key.upper()
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
            errors.append(f"Line {n}: '{key}' is not a connected camera module "
                          f"or a known AudioMoth serial")
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
