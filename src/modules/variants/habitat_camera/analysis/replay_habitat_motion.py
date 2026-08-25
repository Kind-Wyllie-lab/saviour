#!/usr/bin/env python3
"""
replay_habitat_motion.py -- offline replay of habitat_camera's motion-trigger
logic against a pre-recorded video, so a threshold/config change (or the
AE-stability gate itself) can be validated against real footage before it
ever runs on a live rig. Nothing here touches a camera, a module, or the
network -- it's a pure decode-and-replay over an existing video file plus
its paired *_timestamps.csv sidecar (every SAVIOUR camera writes one
alongside every segment, habitat or not -- see camera_base.py).

Imports the real HabitatMotionDetector (the actual score algorithm) from
motion_detector.py, so scoring is byte-for-byte identical to what runs on
hardware -- that module holds only the scoring class (cv2/numpy only, no
picamera2), split out of habitat_camera_module.py specifically so this tool
can run on a plain dev machine without the picamera2 hardware dependency the
rest of that file drags in. The surrounding hysteresis state machine +
AE-stability gate are reimplemented here as ReplayTrigger, mirroring
HabitatCameraModule._process_main_frame/_update_ae_stability exactly (see
those methods' own docstrings for the reasoning) -- if that logic changes,
update this to match, since replay output is only trustworthy if it does.

Usage (paths below relative to the repo root):
    SCRIPT=src/modules/variants/habitat_camera/analysis/replay_habitat_motion.py
    python3 $SCRIPT VIDEO.ts [--timestamps CSV]
        [--config habitat_camera_config.json] [--no-ae-gate]
        [--start-frame N] [--max-frames N] [--diagnostic-csv OUT.csv]

    # Compare the AE-gate fix against pre-fix behaviour on the same footage:
    python3 $SCRIPT video.ts
    python3 $SCRIPT video.ts --no-ae-gate

Exit status is always 0 -- this is an analysis tool, not a check.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # .../src
from modules.variants.habitat_camera.motion_detector import HabitatMotionDetector  # noqa: E402

_DEFAULTS = {
    "algorithm": "frame_diff",
    "activity_threshold": 0.02,
    "activity_min_duration_s": 1.0,
    "inactivity_min_duration_s": 300.0,
    "pre_roll_secs": 3.0,
    "ae_settle_s": 0.75,
    "process_width": 256,
    "_mog2_history": 500,
    "_mog2_var_threshold": 16,
}


def load_habitat_motion_config(config_path: str | None) -> dict:
    cfg = dict(_DEFAULTS)
    if config_path:
        with open(config_path) as f:
            full = json.load(f)
        cfg.update(full.get("habitat_motion", {}))
    return cfg


class ReplayTrigger:
    """Pure reimplementation of HabitatCameraModule's hysteresis state
    machine + AE-stability gate -- see this file's module docstring."""

    def __init__(self, activity_threshold, activity_min_duration_s,
                 inactivity_min_duration_s, ae_settle_s, ae_gate_enabled):
        self.activity_threshold = activity_threshold
        self.activity_min_duration_s = activity_min_duration_s
        self.inactivity_min_duration_s = inactivity_min_duration_s
        self.ae_settle_s = ae_settle_s
        self.ae_gate_enabled = ae_gate_enabled

        self.state = "idle"
        self.since_ns = None
        self.last_above = None
        self.last_exposure = None
        self.last_gain = None
        self.ae_unstable_until_ns = None
        self.clip_open = False

    def _ae_stable(self, exposure, gain, timestamp_ns) -> bool:
        if not self.ae_gate_enabled:
            return True
        changed = (
            self.last_exposure is not None
            and (exposure != self.last_exposure or gain != self.last_gain)
        )
        self.last_exposure = exposure
        self.last_gain = gain
        if changed:
            self.ae_unstable_until_ns = timestamp_ns + int(self.ae_settle_s * 1e9)
        return self.ae_unstable_until_ns is None or timestamp_ns >= self.ae_unstable_until_ns

    def process_frame(self, score: float, exposure, gain, timestamp_ns: int) -> dict:
        ae_stable = self._ae_stable(exposure, gain, timestamp_ns)
        above = score >= self.activity_threshold and ae_stable

        if self.last_above is None or above != self.last_above:
            self.since_ns = timestamp_ns
            self.last_above = above
        elapsed_s = (
            (timestamp_ns - self.since_ns) / 1e9 if self.since_ns is not None else 0.0
        )

        opened = closed = False
        if self.state != "active":
            if above and elapsed_s >= self.activity_min_duration_s:
                self.state = "active"
                if not self.clip_open:
                    self.clip_open, opened = True, True
            else:
                self.state = "waiting" if above else "idle"
        elif not above and elapsed_s >= self.inactivity_min_duration_s:
            self.state = "idle"
            if self.clip_open:
                self.clip_open, closed = False, True

        return {
            "state": self.state, "clip_open": self.clip_open,
            "opened": opened, "closed": closed,
            "score": score, "ae_stable": ae_stable,
        }


def _load_frame_metadata(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _fmt_ts(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M:%S")


def _fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return (f"{h}h{m:02d}m{s:02d}s" if h else
            f"{m}m{s:02d}s" if m else f"{s}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--timestamps", help="paired _timestamps.csv (default: derived from video path)")
    ap.add_argument("--config", help="habitat_camera_config.json to read habitat_motion.* from")
    ap.add_argument("--no-ae-gate", action="store_true", help="disable the AE-stability gate (pre-fix behaviour)")
    ap.add_argument("--start-frame", type=int, default=0)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--diagnostic-csv", help="write a per-frame score/state/ae_stable CSV here")
    ap.add_argument("--compare-labels", help="ground-truth labels CSV from label_activity.py -- "
                                              "reports how many labeled segments this config's "
                                              "threshold/duration settings would have caught")
    ap.add_argument("--crop-top-frac", type=float, default=0.0,
                     help="crop this fraction off the top of each frame before scoring -- "
                          "the recorded .ts has the timestamp overlay burned in "
                          "(camera_base.py's _apply_timestamp runs AFTER _process_main_frame "
                          "on hardware, so live scoring never sees it -- this flag makes replay "
                          "match that by cropping out the burned-in text before this tool "
                          "scores it). 0.08 comfortably covers the default text_size overlay.")
    args = ap.parse_args()

    video_path = Path(args.video)
    ts_path = Path(args.timestamps) if args.timestamps else (
        video_path.parent / f"{video_path.stem}_timestamps.csv"
    )
    if not ts_path.exists():
        sys.exit(f"No timestamps CSV found at {ts_path} -- pass --timestamps explicitly")

    cfg = load_habitat_motion_config(args.config)
    detector = HabitatMotionDetector(
        algorithm=cfg["algorithm"], process_width=int(cfg["process_width"]),
        mog2_history=int(cfg["_mog2_history"]), mog2_var_threshold=float(cfg["_mog2_var_threshold"]),
    )
    trigger = ReplayTrigger(
        activity_threshold=float(cfg["activity_threshold"]),
        activity_min_duration_s=float(cfg["activity_min_duration_s"]),
        inactivity_min_duration_s=float(cfg["inactivity_min_duration_s"]),
        ae_settle_s=float(cfg["ae_settle_s"]),
        ae_gate_enabled=not args.no_ae_gate,
    )

    rows = _load_frame_metadata(ts_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"Could not open video: {video_path}")

    if args.start_frame:
        # CAP_PROP_POS_FRAMES is not reliable for this .ts container -- measured
        # live (see label_activity.py's get_frame docstring) landing ~17% short
        # of the requested frame, consistent with the ffmpeg backend assuming
        # 30fps for footage that's actually 25fps. Seek by the target frame's
        # own real timestamp (CAP_PROP_POS_MSEC) instead -- accurate to
        # ~0.1-0.3s in the same test, independent of any fps assumption.
        t0 = datetime.fromisoformat(rows[0]["timestamp_utc"])
        target_dt = datetime.fromisoformat(rows[args.start_frame]["timestamp_utc"])
        cap.set(cv2.CAP_PROP_POS_MSEC, (target_dt - t0).total_seconds() * 1000)

    diag_writer = None
    diag_file = None
    if args.diagnostic_csv:
        diag_file = open(args.diagnostic_csv, "w", newline="")
        diag_writer = csv.writer(diag_file)
        diag_writer.writerow(["timestamp_utc", "score", "state", "clip_open", "ae_stable"])

    segments = []  # list of (open_ts, close_ts_or_None)
    current_open_ts = None
    n_processed = 0
    frame_idx = args.start_frame

    print(f"Replaying {video_path.name} against {ts_path.name}", file=sys.stderr)
    print(f"algorithm={cfg['algorithm']} threshold={cfg['activity_threshold']} "
          f"activity_min={cfg['activity_min_duration_s']}s "
          f"inactivity_min={cfg['inactivity_min_duration_s']}s "
          f"ae_gate={'ON' if trigger.ae_gate_enabled else 'OFF'} "
          f"ae_settle={cfg['ae_settle_s']}s", file=sys.stderr)

    while True:
        if args.max_frames is not None and n_processed >= args.max_frames:
            break
        if frame_idx >= len(rows):
            break
        ok, frame = cap.read()
        if not ok:
            break
        row = rows[frame_idx]

        if args.crop_top_frac > 0:
            crop_rows = int(frame.shape[0] * args.crop_top_frac)
            frame = frame[crop_rows:, :]

        score = detector.score(frame)
        timestamp_ns = int(row["timestamp_ns"])
        exposure = row.get("exposure_time_us", "")
        gain = row.get("analogue_gain", "")

        result = trigger.process_frame(score, exposure, gain, timestamp_ns)

        if diag_writer:
            diag_writer.writerow([
                row["timestamp_utc"], f"{score:.4f}", result["state"],
                result["clip_open"], result["ae_stable"],
            ])

        if result["opened"]:
            current_open_ts = row["timestamp_utc"]
        if result["closed"] and current_open_ts is not None:
            segments.append((current_open_ts, row["timestamp_utc"]))
            current_open_ts = None

        frame_idx += 1
        n_processed += 1
        if n_processed % 5000 == 0:
            print(f"  ...{n_processed} frames ({_fmt_ts(row['timestamp_utc'])})", file=sys.stderr)

    if current_open_ts is not None:
        segments.append((current_open_ts, rows[min(frame_idx, len(rows) - 1)]["timestamp_utc"]))

    cap.release()
    if diag_file:
        diag_file.close()

    total_span_s = (
        datetime.fromisoformat(rows[min(frame_idx, len(rows) - 1)]["timestamp_utc"])
        - datetime.fromisoformat(rows[args.start_frame]["timestamp_utc"])
    ).total_seconds()
    kept_s = sum(
        (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
        for a, b in segments
    )

    print(file=sys.stderr)
    print(f"=== {len(segments)} clip(s) would have been kept "
          f"({_fmt_dur(kept_s)} of {_fmt_dur(total_span_s)}, "
          f"{100*kept_s/total_span_s:.1f}%) ===")
    for i, (a, b) in enumerate(segments, 1):
        dur = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
        print(f"  clip{i:>3}  {_fmt_ts(a)} -> {_fmt_ts(b)}  ({_fmt_dur(dur)})")

    if args.compare_labels:
        _print_label_comparison(args.compare_labels, segments)


def _load_labels(path: str) -> list[tuple[datetime, datetime]]:
    with open(path, newline="") as f:
        return [
            (datetime.fromisoformat(row["start_utc"]),
             datetime.fromisoformat(row["end_utc"]))
            for row in csv.DictReader(f)
        ]


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _print_label_comparison(labels_path: str, segments: list[tuple[str, str]]) -> None:
    labels = _load_labels(labels_path)
    seg_dt = [
        (datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in segments
    ]

    caught = [
        (ls, le) for ls, le in labels
        if any(_overlaps(ls, le, ds, de) for ds, de in seg_dt)
    ]
    missed = [(ls, le) for ls, le in labels if (ls, le) not in caught]
    unmatched_clips = [
        (ds, de) for ds, de in seg_dt
        if not any(_overlaps(ls, le, ds, de) for ls, le in labels)
    ]

    print()
    print(f"=== Compared against {len(labels)} labeled ground-truth segment(s) "
          f"({Path(labels_path).name}) ===")
    print(f"  caught (overlapped by a detected clip):  {len(caught)}/{len(labels)}")
    print(f"  missed (no detected clip overlapped):    {len(missed)}/{len(labels)}")
    print(f"  detected clip(s) with no labeled overlap: "
          f"{len(unmatched_clips)}/{len(seg_dt)}")
    if missed:
        print("  missed labeled segment(s):")
        for ls, le in missed:
            print(f"    {_fmt_ts(ls.isoformat())} -> {_fmt_ts(le.isoformat())}")
    if unmatched_clips:
        print("  unmatched detected clip(s):")
        for ds, de in unmatched_clips:
            print(f"    {_fmt_ts(ds.isoformat())} -> {_fmt_ts(de.isoformat())}")


if __name__ == "__main__":
    main()
