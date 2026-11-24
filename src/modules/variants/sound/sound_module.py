#!/usr/bin/env python3
"""
SAVIOUR System - Sound Module Class

Plays sounds on a Pi 5 sound HAT (HiFiBerry DAC8x: 4 stereo outputs;
Raspberry Pi DAC Pro: 1), on command and from a session protocol's ``play``
steps (plans/session-protocol.md). Sounds are files in the sounds/ folder
(.wav, formatted to -3 dB peak).

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
import json
import os
import subprocess
import sys
import threading
import time

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, check, command
from modules.variants.sound import sound_hardware
from modules.variants.sound.sound_stream import SoundStream, load_sound
from shared import session_protocol

CSV_HEADER = ("Timestamp (ns), sound played, volume factor, duration (s), "
              "output, source\n")


class SoundModule(Module):
    # A re-armed module skips protocol sounds more than this far in the past.
    _PROTOCOL_RESUME_GRACE_S = 0.5
    _PROTOCOL_LATE_LOG_LIMIT = 20
    _RATE = 48_000

    def __init__(self, module_type="sound"):
        super().__init__(module_type)

        self.config.load_module_config("sound_config.json")

        # .wav files ship alongside this module; resolve relative to the file
        # so discovery/playback don't depend on the process CWD.
        self._sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")

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

        self.module_checks = [self._check_hifiberry]

        self.sound_commands = {
            "play_sound": self._play_sound,
            "list_sound_files": self._list_sound_files,
            "use_this_sound_file": self._use_this_sound_file,
            "validate_protocol": self.validate_protocol,
            "protocol_status": self.protocol_status,
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
        hw["files"] = sound_hardware.sound_files(self._sounds_dir)
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
        path = os.path.join(self._sounds_dir, name)
        if not os.path.isfile(path):
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

        stream = self._stream
        if stream is not None and stream.running:
            # The session's stream holds the card: mix it in there.
            samples = load_sound(path, self._RATE, float(volume), duration)
            stream.schedule(None, samples, output, {
                "file": name, "volume": volume, "duration_s": len(samples) / self._RATE,
                "output": output, "source": "manual"})
            return {"result": "success", "via": "session stream"}

        device = self._playback_device()
        if device is None:
            return {"result": "error", "message": self.hardware_fault or "no sound card"}
        if output != 1:
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
        samples = load_sound(path, self._RATE, volume, duration)
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
        try:
            return [
                f for f in os.listdir(self._sounds_dir)
                if os.path.isfile(os.path.join(self._sounds_dir, f))
            ]
        except FileNotFoundError:
            self.logger.warning(f"No sounds directory at {self._sounds_dir}")
            return []

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
        return session_protocol.expand(
            protocol, module_id=getattr(self, "module_id", None), module_type="sound",
            sound_files=hw.get("files"), outputs=hw.get("outputs") or None)

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

    def _open_protocol_stream(self, expansion):
        """Load the sounds and open the stream: (stream, cues with samples,
        t0, skipped) or raise RuntimeError with the reason."""
        hw = self.hardware
        if not hw.get("present"):
            raise RuntimeError(self.hardware_fault or "no sound card")
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
                cache[key] = load_sound(os.path.join(self._sounds_dir, c.file), self._RATE,
                                        default_volume if c.volume is None else c.volume,
                                        c.duration_s)
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
