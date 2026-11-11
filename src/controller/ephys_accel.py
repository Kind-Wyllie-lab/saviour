"""Headstage accelerometer (Open Ephys ``*_AUX1-3``) → aligned motion data.

Intan headstages carry a 3-axis analog accelerometer that the acquisition
board records as AUX channels, interleaved with the neural channels in
``continuous.dat``. This turns one headstage's AUX channels into:

- ``accel.csv``: 100 Hz acceleration (g), head pitch/roll (deg) from the
  gravity direction, ODBA (overall dynamic body acceleration, g), an
  ``invalid`` flag, OE time and (with a clock model) SAVIOUR wall time;
- ``summary.json``: calibration, data quality, timing;
- ``plot.json``: a reduced copy (min/mean/max per bucket) for the browser.

Findings behind the choices (plans/ephys-accelerometer.md):
- AUX values update every 4th sample (7.5 kHz at 30 kHz); 100 Hz is plenty.
- The Intan AUX input is 0-2.45 V, but int16 storage tops out at
  32767 * bit_volts (~1.2255 V): samples at the ceiling are clipped, and
  samples below 0 V cannot come from the sensor (~1 ms dips; garbage in
  saturation bursts). Both are masked before a per-bin median, and gaps are
  interpolated.
- Calibration: a sphere fit to the gravity component over the stillest part
  of the recording gives each axis's zero-g offset and the V-per-g
  sensitivity. Without enough orientation variety (headstage barely moved)
  the fit is unreliable and DEFAULT_CALIBRATION is used, flagged.
- Heading (yaw) is not observable from an accelerometer.

numpy only (the controller env has no scipy/matplotlib). Run as
``python -m src.controller.ephys_accel`` for the Post-Process job; prints a
one-line JSON result.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback

import numpy as np

from src.controller import ephys_data

FS = 100                 # output rate, Hz
GRAVITY_WINDOW_S = 1.0   # boxcar applied twice (zero phase) for gravity
ODBA_WINDOW_S = 0.5
STILL_FRACTION = 0.2     # stillest share of the recording used to calibrate
PLOT_BUCKETS = 1500
CHUNK_S = 60             # read continuous.dat this much at a time
# Measured on the SAVIOUR_Natalie_GS_lab-240626 headstage (sphere fit,
# 2026-11-10). A fallback only: flagged as "default" wherever it is used.
DEFAULT_CALIBRATION = {"offset_v": [0.4945, 0.5011, 0.4848], "v_per_g": 0.3396}
# Fit quality gates for the auto calibration.
MIN_SPREAD_DEG = 25.0    # largest angle between still gravity directions
MAX_RESIDUAL = 0.03      # sd of |g| about the fitted sphere, fraction of 1 g
V_PER_G_RANGE = (0.2, 0.6)
SERIES = ("ax_g", "ay_g", "az_g", "pitch_deg", "roll_deg", "odba_g")


class AccelError(ValueError):
    pass


# ------------------------------------------------------------- loading ----

def load_aux(stream: ephys_data.Stream, idx: list[int]):
    """100 Hz AUX volts (n, 3), invalid-bin mask (n, 3), OE time (n,).

    Bins are 10 ms; a bin is invalid when every sample in it was clipped or
    below 0 V (then filled by interpolation). Reads ``continuous.dat`` in
    CHUNK_S pieces so a multi-hour file never sits in memory.
    """
    nch, fs = stream.num_channels, stream.sample_rate
    if fs % FS:
        raise AccelError(f"sample rate {fs} is not a multiple of {FS} Hz")
    bv = float(stream.channels[idx[0]].get("bit_volts", 3.74e-5))
    dat = os.path.join(stream.folder, "continuous.dat")
    d = np.memmap(dat, dtype="<i2", mode="r")
    n_samples = d.shape[0] // nch
    d = d[: n_samples * nch].reshape(n_samples, nch)
    blk = int(fs // FS)            # samples per output bin
    # AUX holds each value for 4 samples, so every 4th sample loses nothing
    sub = 4 if blk % 4 == 0 else 1
    n = n_samples // blk
    if n < FS * 5:
        raise AccelError("recording is shorter than 5 s")
    lo, hi = min(idx), max(idx) + 1
    cols = [i - lo for i in idx]
    out = np.empty((n, 3))
    bad = np.zeros((n, 3), bool)
    step = int(CHUNK_S * FS)
    for b0 in range(0, n, step):
        b1 = min(n, b0 + step)
        raw = np.asarray(d[b0 * blk: b1 * blk: sub, lo:hi])[:, cols]
        v = raw.astype(np.float64) * bv
        v[(raw >= 32767) | (raw < 0)] = np.nan
        v = v.reshape(b1 - b0, blk // sub, 3)
        allbad = np.isnan(v).all(axis=1)
        v = np.where(allbad[:, None, :], 0.0, v)  # placeholder; nanmedian stays quiet
        out[b0:b1] = np.nanmedian(v, axis=1)
        bad[b0:b1] = allbad
    for i in range(3):
        if bad[:, i].all():
            raise AccelError(f"AUX{i + 1} has no valid samples")
        if bad[:, i].any():
            ok = ~bad[:, i]
            out[bad[:, i], i] = np.interp(np.flatnonzero(bad[:, i]),
                                          np.flatnonzero(ok), out[ok, i])
    ts_path = os.path.join(stream.folder, "timestamps.npy")
    centres = np.arange(n) * blk + blk // 2
    if os.path.exists(ts_path):
        ts = np.load(ts_path, mmap_mode="r")
        t_oe = np.asarray(ts[np.minimum(centres, ts.shape[0] - 1)], dtype=np.float64)
    else:
        t_oe = centres / fs
    return out, bad, t_oe, bv


# ------------------------------------------------------------- signal ----

def smooth(x: np.ndarray, window_s: float, passes: int = 1) -> np.ndarray:
    """Centred boxcar (zero phase), edge-padded, along axis 0."""
    w = max(1, int(round(window_s * FS)))
    k = np.ones(w) / w
    y = np.asarray(x, dtype=np.float64)
    for _ in range(passes):
        pad = np.pad(y, [(w, w)] + [(0, 0)] * (y.ndim - 1), mode="edge")
        if y.ndim == 1:
            y = np.convolve(pad, k, "same")[w:-w]
        else:
            y = np.stack([np.convolve(pad[:, i], k, "same")[w:-w]
                          for i in range(y.shape[1])], axis=1)
    return y


def gravity(v: np.ndarray) -> np.ndarray:
    return smooth(v, GRAVITY_WINDOW_S, passes=2)


def calibrate(v: np.ndarray) -> dict:
    """Sphere fit of the gravity component over the stillest periods.

    Returns {"offset_v", "v_per_g", "method", "residual", "spread_deg",
    "ok"}; ``ok`` False means the fit failed a quality gate and the caller
    should fall back.
    """
    g = gravity(v)
    still = smooth(np.linalg.norm(v - g, axis=1), 0.5)
    sel = still <= np.quantile(still, STILL_FRACTION)
    G = g[sel][:: max(1, sel.sum() // 5000)]
    A = np.c_[2 * G, np.ones(len(G))]
    sol, *_ = np.linalg.lstsq(A, (G ** 2).sum(1), rcond=None)
    c = sol[:3]
    r2 = sol[3] + c @ c
    r = float(np.sqrt(r2)) if r2 > 0 else float("nan")
    res = float(np.std(np.linalg.norm(G - c, axis=1)) / r) if r == r else float("inf")
    u = (G - c) / np.linalg.norm(G - c, axis=1, keepdims=True)
    mean_dir = u.mean(0) / np.linalg.norm(u.mean(0))
    spread = float(np.degrees(np.arccos(np.clip(u @ mean_dir, -1, 1))).max() * 2)
    ok = (V_PER_G_RANGE[0] <= r <= V_PER_G_RANGE[1] and res <= MAX_RESIDUAL
          and spread >= MIN_SPREAD_DEG)
    return {"offset_v": [round(float(x), 4) for x in c], "v_per_g": round(r, 4),
            "method": "auto", "residual": round(res, 4),
            "spread_deg": round(spread, 1), "ok": bool(ok)}


def derive(v: np.ndarray, offset_v, v_per_g: float) -> dict[str, np.ndarray]:
    acc = (v - np.asarray(offset_v)) / v_per_g
    g = gravity(acc)
    dyn = acc - g
    u = g / np.linalg.norm(g, axis=1, keepdims=True)
    return {
        "ax_g": acc[:, 0], "ay_g": acc[:, 1], "az_g": acc[:, 2],
        # pitch: elevation of headstage X above horizontal; roll: rotation
        # about X (0 with Z up). Headstage frame until mounting is known.
        "pitch_deg": np.degrees(np.arcsin(np.clip(u[:, 0], -1, 1))),
        "roll_deg": np.degrees(np.arctan2(u[:, 1], u[:, 2])),
        "odba_g": smooth(np.abs(dyn).sum(1), ODBA_WINDOW_S),
        "gravity_g": g, "dynamic_g": dyn,
    }


# --------------------------------------------------------------- timing ----

def load_model(session_dir: str) -> dict | None:
    path = os.path.join(session_dir, "_ephys", "model.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def oe_to_saviour_ns(t_oe: np.ndarray, model: dict) -> np.ndarray:
    """Inverse of tool2's saviour_ns_to_oe_seconds:
    t_oe = scale * (sv_s - sv_t0) + offset."""
    sv_s = (t_oe - model["offset"]) / model["scale"] + model["sv_t0_s"]
    return np.round(sv_s * 1e9).astype(np.int64)


# -------------------------------------------------------------- outputs ----

def reduce_for_plot(cols: dict[str, np.ndarray], n_buckets: int = PLOT_BUCKETS):
    n = len(next(iter(cols.values())))
    edges = np.linspace(0, n, min(n_buckets, n) + 1).astype(int)
    starts, counts = edges[:-1], np.diff(edges)
    out = {}
    for k, x in cols.items():
        out[k] = {
            "min": np.round(np.minimum.reduceat(x, starts), 3).tolist(),
            "mean": np.round(np.add.reduceat(x, starts) / counts, 3).tolist(),
            "max": np.round(np.maximum.reduceat(x, starts), 3).tolist(),
        }
    return edges, out


def write_csv(path: str, t_oe, ts_ns, d: dict, invalid: np.ndarray) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ns", "oe_s", *SERIES, "invalid"])
        for i in range(len(t_oe)):
            w.writerow([
                "" if ts_ns is None else int(ts_ns[i]),
                f"{t_oe[i]:.4f}",
                *(f"{d[k][i]:.4f}" for k in SERIES[:3]),
                *(f"{d[k][i]:.2f}" for k in SERIES[3:5]),
                f"{d['odba_g'][i]:.4f}",
                int(invalid[i]),
            ])


def process(ephys_root: str, session_dir: str, recording: str | None = None,
            port: str | None = None, calibration: dict | None = None,
            out_root: str | None = None) -> dict:
    """Run the whole step for one headstage; returns the summary dict."""
    model = load_model(session_dir)
    warnings: list[str] = []
    if recording:
        rec, reason = ephys_data.find_recording(ephys_root, recording), "chosen by hand"
    elif model and model.get("oe_recording"):
        rec = ephys_data.find_recording(ephys_root, model["oe_recording"])
        reason = "the recording the clock model was fitted on"
    else:
        rec, reason = ephys_data.pick_recording(
            ephys_data.list_recordings(ephys_root), ephys_data.saviour_span(session_dir))
    stream = rec.main_stream()
    ports = ephys_data.aux_ports(stream)
    if not ports:
        raise AccelError(f"recording {rec.rel} has no headstage AUX channels")
    port = port or sorted(ports)[0]
    if port not in ports:
        raise AccelError(f"no AUX port {port!r} (have {', '.join(sorted(ports))})")

    v, bad, t_oe, bv = load_aux(stream, ports[port])

    if calibration:
        cal = {"offset_v": list(calibration["offset_v"]),
               "v_per_g": float(calibration["v_per_g"]), "method": "given"}
    else:
        cal = calibrate(v)
        if not cal["ok"]:
            warnings.append(
                f"auto calibration not reliable (spread {cal['spread_deg']}°, "
                f"residual {cal['residual']}, {cal['v_per_g']} V/g): the headstage "
                "may not have moved enough; using the default calibration")
            cal = {**DEFAULT_CALIBRATION, "method": "default",
                   "auto_attempt": {k: cal[k] for k in
                                    ("offset_v", "v_per_g", "residual", "spread_deg")}}
        cal.pop("ok", None)

    d = derive(v, cal["offset_v"], cal["v_per_g"])
    invalid = bad.any(axis=1)

    ts_ns = None
    aligned = False
    if model:
        if model.get("oe_recording") and model["oe_recording"] != rec.rel:
            warnings.append(f"clock model was fitted on {model['oe_recording']}, "
                            f"not {rec.rel}: output left in ephys time")
        else:
            if not model.get("oe_recording"):
                warnings.append("clock model predates recording selection; "
                                "re-run alignment to confirm it matches")
            ts_ns = oe_to_saviour_ns(t_oe, model)
            aligned = True

    out_dir = os.path.join(out_root or os.path.join(session_dir, "_ephys", "accel"), port)
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, "accel.csv"), t_oe, ts_ns, d, invalid)

    clip_g = [(32767 * bv - o) / cal["v_per_g"] for o in cal["offset_v"]]
    summary = {
        "recording": rec.rel, "recording_reason": reason, "port": port,
        "channels": [stream.channels[i]["channel_name"] for i in ports[port]],
        "rate_hz": FS, "n": int(len(t_oe)),
        "duration_s": round(float(len(t_oe)) / FS, 2),
        "oe_start_s": round(float(t_oe[0]), 4),
        "aligned": aligned,
        "start_ns": int(ts_ns[0]) if aligned else None,
        "end_ns": int(ts_ns[-1]) if aligned else None,
        "calibration": cal,
        "clip_ceiling_g": [round(float(x), 2) for x in clip_g],
        "invalid_fraction": [round(float(x), 5) for x in bad.mean(0)],
        "frame": "headstage axes (mounting not set); heading not measurable",
        "warnings": warnings,
        "files": {"csv": "accel.csv", "plot": "plot.json"},
    }
    edges, series = reduce_for_plot({k: d[k] for k in SERIES})
    centre = np.minimum((edges[:-1] + edges[1:]) // 2, len(t_oe) - 1)
    plot = {
        "t_s": np.round((centre / FS), 3).tolist(),   # seconds from recording start
        "t_ns": ts_ns[centre].tolist() if aligned else None,
        "series": series,
        "summary": summary,
    }
    with open(os.path.join(out_dir, "plot.json"), "w", encoding="utf-8") as f:
        json.dump(plot, f, separators=(",", ":"))
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ephys", required=True, help="uploaded Open Ephys root")
    ap.add_argument("--session", required=True, help="SAVIOUR session dir")
    ap.add_argument("--recording", default=None, help="recording rel path (default: auto)")
    ap.add_argument("--port", default=None, help="headstage port, e.g. A1 (default: first)")
    ap.add_argument("--offset-v", type=float, nargs=3, default=None,
                    help="zero-g volts per axis (with --v-per-g: skip auto-calibration)")
    ap.add_argument("--v-per-g", type=float, default=None)
    ap.add_argument("--out", default=None, help="output root (default: <session>/_ephys/accel)")
    a = ap.parse_args(argv)
    cal = ({"offset_v": a.offset_v, "v_per_g": a.v_per_g}
           if a.offset_v and a.v_per_g else None)
    try:
        s = process(a.ephys, a.session, a.recording, a.port, cal, a.out)
    except Exception as exc:  # noqa: BLE001 -- surfaced as JSON to the caller
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "trace": traceback.format_exc()[-800:]}))
        sys.exit(1)
    print(json.dumps({"ok": True, "port": s["port"], "aligned": s["aligned"],
                      "recording": s["recording"], "warnings": s["warnings"],
                      "calibration": s["calibration"]["method"]}))


if __name__ == "__main__":
    main()
