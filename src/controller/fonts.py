"""On-disk store for operator-added frontend fonts.

Built-in fonts are system font stacks hardcoded in the frontend
(`src/basic/utils/fonts.js`): they use whatever the viewing device has
installed and need no files. An *added* font is uploaded by an operator's
browser and served by the controller, so every browser on the rig LAN gets it
without internet access, and the controller never needs internet either.

The browser does the fetching: given a Google Fonts link it downloads the CSS
and the font files itself (Google serves both with CORS), or it takes font
files dropped onto the Frontend settings tab. It then POSTs them to
`/api/fonts` (web.py), which hands them to `FontStore.save`.

Each font is a directory `<active_config dir>/fonts/<id>/` (by default
`/etc/saviour/controller/fonts/<id>/`) holding the font files and a
`font.json`:

    {
      "id": "inter",
      "name": "Inter",
      "faces": [
        {"file": "3f2a9c1e.woff2", "format": "woff2", "weight": "100 900",
         "style": "normal", "unicode_range": "U+0000-00FF, ...", "bytes": 48256}
      ],
      "source": {"kind": "google",
                 "url": "https://fonts.googleapis.com/css2?family=Inter"}
    }

File names are a hash of the content, chosen here, so nothing a client sends
ever becomes a path, and web.py can serve them with a long immutable cache.
Validation is strict because the metadata is injected into a `<style>` tag on
every client (`useControllerTheme.js`): the family name is limited to letters,
digits, spaces, `-` and `_`, weights to numbers, styles to a fixed set, and
unicode ranges to `U+` notation. Files are checked by their magic bytes, not
their names.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile

# Built-in font ids the frontend ships -- an added font may not shadow one.
# Mirror of BUILTIN_FONTS in src/basic/utils/fonts.js -- keep in sync.
RESERVED_IDS = frozenset({
    "system", "humanist", "geometric", "rounded", "industrial",
    "transitional", "old-style", "mono",
})

MAX_FACES = 64
MAX_FACE_BYTES = 2 * 1024 * 1024
MAX_FONT_BYTES = 12 * 1024 * 1024
MAX_UPLOAD_BYTES = MAX_FONT_BYTES + 256 * 1024   # files plus form overhead
_MAX_NAME_LEN = 60
_MAX_URL_LEN = 2000
_MAX_RANGE_LEN = 4000
_DEFAULT_DIR = "/etc/saviour/controller/fonts"
_META = "font.json"

_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")
_ID_RE = re.compile(r"^[a-z0-9-]+$")
_FILE_RE = re.compile(r"^[0-9a-f]{16}\.(?:woff2|woff|ttf|otf)$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_RANGE_ITEM = r"[Uu]\+[0-9A-Fa-f?]{1,6}(?:-[0-9A-Fa-f]{1,6})?"
_RANGE_RE = re.compile(rf"^{_RANGE_ITEM}(?:\s*,\s*{_RANGE_ITEM})*$")
_STYLES = ("normal", "italic", "oblique")
_WEIGHT_WORDS = {"normal": 400, "regular": 400, "bold": 700}
_GOOGLE_PREFIX = "https://fonts.googleapis.com/"

# (magic prefix, format, file extension, MIME type)
_FORMATS = (
    (b"wOF2", "woff2", "woff2", "font/woff2"),
    (b"wOFF", "woff", "woff", "font/woff"),
    (b"\x00\x01\x00\x00", "truetype", "ttf", "font/ttf"),
    (b"true", "truetype", "ttf", "font/ttf"),
    (b"OTTO", "opentype", "otf", "font/otf"),
)
MIME_BY_EXT = {ext: mime for _, _, ext, mime in _FORMATS}


class FontError(ValueError):
    """Raised for an invalid font upload; message is safe to show a user."""


def _slugify(name: str) -> str:
    slug = _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "font"


def sniff_format(data: bytes) -> tuple[str, str]:
    """(css format, file extension) from a font file's magic bytes."""
    for magic, fmt, ext, _ in _FORMATS:
        if data[:4] == magic:
            return fmt, ext
    raise FontError("Not a font file (expected WOFF2, WOFF, TTF or OTF)")


def _clean_weight(raw: object) -> str:
    """'400', or '100 900' for a variable font's range."""
    if isinstance(raw, bool):
        raise FontError(f"Invalid font weight {raw!r}")
    if isinstance(raw, int):
        parts = [raw]
    else:
        text = str(raw if raw is not None else "400").strip().lower()
        if text in _WEIGHT_WORDS:
            parts = [_WEIGHT_WORDS[text]]
        else:
            try:
                parts = [int(p) for p in text.split()]
            except ValueError:
                raise FontError(f"Invalid font weight {raw!r}") from None
    if not 1 <= len(parts) <= 2 or any(not 1 <= p <= 1000 for p in parts):
        raise FontError(f"Font weight must be 1-1000 or a range like '100 900' (got {raw!r})")
    if len(parts) == 2 and parts[0] > parts[1]:
        parts.reverse()
    return " ".join(str(p) for p in parts)


def _clean_style(raw: object) -> str:
    style = str(raw if raw is not None else "normal").strip().lower()
    if style not in _STYLES:
        raise FontError(f"Font style must be normal, italic or oblique (got {raw!r})")
    return style


def _clean_range(raw: object) -> str | None:
    if raw in (None, ""):
        return None
    text = " ".join(str(raw).split())
    if len(text) > _MAX_RANGE_LEN or not _RANGE_RE.match(text):
        raise FontError("Invalid unicode-range (expected U+ notation, e.g. U+0000-00FF)")
    return text


def _clean_name(raw: object) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise FontError("A font name is required")
    if len(name) > _MAX_NAME_LEN:
        raise FontError(f"Font name is too long (max {_MAX_NAME_LEN} characters)")
    if not _NAME_RE.match(name):
        raise FontError("Font name may only contain letters, digits, spaces, - and _")
    return name


def _clean_source(raw: object) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FontError("Font source must be an object")
    kind = raw.get("kind")
    if kind == "google":
        url = str(raw.get("url") or "")
        if len(url) > _MAX_URL_LEN or not url.startswith(_GOOGLE_PREFIX):
            raise FontError("Google Fonts source must be a fonts.googleapis.com link")
        return {"kind": "google", "url": url}
    if kind == "files":
        return {"kind": "files"}
    raise FontError("Font source kind must be 'google' or 'files'")


class FontStore:
    def __init__(self, fonts_dir: str | None = None,
                 logger: logging.Logger | None = None):
        self.dir = fonts_dir or _DEFAULT_DIR
        self.logger = logger or logging.getLogger(__name__)

    # -- reads --------------------------------------------------------------

    def list(self) -> list[dict]:
        """Metadata of every valid font, sorted by name. A font directory with
        a malformed `font.json` or missing files is logged and skipped rather
        than breaking the Settings page."""
        try:
            ids = sorted(os.listdir(self.dir))
        except FileNotFoundError:
            return []
        out = []
        for font_id in ids:
            if not _ID_RE.match(font_id):
                continue
            try:
                out.append(self._read(font_id))
            except (OSError, ValueError) as e:
                self.logger.warning(f"Skipping invalid font {font_id}: {e}")
        out.sort(key=lambda f: f["name"].lower())
        return out

    def file_path(self, font_id: str, filename: str) -> str | None:
        """Absolute path of one stored font file, or None if the id or name
        isn't one this store could have written, or the file is missing."""
        if not _ID_RE.match(font_id or "") or not _FILE_RE.match(filename or ""):
            return None
        path = os.path.join(self.dir, font_id, filename)
        return path if os.path.isfile(path) else None

    def _read(self, font_id: str) -> dict:
        with open(os.path.join(self.dir, font_id, _META)) as f:
            raw = json.load(f)
        if not isinstance(raw, dict) or not isinstance(raw.get("faces"), list):
            raise FontError("font.json must hold an object with a faces list")
        faces = []
        for face in raw["faces"]:
            fname = str(face.get("file", ""))
            if not _FILE_RE.match(fname) or not os.path.isfile(
                    os.path.join(self.dir, font_id, fname)):
                raise FontError(f"missing font file {fname!r}")
            faces.append({
                "file": fname,
                "format": str(face.get("format", "")),
                "weight": _clean_weight(face.get("weight")),
                "style": _clean_style(face.get("style")),
                "unicode_range": _clean_range(face.get("unicode_range")),
                "bytes": int(face.get("bytes", 0)),
            })
        if not faces:
            raise FontError("font has no faces")
        font = {"id": font_id, "name": _clean_name(raw.get("name")), "faces": faces,
                "bytes": sum(f["bytes"] for f in faces)}
        source = _clean_source(raw.get("source"))
        if source:
            font["source"] = source
        return font

    # -- writes -------------------------------------------------------------

    def save(self, meta: dict, files: dict[str, bytes]) -> dict:
        """Validate and store one font. Returns its metadata (as list() gives it).

        `meta` is {"name", "source"?, "faces": [{"field", "weight"?,
        "style"?, "unicode_range"?}]} where each face's `field` names its
        bytes in `files`. A new font gets an id derived from its name, made
        unique; an existing font is never overwritten -- delete it first.
        Raises FontError on any validation failure."""
        if not isinstance(meta, dict):
            raise FontError("Font details must be an object")
        name = _clean_name(meta.get("name"))
        source = _clean_source(meta.get("source"))
        raw_faces = meta.get("faces")
        if not isinstance(raw_faces, list) or not raw_faces:
            raise FontError("Add at least one font file")
        if len(raw_faces) > MAX_FACES:
            raise FontError(f"Too many font files (max {MAX_FACES})")

        faces, blobs, total = [], {}, 0
        seen = set()
        for i, face in enumerate(raw_faces):
            if not isinstance(face, dict):
                raise FontError("Each font file entry must be an object")
            data = files.get(str(face.get("field", "")))
            if not data:
                raise FontError(f"Font file {i + 1} is missing")
            if len(data) > MAX_FACE_BYTES:
                raise FontError(
                    f"Font file {i + 1} is larger than {MAX_FACE_BYTES // (1024 * 1024)} MB")
            total += len(data)
            if total > MAX_FONT_BYTES:
                raise FontError(
                    f"Font is larger than {MAX_FONT_BYTES // (1024 * 1024)} MB in total")
            fmt, ext = sniff_format(data)
            fname = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
            entry = {
                "file": fname, "format": fmt,
                "weight": _clean_weight(face.get("weight")),
                "style": _clean_style(face.get("style")),
                "unicode_range": _clean_range(face.get("unicode_range")),
                "bytes": len(data),
            }
            key = (entry["file"], entry["weight"], entry["style"], entry["unicode_range"])
            if key in seen:
                continue
            seen.add(key)
            faces.append(entry)
            blobs[fname] = data

        base = _slugify(name)
        font_id = base
        n = 2
        while font_id in RESERVED_IDS or os.path.exists(os.path.join(self.dir, font_id)):
            font_id = f"{base}-{n}"
            n += 1

        record = {"id": font_id, "name": name, "faces": faces}
        if source:
            record["source"] = source

        # Build the directory beside its final place, then rename it in, so a
        # crash mid-write never leaves a half font that list() would trip on.
        os.makedirs(self.dir, exist_ok=True)
        tmp = tempfile.mkdtemp(dir=self.dir, prefix=".tmp-")
        try:
            for fname, data in blobs.items():
                with open(os.path.join(tmp, fname), "wb") as f:
                    f.write(data)
            with open(os.path.join(tmp, _META), "w") as f:
                json.dump(record, f, indent=2)
            os.rename(tmp, os.path.join(self.dir, font_id))
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        self.logger.info(
            f"Saved font '{font_id}': {len(faces)} file(s), {total} bytes")
        return self._read(font_id)

    def delete(self, font_id: str) -> bool:
        """Remove an added font. Returns False if it wasn't there."""
        if font_id in RESERVED_IDS:
            raise FontError("Built-in fonts cannot be deleted")
        if not _ID_RE.match(font_id or ""):
            raise FontError("Invalid font id")
        path = os.path.join(self.dir, font_id)
        if not os.path.isdir(path):
            return False
        shutil.rmtree(path)
        self.logger.info(f"Deleted font '{font_id}'")
        return True
