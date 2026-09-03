# -*- coding: utf-8 -*-
"""
tool3_export_video.py
=====================
SAVIOUR ↔ OpenEphys  |  Synchronised Video Exporter

Exports an .mp4 combining:
  - Up to 5 SAVIOUR camera streams (tiled horizontally in top panel)
  - LFP traces for selected channels (bottom panel)

Two LFP display modes:
  - Epoch     : fixed window centred on current frame, cursor at 0 (default)
  - Scrolling : cursor at 30% from left, more history visible

Auto-discovers OpenEphys and SAVIOUR data folders from the script directory.

Dependencies: numpy, pandas, matplotlib, opencv-python, tkinter (built-in)
"""

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import traceback
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_agg import FigureCanvasAgg
import cv2
import os

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
MAX_CAMERAS  = 5

BG      = "#0f1117"
PANEL   = "#1a1d27"
PANEL2  = "#13161f"
BORDER  = "#2a2d3a"
ACCENT  = "#4fc3f7"
ACCENT2 = "#81c784"
WARN    = "#ef9a9a"
TEXT    = "#e8eaf6"
MUTED   = "#7986cb"

# Distinct colours for each camera stream overlay
CAM_COLORS = ["#4fc3f7", "#81c784", "#ffb74d", "#ce93d8", "#ef9a9a"]


# =============================================================================
# AUTO-DISCOVERY
# =============================================================================

def find_continuous_dir(oe_root):
    for dirpath, _, filenames in os.walk(oe_root):
        if 'continuous.dat' in filenames and 'timestamps.npy' in filenames:
            return dirpath
    raise FileNotFoundError(
        f"No continuous folder (continuous.dat + timestamps.npy) "
        f"found under:\n{oe_root}")


def find_oebin(continuous_dir):
    path = continuous_dir
    for _ in range(8):
        candidate = os.path.join(path, 'structure.oebin')
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    raise FileNotFoundError(
        f"structure.oebin not found above {continuous_dir}")


def find_video_pairs(sv_root):
    """
    Walk sv_root, return list of dicts:
        { name, video_path, timestamps_csv }
    Camera name is taken from the containing folder name
    (e.g. .../Top Camera/file.ts → name = 'Top Camera').
    Falls back to the file stem if the folder name is ambiguous.
    """
    pairs = []
    for dirpath, _, filenames in os.walk(sv_root):
        ts_files  = [f for f in filenames if f.endswith('.ts')]
        csv_files = set(filenames)
        for ts in ts_files:
            stem  = ts[:-3]
            match = next(
                (f for f in csv_files
                 if f.startswith(stem) and f.endswith('_timestamps.csv')),
                None)
            if match:
                # Use the folder name as the camera name — it's human-readable
                # e.g. ".../Top Camera/" → "Top Camera"
                folder_name = os.path.basename(dirpath)
                pairs.append({
                    'name':           folder_name,
                    'video_path':     os.path.join(dirpath, ts),
                    'timestamps_csv': os.path.join(dirpath, match),
                })
    return pairs


def auto_detect_roots(search_dir):
    oe_root = None
    sv_root = None
    try:
        candidates = [search_dir] + [
            os.path.join(search_dir, d)
            for d in os.listdir(search_dir)
            if os.path.isdir(os.path.join(search_dir, d))
        ]
    except Exception:
        return None, None
    for c in candidates:
        if oe_root is None:
            try:
                find_continuous_dir(c)
                oe_root = c
            except FileNotFoundError:
                pass
        if sv_root is None:
            if find_video_pairs(c):
                sv_root = c
        if oe_root and sv_root:
            break
    return oe_root, sv_root


# =============================================================================
# CONVERSION + DATA LOADING
# =============================================================================

def saviour_ns_to_oe_seconds(saviour_unix_ns, sv_t0, scale, offset):
    sv_rel = (np.asarray(saviour_unix_ns, dtype=np.float64) / 1e9) - sv_t0
    return scale * sv_rel + offset


def load_model(path):
    with open(path) as f:
        return json.load(f)


def load_continuous(continuous_dir, oebin_path):
    with open(oebin_path) as f:
        oebin = json.load(f)
    stream = next(
        s for s in oebin['continuous']
        if s['stream_name'] == 'acquisition_board')
    n_channels  = stream['num_channels']
    sample_rate = float(stream['sample_rate'])
    ch_names    = [ch['channel_name'] for ch in stream['channels']]
    bit_volts   = np.array([ch['bit_volts'] for ch in stream['channels']],
                            dtype=np.float32)
    ephys_idx   = [i for i, n in enumerate(ch_names) if n.startswith('CH')]
    timestamps  = np.load(os.path.join(continuous_dir, 'timestamps.npy'))
    dat_path    = os.path.join(continuous_dir, 'continuous.dat')
    n_samples   = len(timestamps)
    data        = np.memmap(dat_path, dtype='int16', mode='r',
                             shape=(n_samples, n_channels))
    return data, timestamps, sample_rate, ch_names, bit_volts, ephys_idx


def load_video_timestamps(csv_path, model):
    df = pd.read_csv(csv_path)
    df['oe_time_s'] = saviour_ns_to_oe_seconds(
        df['timestamp_ns'].values,
        model['sv_t0'], model['scale'], model['offset'])
    return df


# =============================================================================
# RENDERING
# =============================================================================

def tile_frames(frames, target_w, target_h):
    """
    Tile a list of BGR frames horizontally into a (target_h × target_w) image.
    Each frame is resized to equal width share, same height.
    """
    n      = len(frames)
    cell_w = target_w // n
    resized = [cv2.resize(f, (cell_w, target_h)) for f in frames]
    # pad last cell if rounding left a gap
    combined = np.hstack(resized)
    if combined.shape[1] < target_w:
        pad = np.zeros((target_h, target_w - combined.shape[1], 3),
                       dtype=np.uint8)
        combined = np.hstack([combined, pad])
    return combined


def render_ephys_frame(timestamps_cont, signal_uv, ch_names_sel,
                        oe_now, window_s, mode,
                        fig_w_px, fig_h_px, dpi=100):
    """
    Render an ephys panel for a single video frame via Agg backend.
    Returns RGB numpy array (fig_h_px, fig_w_px, 3).

    X-axis is always relative to the current frame (oe_now = 0),
    so tick labels are static across all frames — no flicker.

    mode:
      'epoch'     — oe_now centred, window_s / 2 either side, cursor at 0
      'scrolling' — oe_now at 30% from left, cursor at -0.2 * window_s
    """
    n_ch     = signal_uv.shape[1]
    fig_w_in = fig_w_px / dpi
    fig_h_in = fig_h_px / dpi

    # Absolute time window to fetch samples
    if mode == 'scrolling':
        cursor_rel = -window_s * 0.3        # cursor 30% from left in rel coords
        t_left_abs  = oe_now + cursor_rel
        t_right_abs = t_left_abs + window_s
    else:
        cursor_rel  = 0.0                   # cursor dead centre
        t_left_abs  = oe_now - window_s / 2
        t_right_abs = oe_now + window_s / 2

    # Relative axis limits (these never change between frames)
    rel_left  = t_left_abs  - oe_now       # e.g. -0.25 for epoch at 0.5s
    rel_right = t_right_abs - oe_now       # e.g. +0.25

    n_samples = len(timestamps_cont)
    i_left    = int(np.clip(np.searchsorted(timestamps_cont, t_left_abs),
                             0, n_samples - 1))
    i_right   = int(np.clip(np.searchsorted(timestamps_cont, t_right_abs),
                             0, n_samples - 1))

    # Convert fetched timestamps to relative time — signal slides, axis stays
    t_win_rel = timestamps_cont[i_left:i_right] - oe_now
    sig_win   = signal_uv[i_left:i_right, :]

    # Fixed tick positions and labels (computed once, same every frame)
    n_ticks   = 5
    ticks     = np.linspace(rel_left, rel_right, n_ticks)
    tick_lbls = [f"{t:+.2f}" for t in ticks]

    fig = plt.figure(figsize=(fig_w_in, fig_h_in), dpi=dpi,
                     facecolor='#0a0a0a')
    gs  = gridspec.GridSpec(n_ch, 1, figure=fig,
                             hspace=0.12,
                             top=0.92, bottom=0.12,
                             left=0.07, right=0.99)

    for ch_i in range(n_ch):
        ax = fig.add_subplot(gs[ch_i])
        ax.set_facecolor('#0d1117')

        if len(t_win_rel) > 1:
            ax.plot(t_win_rel, sig_win[:, ch_i],
                    color='#4fc3f7', linewidth=0.6, alpha=0.9)
            pad = max(sig_win[:, ch_i].std() * 3, 10)
            mid = sig_win[:, ch_i].mean()
            ax.set_ylim(mid - pad, mid + pad)

        ax.set_xlim(rel_left, rel_right)

        # Cursor at fixed relative position
        ax.axvline(cursor_rel, color='#ef5350', linewidth=1.0,
                   linestyle='--', alpha=0.85)

        ax.set_ylabel(ch_names_sel[ch_i], color='#7986cb',
                      fontsize=6, rotation=0, labelpad=28, va='center')
        ax.tick_params(colors='#7986cb', labelsize=6)
        for spine in ax.spines.values():
            spine.set_edgecolor('#2a2d3a')

        # Only bottom axis gets tick labels — and they're always the same
        ax.set_xticks(ticks)
        if ch_i < n_ch - 1:
            ax.set_xticklabels([])
        else:
            ax.set_xticklabels(tick_lbls)
            ax.set_xlabel("time relative to frame (s)",
                          color='#7986cb', fontsize=7)

    # Mode label — bottom right, static
    mode_label = "SCROLLING" if mode == 'scrolling' else "EPOCH"
    fig.text(0.99, 0.03, mode_label, ha='right', va='bottom',
             color='#444455', fontsize=7, fontstyle='italic')

    # Current time — bottom left, matches cursor colour
    oe_mm, oe_ss = divmod(oe_now, 60)
    time_str = f"t = {oe_now:.3f}s  ({int(oe_mm):02d}:{oe_ss:06.3f})"
    fig.text(0.01, 0.03, time_str, ha='left', va='bottom',
             color='#ef5350', fontsize=8,
             fontfamily='monospace',
             bbox=dict(boxstyle='round,pad=0.3',
                       facecolor='#0a0a0a',
                       edgecolor='#ef5350',
                       alpha=0.8, linewidth=0.8))

    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    buf = canvas.buffer_rgba()
    img = np.frombuffer(buf, dtype=np.uint8).reshape(fig_h_px, fig_w_px, 4)
    plt.close(fig)
    return img[:, :, :3]


def add_camera_label(frame_bgr, label, color_hex, font_scale=0.55):
    """Burn a camera name label into the bottom-left of a frame."""
    color_rgb = tuple(int(color_hex.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
    color_bgr = (color_rgb[2], color_rgb[1], color_rgb[0])
    h = frame_bgr.shape[0]
    # Position: bottom-left with a small margin
    y = h - 10
    cv2.putText(frame_bgr, label, (8, y),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                (0, 0, 0), 3, cv2.LINE_AA)   # black shadow
    cv2.putText(frame_bgr, label, (8, y),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                color_bgr, 1, cv2.LINE_AA)   # coloured text
    return frame_bgr


# =============================================================================
# EXPORT ENGINE
# =============================================================================

def export_video(config, progress_cb, done_cb, error_cb, abort_event=None):
    """
    config keys:
        model_path, continuous_dir,
        cameras           : list of { name, video_path, timestamps_csv }
        channels          : list of int (indices into ephys_idx)
        window_s, mode    : LFP display settings
        output_path
        start_s, end_s    : optional OE time clip (None = full)
        out_w, vid_h      : output frame dimensions
        ephys_h           : pixel height of LFP panel
    """
    try:
        progress_cb("Loading alignment model...")
        model = load_model(config['model_path'])

        progress_cb("Loading ephys data...")
        oebin_path = find_oebin(config['continuous_dir'])
        (data_cont, timestamps_cont, sample_rate,
         ch_names, bit_volts, ephys_idx) = load_continuous(
            config['continuous_dir'], oebin_path)

        sel_idx      = [ephys_idx[i] for i in config['channels']]
        ch_names_sel = [ch_names[i] for i in sel_idx]
        progress_cb(f"Pre-loading {len(sel_idx)} channel(s) into RAM...")
        signal_uv = (data_cont[:, sel_idx].astype(np.float32)
                     * bit_volts[sel_idx])

        # ── Open all cameras ─────────────────────────────────────────────────
        progress_cb("Opening camera streams...")
        caps      = []
        df_videos = []
        for cam in config['cameras']:
            cap = cv2.VideoCapture(cam['video_path'])
            if not cap.isOpened():
                raise RuntimeError(
                    f"Could not open video:\n{cam['video_path']}")
            caps.append(cap)
            df_videos.append(
                load_video_timestamps(cam['timestamps_csv'], model))

        fps     = caps[0].get(cv2.CAP_PROP_FPS)
        n_total = int(caps[0].get(cv2.CAP_PROP_FRAME_COUNT))

        # Use first camera as timing reference
        oe_video_start = df_videos[0]['oe_time_s'].iloc[0]
        oe_video_end   = df_videos[0]['oe_time_s'].iloc[-1]
        vid_dur_s      = oe_video_end - oe_video_start

        # start_s / end_s are seconds into the video (0 = first frame)
        start_vid = config.get('start_s')
        end_vid   = config.get('end_s')
        if start_vid is None:
            start_vid = 0.0
        if end_vid is None:
            end_vid = vid_dur_s

        start_vid = float(np.clip(start_vid, 0.0, vid_dur_s))
        end_vid   = float(np.clip(end_vid,   0.0, vid_dur_s))

        if end_vid <= start_vid:
            raise ValueError(
                f"Clip end ({end_vid:.2f}s) must be greater than "
                f"clip start ({start_vid:.2f}s). "
                f"Video duration is {vid_dur_s:.2f}s.")

        frame_start  = int(np.clip(round(start_vid * fps), 0, n_total - 1))
        frame_end    = int(np.clip(round(end_vid   * fps), 0, n_total - 1))
        n_frames_out = frame_end - frame_start

        progress_cb(f"Clip: {start_vid:.2f}s to {end_vid:.2f}s  "
                    f"({n_frames_out} frames @ {fps:.0f}fps)  "
                    f"{len(config['cameras'])} camera(s)  "
                    f"{len(sel_idx)} channel(s)...")

        out_w   = config['out_w']
        vid_h   = config['vid_h']
        ephys_h = config['ephys_h']
        out_h   = vid_h + ephys_h

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(
            config['output_path'], fourcc, fps, (out_w, out_h))
        if not writer.isOpened():
            raise RuntimeError(
                f"Could not open video writer:\n{config['output_path']}")

        # Seek all cameras to start frame
        for cap in caps:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_start)

        for frame_i in range(n_frames_out):
            oe_now = oe_video_start + (frame_start + frame_i) / fps

            # Read one frame from each camera
            cam_frames = []
            for ci, cap in enumerate(caps):
                ret, frame = cap.read()
                if not ret:
                    # If camera runs out, use black frame
                    frame = np.zeros((vid_h, out_w // len(caps), 3),
                                     dtype=np.uint8)
                label = config['cameras'][ci]['name'][:20]
                frame = add_camera_label(frame, label, CAM_COLORS[ci])
                cam_frames.append(frame)

            # Tile camera frames horizontally
            video_row = tile_frames(cam_frames, out_w, vid_h)

            # Render ephys panel
            ephys_rgb = render_ephys_frame(
                timestamps_cont, signal_uv, ch_names_sel,
                oe_now, config['window_s'], config['mode'],
                out_w, ephys_h, dpi=100)
            ephys_bgr = cv2.cvtColor(ephys_rgb, cv2.COLOR_RGB2BGR)

            combined = np.vstack([video_row, ephys_bgr])
            writer.write(combined)

            # Check for abort
            if abort_event is not None and abort_event.is_set():
                progress_cb("Aborting...")
                break

            if frame_i % 30 == 0:
                pct = int(100 * frame_i / max(n_frames_out, 1))
                progress_cb(
                    f"Rendering  {frame_i}/{n_frames_out}  ({pct}%)")

        for cap in caps:
            cap.release()
        writer.release()

        if abort_event is not None and abort_event.is_set():
            # Remove incomplete file
            try:
                os.remove(config['output_path'])
            except Exception:
                pass
            progress_cb("Export aborted.")
            done_cb(None)   # None signals aborted
        else:
            done_cb(config['output_path'])

    except Exception:
        error_cb(traceback.format_exc())


# =============================================================================
# GUI
# =============================================================================

class ExportTool(tk.Tk):

    FONT    = ("Courier New", 10)
    FONT_LG = ("Courier New", 13, "bold")
    FONT_SM = ("Courier New", 9)

    def __init__(self):
        super().__init__()
        self.title("SAVIOUR ↔ OpenEphys  |  Video Exporter")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("960x960")

        self.model        = None
        self.ephys_idx    = []
        self.ch_names     = []
        self.sample_rate  = 3000.0
        self.video_pairs  = []       # list of dicts {name, video_path, timestamps_csv}
        self._ch_vars     = []
        self._cam_vars    = []       # BooleanVar per camera

        self.model_path   = tk.StringVar()
        self.oe_root      = tk.StringVar()
        self.sv_root      = tk.StringVar()
        self.output_path  = tk.StringVar(
            value=os.path.join(SCRIPT_DIR, "export.mp4"))
        self.window_s     = tk.DoubleVar(value=0.5)
        self.mode         = tk.StringVar(value="epoch")
        self.start_s      = tk.StringVar(value="")
        self.end_s        = tk.StringVar(value="")

        self._abort_event = None   # threading.Event set to abort export

        self._build_ui()
        self._auto_detect()

    # ── auto-detect ───────────────────────────────────────────────────────────

    def _auto_detect(self):
        self.oe_status.config(text="Searching...", fg=MUTED)
        self.sv_status.config(text="Searching...", fg=MUTED)
        self.update_idletasks()

        def _search():
            oe, sv = auto_detect_roots(SCRIPT_DIR)
            self.after(0, lambda: self._apply_auto_detect(oe, sv))

        threading.Thread(target=_search, daemon=True).start()

        # Auto-load model
        default_model = os.path.join(SCRIPT_DIR, "model.json")
        if os.path.exists(default_model):
            self.model_path.set(default_model)
            self._load_model_from_path(default_model)

    def _apply_auto_detect(self, oe_root, sv_root):
        if oe_root:
            self.oe_root.set(oe_root)
            self._on_oe_root_changed()
        else:
            self.oe_status.config(
                text="Not found — please browse.", fg=MUTED)
        if sv_root:
            self.sv_root.set(sv_root)
            self._on_sv_root_changed()
        else:
            self.sv_status.config(
                text="Not found — please browse.", fg=MUTED)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=20, pady=(18, 4))
        tk.Label(hdr, text="VIDEO EXPORTER",
                 font=("Courier New", 16, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="Synchronised multi-camera + LFP export",
                 font=self.FONT_SM, bg=BG, fg=MUTED).pack(
                     side="left", padx=12)

        self._divider()
        self._section("1  ALIGNMENT MODEL")
        self._path_row("Model JSON (from tool1)", self.model_path,
                        self._browse_model)
        self.model_status = tk.Label(self, text="", font=self.FONT_SM,
                                      bg=BG, fg=MUTED)
        self.model_status.pack(anchor="w", padx=52)

        self._divider()
        self._section("2  DATA FOLDERS")
        self._path_row("OpenEphys recording folder", self.oe_root,
                        lambda: self._browse_dir(
                            self.oe_root, self._on_oe_root_changed))
        self.oe_status = tk.Label(self, text="", font=self.FONT_SM,
                                   bg=BG, fg=MUTED)
        self.oe_status.pack(anchor="w", padx=52)

        self._path_row("SAVIOUR session folder", self.sv_root,
                        lambda: self._browse_dir(
                            self.sv_root, self._on_sv_root_changed))
        self.sv_status = tk.Label(self, text="", font=self.FONT_SM,
                                   bg=BG, fg=MUTED)
        self.sv_status.pack(anchor="w", padx=52)

        self._divider()
        self._section(f"3  CAMERA STREAMS  (select up to {MAX_CAMERAS})")
        cam_outer = tk.Frame(self, bg=PANEL)
        cam_outer.pack(fill="x", padx=24, pady=4)
        self.cam_inner = tk.Frame(cam_outer, bg=PANEL)
        self.cam_inner.pack(fill="x", padx=12, pady=8)
        tk.Label(self.cam_inner,
                 text="Load SAVIOUR folder to see cameras.",
                 font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w")

        self._divider()
        self._section("4  CHANNEL SELECTION")
        ch_outer = tk.Frame(self, bg=PANEL)
        ch_outer.pack(fill="x", padx=24, pady=4)
        self.ch_inner = tk.Frame(ch_outer, bg=PANEL)
        self.ch_inner.pack(fill="x", padx=12, pady=8)
        tk.Label(self.ch_inner,
                 text="Load OpenEphys folder to see channels.",
                 font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w")

        self._divider()
        self._section("5  EXPORT SETTINGS")

        settings = tk.Frame(self, bg=BG)
        settings.pack(fill="x", padx=24, pady=4)

        row1 = tk.Frame(settings, bg=BG)
        row1.pack(fill="x", pady=3)
        tk.Label(row1, text="LFP window (s):", font=self.FONT_SM,
                 bg=BG, fg=MUTED, width=18, anchor="w").pack(side="left")
        tk.Spinbox(row1, from_=0.1, to=30.0, increment=0.1,
                   textvariable=self.window_s, width=6,
                   font=self.FONT, bg=PANEL, fg=TEXT,
                   buttonbackground=PANEL, insertbackground=TEXT,
                   relief="flat").pack(side="left", padx=8)
        tk.Label(row1, text="Display mode:", font=self.FONT_SM,
                 bg=BG, fg=MUTED).pack(side="left", padx=(24, 4))
        tk.Radiobutton(row1, text="Epoch  (cursor centred)",
                       variable=self.mode, value="epoch",
                       font=self.FONT_SM, bg=BG, fg=ACCENT2,
                       activebackground=BG, selectcolor=PANEL,
                       activeforeground=ACCENT2).pack(side="left", padx=4)
        tk.Radiobutton(row1, text="Scrolling  (cursor at 30%)",
                       variable=self.mode, value="scrolling",
                       font=self.FONT_SM, bg=BG, fg=MUTED,
                       activebackground=BG, selectcolor=PANEL,
                       activeforeground=MUTED).pack(side="left", padx=4)

        row2 = tk.Frame(settings, bg=BG)
        row2.pack(fill="x", pady=3)
        tk.Label(row2, text="Clip start (OE s):", font=self.FONT_SM,
                 bg=BG, fg=MUTED, width=18, anchor="w").pack(side="left")
        tk.Entry(row2, textvariable=self.start_s, font=self.FONT_SM,
                 bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", bd=4, width=10).pack(side="left", padx=4)
        tk.Label(row2, text="End (OE s):", font=self.FONT_SM,
                 bg=BG, fg=MUTED).pack(side="left", padx=(16, 4))
        tk.Entry(row2, textvariable=self.end_s, font=self.FONT_SM,
                 bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", bd=4, width=10).pack(side="left", padx=4)
        tk.Label(row2, text="(seconds into video, blank = full)",
                 font=self.FONT_SM, bg=BG, fg=MUTED).pack(
                     side="left", padx=8)

        self._divider()
        self._section("6  OUTPUT")
        self._path_row("Save video to (.mp4)", self.output_path,
                        self._browse_output)

        self._divider()

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=10)
        self.export_btn = tk.Button(
            btn_frame, text="EXPORT VIDEO",
            font=("Courier New", 11, "bold"),
            bg=ACCENT, fg=BG, activebackground="#81d4fa",
            relief="flat", padx=24, pady=8, cursor="hand2",
            command=self._run_export)
        self.export_btn.pack(side="left", padx=6)

        self.abort_btn = tk.Button(
            btn_frame, text="ABORT",
            font=("Courier New", 11, "bold"),
            bg=WARN, fg=BG, activebackground="#ef9a9a",
            relief="flat", padx=16, pady=8, cursor="hand2",
            state="disabled",
            command=self._abort_export)
        self.abort_btn.pack(side="left", padx=6)

        prog_outer = tk.Frame(self, bg=PANEL)
        prog_outer.pack(fill="x", padx=24, pady=4)
        self.progress_label = tk.Label(
            prog_outer,
            text="Configure settings and click Export.",
            font=self.FONT_SM, bg=PANEL, fg=MUTED, anchor="w")
        self.progress_label.pack(fill="x", padx=12, pady=8)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TProgressbar",
                         background=ACCENT, troughcolor=PANEL,
                         bordercolor=BORDER, lightcolor=ACCENT,
                         darkcolor=ACCENT)
        self.progress_bar = ttk.Progressbar(
            self, mode='indeterminate', length=400)
        self.progress_bar.pack(pady=4)

    def _divider(self):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=6)

    def _section(self, title):
        tk.Label(self, text=title, font=self.FONT_LG,
                 bg=BG, fg=ACCENT).pack(anchor="w", padx=24, pady=(6, 2))

    def _path_row(self, label, var, cmd):
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=24, pady=3)
        tk.Label(row, text=f"{label}:", font=self.FONT_SM,
                 bg=BG, fg=MUTED, width=28, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, font=self.FONT_SM,
                 bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief="flat", bd=4).pack(
                     side="left", fill="x", expand=True, padx=4)
        tk.Button(row, text="Browse", font=self.FONT_SM,
                  bg=BORDER, fg=TEXT, relief="flat",
                  activebackground=MUTED, cursor="hand2",
                  command=cmd).pack(side="left")

    # ── browse helpers ────────────────────────────────────────────────────────

    def _browse_dir(self, var, callback=None):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            var.set(d)
            if callback:
                callback()

    def _browse_model(self):
        f = filedialog.askopenfilename(
            title="Load alignment model",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialdir=SCRIPT_DIR)
        if f:
            self.model_path.set(f)
            self._load_model_from_path(f)

    def _browse_output(self):
        f = filedialog.asksaveasfilename(
            title="Save exported video",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4")],
            initialdir=SCRIPT_DIR,
            initialfile="export.mp4")
        if f:
            self.output_path.set(f)

    # ── model ─────────────────────────────────────────────────────────────────

    def _load_model_from_path(self, path):
        try:
            with open(path) as f:
                self.model = json.load(f)
            sr = self.model.get('sample_rate')
            if sr:
                self.sample_rate = float(sr)
            self.model_status.config(
                text=f"✓  R²={self.model['r2']:.10f}  |  "
                     f"drift={self.model['meta'].get('drift_ms','?')} ms",
                fg=ACCENT2)
        except Exception as e:
            self.model_status.config(text=f"✗  {e}", fg=WARN)

    # ── OE root ───────────────────────────────────────────────────────────────

    def _on_oe_root_changed(self):
        root = self.oe_root.get()
        if not root or not os.path.isdir(root):
            return
        try:
            cont_dir = find_continuous_dir(root)
            oebin    = find_oebin(cont_dir)
            with open(oebin) as f:
                ob = json.load(f)
            stream = next(
                s for s in ob['continuous']
                if s['stream_name'] == 'acquisition_board')
            self.ch_names   = [ch['channel_name']
                                for ch in stream['channels']]
            self.ephys_idx  = [i for i, n in enumerate(self.ch_names)
                                if n.startswith('CH')]
            self.sample_rate = float(stream['sample_rate'])
            self.oe_status.config(
                text=f"✓  {len(self.ephys_idx)} ephys ch  |  "
                     f"{self.sample_rate:.0f} Hz  |  {cont_dir}",
                fg=ACCENT2)
            self._build_channel_checkboxes()
        except Exception as e:
            self.oe_status.config(text=f"✗  {e}", fg=WARN)

    # ── SAVIOUR root ──────────────────────────────────────────────────────────

    def _on_sv_root_changed(self):
        root = self.sv_root.get()
        if not root or not os.path.isdir(root):
            return
        pairs = find_video_pairs(root)
        self.video_pairs = pairs
        if pairs:
            self.sv_status.config(
                text=f"✓  {len(pairs)} camera stream(s) found",
                fg=ACCENT2)
            self._build_camera_checkboxes()
        else:
            self.sv_status.config(
                text="✗  No .ts + timestamps CSV pairs found", fg=WARN)

    # ── camera checkboxes ─────────────────────────────────────────────────────

    def _build_camera_checkboxes(self):
        for w in self.cam_inner.winfo_children():
            w.destroy()
        self._cam_vars = []

        if not self.video_pairs:
            tk.Label(self.cam_inner, text="No cameras found.",
                     font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w")
            return

        tk.Label(self.cam_inner,
                 text=f"Select up to {MAX_CAMERAS} cameras "
                      f"(tiled left-to-right in export):",
                 font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w",
                                                               pady=(0, 4))

        for i, pair in enumerate(self.video_pairs):
            var = tk.BooleanVar(value=(i == 0))  # first camera on by default
            self._cam_vars.append(var)
            color = CAM_COLORS[i % len(CAM_COLORS)]
            row   = tk.Frame(self.cam_inner, bg=PANEL)
            row.pack(anchor="w", pady=1)
            # Colour swatch
            swatch = tk.Label(row, text="  ", bg=color, width=2)
            swatch.pack(side="left", padx=(0, 6))
            tk.Checkbutton(row, text=pair['name'],
                           variable=var,
                           font=self.FONT_SM, bg=PANEL, fg=TEXT,
                           activebackground=PANEL, selectcolor=PANEL2,
                           activeforeground=color,
                           command=self._enforce_camera_limit).pack(
                               side="left")

        if len(self.video_pairs) > MAX_CAMERAS:
            tk.Label(self.cam_inner,
                     text=f"(only first {MAX_CAMERAS} selected will be used)",
                     font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(
                         anchor="w", pady=(4, 0))

    def _enforce_camera_limit(self):
        """Uncheck extras if more than MAX_CAMERAS are selected."""
        checked = [i for i, v in enumerate(self._cam_vars) if v.get()]
        if len(checked) > MAX_CAMERAS:
            # Uncheck the most recently checked (last in list)
            for i in checked[MAX_CAMERAS:]:
                self._cam_vars[i].set(False)

    # ── channel checkboxes ────────────────────────────────────────────────────

    def _build_channel_checkboxes(self):
        for w in self.ch_inner.winfo_children():
            w.destroy()
        self._ch_vars = []

        ephys_names = [self.ch_names[i] for i in self.ephys_idx]

        ctrl = tk.Frame(self.ch_inner, bg=PANEL)
        ctrl.pack(anchor="w", pady=(0, 4))
        tk.Button(ctrl, text="All", font=self.FONT_SM,
                  bg=BORDER, fg=TEXT, relief="flat", cursor="hand2",
                  command=lambda: [v.set(True)
                                   for v in self._ch_vars]).pack(
                      side="left", padx=(0, 4))
        tk.Button(ctrl, text="None", font=self.FONT_SM,
                  bg=BORDER, fg=TEXT, relief="flat", cursor="hand2",
                  command=lambda: [v.set(False)
                                   for v in self._ch_vars]).pack(side="left")

        grid = tk.Frame(self.ch_inner, bg=PANEL)
        grid.pack(fill="x")
        cols = 8
        for idx, name in enumerate(ephys_names):
            var = tk.BooleanVar(value=(idx < 4))
            self._ch_vars.append(var)
            row, col = divmod(idx, cols)
            tk.Checkbutton(grid, text=name, variable=var,
                           font=self.FONT_SM, bg=PANEL, fg=TEXT,
                           activebackground=PANEL, selectcolor=PANEL2,
                           activeforeground=ACCENT).grid(
                               row=row, column=col,
                               sticky="w", padx=4, pady=1)

    # ── export ────────────────────────────────────────────────────────────────

    def _run_export(self):
        if not self.model:
            messagebox.showerror("Missing", "Please load an alignment model.")
            return
        if not self.oe_root.get():
            messagebox.showerror("Missing",
                "Please select the OpenEphys recording folder.")
            return

        sel_cams = [self.video_pairs[i]
                    for i, v in enumerate(self._cam_vars)
                    if v.get()][:MAX_CAMERAS]
        if not sel_cams:
            messagebox.showerror("No cameras",
                "Please select at least one camera stream.")
            return

        if not self.output_path.get():
            messagebox.showerror("Missing",
                "Please choose an output path.")
            return

        if not self._ch_vars:
            messagebox.showerror("Missing",
                "No channels loaded. Select OpenEphys folder first.")
            return

        sel_channels = [i for i, v in enumerate(self._ch_vars) if v.get()]
        if not sel_channels:
            messagebox.showerror("No channels",
                "Please select at least one channel.")
            return

        try:
            start_s = (float(self.start_s.get())
                       if self.start_s.get().strip() else None)
            end_s   = (float(self.end_s.get())
                       if self.end_s.get().strip() else None)
        except ValueError:
            messagebox.showerror("Invalid range",
                "Clip start/end must be numbers or blank.")
            return

        try:
            cont_dir = find_continuous_dir(self.oe_root.get())
        except FileNotFoundError as e:
            messagebox.showerror("Not found", str(e))
            return

        # Determine output dimensions from first camera
        cap_probe = cv2.VideoCapture(sel_cams[0]['video_path'])
        vid_w     = int(cap_probe.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h     = int(cap_probe.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap_probe.release()

        # Scale video panel width to accommodate multiple cameras
        n_ch_sel  = len(sel_channels)
        out_w     = vid_w * len(sel_cams)
        ephys_h   = max(120, min(40 * n_ch_sel + 60, 320))

        config = {
            'model_path':     self.model_path.get(),
            'continuous_dir': cont_dir,
            'cameras':        sel_cams,
            'channels':       sel_channels,
            'window_s':       float(self.window_s.get()),
            'mode':           self.mode.get(),
            'output_path':    self.output_path.get(),
            'start_s':        start_s,
            'end_s':          end_s,
            'out_w':          out_w,
            'vid_h':          vid_h,
            'ephys_h':        ephys_h,
        }

        self._abort_event = __import__('threading').Event()
        self.export_btn.config(state="disabled", text="EXPORTING...")
        self.abort_btn.config(state="normal")
        self.progress_bar.start(12)

        def _progress(msg):
            self.after(0, lambda m=msg:
                       self.progress_label.config(text=m, fg=MUTED))

        abort_ev = self._abort_event
        threading.Thread(
            target=export_video,
            args=(config, _progress,
                  lambda p: self.after(0, lambda: self._on_done(p)),
                  lambda t: self.after(0, lambda: self._on_error(t)),
                  abort_ev),
            daemon=True).start()

    def _abort_export(self):
        if self._abort_event:
            self._abort_event.set()
        self.abort_btn.config(state="disabled", text="ABORTING...")
        self.progress_label.config(text="Aborting — finishing current frame...",
                                    fg=WARN)

    def _on_done(self, out_path):
        self.progress_bar.stop()
        self.export_btn.config(state="normal", text="EXPORT VIDEO")
        self.abort_btn.config(state="disabled")
        if out_path is None:
            self.progress_label.config(
                text="Export aborted.", fg=WARN)
        else:
            self.progress_label.config(
                text=f"✓  Exported: {out_path}", fg=ACCENT2)
            messagebox.showinfo("Export complete",
                                 f"Video saved to:\n{out_path}")

    def _on_error(self, tb):
        self.progress_bar.stop()
        self.export_btn.config(state="normal", text="EXPORT VIDEO")
        self.abort_btn.config(state="disabled")
        self.progress_label.config(
            text="✗  Export failed — see error dialog", fg=WARN)
        log_path = os.path.join(SCRIPT_DIR, "tool3_error.txt")
        with open(log_path, "w") as f:
            f.write(tb)
        messagebox.showerror("Export failed",
                              f"See log: {log_path}\n\n{tb[:500]}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    app = ExportTool()
    app.mainloop()