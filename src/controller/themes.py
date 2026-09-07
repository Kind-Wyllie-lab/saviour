"""On-disk store for operator-defined frontend colour themes.

Built-in themes (Default, SIDB, UofE, Wood, Kind, Pagan) are hardcoded in the
frontend (`src/basic/utils/themes.js`). A *custom* theme -- typically imported
from a Coolors palette via the "Import from Coolors" modal -- is persisted
here as one JSON file per theme, at `<active_config dir>/themes/<id>.json`
(so, by default, `/etc/saviour/controller/themes/`).

The frontend builds the full light+dark token maps (it owns the palette ->
role derivation, so the live preview and the saved result use one code path);
this store only *validates and persists* what it's handed. Validation is
strict -- every token value must be a `#rgb` / `#rrggbb` string -- because the
values are injected verbatim into a `<style>` tag on every client
(`useControllerTheme.js`).

A theme file is plain and hand-editable:

    {
      "id": "ocean",
      "name": "Ocean",
      "light": { "--bg-color": "#eef4f7", ... 8 keys ... },
      "dark":  { "--bg-color": "#0f1b22", ... 8 keys ... },
      "source": {
        "palette": ["#264653", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51"],
        "roles": {"bg": "#e9c46a", "card": "#e9c46a", "text": "#264653",
                  "accent": "#2a9d8f", "accentAlt": "#f4a261"},
        "raw": false
      }
    }

`source` is what lets the "Import from Coolors" modal reopen a saved theme
for editing instead of only offering delete -- it records the original
palette and the bg/card/text/accent/accentAlt role assignment (`roles: null`
means the palette was never manually reassigned; `light`/`dark` were derived
straight from the palette's darkest/lightest/most-vivid hexes, one pick per
mode) so the frontend can reconstruct the exact drag-and-drop state rather
than reverse-engineering it from the final colours. It's entirely optional --
omitted for a theme saved before this existed, or one hand-written directly
as a file -- and purely descriptive: this store never reads it, only
round-trips it verbatim (light/dark, not source, are what's ever rendered).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile

# Mirror of THEME_TOKENS in src/basic/utils/themes.js -- keep in sync.
TOKEN_KEYS = (
    "--bg-color",
    "--card-bg-color",
    "--text-color",
    "--secondary-text-color",
    "--accent-color",
    "--accent-color-alt",
    "--border-color",
    "--button-color",
)

# Built-in theme ids the frontend ships -- a custom theme may not shadow one.
RESERVED_IDS = frozenset({"default", "sidb", "uofe", "wood", "kind", "pagan"})

# Mirror of PALETTE_ROLES in src/basic/utils/themes.js -- keep in sync.
_ROLE_KEYS = ("bg", "card", "text", "accent", "accentAlt")
_MAX_PALETTE_LEN = 16

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_DEFAULT_DIR = "/etc/saviour/controller/themes"
_MAX_NAME_LEN = 60


class ThemeError(ValueError):
    """Raised for an invalid theme payload; message is safe to show a user."""


def _slugify(name: str) -> str:
    slug = _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "theme"


class ThemeStore:
    def __init__(self, themes_dir: str | None = None,
                 logger: logging.Logger | None = None):
        self.dir = themes_dir or _DEFAULT_DIR
        self.logger = logger or logging.getLogger(__name__)

    # -- reads --------------------------------------------------------------

    def list(self) -> list[dict]:
        """Every valid theme file in the directory, sorted by display name.

        A malformed or unreadable file is logged and skipped rather than
        breaking the whole list (and therefore the Settings page)."""
        try:
            names = [n for n in os.listdir(self.dir) if n.endswith(".json")]
        except FileNotFoundError:
            return []
        out: list[dict] = []
        for fname in names:
            path = os.path.join(self.dir, fname)
            try:
                with open(path) as f:
                    raw = json.load(f)
                theme = self._normalise(raw, fallback_id=fname[:-5])
            except (OSError, ValueError) as e:
                self.logger.warning(f"Skipping invalid theme file {fname}: {e}")
                continue
            out.append(theme)
        out.sort(key=lambda t: t["name"].lower())
        return out

    # -- writes -----------------------------------------------------------

    def save(self, payload: dict) -> dict:
        """Validate, normalise and persist a theme. Returns the stored theme.

        `id` is derived from `name` when absent. Raises ThemeError on any
        validation failure or on a collision with a built-in id."""
        theme = self._normalise(payload, fallback_id=None)

        # Give it an id if the caller didn't, keeping it unique against other
        # files (but letting a save with an explicit id overwrite itself).
        if not theme.get("id"):
            base = _slugify(theme["name"])
            theme["id"] = base
            n = 2
            while os.path.exists(os.path.join(self.dir, f"{theme['id']}.json")):
                theme["id"] = f"{base}-{n}"
                n += 1

        if theme["id"] in RESERVED_IDS:
            raise ThemeError(
                f"'{theme['id']}' is a built-in theme name; choose another"
            )

        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"{theme['id']}.json")
        # Atomic write so a crash mid-write can't leave a half file the
        # list() loop would then have to skip.
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(theme, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
        self.logger.info(f"Saved custom theme '{theme['id']}' to {path}")
        return theme

    def delete(self, theme_id: str) -> bool:
        """Remove a custom theme file. Returns False if it wasn't there.

        Built-in ids are refused outright (there's no file to remove and the
        id should never reach here)."""
        if theme_id in RESERVED_IDS:
            raise ThemeError("Built-in themes cannot be deleted")
        if not re.fullmatch(r"[a-z0-9-]+", theme_id or ""):
            raise ThemeError("Invalid theme id")
        path = os.path.join(self.dir, f"{theme_id}.json")
        try:
            os.remove(path)
        except FileNotFoundError:
            return False
        self.logger.info(f"Deleted custom theme '{theme_id}'")
        return True

    # -- validation -----------------------------------------------------

    def _normalise(self, raw: object, fallback_id: str | None) -> dict:
        if not isinstance(raw, dict):
            raise ThemeError("Theme must be an object")

        name = str(raw.get("name", "")).strip()
        if not name:
            raise ThemeError("A theme name is required")
        if len(name) > _MAX_NAME_LEN:
            raise ThemeError(
                f"Theme name is too long (max {_MAX_NAME_LEN} characters)"
            )

        theme_id = raw.get("id") or fallback_id or ""
        theme_id = str(theme_id).strip().lower()
        if theme_id and not re.fullmatch(r"[a-z0-9-]+", theme_id):
            raise ThemeError("Theme id may only contain letters, digits and hyphens")

        modes = {}
        for mode in ("light", "dark"):
            block = raw.get(mode)
            if not isinstance(block, dict):
                raise ThemeError(f"Theme is missing its '{mode}' colours")
            clean = {}
            for key in TOKEN_KEYS:
                val = block.get(key)
                if not isinstance(val, str) or not _HEX_RE.match(val):
                    raise ThemeError(
                        f"{mode} {key} must be a hex colour like #1a2b3c (got {val!r})"
                    )
                clean[key] = val.lower()
            modes[mode] = clean

        result = {"id": theme_id, "name": name, "light": modes["light"],
                  "dark": modes["dark"]}
        source = self._normalise_source(raw.get("source"))
        if source is not None:
            result["source"] = source
        return result

    def _normalise_source(self, raw_source: object) -> dict | None:
        """Validate the optional provenance block (see the module docstring).
        Never raises for an absent block -- only a malformed *present* one,
        since older/hand-written theme files simply won't have it."""
        if raw_source is None:
            return None
        if not isinstance(raw_source, dict):
            raise ThemeError("Theme source must be an object")
        out: dict = {}

        palette = raw_source.get("palette")
        if palette is not None:
            is_list = isinstance(palette, list)
            if not is_list or not (1 <= len(palette) <= _MAX_PALETTE_LEN):
                raise ThemeError(
                    f"Theme source palette must be a list of "
                    f"1-{_MAX_PALETTE_LEN} hex colours"
                )
            clean_palette = []
            for val in palette:
                if not isinstance(val, str) or not _HEX_RE.match(val):
                    raise ThemeError(
                        "Theme source palette colour must be a hex colour "
                        f"like #1a2b3c (got {val!r})"
                    )
                clean_palette.append(val.lower())
            out["palette"] = clean_palette

        roles = raw_source.get("roles")
        if roles is None:
            out["roles"] = None
        else:
            if not isinstance(roles, dict) or set(roles) != set(_ROLE_KEYS):
                raise ThemeError(
                    "Theme source roles must have exactly bg/card/text/accent/accentAlt"
                )
            clean_roles = {}
            for key in _ROLE_KEYS:
                val = roles[key]
                if not isinstance(val, str) or not _HEX_RE.match(val):
                    raise ThemeError(
                        f"Theme source role '{key}' must be a hex colour "
                        f"like #1a2b3c (got {val!r})"
                    )
                clean_roles[key] = val.lower()
            out["roles"] = clean_roles

        out["raw"] = bool(raw_source.get("raw", False))
        return out
