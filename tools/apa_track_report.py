#!/usr/bin/env python3
"""
apa_track_report.py -- summarise tools/apa_track_assess.py output: tracker
agreement, zone-state agreement, jumps and ambiguity per video, and contact
sheets of random and flagged frames for checking by eye.

    python3 tools/apa_track_report.py <assess_dir> [--sheets N_RANDOM_PER_VIDEO]

Writes <assess_dir>/report.json, <assess_dir>/sheets/*.jpg and
<assess_dir>/sheets/index.csv (tile id -> session, frame, flags), and prints
a per-video table.

There is no hand-labelled ground truth, so "offline" is scored against:
  hood    an independent detector (agreement within AGREE_PX = same animal);
  jumps   displacement no rat can make in one frame;
  sheets  random and flagged frames, read by a person (the actual accuracy
          figure; index.csv has a blank verdict column to fill in).
"causal" and "legacy" are scored against offline, as candidates for live
(automated) zone detection.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apa_track  # noqa: E402 -- path set just above

AGREE_PX = 100        # full-res px; about two-thirds of a rat's body length here
JUMP_PX = 60          # one-frame displacement treated as impossible (1800 px/s)
ONSET_RUN = 30        # rat counted present from the first run of this many frames
                      # where offline and hood agree (skips the empty arena / hand)
TILE = 320


def geom_dict(a) -> dict:
    keys = ("cx", "cy", "outer_r", "inner_r", "start_deg", "span_deg")
    return dict(zip(keys, (float(v) for v in a)))


def zone_of(xy: np.ndarray, g: dict) -> np.ndarray:
    return apa_track.in_zone(xy[:, 0].astype(float), xy[:, 1].astype(float), g)


def dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.hypot(a[:, 0] - b[:, 0], a[:, 1] - b[:, 1])


def onset(agree: np.ndarray) -> int:
    run = 0
    for i, ok in enumerate(agree):
        run = run + 1 if ok else 0
        if run >= ONSET_RUN:
            return i - ONSET_RUN + 1
    return len(agree)


def confusion(ref: np.ndarray, other: np.ndarray, valid: np.ndarray) -> dict:
    r, o = ref[valid], other[valid]
    tp, fp = int((r & o).sum()), int((~r & o).sum())
    fn, tn = int((r & ~o).sum()), int((~r & ~o).sum())
    return {"frames": int(valid.sum()), "agree": round((tp + tn) / max(1, valid.sum()), 4),
            "false_in": fp, "missed_in": fn,
            "precision": round(tp / max(1, tp + fp), 4), "recall": round(tp / max(1, tp + fn), 4)}


def boundary_entries(zone: np.ndarray) -> int:
    return int(np.sum(np.diff(zone.astype(np.int8)) == 1))


def remap_paths(d: dict, share_root: str | None) -> None:
    """Point date_dir/video at share_root/<session>/<DATE>/..., for results
    computed on another machine (e.g. a Windows laptop with a copy)."""
    if not share_root:
        return
    def parts(p):
        return str(p).replace("\\", "/").rstrip("/").split("/")
    date = parts(d["date_dir"])[-1]
    date_dir = os.path.join(share_root, str(d["session"]), date)
    d["date_dir"] = np.array(date_dir)
    d["video"] = np.array(os.path.join(date_dir, *parts(d["video"])[-2:]))


def analyse_video(d) -> tuple[dict, dict]:
    g = geom_dict(d["geom"])
    off, hood, cau, leg = (d[k] for k in ("offline", "hood", "causal", "legacy"))
    n = len(d["ts"])
    have_off = np.isfinite(off[:, 0])
    dh = dist(off, hood)
    agree_h = have_off & np.isfinite(hood[:, 0]) & (dh < AGREE_PX)
    start = onset(agree_h)
    live = np.arange(n) >= start
    if live.sum() < 30 * 30:
        return {"session": str(d["session"]), "skipped": "rat never reliably present"}, {}

    step = np.r_[0, dist(off[1:], off[:-1])]
    jump = live & np.isfinite(step) & (step > JUMP_PX)
    amb = live & (d["blobs"] >= 2) & (d["second"] >= 0.5 * np.nan_to_num(off[:, 2]))
    z_off = zone_of(off, g) & have_off
    out = {
        "session": str(d["session"]),
        "frames": n, "onset_frame": int(start), "frames_scored": int(live.sum()),
        "offline_detect": round(float(have_off[live].mean()), 4),
        "hood_detect": round(float(np.isfinite(hood[live, 0]).mean()), 4),
        "offline_vs_hood_agree": round(float(agree_h[live].mean()), 4),
        "offline_vs_hood_median_px": round(float(np.nanmedian(dh[live])), 1),
        "jumps": int(jump.sum()),
        "ambiguous_frames": int(amb.sum()),
        "offline_area_p5_p50_p95": [round(float(v)) for v in np.nanpercentile(off[live, 2], [5, 50, 95])],
        "time_in_zone_pct": round(100 * float(z_off[live].mean()), 1),
        "zone_entries": boundary_entries(z_off[live]),
        "det_ms_median": round(float(np.median(d["det_ms"][live])), 2),
    }
    for name, arr in (("hood", hood), ("causal", cau), ("legacy", leg)):
        have = np.isfinite(arr[:, 0])
        z = zone_of(arr, g) & have
        both = live & have_off & have
        out[f"zone_{name}"] = confusion(z_off, z, both)
        out[f"{name}_detect"] = round(float(have[live].mean()), 4)
        out[f"{name}_vs_offline_agree"] = round(float((dist(arr, off)[both] < AGREE_PX).mean()), 4) \
            if both.any() else None
    # Distance to the zone edge, where small position errors flip the zone state.
    r = np.hypot(off[:, 0] - g["cx"], off[:, 1] - g["cy"])
    theta = np.degrees(np.arctan2(off[:, 1] - g["cy"], off[:, 0] - g["cx"])) % 360
    da = np.minimum((theta - g["start_deg"]) % 360, (g["start_deg"] + g["span_deg"] - theta) % 360)
    edge = np.minimum.reduce([np.abs(r - g["inner_r"]), np.abs(r - g["outer_r"]),
                              np.radians(np.minimum(da, 360 - da)) * r])
    out["near_edge_pct"] = round(100 * float((live & have_off & (edge < 20)).mean()), 2)

    # Operator agreement: shock pulses vs the offline zone state.
    shocks = apa_track.load_shock_events(str(d["date_dir"]))
    pulses = np.array([t for t, e in shocks if e == "SENDING_SHOCK"], np.int64)
    ts = d["ts"]
    pulses = pulses[(pulses >= ts[start]) & (pulses <= ts[-1])]
    if len(pulses):
        zt = ts[live & z_off]
        near = np.array([np.any(np.abs(zt - p) <= 5e8) for p in pulses])
        out["pulses"] = int(len(pulses))
        out["pulses_in_offline_zone"] = int(near.sum())

    flags = {"jump": np.flatnonzero(jump), "ambiguous": np.flatnonzero(amb),
             "hood_disagree": np.flatnonzero(live & have_off & np.isfinite(hood[:, 0]) & ~agree_h),
             "offline_missing": np.flatnonzero(live & ~have_off),
             "causal_zone_disagree": np.flatnonzero(live & have_off & np.isfinite(cau[:, 0])
                                                    & (zone_of(cau, g) != z_off)),
             "live": np.flatnonzero(live)}
    return out, flags


def draw_tile(frame, d, i, g, label):
    img = frame.copy()
    apa_track.draw_zone(img, g, 1.0, (0, 0, 255), 0.15)
    for key, col, mk in (("hood", (255, 255, 0), cv2.MARKER_CROSS),
                         ("causal", (255, 0, 255), cv2.MARKER_DIAMOND),
                         ("legacy", (0, 140, 255), cv2.MARKER_SQUARE)):
        x, y = d[key][i, :2]
        if np.isfinite(x):
            cv2.drawMarker(img, (int(x), int(y)), col, mk, 28, 3)
    x, y = d["offline"][i, :2]
    if np.isfinite(x):
        cv2.circle(img, (int(x), int(y)), 18, (0, 255, 255), 4)
    # Crop to the arena so the rat is as large as possible in the tile.
    r = int(g["outer_r"] * 1.05)
    cx, cy = int(g["cx"]), int(g["cy"])
    crop = img[max(0, cy - r):cy + r, max(0, cx - r):cx + r]
    tile = cv2.resize(crop, (TILE, TILE))
    cv2.rectangle(tile, (0, 0), (TILE, 22), (0, 0, 0), -1)
    cv2.putText(tile, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return tile


def write_sheets(picks, out_dir, cols=5, rows=4):
    """picks: list of (tile_id, npz, frame_idx, flags_str). Frames are read in
    file order per video so each video is opened once."""
    os.makedirs(out_dir, exist_ok=True)
    tiles = {}
    by_video: dict[str, list] = {}
    for p in picks:
        by_video.setdefault(str(p[1]["video"]), []).append(p)
    for video, ps in by_video.items():
        cap = cv2.VideoCapture(video)
        for tid, d, i, flags in sorted(ps, key=lambda p: p[2]):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, frame = cap.read()
            if ok:
                tiles[tid] = draw_tile(frame, d, i, geom_dict(d["geom"]), f"#{tid} {flags}")
        cap.release()
    ids = sorted(tiles)
    per = cols * rows
    for s in range(0, len(ids), per):
        chunk = [tiles[t] for t in ids[s:s + per]]
        while len(chunk) < per:
            chunk.append(np.zeros_like(chunk[0]))
        sheet = np.vstack([np.hstack(chunk[r * cols:(r + 1) * cols]) for r in range(rows)])
        cv2.imwrite(os.path.join(out_dir, f"sheet_{s // per:03d}.jpg"), sheet,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("assess_dir")
    ap.add_argument("--sheets", type=int, default=8, help="random frames per video")
    ap.add_argument("--flagged", type=int, default=4, help="flagged frames per video")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--share-root", default=None,
                    help="where the sessions live here, if the .npz were made elsewhere")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    results, picks = [], []
    tid = 0
    for path in sorted(glob.glob(os.path.join(args.assess_dir, "*.npz"))):
        d = dict(np.load(path, allow_pickle=False))
        remap_paths(d, args.share_root)
        res, flags = analyse_video(d)
        results.append(res)
        if not flags:
            continue
        for i in rng.choice(flags["live"], min(args.sheets, len(flags["live"])), replace=False):
            tid += 1
            picks.append((tid, d, int(i), "random"))
        pool = [(k, i) for k in ("jump", "hood_disagree", "offline_missing", "ambiguous",
                                 "causal_zone_disagree") for i in flags[k]]
        if pool:
            for j in rng.choice(len(pool), min(args.flagged, len(pool)), replace=False):
                tid += 1
                picks.append((tid, d, int(pool[j][1]), pool[j][0]))

    sheet_dir = os.path.join(args.assess_dir, "sheets")
    write_sheets(picks, sheet_dir)
    with open(os.path.join(sheet_dir, "index.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tile", "session", "frame", "flag", "verdict"])
        for t, d, i, fl in picks:
            w.writerow([t, str(d["session"]), i, fl, ""])

    scored = [r for r in results if "skipped" not in r]
    pooled = {"videos": len(scored), "frames_scored": sum(r["frames_scored"] for r in scored)}
    for k in ("offline_detect", "offline_vs_hood_agree", "causal_detect", "legacy_detect",
              "causal_vs_offline_agree", "legacy_vs_offline_agree"):
        vals = [(r[k], r["frames_scored"]) for r in scored if r.get(k) is not None]
        pooled[k] = round(sum(v * n for v, n in vals) / max(1, sum(n for _, n in vals)), 4)
    for name in ("hood", "causal", "legacy"):
        c = [r[f"zone_{name}"] for r in scored]
        fr = sum(x["frames"] for x in c)
        pooled[f"zone_{name}"] = {
            "frames": fr,
            "agree": round(sum(x["agree"] * x["frames"] for x in c) / max(1, fr), 4),
            "false_in": sum(x["false_in"] for x in c), "missed_in": sum(x["missed_in"] for x in c)}
    pooled["jumps"] = sum(r["jumps"] for r in scored)
    pooled["ambiguous_frames"] = sum(r["ambiguous_frames"] for r in scored)
    pooled["pulses"] = sum(r.get("pulses", 0) for r in scored)
    pooled["pulses_in_offline_zone"] = sum(r.get("pulses_in_offline_zone", 0) for r in scored)
    with open(os.path.join(args.assess_dir, "report.json"), "w") as f:
        json.dump({"pooled": pooled, "videos": results}, f, indent=1)

    hdr = f"{'session':45s} {'scored':>7s} {'off%':>6s} {'hood=':>6s} {'jump':>5s} {'amb':>5s} " \
          f"{'zone=h':>7s} {'zone=c':>7s} {'zone=l':>7s} {'pulses in/all':>13s}"
    print(hdr)
    for r in results:
        if "skipped" in r:
            print(f"{r['session']:45s} skipped: {r['skipped']}")
            continue
        print(f"{r['session'][:45]:45s} {r['frames_scored']:7d} {100 * r['offline_detect']:6.1f} "
              f"{100 * r['offline_vs_hood_agree']:6.1f} {r['jumps']:5d} {r['ambiguous_frames']:5d} "
              f"{100 * r['zone_hood']['agree']:7.2f} {100 * r['zone_causal']['agree']:7.2f} "
              f"{100 * r['zone_legacy']['agree']:7.2f} "
              f"{r.get('pulses_in_offline_zone', '-'):>6}/{r.get('pulses', '-'):<6}")
    print(json.dumps(pooled, indent=1))
    print(f"{tid} tiles in {sheet_dir}")


if __name__ == "__main__":
    main()
