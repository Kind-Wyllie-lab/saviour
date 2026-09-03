# -*- coding: utf-8 -*-
"""
saviour_oe_pipeline.py
======================
Aligns SAVIOUR behavioural/video data with OpenEphys neural recordings
using a shared pseudorandom TTL signal.

Pipeline steps
--------------
1. Load TTL events from both systems
2. Validate and align clocks via linear regression on rising edges
3. Assess PTP sync quality across Raspberry Pis
4. Map video frame timestamps into OpenEphys time
5. Static ephys lookup at any video timestamp
6. Animated video + LFP viewer

Dependencies: numpy, pandas, matplotlib, scipy, opencv-python (cv2)
"""

import os
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import cv2
from scipy import stats


# =============================================================================
# SECTION 1 — CONFIGURATION
# Edit all paths and settings here. Nothing else should need changing.
# =============================================================================

# --- OpenEphys paths ---
OE_TTL_DIR      = r"ephys_data\saviour_test\2026-05-22_10-54-24\Record Node 102\experiment2\recording1\events\Acquisition_Board-100.acquisition_board\TTL"
OE_CONTINUOUS_DIR = r"ephys_data\saviour_test\2026-05-22_10-54-24\Record Node 102\experiment2\recording1\continuous\Acquisition_Board-100.acquisition_board"
OE_OEBIN_PATH   = r"ephys_data\saviour_test\2026-05-22_10-54-24\Record Node 102\experiment2\recording1\structure.oebin"

# --- SAVIOUR paths ---
SAVIOUR_TTL_CSV     = r"saviour_data\ephys_sync-111328\20260522\ttl_2a23\ephys_sync-111328_ttl_2a23_(0_20260522-101331).csv"
SAVIOUR_TTL_HEALTH  = r"saviour_data\ephys_sync-111328\20260522\ttl_2a23\ephys_sync-111328_ttl_2a23_health_metadata_(0_20260522-101331).csv"

# --- Camera definitions: add one dict per camera ---
CAMERAS = [
    {
        "name":        "Top Camera",
        "video":       r"saviour_data\ephys_sync-111328\20260522\Top Camera\ephys_sync-111328_Top Camera_6bcb_(0_20260522-101331).ts",
        "timestamps":  r"saviour_data\ephys_sync-111328\20260522\Top Camera\ephys_sync-111328_Top Camera_6bcb_(0_20260522-101331)_timestamps.csv",
        "health":      r"saviour_data\ephys_sync-111328\20260522\Top Camera\ephys_sync-111328_Top Camera_6bcb_health_metadata_(0_20260522-101331).csv",
    },
    # Add more cameras here following the same pattern:
    # {
    #     "name":       "Side Camera",
    #     "video":      r"path\to\video.ts",
    #     "timestamps": r"path\to\timestamps.csv",
    #     "health":     r"path\to\health.csv",
    # },
    {
     "name": "Side Camera",
     "video": r"saviour_data\ephys_sync-111328\20260522\Side Camera\ephys_sync-111328_Side Camera_671c_(0_20260522-101331).ts",
     "timestamps": r"saviour_data\ephys_sync-111328\20260522\Side Camera\ephys_sync-111328_Side Camera_671c_(0_20260522-101331)_timestamps.csv",
     "health": r"saviour_data\ephys_sync-111328\20260522\Side Camera\ephys_sync-111328_Side Camera_671c_health_metadata_(0_20260522-101331).csv",
     }
]

# --- TTL settings ---
SAVIOUR_TTL_PIN     = 19        # GPIO pin number carrying the sync signal
SAVIOUR_TTL_INVERT  = True      # True if SAVIOUR polarity is opposite to OE

# --- Viewer settings ---
VIEWER_CHANNEL_IDX  = 0         # Ephys channel index for the animated viewer (0 = CH1)
VIEWER_WINDOW_S     = 0.2       # Width of scrolling LFP window in seconds
VIEWER_SPEED        = 0.1       # Playback speed (1.0 = realtime, 0.5 = half)
VIEWER_CAMERA       = "Top Camera"  # Which camera to use in the viewer


# =============================================================================
# SECTION 2 — TTL LOADING
# =============================================================================

def load_oe_ttl(ttl_dir):
    """
    Load OpenEphys TTL events from a TTL folder.

    Returns
    -------
    timestamps : np.ndarray  — seconds from recording start
    states     : np.ndarray  — +1 (rising) / -1 (falling)
    """
    required = ['timestamps.npy', 'states.npy', 'sample_numbers.npy']
    for f in required:
        if not os.path.exists(os.path.join(ttl_dir, f)):
            raise FileNotFoundError(f"Missing OE TTL file: {f} in {ttl_dir}")

    timestamps     = np.load(os.path.join(ttl_dir, 'timestamps.npy'))
    states         = np.load(os.path.join(ttl_dir, 'states.npy'))
    sample_numbers = np.load(os.path.join(ttl_dir, 'sample_numbers.npy'))

    print("=== OpenEphys TTL ===")
    print(f"  Total events : {len(states)}")
    print(f"  Rising  (+1) : {np.sum(states ==  1)}")
    print(f"  Falling (-1) : {np.sum(states == -1)}")
    print(f"  Duration     : {timestamps[-1] - timestamps[0]:.3f} s")

    return timestamps, states, sample_numbers


def load_saviour_ttl(csv_path, pin_number, invert=True):
    """
    Load SAVIOUR TTL events for a specific GPIO pin.

    Parameters
    ----------
    csv_path   : str   — path to the SAVIOUR TTL CSV
    pin_number : int   — GPIO pin to extract (e.g. 19)
    invert     : bool  — flip polarity to match OpenEphys convention

    Returns
    -------
    timestamps_rel : np.ndarray  — seconds, zero-referenced to first event
    states         : np.ndarray  — +1 (rising) / -1 (falling)
    t0_unix_s      : float       — Unix time of first event in seconds
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"SAVIOUR TTL CSV not found: {csv_path}")

    df   = pd.read_csv(csv_path)
    df19 = df[df['pin_number'] == pin_number].copy()

    if len(df19) == 0:
        raise ValueError(f"No events found for pin {pin_number} in {csv_path}")

    df19['time_s']    = df19['Timestamp_nanoseconds'] / 1e9
    df19['state_raw'] = df19['pin_state'].map({'TTLValue.HIGH': 1, 'TTLValue.LOW': -1})
    df19['state']     = df19['state_raw'] * (-1 if invert else 1)

    t0_unix_s      = df19['time_s'].iloc[0]
    timestamps_rel = df19['time_s'].values - t0_unix_s
    states         = df19['state'].values

    print("\n=== SAVIOUR TTL ===")
    print(f"  Pin          : {pin_number}")
    print(f"  Total events : {len(states)}")
    print(f"  Rising  (+1) : {np.sum(states ==  1)}")
    print(f"  Falling (-1) : {np.sum(states == -1)}")
    print(f"  Duration     : {timestamps_rel[-1]:.3f} s")
    print(f"  t0 Unix      : {t0_unix_s:.3f} s")

    return timestamps_rel, states, t0_unix_s


def plot_ttl_comparison(oe_timestamps, oe_states, sv_timestamps, sv_states):
    """Plot OE and SAVIOUR TTL signals side by side for visual inspection."""
    fig, axes = plt.subplots(4, 1, figsize=(16, 10), sharex=False)
    fig.suptitle("TTL Sync Signal Comparison", fontsize=13, fontweight='bold')

    for ax, t, s, title, color in [
        (axes[0], oe_timestamps, oe_states, "OpenEphys TTL",                    'steelblue'),
        (axes[2], sv_timestamps, sv_states, "SAVIOUR TTL (polarity corrected)",  'darkorange'),
    ]:
        ax.plot(t, (s + 1) / 2, drawstyle='steps-post', color=color, linewidth=0.9)
        ax.set_ylabel("State (0/1)")
        ax.set_title(title)
        ax.set_ylim(-0.1, 1.1)

    for ax, t, s, xlabel in [
        (axes[1], oe_timestamps, oe_states, "Time from OE start (s)"),
        (axes[3], sv_timestamps, sv_states, "Time from SAVIOUR start (s)"),
    ]:
        rising  = t[s ==  1]
        falling = t[s == -1]
        ax.vlines(rising,  0, 1, color='green', linewidth=0.8, label='Rising')
        ax.vlines(falling, 0, 1, color='red',   linewidth=0.8, label='Falling')
        ax.set_ylabel("Events")
        ax.set_xlabel(xlabel)
        ax.legend(loc='upper right', fontsize=8)
        ax.set_ylim(0, 1.2)

    plt.tight_layout()
    plt.show()


# =============================================================================
# SECTION 3 — CLOCK ALIGNMENT
# =============================================================================

def align_clocks(oe_timestamps, oe_states, sv_timestamps, sv_states):
    """
    Fit a linear transform from SAVIOUR time to OpenEphys time using
    matched rising edge sequences.

    Returns
    -------
    scale  : float  — clock rate ratio (close to 1.0)
    offset : float  — time offset in seconds
    r2     : float  — goodness of fit (should be > 0.9999999)
    """
    oe_rising = oe_timestamps[oe_states == 1]
    sv_rising = sv_timestamps[sv_states == 1]

    print(f"\n=== Clock Alignment ===")
    print(f"  OE rising edges     : {len(oe_rising)}")
    print(f"  SAVIOUR rising edges: {len(sv_rising)}")

    if abs(len(oe_rising) - len(sv_rising)) > 5:
        print(f"  WARNING: large edge count mismatch ({abs(len(oe_rising) - len(sv_rising))} edges)")
        print(f"           Alignment may be unreliable. Check TTL signals carefully.")

    # Compare inter-pulse intervals as a sanity check
    n       = min(len(oe_rising), len(sv_rising)) - 1
    oe_ipi  = np.diff(oe_rising[:n+1])
    sv_ipi  = np.diff(sv_rising[:n+1])
    corr    = np.corrcoef(oe_ipi, sv_ipi)[0, 1]
    print(f"  IPI correlation     : {corr:.6f}  (should be ~1.0)")

    if corr < 0.999:
        print(f"  WARNING: low IPI correlation. Pulses may be mismatched.")

    # Linear regression: t_OE = scale * t_SAVIOUR + offset
    n_edges  = min(len(oe_rising), len(sv_rising))
    result   = stats.linregress(sv_rising[:n_edges], oe_rising[:n_edges])
    scale    = result.slope
    offset   = result.intercept
    r2       = result.rvalue ** 2
    drift_ms = abs(1.0 - scale) * sv_timestamps[-1] * 1000

    print(f"  Scale               : {scale:.10f}  (drift over recording: {drift_ms:.2f} ms)")
    print(f"  Offset              : {offset:.6f} s")
    print(f"  R²                  : {r2:.10f}")

    if r2 < 0.9999999:
        print(f"  WARNING: R² below threshold. Check residuals plot.")

    return scale, offset, r2


def plot_alignment_diagnostics(oe_timestamps, oe_states, sv_timestamps, sv_states, scale, offset):
    """Plot IPI comparison and residuals to validate the alignment fit."""
    oe_rising = oe_timestamps[oe_states == 1]
    sv_rising = sv_timestamps[sv_states == 1]
    n         = min(len(oe_rising), len(sv_rising))

    oe_ipi = np.diff(oe_rising[:n])
    sv_ipi = np.diff(sv_rising[:n])
    m      = min(len(oe_ipi), len(sv_ipi))

    # Residuals
    oe_predicted = scale * sv_rising[:n] + offset
    residuals    = oe_rising[:n] - oe_predicted

    fig, axes = plt.subplots(2, 2, figsize=(14, 6))
    fig.suptitle("Alignment Diagnostics", fontsize=12, fontweight='bold')

    axes[0, 0].plot(oe_ipi[:m], color='steelblue',  linewidth=0.8, label='OpenEphys')
    axes[0, 0].plot(sv_ipi[:m], color='darkorange',  linewidth=0.8, label='SAVIOUR', alpha=0.7)
    axes[0, 0].set_title("Inter-Pulse Intervals (should overlap)")
    axes[0, 0].set_ylabel("IPI (s)")
    axes[0, 0].legend(fontsize=8)

    axes[0, 1].scatter(oe_ipi[:m], sv_ipi[:m], s=4, alpha=0.5, color='purple')
    axes[0, 1].set_title(f"IPI correlation: r = {np.corrcoef(oe_ipi[:m], sv_ipi[:m])[0,1]:.6f}")
    axes[0, 1].set_xlabel("OE IPI (s)")
    axes[0, 1].set_ylabel("SAVIOUR IPI (s)")

    axes[1, 0].plot(sv_rising[:n], residuals * 1000, color='purple', linewidth=0.8)
    axes[1, 0].axhline(0, color='k', linewidth=0.5, linestyle='--')
    axes[1, 0].set_title("Residuals over time (flat = good)")
    axes[1, 0].set_ylabel("Residual (ms)")
    axes[1, 0].set_xlabel("SAVIOUR time (s)")

    axes[1, 1].hist(residuals * 1000, bins=50, color='purple', alpha=0.7)
    axes[1, 1].set_title("Residual distribution (narrow = good)")
    axes[1, 1].set_xlabel("Residual (ms)")
    axes[1, 1].set_ylabel("Count")

    plt.tight_layout()
    plt.show()


def make_transform(scale, offset, sv_t0_unix_s):
    """
    Return a function that converts SAVIOUR Unix nanosecond timestamps
    to OpenEphys seconds.
    """
    def saviour_to_oe(saviour_unix_ns):
        saviour_s_rel = (saviour_unix_ns / 1e9) - sv_t0_unix_s
        return scale * saviour_s_rel + offset
    return saviour_to_oe


# =============================================================================
# SECTION 4 — PTP HEALTH ASSESSMENT
# =============================================================================

def load_health(csv_path):
    """Load a SAVIOUR health metadata CSV, handling comma separation and encoding."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Health CSV not found: {csv_path}")
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    df.columns = df.columns.str.strip()
    df['timestamp'] = pd.to_numeric(df['timestamp'])
    df['total_ptp_offset_ns'] = df['ptp4l_offset'].abs() + df['phc2sys_offset'].abs()
    return df


def assess_ptp_health(health_dfs, rec_start_s, rec_dur_s=70):
    """
    Print PTP sync quality summary for each Pi during the recording window.

    Parameters
    ----------
    health_dfs  : list of (name, df) tuples
    rec_start_s : float  — recording start in Unix seconds (sv_t0)
    rec_dur_s   : float  — recording duration in seconds
    """
    rec_end_s = rec_start_s + rec_dur_s
    print("\n=== PTP Sync Health ===")
    for name, df in health_dfs:
        dfw = df[(df['timestamp'] >= rec_start_s) & (df['timestamp'] <= rec_end_s)]
        if len(dfw) == 0:
            print(f"  {name}: no health samples found in recording window")
            continue
        max_err = dfw['total_ptp_offset_ns'].max()
        mean_err = dfw['total_ptp_offset_ns'].mean()
        flag = "  OK" if max_err < 1e6 else "  WARNING: >1ms peak error"
        print(f"  {name} ({len(dfw)} samples):")
        print(f"    ptp4l    mean={dfw['ptp4l_offset'].mean():.0f} ns  max={dfw['ptp4l_offset'].abs().max():.0f} ns")
        print(f"    phc2sys  mean={dfw['phc2sys_offset'].mean():.0f} ns  max={dfw['phc2sys_offset'].abs().max():.0f} ns")
        print(f"    combined mean={mean_err:.0f} ns  max={max_err:.0f} ns  {flag}")


def plot_ptp_health(health_dfs, rec_start_s):
    """Plot PTP offset traces for all Pis during the recording."""
    n    = len(health_dfs)
    fig, axes = plt.subplots(2, n, figsize=(8 * n, 7), sharex='col')
    if n == 1:
        axes = axes.reshape(2, 1)
    fig.suptitle("PTP Sync Health During Recording", fontsize=13, fontweight='bold')

    colors = ['steelblue', 'darkorange', 'seagreen', 'crimson']
    for col, (name, df) in enumerate(health_dfs):
        color = colors[col % len(colors)]
        t     = df['timestamp'] - rec_start_s

        axes[0, col].plot(t, df['ptp4l_offset'],   color=color,   linewidth=0.9, label='ptp4l')
        axes[0, col].plot(t, df['phc2sys_offset'],  color=color,   linewidth=0.9,
                          linestyle='--', alpha=0.7, label='phc2sys')
        axes[0, col].axhline(0, color='k', linewidth=0.4, linestyle=':')
        axes[0, col].set_title(f"{name} — offsets")
        axes[0, col].set_ylabel("Offset (ns)")
        axes[0, col].legend(fontsize=8)

        axes[1, col].plot(t, df['total_ptp_offset_ns'], color='purple', linewidth=0.9)
        axes[1, col].axhline(1e6, color='red', linewidth=0.5, linestyle='--', label='1 ms')
        axes[1, col].set_title(f"{name} — total error")
        axes[1, col].set_ylabel("|ptp4l| + |phc2sys| (ns)")
        axes[1, col].set_xlabel("Time from recording start (s)")
        axes[1, col].legend(fontsize=8)

    plt.tight_layout()
    plt.show()


# =============================================================================
# SECTION 5 — VIDEO FRAME ALIGNMENT
# =============================================================================

def load_camera(camera_def, saviour_to_oe_fn):
    """
    Load video frame timestamps for one camera and map them to OE time.

    Parameters
    ----------
    camera_def       : dict  — one entry from CAMERAS config list
    saviour_to_oe_fn : callable  — transform from make_transform()

    Returns
    -------
    df : pd.DataFrame with added 'oe_time_s' column
    """
    path = camera_def['timestamps']
    if not os.path.exists(path):
        raise FileNotFoundError(f"Camera timestamp CSV not found: {path}")

    df = pd.read_csv(path)
    df['oe_time_s'] = saviour_to_oe_fn(df['timestamp_ns'].values)

    name        = camera_def['name']
    duration_s  = (df['timestamp_ns'].iloc[-1] - df['timestamp_ns'].iloc[0]) / 1e9
    mean_fps    = 1000.0 / df['delta_ms'].mean()
    n_dropped   = df['dropped_before'].sum()

    print(f"\n=== Camera: {name} ===")
    print(f"  Frames      : {len(df)}")
    print(f"  Duration    : {duration_s:.3f} s")
    print(f"  Mean FPS    : {mean_fps:.2f}")
    print(f"  OE range    : {df['oe_time_s'].iloc[0]:.3f}s → {df['oe_time_s'].iloc[-1]:.3f}s")
    if n_dropped > 0:
        print(f"  WARNING: {n_dropped} total dropped frames detected")
    else:
        print(f"  Dropped     : 0")

    return df


# =============================================================================
# SECTION 6 — CONTINUOUS EPHYS
# =============================================================================

def load_continuous(continuous_dir, oebin_path):
    """
    Load continuous LFP data from a .dat file using structure.oebin for metadata.

    Returns
    -------
    data_cont       : np.memmap  — shape (n_samples, n_channels), int16
    timestamps_cont : np.ndarray — OE time in seconds per sample
    meta            : dict       — channel names, bit_volts, units, sample_rate
    """
    if not os.path.exists(oebin_path):
        raise FileNotFoundError(f"structure.oebin not found: {oebin_path}")

    with open(oebin_path) as f:
        oebin = json.load(f)

    stream = next(
        (s for s in oebin['continuous'] if s['stream_name'] == 'acquisition_board'),
        None
    )
    if stream is None:
        raise ValueError("No 'acquisition_board' stream found in structure.oebin")

    meta = {
        'sample_rate':  stream['sample_rate'],
        'n_channels':   stream['num_channels'],
        'ch_names':     [ch['channel_name'] for ch in stream['channels']],
        'bit_volts':    np.array([ch['bit_volts'] for ch in stream['channels']], dtype=np.float32),
        'ch_units':     [ch['units'] for ch in stream['channels']],
        'ephys_idx':    [i for i, n in enumerate([ch['channel_name'] for ch in stream['channels']]) if n.startswith('CH')],
        'adc_idx':      [i for i, n in enumerate([ch['channel_name'] for ch in stream['channels']]) if n.startswith('ADC')],
    }

    dat_path        = os.path.join(continuous_dir, 'continuous.dat')
    timestamps_cont = np.load(os.path.join(continuous_dir, 'timestamps.npy'))
    n_samples       = len(timestamps_cont)

    expected_bytes = n_samples * meta['n_channels'] * 2
    actual_bytes   = os.path.getsize(dat_path)
    if expected_bytes != actual_bytes:
        raise ValueError(
            f"File size mismatch: expected {expected_bytes} bytes, got {actual_bytes}. "
            f"Check n_channels ({meta['n_channels']}) against oebin."
        )

    data_cont = np.memmap(dat_path, dtype='int16', mode='r',
                          shape=(n_samples, meta['n_channels']))

    print(f"\n=== Continuous Ephys ===")
    print(f"  Sample rate : {meta['sample_rate']:.0f} Hz")
    print(f"  Channels    : {meta['n_channels']}  ({len(meta['ephys_idx'])} ephys, {len(meta['adc_idx'])} ADC)")
    print(f"  Samples     : {n_samples}")
    print(f"  Duration    : {timestamps_cont[-1] - timestamps_cont[0]:.3f} s")

    return data_cont, timestamps_cont, meta


# =============================================================================
# SECTION 7 — STATIC EPHYS LOOKUP
# =============================================================================

def parse_video_time(video_time_str):
    """Parse 'MM:SS', 'HH:MM:SS', 'SS.s', or float into seconds."""
    if isinstance(video_time_str, (int, float)):
        return float(video_time_str)
    parts = str(video_time_str).strip().split(':')
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def plot_ephys_at_video_time(video_time_str, df_video, data_cont, timestamps_cont, meta,
                              window_s=1.0, channels='ephys'):
    """
    Plot LFP traces centred on a video timestamp.

    Parameters
    ----------
    video_time_str  : str or float  — e.g. '0:45', '1:23.5', or 45.0
    df_video        : pd.DataFrame  — camera frame table with 'oe_time_s' column
    data_cont       : np.memmap     — continuous dat data
    timestamps_cont : np.ndarray    — OE time per sample
    meta            : dict          — from load_continuous()
    window_s        : float         — seconds either side to plot
    channels        : 'ephys', 'adc', 'all', or list of int
    """
    video_s   = parse_video_time(video_time_str)
    frame_idx = int(np.clip(np.searchsorted(df_video['oe_time_s'].values, video_s),
                            0, len(df_video) - 1))
    frame     = df_video.iloc[frame_idx]
    oe_centre = frame['oe_time_s']

    print(f"\n{'='*52}")
    print(f"  Video time     : {video_time_str}")
    print(f"  Nearest frame  : {int(frame['frame_id'])}")
    print(f"  OE time        : {oe_centre:.4f} s")
    print(f"  Frame delta_ms : {frame['delta_ms']:.2f} ms")
    if frame['dropped_before'] > 0:
        print(f"  *** WARNING: {int(frame['dropped_before'])} dropped frames before this ***")
    print(f"{'='*52}")

    if channels == 'ephys':
        ch_idx = meta['ephys_idx']
    elif channels == 'adc':
        ch_idx = meta['adc_idx']
    elif channels == 'all':
        ch_idx = list(range(meta['n_channels']))
    else:
        ch_idx = list(channels)

    n_samples   = len(timestamps_cont)
    i_start     = int(np.clip(np.searchsorted(timestamps_cont, oe_centre - window_s), 0, n_samples - 1))
    i_end       = int(np.clip(np.searchsorted(timestamps_cont, oe_centre + window_s), 0, n_samples - 1))
    t_oe        = timestamps_cont[i_start:i_end]
    raw         = data_cont[i_start:i_end, :][:, ch_idx].astype(np.float32)
    signal_uv   = raw * meta['bit_volts'][ch_idx]

    print(f"  Extracted {i_end - i_start} samples × {len(ch_idx)} channels")

    fig, axes = plt.subplots(len(ch_idx), 1,
                             figsize=(14, max(2, 1.5 * len(ch_idx))),
                             sharex=True)
    if len(ch_idx) == 1:
        axes = [axes]

    fig.suptitle(
        f"Video {video_time_str}  →  OE {oe_centre:.3f} s  (±{window_s}s,  {meta['sample_rate']:.0f} Hz)",
        fontsize=11, fontweight='bold'
    )
    for i, ci in enumerate(ch_idx):
        axes[i].plot(t_oe, signal_uv[:, i], linewidth=0.6, color='steelblue')
        axes[i].axvline(oe_centre, color='red', linewidth=1.0, linestyle='--',
                        label='Video frame' if i == 0 else None)
        axes[i].set_ylabel(f"{meta['ch_names'][ci]}\n({meta['ch_units'][ci]})",
                           fontsize=7, rotation=0, labelpad=40, va='center')
        axes[i].tick_params(labelsize=7)

    axes[0].legend(fontsize=8, loc='upper right')
    axes[-1].set_xlabel("OpenEphys time (s)")
    plt.tight_layout()
    plt.show()

    return t_oe, signal_uv, oe_centre


# =============================================================================
# SECTION 8 — ANIMATED VIEWER
# =============================================================================

def run_viewer(camera_def, df_video, data_cont, timestamps_cont, meta,
               channel_idx=0, window_s=1.0, speed=1.0):
    """
    Play a camera video alongside a scrolling LFP trace in sync.

    Parameters
    ----------
    camera_def      : dict   — one entry from CAMERAS
    df_video        : pd.DataFrame
    data_cont       : np.memmap
    timestamps_cont : np.ndarray
    meta            : dict
    channel_idx     : int    — which channel to display (0-indexed)
    window_s        : float  — width of scrolling LFP window
    speed           : float  — playback speed multiplier
    """
    video_path = camera_def['video']
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap       = cv2.VideoCapture(video_path)
    fps       = cap.get(cv2.CAP_PROP_FPS)
    n_frames  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"\n=== Viewer: {camera_def['name']} ===")
    print(f"  Video    : {n_frames} frames @ {fps:.2f} fps = {n_frames/fps:.1f} s")
    print(f"  Channel  : {meta['ch_names'][channel_idx]}")
    print(f"  Window   : {window_s} s")

    # Pre-load single channel into RAM to avoid repeated memmap reads during animation
    ch_signal_uv = (data_cont[:, channel_idx].astype(np.float32)
                    * meta['bit_volts'][channel_idx])
    ch_name      = meta['ch_names'][channel_idx]
    n_samples    = len(timestamps_cont)

    # Get video dimensions from first frame
    ret, frame0  = cap.read()
    if not ret:
        raise RuntimeError("Could not read first video frame.")
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame0_rgb   = cv2.cvtColor(frame0, cv2.COLOR_BGR2RGB)
    vid_h, vid_w = frame0_rgb.shape[:2]
    print(f"  Dimensions: {vid_w} × {vid_h}  (aspect {vid_w/vid_h:.3f})")

    # --- Build figure ---
    fig = plt.figure(figsize=(12, 7))
    fig.patch.set_facecolor('black')

    ax_vid = fig.add_axes([0.0, 0.35, 1.0, 0.63])
    ax_vid.set_xlim(0, vid_w)
    ax_vid.set_ylim(vid_h, 0)
    ax_vid.set_aspect('equal')
    ax_vid.axis('off')
    ax_vid.set_facecolor('black')

    ax_eeg = fig.add_axes([0.08, 0.05, 0.90, 0.25])
    ax_eeg.set_facecolor('#0a0a0a')
    ax_eeg.tick_params(colors='white', labelsize=8)
    ax_eeg.spines[['top', 'right']].set_visible(False)
    for spine in ['bottom', 'left']:
        ax_eeg.spines[spine].set_color('#444444')
    ax_eeg.set_ylabel(f"{ch_name} (µV)", color='white', fontsize=9)
    ax_eeg.set_xlabel("OE time (s)", color='white', fontsize=9)
    ax_eeg.yaxis.label.set_color('white')

    im_display  = ax_vid.imshow(frame0_rgb, extent=[0, vid_w, vid_h, 0], aspect='equal')
    time_text   = ax_vid.set_title("", color='white', fontsize=10, pad=4)
    eeg_line,   = ax_eeg.plot([], [], color='#00ccff', linewidth=0.7, alpha=0.9)
    cursor_line  = ax_eeg.axvline(0, color='red', linewidth=1.2, linestyle='--', alpha=0.8)

    oe_video_start = df_video['oe_time_s'].iloc[0]

    def animate(frame_num):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret:
            return
        im_display.set_data(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        oe_now  = oe_video_start + frame_num / fps
        t_left  = oe_now - window_s * 0.3
        t_right = t_left + window_s

        i_left  = int(np.clip(np.searchsorted(timestamps_cont, t_left),  0, n_samples - 1))
        i_right = int(np.clip(np.searchsorted(timestamps_cont, t_right), 0, n_samples - 1))
        t_win   = timestamps_cont[i_left:i_right]
        sig_win = ch_signal_uv[i_left:i_right]

        eeg_line.set_data(t_win, sig_win)
        ax_eeg.set_xlim(t_left, t_right)
        if len(sig_win) > 1:
            pad = max(sig_win.std() * 3, 10)
            ax_eeg.set_ylim(sig_win.mean() - pad, sig_win.mean() + pad)
        cursor_line.set_xdata([oe_now, oe_now])

        vid_s  = frame_num / fps
        mm, ss = divmod(vid_s, 60)
        time_text.set_text(
            f"Video {int(mm):02d}:{ss:05.2f}   |   OE {oe_now:.3f} s   |   Frame {frame_num}"
        )
        fig.canvas.draw_idle()

    ani = animation.FuncAnimation(
        fig, animate,
        frames=range(0, n_frames, max(1, int(speed))),
        interval=(1000 / fps) / speed,
        blit=False,
        repeat=False
    )
    plt.show(block=True)
    cap.release()
    return ani


# =============================================================================
# SECTION 9 — HELPER FUNCTIONS
# Simple functions for doing typical researcher activities after establishing model.
# =============================================================================

def saviour_ns_to_oe_seconds(saviour_unix_ns, sv_t0, scale, offset):
    """
    Convert a SAVIOUR timestamp (Unix nanoseconds) to OpenEphys time (seconds
    from start of OE recording).

    Parameters
    ----------
    saviour_unix_ns : int or np.ndarray  — SAVIOUR timestamp(s) in Unix nanoseconds
    sv_t0           : float  — Unix time in seconds of the first SAVIOUR TTL event
    scale           : float  — regression slope  (t_OE = scale * sv_rel + offset)
    offset          : float  — regression intercept in seconds

    Returns
    -------
    float or np.ndarray — OE time in seconds
    """
    sv_rel = (saviour_unix_ns / 1e9) - sv_t0   # step 1: ns → relative seconds
    return scale * sv_rel + offset              # step 2: apply regression


def oe_seconds_to_saviour_ns(oe_time_s, sv_t0, scale, offset):
    """
    Convert an OpenEphys timestamp (seconds from recording start) back to
    SAVIOUR Unix nanoseconds.

    Parameters
    ----------
    oe_time_s : float or np.ndarray  — OE time in seconds
    sv_t0     : float  — Unix time in seconds of the first SAVIOUR TTL event
    scale     : float  — regression slope
    offset    : float  — regression intercept in seconds

    Returns
    -------
    int or np.ndarray — SAVIOUR Unix timestamp(s) in nanoseconds
    """
    sv_rel          = (oe_time_s - offset) / scale   # step 1: invert regression
    saviour_unix_s  = sv_rel + sv_t0                 # step 2: add back Unix epoch reference
    return (saviour_unix_s * 1e9).astype(np.int64)   # step 3: seconds → nanoseconds


def oe_seconds_to_sample_index(oe_time_s, timestamps_cont):
    """
    Convert OE time in seconds to the nearest sample index in data_cont.
    Uses searchsorted against the actual timestamps array rather than
    arithmetic, which is robust to any gaps or irregular sampling.

    Returns
    -------
    int — index into data_cont / timestamps_cont
    """
    idx = np.searchsorted(timestamps_cont, oe_time_s)
    return int(np.clip(idx, 0, len(timestamps_cont) - 1))


def sample_index_to_oe_seconds(sample_idx, timestamps_cont):
    """
    Convert a sample index back to OE time in seconds.
    """
    return float(timestamps_cont[np.clip(sample_idx, 0, len(timestamps_cont) - 1)])


# =============================================================================
# SECTION 10 — MAIN PIPELINE
# Run each step in order. Comment out any steps you don't need.
# =============================================================================

if __name__ == "__main__":

    # -- Step 1: Load TTL signals --
    oe_timestamps, oe_states, oe_samples = load_oe_ttl(OE_TTL_DIR)
    sv_timestamps, sv_states, sv_t0      = load_saviour_ttl(
        SAVIOUR_TTL_CSV, SAVIOUR_TTL_PIN, invert=SAVIOUR_TTL_INVERT
    )
    plot_ttl_comparison(oe_timestamps, oe_states, sv_timestamps, sv_states)

    # -- Step 2: Align clocks --
    scale, offset, r2 = align_clocks(oe_timestamps, oe_states, sv_timestamps, sv_states)
    plot_alignment_diagnostics(oe_timestamps, oe_states, sv_timestamps, sv_states, scale, offset)
    saviour_to_oe = make_transform(scale, offset, sv_t0)

    # -- Step 3: PTP health --
    health_ttl = load_health(SAVIOUR_TTL_HEALTH)
    health_dfs = [("TTL Pi", health_ttl)]
    for cam in CAMERAS:
        health_dfs.append((cam['name'], load_health(cam['health'])))
    assess_ptp_health(health_dfs, rec_start_s=sv_t0)
    plot_ptp_health(health_dfs, rec_start_s=sv_t0)

    # -- Step 4: Load continuous ephys --
    data_cont, timestamps_cont, ephys_meta = load_continuous(OE_CONTINUOUS_DIR, OE_OEBIN_PATH)

    # -- Step 5: Load and align all cameras --
    camera_data = {}
    for cam in CAMERAS:
        camera_data[cam['name']] = load_camera(cam, saviour_to_oe)

    # -- Step 6: Static lookup (edit time and camera as needed) --
    df_top = camera_data["Top Camera"]
    plot_ephys_at_video_time(
        '0:45', df_top, data_cont, timestamps_cont, ephys_meta,
        window_s=1.0, channels='ephys'
    )

    # -- Step 7: Animated viewer --
    top_cam_def = next(c for c in CAMERAS if c['name'] == VIEWER_CAMERA)
    run_viewer(
        top_cam_def, camera_data[VIEWER_CAMERA],
        data_cont, timestamps_cont, ephys_meta,
        channel_idx=VIEWER_CHANNEL_IDX,
        window_s=VIEWER_WINDOW_S,
        speed=VIEWER_SPEED
    )