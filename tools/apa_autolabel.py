#!/usr/bin/env python3
"""
apa_autolabel.py -- build a YOLO training set for the APA rat detector from
tools/apa_track_assess.py output, so the rat doesn't have to be boxed by hand.
Step 1 of plans/apa-rat-detector.md.

    python3 tools/apa_autolabel.py <assess_dir> --out DIR \
        [--share-root /home/pi/controller_share] [--audit audit_verdicts.csv] \
        [--holdout 152,854] [--jobs 2]

Per session, from the per-frame tracker results in <assess_dir>/*.npz:

  positives  frames where the offline tracker and the independent hood
             detector agree, the blob is a normal size and the track is
             steady; spread over where the rat is (centre, middle, rim,
             in the zone, on the zone edge) and at least MIN_GAP_S apart
  review     the hard cases: trackers disagreeing, jumps, competing blobs,
             the rat at the rim, frames a person marked wrong or uncertain
             in the visual audit -- with the offline tracker's box as a
             best guess, for correcting in Roboflow
  negatives  the arena before the rat goes in and after it comes out
             (empty arena, the operator's hand); empty label files

The box is the offline tracker's blob, re-found on the frame by its stored
centroid. Its blur and opening already drop most of the tail, which matches
the convention in docs/readthedocs/apa_rat_detector_training.md (body, no
tail). Videos are decoded in order, never by seeking: the May recordings
are 25 fps in a container that says 30, and seeks land on the wrong frame.

Writes, in Roboflow's YOLO layout (train/ and valid/, images/ and labels/):
  DIR/dataset/   positives + negatives, data.yaml
  DIR/review/    the hard cases, pre-labelled
  DIR/sheets/    contact sheets per category, boxes drawn, for checking
  DIR/calib/     --calib train images for quantising the HEF
  DIR/manifest.csv
Whole rats in --holdout go to valid/, everything else to train/.
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apa_track  # noqa: E402 -- path set just above
import apa_track_report as report  # noqa: E402

MIN_GAP_S = 1.0          # between any two exported frames of one session
STEADY_FRAMES = 15       # no jump within this many frames either side
MATCH_PX = 12            # full-res px between stored centroid and the re-found blob
BOX_MASK_GROW = 1.25     # boxes are found inside this x the zone mask: rats at the wall
                         # lean over the wall's inner face, which the mask cuts off
MIN_FILL = 0.37          # blob area / box area; below it the box has taken in the moving
                         # grid or rim beside the rat (loose, centre off): not a positive
LOOSE_REVIEW_PER_SESSION = 5
CLIP_PX = 3              # tracking-res px: a blob this close to the mask edge is cut off by it
EDGE_PX = 30             # "on the zone edge": within this of a zone boundary
RIM_FRACTION = 0.8       # "at the rim": beyond this x the arena radius
CENTRE_FRACTION = 0.45   # "centre": inside this x the arena radius
POSITIVE_QUOTA = {"zone": 15, "zone_edge": 15, "rim": 25, "centre": 10, "middle": 15}
REVIEW_PER_SESSION = 10
POSITIVE_MIN_AREA_PCT = 30  # positives need a blob at least this percentile of the session's
NEGATIVES_PER_SESSION = 6
NEGATIVE_MAX_FRACTION = 0.25  # an "empty" frame: neither tracker sees more than this x a rat
CHECK_EMPTY_PER_SESSION = 2   # frames before/after the rat with something in them, for review
NEGATIVE_MARGIN = 45     # frames kept clear of the rat's first/last appearance
JPEG_QUALITY = 92
SHEET_TILE = 240
CALIB_N = 128            # HEF calibration images (Hailo DFC wants 64-1024)
CALIB_NEG_FRACTION = 0.1


def rat_id(session: str) -> str:
    m = re.search(r"-(\d{3})-", session)
    return m.group(1) if m else session


def band_of(off: np.ndarray, g: dict, z: np.ndarray) -> np.ndarray:
    """Where the rat is, per frame: zone_edge, zone, rim, centre or middle."""
    r = np.hypot(off[:, 0] - g["cx"], off[:, 1] - g["cy"])
    theta = np.degrees(np.arctan2(off[:, 1] - g["cy"], off[:, 0] - g["cx"])) % 360
    da = np.minimum((theta - g["start_deg"]) % 360, (g["start_deg"] + g["span_deg"] - theta) % 360)
    edge = np.minimum.reduce([np.abs(r - g["inner_r"]), np.abs(r - g["outer_r"]),
                              np.radians(np.minimum(da, 360 - da)) * r])
    out = np.full(len(off), "middle", dtype=object)
    out[r < CENTRE_FRACTION * g["outer_r"]] = "centre"
    out[r > RIM_FRACTION * g["outer_r"]] = "rim"
    out[z] = "zone"
    near_zone = (r > g["inner_r"] - EDGE_PX) & (r < g["outer_r"] + EDGE_PX)
    out[(edge < EDGE_PX) & near_zone] = "zone_edge"
    return out


def spaced_sample(cands: np.ndarray, k: int, taken: list[int], gap: int,
                  rng: np.random.Generator) -> list[int]:
    """Up to k frames from cands, each at least gap frames from every frame
    already taken (taken is extended in place)."""
    picked = []
    for i in rng.permutation(cands):
        if len(picked) >= k:
            break
        if all(abs(int(i) - t) >= gap for t in taken):
            picked.append(int(i))
            taken.append(int(i))
    return picked


def choose_frames(d: dict, audit: dict[str, dict[int, str]], seed: int) -> list[tuple[int, str, str]]:
    """[(frame, set, category)] for one session; set is positive/review/negative."""
    stats, flags = report.analyse_video(d)
    if "skipped" in stats:
        return []
    g = report.geom_dict(d["geom"])
    off, hood = d["offline"], d["hood"]
    n = len(d["ts"])
    fps = 1e9 / np.median(np.diff(d["ts"][:500]))
    gap = int(round(MIN_GAP_S * fps))
    rng = np.random.default_rng(seed)
    live = np.zeros(n, bool)
    live[flags["live"]] = True
    have = np.isfinite(off[:, 0])
    agree = have & np.isfinite(hood[:, 0]) & (report.dist(off, hood) < report.AGREE_PX)
    # A blob smaller than usual is often part of the rat: its dark back
    # against the dark wall doesn't differ from the background.
    lo, hi = np.nanpercentile(off[live & have, 2], [POSITIVE_MIN_AREA_PCT, 95])
    steady = np.ones(n, bool)
    for j in flags["jump"]:
        steady[max(0, j - STEADY_FRAMES):j + STEADY_FRAMES + 1] = False
    amb = np.zeros(n, bool)
    amb[flags["ambiguous"]] = True
    z = report.zone_of(off, g) & have
    band = band_of(off, g, z)
    good = live & agree & steady & ~amb & (off[:, 2] >= lo) & (off[:, 2] <= hi)

    taken: list[int] = []
    out: list[tuple[int, str, str]] = []
    # Review first, so the hard cases get their frames before positives fill the gaps.
    session = str(d["session"])
    for i, verdict in sorted(audit.get(session, {}).items()):
        if verdict in ("wrong", "uncertain") and i < n:
            taken.append(i)
            out.append((i, "review", f"audit_{verdict}"))
    rim_disagree = np.intersect1d(flags["hood_disagree"],
                                  np.flatnonzero(band == "rim"), assume_unique=False)
    small_rim = np.flatnonzero(live & have & (off[:, 2] < lo) & (band == "rim"))
    pools = {"rim_disagree": rim_disagree, "small_rim": small_rim, "jump": flags["jump"],
             "ambiguous": flags["ambiguous"], "causal_zone_disagree": flags["causal_zone_disagree"],
             "hood_disagree": flags["hood_disagree"]}
    room = max(0, REVIEW_PER_SESSION - len(out))
    per = max(1, math.ceil(room / len(pools)))
    for cat, pool in pools.items():
        if room <= 0:
            break
        got = spaced_sample(np.asarray(pool), min(per, room), taken, gap // 2, rng)
        out += [(i, "review", cat) for i in got]
        room -= len(got)
    for cat, k in POSITIVE_QUOTA.items():
        got = spaced_sample(np.flatnonzero(good & (band == cat)), k, taken, gap, rng)
        out += [(i, "positive", cat) for i in got]
    # Negatives: before the rat is first in and after it is last seen with
    # both trackers agreeing (the operator lifting it out). The rat is often
    # already in before the trackers agree, so a frame only counts as empty
    # when neither tracker sees anything near rat-sized; the rest are
    # checked by a person.
    first = int(flags["live"][0])
    last = int(np.flatnonzero(live & agree)[-1]) if (live & agree).any() else n
    outside = np.zeros(n, bool)
    outside[:max(0, first - NEGATIVE_MARGIN)] = True
    outside[min(n, last + NEGATIVE_MARGIN):] = True
    rat_off = np.nanmedian(off[live & have, 2])
    rat_hood = np.nanmedian(hood[live & np.isfinite(hood[:, 0]), 2])
    small = (np.nan_to_num(off[:, 2]) < NEGATIVE_MAX_FRACTION * rat_off) & \
            (np.nan_to_num(hood[:, 2]) < NEGATIVE_MAX_FRACTION * rat_hood)
    got = spaced_sample(np.flatnonzero(outside & small), NEGATIVES_PER_SESSION, taken, gap, rng)
    out += [(i, "negative", "empty") for i in got]
    got = spaced_sample(np.flatnonzero(outside & ~small), CHECK_EMPTY_PER_SESSION, taken, gap, rng)
    out += [(i, "review", "check_empty") for i in got]
    return sorted(out)


def find_box(tracker: apa_track.RatTracker, grey: np.ndarray, xy, scale: float):
    """(full-res (x0, y0, x1, y1), clipped) for the blob at the stored
    offline position, or (None, False) if no blob is there. The tracker here
    has a wider mask than the one the position came from (BOX_MASK_GROW), so
    the blob is the one covering that position, or else the nearest within
    MATCH_PX. clipped: the blob still reaches the mask's edge, so part of
    the rat may be outside it and the box too small."""
    if not np.isfinite(xy[0]):
        return None, False
    n, labels, stats, cents = tracker.components(grey)
    px, py = int(round(xy[0] * scale)), int(round(xy[1] * scale))
    best = None
    if 0 <= py < labels.shape[0] and 0 <= px < labels.shape[1] and labels[py, px]:
        best = int(labels[py, px])
    else:
        best_d = MATCH_PX * scale
        for k in range(1, n):
            dd = math.hypot(cents[k][0] - xy[0] * scale, cents[k][1] - xy[1] * scale)
            if dd < best_d:
                best, best_d = k, dd
    if best is None:
        return None, False
    x, y, w, h = (int(v) for v in stats[best, :4])
    ys, xs = np.nonzero(labels[y:y + h, x:x + w] == best)
    r = np.hypot(xs + x - tracker.centre[0], ys + y - tracker.centre[1])
    clipped = bool(r.max() >= tracker.radius - CLIP_PX)
    return (x / scale, y / scale, (x + w) / scale, (y + h) / scale), clipped


def box_fill(area: float, box) -> float:
    """The blob's area as a fraction of its box (both full-res px)."""
    x0, y0, x1, y1 = box
    return float(area) / max(1.0, (x1 - x0) * (y1 - y0))


def export_session(npz_path: str, out: str, share_root: str | None,
                   audit: dict, holdout: set[str], seed: int) -> list[dict]:
    d = dict(np.load(npz_path))
    report.remap_paths(d, share_root)
    session = str(d["session"])
    picks = choose_frames(d, audit, seed)
    if not picks:
        return []
    split = "valid" if rat_id(session) in holdout else "train"
    g = report.geom_dict(d["geom"])
    video = str(d["video"])
    cap = cv2.VideoCapture(video)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = apa_track.PROC_SIZE / max(w, h)
    size = (round(w * scale), round(h * scale))
    bg, _ = apa_track.background(video, size)
    yy, xx = np.mgrid[:size[1], :size[0]]
    radius = g["outer_r"] * scale * BOX_MASK_GROW
    arena = np.hypot(xx - g["cx"] * scale, yy - g["cy"] * scale) <= radius
    tracker = apa_track.RatTracker(bg, arena, 15, 150, 60.0,
                                   centre=(g["cx"] * scale, g["cy"] * scale), radius=radius)
    want = {i: (s, c) for i, s, c in picks}
    rows = []
    n_loose = 0
    last = max(want)
    for i in range(last + 1):
        if i not in want:
            if not cap.grab():
                break
            continue
        ok, frame = cap.read()
        if not ok:
            break
        kind, cat = want[i]
        box = None
        if kind != "negative" and cat != "check_empty":
            box, clipped = find_box(tracker, apa_track._prep(frame, size), d["offline"][i], scale)
            if box is None and kind == "positive":
                continue  # the stored blob isn't there: don't guess
            if clipped and kind == "positive":
                kind, cat = "review", f"clipped_{cat}"  # the box misses the part outside the mask
            elif kind == "positive" and box_fill(d["offline"][i][2], box) < MIN_FILL:
                if n_loose >= LOOSE_REVIEW_PER_SESSION:
                    continue
                n_loose += 1
                kind, cat = "review", f"loose_{cat}"
        root = "review" if kind == "review" else "dataset"
        name = f"{session}_f{i:06d}"
        img_dir = os.path.join(out, root, split, "images")
        lab_dir = os.path.join(out, root, split, "labels")
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lab_dir, exist_ok=True)
        cv2.imwrite(os.path.join(img_dir, name + ".jpg"), frame,
                    [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        with open(os.path.join(lab_dir, name + ".txt"), "w") as f:
            if box:
                x0, y0, x1, y1 = box
                f.write(f"0 {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} "
                        f"{(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}\n")
        rows.append({"image": f"{root}/{split}/images/{name}.jpg", "session": session,
                     "rat": rat_id(session), "frame": i, "ts_ns": int(d["ts"][i]),
                     "set": kind, "category": cat, "split": split,
                     "box": "" if box is None else " ".join(f"{v:.0f}" for v in box)})
    cap.release()
    return rows


def write_sheets(rows: list[dict], out: str, per_sheet: int = 30, cols: int = 6) -> None:
    """Contact sheets per set, each tile numbered; sheets/index.csv maps
    (sheet, tile) to the image and label files, for finding a bad one."""
    sheet_dir = os.path.join(out, "sheets")
    os.makedirs(sheet_dir, exist_ok=True)
    index = []
    for kind in ("positive", "review", "negative"):
        sel = [r for r in rows if r["set"] == kind]
        for s in range(0, len(sel), per_sheet):
            sheet = f"{kind}_{s // per_sheet:03d}.jpg"
            tiles = []
            for n, r in enumerate(sel[s:s + per_sheet]):
                index.append({"sheet": sheet, "tile": n, "image": r["image"],
                              "label": label_path(r["image"]), "session": r["session"],
                              "frame": r["frame"], "category": r["category"]})
                img = cv2.imread(os.path.join(out, r["image"]))
                if r["box"]:
                    x0, y0, x1, y1 = (int(v) for v in r["box"].split())
                    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 0), 3)
                t = cv2.resize(img, (SHEET_TILE, SHEET_TILE * img.shape[0] // img.shape[1]))
                cv2.rectangle(t, (0, 0), (SHEET_TILE, 18), (0, 0, 0), -1)
                cv2.putText(t, f"#{n} {r['rat']} f{r['frame']} {r['category']}", (3, 13),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)
                tiles.append(t)
            th = max(t.shape[0] for t in tiles)
            tiles = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) for t in tiles]
            while len(tiles) % cols:
                tiles.append(np.zeros_like(tiles[0]))
            grid = np.vstack([np.hstack(tiles[j:j + cols]) for j in range(0, len(tiles), cols)])
            cv2.imwrite(os.path.join(sheet_dir, sheet), grid, [cv2.IMWRITE_JPEG_QUALITY, 80])
    with open(os.path.join(sheet_dir, "index.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(index[0]) if index else ["sheet"])
        w.writeheader()
        w.writerows(index)


def label_path(image: str) -> str:
    """The YOLO label file next to an exported image (.../images/x.jpg ->
    .../labels/x.txt)."""
    head, name = os.path.split(image)
    return os.path.join(os.path.dirname(head), "labels", os.path.splitext(name)[0] + ".txt")


def write_calib(rows: list[dict], out: str, n: int, seed: int) -> int:
    """Copy n train images (mostly positives, a few negatives) into DIR/calib/
    for quantising the HEF: the same camera and lighting the model will see."""
    rng = np.random.default_rng(seed)
    pos = [r for r in rows if r["set"] == "positive" and r["split"] == "train"]
    neg = [r for r in rows if r["set"] == "negative" and r["split"] == "train"]
    k_neg = min(len(neg), round(n * CALIB_NEG_FRACTION))
    pick = [pos[j] for j in rng.choice(len(pos), min(len(pos), n - k_neg), replace=False)]
    pick += [neg[j] for j in rng.choice(len(neg), k_neg, replace=False)]
    calib = os.path.join(out, "calib")
    os.makedirs(calib, exist_ok=True)
    for r in pick:
        shutil.copy(os.path.join(out, r["image"]), calib)
    return len(pick)


def load_audit(path: str | None) -> dict[str, dict[int, str]]:
    audit: dict[str, dict[int, str]] = {}
    if path:
        with open(path) as f:
            for r in csv.DictReader(f):
                audit.setdefault(r["session"], {})[int(r["frame"])] = r.get("verdict", "")
    return audit


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("assess_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--share-root", default=None,
                    help="where the sessions live here, if the .npz were made elsewhere")
    ap.add_argument("--audit", default=None, help="audit verdicts CSV (tile,session,frame,flag,verdict)")
    ap.add_argument("--holdout", default="152,854", help="rat ids kept whole for valid/")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--calib", type=int, default=CALIB_N, help="HEF calibration images to copy (0: none)")
    args = ap.parse_args()
    audit = load_audit(args.audit)
    holdout = {h.strip() for h in args.holdout.split(",") if h.strip()}
    paths = sorted(glob.glob(os.path.join(args.assess_dir, "*.npz")))
    rows: list[dict] = []
    with ProcessPoolExecutor(args.jobs) as pool:
        futs = {pool.submit(export_session, p, args.out, args.share_root, audit, holdout,
                            args.seed + k): p for k, p in enumerate(paths)}
        for f in as_completed(futs):
            name = os.path.basename(futs[f])[:-4]
            try:
                got = f.result()
            except Exception as e:  # noqa: BLE001 -- report and carry on with the batch
                print(f"{name}: failed ({e!r})", flush=True)
                continue
            rows += got
            counts = {k: sum(r["set"] == k for r in got) for k in ("positive", "review", "negative")}
            print(f"{name}: {counts}", flush=True)
    rows.sort(key=lambda r: (r["session"], r["frame"]))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "manifest.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(args.out, "dataset", "data.yaml"), "w") as f:
        f.write("train: train/images\nval: valid/images\nnc: 1\nnames: ['rat']\n")
    write_sheets(rows, args.out)
    if args.calib:
        print(f"calib/: {write_calib(rows, args.out, args.calib, args.seed)} images")
    total = {k: sum(r["set"] == k for r in rows) for k in ("positive", "review", "negative")}
    print(f"done: {total}; holdout rats {sorted(holdout)} -> valid/")


if __name__ == "__main__":
    main()
