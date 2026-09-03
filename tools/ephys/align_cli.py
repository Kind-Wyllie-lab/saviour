"""
Headless ephys <-> SAVIOUR clock alignment for one session.

The vendored `tool1_fit_alignment.py` is a tkinter GUI; this wrapper
drives its discovery / loader / fit functions from the command line so
the controller's Post-Process page (`run_ephys_align`) can run it with
no display.

    python tools/ephys/align_cli.py \
        --oe   /path/to/open-ephys/recording-or-parent \
        --session /path/to/<share>/<session>/<date> \
        [--pin 1] [--no-invert] [--out model.json]

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fit(oe_root: str, session_dir: str, pin: int, invert: bool, out: str) -> dict:
    from tool1_fit_alignment import (  # noqa: PLC0415 -- vendored GUI module
        find_oe_ttl_dir,
        find_saviour_ttl_csv,
        fit_alignment,
        load_oe_ttl,
        load_saviour_ttl,
    )

    ttl_dir = find_oe_ttl_dir(oe_root)
    csv_path = find_saviour_ttl_csv(session_dir)
    oe_ts, oe_states = load_oe_ttl(ttl_dir)
    sv_ts, sv_states, sv_t0 = load_saviour_ttl(csv_path, pin, invert=invert)

    scale, offset, r2, residuals, _sv_edges, meta = fit_alignment(
        oe_ts, oe_states, sv_ts, sv_states,
    )

    model = {
        "scale": scale,
        "offset": offset,
        "r2": r2,
        "sv_t0_s": float(sv_t0),
        "pin_number": pin,
        "invert": invert,
        "oe_ttl_dir": ttl_dir,
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
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--oe", required=True, help="Open Ephys recording (or a parent)")
    ap.add_argument("--session", required=True, help="SAVIOUR session date dir")
    ap.add_argument("--pin", type=int, default=1, help="TTL pin number (default 1)")
    ap.add_argument("--no-invert", action="store_true",
                    help="Don't invert SAVIOUR TTL polarity (default: invert)")
    ap.add_argument("--out", default=None,
                    help="model.json path (default: <session>/_ephys/model.json)")
    args = ap.parse_args()

    out = args.out or os.path.join(args.session, "_ephys", "model.json")
    try:
        result = _fit(args.oe, args.session, args.pin, not args.no_invert, out)
    except Exception as exc:  # noqa: BLE001 -- surfaced as JSON to the caller
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "trace": traceback.format_exc()[-800:]}))
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
