"""On-disk store for operator-defined dashboard layouts ("Saved Views").

The basic-variant Dashboard is a free canvas of draggable/resizable tiles
(camera & sensor streams, plus status widgets like the health summary and
module list). A *view* is one named arrangement of those tiles. Views are
persisted here -- one JSON file per view, at
`<active_config dir>/dashboard_views/<id>.json` (so, by default,
`/etc/saviour/controller/dashboard_views/`) -- so every browser pointed at a
controller sees the same set of views rather than a per-browser layout.

A view file is plain and hand-editable:

    {
      "id": "overview",
      "name": "Overview",
      "group": "",                       # optional module-group filter ("" = all)
      "widgets": [
        {"id": "stream:camera_ab12", "type": "stream", "target": "camera_ab12"},
        {"id": "widget:health",      "type": "health"},
        {"id": "widget:module-list", "type": "module-list"}
      ],
      "layout": {
        "stream:camera_ab12": {"x": 0,   "y": 0, "width": 440},
        "widget:health":      {"x": 900, "y": 0, "width": 340, "height": 300}
      }
    }

The frontend owns what the widgets mean and how they're rendered; this store
only validates and persists structure. The default-view id lives in a sibling
`_meta.json` so changing it doesn't rewrite every view file.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile

WIDGET_TYPES = frozenset({"stream", "health", "module-list"})

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"[a-z0-9-]+")
_DEFAULT_DIR = "/etc/saviour/controller/dashboard_views"
_META_FILE = "_meta.json"
_MAX_NAME_LEN = 60
_MAX_WIDGETS = 64
# A widget instance id -- "stream:camera_ab12", "widget:health". Kept liberal
# (the frontend generates these) but bounded and free of path separators so it
# can never influence a filename or JSON structure downstream.
_WIDGET_ID_RE = re.compile(r"[A-Za-z0-9_:-]{1,80}")
_COORD_MAX = 20000  # px; a generous bound so a corrupt value can't blow up layout


class ViewError(ValueError):
    """Raised for an invalid view payload; message is safe to show a user."""


def _slugify(name: str) -> str:
    slug = _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "view"


def _num(value: object, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(n):
        return default
    return n


class DashboardViewStore:
    def __init__(self, views_dir: str | None = None,
                 logger: logging.Logger | None = None):
        self.dir = views_dir or _DEFAULT_DIR
        self.logger = logger or logging.getLogger(__name__)

    # -- reads ------------------------------------------------------------

    def list(self) -> list[dict]:
        """Every valid view file, sorted by display name. A malformed file is
        logged and skipped rather than breaking the whole list."""
        try:
            names = [
                n for n in os.listdir(self.dir)
                if n.endswith(".json") and not n.startswith("_")
            ]
        except FileNotFoundError:
            return []
        out: list[dict] = []
        for fname in names:
            path = os.path.join(self.dir, fname)
            try:
                with open(path) as f:
                    raw = json.load(f)
                out.append(self._normalise(raw, fallback_id=fname[:-5]))
            except (OSError, ValueError) as e:
                self.logger.warning(f"Skipping invalid dashboard view {fname}: {e}")
        out.sort(key=lambda v: v["name"].lower())
        return out

    def get_default_id(self) -> str:
        """The id marked default, or "" if unset / no longer present."""
        try:
            with open(os.path.join(self.dir, _META_FILE)) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return ""
        default_id = str((meta or {}).get("default_id", "") or "")
        if not default_id:
            return ""
        if not os.path.exists(os.path.join(self.dir, f"{default_id}.json")):
            return ""
        return default_id

    # -- writes ---------------------------------------------------------

    def save(self, payload: dict) -> dict:
        """Validate, normalise and persist a view. Returns the stored view.
        `id` is derived from `name` when absent. Raises ViewError on any
        validation failure."""
        view = self._normalise(payload, fallback_id=None)

        if not view.get("id"):
            base = _slugify(view["name"])
            view["id"] = base
            n = 2
            while os.path.exists(os.path.join(self.dir, f"{view['id']}.json")):
                view["id"] = f"{base}-{n}"
                n += 1

        os.makedirs(self.dir, exist_ok=True)
        self._atomic_write(f"{view['id']}.json", view)
        self.logger.info(f"Saved dashboard view '{view['id']}'")
        return view

    def delete(self, view_id: str) -> bool:
        """Remove a view file. Returns False if it wasn't there. Clears the
        default marker if it pointed here."""
        view_id = self._clean_id(view_id)
        try:
            os.remove(os.path.join(self.dir, f"{view_id}.json"))
        except FileNotFoundError:
            return False
        if self.get_default_id() == view_id:
            self._write_meta("")
        self.logger.info(f"Deleted dashboard view '{view_id}'")
        return True

    def set_default_id(self, view_id: str) -> None:
        """Mark `view_id` as the default view. "" clears it. Raises ViewError
        if the id is set but no such view exists."""
        if not view_id:
            self._write_meta("")
            return
        view_id = self._clean_id(view_id)
        if not os.path.exists(os.path.join(self.dir, f"{view_id}.json")):
            raise ViewError("No such view")
        self._write_meta(view_id)

    # -- internals ----------------------------------------------------

    def _clean_id(self, view_id: object) -> str:
        view_id = str(view_id or "").strip().lower()
        if not _ID_RE.fullmatch(view_id):
            raise ViewError("Invalid view id")
        return view_id

    def _write_meta(self, default_id: str) -> None:
        os.makedirs(self.dir, exist_ok=True)
        self._atomic_write(_META_FILE, {"default_id": default_id})

    def _atomic_write(self, fname: str, obj: dict) -> None:
        path = os.path.join(self.dir, fname)
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(obj, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _normalise(self, raw: object, fallback_id: str | None) -> dict:
        if not isinstance(raw, dict):
            raise ViewError("View must be an object")

        name = str(raw.get("name", "")).strip()
        if not name:
            raise ViewError("A view name is required")
        if len(name) > _MAX_NAME_LEN:
            raise ViewError(f"View name is too long (max {_MAX_NAME_LEN} characters)")

        view_id = raw.get("id") or fallback_id or ""
        view_id = str(view_id).strip().lower()
        if view_id and not _ID_RE.fullmatch(view_id):
            raise ViewError("View id may only contain letters, digits and hyphens")

        group = str(raw.get("group", "") or "").strip()
        if len(group) > _MAX_NAME_LEN:
            raise ViewError("Group filter is too long")

        return {
            "id": view_id,
            "name": name,
            "group": group,
            "widgets": self._clean_widgets(raw.get("widgets", [])),
            "layout": self._clean_layout(raw.get("layout", {})),
        }

    @staticmethod
    def _clean_widgets(widgets_raw: object) -> list[dict]:
        if not isinstance(widgets_raw, list):
            raise ViewError("'widgets' must be a list")
        if len(widgets_raw) > _MAX_WIDGETS:
            raise ViewError(f"Too many widgets (max {_MAX_WIDGETS})")
        widgets: list[dict] = []
        seen_ids: set[str] = set()
        for item in widgets_raw:
            if not isinstance(item, dict):
                raise ViewError("Each widget must be an object")
            wtype = str(item.get("type", "")).strip()
            if wtype not in WIDGET_TYPES:
                raise ViewError(f"Unknown widget type {wtype!r}")
            wid = str(item.get("id", "")).strip()
            if not _WIDGET_ID_RE.fullmatch(wid):
                raise ViewError("Invalid widget id")
            if wid in seen_ids:
                raise ViewError(f"Duplicate widget id {wid!r}")
            seen_ids.add(wid)
            widget = {"id": wid, "type": wtype}
            if wtype == "stream":
                target = str(item.get("target", "")).strip()
                if not target:
                    raise ViewError("A stream widget needs a target module id")
                widget["target"] = target
            widgets.append(widget)
        return widgets

    @staticmethod
    def _clean_layout(layout_raw: object) -> dict:
        if not isinstance(layout_raw, dict):
            raise ViewError("'layout' must be an object")
        layout: dict = {}
        for raw_key, geom in layout_raw.items():
            key = str(raw_key).strip()
            if not _WIDGET_ID_RE.fullmatch(key) or not isinstance(geom, dict):
                continue  # drop a junk entry rather than reject the whole view
            slot = {
                "x": max(0.0, min(_COORD_MAX, _num(geom.get("x")))),
                "y": max(0.0, min(_COORD_MAX, _num(geom.get("y")))),
                "width": max(1.0, min(_COORD_MAX, _num(geom.get("width"), 440))),
            }
            if geom.get("height") is not None:
                slot["height"] = max(
                    1.0, min(_COORD_MAX, _num(geom.get("height"), 280))
                )
            layout[key] = slot
        return layout
