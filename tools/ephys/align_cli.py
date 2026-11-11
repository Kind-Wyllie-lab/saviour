"""
Headless ephys <-> SAVIOUR clock alignment for one session.

The vendored `tool1_fit_alignment.py` is a tkinter GUI; this wrapper
drives its discovery / loader / fit functions from the command line so
the controller's Post-Process page (`run_ephys_align`) can run it with
no display.

    python tools/ephys/align_cli.py \
        --oe   /path/to/open-ephys/recording-or-parent \
        --session /path/to/<share>/<session>/<date> \
        [--recording <rel path>] [--pin N] [--no-invert] [--out model.json]

An upload can hold several recordings (experimentN/recordingM). Unless
--recording names one (its path relative to --oe), the recording whose
span overlaps the session's TTL log is chosen
(src/controller/ephys_data.pick_recording); no overlap, or two about
equal, is an error listing the candidates rather than a silent guess.

Prints a one-line JSON result to stdout; non-zero exit + an "error"
field on failure. Needs numpy + pandas (pandas is not in the
controller's main venv -- run under env2 or `pip install pandas`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # repo root

from src.controller import ephys_data  # noqa: E402


def _select(oe_root: str, session_dir: str, recording: str | None):
    if recording:
        rec = ephys_data.find_recording(oe_root, recording)
        return rec, "chosen by hand"
    return ephys_data.pick_recording(
        ephys_data.list_recordings(oe_root), ephys_data.saviour_span(session_dir))


def _pc_clock_offset(rec, scale: float, offset: float, sv_t0: float):
    """How far the acquisition PC's clock (sync_messages "Software Time")
    was from PTP time at the first sample, per the fitted model. A sanity
    check on the recording choice, which leans on that clock."""
    import numpy as np  # noqa: PLC0415

    stream = rec.main_stream()
    if rec.start_unix_s is None or stream is None:
        return None
    ts = os.path.join(stream.folder, "timestamps.npy")
    if not os.path.exists(ts):
        return None
    t0_oe = float(np.load(ts, mmap_mode="r")[0])
    first_ptp = (t0_oe - offset) / scale + sv_t0
    return round(rec.start_unix_s - first_ptp, 3)


def _fit(oe_root: str, session_dir: str, pin: int | None, invert: bool, out: str,
         recording: str | None = None) -> dict:
    from tool1_fit_alignment import (  # noqa: PLC0415 -- vendored GUI module
        fit_alignment,
        load_oe_ttl,
        load_saviour_ttl,
    )

    rec, reason = _select(oe_root, session_dir, recording)
    if not rec.ttl_dirs:
        raise FileNotFoundError(f"recording {rec.rel} has no TTL events folder")
    ttl_dir = rec.ttl_dirs[0]
    # Skips SAVIOUR's own _ephys/ output, unlike tool1's find_saviour_ttl_csv.
    csvs = ephys_data.saviour_ttl_csvs(session_dir)
    if not csvs:
        raise FileNotFoundError(f"No SAVIOUR TTL CSV found under {session_dir}")
    csv_path = csvs[0]
    oe_ts, oe_states = load_oe_ttl(ttl_dir)
    if pin is None:  # the pin logged in pseudorandom mode, else the only pin
        pin = ephys_data.sync_pin(csv_path)
    sv_ts, sv_states, sv_t0 = load_saviour_ttl(csv_path, pin, invert=invert)

    scale, offset, r2, residuals, _sv_edges, meta = fit_alignment(
        oe_ts, oe_states, sv_ts, sv_states,
    )
    pc_offset = _pc_clock_offset(rec, scale, offset, float(sv_t0))

    model = {
        "scale": scale,
        "offset": offset,
        "r2": r2,
        "sv_t0_s": float(sv_t0),
        "pin_number": pin,
        "invert": invert,
        "oe_ttl_dir": ttl_dir,
        "oe_recording": rec.rel,
        "oe_recording_reason": reason,
        "pc_clock_offset_s": pc_offset,
        "sv_ttl_csv": csv_path,
        **meta,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        json.dump(model, f, indent=2)
    return {
        "ok": True,
        "model_path": out,
        "r2": r2,
        "edges_used": meta.get("edges_used"),
        "residual_max_ms": meta.get("residual_max_ms"),
        "drift_ms": meta.get("drift_ms"),
        "oe_recording": rec.rel,
        "oe_recording_reason": reason,
        "pc_clock_offset_s": pc_offset,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oe", required=True, help="Open Ephys recording (or a parent)")
    ap.add_argument("--session", required=True, help="SAVIOUR session date dir")
    ap.add_argument("--recording", default=None,
                    help="recording path relative to --oe (default: auto)")
    ap.add_argument("--pin", type=int, default=None,
                    help="TTL pin number (default: the pseudorandom-mode pin)")
    ap.add_argument("--no-invert", action="store_true",
                    help="Don't invert SAVIOUR TTL polarity (default: invert)")
    ap.add_argument("--out", default=None,
                    help="model.json path (default: <session>/_ephys/model.json)")
    args = ap.parse_args()

    out = args.out or os.path.join(args.session, "_ephys", "model.json")
    try:
        result = _fit(args.oe, args.session, args.pin, not args.no_invert, out,
                      recording=args.recording)
    except Exception as exc:  # noqa: BLE001 -- surfaced as JSON to the caller
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "trace": traceback.format_exc()[-800:]}))
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
