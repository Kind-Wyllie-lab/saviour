# -*- coding: utf-8 -*-
"""
tool1_fit_alignment.py
======================
SAVIOUR ↔ OpenEphys Clock Alignment Tool

On launch, automatically searches the script's own directory for an
OpenEphys recording folder and a SAVIOUR session folder. Both can be
overridden by browsing.

Before fitting, checks PTP sync quality of all health metadata files found
under the SAVIOUR session folder. Warns the user if any Pi exceeded the
1ms combined offset threshold, and lets them decide whether to proceed.

Fits and saves a JSON alignment model (model.json) next to this script.

No scipy dependency — all maths is pure numpy.

Dependencies: numpy, pandas, matplotlib, tkinter (built-in)
"""

import json
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import traceback
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PTP_THRESHOLD_NS_DEFAULT = 1_000_000   # 1 ms in nanoseconds

BG      = "#0f1117"
PANEL   = "#1a1d27"
BORDER  = "#2a2d3a"
ACCENT  = "#4fc3f7"
ACCENT2 = "#81c784"
WARN    = "#ef9a9a"
TEXT    = "#e8eaf6"
MUTED   = "#7986cb"


# =============================================================================
# AUTO-DISCOVERY
# =============================================================================

def find_oe_ttl_dir(oe_root):
    """Walk oe_root, return first folder with timestamps.npy + states.npy."""
    for dirpath, _, filenames in os.walk(oe_root):
        if 'timestamps.npy' in filenames and 'states.npy' in filenames:
            return dirpath
    raise FileNotFoundError(
        f"No TTL folder (timestamps.npy + states.npy) found under:\n{oe_root}")


def find_saviour_ttl_csv(sv_root):
    """Walk sv_root, return first CSV with 'ttl' but not 'health' in name."""
    for dirpath, _, filenames in os.walk(sv_root):
        for fname in filenames:
            lower = fname.lower()
            if lower.endswith('.csv') and 'ttl' in lower and 'health' not in lower:
                return os.path.join(dirpath, fname)
    raise FileNotFoundError(
        f"No SAVIOUR TTL CSV found under:\n{sv_root}")


def find_saviour_health_csvs(sv_root):
    """Walk sv_root, return all health metadata CSVs."""
    found = []
    for dirpath, _, filenames in os.walk(sv_root):
        for fname in filenames:
            lower = fname.lower()
            if lower.endswith('.csv') and 'health' in lower:
                found.append(os.path.join(dirpath, fname))
    return found


def auto_detect_roots(search_dir):
    """
    Search search_dir and its immediate subdirectories for OE and SAVIOUR roots.
    Returns (oe_root, sv_root) — either may be None if not found.
    """
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

    for candidate in candidates:
        if oe_root is None:
            try:
                find_oe_ttl_dir(candidate)
                oe_root = candidate
            except FileNotFoundError:
                pass
        if sv_root is None:
            try:
                find_saviour_ttl_csv(candidate)
                sv_root = candidate
            except FileNotFoundError:
                pass
        if oe_root and sv_root:
            break

    return oe_root, sv_root


# =============================================================================
# PTP HEALTH CHECK
# =============================================================================

def check_ptp_health(sv_root, sv_t0, rec_dur_s=120,
                      threshold_ns=PTP_THRESHOLD_NS_DEFAULT):
    """
    Find all health CSVs under sv_root, assess PTP sync quality during
    the recording window (sv_t0 to sv_t0 + rec_dur_s).

    Returns a list of dicts, one per health file:
        name, path, n_samples, ptp4l_mean_ns, ptp4l_max_ns,
        phc2sys_mean_ns, phc2sys_max_ns, combined_max_ns, ok (bool)
    """
    health_paths = find_saviour_health_csvs(sv_root)
    results = []

    for path in health_paths:
        try:
            df = pd.read_csv(path, encoding='utf-8-sig')
            df.columns = df.columns.str.strip()

            # Require the essential columns
            required = {'timestamp', 'ptp4l_offset', 'phc2sys_offset'}
            if not required.issubset(set(df.columns)):
                continue

            df['timestamp']       = pd.to_numeric(df['timestamp'],
                                                   errors='coerce')
            df['ptp4l_offset']    = pd.to_numeric(df['ptp4l_offset'],
                                                   errors='coerce')
            df['phc2sys_offset']  = pd.to_numeric(df['phc2sys_offset'],
                                                   errors='coerce')
            df = df.dropna(subset=['timestamp', 'ptp4l_offset',
                                   'phc2sys_offset'])

            # Filter to recording window
            rec_end_s = sv_t0 + rec_dur_s
            dfw = df[(df['timestamp'] >= sv_t0) &
                     (df['timestamp'] <= rec_end_s)]

            if len(dfw) == 0:
                # Try the whole file if window filter returns nothing
                dfw = df

            combined = dfw['ptp4l_offset'].abs() + dfw['phc2sys_offset'].abs()

            result = {
                'name':             os.path.basename(path),
                'path':             path,
                'n_samples':        int(len(dfw)),
                'ptp4l_mean_ns':    float(dfw['ptp4l_offset'].mean()),
                'ptp4l_max_ns':     float(dfw['ptp4l_offset'].abs().max()),
                'phc2sys_mean_ns':  float(dfw['phc2sys_offset'].mean()),
                'phc2sys_max_ns':   float(dfw['phc2sys_offset'].abs().max()),
                'combined_max_ns':  float(combined.max()),
                'combined_mean_ns': float(combined.mean()),
                'ok':               float(combined.max()) < threshold_ns,
            }
            results.append(result)

        except Exception:
            results.append({
                'name':    os.path.basename(path),
                'path':    path,
                'error':   traceback.format_exc(),
                'ok':      None,
            })

    return results


# =============================================================================
# ALIGNMENT LOGIC — pure numpy, no scipy
# =============================================================================

def load_oe_ttl(ttl_dir):
    for f in ['timestamps.npy', 'states.npy']:
        if not os.path.exists(os.path.join(ttl_dir, f)):
            raise FileNotFoundError(f"Missing: {os.path.join(ttl_dir, f)}")
    timestamps = np.load(os.path.join(ttl_dir, 'timestamps.npy'))
    states     = np.load(os.path.join(ttl_dir, 'states.npy'))
    return timestamps, states


def load_saviour_ttl(csv_path, pin_number, invert=True):
    df   = pd.read_csv(csv_path)
    df19 = df[df['pin_number'] == pin_number].copy()
    if len(df19) == 0:
        raise ValueError(f"No events for pin {pin_number} in {csv_path}")
    df19['time_s']    = df19['Timestamp_nanoseconds'] / 1e9
    df19['state_raw'] = df19['pin_state'].map(
        {'TTLValue.HIGH': 1, 'TTLValue.LOW': -1})
    df19['state']     = df19['state_raw'] * (-1 if invert else 1)
    t0     = df19['time_s'].iloc[0]
    t_rel  = df19['time_s'].values - t0
    states = df19['state'].values
    return t_rel, states, t0


def numpy_linregress(x, y):
    """OLS linear regression — pure numpy."""
    x  = np.asarray(x, dtype=np.float64)
    y  = np.asarray(y, dtype=np.float64)
    xm = x - x.mean()
    ym = y - y.mean()
    slope     = np.sum(xm * ym) / np.sum(xm ** 2)
    intercept = y.mean() - slope * x.mean()
    denom     = np.sqrt(np.sum(xm ** 2) * np.sum(ym ** 2))
    r_value   = float(np.sum(xm * ym) / denom) if denom > 0 else 0.0
    return float(slope), float(intercept), r_value


def numpy_corrcoef(a, b):
    """Pearson correlation — pure numpy."""
    a  = np.asarray(a, dtype=np.float64)
    b  = np.asarray(b, dtype=np.float64)
    am = a - a.mean()
    bm = b - b.mean()
    denom = np.sqrt(np.sum(am ** 2) * np.sum(bm ** 2))
    return float(np.sum(am * bm) / denom) if denom > 0 else 0.0


def fit_alignment(oe_timestamps, oe_states, sv_timestamps, sv_states):
    oe_rising = oe_timestamps[oe_states == 1]
    sv_rising = sv_timestamps[sv_states == 1]
    n_edges   = min(len(oe_rising), len(sv_rising))

    oe_ipi   = np.diff(oe_rising[:n_edges])
    sv_ipi   = np.diff(sv_rising[:n_edges])
    m        = min(len(oe_ipi), len(sv_ipi))
    ipi_corr = numpy_corrcoef(oe_ipi[:m], sv_ipi[:m])

    scale, offset, r_value = numpy_linregress(
        sv_rising[:n_edges], oe_rising[:n_edges])
    r2        = r_value ** 2
    predicted = scale * sv_rising[:n_edges] + offset
    residuals = oe_rising[:n_edges] - predicted
    drift_ms  = abs(1.0 - scale) * float(sv_timestamps[-1]) * 1000

    meta = {
        "oe_rising_edges":  int(len(oe_rising)),
        "sv_rising_edges":  int(len(sv_rising)),
        "edges_used":       int(n_edges),
        "ipi_correlation":  round(ipi_corr, 8),
        "scale":            scale,
        "offset":           offset,
        "r2":               r2,
        "drift_ms":         round(drift_ms, 4),
        "residual_mean_ms": round(float(np.mean(residuals)) * 1000, 4),
        "residual_std_ms":  round(float(np.std(residuals)) * 1000, 4),
        "residual_max_ms":  round(float(np.max(np.abs(residuals))) * 1000, 4),
    }
    return scale, offset, r2, residuals, sv_rising[:n_edges], meta


# =============================================================================
# PTP WARNING DIALOG
# =============================================================================

def show_ptp_warning(parent, ptp_results, threshold_us=1000):
    """
    Show a modal dialog with PTP health results.
    Returns True if user chooses to proceed, False to abort.
    """
    win = tk.Toplevel(parent)
    win.title(f"PTP Sync Health Warning  (threshold: {threshold_us} µs)")
    win.configure(bg=BG)
    win.resizable(False, False)
    win.grab_set()   # modal

    # Centre on parent
    win.geometry("680x420")
    win.update_idletasks()
    px = parent.winfo_x() + (parent.winfo_width()  - 680) // 2
    py = parent.winfo_y() + (parent.winfo_height() - 420) // 2
    win.geometry(f"680x420+{px}+{py}")

    proceed = tk.BooleanVar(value=False)

    tk.Label(win,
             text="⚠  PTP SYNC QUALITY CHECK",
             font=("Courier New", 13, "bold"),
             bg=BG, fg=WARN).pack(padx=20, pady=(16, 4))
    tk.Label(win,
             text=f"One or more Raspberry Pis exceeded the {threshold_us} µs combined PTP\n"
                  f"offset threshold during this recording. Review before proceeding.",
             font=("Courier New", 9), bg=BG, fg=TEXT,
             justify="center").pack(padx=20, pady=(0, 10))

    # Table
    tbl = tk.Frame(win, bg=PANEL)
    tbl.pack(fill="x", padx=20, pady=4)

    headers = ["Module", "Samples", "ptp4l max", "phc2sys max",
               "Combined max", "Status"]
    widths  = [24, 8, 12, 14, 14, 8]
    FONT_SM = ("Courier New", 9)

    hdr_row = tk.Frame(tbl, bg=BORDER)
    hdr_row.pack(fill="x")
    for h, w in zip(headers, widths):
        tk.Label(hdr_row, text=h, font=("Courier New", 9, "bold"),
                 bg=BORDER, fg=MUTED, width=w,
                 anchor="w").pack(side="left", padx=2)

    for r in ptp_results:
        row = tk.Frame(tbl, bg=PANEL)
        row.pack(fill="x", pady=1)

        if 'error' in r:
            tk.Label(row, text=r['name'][:22], font=FONT_SM,
                     bg=PANEL, fg=WARN, width=24,
                     anchor="w").pack(side="left", padx=2)
            tk.Label(row, text="(could not read)", font=FONT_SM,
                     bg=PANEL, fg=WARN).pack(side="left", padx=2)
            continue

        ok     = r['ok']
        color  = ACCENT2 if ok else WARN
        status = "OK" if ok else "WARN"

        vals = [
            r['name'][:22],
            str(r['n_samples']),
            f"{r['ptp4l_max_ns']/1000:.1f} µs",
            f"{r['phc2sys_max_ns']/1000:.1f} µs",
            f"{r['combined_max_ns']/1000:.1f} µs",
            status,
        ]
        for val, w in zip(vals, widths):
            tk.Label(row, text=val, font=FONT_SM,
                     bg=PANEL, fg=color if val == status else TEXT,
                     width=w, anchor="w").pack(side="left", padx=2)

    # Interpretation
    all_ok = all(r.get('ok', False) for r in ptp_results)
    if all_ok:
        note = ("All modules are within threshold. This warning should not appear — "
                "please report this.")
        note_color = ACCENT2
    else:
        worst = max((r for r in ptp_results if r.get('ok') is not None),
                    key=lambda r: r.get('combined_max_ns', 0),
                    default=None)
        if worst:
            frames_30fps = worst['combined_max_ns'] / 1e6 / 33.3
            note = (f"Worst combined offset: {worst['combined_max_ns']/1000:.1f} µs  "
                    f"≈ {frames_30fps:.3f} frames at 30fps.\n"
                    f"This may affect per-frame alignment accuracy.")
        else:
            note = "Could not determine worst-case offset."
        note_color = WARN

    tk.Label(win, text=note, font=("Courier New", 9),
             bg=BG, fg=note_color, justify="center",
             wraplength=640).pack(padx=20, pady=10)

    # Buttons
    btn_row = tk.Frame(win, bg=BG)
    btn_row.pack(pady=10)

    def _proceed():
        proceed.set(True)
        win.destroy()

    def _abort():
        proceed.set(False)
        win.destroy()

    tk.Button(btn_row, text="Proceed Anyway",
              font=("Courier New", 10, "bold"),
              bg=WARN, fg=BG, relief="flat",
              padx=16, pady=6, cursor="hand2",
              command=_proceed).pack(side="left", padx=8)
    tk.Button(btn_row, text="Abort",
              font=("Courier New", 10),
              bg=BORDER, fg=TEXT, relief="flat",
              padx=16, pady=6, cursor="hand2",
              command=_abort).pack(side="left", padx=8)

    win.wait_window()
    return proceed.get()


def _read_sample_rate(ttl_dir):
    """
    Try to read sample_rate from structure.oebin by searching upward from ttl_dir.
    Returns float sample rate if found, or None.
    """
    # Walk upward looking for structure.oebin
    path = ttl_dir
    for _ in range(8):
        candidate = os.path.join(path, 'structure.oebin')
        if os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    oebin = json.load(f)
                stream = next(
                    (s for s in oebin.get('continuous', [])
                     if s.get('stream_name') == 'acquisition_board'), None)
                if stream:
                    return float(stream['sample_rate'])
            except Exception:
                pass
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


# =============================================================================
# GUI
# =============================================================================

class AlignmentTool(tk.Tk):

    FONT    = ("Courier New", 10)
    FONT_LG = ("Courier New", 13, "bold")
    FONT_SM = ("Courier New", 9)

    def __init__(self):
        super().__init__()
        self.title("SAVIOUR ↔ OpenEphys  |  Alignment Fitter")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("920x860")

        self.oe_root   = tk.StringVar()
        self.sv_root   = tk.StringVar()
        self.sv_pin    = tk.IntVar(value=19)
        self.sv_invert = tk.BooleanVar(value=True)
        self.ptp_threshold_us = tk.IntVar(value=1000)  # µs, default 1ms
        self.save_path = tk.StringVar(
            value=os.path.join(SCRIPT_DIR, "model.json"))
        self.result    = None

        self._build_ui()
        self._auto_detect()

    # ── startup auto-detection ────────────────────────────────────────────────

    def _auto_detect(self):
        self.status_label.config(
            text="Searching for data folders...", fg=MUTED)
        self.update_idletasks()

        def _search():
            oe, sv = auto_detect_roots(SCRIPT_DIR)
            self.after(0, lambda: self._apply_auto_detect(oe, sv))

        threading.Thread(target=_search, daemon=True).start()

    def _apply_auto_detect(self, oe_root, sv_root):
        found = []
        if oe_root:
            self.oe_root.set(oe_root)
            found.append("OpenEphys")
        if sv_root:
            self.sv_root.set(sv_root)
            found.append("SAVIOUR")
        if found:
            self.status_label.config(
                text=f"Auto-detected: {', '.join(found)}", fg=ACCENT2)
        else:
            self.status_label.config(
                text="No data folders found — please browse.", fg=MUTED)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=20, pady=(18, 4))
        tk.Label(hdr, text="ALIGNMENT FITTER",
                 font=("Courier New", 16, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="SAVIOUR ↔ OpenEphys clock registration",
                 font=self.FONT_SM, bg=BG, fg=MUTED).pack(
                     side="left", padx=12)

        self._divider()
        self._section("1  INPUT FOLDERS")

        self._path_row("OpenEphys recording folder", self.oe_root,
                        lambda: self._browse_dir(self.oe_root))
        self.oe_found_label = tk.Label(self, text="", font=self.FONT_SM,
                                        bg=BG, fg=MUTED)
        self.oe_found_label.pack(anchor="w", padx=52)
        self.oe_root.trace_add("write", self._on_oe_root_changed)

        self._path_row("SAVIOUR session folder", self.sv_root,
                        lambda: self._browse_dir(self.sv_root))
        self.sv_found_label = tk.Label(self, text="", font=self.FONT_SM,
                                        bg=BG, fg=MUTED)
        self.sv_found_label.pack(anchor="w", padx=52)
        self.sv_root.trace_add("write", self._on_sv_root_changed)

        opts = tk.Frame(self, bg=BG)
        opts.pack(fill="x", padx=24, pady=4)
        tk.Label(opts, text="SAVIOUR sync pin:", font=self.FONT_SM,
                 bg=BG, fg=MUTED).pack(side="left")
        tk.Spinbox(opts, from_=1, to=40, textvariable=self.sv_pin,
                   width=5, font=self.FONT, bg=PANEL, fg=TEXT,
                   buttonbackground=PANEL, insertbackground=TEXT,
                   relief="flat").pack(side="left", padx=8)
        tk.Checkbutton(opts, text="Invert polarity",
                       variable=self.sv_invert,
                       font=self.FONT_SM, bg=BG, fg=MUTED,
                       activebackground=BG, selectcolor=PANEL,
                       activeforeground=TEXT).pack(side="left", padx=16)
        tk.Label(opts, text="PTP threshold:", font=self.FONT_SM,
                 bg=BG, fg=MUTED).pack(side="left", padx=(24, 0))
        tk.Spinbox(opts, from_=100, to=10000, increment=100,
                   textvariable=self.ptp_threshold_us, width=6,
                   font=self.FONT, bg=PANEL, fg=TEXT,
                   buttonbackground=PANEL, insertbackground=TEXT,
                   relief="flat").pack(side="left", padx=4)
        tk.Label(opts, text="µs", font=self.FONT_SM,
                 bg=BG, fg=MUTED).pack(side="left")

        self._divider()
        self._section("2  OUTPUT")
        self._path_row("Save model to (.json)", self.save_path,
                        lambda: self._browse_save(self.save_path))

        self._divider()

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=10)
        self.run_btn = tk.Button(
            btn_frame, text="FIT ALIGNMENT MODEL",
            font=("Courier New", 11, "bold"),
            bg=ACCENT, fg=BG, activebackground="#81d4fa",
            relief="flat", padx=24, pady=8, cursor="hand2",
            command=self._run)
        self.run_btn.pack()

        self._divider()

        # PTP health panel
        self._section("3  PTP SYNC HEALTH")
        ptp_outer = tk.Frame(self, bg=PANEL)
        ptp_outer.pack(fill="x", padx=24, pady=4)
        self.ptp_frame = tk.Frame(ptp_outer, bg=PANEL)
        self.ptp_frame.pack(fill="x", padx=12, pady=8)
        tk.Label(self.ptp_frame,
                 text="PTP health will be checked before fitting.",
                 font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w")

        self._divider()
        self._section("4  ALIGNMENT RESULTS")

        stats_outer = tk.Frame(self, bg=PANEL)
        stats_outer.pack(fill="x", padx=24, pady=4)
        self.stats_frame = tk.Frame(stats_outer, bg=PANEL)
        self.stats_frame.pack(fill="x", padx=12, pady=8)
        tk.Label(self.stats_frame,
                 text="Run the fitter to see alignment statistics.",
                 font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w")

        self.plot_frame = tk.Frame(self, bg=BG)
        self.plot_frame.pack(fill="both", expand=True, padx=24, pady=(4, 4))

        self.status_label = tk.Label(self, text="", font=self.FONT_SM,
                                      bg=BG, fg=MUTED)
        self.status_label.pack(pady=4)

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

    # ── discovery callbacks ───────────────────────────────────────────────────

    def _on_oe_root_changed(self, *_):
        root = self.oe_root.get()
        if not root or not os.path.isdir(root):
            self.oe_found_label.config(text="", fg=MUTED)
            return
        try:
            ttl_dir = find_oe_ttl_dir(root)
            rel     = os.path.relpath(ttl_dir, root)
            self.oe_found_label.config(
                text=f"✓  TTL folder: ...{os.sep}{rel}", fg=ACCENT2)
        except FileNotFoundError:
            self.oe_found_label.config(
                text="✗  No TTL folder found", fg=WARN)

    def _on_sv_root_changed(self, *_):
        root = self.sv_root.get()
        if not root or not os.path.isdir(root):
            self.sv_found_label.config(text="", fg=MUTED)
            return
        try:
            csv_path = find_saviour_ttl_csv(root)
            rel      = os.path.relpath(csv_path, root)
            self.sv_found_label.config(
                text=f"✓  TTL CSV: ...{os.sep}{rel}", fg=ACCENT2)
        except FileNotFoundError:
            self.sv_found_label.config(
                text="✗  No SAVIOUR TTL CSV found", fg=WARN)

    # ── browse helpers ────────────────────────────────────────────────────────

    def _browse_dir(self, var):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            var.set(d)

    def _browse_save(self, var):
        f = filedialog.asksaveasfilename(
            title="Save alignment model",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialdir=SCRIPT_DIR,
            initialfile="model.json")
        if f:
            var.set(f)

    # ── run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if not self.oe_root.get():
            messagebox.showerror("Missing input",
                "Please select the OpenEphys recording folder.")
            return
        if not self.sv_root.get():
            messagebox.showerror("Missing input",
                "Please select the SAVIOUR session folder.")
            return
        self.run_btn.config(state="disabled", text="CHECKING PTP...")
        self.status_label.config(text="Checking PTP sync health...", fg=MUTED)
        threading.Thread(target=self._ptp_then_fit_thread, daemon=True).start()

    def _ptp_then_fit_thread(self):
        """Check PTP health first, prompt user if needed, then fit."""
        log_path = os.path.join(SCRIPT_DIR, "tool1_error.txt")
        try:
            with open(log_path, "w") as f:
                f.write("Starting PTP check\n")

            # Load SAVIOUR TTL to get sv_t0 for the recording window
            csv_path = find_saviour_ttl_csv(self.sv_root.get())
            sv_t, sv_s, sv_t0 = load_saviour_ttl(
                csv_path, self.sv_pin.get(), self.sv_invert.get())
            rec_dur_s = float(sv_t[-1] - sv_t[0]) + 5.0

            threshold_ns = int(self.ptp_threshold_us.get()) * 1000

            ptp_results = check_ptp_health(
                self.sv_root.get(), sv_t0, rec_dur_s,
                threshold_ns=threshold_ns)

            with open(log_path, "a") as f:
                f.write(f"PTP check: {len(ptp_results)} health files found\n")
                for r in ptp_results:
                    f.write(f"  {r.get('name','?')}: "
                            f"combined_max={r.get('combined_max_ns','?')} ns "
                            f"ok={r.get('ok','?')}\n")

            any_bad = any(
                r.get('ok') is False for r in ptp_results)
            no_files = len(ptp_results) == 0

            # Update PTP panel on main thread
            self.after(0, lambda: self._show_ptp_results(ptp_results))

            if no_files:
                # No health files — warn once then proceed
                proceed = self.after_idle_result(
                    lambda: messagebox.askokcancel(
                        "No health files found",
                        "No SAVIOUR health metadata CSVs were found under the "
                        "session folder.\nCannot verify PTP sync quality.\n\n"
                        "Proceed with fitting anyway?"))
                if not proceed:
                    self.after(0, self._reset_run_btn)
                    return

            elif any_bad:
                # Bad PTP — show warning dialog and let user decide
                # Must run on main thread (tkinter modal)
                result_holder = [None]

                def _show_dialog():
                    result_holder[0] = show_ptp_warning(
                        self, ptp_results,
                        threshold_us=self.ptp_threshold_us.get())

                self.after(0, _show_dialog)

                # Wait for dialog to close
                import time
                while result_holder[0] is None:
                    time.sleep(0.05)

                if not result_holder[0]:
                    self.after(0, self._reset_run_btn)
                    self.after(0, lambda: self.status_label.config(
                        text="Aborted — PTP quality below threshold.", fg=WARN))
                    return

            # PTP OK or user chose to proceed — run fit
            self.after(0, lambda: self.run_btn.config(
                text="FITTING..."))
            self.after(0, lambda: self.status_label.config(
                text="Fitting alignment model...", fg=MUTED))
            self._fit_thread(csv_path, sv_t, sv_s, sv_t0, log_path)

        except Exception:
            tb = traceback.format_exc()
            with open(log_path, "a") as f:
                f.write(f"\nEXCEPTION:\n{tb}\n")
            self.after(0, lambda t=tb: messagebox.showerror(
                "Error", f"See log: {log_path}\n\n{t[:400]}"))
            self.after(0, self._reset_run_btn)

    def _reset_run_btn(self):
        self.run_btn.config(state="normal", text="FIT ALIGNMENT MODEL")

    def _show_ptp_results(self, ptp_results):
        """Populate the PTP health panel with results."""
        for w in self.ptp_frame.winfo_children():
            w.destroy()

        if not ptp_results:
            tk.Label(self.ptp_frame,
                     text="No health metadata files found.",
                     font=self.FONT_SM, bg=PANEL, fg=MUTED).pack(anchor="w")
            return

        headers = ["Module", "Samples", "ptp4l max", "phc2sys max",
                   "Combined max", "Status"]
        widths  = [26, 8, 12, 14, 14, 8]

        hdr_row = tk.Frame(self.ptp_frame, bg=BORDER)
        hdr_row.pack(fill="x")
        for h, w in zip(headers, widths):
            tk.Label(hdr_row, text=h,
                     font=("Courier New", 9, "bold"),
                     bg=BORDER, fg=MUTED, width=w,
                     anchor="w").pack(side="left", padx=2)

        for r in ptp_results:
            row = tk.Frame(self.ptp_frame, bg=PANEL)
            row.pack(fill="x", pady=1)

            if 'error' in r:
                tk.Label(row, text=r['name'][:24],
                         font=self.FONT_SM, bg=PANEL, fg=WARN,
                         width=26, anchor="w").pack(side="left", padx=2)
                tk.Label(row, text="(error reading file)",
                         font=self.FONT_SM, bg=PANEL,
                         fg=WARN).pack(side="left", padx=2)
                continue

            ok     = r['ok']
            status = "✓ OK" if ok else "⚠ WARN"
            color  = ACCENT2 if ok else WARN

            vals = [
                r['name'][:24],
                str(r['n_samples']),
                f"{r['ptp4l_max_ns']/1000:.1f} µs",
                f"{r['phc2sys_max_ns']/1000:.1f} µs",
                f"{r['combined_max_ns']/1000:.1f} µs",
                status,
            ]
            for val, w in zip(vals, widths):
                fg = color if val == status else TEXT
                tk.Label(row, text=val, font=self.FONT_SM,
                         bg=PANEL, fg=fg, width=w,
                         anchor="w").pack(side="left", padx=2)

    def _fit_thread(self, csv_path, sv_t, sv_s, sv_t0, log_path):
        """Run the alignment fit (called after PTP check passes)."""
        try:
            ttl_dir = find_oe_ttl_dir(self.oe_root.get())
            with open(log_path, "a") as f:
                f.write(f"Starting fit\nOE TTL : {ttl_dir}\n"
                        f"SV CSV : {csv_path}\n")

            oe_t, oe_s = load_oe_ttl(ttl_dir)
            with open(log_path, "a") as f:
                f.write(f"OE {len(oe_t)} events  SV {len(sv_t)} events\n")

            scale, offset, r2, residuals, sv_edges, meta = fit_alignment(
                oe_t, oe_s, sv_t, sv_s)
            with open(log_path, "a") as f:
                f.write(f"Fit: scale={scale:.10f} offset={offset:.6f} "
                        f"r2={r2:.10f}\n")

            # Try to read sample rate from structure.oebin
            sample_rate = _read_sample_rate(ttl_dir)

            self.result = {
                "sv_t0":       sv_t0,
                "scale":       scale,
                "offset":      offset,
                "r2":          r2,
                "sample_rate": sample_rate,
                "meta":        meta,
                "sources": {
                    "oe_recording_root": self.oe_root.get(),
                    "oe_ttl_dir":        ttl_dir,
                    "sv_session_root":   self.sv_root.get(),
                    "sv_ttl_csv":        csv_path,
                    "sv_pin":            self.sv_pin.get(),
                    "sv_invert":         self.sv_invert.get(),
                }
            }

            with open(self.save_path.get(), "w") as f:
                json.dump(self.result, f, indent=2)
            with open(log_path, "a") as f:
                f.write(f"Saved: {self.save_path.get()}\n")

            self.after(0, lambda: self._show_results(
                scale, offset, r2, residuals, sv_edges, meta,
                ttl_dir, csv_path))

        except Exception:
            tb = traceback.format_exc()
            with open(log_path, "a") as f:
                f.write(f"\nFIT EXCEPTION:\n{tb}\n")
            self.after(0, lambda t=tb: messagebox.showerror(
                "Fit failed", f"See log: {log_path}\n\n{t[:400]}"))
            self.after(0, self._reset_run_btn)
            self.after(0, lambda: self.status_label.config(
                text="Failed — see tool1_error.txt", fg=WARN))

    # ── results ───────────────────────────────────────────────────────────────

    def _show_results(self, scale, offset, r2, residuals,
                       sv_edges, meta, ttl_dir, csv_path):
        for w in self.stats_frame.winfo_children():
            w.destroy()

        ok  = ACCENT2
        bad = WARN

        rows = [
            ("Scale (clock ratio)",    f"{scale:.10f}",                      ok),
            ("Offset",                 f"{offset:.6f} s",                    ok),
            ("R²",                     f"{r2:.10f}",
             ok if r2 > 0.9999999 else bad),
            ("Clock drift",            f"{meta['drift_ms']:.3f} ms",         ok),
            ("IPI correlation",        f"{meta['ipi_correlation']:.6f}",
             ok if meta['ipi_correlation'] > 0.999 else bad),
            ("Rising edges (OE / SV)", f"{meta['oe_rising_edges']} / "
                                       f"{meta['sv_rising_edges']}",         ok),
            ("Residual std",           f"{meta['residual_std_ms']:.4f} ms",
             ok if meta['residual_max_ms'] < 2.0 else bad),
            ("Residual max",           f"{meta['residual_max_ms']:.4f} ms",
             ok if meta['residual_max_ms'] < 2.0 else bad),
            ("OE TTL",                 ttl_dir,                              MUTED),
            ("SV CSV",                 os.path.basename(csv_path),           MUTED),
            ("Model saved",            self.save_path.get(),                 MUTED),
        ]

        for label, value, color in rows:
            r = tk.Frame(self.stats_frame, bg=PANEL)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=f"{label}:", font=self.FONT_SM,
                     bg=PANEL, fg=MUTED, width=26,
                     anchor="w").pack(side="left")
            tk.Label(r, text=value, font=self.FONT_SM,
                     bg=PANEL, fg=color,
                     wraplength=600, anchor="w",
                     justify="left").pack(side="left")

        # Plots
        for w in self.plot_frame.winfo_children():
            w.destroy()

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 2.4),
                                        facecolor=BG)
        for ax in (ax1, ax2):
            ax.set_facecolor(PANEL)
            ax.tick_params(colors=MUTED, labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)

        ax1.plot(sv_edges, residuals * 1000, color=ACCENT, linewidth=0.8)
        ax1.axhline(0, color=BORDER, linewidth=0.6, linestyle='--')
        ax1.set_title("Residuals over time", color=TEXT, fontsize=8)
        ax1.set_xlabel("SAVIOUR time (s)", color=MUTED, fontsize=7)
        ax1.set_ylabel("Residual (ms)", color=MUTED, fontsize=7)

        ax2.hist(residuals * 1000, bins=30, color=ACCENT2,
                 alpha=0.8, edgecolor='none')
        ax2.set_title("Residual distribution", color=TEXT, fontsize=8)
        ax2.set_xlabel("Residual (ms)", color=MUTED, fontsize=7)
        ax2.set_ylabel("Count", color=MUTED, fontsize=7)

        fig.tight_layout(pad=1.2)
        canvas = FigureCanvasTkAgg(fig, master=self.plot_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        plt.close(fig)

        self.status_label.config(
            text="✓  Alignment model fitted and saved.", fg=ACCENT2)
        self.run_btn.config(state="normal", text="FIT ALIGNMENT MODEL")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    app = AlignmentTool()
    app.mainloop()