#!/usr/bin/env python3
"""
av_sync_check.py -- how well do audio and video line up? Measured against
buzzer/LED pulses fired during a session.

The microphone module's `sync_pulses` command (REST: POST
/api/v1/sessions/<name>/sync_pulses) fires a piezo buzzer and an LED
together from its own GPIO and logs each edge, stamped on the
PTP-disciplined clock, to `*_sync_pulses_*.csv`, which exports with the
session. For every edge this tool finds:

  audio  the buzz onset in each AudioMoth FLAC, placed in wall time with the
         same anchor the aligner uses (audio_align.parse_mic_sidecar)
  video  the first frame in each camera where the LED is lit, at that
         frame's timestamp_ns (what compose uses), and how lit it was

and reports, per stream, the offset from the edge, plus audio minus video
for every mic/camera pair -- how far apart a sound and a flash that
happened at the same instant end up in an aligned composite.

Reading it (details in docs/AV_SYNC_TEST.md):
  * audio - edge: the aligner's audio placement error (+ = audio late).
  * video - edge: frame quantisation means this spreads over about one
    frame period; its centre depends on what the frame timestamp marks
    (start or end of exposure) -- the lit-fraction fit reports that.
  * audio - video: the number that matters for scoring sound against
    behaviour. Its mean is a correctable constant, its spread is the floor.

Usage (on the controller, in its venv -- needs numpy, soundfile, opencv):
    env/bin/python tools/av_sync_check.py /home/pi/controller_share/<session>
        [--out report.json] [--csv pulses.csv] [--hp-hz 1000]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.controller.audio_align import (  # noqa: E402
    _camera_timestamp_csvs,
    discover_audio_streams,
    discover_ptp_history,
    parse_mic_sidecar,
    summarise_ptp_window,
)
from src.shared import av_sync  # noqa: E402

_VIDEO_EXTS = (".ts", ".mp4", ".mkv", ".avi")


def date_dirs(path: str) -> list[str]:
    """A session dir holds YYYYMMDD date dirs; accept either level."""
    subs = sorted(
        d
        for d in glob.glob(os.path.join(path, "*"))
        if os.path.isdir(d) and os.path.basename(d).isdigit()
    )
    return subs or [path]


def read_pulses(dirs: list[str]) -> tuple[list[int], float, float, str]:
    """All logged edges (sorted), pulse_ms, mic_distance_m, source files."""
    edges: list[int] = []
    pulse_ms, distance = 50.0, 0.0
    files = sorted(
        f
        for d in dirs
        for f in glob.glob(os.path.join(d, "**", "*_sync_pulses_*.csv"), recursive=True)
    )
    for path in files:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    edges.append(int(row["edge_on_ns"]))
                    pulse_ms = float(row.get("pulse_ms") or pulse_ms)
                    distance = float(row.get("mic_distance_m") or distance)
                except (KeyError, ValueError):
                    continue
    return (
        sorted(edges),
        pulse_ms,
        distance,
        ", ".join(os.path.basename(f) for f in files),
    )


def video_for_csv(csv_path: str) -> str | None:
    stem = csv_path[: -len("_timestamps.csv")]
    for ext in _VIDEO_EXTS:
        if os.path.isfile(stem + ext):
            return stem + ext
    return None


def camera_label(csv_path: str) -> str:
    return os.path.basename(os.path.dirname(csv_path))


def read_frame_csv(csv_path: str) -> tuple[np.ndarray, np.ndarray | None]:
    ts, exp = [], []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts.append(int(row["timestamp_ns"]))
            except (KeyError, ValueError):
                continue
            try:
                exp.append(float(row.get("exposure_time_us") or "nan"))
            except ValueError:
                exp.append(float("nan"))
    exp_arr = np.asarray(exp, dtype=np.float64)
    return np.asarray(ts, dtype=np.int64), (
        exp_arr if np.isfinite(exp_arr).any() else None
    )


def analyse_audio(
    dirs, edges, *, hp_hz, distance_m
) -> dict[str, dict[int, av_sync.AudioOnset]]:
    """label -> {edge_ns: onset} over every segment that covers an edge."""
    out: dict[str, dict[int, av_sync.AudioOnset]] = {}
    for d in dirs:
        for stream in discover_audio_streams(d):
            fit = parse_mic_sidecar(stream.sidecar_path, stream.audio_path)
            end_ns = fit.sample0_wall_ns + int(
                fit.probe_samples / fit.measured_rate_hz * 1e9
            )
            inside = [e for e in edges if fit.sample0_wall_ns + 3e8 < e < end_ns - 5e8]
            if not inside:
                continue
            onsets = av_sync.audio_onsets(
                stream.audio_path,
                fit.sample0_wall_ns,
                fit.measured_rate_hz,
                inside,
                hp_hz=hp_hz,
                mic_distance_m=distance_m,
            )
            bucket = out.setdefault(stream.label, {})
            for e, o in zip(inside, onsets, strict=True):
                if o is not None:
                    bucket[e] = o
    return out


def analyse_video(dirs, edges, *, pulse_ms) -> dict[str, dict[int, av_sync.LedOnset]]:
    out: dict[str, dict[int, av_sync.LedOnset]] = {}
    for d in dirs:
        for csv_path in _camera_timestamp_csvs(d):
            video = video_for_csv(csv_path)
            if video is None:
                continue
            ts, exp = read_frame_csv(csv_path)
            if ts.size < 10:
                continue
            inside = [e for e in edges if ts[0] < e < ts[-1]]
            if not inside:
                continue
            expected = av_sync.expected_led_state(ts, inside, pulse_ms, exp)
            try:
                trace, _mask = av_sync.led_trace(video, expected)
            except ValueError as e:
                print(f"  ! {camera_label(csv_path)}: {e}", file=sys.stderr)
                continue
            if abs(trace.size - ts.size) > 2:
                print(
                    f"  ! {os.path.basename(video)}: {trace.size} frames decoded vs "
                    f"{ts.size} CSV rows -- frame/row pairing may be off",
                    file=sys.stderr,
                )
            onsets = av_sync.led_onsets(trace, ts, inside, exposure_us=exp)
            bucket = out.setdefault(camera_label(csv_path), {})
            for e, o in zip(inside, onsets, strict=True):
                if o is not None:
                    bucket[e] = o
    return out


def build_report(session: str, *, hp_hz: float = 1000.0) -> tuple[dict, list[dict]]:
    dirs = date_dirs(session)
    edges, pulse_ms, distance, sources = read_pulses(dirs)
    if not edges:
        raise SystemExit(f"No *_sync_pulses_*.csv with edges under {session}")

    audio = analyse_audio(dirs, edges, hp_hz=hp_hz, distance_m=distance)
    video = analyse_video(dirs, edges, pulse_ms=pulse_ms)

    rows: list[dict] = []
    for i, e in enumerate(edges):
        row: dict = {"pulse": i, "edge_ns": e}
        for mic, hits in audio.items():
            o = hits.get(e)
            row[f"audio:{mic}"] = round(o.offset_ms, 2) if o else None
        for cam, hits in video.items():
            o = hits.get(e)
            row[f"video:{cam}"] = round(o.offset_ms, 2) if o else None
            row[f"lit:{cam}"] = round(o.lit_fraction, 2) if o else None
        for mic, ah in audio.items():
            for cam, vh in video.items():
                a, v = ah.get(e), vh.get(e)
                row[f"a-v:{mic}|{cam}"] = (
                    round((a.onset_wall_ns - v.frame_ts_ns) / 1e6, 2)
                    if a and v
                    else None
                )
        rows.append(row)

    report: dict = {
        "session": os.path.basename(os.path.normpath(session)),
        "pulse_files": sources,
        "pulses": len(edges),
        "pulse_ms": pulse_ms,
        "mic_distance_m": distance,
        "audio_minus_edge": {
            m: av_sync.summarise([r[f"audio:{m}"] for r in rows]) for m in audio
        },
        "video_minus_edge": {},
        "audio_minus_video": {},
        "sign": "positive = later than the reference",
    }
    for cam, hits in video.items():
        offs = [r[f"video:{cam}"] for r in rows]
        summary = av_sync.summarise(offs)
        pairs = [(o.offset_ms, o.lit_fraction) for o in hits.values()]
        fit = av_sync.fraction_fit([p[0] for p in pairs], [p[1] for p in pairs])
        exps = [o.exposure_us for o in hits.values() if o.exposure_us]
        if exps:
            summary["exposure_ms"] = round(float(np.median(exps)) / 1e3, 2)
        if fit:
            summary["lit_fraction_fit"] = fit
        report["video_minus_edge"][cam] = summary
    for mic in audio:
        for cam in video:
            key = f"{mic}|{cam}"
            report["audio_minus_video"][key] = av_sync.summarise(
                [r[f"a-v:{key}"] for r in rows]
            )

    ptp_csv = discover_ptp_history(dirs[0])
    if ptp_csv:
        report["ptp_during_pulses"] = summarise_ptp_window(
            ptp_csv, edges[0] - int(60e9), edges[-1] + int(60e9)
        )
    return report, rows


def _print(report: dict) -> None:
    def line(name, s):
        if not s.get("detected"):
            print(f"  {name:34s} not detected ({s.get('n', 0)} pulses)")
            return
        print(
            f"  {name:34s} {s['detected']:3d}/{s['n']:<3d} mean {s['mean_ms']:+8.2f}  "
            f"sd {s['std_ms']:6.2f}  [{s['min_ms']:+.1f} .. {s['max_ms']:+.1f}] ms"
        )

    print(
        f"\nSession {report['session']}: {report['pulses']} pulses "
        f"({report['pulse_ms']:.0f} ms), mic distance {report['mic_distance_m']} m"
    )
    print("\nAudio onset - GPIO edge (aligner placement error):")
    for m, s in report["audio_minus_edge"].items():
        line(m, s)
    print("\nFirst lit frame timestamp - GPIO edge:")
    for c, s in report["video_minus_edge"].items():
        line(c, s)
        if "lit_fraction_fit" in s:
            f = s["lit_fraction_fit"]
            print(
                f"  {'':34s} lit-fraction fit: intercept {f['intercept_ms']:+.1f} ms, "
                f"slope {f['slope_ms']:.1f} ms "
                f"(exposure {s.get('exposure_ms', '?')} ms)"
            )
    print("\nAudio - video (sound vs flash, as an aligned composite shows them):")
    for k, s in report["audio_minus_video"].items():
        line(k, s)
    if report.get("ptp_during_pulses"):
        print(f"\nPTP during pulses: {report['ptp_during_pulses']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", help="session dir (or one of its date dirs)")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--csv", help="write the per-pulse table here")
    ap.add_argument(
        "--hp-hz",
        type=float,
        default=1000.0,
        help="high-pass before onset detection (default 1000)",
    )
    args = ap.parse_args(argv)

    report, rows = build_report(args.session, hp_hz=args.hp_hz)
    _print(report)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
    if args.csv and rows:
        keys = sorted(
            {k for r in rows for k in r},
            key=lambda k: (k != "pulse", k != "edge_ns", k),
        )
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
