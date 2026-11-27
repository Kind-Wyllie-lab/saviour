#!/usr/bin/env python3
"""
Pool a multi-view Lightning Pose labelled dataset into one single-view project,
ready to train a per-camera heatmap model for hailo_camera.

Input (an LP / DLC multi-view label export):
  <labels-dir>/CollectedData_<view>.csv   3 header rows (scorer, bodyparts,
                                          coords), then one row per frame
  <labels-dir>/frames/<view>/<image>      the labelled frames named in the CSVs

Output (<out>/, an LP single-view project):
  labeled-data/<view>/<image>   copies of every labelled frame
  CollectedData.csv             training labels, all views pooled
  CollectedData_heldout.csv     held-out labels, never seen in training
  heldout_frames.txt            which frame ids were held out
  videos/                       optional copies of --videos (LP predicts on
                                these after training: the reference for the
                                Hailo comparison)
  config.yaml                   LP 2.4 single-view config (from --base-config)
  set_paths.py                  rewrites data_dir / video_dir to wherever the
                                folder ends up, run once before training

The hold-out is by frame id, not by row: the same instant seen from another
camera is nearly the same pose, so a per-row split would leak test frames
into training. LP's own train/val split (inside CollectedData.csv) can still
put two views of one instant on both sides; that only affects validation.

Typical run:
  python src/modules/variants/hailo_camera/training/prepare_lp_pooled.py \\
      --labels-dir LP3D_hailo_extras/training_labels \\
      --base-config config_default.yaml --out lp_single_view \\
      --videos SHARE_lp3d_package/test_videos/*.mp4
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import shutil
import sys
from pathlib import Path

import yaml

SET_PATHS = '''\
#!/usr/bin/env python3
"""Point config.yaml's data_dir / video_dir at this folder. Run once after
copying the project to the training machine (needs PyYAML, which LP brings)."""
from pathlib import Path

import yaml

here = Path(__file__).resolve().parent
cfg_path = here / "config.yaml"
cfg = yaml.safe_load(cfg_path.read_text())
cfg["data"]["data_dir"] = str(here)
cfg["data"]["video_dir"] = str(here / "videos")
cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
print(f"data_dir  = {here}")
print(f"video_dir = {here / 'videos'}")
'''


def die(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def read_labels(path: Path) -> tuple[list[list[str]], list[list[str]]]:
    rows = list(csv.reader(path.open(newline="")))
    if len(rows) < 4 or rows[0][0] != "scorer" or rows[2][0] != "coords":
        die(f"{path}: not an LP/DLC label file (expected scorer/bodyparts/coords)")
    return rows[:3], rows[3:]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--labels-dir", type=Path, required=True)
    p.add_argument("--views", nargs="+",
                   help="views to pool (default: every CollectedData_<view>.csv)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--base-config", type=Path, required=True,
                   help="LP's scripts/configs/config_default.yaml (same LP version "
                        "as training)")
    p.add_argument("--height", type=int, default=384)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--backbone", default="resnet50_animal_ap10k")
    p.add_argument("--heldout-frac", type=float, default=0.15)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--videos", type=Path, nargs="*", default=[])
    args = p.parse_args()

    for dim in (args.height, args.width):
        if dim % 128:
            die(f"image size {dim} is not a multiple of 128 (LP requires it)")
    if args.out.exists() and any(args.out.iterdir()):
        die(f"{args.out} exists and is not empty")

    if args.views:
        views = args.views
    else:
        views = sorted(
            m.group(1)
            for f in args.labels_dir.glob("CollectedData_*.csv")
            if (m := re.fullmatch(r"CollectedData_(.+)\.csv", f.name))
        )
    if not views:
        die(f"no CollectedData_<view>.csv in {args.labels_dir}")

    header = None
    per_view: dict[str, list[list[str]]] = {}
    for view in views:
        h, rows = read_labels(args.labels_dir / f"CollectedData_{view}.csv")
        if header is None:
            header = h
        elif h[1:] != header[1:]:
            die(f"view {view}: keypoint columns differ from view {views[0]}")
        per_view[view] = rows
    keypoints = header[1][1::2]

    frame_ids = sorted({Path(r[0]).name for rows in per_view.values() for r in rows})
    rng = random.Random(args.seed)
    n_hold = round(len(frame_ids) * args.heldout_frac)
    heldout = set(rng.sample(frame_ids, n_hold))

    train_rows, hold_rows = [], []
    for view, rows in per_view.items():
        dst_dir = args.out / "labeled-data" / view
        dst_dir.mkdir(parents=True, exist_ok=True)
        for r in rows:
            name = Path(r[0]).name
            src = args.labels_dir / "frames" / view / name
            if not src.is_file():
                die(f"missing frame {src}")
            shutil.copy2(src, dst_dir / name)
            row = [f"labeled-data/{view}/{name}", *r[1:]]
            if not any(v.strip() for v in r[1:]):
                continue  # nothing labelled in this view
            (hold_rows if name in heldout else train_rows).append(row)

    for fname, rows in (("CollectedData.csv", train_rows),
                        ("CollectedData_heldout.csv", hold_rows)):
        with (args.out / fname).open("w", newline="") as f:
            w = csv.writer(f)
            w.writerows(header)
            w.writerows(rows)
    (args.out / "heldout_frames.txt").write_text("\n".join(sorted(heldout)) + "\n")

    vid_dir = args.out / "videos"
    vid_dir.mkdir()
    for v in args.videos:
        shutil.copy2(v, vid_dir / v.name)

    cfg = yaml.safe_load(args.base_config.read_text())
    d, t, m = cfg["data"], cfg["training"], cfg["model"]
    d["image_resize_dims"] = {"height": args.height, "width": args.width}
    d["data_dir"] = "/replace/with/your/path"  # set_paths.py fills these in
    d["video_dir"] = "/replace/with/your/path/videos"
    d["csv_file"] = "CollectedData.csv"
    d["num_keypoints"] = len(keypoints)
    d["keypoint_names"] = keypoints
    d["mirrored_column_matches"] = None
    d["columns_for_singleview_pca"] = None
    d["downsample_factor"] = 2  # hailo_camera's decoder is unbiased at 2
    t["train_batch_size"] = args.batch_size
    t["train_prob"] = 0.9
    t["val_prob"] = 0.1
    m["backbone"] = args.backbone
    m["model_type"] = "heatmap"  # one frame in, heatmaps out: Hailo-compatible
    m["losses_to_use"] = []
    m["model_name"] = f"single_view_{len(views)}views_{args.height}x{args.width}"
    cfg["eval"]["predict_vids_after_training"] = bool(args.videos)
    (args.out / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    (args.out / "set_paths.py").write_text(SET_PATHS)

    print(f"views:     {', '.join(views)}")
    print(f"keypoints: {len(keypoints)} ({', '.join(keypoints)})")
    print(f"frame ids: {len(frame_ids)} ({n_hold} held out)")
    print(f"rows:      {len(train_rows)} train, {len(hold_rows)} held out")
    print(f"videos:    {len(args.videos)}")
    print(f"written:   {args.out}")


if __name__ == "__main__":
    main()
