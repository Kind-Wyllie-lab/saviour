"""
Turn an exported SAVIOUR session into one NWB file (plans/nwb-export.md,
mapping in docs/NWB_EXPORT_DESIGN.md).

Run in the NWB venv (``env-nwb``, see nwb_export.py), as a child of the
controller or by hand::

    env-nwb/bin/python -m src.controller.nwb_convert /share/<session> \\
        [--output out.nwb] [--species "Rattus norvegicus"] [--subject-id R1]

``tools/export_nwb.py`` is the same thing for a workstation. Everything
above ``convert()`` imports without pynwb (the controller uses
``inspect_session`` / ``clean_options`` / ``read_metadata`` for the form).

What goes in, all on the session's clock (seconds from its UTC start):

- each camera segment: an external-file ``ImageSeries`` (the video stays
  beside the .nwb; relative path) with its real per-frame PTP timestamps,
  pre-stage rows dropped as compose does; apa / loom tracking columns as a
  pixel-space ``SpatialSeries`` (+ zone state) under ``processing/behavior``
- each AudioMoth recording: an embedded ``TimeSeries`` (int16, gzip), sample 0
  placed by ``audio_align.parse_mic_sidecar`` (PTP, latency-anchored) at its
  measured true rate
- TTL edges: one ``IntervalSeries`` per pin (+1 rising / -1 falling)
- APA shock log: an ``EventsTable`` (event, rotation speed)
- session markers (``markers.csv``): an ``EventsTable``
- one ``Device`` per module; the subject from the export form
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import UTC, datetime

from src.shared import timestamps

DEFAULT_SPECIES = "Rattus norvegicus"
SEXES = {"M", "F", "U", "O"}
_SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")
_ISO_DURATION = re.compile(r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?$")
INCLUDE_KEYS = ("video", "tracking", "audio", "ttl", "events", "markers")
TEXT_FIELDS = {
    "species": 120, "subject_id": 120, "strain": 120, "sex": 1, "age": 40,
    "weight": 40, "genotype": 200, "subject_description": 1000,
    "session_description": 2000, "experimenter": 200, "institution": 200,
    "lab": 200, "keywords": 400,
}


def safe_name(name: str) -> bool:
    return bool(name) and bool(_SAFE.match(name))


def day_dirs(session_dir: str) -> list[str]:
    """The session's recording day folders (``YYYYMMDD``), oldest first."""
    out = []
    for entry in sorted(os.listdir(session_dir)):
        p = os.path.join(session_dir, entry)
        if os.path.isdir(p) and not entry.startswith(("_", ".")):
            out.append(p)
    return out


def read_metadata(session_dir: str) -> dict:
    """``session_metadata.json`` (experimenter, rat_id, strain, …), or {}."""
    try:
        with open(os.path.join(session_dir, "session_metadata.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def classify_csv(path: str) -> str | None:
    """``ttl`` / ``apa_shocks`` from a CSV's header, else None."""
    try:
        with open(path, newline="") as f:
            header = f.readline()
    except OSError:
        return None
    cols = [c.strip().lower() for c in header.split(",")]
    if cols[:2] == ["timestamp_nanoseconds", "pin_number"]:
        return "ttl"
    if cols[:2] == ["timestamp_nanoseconds", "event"]:
        return "apa_shocks"
    return None


def _event_csvs(day: str) -> list[tuple[str, str, str]]:
    """(module, kind, path) for every TTL / shock CSV under a day folder."""
    found = []
    for module in sorted(os.listdir(day)):
        mdir = os.path.join(day, module)
        if not os.path.isdir(mdir):
            continue
        for name in sorted(os.listdir(mdir)):
            if not name.endswith(".csv") or name.endswith("_timestamps.csv"):
                continue
            if "health_metadata" in name or name.startswith("PENDING_"):
                continue
            path = os.path.join(mdir, name)
            kind = classify_csv(path)
            if kind:
                found.append((module, kind, path))
    return found


def _count_rows(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def inspect_session(session_dir: str) -> dict:
    """What a conversion would include, cheaply (no video probing)."""
    from src.controller import audio_align
    from src.controller.camera_segments import camera_segments

    days = day_dirs(session_dir)
    cameras: dict[str, dict] = {}
    audio: list[dict] = []
    events: list[dict] = []
    for day in days:
        for module in sorted(os.listdir(day)):
            mdir = os.path.join(day, module)
            if not os.path.isdir(mdir):
                continue
            segs = camera_segments(mdir)
            if segs:
                cam = cameras.setdefault(module, {"module": module, "segments": 0,
                                                  "bytes": 0, "tracking": False})
                cam["segments"] += len(segs)
                cam["bytes"] += sum(os.path.getsize(s.video_path) for s in segs)
                cam["tracking"] = cam["tracking"] or bool(_tracking_columns(segs[0].csv_path))
        for st in audio_align.discover_audio_streams(day):
            entry = {"label": st.label, "module": os.path.basename(os.path.dirname(st.audio_path)),
                     "file": os.path.relpath(st.audio_path, session_dir),
                     "seconds": None, "rate": None, "pcm_bytes": None}
            try:
                import soundfile
                info = soundfile.info(st.audio_path)
                entry.update(seconds=round(info.frames / info.samplerate, 1),
                             rate=info.samplerate, pcm_bytes=info.frames * info.channels * 2)
            except Exception:                               # noqa: BLE001 -- unreadable file: shown without a size
                pass
            audio.append(entry)
        for module, kind, path in _event_csvs(day):
            events.append({"module": module, "kind": kind,
                           "file": os.path.relpath(path, session_dir),
                           "rows": _count_rows(path)})
    markers = os.path.join(session_dir, "markers.csv")
    return {
        "days": [os.path.basename(d) for d in days],
        "cameras": list(cameras.values()),
        "audio": audio,
        "audio_pcm_bytes": sum(a["pcm_bytes"] or 0 for a in audio),
        "events": events,
        "markers": _count_rows(markers) if os.path.isfile(markers) else 0,
    }


def clean_options(options: dict | None, session_name: str) -> dict:
    """The export form's fields, checked. Raises ValueError with a message
    for the user."""
    o = options if isinstance(options, dict) else {}
    out: dict = {}
    for key, limit in TEXT_FIELDS.items():
        v = o.get(key)
        v = "" if v is None else str(v).strip()
        if len(v) > limit:
            raise ValueError(f"{key.replace('_', ' ')} is too long")
        out[key] = v
    out["species"] = out["species"] or DEFAULT_SPECIES
    out["sex"] = (out["sex"] or "U").upper()
    if out["sex"] not in SEXES:
        raise ValueError("Sex must be M, F, U (unknown) or O (other)")
    if out["age"] and not _ISO_DURATION.match(out["age"].upper()):
        raise ValueError("Age must be an ISO 8601 duration, e.g. P90D (90 days) or P12W")
    out["age"] = out["age"].upper()
    out["session_description"] = out["session_description"] or session_name
    inc = o.get("include") if isinstance(o.get("include"), dict) else {}
    out["include"] = {k: bool(inc.get(k, True)) for k in INCLUDE_KEYS}
    out["overwrite"] = bool(o.get("overwrite"))
    out["session_name"] = session_name
    return out


def resolve_start(opts: dict, metadata: dict) -> datetime | None:
    """Session start (aware UTC): the controller's session record, else
    the metadata's ``created_at``; None leaves it to the data."""
    for value in (opts.get("session_start"), metadata.get("created_at")):
        dt = timestamps.parse(value) if isinstance(value, str) else None
        if dt:
            return dt
    return None


def _tracking_columns(csv_path: str) -> tuple[str, str, str | None] | None:
    try:
        with open(csv_path, newline="") as f:
            header = [c.strip() for c in f.readline().split(",")]
    except OSError:
        return None
    for x, y, z in (("det_cx", "det_cy", "in_zone"), ("cx", "cy", "zone_state")):
        if x in header and y in header:
            return x, y, z if z in header else None
    return None


def _float(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def read_camera_csv(csv_path: str, skip: int = 0) -> dict:
    """``timestamp_ns`` (+ tracking columns when present), leading
    ``skip`` rows dropped."""
    cols = _tracking_columns(csv_path)
    ts: list[int] = []
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < skip:
                continue
            try:
                t = int(row["timestamp_ns"])
            except (KeyError, TypeError, ValueError):
                continue
            ts.append(t)
            if cols:
                xs.append(_float(row.get(cols[0])))
                ys.append(_float(row.get(cols[1])))
                if cols[2]:
                    zs.append(_float(row.get(cols[2])))
    return {"timestamp_ns": ts, "tracking": cols, "x": xs, "y": ys, "zone": zs}


def read_ttl_csv(path: str) -> dict[int, dict]:
    """Per pin: edge times (ns), +1/-1 states, description, role, mode."""
    pins: dict[int, dict] = {}
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            try:
                t = int(row[0])
                pin = int(row[1])
            except ValueError:
                continue
            state = row[3].strip().upper()
            high = "HIGH" in state or state in ("1", "TRUE")
            p = pins.setdefault(pin, {"t": [], "v": [], "mode": row[2].strip(),
                                      "description": "", "role": ""})
            p["t"].append(t)
            p["v"].append(1 if high else -1)
            if len(row) > 4 and row[4].strip():
                p["description"] = row[4].strip()
            if len(row) > 5 and row[5].strip():
                p["role"] = row[5].strip()
    return pins


def read_shock_csv(path: str) -> dict:
    t: list[int] = []
    ev: list[str] = []
    rpm: list[float] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 2:
                continue
            try:
                t.append(int(row[0]))
            except ValueError:
                continue
            ev.append(row[1].strip())
            rpm.append(_float(row[2]) if len(row) > 2 else float("nan"))
    return {"t": t, "event": ev, "rpm": rpm}


def read_markers(path: str) -> dict:
    t: list[int] = []
    labels: list[str] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                t.append(int(row["recv_wall_ns"]))
            except (KeyError, TypeError, ValueError):
                continue
            src = (row.get("source") or "").strip()
            labels.append(f"{row.get('label', '')}" + (f" [{src}]" if src else ""))
    return {"t": t, "label": labels}


def nwb_name(*parts: str) -> str:
    """An NWB object name: no '/' or ':'."""
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", "_".join(p for p in parts if p))


# --------------------------------------------------------------------------- #
# Conversion (needs pynwb)                                                    #
# --------------------------------------------------------------------------- #

class Progress:
    def __init__(self, json_lines: bool):
        self.json_lines = json_lines

    def _out(self, msg: dict) -> None:
        if self.json_lines:
            print(json.dumps(msg), flush=True)
        else:
            text = msg.get("log") or msg.get("warning") or msg.get("stage")
            if "progress" in msg:
                text = f"[{msg['progress'] * 100:5.1f}%] {msg.get('stage', '')}"
            if msg.get("warning"):
                text = f"warning: {msg['warning']}"
            if text:
                print(text, flush=True)

    def stage(self, frac: float, stage: str) -> None:
        self._out({"progress": round(min(1.0, max(0.0, frac)), 4), "stage": stage})

    def log(self, text: str) -> None:
        self._out({"log": text})

    def warn(self, text: str) -> None:
        self._out({"warning": text})


def convert(session_dir: str, output: str, opts: dict, progress: Progress) -> dict:
    import numpy as np
    import soundfile
    from hdmf.backends.hdf5.h5_utils import H5DataIO
    from hdmf.data_utils import GenericDataChunkIterator
    from pynwb import NWBHDF5IO, NWBFile, TimeSeries
    from pynwb.behavior import Position, SpatialSeries
    from pynwb.file import Subject
    from pynwb.image import ImageSeries
    from pynwb.misc import IntervalSeries

    from src.controller import audio_align, compose

    session_dir = os.path.realpath(session_dir)
    session_name = opts.get("session_name") or os.path.basename(session_dir)
    inc = opts.get("include") or {k: True for k in INCLUDE_KEYS}
    metadata = read_metadata(session_dir)
    days = day_dirs(session_dir)
    out_dir = os.path.dirname(os.path.abspath(output))
    progress.stage(0.01, "Reading the session")

    # ── gather everything first, so the start time can fall back to the data
    cameras = []          # (module, segment, skip)
    audios = []           # (AudioStream, SidecarFit, info)
    ttl_files = []        # (module, path)
    shock_files = []
    for day in days:
        if inc.get("video") or inc.get("tracking"):
            try:
                streams = compose.discover_streams(day, None)
            except compose.ComposeError:
                streams = []
            for st in streams:
                segs = st.segments or []
                for i, seg in enumerate(segs):
                    skip = st.csv_skip if i == 0 else compose._prestage_skip(
                        seg.video_path, _count_rows(seg.csv_path))
                    cameras.append((st.name, seg, skip))
        if inc.get("audio"):
            for st in audio_align.discover_audio_streams(day):
                try:
                    fit = audio_align.parse_mic_sidecar(st.sidecar_path, st.audio_path)
                    info = soundfile.info(st.audio_path)
                except Exception as e:                      # noqa: BLE001 -- skip one bad recording, say so
                    progress.warn(f"audio {os.path.basename(st.audio_path)} skipped: {e}")
                    continue
                audios.append((st, fit, info))
        for module, kind, path in _event_csvs(day):
            if kind == "ttl" and inc.get("ttl"):
                ttl_files.append((module, path))
            elif kind == "apa_shocks" and inc.get("events"):
                shock_files.append((module, path))

    cam_data = []
    for module, seg, skip in cameras:
        cam_data.append((module, seg, skip, read_camera_csv(seg.csv_path, skip)))
    ttl_data = [(m, p, read_ttl_csv(p)) for m, p in ttl_files]
    shock_data = [(m, p, read_shock_csv(p)) for m, p in shock_files]
    markers_path = os.path.join(session_dir, "markers.csv")
    markers = read_markers(markers_path) if inc.get("markers") and os.path.isfile(markers_path) else None

    start_dt = resolve_start(opts, metadata)
    if start_dt is None:
        firsts = [d["timestamp_ns"][0] for *_, d in cam_data if d["timestamp_ns"]]
        firsts += [fit.sample0_wall_ns for _, fit, _ in audios]
        if not firsts:
            raise ValueError("No session start time and no timestamped data to infer one")
        start_dt = datetime.fromtimestamp(min(firsts) / 1e9, UTC).replace(microsecond=0)
        progress.warn("No session start on record; using the first data timestamp")
    t0 = int(start_dt.timestamp()) * 1_000_000_000

    def rel(ns_list) -> np.ndarray:
        return (np.asarray(ns_list, dtype=np.int64) - t0) / 1e9

    # ── file + subject
    keywords = [k.strip() for k in (opts.get("keywords") or "").split(",") if k.strip()]
    nwb = NWBFile(
        session_description=opts.get("session_description") or session_name,
        identifier=f"{session_name}-{int(time.time())}",
        session_start_time=start_dt,
        session_id=session_name,
        experimenter=[opts.get("experimenter") or metadata.get("experimenter")]
        if (opts.get("experimenter") or metadata.get("experimenter")) else None,
        institution=opts.get("institution") or None,
        lab=opts.get("lab") or None,
        keywords=keywords or None,
        experiment_description=metadata.get("experiment") or None,
        notes=_notes(session_name, metadata, opts),
        source_script="SAVIOUR src/controller/nwb_convert.py",
        source_script_file_name="nwb_convert.py",
    )
    nwb.subject = Subject(
        subject_id=opts.get("subject_id") or metadata.get("rat_id") or "unknown",
        species=opts.get("species") or DEFAULT_SPECIES,
        strain=opts.get("strain") or metadata.get("strain") or None,
        sex=opts.get("sex") or "U",
        age=opts.get("age") or None,
        weight=opts.get("weight") or None,
        genotype=opts.get("genotype") or None,
        description=opts.get("subject_description") or None,
    )

    devices: dict = {}

    def device(module: str, kind: str):
        if module not in devices:
            devices[module] = nwb.create_device(
                name=nwb_name(module), description=f"SAVIOUR {kind} module {module}",
                manufacturer="SAVIOUR")
        return devices[module]

    summary = {"video": 0, "tracking": 0, "audio": 0, "ttl": 0, "events": 0, "markers": 0}

    # ── video + tracking
    behavior = None
    seg_count: dict[str, int] = {}
    for module, seg, skip, data in cam_data:
        n = seg_count[module] = seg_count.get(module, 0) + 1
        ts = rel(data["timestamp_ns"])
        if not len(ts):
            progress.warn(f"{os.path.basename(seg.video_path)}: no frame timestamps, skipped")
            continue
        name = nwb_name("video", module, str(n))
        if inc.get("video"):
            series = ImageSeries(
                name=name,
                description=(f"{module} segment {n}: external video, one timestamp per "
                             f"frame from the camera's PTP sensor clock"
                             + (f"; first {skip} log rows (pre-stage) dropped" if skip else "")),
                # relative to the .nwb, so the pair can move together
                external_file=[os.path.relpath(seg.video_path, out_dir)],
                format="external", starting_frame=[0], timestamps=ts,
                device=device(module, "camera"))
            nwb.add_acquisition(series)
            summary["video"] += 1
        if inc.get("tracking") and data["tracking"]:
            if behavior is None:
                behavior = nwb.create_processing_module(
                    name="behavior", description="Online tracking from SAVIOUR cameras")
                behavior.add(Position(name="position"))
            xy = np.column_stack([data["x"], data["y"]]).astype(np.float32)
            behavior["position"].add_spatial_series(SpatialSeries(
                name=nwb_name("position", module, str(n)),
                description=f"{module} online detection centre ({data['tracking'][0]}, "
                            f"{data['tracking'][1]})",
                data=xy, timestamps=ts, unit="pixels",
                reference_frame="camera image pixels, origin top-left, uncalibrated"))
            if data["tracking"][2] and data["zone"]:
                behavior.add(TimeSeries(
                    name=nwb_name(data["tracking"][2], module, str(n)),
                    description=f"{module} {data['tracking'][2]} per frame",
                    data=np.asarray(data["zone"], dtype=np.float32), timestamps=ts,
                    unit="n.a."))
            summary["tracking"] += 1
    progress.stage(0.1, "Video and tracking added")

    # ── audio (streamed from FLAC at write time)
    total_samples = sum(info.frames for _, _, info in audios) or 1
    done_samples = [0]

    class FlacChunks(GenericDataChunkIterator):
        def __init__(self, path: str, info, **kw):
            self.path, self.info = path, info
            super().__init__(**kw)

        def _get_data(self, selection):
            sl = selection[0]
            with soundfile.SoundFile(self.path) as f:
                f.seek(sl.start)
                data = f.read(sl.stop - sl.start, dtype="int16", always_2d=self.info.channels > 1)
            done_samples[0] += sl.stop - sl.start
            progress.stage(0.15 + 0.8 * done_samples[0] / total_samples,
                           f"Writing audio {os.path.basename(self.path)}")
            return data if self.info.channels == 1 else data[:, selection[1]]

        def _get_maxshape(self):
            return (self.info.frames,) if self.info.channels == 1 else (
                self.info.frames, self.info.channels)

        def _get_dtype(self):
            return np.dtype("int16")

    for st, fit, info in audios:
        module = os.path.basename(os.path.dirname(st.audio_path))
        if info.frames == 0:
            continue
        chunk = min(info.frames, max(1, int(info.samplerate)))
        it = FlacChunks(st.audio_path, info, buffer_gb=0.05,
                        chunk_shape=(chunk,) if info.channels == 1 else (chunk, info.channels),
                        display_progress=False)
        nwb.add_acquisition(TimeSeries(
            name=nwb_name("audio", st.label),
            description=(f"{module} AudioMoth recording {os.path.basename(st.audio_path)}; "
                         f"sample 0 placed on the PTP clock ({fit.anchor} anchor), "
                         f"rate = measured true rate"),
            comments=json.dumps(fit.as_report()),
            data=H5DataIO(it, compression="gzip", compression_opts=1),
            starting_time=(fit.sample0_wall_ns - t0) / 1e9,
            rate=float(fit.measured_rate_hz),
            unit="full scale", conversion=1 / 32768,
        ))
        device(module, "microphone")
        summary["audio"] += 1

    # ── TTL
    for module, _path, pins in ttl_data:
        device(module, "TTL")
        for pin, p in sorted(pins.items()):
            desc = "; ".join(x for x in (p["description"], f"role {p['role']}" if p["role"] else "",
                                         f"mode {p['mode']}" if p["mode"] else "") if x)
            name = nwb_name("ttl", module, f"pin{pin}")
            k = 2
            while name in nwb.acquisition:
                name = nwb_name("ttl", module, f"pin{pin}", str(k))
                k += 1
            nwb.add_acquisition(IntervalSeries(
                name=name, description=f"TTL edges on GPIO {pin} (+1 rising, -1 falling)"
                                       + (f": {desc}" if desc else ""),
                data=np.asarray(p["v"], dtype=np.int8), timestamps=rel(p["t"])))
            summary["ttl"] += 1

    # ── APA shocks
    for module, _path, d in shock_data:
        if not d["t"]:
            continue
        device(module, "APA arduino")
        _events(nwb, nwb_name("apa", module, "shock_events"),
                "APA shock log (SENDING_SHOCK, STOPPING_SHOCK, SHOCK_DELIVERY, "
                "SHOCK_STOP_DELIVERY) with the arena rotation speed at each event",
                rel(d["t"]), {"annotation": ("event", d["event"]),
                              "rotation_speed_rpm": ("arena rotation speed (rpm)", d["rpm"])})
        summary["events"] += 1

    if markers and markers["t"]:
        _events(nwb, "markers", "Session markers from markers.csv (controller receive time)",
                rel(markers["t"]), {"annotation": ("marker label [source]", markers["label"])})
        summary["markers"] = len(markers["t"])

    # ── write
    progress.stage(0.15, "Writing the NWB file")
    partial = output[:-4] + ".partial.nwb" if output.endswith(".nwb") else output + ".partial"
    with NWBHDF5IO(partial, "w") as io:
        io.write(nwb)
    os.replace(partial, output)
    progress.stage(0.96, "Validating")
    validation = _validate(output, progress)
    progress.log("Included: " + ", ".join(f"{k} {v}" for k, v in summary.items() if v)
                 or "Included: metadata only")
    return {"summary": summary, "validation": validation,
            "session_start_time": timestamps.iso(start_dt)}


def _notes(session_name: str, metadata: dict, opts: dict) -> str:
    keep = {k: metadata[k] for k in ("target", "batch", "stage", "trial", "external_sync")
            if metadata.get(k) not in (None, "")}
    lines = [f"Exported from SAVIOUR session {session_name}."]
    if keep:
        lines.append("Session metadata: " + json.dumps(keep))
    lines.append("Video is referenced as external files relative to this .nwb; keep "
                 "the session folder together. Health / PTP logs stay as sidecar files.")
    return "\n".join(lines)


def _events(nwb, name: str, description: str, times, columns: dict) -> None:
    """An EventsTable (pynwb 4+: timestamp + one column per entry of
    ``columns`` {name: (description, values)})."""
    table = nwb.create_events_table(name=name, description=description,
                                    source_description="SAVIOUR")
    for col, (desc, _) in columns.items():
        table.add_column(name=col, description=desc)
    for i, t in enumerate(times):
        table.add_row(timestamp=float(t), **{c: v[i] for c, (_, v) in columns.items()})


def _validate(path: str, progress: Progress) -> dict:
    try:
        try:
            from pynwb.validation import validate
            res = validate(path=path)
        except ImportError:
            from pynwb import validate
            res = validate(paths=[path])
        errors = res[0] if isinstance(res, tuple) else res
        errors = [str(e) for e in (errors or [])]
    except Exception as e:                                  # noqa: BLE001 -- validation is advisory
        progress.warn(f"validation could not run: {e}")
        return {"ran": False, "errors": []}
    for e in errors[:20]:
        progress.warn(f"validation: {e}")
    return {"ran": True, "errors": errors[:50]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export a SAVIOUR session to NWB")
    ap.add_argument("session_dir")
    ap.add_argument("--output", help="default: <session_dir>/<session>.nwb")
    ap.add_argument("--options-json", default="{}", help="export form fields as JSON")
    ap.add_argument("--species")
    ap.add_argument("--subject-id")
    ap.add_argument("--sex")
    ap.add_argument("--age")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--json-progress", action="store_true")
    args = ap.parse_args(argv)
    progress = Progress(args.json_progress)
    session_dir = os.path.realpath(args.session_dir)
    name = os.path.basename(session_dir)
    try:
        raw = json.loads(args.options_json)
        for key, val in (("species", args.species), ("subject_id", args.subject_id),
                         ("sex", args.sex), ("age", args.age)):
            if val:
                raw[key] = val
        if args.no_audio or args.no_video:
            inc = dict(raw.get("include") or {})
            if args.no_audio:
                inc["audio"] = False
            if args.no_video:
                inc["video"] = False
            raw["include"] = inc
        session_start = raw.get("session_start")
        opts = clean_options(raw, name)
        opts["session_start"] = session_start
        output = args.output or os.path.join(session_dir, f"{name}.nwb")
        if os.path.exists(output) and not (args.overwrite or opts["overwrite"]):
            raise ValueError(f"{output} exists (--overwrite to replace it)")
        result = convert(session_dir, output, opts, progress)
    except Exception as e:                                  # noqa: BLE001 -- reported as the job's error
        out = args.output or os.path.join(session_dir, f"{name}.nwb")
        for partial in (out[:-4] + ".partial.nwb", out + ".partial"):
            if os.path.exists(partial):
                os.remove(partial)
        if args.json_progress:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json_progress:
        print(json.dumps({"done": output, **result}), flush=True)
    else:
        print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
