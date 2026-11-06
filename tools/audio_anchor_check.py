#!/usr/bin/env python3
"""
audio_anchor_check.py -- which timestamp best predicts an AudioMoth
recording's true sample-0 time?

Input: kept sync self-test runs (the microphone module's `sync_selftest`
with `keep_audio: true`), one directory per run, each holding
`sync_selftest.json` and `selftest_<label>_timestamps.txt` per AudioMoth.
The buzzer gives the truth: the self-test's median offset says how late
the current anchor (`STARTED`) places the sound, so

    true sample 0 = STARTED - median offset

For every recording, each candidate anchor is compared with that, and the
spread of (candidate - true) across recordings is what matters: a
constant mean can be subtracted, the sd can't.

Candidates:
  STARTED          the current anchor (audio_align.parse_mic_sidecar)
  fit intercept    robust block-index fit over blocks 1.. (steady state)
  stream latency   from the recorder's `LAT <index> <t0> <t1> <latency_us>`
                   lines (microphone.latency_probe): per line,
                   (t0+t1)/2 - latency - index/rate, median over the
                   recording. Only when the sidecars have LAT lines.

Plan: plans/audio-video-sync-residual-validation.md ("Proposed anchor").
Needs numpy only (main env).

Getting the runs off the module (the scratch dirs are root-owned):

    curl -X POST -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \\
      -d '{"command":"sync_selftest","module_id":"microphone_4703",
           "params":{"pulses":20,"interval_s":1.2,"keep_audio":true}}' \\
      http://10.0.0.1:5000/facade/send_command
    # result's scratch_dir, from GET /api/v1/modules/<id>/sync_selftest
    ssh pi@<mic_ip> "sudo tar -C <scratch_dir> -cf - ." | tar -C run_1 -xf -

Usage:
    python3 tools/audio_anchor_check.py run_1 run_2 ... [--csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics as st
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.controller.audio_align import _robust_linfit  # noqa: E402


def read_sidecar(path: str) -> tuple[dict[str, float], list[float], list[tuple]]:
    """(trailer KEY value lines, block-start times, LAT tuples)."""
    trailer: dict[str, float] = {}
    blocks: list[float] = []
    lats: list[tuple[int, float, float, float]] = []
    with open(path) as f:
        for raw in f:
            p = raw.split()
            if len(p) == 1:
                blocks.append(float(p[0]))
            elif len(p) == 2:
                trailer[p[0]] = float(p[1])
            elif len(p) == 5 and p[0] == "LAT":
                lats.append((int(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return trailer, blocks, lats


def anchors_for(sidecar: str, offset_ms: float) -> dict:
    trailer, blocks, lats = read_sidecar(sidecar)
    started = trailer["STARTED"]
    frame_num = trailer.get("FIRST_RECORD_SAMPLES", 32768)
    true0 = started - offset_ms / 1e3
    k = np.arange(len(blocks), dtype=float)
    slope, intercept, _keep = _robust_linfit(k[1:], np.asarray(blocks[1:]))
    rate = frame_num / slope
    row = {
        "offset_ms": offset_ms,
        "STARTED": (started - true0) * 1e3,
        "fit intercept": (intercept - true0) * 1e3,
    }
    if lats:
        est = np.array([(t0 + t1) / 2 - lat_us / 1e6 - idx / rate
                        for idx, t0, t1, lat_us in lats])
        cost_us = np.array([(t1 - t0) * 1e6 for _i, t0, t1, _l in lats])
        med = float(np.median(est))
        row["stream latency"] = (med - true0) * 1e3
        row["lat_within_mad_ms"] = float(np.median(np.abs(est - med))) * 1e3
        row["lat_query_p95_us"] = float(np.percentile(cost_us, 95))
        row["lat_n"] = len(lats)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dirs", nargs="+", help="kept sync self-test dirs")
    ap.add_argument("--csv", help="write the per-recording rows here")
    args = ap.parse_args(argv)

    rows = []
    for run in args.run_dirs:
        result = json.load(open(os.path.join(run, "sync_selftest.json")))
        for label, mic in sorted(result.get("microphones", {}).items()):
            sidecar = os.path.join(run, f"selftest_{label}_timestamps.txt")
            if mic.get("median_ms") is None or not glob.glob(sidecar):
                print(f"skip {run} {label}: no offset or sidecar", file=sys.stderr)
                continue
            rows.append({"run": os.path.basename(os.path.normpath(run)),
                         "mic": label, **anchors_for(sidecar, mic["median_ms"])})
    if not rows:
        print("no usable recordings", file=sys.stderr)
        return 1

    cands = [c for c in ("STARTED", "fit intercept", "stream latency") if c in rows[0]]
    print(f"{'run':>10} {'mic':>4} {'offset':>7} " + " ".join(f"{c:>15}" for c in cands)
          + ("  lat MAD ms  query p95 us" if "stream latency" in cands else ""))
    for r in rows:
        line = f"{r['run']:>10} {r['mic']:>4} {r['offset_ms']:7.2f} " + " ".join(
            f"{r[c]:15.2f}" for c in cands)
        if "stream latency" in r:
            line += f"  {r['lat_within_mad_ms']:10.2f}  {r['lat_query_p95_us']:12.0f}"
        print(line)
    print("\ncandidate minus true sample 0 (ms) across recordings:")
    for c in cands:
        v = [r[c] for r in rows if c in r]
        sd = st.stdev(v) if len(v) > 1 else float("nan")
        print(f"  {c:15s} n {len(v):3d}  mean {st.mean(v):8.2f}  sd {sd:6.2f}  "
              f"range {min(v):8.2f} .. {max(v):8.2f}")
    if args.csv:
        keys = sorted({k for r in rows for k in r})
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
