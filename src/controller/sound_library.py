"""Sound files for sound modules, kept on the controller (the sound library).

Uploaded WAVs are analysed (src/shared/sound_analysis.py) and shown to the
operator before they're added; added files live in LIBRARY_DIR as
`<name>.wav` + `<name>.json` (sidecar: SHA-256, size, when, source,
analysis incl. render data). Every sound module is told the library's
manifest (`sync_sound_library`: names + hashes + where to fetch) whenever it
changes and on every get_config ack, and fetches what it lacks from
`/sound_library/<name>`. The files committed to the sound module's
`sounds/` folder are built-ins: listed (with analysis) but read-only, and a
library file can't take a built-in's name. Plan: plans/sound-library.md.

Socket.IO (registered from controller.py, every variant):
  get_sound_library                       anyone -> sound_library
  get_sound_file {name}                   anyone -> sound_file (with render data)
  upload_sound_start {filename, total_bytes, total_chunks}   admin
  upload_sound_chunk {index, data}        admin  -> sound_upload_progress,
                                                    then sound_upload_analysis
  preview_sound {params, seq}             admin  -> sound_preview (analysis, levels and
                                                    a 16-bit WAV of up to PREVIEW_MAX_S;
                                                    nothing stored)
  generate_sound {params, name?, add?}    admin  -> sound_upload_analysis (with
                                                    `generator`), or with add: added
                                                    straight away (sound_library)
  add_sound {name}                        admin  (the analysed upload or generated sound)
  discard_sound {name}                    admin
  delete_sound {name}                     admin  (refused while a saved protocol uses it)
Changes broadcast `sound_library`; failures reply `sound_library_error`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable

from src.shared import sound_analysis, sound_generator
from src.shared.sound_analysis import NAME_RE, SoundFileError

LIBRARY_DIR = "/var/lib/saviour/sound_library"
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "modules", "variants", "sound", "sounds")
MAX_BYTES = 100 * 1024 * 1024      # 16 s of 192 kHz 24-bit stereo is 18 MB
PENDING_TTL_S = 3600
PREVIEW_MAX_S = 10.0
# Browsers skip or swallow very short audio (a 100 µs click is 5 samples):
# the preview's audio (not its analysis) pads short sounds with silence and
# plays a click several times.
LISTEN_MIN_S = 0.4
LISTEN_PAD_S = 0.1
LISTEN_CLICKS = 4
LISTEN_CLICK_EVERY_S = 0.25
_RENDER_KEYS = ("envelope", "spectrogram")


class SoundLibraryError(ValueError):
    """A request the library refuses; the message is for the operator."""


def _summary(analysis: dict) -> dict:
    return {k: v for k, v in analysis.items() if k not in _RENDER_KEYS}


def _for_listening(x, rate: int, kind: str):
    """(samples for the browser to play, a note saying how they differ from
    the sound, or None)."""
    import numpy as np

    pad = np.zeros(round(LISTEN_PAD_S * rate), dtype=x.dtype)
    if kind == "click":
        gap = np.zeros(max(0, round(LISTEN_CLICK_EVERY_S * rate) - len(x)), dtype=x.dtype)
        out = np.concatenate([pad, *([x, gap] * LISTEN_CLICKS), pad])
        return out, (f"Played {LISTEN_CLICKS} times, {LISTEN_CLICK_EVERY_S * 1000:g} ms "
                     "apart, so it can be heard; the sound is one click.")
    if len(x) / rate < LISTEN_MIN_S:
        tail = np.zeros(max(0, round(LISTEN_MIN_S * rate) - len(x) - len(pad)), dtype=x.dtype)
        return np.concatenate([pad, x, tail]), "Padded with silence so the browser plays it."
    return x, None


def protocol_sound_files(protocol: object) -> set[str]:
    """Every `file` a protocol's play steps name (any nesting)."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("action") == "play" and isinstance(node.get("params"), dict):
                f = node["params"].get("file")
                if isinstance(f, str):
                    found.add(f)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(protocol)
    return found


class SoundLibrary:
    def __init__(self, directory: str = LIBRARY_DIR, builtin_dir: str = BUILTIN_DIR,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = directory
        self.builtin_dir = builtin_dir
        self._clock = clock
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._pending: dict[str, dict] = {}        # name -> {data, analysis, at, ...}
        self._builtin_cache: dict[str, tuple[float, dict]] = {}

    # ── names ────────────────────────────────────────────────────────────────
    def builtin_names(self) -> list[str]:
        try:
            return sorted(n for n in os.listdir(self.builtin_dir)
                          if os.path.isfile(os.path.join(self.builtin_dir, n))
                          and not n.startswith("."))
        except OSError:
            return []

    def check_name(self, name: object) -> str:
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise SoundLibraryError(
                "File names must end in .wav and use only letters, digits, . _ - "
                "(at most 80 characters before .wav)")
        if name.lower() in (b.lower() for b in self.builtin_names()):
            raise SoundLibraryError(f"{name} is a built-in sound: rename the file")
        return name

    def _wav(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _sidecar(self, name: str) -> str:
        return os.path.join(self.dir, name[:-4] + ".json")

    # ── reading ──────────────────────────────────────────────────────────────
    def _read_sidecar(self, name: str) -> dict | None:
        try:
            with open(self._sidecar(name)) as f:
                meta = json.load(f)
            return meta if isinstance(meta, dict) else None
        except (OSError, ValueError):
            return None

    def library_names(self) -> list[str]:
        try:
            return sorted(n for n in os.listdir(self.dir)
                          if NAME_RE.match(n) and os.path.isfile(self._wav(n)))
        except OSError:
            return []

    def _builtin_analysis(self, name: str) -> dict | None:
        path = os.path.join(self.builtin_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._builtin_cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            analysis = sound_analysis.analyse_file(path)
        except (OSError, SoundFileError) as e:
            analysis = {"error": str(e)}
        self._builtin_cache[name] = (mtime, analysis)
        return analysis

    def get(self, name: str, *, render: bool = True) -> dict | None:
        """One file's entry: {name, builtin, sha256, size_bytes, added_at,
        source, analysis}; None if it doesn't exist."""
        if name in self.builtin_names():
            a = self._builtin_analysis(name) or {}
            return {"name": name, "builtin": True, "kind": "recording",
                    "sha256": a.get("sha256"),
                    "size_bytes": a.get("size_bytes"), "added_at": None,
                    "source": "built-in",
                    "analysis": a if render else _summary(a)}
        if not NAME_RE.match(name or "") or not os.path.isfile(self._wav(name)):
            return None
        meta = self._read_sidecar(name)
        if meta is None:
            # A file without a sidecar (copied in by hand): analyse it now.
            meta = self._write_sidecar(name, source="manual")
        entry = {"name": name, "builtin": False,
                 # Built in the sound builder (a recipe) or an uploaded recording.
                 "kind": "recipe" if meta.get("generator") else "recording",
                 **{k: v for k, v in meta.items() if k != "analysis"}}
        a = meta.get("analysis") or {}
        entry["analysis"] = a if render else _summary(a)
        return entry

    def list(self) -> list[dict]:
        out = [self.get(n, render=False) for n in self.builtin_names()]
        out += [self.get(n, render=False) for n in self.library_names()]
        return [e for e in out if e]

    def manifest(self) -> list[dict]:
        """What a sound module needs to mirror the library."""
        out = []
        for n in self.library_names():
            meta = self._read_sidecar(n) or self._write_sidecar(n, source="manual")
            out.append({"name": n, "sha256": meta.get("sha256")})
        return out

    # ── changing ─────────────────────────────────────────────────────────────
    def _write_sidecar(self, name: str, *, source: str, original: str | None = None,
                       analysis: dict | None = None,
                       generator: dict | None = None) -> dict:
        if analysis is None:
            try:
                analysis = sound_analysis.analyse_file(self._wav(name))
            except (OSError, SoundFileError) as e:
                analysis = {"error": str(e)}
        meta = {"name": name, "sha256": analysis.get("sha256"),
                "size_bytes": analysis.get("size_bytes"), "added_at": self._clock(),
                "source": source, "original_filename": original,
                "generator": generator, "analysis": analysis}
        tmp = self._sidecar(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f)
        os.replace(tmp, self._sidecar(name))
        return meta

    def stage(self, filename: str, data: bytes, *, generator: dict | None = None) -> dict:
        """Analyse an upload (or a generated sound, with its *generator*
        parameters) and hold it until add() or discard(). Returns {name,
        replaces, analysis[, generator]}; raises SoundLibraryError if it
        isn't a readable WAV."""
        name = self.check_name(os.path.basename(filename or ""))
        if not data or len(data) > MAX_BYTES:
            raise SoundLibraryError(f"Sound files must be 1 byte to {MAX_BYTES // 2**20} MB")
        try:
            analysis = sound_analysis.analyse_bytes(data)
        except SoundFileError as e:
            raise SoundLibraryError(str(e)) from e
        now = self._clock()
        with self._lock:
            for k in [k for k, p in self._pending.items() if now - p["at"] > PENDING_TTL_S]:
                del self._pending[k]
            self._pending[name] = {"data": data, "analysis": analysis, "at": now,
                                   "original": None if generator else filename,
                                   "generator": generator}
        out = {"name": name, "replaces": os.path.isfile(self._wav(name)),
               "analysis": analysis}
        if generator:
            out["generator"] = generator
        return out

    def generate(self, params: dict, name: str | None = None) -> dict:
        """Render a sound from generator *params* (src/shared/sound_generator.py)
        and stage it like an upload. The name defaults to one describing it."""
        try:
            samples, full = sound_generator.generate(params)
        except sound_generator.GeneratorError as e:
            raise SoundLibraryError("Can't make that sound: " + "; ".join(e.errors)) from e
        name = (name or "").strip() or sound_generator.default_name(full)
        if not name.lower().endswith(".wav"):
            name += ".wav"
        return self.stage(name, sound_generator.wav_bytes(samples, full["sample_rate"]),
                          generator=full)

    def preview(self, params: dict) -> dict:
        """Render (up to PREVIEW_MAX_S) and analyse without storing: {params
        (normalised, incl. the seed drawn for blank), analysis, levels,
        preview_s, wav}. Raises SoundLibraryError listing the problems."""
        try:
            samples, full = sound_generator.generate(params, max_s=PREVIEW_MAX_S)
        except sound_generator.GeneratorError as e:
            raise SoundLibraryError("; ".join(e.errors)) from e
        rate = full["sample_rate"]
        analysis = sound_analysis.analyse(samples[:, None], rate)
        listen, note = _for_listening(samples, rate, full["type"])
        return {"params": full, "analysis": analysis,
                "levels": sound_generator.levels(samples, full),
                "preview_s": round(len(samples) / rate, 6),
                "name": sound_generator.default_name(full),
                "wav": sound_generator.wav_bytes(listen, rate, bits=16),
                "listen_note": note}

    def add(self, name: str) -> dict:
        with self._lock:
            pending = self._pending.pop(name, None)
        if pending is None:
            raise SoundLibraryError(f"No analysed upload called {name}: upload it again")
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._wav(name) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(pending["data"])
        os.replace(tmp, self._wav(name))
        self._write_sidecar(name, source="generator" if pending["generator"] else "upload",
                            original=pending["original"], analysis=pending["analysis"],
                            generator=pending["generator"])
        self._logger.info(f"Sound library: added {name} "
                          f"({len(pending['data']) // 1024} KiB)")
        return self.get(name, render=False)

    def discard(self, name: str) -> None:
        with self._lock:
            self._pending.pop(name, None)

    def delete(self, name: str, used_by: Iterable[str] = ()) -> None:
        if name in self.builtin_names():
            raise SoundLibraryError(f"{name} is built in and can't be deleted")
        if not NAME_RE.match(name or "") or not os.path.isfile(self._wav(name)):
            raise SoundLibraryError(f"No sound called {name}")
        users = sorted(used_by)
        if users:
            raise SoundLibraryError(
                f"{name} is used by protocol(s) {', '.join(users)}: change them first")
        os.remove(self._wav(name))
        with contextlib.suppress(OSError):
            os.remove(self._sidecar(name))
        self._logger.info(f"Sound library: deleted {name}")


def register(web, library: SoundLibrary, *, protocols, get_modules: Callable[[], dict],
             send_command: Callable[[str, str, dict], None],
             controller_url: Callable[[], str], logger: logging.Logger) -> Callable:
    """Socket.IO handlers + the file route. Returns push(module_id=None),
    which sends the manifest to one sound module or all of them."""
    from flask import request, send_file

    sio, app = web.socketio, web.app
    uploads: dict[str, dict] = {}            # sid -> chunked upload in progress
    uploads_lock = threading.Lock()

    @app.route("/sound_library/<name>")
    def serve_sound_library_file(name):
        """Unauthenticated like /hailo_models: modules fetch from here on the
        closed LAN (threat model in CLAUDE.md)."""
        if not NAME_RE.match(name) or not os.path.isfile(os.path.join(library.dir, name)):
            return "Not found", 404
        return send_file(os.path.join(library.dir, name), as_attachment=True,
                         download_name=name, mimetype="audio/wav")

    def push(module_id: str | None = None) -> int:
        params = {"controller_url": controller_url(), "files": library.manifest()}
        sent = 0
        for mid, info in (get_modules() or {}).items():
            if module_id not in (None, mid):
                continue
            if (info or {}).get("type") != "sound" or not (info or {}).get("online", True):
                continue
            send_command(mid, "sync_sound_library", params)
            sent += 1
        return sent

    def reply(event: str, data: dict) -> None:
        sio.emit(event, data, room=request.sid)

    def broadcast(**extra) -> None:
        sio.emit("sound_library", {"items": library.list(), **extra})

    def used_by(name: str) -> list[str]:
        return [p["name"] for p in protocols.list()
                if name in protocol_sound_files(protocols.get(p["name"]))]

    def guarded(fn, *, admin: bool):
        def wrapped(data=None):
            if admin and not web._require_auth(
                    "sound_library_error", {"error": "Sign in as admin to change sounds"}):
                return
            try:
                fn(data if isinstance(data, dict) else {})
            except SoundLibraryError as e:
                reply("sound_library_error", {"error": str(e)})
            except Exception as e:                          # noqa: BLE001 -- reported to the caller
                logger.exception("Sound library handler failed")
                reply("sound_library_error", {"error": f"Unexpected error: {e}"})
        return wrapped

    def get_file(d: dict) -> None:
        entry = library.get(str(d.get("name", "")))
        if entry is None:
            raise SoundLibraryError(f"No sound called {d.get('name')}")
        reply("sound_file", entry)

    def upload_start(d: dict) -> None:
        filename = os.path.basename(str(d.get("filename", "")))
        library.check_name(filename)
        total = int(d.get("total_bytes") or 0)
        if total <= 0 or total > MAX_BYTES:
            raise SoundLibraryError(f"Sound files must be 1 byte to {MAX_BYTES // 2**20} MB")
        with uploads_lock:
            uploads[request.sid] = {"filename": filename, "total_bytes": total,
                                    "total_chunks": int(d.get("total_chunks") or 0),
                                    "chunks": {}}

    def upload_chunk(d: dict) -> None:
        index, chunk = d.get("index"), d.get("data")
        if index is None or chunk is None:
            return
        with uploads_lock:
            up = uploads.get(request.sid)
            if not up:
                return
            up["chunks"][int(index)] = bytes(chunk)
            received, total = len(up["chunks"]), up["total_chunks"]
            if received >= total:
                uploads.pop(request.sid, None)
        reply("sound_upload_progress", {"received": received, "total": total})
        if received < total:
            return
        data = b"".join(up["chunks"].get(i, b"") for i in range(total))
        if len(data) != up["total_bytes"]:
            raise SoundLibraryError("Upload incomplete: size mismatch, try again")
        reply("sound_upload_analysis", library.stage(up["filename"], data))

    def preview(d: dict) -> None:
        seq = d.get("seq")
        try:
            reply("sound_preview", {"seq": seq, **library.preview(d.get("params") or {})})
        except SoundLibraryError as e:
            reply("sound_preview", {"seq": seq, "error": str(e)})

    def generate(d: dict) -> None:
        staged = library.generate(d.get("params") or {}, d.get("name"))
        if not d.get("add"):
            reply("sound_upload_analysis", staged)
            return
        entry = library.add(staged["name"])
        broadcast(added=entry["name"])
        push()

    def add(d: dict) -> None:
        entry = library.add(str(d.get("name", "")))
        broadcast(added=entry["name"])
        push()

    def discard(d: dict) -> None:
        library.discard(str(d.get("name", "")))

    def delete(d: dict) -> None:
        name = str(d.get("name", ""))
        library.delete(name, used_by(name))
        broadcast(deleted=name)
        push()

    events = {
        "get_sound_library": (lambda d: reply("sound_library", {"items": library.list()}), False),
        "get_sound_file": (get_file, False),
        "upload_sound_start": (upload_start, True),
        "upload_sound_chunk": (upload_chunk, True),
        "preview_sound": (preview, True),
        "generate_sound": (generate, True),
        "add_sound": (add, True),
        "discard_sound": (discard, True),
        "delete_sound": (delete, True),
    }
    for name, (fn, admin) in events.items():
        sio.on_event(name, guarded(fn, admin=admin))
    return push
