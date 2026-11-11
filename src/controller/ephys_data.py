"""Open Ephys recordings inside a session's uploaded ``_ephys/`` folder.

One upload can hold several recordings (``experimentN/recordingM``), e.g. a
bench test followed by the real session. Both the clock alignment
(``tools/ephys/align_cli.py``) and the accelerometer step
(``ephys_accel.py``) must use the recording that actually overlaps the
SAVIOUR session, so the choice lives here, once.

Pure: numpy + stdlib, no Flask, cheap to import and test.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import asdict, dataclass, field

import numpy as np

# Below this share of the best overlap, a second candidate doesn't make the
# choice ambiguous (e.g. a recording that only clips the session's start).
AMBIGUOUS_RATIO = 0.8

_SOFTWARE_TIME = re.compile(r"Software Time.*?:\s*(\d+)")


class EphysSelectionError(ValueError):
    """No recording, or more than one, plausibly matches the session."""


@dataclass
class Stream:
    folder: str              # continuous/<folder>, absolute path
    name: str                # stream_name from structure.oebin
    sample_rate: float
    num_channels: int
    channels: list[dict]
    n_samples: int

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.sample_rate if self.sample_rate else 0.0


@dataclass
class Recording:
    path: str                         # absolute path of recordingN/
    rel: str                          # path relative to the ephys root (its id)
    start_unix_s: float | None        # acquisition PC clock at the first sample
    duration_s: float
    ttl_dirs: list[str] = field(default_factory=list)
    streams: list[Stream] = field(default_factory=list)

    @property
    def end_unix_s(self) -> float | None:
        if self.start_unix_s is None:
            return None
        return self.start_unix_s + self.duration_s

    def main_stream(self) -> Stream | None:
        """The acquisition-board stream (the one carrying CH/AUX/ADC)."""
        for s in self.streams:
            if "acquisition" in s.name.lower():
                return s
        return self.streams[0] if self.streams else None

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("streams")
        d["ttl_dirs"] = [os.path.relpath(p, self.path) for p in self.ttl_dirs]
        d["aux_ports"] = sorted(aux_ports(self.main_stream()).keys())
        return d


def _read_software_time(rec_dir: str) -> float | None:
    path = os.path.join(rec_dir, "sync_messages.txt")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = _SOFTWARE_TIME.search(f.read())
    except OSError:
        return None
    return int(m.group(1)) / 1000.0 if m else None


def _n_samples(stream_dir: str, num_channels: int) -> int:
    ts = os.path.join(stream_dir, "timestamps.npy")
    if os.path.exists(ts):
        return int(np.load(ts, mmap_mode="r").shape[0])
    dat = os.path.join(stream_dir, "continuous.dat")
    if os.path.exists(dat) and num_channels:
        return os.path.getsize(dat) // (2 * num_channels)
    return 0


def _load_streams(rec_dir: str) -> list[Stream]:
    try:
        with open(os.path.join(rec_dir, "structure.oebin"), encoding="utf-8") as f:
            oebin = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for c in oebin.get("continuous", []):
        folder = os.path.join(rec_dir, "continuous", c.get("folder_name", "").strip("/"))
        nch = int(c.get("num_channels", 0))
        out.append(Stream(
            folder=folder,
            name=str(c.get("stream_name") or c.get("folder_name", "")),
            sample_rate=float(c.get("sample_rate", 0.0)),
            num_channels=nch,
            channels=list(c.get("channels", [])),
            n_samples=_n_samples(folder, nch),
        ))
    return out


def _ttl_dirs(rec_dir: str) -> list[str]:
    found = []
    for dirpath, _dirs, files in os.walk(os.path.join(rec_dir, "events")):
        if "timestamps.npy" in files and "states.npy" in files:
            found.append(dirpath)
    return sorted(found)


def list_recordings(root: str) -> list[Recording]:
    """Every Open Ephys recording (a folder holding ``structure.oebin``)
    under ``root``, sorted by path."""
    recs = []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        if "structure.oebin" not in files:
            continue
        streams = _load_streams(dirpath)
        main = next((s for s in streams if "acquisition" in s.name.lower()),
                    streams[0] if streams else None)
        recs.append(Recording(
            path=dirpath,
            rel=os.path.relpath(dirpath, root).replace(os.sep, "/"),
            start_unix_s=_read_software_time(dirpath),
            duration_s=main.duration_s if main else 0.0,
            ttl_dirs=_ttl_dirs(dirpath),
            streams=streams,
        ))
        dirs[:] = []  # a recording folder never nests another
    return sorted(recs, key=lambda r: r.rel)


def find_recording(root: str, rel: str) -> Recording:
    for r in list_recordings(root):
        if r.rel == rel:
            return r
    raise EphysSelectionError(f"no recording {rel!r} in the uploaded dataset")


def saviour_ttl_csvs(session_dir: str) -> list[str]:
    """The session's TTL module logs (same rule as tool1's
    ``find_saviour_ttl_csv``: a .csv with 'ttl' and not 'health' in its
    name), skipping SAVIOUR's own derived ``_ephys`` folder."""
    out = []
    for dirpath, dirs, files in os.walk(session_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("_"))
        for name in sorted(files):
            low = name.lower()
            if low.endswith(".csv") and "ttl" in low and "health" not in low:
                out.append(os.path.join(dirpath, name))
    return out


def saviour_span(session_dir: str) -> tuple[float, float] | None:
    """(first, last) TTL timestamp of the session in unix seconds, or None
    when the session has no TTL log with timestamps."""
    lo = hi = None
    for path in saviour_ttl_csvs(session_dir):
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader, [])
                if not header:
                    continue
                col = next((i for i, h in enumerate(header)
                            if "nanosecond" in h.lower()), 0)
                for row in reader:
                    try:
                        t = int(row[col]) / 1e9
                    except (ValueError, IndexError):
                        continue
                    lo = t if lo is None or t < lo else lo
                    hi = t if hi is None or t > hi else hi
        except OSError:
            continue
    return (lo, hi) if lo is not None else None


def overlap_s(rec: Recording, span: tuple[float, float]) -> float:
    if rec.start_unix_s is None:
        return 0.0
    return max(0.0, min(rec.end_unix_s, span[1]) - max(rec.start_unix_s, span[0]))


def pick_recording(recs: list[Recording],
                   span: tuple[float, float] | None) -> tuple[Recording, str]:
    """The recording the session belongs to, and a one-line reason.

    Raises EphysSelectionError (listing the candidates) when there is no
    recording, when none overlaps the session, or when two overlap about
    equally. A single recording is accepted without a span check only when
    the session has no TTL log to compare against.
    """
    if not recs:
        raise EphysSelectionError("no Open Ephys recording found in the upload")
    if span is None:
        if len(recs) == 1:
            return recs[0], "only recording (session has no TTL log to match)"
        raise EphysSelectionError(
            "session has no TTL log, so the recording can't be matched; "
            "choose one: " + describe(recs, None))
    scored = sorted(((overlap_s(r, span), r) for r in recs),
                    key=lambda x: x[0], reverse=True)
    best_ov, best = scored[0]
    if best_ov <= 0:
        raise EphysSelectionError(
            "no recording overlaps the session's TTL log; choose one: "
            + describe(recs, span))
    if len(scored) > 1 and scored[1][0] >= AMBIGUOUS_RATIO * best_ov:
        raise EphysSelectionError(
            "two recordings overlap the session about equally; choose one: "
            + describe(recs, span))
    sess = span[1] - span[0]
    return best, (f"overlaps the session's TTL log for {best_ov:.0f} of "
                  f"{sess:.0f} s ({len(recs)} recording"
                  f"{'s' if len(recs) != 1 else ''} in the upload)")


def describe(recs: list[Recording], span) -> str:
    parts = []
    for r in recs:
        ov = f", overlap {overlap_s(r, span):.0f} s" if span else ""
        parts.append(f"{r.rel} ({r.duration_s:.0f} s{ov})")
    return "; ".join(parts)


# ---------------------------------------------------------------- AUX ----

_AUX = re.compile(r"^(?:(?P<port>[A-Z]\d*)_)?AUX(?P<n>\d+)$")


def aux_ports(stream: Stream | None) -> dict[str, list[int]]:
    """Headstage accelerometer channels grouped by port, e.g.
    ``{"A1": [64, 65, 66]}``. Only complete 3-axis groups are returned."""
    if stream is None:
        return {}
    groups: dict[str, list[tuple[int, int]]] = {}
    for i, ch in enumerate(stream.channels):
        m = _AUX.match(str(ch.get("channel_name", "")))
        if m:
            groups.setdefault(m.group("port") or "A", []).append((int(m.group("n")), i))
    return {p: [i for _n, i in sorted(v)][:3]
            for p, v in groups.items() if len(v) >= 3}


def sync_pin(csv_path: str) -> int:
    """The TTL pin carrying the shared sync pulse train in a SAVIOUR TTL
    log: the pin logged in ``pseudorandom`` mode, else the only pin.
    Raises EphysSelectionError when that's ambiguous."""
    pins: dict[int, set[str]] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                pin = int(row.get("pin_number", ""))
            except ValueError:
                continue
            pins.setdefault(pin, set()).add((row.get("pin_mode") or "").lower())
    pseudo = [p for p, modes in pins.items() if "pseudorandom" in modes]
    if len(pseudo) == 1:
        return pseudo[0]
    if len(pins) == 1:
        return next(iter(pins))
    raise EphysSelectionError(
        f"can't tell which TTL pin carries the sync train in "
        f"{os.path.basename(csv_path)} (pins {sorted(pins)}); pass one")
