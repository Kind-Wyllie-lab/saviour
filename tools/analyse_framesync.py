#!/usr/bin/env python3
"""
analyse_framesync.py — inter-camera frame-sync analysis for SAVIOUR recordings.

Usage:
    python3 tools/analyse_framesync.py /path/to/session_dir [/another ...]
    python3 tools/analyse_framesync.py /path/to/session_dir --fps 60 --no-plot

Finds all *_timestamps.csv files in each session directory, aligns frames
across cameras by nearest PTP timestamp, and reports inter-camera offset stats.

CSV output (always written):
  ./framesync_summary.csv             — one row per camera pair, appended each run
  {session_dir}/framesync_per_frame.csv  — per-frame offsets for the session
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _camera_tag(p: Path) -> str:
    """'{session}_{animal}_{module_id}_(segment...)_timestamps.csv' → '{animal}_{module_id}'"""
    stem = p.stem.replace("_timestamps", "")
    stem = re.sub(r'_\(\d+_\d{8}-\d{6}\).*$', '', stem)
    parts = stem.split("_")
    return "_".join(parts[-2:]) if len(parts) >= 2 else stem


def load_csvs(session_dir: Path) -> dict[str, pd.DataFrame]:
    csvs = sorted(session_dir.rglob("*_timestamps.csv"))
    if not csvs:
        sys.exit(f"No *_timestamps.csv files found under {session_dir}")

    cameras: dict[str, pd.DataFrame] = {}
    for p in csvs:
        tag = _camera_tag(p)
        df = pd.read_csv(p)
        df["timestamp_ns"] = pd.to_numeric(df["timestamp_ns"], errors="coerce")
        df = df.dropna(subset=["timestamp_ns"]).reset_index(drop=True)
        if tag in cameras:
            cameras[tag] = pd.concat([cameras[tag], df], ignore_index=True)
            print(f"  + segment  {p.name}  →  {tag}")
        else:
            cameras[tag] = df
            print(f"  Loaded     {p.name}  →  {tag}  ({len(df)} frames)")

    return cameras


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def align_frames(cameras: dict[str, pd.DataFrame]) -> tuple:
    """Nearest-neighbour match on PTP timestamp_ns.

    Returns (per_frame_df, ref_tag, [client_tags]).
    per_frame_df columns: frame_id, timestamp_s,
                          offset_us_{tag}, offset_s_{tag}  per client.
    """
    sorted_cams = sorted(cameras.items(), key=lambda kv: len(kv[1]), reverse=True)
    ref_tag, ref_df = sorted_cams[0]
    ref_ts = ref_df["timestamp_ns"].to_numpy()

    per_frame = pd.DataFrame({
        "frame_id":    ref_df["frame_id"].to_numpy(),
        "timestamp_s": ref_ts / 1e9,
    })

    for tag, df in sorted_cams[1:]:
        cam_ts = df["timestamp_ns"].to_numpy()
        idx = np.searchsorted(cam_ts, ref_ts)
        idx = np.clip(idx, 0, len(cam_ts) - 1)
        lo  = np.clip(idx - 1, 0, len(cam_ts) - 1)
        pick = np.where(
            np.abs(cam_ts[idx] - ref_ts) <= np.abs(cam_ts[lo] - ref_ts),
            idx, lo
        )
        delta_ns = cam_ts[pick] - ref_ts
        per_frame[f"offset_us_{tag}"] = delta_ns / 1e3
        per_frame[f"offset_s_{tag}"]  = delta_ns / 1e9

    client_tags = [t for t, _ in sorted_cams[1:]]
    return per_frame, ref_tag, client_tags


def _outlier_threshold_us(fps: float) -> float:
    """Offsets ≥ 90 % of a frame period are nearest-neighbour artefacts."""
    return 1e6 / fps * 0.9


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------

def report_dropped(cameras: dict[str, pd.DataFrame]) -> None:
    print("\n── Dropped frames ─────────────────────────────────────")
    for tag, df in cameras.items():
        total    = len(df)
        drops    = pd.to_numeric(df["dropped_before"], errors="coerce").fillna(0).astype(int)
        n_events = int((drops > 0).sum())
        n_lost   = int(drops.sum())
        pct      = 100.0 * n_events / total if total else 0
        print(f"  {tag:25s}  {total} frames  |  "
              f"{n_events} events ({pct:.1f}%)  |  {n_lost} frames lost")


def _verdict(detrended_p95_us: float, drift_us_per_sec: float,
             half_frame_us: float, fps: float, phase_offset_us: float) -> None:
    """Print a plain-language alignment verdict for a camera pair."""
    # Maximum safe session length before drift accumulates past one half-frame,
    # at which point nearest-neighbour timestamp matching may assign the wrong frame.
    if drift_us_per_sec and abs(drift_us_per_sec) > 0:
        max_safe_min = half_frame_us / abs(drift_us_per_sec) / 60.0
    else:
        max_safe_min = float("inf")

    # Rough behavioural scale for context: fastest observable rodent events
    # (whisker deflection, startle, lick) are ~5–50 ms; locomotion 50–500 ms.
    margin_x = (5000 / detrended_p95_us) if detrended_p95_us else 0

    print(f"\n── Alignment verdict ───────────────────────────────────")
    print(f"  ★ Timing accuracy  : {detrended_p95_us:.1f} µs  (p95, after timestamp alignment)")
    print(f"                       ~{margin_x:.0f}× better than fastest observable rodent events (~5 ms)")
    print(f"  Phase offset       : {phase_offset_us:+.0f} µs  — fixed per session, subtract to align by frame number")
    if max_safe_min < 9999:
        print(f"  Max safe duration  : {max_safe_min:.0f} min at {fps:.0f} fps  "
              f"(drift {abs(drift_us_per_sec):.2f} µs/sec → exceeds ½-frame at this point)")
    else:
        print(f"  Max safe duration  : no limit  (drift negligible)")
    print(f"  Action required    : match frames via per-frame CSV timestamps, not frame numbers")


def report_offsets(per_frame: pd.DataFrame, ref_tag: str, client_tags: list,
                   fps: float) -> list[dict]:
    """Print stats and return summary rows for CSV."""
    half_frame_us  = 1e6 / fps / 2.0
    outlier_thr_us = _outlier_threshold_us(fps)

    print(f"\n── Inter-camera offset  (ref: {ref_tag}) ───────────────")
    print(f"   Frame period: {1e6/fps:.0f} µs  |  half-frame: {half_frame_us:.0f} µs")

    rows = []
    for tag in client_tags:
        col = f"offset_us_{tag}"
        if col not in per_frame:
            continue
        v         = per_frame[col].dropna()
        real      = v[v.abs() < outlier_thr_us]
        artefacts = v[v.abs() >= outlier_thr_us]
        within    = (real.abs() <= half_frame_us).mean() * 100 if len(real) else 0

        # Linear drift fit: slope in µs/frame → µs/sec
        drift_us_per_sec = None
        detrended_p95_us = None
        if len(real) >= 10:
            x = np.arange(len(real), dtype=float)
            slope, intercept = np.polyfit(x, real.values, 1)
            drift_us_per_sec = slope * fps
            detrended = real.values - (slope * x + intercept)
            detrended_p95_us = float(np.percentile(np.abs(detrended), 95))

        print(f"\n  {tag}")
        if len(real):
            mean_us = real.mean()
            print(f"    phase offset     : {mean_us:+.1f} ± {real.std():.1f} µs"
                  f"  [fixed per session; random at session start due to hardware sync limits]")
            print(f"    within ½ frame   : {within:.1f}%  ({len(real)} frames)"
                  f"  [100% = nearest-neighbour matching always picks the correct frame]")
        if drift_us_per_sec is not None:
            if abs(drift_us_per_sec) > 0:
                safe_min = half_frame_us / abs(drift_us_per_sec) / 60.0
                safe_str = f"  [misassignment risk after {safe_min:.0f} min without timestamp alignment]"
            else:
                safe_str = ""
            print(f"    clock drift      : {drift_us_per_sec:+.3f} µs/sec ({drift_us_per_sec:.2f} ppm)"
                  + safe_str)
            print(f"  ★ timing accuracy  : {detrended_p95_us:.1f} µs p95"
                  f"  [residual uncertainty after phase+drift correction — report this number]")
        if len(artefacts):
            print(f"    matching artefacts (|offset| ≥ {outlier_thr_us/1000:.0f} ms): "
                  f"{len(artefacts)} — differing frame counts, not real sync error")

        if detrended_p95_us is not None and drift_us_per_sec is not None and len(real):
            _verdict(detrended_p95_us, drift_us_per_sec, half_frame_us, fps, real.mean())

        rows.append({
            "ref_camera":             ref_tag,
            "client_camera":          tag,
            "fps":                    fps,
            "n_frames":               len(real),
            "n_artefacts":            len(artefacts),
            "mean_offset_us":         round(real.mean(), 3)             if len(real) else None,
            "std_offset_us":          round(real.std(), 3)              if len(real) else None,
            "mean_abs_offset_us":     round(real.abs().mean(), 3)       if len(real) else None,
            "mean_abs_offset_s":      round(real.abs().mean() / 1e6, 9) if len(real) else None,
            "p50_offset_us":          round(real.quantile(.50), 3)      if len(real) else None,
            "p95_offset_us":          round(real.quantile(.95), 3)      if len(real) else None,
            "max_abs_offset_us":      round(real.abs().max(), 3)        if len(real) else None,
            "pct_within_half_frame":  round(within, 2)                  if len(real) else None,
            "drift_us_per_sec":       round(drift_us_per_sec, 4)        if drift_us_per_sec is not None else None,
            "detrended_p95_us":       round(detrended_p95_us, 1)        if detrended_p95_us is not None else None,
        })
    return rows


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_per_frame_csv(per_frame: pd.DataFrame, session_dir: Path,
                        client_tags: list) -> Path:
    cols = (["frame_id", "timestamp_s"]
            + [f"offset_s_{t}"  for t in client_tags if f"offset_s_{t}"  in per_frame]
            + [f"offset_us_{t}" for t in client_tags if f"offset_us_{t}" in per_frame])
    out = session_dir / "framesync_per_frame.csv"
    per_frame[cols].to_csv(out, index=False, float_format="%.9f")
    return out


def write_summary_csv(rows: list[dict], cwd: Path) -> Path:
    out = cwd / "framesync_summary.csv"
    df_new = pd.DataFrame(rows)
    if out.exists():
        df_existing = pd.read_csv(out)
        sessions_this_run = df_new["session"].unique()
        df_existing = df_existing[~df_existing["session"].isin(sessions_this_run)]
        df_out = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_out = df_new
    df_out.to_csv(out, index=False)
    return out


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot(per_frame: pd.DataFrame, ref_tag: str, client_tags: list,
         fps: float, session_dir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
    except ImportError:
        print("  [plot skipped — pip install matplotlib]")
        return

    half_frame_us  = 1e6 / fps / 2.0
    outlier_thr_us = _outlier_threshold_us(fps)
    n = len(client_tags)
    if n == 0:
        return

    fig = plt.figure(figsize=(14, 4 * n))
    gs  = gridspec.GridSpec(n, 2, figure=fig, hspace=0.45, wspace=0.35)
    fig.suptitle(f"Frame-sync — {session_dir.name}  (ref: {ref_tag}, {fps} fps)",
                 fontsize=11, y=1.01)

    t_s = per_frame["timestamp_s"] - per_frame["timestamp_s"].iloc[0]

    for i, tag in enumerate(client_tags):
        col = f"offset_us_{tag}"
        if col not in per_frame:
            continue
        v         = per_frame[col]
        real_mask = v.abs() < outlier_thr_us

        ax_ts = fig.add_subplot(gs[i, 0])
        ax_ts.plot(t_s, v.where(real_mask), lw=0.6, color="steelblue",
                   alpha=0.8, label="offset")
        ax_ts.scatter(t_s[~real_mask], v[~real_mask], s=8, color="tomato",
                      zorder=5, label="artefact")
        ax_ts.axhline(0, color="k", lw=0.8, ls="--")
        ax_ts.axhline(+half_frame_us, color="orange", lw=0.8, ls=":", label="±½ frame")
        ax_ts.axhline(-half_frame_us, color="orange", lw=0.8, ls=":")
        ax_ts.set_xlabel("Time (s)")
        ax_ts.set_ylabel("Offset (µs)")
        ax_ts.set_title(f"{tag} vs {ref_tag}")
        ax_ts.legend(fontsize=7)

        real = v[real_mask]
        ax_h = fig.add_subplot(gs[i, 1])
        if len(real) > 1:
            lo   = real.quantile(0.001)
            hi   = real.quantile(0.999)
            bins = np.linspace(lo, hi, min(60, max(10, len(real) // 5)))
            ax_h.hist(real, bins=bins, color="steelblue", edgecolor="white", lw=0.3)
        ax_h.axvline(0, color="k", lw=0.8, ls="--")
        ax_h.axvline(+half_frame_us, color="orange", lw=0.8, ls=":", label="±½ frame")
        ax_h.axvline(-half_frame_us, color="orange", lw=0.8, ls=":")
        ax_h.set_xlabel("Offset (µs)")
        ax_h.set_ylabel("Count")
        ax_h.set_title(f"{tag} — distribution (real jitter only)")
        ax_h.legend(fontsize=7)

    out = session_dir / "framesync_analysis.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Plot           → {out}")
    plt.show()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dirs", nargs="+",
                    help="Session directories containing *_timestamps.csv files")
    ap.add_argument("--fps", type=float, default=None,
                    help="Expected frame rate (auto-detected if omitted)")
    ap.add_argument("--no-plot", action="store_true", help="Skip matplotlib output")
    args = ap.parse_args()

    # Summary CSV goes next to the session dirs, not in the shell's cwd
    summary_dir = Path(args.session_dirs[0]).resolve().parent
    all_summary_rows: list[dict] = []

    for session_arg in args.session_dirs:
        session_dir = Path(session_arg).resolve()
        if not session_dir.is_dir():
            print(f"[skip] not a directory: {session_dir}", file=sys.stderr)
            continue

        print(f"\nSession: {session_dir.name}")
        cameras = load_csvs(session_dir)

        if args.fps:
            fps = args.fps
        else:
            ref_df       = max(cameras.values(), key=len)
            median_delta = pd.to_numeric(ref_df["delta_ms"], errors="coerce").median()
            fps          = round(1000.0 / median_delta) if pd.notna(median_delta) and median_delta > 0 else 30.0
            print(f"  Auto-detected FPS: {fps}")

        report_dropped(cameras)

        if len(cameras) < 2:
            print("  Only one camera found — skipping offset analysis.")
            continue

        per_frame, ref_tag, client_tags = align_frames(cameras)
        session_rows = report_offsets(per_frame, ref_tag, client_tags, fps)

        print("\n── CSV output ──────────────────────────────────────────")
        pf_out = write_per_frame_csv(per_frame, session_dir, client_tags)
        print(f"  Per-frame CSV  → {pf_out}")

        for r in session_rows:
            all_summary_rows.append({"session": session_dir.name, **r})

        if not args.no_plot:
            plot(per_frame, ref_tag, client_tags, fps, session_dir)

    if all_summary_rows:
        summary_out = write_summary_csv(all_summary_rows, summary_dir)
        print(f"\nSummary CSV    → {summary_out}")

    print()


if __name__ == "__main__":
    main()
