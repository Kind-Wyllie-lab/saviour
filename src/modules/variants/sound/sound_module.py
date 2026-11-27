#!/usr/bin/env python3
"""
SAVIOUR System - Sound Module Class

Plays sounds on a Pi 5 sound HAT (HiFiBerry DAC8x: 4 stereo outputs;
Raspberry Pi DAC Pro: 1), on command and from a session protocol's ``play``
steps (plans/session-protocol.md). Sounds are the built-in files in the sounds/
folder plus the controller's sound library, mirrored into LIBRARY_DIR by
``sync_sound_library`` (plans/sound-library.md).

The card and its outputs are found at start (sound_hardware.py) and
reported to the controller in the ``sound_hardware`` runtime section of the
config (with every sound file's length), so a protocol is checked against
them before a session is created. During a recording with sound steps, one
output stream stays open all session (sound_stream.py) and each sound is
mixed in at its deadline on the PTP-disciplined clock.

Author: Andrew SG
Created: 27/01/2026
"""
# Base Imports
import contextlib
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import numpy as np

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, check, command
from modules.variants.sound import sound_hardware
from modules.variants.sound.sound_stream import SoundStream, load_sound
from shared import session_protocol, sound_analysis, sound_generator

# Library files mirrored from the controller (outside the checkout, so a
# deploy's hard reset never touches them).
LIBRARY_DIR = "/var/lib/saviour/sound_library"
MAX_LIBRARY_FILE_BYTES = 100 * 1024 * 1024

CSV_HEADER = ("Timestamp (ns), sound played, volume factor, duration (s), "
              "output, source\n")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: str, digest: str | None) -> None:
    """Fetch *url* to *dest* via a .part file; refuses a file over the size
    limit or whose SHA-256 isn't *digest*."""
    tmp = dest + ".part"
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, "wb") as f:
            total = 0
            while chunk := resp.read(1 << 20):
                total += len(chunk)
                if total > MAX_LIBRARY_FILE_BYTES:
                    raise ValueError("file larger than 100 MB")
                h.update(chunk)
                f.write(chunk)
        if digest and h.hexdigest() != digest:
            raise ValueError("checksum mismatch")
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


class SoundModule(Module):
    # A re-armed module skips protocol sounds more than this far in the past.
    _PROTOCOL_RESUME_GRACE_S = 0.5
    # A test with no start_at from the controller starts this long after the
    # command; one whose start_at is already this far past starts now instead.
    _PROTOCOL_TEST_LEAD_S = 0.5
    # How long after the last sound ends the test stream stays open (the
    # output queue still holds the tail).
    _PROTOCOL_TEST_TAIL_S = 0.5
    _PROTOCOL_LATE_LOG_LIMIT = 20
    _RATE = 48_000

    def __init__(self, module_type="sound"):
        super().__init__(module_type)

        self.config.load_module_config("sound_config.json")

        # .wav files ship alongside this module; resolve relative to the file
        # so discovery/playback don't depend on the process CWD.
        self._sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")
        self._library_dir = LIBRARY_DIR
        self._peak_cache: dict[str, tuple[tuple, float | None]] = {}
        # Built sounds' recipes (from the controller's manifest): rendered at
        # whatever length a play asks for instead of cutting the 1 s copy.
        self._recipes: dict[str, dict] = self._load_recipes()

        # None once a sound card is detected; otherwise a human-readable
        # reason. Surfaced every heartbeat via get_health() (the System page's
        # "NO HARDWARE" badge) and as the _check_hifiberry readiness reason.
        self.hardware_fault: str | None = None
        # sound_hardware.detect() plus "files": what the controller checks
        # protocols against.
        self.hardware: dict = {}

        # Session protocol runner
        self._stream: SoundStream | None = None
        self._stream_lock = threading.Lock()
        self._protocol_status: dict = {"running": False}
        self._protocol_error: str | None = None
        self._test_stop: threading.Event | None = None
        self._test_thread: threading.Thread | None = None

        self.module_checks = [self._check_hifiberry]

        self.sound_commands = {
            "play_sound": self._play_sound,
            "list_sound_files": self._list_sound_files,
            "use_this_sound_file": self._use_this_sound_file,
            "validate_protocol": self.validate_protocol,
            "protocol_status": self.protocol_status,
            "test_protocol": self.test_protocol,
            "stop_protocol_test": self.stop_protocol_test,
            "sync_sound_library": self.sync_sound_library,
        }

        self.command.set_commands(self.sound_commands)

        # Sound files
        self.available_sounds = self._get_available_sounds()
        self.sound_to_play = (
            self.available_sounds[0] if self.available_sounds else None
        )

        # Recording
        self.current_sound_event_file = None
        self._sound_file_handle = None
        self._sound_file_lock = threading.Lock()

        # Find the card (and ask ALSA its channel count) before the first
        # heartbeat and get_config.
        self._refresh_hardware(query_channels=True)

    """Hardware"""
    def _refresh_hardware(self, query_channels: bool = False) -> dict:
        """Re-read the sound card and files and report them in the
        ``sound_hardware`` runtime section. ALSA is only asked for the
        channel count when *query_channels* and no stream holds the card;
        otherwise the last count for the same card is kept."""
        previous = self.hardware or {}
        can_query = query_channels and self._stream is None
        hw = sound_hardware.detect(run=subprocess.run if can_query else None)
        if (not can_query and hw.get("present")
                and previous.get("card_id") == hw.get("card_id")
                and previous.get("channels_from") == "alsa"):
            for k in ("channels", "outputs", "channels_from", "detail"):
                hw[k] = previous[k]
        hw["files"] = self._all_sound_files()
        hw["library_files"] = sorted(
            n for n in sound_hardware.sound_files(self._library_dir)
            if n not in sound_hardware.sound_files(self._sounds_dir))
        hw["file_peaks_dbfs"] = {n: self._file_peak_dbfs(n) for n in hw["files"]}
        # Files a play can stretch to any length (built sounds, not clicks).
        hw["recipes"] = sorted(n for n in hw["library_files"] if self._recipe(n))
        hw["recipe_max_s"] = sound_generator.MAX_DURATION_S
        if hw != previous:
            self.hardware = hw
            try:
                self.config.set("sound_hardware", dict(hw))
            except Exception as e:  # noqa: BLE001 -- reporting must not stop the module
                self.logger.warning(f"Could not store sound_hardware: {e}")
        self.hardware_fault = None if hw.get("present") else hw.get("detail")
        return hw

    def _playback_device(self) -> str | None:
        card = self.hardware.get("card_id")
        # plughw converts sample format and rate for the card; latency is
        # unchanged (conversion only, no extra buffering).
        return f"plughw:CARD={card},DEV=0" if card else None

    """Commands"""
    @command()
    def _play_sound(self, filename: str | None = None, output: int = 1,
                    volume: float | None = None, duration_s: float | None = None):
        """Play a sound now: *filename* (default: the selected one) on
        *output* (1 = channels 1-2), *volume* gain factor (default
        sound.volume), cut to *duration_s* (default sound.duration)."""
        name = filename or self.sound_to_play
        if not name:
            return {"result": "error", "message": "no sound file available"}
        if "/" in name or name.startswith("."):
            return {"result": "error", "message": f"not a sound file name: {name}"}
        path = self._sound_path(name)
        if path is None:
            return {"result": "error", "message": f"no sound file {name}"}
        outputs = int(self.hardware.get("outputs") or 0)
        try:
            output = int(output)
        except (TypeError, ValueError):
            return {"result": "error", "message": f"output must be a number (got {output!r})"}
        if outputs and not 1 <= output <= outputs:
            return {"result": "error",
                    "message": f"output {output} doesn't exist ({outputs} on this card)"}
        volume = self.config.get("sound.volume") if volume is None else volume
        duration = self.config.get("sound.duration") if duration_s is None else duration_s
        refused = self._over_limit(name, volume, duration)
        if refused:
            return {"result": "error", "message": refused}

        stream = self._stream
        if stream is not None and stream.running:
            # The session's stream holds the card: mix it in there.
            try:
                samples = self._samples(name, float(volume), duration)
            except RuntimeError as e:
                return {"result": "error", "message": str(e)}
            stream.schedule(None, samples, output, {
                "file": name, "volume": volume, "duration_s": len(samples) / self._RATE,
                "output": output, "source": "manual"})
            return {"result": "success", "via": "session stream"}

        device = self._playback_device()
        if device is None:
            return {"result": "error", "message": self.hardware_fault or "no sound card"}
        if output != 1 or (duration and self._recipe(name)):
            return self._play_once_on_stream(path, name, output, float(volume), duration)

        timestamp = time.time_ns()
        ffmpeg_proc = subprocess.Popen([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-t", str(duration), "-i", path,
            "-filter:a", f"volume={volume}", "-f", "wav", "-"],
            stdout=subprocess.PIPE)
        aplay_proc = subprocess.Popen(["aplay", "-D", device], stdin=ffmpeg_proc.stdout)
        ffmpeg_proc.stdout.close()
        aplay_proc.communicate()

        if self.facade.get_recording_status() is True:
            self._write_sound_event(timestamp, name, volume, duration, output, "manual")
        ok = aplay_proc.returncode == 0
        return {"result": "success" if ok else "error",
                **({} if ok else {"message": f"aplay exited {aplay_proc.returncode}"})}

    def _play_once_on_stream(self, path, name, output, volume, duration):
        """Outputs past the first need the multichannel stream: open one for
        this sound only."""
        try:
            samples = self._samples(name, volume, duration)
        except RuntimeError as e:
            return {"result": "error", "message": str(e)}
        played = {}
        stream = SoundStream(
            self._playback_device(), int(self.hardware.get("channels") or 2),
            rate=self._RATE, extra_latency_s=self._extra_latency_s(),
            on_fire=lambda meta, at, late: played.update(at=at), logger=self.logger)
        stream.start()
        try:
            stream.schedule(None, samples, output, {"file": name})
            deadline = time.time() + len(samples) / self._RATE + 4 * stream.latency_s + 1.0
            while time.time() < deadline and stream.error is None:
                if "at" in played and time.time() > played["at"] + len(samples) / self._RATE:
                    break
                time.sleep(0.02)
        finally:
            stream.stop()
        if stream.error:
            return {"result": "error", "message": stream.error}
        if "at" in played and self.facade.get_recording_status() is True:
            self._write_sound_event(int(played["at"] * 1e9), name, volume,
                                    len(samples) / self._RATE, output, "manual")
        return {"result": "success"}

    @command()
    def _list_sound_files(self):
        files = self._get_available_sounds()
        return {"sound_files": files, "selected_file": self.sound_to_play,
                "outputs": self.hardware.get("outputs"),
                "lengths": self.hardware.get("files")}

    @command()
    def _use_this_sound_file(self, filename: str):
        self.logger.info(f"Switching to use {filename}")
        self.sound_to_play = filename

    def _get_available_sounds(self) -> list:
        return sorted(self._all_sound_files())

    """Sound files: built-ins + the controller's library"""
    def _all_sound_files(self) -> dict:
        """Name -> length (s) of every playable file; a built-in wins over a
        library file of the same name (the controller refuses those)."""
        return {**sound_hardware.sound_files(self._library_dir),
                **sound_hardware.sound_files(self._sounds_dir)}

    # -- built sounds: render at the length asked for ------------------------

    _RECIPES_FILE = ".recipes.json"   # hidden: not listed as a sound

    def _load_recipes(self) -> dict:
        try:
            with open(os.path.join(self._library_dir, self._RECIPES_FILE)) as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_recipes(self) -> None:
        try:
            path = os.path.join(self._library_dir, self._RECIPES_FILE)
            with open(path + ".tmp", "w") as f:
                json.dump(self._recipes, f)
            os.replace(path + ".tmp", path)
        except OSError as e:
            self.logger.warning(f"Could not save sound recipes: {e}")

    def _recipe(self, name: str) -> dict | None:
        """*name*'s recipe when it is a library built sound that renders at
        any length (a click has one length), else None. A built-in file of
        the same name wins, as for playing."""
        recipe = getattr(self, "_recipes", {}).get(name)
        if not recipe or recipe.get("type") == "click":
            return None
        if os.path.isfile(os.path.join(self._sounds_dir, name)):
            return None
        return recipe

    def _samples(self, name: str, volume: float, duration_s: float | None):
        """*name* as float32 stereo frames at the output rate, gain applied:
        a built sound rendered at *duration_s* (its ramps at the new ends),
        anything else read from its file and cut to *duration_s*. Raises
        RuntimeError with the reason."""
        recipe = self._recipe(name) if duration_s else None
        if recipe is not None:
            try:
                x, _ = sound_generator.generate({**recipe, "duration_s": float(duration_s)})
            except sound_generator.GeneratorError as e:
                raise RuntimeError(f"{name}: {e}") from e
            data = np.repeat(x[:, None], 2, axis=1)
            if volume != 1.0:
                data = data * np.float32(volume)
            return np.ascontiguousarray(data, dtype=np.float32)
        path = self._sound_path(name)
        if path is None:
            raise RuntimeError(f"no sound file {name}")
        return load_sound(path, self._RATE, volume, duration_s)

    def _sound_path(self, name: str) -> str | None:
        if not name or "/" in name or name.startswith("."):
            return None
        for directory in (self._sounds_dir, self._library_dir):
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                return path
        return None

    def _file_peak_dbfs(self, name: str) -> float | None:
        """A file's peak level (dBFS), cached on its size and mtime. None
        when it can't be read (not a WAV the analysis reads)."""
        path = self._sound_path(name)
        if path is None:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (path, st.st_size, st.st_mtime)
        cached = self._peak_cache.get(name)
        if cached and cached[0] == key:
            return cached[1]
        peak = None
        try:
            with open(path, "rb") as f:
                x, _, _ = sound_analysis.read_wav(f.read())
            p = float(abs(x).max()) if len(x) else 0.0
            peak = round(20 * math.log10(p), 2) if p > 0 else None
        except (OSError, sound_analysis.SoundFileError, ValueError):
            peak = None
        self._peak_cache[name] = (key, peak)
        return peak

    def _over_limit(self, name: str, volume, duration_s) -> str | None:
        """Why playing *name* at gain *volume*, cut to *duration_s* (None =
        whole file), would break the module's limits (sound.max_peak_dbfs,
        sound.max_length_s; welfare caps, plans/sound-library.md), or None."""
        try:
            cap_db = float(self.config.get("sound.max_peak_dbfs", 0) or 0)
            cap_s = float(self.config.get("sound.max_length_s", 0) or 0)
            gain = float(volume if volume is not None else 1)
        except (TypeError, ValueError):
            return None
        if cap_db < 0:
            peak = self._file_peak_dbfs(name)
            if peak is None:
                return f"{name}: level unknown, so it can't be checked against the limit"
            level = peak + 20 * math.log10(gain) if gain > 0 else None
            if level is not None and level > cap_db + 0.01:
                return (f"{name} at volume {gain:g} peaks at {level:.1f} dBFS, over this "
                        f"module's limit of {cap_db:g} dBFS")
        if cap_s > 0:
            length = (self.hardware.get("files") or {}).get(name)
            if duration_s is not None and self._recipe(name):
                length = None   # rendered at duration_s, not cut from the file
            if duration_s is not None:
                try:
                    length = min(float(duration_s), length) if length else float(duration_s)
                except (TypeError, ValueError):
                    pass
            if length is not None and length > cap_s + 1e-6:
                return (f"{name} plays for {length:g} s, over this module's limit of "
                        f"{cap_s:g} s")
        return None

    @command()
    def sync_sound_library(self, controller_url: str = "", files: list | None = None) -> dict:
        """Mirror the controller's sound library: fetch files that are
        missing or differ (checked by SHA-256), remove ones it no longer has.
        Built-ins are never touched."""
        wanted = {}
        recipes = {}
        for f in files or []:
            name, digest = (f or {}).get("name"), (f or {}).get("sha256")
            if isinstance(name, str) and sound_analysis.NAME_RE.match(name):
                wanted[name] = digest
                if isinstance((f or {}).get("generator"), dict):
                    recipes[name] = f["generator"]
        os.makedirs(self._library_dir, exist_ok=True)
        recipes_changed = recipes != getattr(self, "_recipes", {})
        if recipes_changed:
            self._recipes = recipes
            self._save_recipes()
        fetched, removed, failed = [], [], {}
        for name, digest in wanted.items():
            path = os.path.join(self._library_dir, name)
            if digest and os.path.isfile(path) and _sha256(path) == digest:
                continue
            if not controller_url:
                failed[name] = "no controller_url"
                continue
            try:
                _download(f"{controller_url.rstrip('/')}/sound_library/"
                          f"{urllib.parse.quote(name)}", path, digest)
                fetched.append(name)
            except Exception as e:  # noqa: BLE001 -- reported per file
                failed[name] = str(e)
                self.logger.error(f"Sound library: fetching {name} failed: {e}")
        for name in os.listdir(self._library_dir):
            if name not in wanted and sound_analysis.NAME_RE.match(name):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self._library_dir, name))
                    removed.append(name)
        if fetched or removed or recipes_changed:
            if fetched or removed:
                self.logger.info(f"Sound library: fetched {fetched or 'none'}, "
                                 f"removed {removed or 'none'}")
            self.available_sounds = self._get_available_sounds()
            before = dict(self.hardware or {})
            self._refresh_hardware()
            if self.hardware != before:
                self.facade.send_status({
                    "type": "module_config_updated", "reason": "sound_library",
                    "config": self.config.get_all()})
        return {"result": "error" if failed else "success", "fetched": fetched,
                "removed": removed, "failed": failed}

    """Session protocol (plans/session-protocol.md)"""
    def _session_protocol(self):
        return getattr(getattr(self, "recording", None), "session_protocol", None)

    def _expand_protocol(self, protocol=None):
        """This module's part of *protocol* (default: the session's), checked
        against its files and outputs. None when there's no protocol."""
        protocol = protocol or self._session_protocol()
        if not protocol:
            return None
        hw = self._refresh_hardware()
        exp = session_protocol.expand(
            protocol, module_id=getattr(self, "module_id", None), module_type="sound",
            sound_files=hw.get("files"), outputs=hw.get("outputs") or None,
            stretchable=set(hw.get("recipes") or ()))
        default_volume = self.config.get("sound.volume")
        seen = set()
        for c in exp.cues:
            vol = default_volume if c.volume is None else c.volume
            refused = self._over_limit(c.file, vol, c.duration_s)
            if refused and refused not in seen:
                seen.add(refused)
                exp.errors.append(refused)
        return exp

    def validate_protocol(self, protocol: dict | None = None) -> dict:
        exp = self._expand_protocol(protocol)
        if exp is None:
            return {"result": "error", "errors": ["no protocol"], "warnings": []}
        return {"result": "success" if exp.ok else "error", "errors": exp.errors,
                "warnings": exp.warnings, "cues": len(exp.cues),
                "duration_s": round(exp.duration_s, 6), "outputs": exp.outputs}

    def protocol_status(self) -> dict:
        status = dict(self._protocol_status)
        stream = self._stream
        if stream is not None:
            status["pending"] = stream.pending()
            status["latency_ms"] = round(stream.latency_s * 1000, 3)
            status["stream_error"] = stream.error
        return status

    def _extra_latency_s(self) -> float:
        try:
            return float(self.config.get("sound.output_latency_offset_ms", 0) or 0) / 1000
        except (TypeError, ValueError):
            return 0.0

    def _protocol_t0(self) -> float:
        """The session's t = 0: the controller sends it with every
        start_recording of a protocol session."""
        rec = getattr(self, "recording", None)
        for value in (getattr(rec, "session_protocol_start_at", None),
                      getattr(rec, "recording_intended_start_at", None),
                      getattr(rec, "recording_start_time", None)):
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        return time.time()

    def _open_protocol_stream(self, expansion, t0: float | None = None):
        """Load the sounds and open the stream: (stream, cues with samples,
        t0, skipped) or raise RuntimeError with the reason. *t0*: the
        protocol's start (default: the session's)."""
        hw = self.hardware
        if not hw.get("present"):
            raise RuntimeError(self.hardware_fault or "no sound card")
        if t0 is None:
            t0 = self._protocol_t0()
        cutoff = (time.time() - t0) - self._PROTOCOL_RESUME_GRACE_S
        upcoming = [c for c in expansion.cues if c.t_s >= cutoff]
        skipped = len(expansion.cues) - len(upcoming)
        default_volume = float(self.config.get("sound.volume") or 1)
        cache: dict = {}
        prepared = []
        for c in upcoming:
            key = (c.file, c.volume, c.duration_s)
            if key not in cache:
                cache[key] = self._samples(
                    c.file, default_volume if c.volume is None else c.volume, c.duration_s)
            prepared.append((c, cache[key]))
        stream = SoundStream(self._playback_device(), int(hw.get("channels") or 2),
                             rate=self._RATE, extra_latency_s=self._extra_latency_s(),
                             on_fire=self._on_sound_fired, on_error=self._on_stream_error,
                             logger=self.logger)
        stream.start()
        return stream, prepared, t0, skipped

    def _start_protocol(self, expansion, csv_filename: str) -> None:
        stream, prepared, t0, skipped = self._open_protocol_stream(expansion)
        self._protocol_status = {
            "running": True, "session_start_at": t0, "total": len(expansion.cues),
            "fired": 0, "late": 0, "max_late_ms": 0.0, "missed": skipped,
            "resumed": skipped > 0,
        }
        self._protocol_error = None
        with self._stream_lock:
            self._stream = stream
        default_volume = self.config.get("sound.volume")
        for c, samples in prepared:
            stream.schedule(t0 + c.t_s, samples, c.output, {
                "file": c.file, "volume": default_volume if c.volume is None else c.volume,
                "duration_s": round(len(samples) / self._RATE, 6), "output": c.output,
                "source": f"protocol step={c.step} play", "planned_at": t0 + c.t_s})

        record = {
            "format": "saviour.sound_protocol/1",
            "module_id": getattr(self, "module_id", None),
            "session": self.facade.get_current_session_name(),
            "session_start_at": t0,
            "runner_started_at": time.time(),
            "missed_cues": skipped,
            "board": self.hardware.get("board"),
            "device": self._playback_device(),
            "channels": self.hardware.get("channels"),
            "output_latency_ms": round(stream.latency_s * 1000, 3),
            "output_latency_offset_ms": self._extra_latency_s() * 1000,
            "late_threshold_ms": session_protocol.LATE_THRESHOLD_S * 1000,
            "warnings": expansion.warnings,
            "protocol": self._session_protocol(),
            "cues": [c.to_dict() for c in expansion.cues],
        }
        stem = csv_filename[:-4] if csv_filename.endswith(".csv") else csv_filename
        record_path = f"{stem}_protocol.json"
        try:
            with open(record_path, "w") as f:
                json.dump(record, f, indent=1)
            self.facade.add_session_file(record_path)
            self.facade.stage_file_for_export(record_path)
        except OSError as e:
            self.logger.error(f"Could not write {record_path}: {e}")
        for w in expansion.warnings:
            self.logger.warning(f"Sound protocol: {w}")
        self.logger.info(
            f"Sound protocol starting: {len(prepared)} sound(s) over "
            f"{expansion.duration_s:.3f}s on output(s) {expansion.outputs}, "
            f"output latency {stream.latency_s * 1000:.1f} ms"
            + (f", {skipped} past sound(s) skipped (re-arm)" if skipped else ""))

    # -- Test routine: the protocol's sounds now, nothing recorded -----------

    def _send_protocol_test_status(self, state: str, **extra) -> None:
        msg = {"type": "sound_protocol_test", "state": state, **extra}
        try:
            self.facade.send_status(msg)
        except Exception as e:                              # noqa: BLE001 -- status is best effort
            self.logger.debug(f"Could not send protocol test status: {e}")

    def _refuse_protocol_test(self, message: str) -> dict:
        self._send_protocol_test_status("refused", message=message)
        return {"result": "error", "message": message}

    def _protocol_test_running(self) -> bool:
        return self._test_thread is not None and self._test_thread.is_alive()

    def test_protocol(self, protocol: dict | None = None,
                      start_at: float | None = None) -> dict:
        """Play *protocol*'s sounds (default: the session's) now, timed as
        in a recording, without recording anything. *start_at*: the
        protocol's t = 0 as wall-clock seconds (the controller gives the TTL
        and sound modules the same one, so a rig test runs in step on their
        PTP clocks); default shortly after the command. Refused while
        recording. Progress goes out as `sound_protocol_test` status
        messages (running / finished / stopped / error / refused)."""
        if self.facade.get_recording_status() is True or (
                self._stream is not None and not self._protocol_test_running()):
            return self._refuse_protocol_test(
                "Can't test the protocol while recording: it is already running "
                "as part of the session")
        if self._protocol_test_running():
            return self._refuse_protocol_test(
                "A protocol test is already running: stop it first")
        expansion = self._expand_protocol(protocol)
        if expansion is None:
            return self._refuse_protocol_test("No protocol to test")
        if not expansion.ok:
            return self._refuse_protocol_test(
                "Protocol invalid: " + "; ".join(expansion.errors[:5]))
        if not expansion.cues:
            return self._refuse_protocol_test("The protocol has no sound steps")
        now = time.time()
        try:
            t0 = float(start_at) if start_at is not None else None
        except (TypeError, ValueError):
            t0 = None
        if t0 is None or not math.isfinite(t0) or t0 < now - 1.0:
            t0 = now + self._PROTOCOL_TEST_LEAD_S
        try:
            stream, prepared, t0, skipped = self._open_protocol_stream(expansion, t0)
        except Exception as e:                              # noqa: BLE001 -- refuse with the reason
            return self._refuse_protocol_test(f"Can't open the sound output: {e}")
        self._protocol_status = {
            "running": True, "test": True, "session_start_at": t0,
            "total": len(expansion.cues), "fired": 0, "late": 0, "max_late_ms": 0.0,
            "missed": skipped, "resumed": False,
        }
        self._protocol_error = None
        with self._stream_lock:
            self._stream = stream
        default_volume = self.config.get("sound.volume")
        for c, samples in prepared:
            stream.schedule(t0 + c.t_s, samples, c.output, {
                "file": c.file, "volume": default_volume if c.volume is None else c.volume,
                "duration_s": round(len(samples) / self._RATE, 6), "output": c.output,
                "source": f"protocol test step={c.step} play", "planned_at": t0 + c.t_s})
        end_at = t0 + expansion.duration_s + stream.latency_s + self._PROTOCOL_TEST_TAIL_S
        stop = threading.Event()
        self._test_stop = stop
        self._test_thread = threading.Thread(
            target=self._protocol_test_worker, args=(stream, end_at, stop),
            daemon=True, name="sound-protocol-test")
        self._test_thread.start()
        info = {"started_at": t0, "duration_s": round(expansion.duration_s, 6),
                "cues": len(expansion.cues), "outputs": expansion.outputs,
                "warnings": expansion.warnings}
        self._send_protocol_test_status("running", **info)
        self.logger.info(f"Sound protocol test: {len(expansion.cues)} sound(s) over "
                         f"{expansion.duration_s:.3f}s on output(s) {expansion.outputs}")
        return {"result": "success", **info}

    def _protocol_test_worker(self, stream, end_at: float, stop: threading.Event) -> None:
        # Ends at end_at, on Stop, or when something else (a recording)
        # takes the stream.
        while not stop.wait(0.2):
            if self._stream is not stream or time.time() >= end_at:
                break
        finished = not stop.is_set() and self._stream is stream
        replaced = self._stream is not stream
        if not replaced:
            self._stop_protocol()
        status = self._protocol_status if not replaced else {}
        error = self._protocol_error
        state = "error" if error else "finished" if finished else "stopped"
        self._send_protocol_test_status(
            state, fired=status.get("fired", 0), cues=status.get("total", 0),
            late=status.get("late", 0), max_late_ms=status.get("max_late_ms", 0.0),
            message=error or ("A recording started" if replaced else None))

    def stop_protocol_test(self) -> dict:
        """Stop a running protocol test: its sounds stop."""
        if not self._protocol_test_running():
            self._send_protocol_test_status("stopped", message="No protocol test was running")
            return {"result": "success", "message": "No protocol test was running"}
        if self._test_stop is not None:
            self._test_stop.set()
        return {"result": "success"}

    def _stop_protocol(self) -> None:
        with self._stream_lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
        self._protocol_status["running"] = False

    def _on_sound_fired(self, meta: dict, play_at: float, late_s: float) -> None:
        planned = meta.get("planned_at")
        source = meta.get("source", "protocol")
        if planned is not None:
            status = self._protocol_status
            status["fired"] = status.get("fired", 0) + 1
            late_ms = late_s * 1000
            status["max_late_ms"] = max(status.get("max_late_ms", 0.0), round(late_ms, 3))
            source = f"{source} late_ms={late_ms:.3f}"
            if late_s > session_protocol.LATE_THRESHOLD_S:
                status["late"] = status.get("late", 0) + 1
                if status["late"] <= self._PROTOCOL_LATE_LOG_LIMIT:
                    self.logger.warning(f"Sound protocol: {meta.get('file')} on output "
                                        f"{meta.get('output')} started {late_ms:.1f} ms late")
        self._write_sound_event(int(play_at * 1e9), meta.get("file"), meta.get("volume"),
                                meta.get("duration_s"), meta.get("output"), source)

    def _on_stream_error(self, message: str) -> None:
        self._protocol_error = message

    def _check_recording_alive(self) -> tuple[bool, str | None]:
        if self._protocol_error:
            return False, f"Sound output failed: {self._protocol_error}"
        return True, None

    """Config"""
    def configure_module_special(self, updated_keys: list[str] | None):
        pass

    """Recording"""
    def _get_sound_segment_filename(self) -> str:
        """Return a filename for the current sound events segment"""
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        return f"{self.facade.get_filename_prefix()}_sound_events_({self.facade.get_segment_id()}_{strtime}).csv"

    def _write_sound_event(self, timestamp_ns: int, name, volume, duration,
                           output, source: str) -> None:
        with self._sound_file_lock:
            if self._sound_file_handle:
                self._sound_file_handle.write(
                    f"{timestamp_ns},{name},{volume},{duration},{output},{source}\n")

    def _create_sound_event_file(self) -> bool:
        filename = self._get_sound_segment_filename()
        self.logger.info(f"Creating sound events file {filename}")
        self.current_sound_event_file = filename
        try:
            handle = open(filename, "w", buffering=1)  # line-buffered
            handle.write("# Sound Events Recording\n")
            handle.write(f"# Session ID / Segment: {self.facade.get_recording_session_id()}_{self.facade.get_segment_id()}\n")
            handle.write(f"# Segment Start: {self.facade.get_segment_start_time()}\n")
            handle.write("# Timestamp: when the sound's first sample is heard (protocol "
                         "sounds: estimated from the output queue; late_ms against the plan)\n")
            handle.write("#\n")
            handle.write(CSV_HEADER)
            with self._sound_file_lock:
                self._sound_file_handle = handle
            return True
        except Exception as e:
            self.logger.error(f"Failed to open sound events file: {e}")
            self._sound_file_handle = None
            return False

    def _close_sound_event_file(self) -> bool:
        """Close sound events file"""
        try:
            with self._sound_file_lock:
                if self._sound_file_handle is not None:
                    self._sound_file_handle.flush()
                    self._sound_file_handle.close()
                    self._sound_file_handle = None
            self.logger.info("Closed sound events file")
        except Exception as e:
            self.logger.warning(f"Error closing sound events file: {e}")
        return True

    def _start_new_recording(self):
        # Check this module's part of the session protocol first: an invalid
        # one refuses the recording with the reasons, rather than running
        # part of it while the session looks healthy.
        if self._protocol_test_running() and self._test_stop is not None:
            self._test_stop.set()
        self._stop_protocol()
        expansion = self._expand_protocol()
        if expansion is not None and not expansion.ok:
            return self._refuse("Sound protocol invalid: " + "; ".join(expansion.errors[:5]))
        self._create_sound_event_file()
        self.facade.add_session_file(self.current_sound_event_file)
        if expansion is not None and expansion.cues:
            try:
                self._start_protocol(expansion, self.current_sound_event_file)
            except Exception as e:  # noqa: BLE001 -- refuse with the reason
                self._close_sound_event_file()
                return self._refuse(f"Sound protocol can't start: {e}")
        return True

    def _refuse(self, message: str) -> bool:
        self.logger.error(message)
        self.facade.send_status({"type": "recording_start_failed",
                                 "error": message, "reason": "sound_protocol"})
        return False

    def _start_next_recording_segment(self):
        self._close_sound_event_file()
        self.facade.stage_file_for_export(self.current_sound_event_file)
        self._create_sound_event_file()
        return True

    def _stop_recording(self):
        self._stop_protocol()
        self._close_sound_event_file()
        self.facade.stage_file_for_export(self.current_sound_event_file)
        return True

    """Self Check"""
    @check()
    def _check_hifiberry(self):
        """A sound card is present (name kept for the readiness check)."""
        hw = self._refresh_hardware()
        if hw.get("present"):
            return True, hw.get("detail") or "Sound card present"
        self.logger.warning(self.hardware_fault)
        return False, self.hardware_fault


def main():
    sound = SoundModule()
    sound.start()

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShuttind down...")
        sound.stop()

if __name__ == "__main__":
    main()
