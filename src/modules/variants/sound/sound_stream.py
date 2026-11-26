"""
A sound card output held open for a whole session, so a protocol's sounds
start on time (plans/session-protocol.md, "Adding sound").

Starting ``aplay`` per sound costs tens of ms, and a varying amount, between
"play now" and sound. Instead one ``aplay`` runs all session on raw PCM from
a pipe, and a writer thread keeps it fed: silence, with sounds mixed in.

Timing. The writer writes one block (5 ms) at a time into a pipe that is
kept full, so every write returns just as the oldest queued audio is
played out, and the block written next starts playing about one queue
length later: ``latency_s`` (pipe + ALSA buffer, from their real sizes,
plus ``extra_latency_s``, a per-rig calibration measured with a mic). Each
block's start time is re-taken from its own write, so the sound card's
clock and the PTP-disciplined system clock can't drift apart over a long
session. A sound due inside a block starts at the right sample of it, so
the scheduling grain is one sample; the remaining error is how steady that
queue length is (about one ALSA period), to be measured on the rig.

Output *n* is channels 2n-1 and 2n; mono files go to both.
"""

from __future__ import annotations

import fcntl
import heapq
import itertools
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from src.shared.resample import resample

F_SETPIPE_SZ = 1031
F_GETPIPE_SZ = 1032
FADE_S = 0.005  # fade-out where a sound is cut short, so it doesn't click


class AplaySink:
    """Raw PCM into ``aplay`` through a small pipe."""

    def __init__(self, device: str, channels: int, rate: int,
                 buffer_us: int = 20_000, period_us: int = 5_000):
        self.proc = subprocess.Popen(
            ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE",
             "-r", str(rate), "-c", str(channels),
             f"--buffer-time={buffer_us}", f"--period-time={period_us}", "-"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.buffer_s = buffer_us / 1e6
        fd = self.proc.stdin.fileno()
        try:
            fcntl.fcntl(fd, F_SETPIPE_SZ, 4096)  # the kernel rounds up to a page
        except OSError:
            pass
        try:
            self.pipe_bytes = fcntl.fcntl(fd, F_GETPIPE_SZ)
        except OSError:
            self.pipe_bytes = 65536

    def write(self, data: bytes) -> None:
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"aplay exited ({self.proc.returncode}): {err or 'no message'}")
        self.proc.stdin.write(data)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def load_sound(path: str, rate: int, volume: float = 1.0,
               duration_s: float | None = None) -> np.ndarray:
    """A file as float32 stereo frames at *rate*, gain and cut applied."""
    import soundfile

    data, file_rate = soundfile.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    else:
        data = data[:, :2]
    if file_rate != rate and len(data):
        # Band-limited: what *rate* can't hold is filtered out, not aliased.
        data = resample(data, file_rate, rate)
    if duration_s is not None:
        keep = max(1, round(duration_s * rate))
        if keep < len(data):
            data = data[:keep].copy()
            fade = min(keep, round(FADE_S * rate))
            data[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
    if volume != 1.0:
        data = data * np.float32(volume)
    return np.ascontiguousarray(data, dtype=np.float32)


@dataclass(order=True)
class _Pending:
    target: float                       # wall-clock seconds; -inf = next block
    seq: int
    samples: np.ndarray = field(compare=False)
    output: int = field(compare=False)
    meta: dict = field(compare=False)


@dataclass
class _Voice:
    samples: np.ndarray
    channels: tuple[int, int]
    pos: int
    start: int  # frame offset inside the current block (first block only)


class SoundStream:
    """See the module docstring. *on_fire(meta, play_at, late_s)* runs on the
    writer thread when a sound starts; *on_error(message)* if output fails."""

    def __init__(self, device: str, channels: int, *, rate: int = 48_000,
                 block_s: float = 0.005, extra_latency_s: float = 0.0,
                 sink_factory=AplaySink, clock=time.time,
                 on_fire=None, on_error=None, logger=None):
        self.device = device
        self.channels = max(2, int(channels))
        self.outputs = self.channels // 2
        self.rate = rate
        self.block = max(1, round(block_s * rate))
        self.extra_latency_s = extra_latency_s
        self._sink_factory = sink_factory
        self._clock = clock
        self._on_fire = on_fire
        self._on_error = on_error
        self._logger = logger
        self._sink = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending: list[_Pending] = []
        self._seq = itertools.count()
        self._voices: list[_Voice] = []
        self.latency_s = 0.0
        self.error: str | None = None
        self.ready = threading.Event()
        self.blocks_written = 0

    # -- control ----------------------------------------------------------

    def start(self) -> None:
        self._sink = self._sink_factory(self.device, self.channels, self.rate)
        frame_bytes = 2 * self.channels
        queued_frames = getattr(self._sink, "pipe_bytes", 0) / frame_bytes
        self.latency_s = (queued_frames / self.rate
                          + getattr(self._sink, "buffer_s", 0.0)
                          + self.extra_latency_s)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="sound-stream")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._sink is not None:
            self._sink.close()
            self._sink = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def schedule(self, target: float | None, samples: np.ndarray, output: int,
                 meta: dict | None = None) -> None:
        """Start *samples* on *output* so its first frame is heard at wall
        time *target* (None: as soon as possible, not counted as late)."""
        if not 1 <= output <= self.outputs:
            raise ValueError(f"output {output} doesn't exist ({self.outputs} on this card)")
        item = _Pending(float("-inf") if target is None else float(target),
                        next(self._seq), samples, output, dict(meta or {}))
        with self._lock:
            heapq.heappush(self._pending, item)

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    # -- writer -------------------------------------------------------------

    def _run(self) -> None:
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(60))
        except (PermissionError, AttributeError, OSError):
            pass
        block_s = self.block / self.rate
        # Writes return at once until the pipe and ALSA buffer are full; only
        # after that does a write's return time say when audio plays.
        warmup_until = self._clock() + 2 * self.latency_s + 0.1
        last_return = None
        try:
            while not self._stop.is_set():
                now = self._clock()
                buf = np.zeros((self.block, self.channels), dtype=np.float32)
                if last_return is not None and now >= warmup_until:
                    self.ready.set()
                    self._start_due(last_return + self.latency_s, block_s)
                self._mix(buf)
                pcm = (np.clip(buf, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                self._sink.write(pcm)
                self.blocks_written += 1
                last_return = self._clock()
        except Exception as e:  # noqa: BLE001 -- reported to the module, not raised
            self.error = str(e)
            if self._logger:
                self._logger.error(f"Sound output failed: {e}")
            if self._on_error:
                self._on_error(self.error)

    def _start_due(self, block_play_at: float, block_s: float) -> None:
        """Turn pending sounds due before this block ends into voices."""
        while True:
            with self._lock:
                if not self._pending or self._pending[0].target >= block_play_at + block_s:
                    return
                item = heapq.heappop(self._pending)
            if item.target == float("-inf"):
                offset, late = 0, 0.0
            else:
                offset = round((item.target - block_play_at) * self.rate)
                late = 0.0
                if offset < 0:
                    late, offset = -offset / self.rate, 0
                offset = min(offset, self.block - 1)
            c0 = 2 * (item.output - 1)
            self._voices.append(_Voice(item.samples, (c0, c0 + 1), 0, offset))
            if self._on_fire:
                try:
                    self._on_fire(item.meta, block_play_at + offset / self.rate, late)
                except Exception as e:  # noqa: BLE001 -- logging must not stop audio
                    if self._logger:
                        self._logger.warning(f"Sound event log failed: {e}")

    def _mix(self, buf: np.ndarray) -> None:
        alive = []
        for v in self._voices:
            room = self.block - v.start
            seg = v.samples[v.pos:v.pos + room]
            n = len(seg)
            if n:
                buf[v.start:v.start + n, v.channels[0]] += seg[:, 0]
                buf[v.start:v.start + n, v.channels[1]] += seg[:, 1]
            v.pos += n
            v.start = 0
            if v.pos < len(v.samples):
                alive.append(v)
        self._voices = alive
