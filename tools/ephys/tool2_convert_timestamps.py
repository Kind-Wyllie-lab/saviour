# -*- coding: utf-8 -*-
"""
tool2_convert_timestamps.py
============================
SAVIOUR ↔ OpenEphys  |  Timestamp Converter

Load a model JSON produced by tool1_fit_alignment.py, then convert
timestamps in either direction:

  SAVIOUR Unix nanoseconds  →  OpenEphys seconds + sample number
  OpenEphys seconds         →  SAVIOUR Unix nanoseconds

Also accepts batch input (one timestamp per line) and exports results to CSV.

Dependencies: numpy, tkinter (built-in)
"""

import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
import os
import csv
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BG      = "#0f1117"
PANEL   = "#1a1d27"
PANEL2  = "#13161f"
BORDER  = "#2a2d3a"
ACCENT  = "#4fc3f7"
ACCENT2 = "#81c784"
ACCENT3 = "#ffb74d"
WARN    = "#ef9a9a"
TEXT    = "#e8eaf6"
MUTED   = "#7986cb"


# =============================================================================
# CONVERSION FUNCTIONS
# =============================================================================

def saviour_ns_to_oe_seconds(saviour_unix_ns, sv_t0, scale, offset):
    """
    Convert SAVIOUR Unix nanosecond timestamp(s) → OpenEphys seconds.

    Steps:
      1. ns → relative seconds:  sv_rel = (ns / 1e9) - sv_t0
      2. Apply regression:        t_OE  = scale * sv_rel + offset
    """
    sv_rel = (np.asarray(saviour_unix_ns, dtype=np.float64) / 1e9) - sv_t0
    return scale * sv_rel + offset


def oe_seconds_to_saviour_ns(oe_time_s, sv_t0, scale, offset):
    """
    Convert OpenEphys second timestamp(s) → SAVIOUR Unix nanoseconds.

    Steps (inverted regression):
      1. sv_rel         = (t_OE - offset) / scale
      2. saviour_unix_s = sv_rel + sv_t0
      3. ns             = saviour_unix_s * 1e9
    """
    sv_rel         = (np.asarray(oe_time_s, dtype=np.float64) - offset) / scale
    saviour_unix_s = sv_rel + sv_t0
    return (saviour_unix_s * 1e9).astype(np.int64)


def oe_seconds_to_sample_number(oe_time_s, sample_rate):
    """Convert OE seconds → nearest sample number (zero-indexed from recording start)."""
    return int(round(float(oe_time_s) * sample_rate))


def sample_number_to_oe_seconds(sample_number, sample_rate):
    """Convert sample number → OE seconds."""
    return float(sample_number) / sample_rate


def format_oe_time(seconds):
    """Format OE seconds as MM:SS.mmm for display."""
    mm, ss = divmod(float(seconds), 60)
    return f"{int(mm):02d}:{ss:06.3f}"


def format_saviour_ns(ns):
    """Format SAVIOUR Unix ns as UTC datetime + raw ns for display."""
    try:
        unix_s = int(ns) / 1e9
        dt = datetime.datetime.utcfromtimestamp(unix_s)
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S.%f')} UTC"
    except Exception:
        return str(ns)


# =============================================================================
# GUI
# =============================================================================

class ConverterTool(tk.Tk):

    FONT      = ("Courier New", 10)
    FONT_LG   = ("Courier New", 13, "bold")
    FONT_SM   = ("Courier New", 9)
    FONT_MONO = ("Courier New", 10)

    def __init__(self):
        super().__init__()
        self.title("SAVIOUR ↔ OpenEphys  |  Timestamp Converter")
        self.configure(bg=BG)
        self.geometry("860x800")
        self.resizable(True, True)

        self.model      = None
        self.model_path = tk.StringVar()
        self.model_info = tk.StringVar(value="No model loaded")

        # Sample rate — loaded from model if available, editable as fallback
        self.sample_rate = tk.DoubleVar(value=3000.0)

        self._build_ui()
        self._try_auto_load_model()

    # ── auto-load model from script dir ──────────────────────────────────────

    def _try_auto_load_model(self):
        default = os.path.join(SCRIPT_DIR, "model.json")
        if os.path.exists(default):
            try:
                with open(default) as f:
                    self.model = json.load(f)
                self.model_path.set(default)
                self._show_model_info()
            except Exception:
                pass

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=20, pady=(18, 4))
        tk.Label(hdr, text="TIMESTAMP CONVERTER",
                 font=("Courier New", 16, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="SAVIOUR ↔ OpenEphys",
                 font=self.FONT_SM, bg=BG, fg=MUTED).pack(side="left", padx=12)

        self._divider()

        # ── Model loader ──────────────────────────────────────────────────────
        self._section("1  LOAD ALIGNMENT MODEL")
        model_row = tk.Frame(self, bg=BG)
        model_row.pack(fill="x", padx=24, pady=4)
        tk.Entry(model_row, textvariable=self.model_path,
                 font=self.FONT_SM, bg=PANEL, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=4
                 ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(model_row, text="Browse", font=self.FONT_SM,
                  bg=BORDER, fg=TEXT, relief="flat",
                  activebackground=MUTED, cursor="hand2",
                  command=self._load_model).pack(side="left")

        info_frame = tk.Frame(self, bg=PANEL)
        info_frame.pack(fill="x", padx=24, pady=4)
        self.info_labels_frame = tk.Frame(info_frame, bg=PANEL)
        self.info_labels_frame.pack(fill="x", padx=12, pady=8)
        tk.Label(self.info_labels_frame, textvariable=self.model_info,
                 font=self.FONT_SM, bg=PANEL, fg=MUTED,
                 justify="left").pack(anchor="w")

        # Sample rate row
        sr_row = tk.Frame(self, bg=BG)
        sr_row.pack(fill="x", padx=24, pady=2)
        tk.Label(sr_row, text="Sample rate (Hz):", font=self.FONT_SM,
                 bg=BG, fg=MUTED, width=20, anchor="w").pack(side="left")
        self.sr_entry = tk.Entry(sr_row, textvariable=self.sample_rate,
                                  font=self.FONT_SM, bg=PANEL, fg=TEXT,
                                  insertbackground=TEXT,
                                  relief="flat", bd=4, width=10)
        self.sr_entry.pack(side="left", padx=4)
        self.sr_note = tk.Label(sr_row, text="(loaded from model)",
                                 font=self.FONT_SM, bg=BG, fg=MUTED)
        self.sr_note.pack(side="left", padx=6)

        self._divider()

        # ── Direction toggle ──────────────────────────────────────────────────
        self._section("2  CONVERSION DIRECTION")
        dir_frame = tk.Frame(self, bg=BG)
        dir_frame.pack(fill="x", padx=24, pady=4)

        self.direction = tk.StringVar(value="sv_to_oe")

        tk.Radiobutton(
            dir_frame,
            text="SAVIOUR ns  →  OE seconds + sample number",
            variable=self.direction, value="sv_to_oe",
            font=self.FONT, bg=BG, fg=ACCENT2,
            activebackground=BG, selectcolor=PANEL,
            activeforeground=ACCENT2,
            command=self._update_direction_labels
        ).pack(side="left", padx=(0, 24))

        tk.Radiobutton(
            dir_frame,
            text="OE seconds  →  SAVIOUR ns",
            variable=self.direction, value="oe_to_sv",
            font=self.FONT, bg=BG, fg=ACCENT3,
            activebackground=BG, selectcolor=PANEL,
            activeforeground=ACCENT3,
            command=self._update_direction_labels
        ).pack(side="left")

        self._divider()

        # ── Single timestamp ──────────────────────────────────────────────────
        self._section("3  SINGLE TIMESTAMP")

        single_frame = tk.Frame(self, bg=PANEL)
        single_frame.pack(fill="x", padx=24, pady=4)
        inner = tk.Frame(single_frame, bg=PANEL)
        inner.pack(fill="x", padx=12, pady=10)

        input_row = tk.Frame(inner, bg=PANEL)
        input_row.pack(fill="x", pady=3)
        self.input_label = tk.Label(input_row, text="SAVIOUR timestamp (ns):",
                                     font=self.FONT_SM, bg=PANEL, fg=MUTED,
                                     width=28, anchor="w")
        self.input_label.pack(side="left")
        self.single_input = tk.Entry(input_row, font=self.FONT_MONO,
                                      bg=PANEL2, fg=TEXT,
                                      insertbackground=TEXT,
                                      relief="flat", bd=4, width=36)
        self.single_input.pack(side="left", padx=4)
        tk.Button(input_row, text="Convert", font=self.FONT_SM,
                  bg=ACCENT, fg=BG, relief="flat",
                  activebackground="#81d4fa", cursor="hand2",
                  command=self._convert_single).pack(side="left", padx=8)

        # OE seconds output
        result_row = tk.Frame(inner, bg=PANEL)
        result_row.pack(fill="x", pady=3)
        self.output_label = tk.Label(result_row, text="OE time (seconds):",
                                      font=self.FONT_SM, bg=PANEL, fg=MUTED,
                                      width=28, anchor="w")
        self.output_label.pack(side="left")
        self.single_output = tk.Entry(result_row, font=self.FONT_MONO,
                                       bg=PANEL2, fg=ACCENT2,
                                       insertbackground=TEXT,
                                       relief="flat", bd=4, width=24,
                                       state="readonly")
        self.single_output.pack(side="left", padx=4)

        # Sample number output
        sample_row = tk.Frame(inner, bg=PANEL)
        sample_row.pack(fill="x", pady=3)
        self.sample_label = tk.Label(sample_row, text="OE sample number:",
                                      font=self.FONT_SM, bg=PANEL, fg=MUTED,
                                      width=28, anchor="w")
        self.sample_label.pack(side="left")
        self.sample_output = tk.Entry(sample_row, font=self.FONT_MONO,
                                       bg=PANEL2, fg=ACCENT,
                                       insertbackground=TEXT,
                                       relief="flat", bd=4, width=16,
                                       state="readonly")
        self.sample_output.pack(side="left", padx=4)

        # Formatted result
        fmt_row = tk.Frame(inner, bg=PANEL)
        fmt_row.pack(fill="x", pady=1)
        tk.Label(fmt_row, text="", width=28, bg=PANEL).pack(side="left")
        self.formatted_label = tk.Label(fmt_row, text="",
                                         font=self.FONT_SM, bg=PANEL,
                                         fg=MUTED)
        self.formatted_label.pack(side="left")

        self._divider()

        # ── Batch conversion ──────────────────────────────────────────────────
        self._section("4  BATCH CONVERSION")

        batch_outer = tk.Frame(self, bg=PANEL)
        batch_outer.pack(fill="both", expand=True, padx=24, pady=4)
        batch_inner = tk.Frame(batch_outer, bg=PANEL)
        batch_inner.pack(fill="both", expand=True, padx=12, pady=8)

        self.batch_label = tk.Label(batch_inner,
                                     text="Enter SAVIOUR timestamps (ns), one per line:",
                                     font=self.FONT_SM, bg=PANEL, fg=MUTED,
                                     anchor="w")
        self.batch_label.pack(anchor="w")

        text_frame = tk.Frame(batch_inner, bg=PANEL)
        text_frame.pack(fill="both", expand=True, pady=4)
        self.batch_text = tk.Text(text_frame, font=self.FONT_MONO,
                                   bg=PANEL2, fg=TEXT,
                                   insertbackground=TEXT,
                                   relief="flat", bd=4, height=5)
        self.batch_text.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(text_frame, command=self.batch_text.yview, bg=PANEL)
        sb.pack(side="right", fill="y")
        self.batch_text.config(yscrollcommand=sb.set)

        btn_row = tk.Frame(batch_inner, bg=PANEL)
        btn_row.pack(fill="x", pady=4)
        tk.Button(btn_row, text="Convert Batch", font=self.FONT_SM,
                  bg=ACCENT, fg=BG, relief="flat",
                  activebackground="#81d4fa", cursor="hand2",
                  command=self._convert_batch).pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Export CSV", font=self.FONT_SM,
                  bg=BORDER, fg=TEXT, relief="flat",
                  activebackground=MUTED, cursor="hand2",
                  command=self._export_csv).pack(side="left")
        self.batch_status = tk.Label(btn_row, text="",
                                      font=self.FONT_SM, bg=PANEL, fg=MUTED)
        self.batch_status.pack(side="left", padx=12)

        # Results table — 4 columns when sv_to_oe, 3 when oe_to_sv
        cols = ("input", "oe_seconds", "oe_sample", "formatted")
        self.tree = ttk.Treeview(batch_inner, columns=cols,
                                  show="headings", height=5)
        self._style_tree()
        self.tree.pack(fill="both", expand=True, pady=4)

        self._update_direction_labels()
        self.batch_results = []

    # ── helpers ───────────────────────────────────────────────────────────────

    def _divider(self):
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=6)

    def _section(self, title):
        tk.Label(self, text=title, font=self.FONT_LG,
                 bg=BG, fg=ACCENT).pack(anchor="w", padx=24, pady=(6, 2))

    def _style_tree(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview",
                         background=PANEL2, foreground=TEXT,
                         fieldbackground=PANEL2, font=self.FONT_SM,
                         rowheight=22)
        style.configure("Treeview.Heading",
                         background=PANEL, foreground=MUTED,
                         font=("Courier New", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", BORDER)])

        self.tree.heading("input",     text="Input")
        self.tree.heading("oe_seconds", text="OE (seconds)")
        self.tree.heading("oe_sample",  text="OE sample #")
        self.tree.heading("formatted",  text="Formatted")
        self.tree.column("input",      width=180, anchor="e")
        self.tree.column("oe_seconds", width=120, anchor="e")
        self.tree.column("oe_sample",  width=110, anchor="e")
        self.tree.column("formatted",  width=260, anchor="w")

    def _update_direction_labels(self):
        if self.direction.get() == "sv_to_oe":
            self.input_label.config(text="SAVIOUR timestamp (ns):")
            self.output_label.config(text="OE time (seconds):")
            self.sample_label.config(text="OE sample number:")
            self.sample_output.config(state="normal")
            self.sample_label.pack()
            self.sample_output.pack()
            self.batch_label.config(
                text="Enter SAVIOUR timestamps (ns), one per line:")
            self.tree.heading("input",      text="SAVIOUR (ns)")
            self.tree.heading("oe_seconds", text="OE (seconds)")
            self.tree.heading("oe_sample",  text="OE sample #")
            self.tree.heading("formatted",  text="OE (MM:SS.mmm)")
        else:
            self.input_label.config(text="OE timestamp (seconds):")
            self.output_label.config(text="SAVIOUR Unix (ns):")
            self.sample_label.config(text="")
            self.sample_output.config(state="readonly")
            self.batch_label.config(
                text="Enter OE timestamps (seconds), one per line:")
            self.tree.heading("input",      text="OE (seconds)")
            self.tree.heading("oe_seconds", text="SAVIOUR (ns)")
            self.tree.heading("oe_sample",  text="")
            self.tree.heading("formatted",  text="SAVIOUR (UTC datetime)")

    # ── model loading ─────────────────────────────────────────────────────────

    def _load_model(self):
        path = filedialog.askopenfilename(
            title="Load alignment model",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialdir=SCRIPT_DIR)
        if not path:
            return
        try:
            with open(path) as f:
                self.model = json.load(f)
            self.model_path.set(path)
            self._show_model_info()
        except Exception as e:
            messagebox.showerror("Error loading model", str(e))

    def _show_model_info(self):
        for w in self.info_labels_frame.winfo_children():
            w.destroy()

        m    = self.model
        meta = m.get("meta", {})

        # Load sample rate from model if present
        if m.get("sample_rate"):
            self.sample_rate.set(float(m["sample_rate"]))
            self.sr_note.config(text="(loaded from model)", fg=ACCENT2)
        else:
            self.sr_note.config(
                text="(not in model — edit if needed)", fg=WARN)

        rows = [
            ("Scale",          f"{m['scale']:.10f}"),
            ("Offset",         f"{m['offset']:.6f} s"),
            ("R²",             f"{m['r2']:.10f}"),
            ("Sample rate",    f"{m.get('sample_rate', 'unknown')} Hz"),
            ("Drift",          f"{meta.get('drift_ms', '?')} ms"),
            ("Residual max",   f"{meta.get('residual_max_ms', '?')} ms"),
            ("SAVIOUR t0",     f"{m['sv_t0']:.3f} (Unix s)"),
            ("OE TTL dir",     m.get('sources', {}).get('oe_ttl_dir', '?')),
            ("SV CSV",         m.get('sources', {}).get('sv_ttl_csv', '?')),
        ]
        for label, value in rows:
            r = tk.Frame(self.info_labels_frame, bg=PANEL)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=f"{label}:", font=self.FONT_SM,
                     bg=PANEL, fg=MUTED, width=16,
                     anchor="w").pack(side="left")
            tk.Label(r, text=value, font=self.FONT_SM,
                     bg=PANEL, fg=TEXT,
                     wraplength=580, anchor="w",
                     justify="left").pack(side="left")

        self.model_info.set("")

    def _require_model(self):
        if self.model is None:
            messagebox.showwarning("No model",
                "Please load an alignment model first.")
            return False
        return True

    # ── conversion ────────────────────────────────────────────────────────────

    def _do_convert(self, value_str):
        """
        Convert a single value string.
        Returns (oe_seconds_str, sample_number_str, formatted_str)
        for sv_to_oe, or (saviour_ns_str, '', formatted_str) for oe_to_sv.
        """
        m   = self.model
        sr  = float(self.sample_rate.get())
        val = float(value_str.strip().replace(",", ""))

        if self.direction.get() == "sv_to_oe":
            oe_s   = float(saviour_ns_to_oe_seconds(
                val, m['sv_t0'], m['scale'], m['offset']))
            samp   = oe_seconds_to_sample_number(oe_s, sr)
            fmt    = f"MM:SS  {format_oe_time(oe_s)}"
            return f"{oe_s:.6f}", str(samp), fmt
        else:
            ns  = int(oe_seconds_to_saviour_ns(
                val, m['sv_t0'], m['scale'], m['offset']))
            fmt = format_saviour_ns(ns)
            return str(ns), "", fmt

    def _convert_single(self):
        if not self._require_model():
            return
        raw = self.single_input.get().strip()
        if not raw:
            return
        try:
            out_s, out_samp, out_fmt = self._do_convert(raw)

            def _set_readonly(entry, value):
                entry.config(state="normal")
                entry.delete(0, "end")
                entry.insert(0, value)
                entry.config(state="readonly")

            _set_readonly(self.single_output, out_s)
            _set_readonly(self.sample_output, out_samp)
            self.formatted_label.config(text=out_fmt, fg=MUTED)

        except Exception as e:
            messagebox.showerror("Conversion error", str(e))

    def _convert_batch(self):
        if not self._require_model():
            return
        lines = self.batch_text.get("1.0", "end").strip().split("\n")
        lines = [l.strip() for l in lines if l.strip()]
        if not lines:
            return

        self.batch_results = []
        for item in self.tree.get_children():
            self.tree.delete(item)

        errors = 0
        for line in lines:
            try:
                out_s, out_samp, out_fmt = self._do_convert(line)
                self.tree.insert("", "end",
                                  values=(line, out_s, out_samp, out_fmt))
                self.batch_results.append((line, out_s, out_samp, out_fmt))
            except Exception:
                self.tree.insert("", "end",
                                  values=(line, "ERROR", "", "Could not parse"))
                errors += 1

        n   = len(lines)
        msg = f"{n - errors}/{n} converted"
        if errors:
            msg += f"  ({errors} errors)"
        self.batch_status.config(
            text=msg, fg=ACCENT2 if errors == 0 else WARN)

    def _export_csv(self):
        if not self.batch_results:
            messagebox.showinfo("Nothing to export",
                "Run a batch conversion first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export results",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return

        direction = self.direction.get()
        if direction == "sv_to_oe":
            headers = ["saviour_unix_ns", "oe_seconds",
                       "oe_sample_number", "oe_formatted"]
        else:
            headers = ["oe_seconds", "saviour_unix_ns", "saviour_utc"]

        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for row in self.batch_results:
                    if direction == "sv_to_oe":
                        w.writerow(row)   # all 4 fields
                    else:
                        w.writerow((row[0], row[1], row[3]))  # skip empty sample col
            self.batch_status.config(
                text=f"Exported {len(self.batch_results)} rows → "
                     f"{os.path.basename(path)}",
                fg=ACCENT2)
        except Exception as e:
            messagebox.showerror("Export error", str(e))


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    app = ConverterTool()
    app.mainloop()