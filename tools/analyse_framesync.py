#!/usr/bin/env python3
"""
analyse_framesync.py — inter-camera frame-sync analysis for SAVIOUR recordings.

Usage:
    python3 tools/analyse_framesync.py /path/to/session_dir
    python3 tools/analyse_framesync.py /path/to/session_dir --fps 60 --no-plot

The script finds all *_timestamps.csv files in the session directory tree,
aligns frames across cameras by nearest PTP timestamp, and reports:
  • Per-camera dropped-frame counts
  • Inter-camera offset statistics (mean, std, p95, max) in µs
  • Optional matplotlib plots
"""

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _camera_tag(p: Path) -> str:
    """Extract a short human-readable tag from a timestamps CSV path.

    Filename format:
        {session}_{animal_id}_{module_short_id}_(segment_...)_timestamps.csv

    We want just "{animal_id}_{module_short_id}".
    """
    stem = p.stem.replace("_timestamps", "")
    # Drop the trailing segment marker (0_YYYYMMDD-HHMMSS) and anything after
    stem = re.sub(r'_\(\d+_\d{8}-\d{6}\).*$', '', stem)
    parts = stem.split("_")
    # Grab the last two underscore-components as animal_id + module_short_id
    if len(parts) >= 2:
        return "_".join(parts[-2:])
    return stem


def load_csvs(session_dir: Path) -> dict[str, pd.DataFrame]:
    """Return {camera_tag: DataFrame} for every *_timestamps.csv found."""
    csvs = sorted(session_dir.rglob("*_timestamps.csv"))
    if not csvs:
        sys.exit(f"No *_timestamps.csv files found under {session_dir}")

    cameras: dict[str, pd.DataFrame] = {}
    for p in csvs:
        tag = _camera_tag(p)
        # Deduplicate tags (multiple segments from same camera)
        if tag in cameras:
            existing = cameras[tag]
            extra = pd.read_csv(p, dtype={"sync_lag_us": "object"})
            cameras[tag] = pd.concat([existing, extra], ignore_index=True)
            print(f"  + segment {p.name}  →  {tag}")
            continue

        df = pd.read_csv(p, dtype={"sync_lag_us": "object"})
        df["timestamp_ns"] = pd.to_numeric(df["timestamp_ns"], errors="coerce")
        df = df.dropna(subset=["timestamp_ns"]).reset_index(drop=True)
        df["sync_lag_us"] = pd.to_numeric(df["sync_lag_us"], errors="coerce")
        cameras[tag] = df
        print(f"  Loaded {p.name}  →  {tag}  ({len(df)} frames)")

    return cameras


def align_frames(cameras: dict[str, pd.DataFrame]) -> tuple:
    """Merge cameras by nearest-neighbour timestamp matching.

    Returns (merged_df, ref_tag, [client_tags]).
    """
    sorted_cams = sorted(cameras.items(), key=lambda kv: len(kv[1]), reverse=True)
    ref_tag, ref_df = sorted_cams[0]

    merged = ref_df[["frame_id", "timestamp_ns", "delta_ms",
                      "dropped_before", "sync_lag_us"]].copy()
    merged = merged.rename(columns={
        "frame_id":       f"frame_id_{ref_tag}",
        "timestamp_ns":   f"ts_ns_{ref_tag}",
        "delta_ms":       f"delta_ms_{ref_tag}",
        "dropped_before": f"dropped_{ref_tag}",
        "sync_lag_us":    f"synclag_us_{ref_tag}",
    })

    ref_ts = ref_df["timestamp_ns"].to_numpy()

    for tag, df in sorted_cams[1:]:
        cam_ts = df["timestamp_ns"].to_numpy()
        indices = np.searchsorted(cam_ts, ref_ts)
        indices = np.clip(indices, 0, len(cam_ts) - 1)
        lo = np.clip(indices - 1, 0, len(cam_ts) - 1)
        pick = np.where(
            np.abs(cam_ts[indices] - ref_ts) <= np.abs(cam_ts[lo] - ref_ts),
            indices, lo
        )
        matched_ts = cam_ts[pick]
        offset_us  = (matched_ts - ref_ts) / 1000.0

        merged[f"ts_ns_{tag}"]      = matched_ts
        merged[f"offset_us_{tag}"]  = offset_us
        merged[f"dropped_{tag}"]    = df["dropped_before"].to_numpy()[pick]

    client_tags = [t for t, _ in sorted_cams[1:]]
    return merged, ref_tag, client_tags


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def report_dropped(cameras: dict[str, pd.DataFrame]) -> None:
    print("\n── Dropped frames ─────────────────────────────────────")
    for tag, df in cameras.items():
        total = len(df)
        drops = pd.to_numeric(df["dropped_before"], errors="coerce").fillna(0).astype(int)
        n_events = (drops > 0).sum()
        n_total  = drops.sum()
        pct = 100.0 * n_events / total if total else 0
        print(f"  {tag:25s}  {total} frames  |  {n_events} events ({pct:.1f}%)  "
              f"|  {n_total} frames lost")


def _outlier_threshold_us(fps: float) -> float:
    """Offsets ≥ one full frame period are matching artefacts, not real jitter."""
    return 1e6 / fps * 0.9   # 90 % of frame period


def report_offsets(merged: pd.DataFrame, ref_tag: str, client_tags: list[str],
                   fps: float) -> None:
    half_frame_us  = 1e6 / fps / 2.0
    outlier_thr_us = _outlier_threshold_us(fps)

    print(f"\n── Inter-camera offset  (ref: {ref_tag}) ───────────────")
    print(f"   Frame period: {1e6/fps:.0f} µs  |  half-frame: {half_frame_us:.0f} µs")

    for tag in client_tags:
        col = f"offset_us_{tag}"
        if col not in merged:
            continue
        v = merged[col].dropna()

        # Separate genuine sync jitter from frame-count mismatch artefacts
        real    = v[v.abs() < outlier_thr_us]
        artefacts = v[v.abs() >= outlier_thr_us]

        within = (real.abs() <= half_frame_us).mean() * 100 if len(real) else 0

        print(f"\n  {tag}")
        if len(real):
            print(f"    mean ± std : {real.mean():+.1f} ± {real.std():.1f} µs")
            print(f"    p50 / p95  : {real.quantile(.5):+.1f} / {real.quantile(.95):+.1f} µs")
            print(f"    min / max  : {real.min():+.1f} / {real.max():+.1f} µs")
            print(f"    within ½ frame period: {within:.1f}%  ({len(real)} frames)")
        if len(artefacts):
            print(f"    matching artefacts (|offset| ≥ {outlier_thr_us/1000:.0f} ms): "
                  f"{len(artefacts)} frames  — caused by differing frame counts, "
                  f"not real sync error")


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot(merged: pd.DataFrame, ref_tag: str, client_tags: list[str],
         fps: float, session_dir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
    except ImportError:
        print("\n[plot skipped — pip install matplotlib]")
        return

    half_frame_us  = 1e6 / fps / 2.0
    outlier_thr_us = _outlier_threshold_us(fps)
    n_clients = len(client_tags)
    if n_clients == 0:
        return

    fig = plt.figure(figsize=(14, 4 * n_clients))
    gs  = gridspec.GridSpec(n_clients, 2, figure=fig, hspace=0.45, wspace=0.35)
    fig.suptitle(
        f"Frame-sync analysis — {session_dir.name}  (ref: {ref_tag}, {fps} fps)",
        fontsize=11, y=1.01
    )

    ref_time_s = (
        merged[f"ts_ns_{ref_tag}"] - merged[f"ts_ns_{ref_tag}"].iloc[0]
    ) / 1e9

    for i, tag in enumerate(client_tags):
        col = f"offset_us_{tag}"
        if col not in merged:
            continue
        v = merged[col]
        real = v[v.abs() < outlier_thr_us]

        # Time series — mark artefacts separately
        ax_ts = fig.add_subplot(gs[i, 0])
        ax_ts.plot(ref_time_s, v.where(v.abs() < outlier_thr_us),
                   lw=0.6, color="steelblue", alpha=0.8, label="offset")
        ax_ts.scatter(
            ref_time_s[v.abs() >= outlier_thr_us],
            v[v.abs() >= outlier_thr_us],
            s=8, color="tomato", zorder=5, label="matching artefact"
        )
        ax_ts.axhline(0, color="k", lw=0.8, ls="--")
        ax_ts.axhline(+half_frame_us, color="orange", lw=0.8, ls=":", label="±½ frame")
        ax_ts.axhline(-half_frame_us, color="orange", lw=0.8, ls=":")
        ax_ts.set_xlabel("Time (s)")
        ax_ts.set_ylabel("Offset (µs)")
        ax_ts.set_title(f"{tag} vs {ref_tag} — time series")
        ax_ts.legend(fontsize=7)

        # Histogram — real jitter only
        ax_h = fig.add_subplot(gs[i, 1])
        if len(real):
            lo, hi = real.quantile(0.001), real.quantile(0.999)
            bins = np.linspace(lo, hi, min(60, max(10, len(real) // 5)))
            ax_h.hist(real, bins=bins, color="steelblue", edgecolor="white", lw=0.3)
        ax_h.axvline(0, color="k", lw=0.8, ls="--")
        ax_h.axvline(+half_frame_us, color="orange", lw=0.8, ls=":", label="±½ frame")
        ax_h.axvline(-half_frame_us, color="orange", lw=0.8, ls=":")
        ax_h.set_xlabel("Offset (µs)")
        ax_h.set_ylabel("Count")
        ax_h.set_title(f"{tag} — offset distribution (real jitter only)")
        ax_h.legend(fontsize=7)

    out = session_dir / "framesync_analysis.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\nPlot saved → {out}")
    plt.show()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("session_dir",
                    help="Path to session directory containing *_timestamps.csv files")
    ap.add_argument("--fps", type=float, default=None,
                    help="Expected frame rate (auto-detected from delta_ms if omitted)")
    ap.add_argument("--no-plot", action="store_true", help="Skip matplotlib output")
    args = ap.parse_args()

    session_dir = Path(args.session_dir).resolve()
    if not session_dir.is_dir():
        sys.exit(f"Not a directory: {session_dir}")

    print(f"\nSession: {session_dir}")
    cameras = load_csvs(session_dir)

    # Auto-detect FPS from median inter-frame interval of the largest camera
    if args.fps:
        fps = args.fps
    else:
        ref_df = max(cameras.values(), key=len)
        median_delta = pd.to_numeric(ref_df["delta_ms"], errors="coerce").median()
        fps = round(1000.0 / median_delta) if pd.notna(median_delta) and median_delta > 0 else 30.0
        print(f"  Auto-detected FPS: {fps}")

    if len(cameras) < 2:
        print("\nOnly one camera found — reporting single-camera stats only.")
        report_dropped(cameras)
        return

    merged, ref_tag, client_tags = align_frames(cameras)

    report_dropped(cameras)
    report_offsets(merged, ref_tag, client_tags, fps)

    if not args.no_plot:
        plot(merged, ref_tag, client_tags, fps, session_dir)

    print()


if __name__ == "__main__":
    main()
