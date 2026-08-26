#!/usr/bin/env python3
"""
prepare_lp_project.py -- assemble a real Lightning Pose training project
from hand-labelled data, for training a single-view 2D pose model that
tools/convert_lp_pose_to_hailo.py (repo root) can later compile for Hailo.

Background: the multiview 3D checkpoint in SHARE_lp3d_package can't run on
Hailo (Hailo runs one model per video stream, not a multiview transformer).
A companion drop, LP3D_hailo_extras, provides real hand-labelled single-view
data instead: DeepLabCut/Lightning-Pose-format CollectedData_camN.csv files
plus the matching labelled frames, one set per camera, all 9 cameras sharing
the same 92 synchronised timepoints. That's exactly what this script expects
as --source-dir (the directory containing training_labels/).

This script does NOT train anything -- it only assembles the on-disk project
layout and config.yaml that Lightning Pose's own `litpose train` command
expects, so that step is a real, documented Lightning Pose command rather
than another guessed-at wrapper. See this folder's README.md for the actual
training invocation once a project has been prepared.

Config values (image_resize_dims, backbone, etc.) are filled in from a
verified copy of Lightning Pose's own config_default.yaml structure
(confirmed against the upstream repo, not guessed) -- overridable via CLI
flags, not hardcoded as gospel; a small dataset like this may need real
tuning (epoch count, backbone choice) that no script can decide for you.

Usage:
    python3 src/modules/variants/lightning_camera/training/prepare_lp_project.py \
        --source-dir "/path/to/LP3D_hailo_extras" \
        --camera cam1 \
        --out ~/lp_projects/cam1

    # then, in a lightning-pose environment (see requirements.txt):
    litpose train ~/lp_projects/cam1/config.yaml --output_dir ~/lp_projects/cam1/outputs
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml (see this folder's requirements.txt)")

# .../saviour/src/modules/variants/lightning_camera/training/prepare_lp_project.py
# -> .../saviour
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_MODULE_CONFIG = (
    REPO_ROOT / "src/modules/variants/lightning_camera/lightning_camera_config.json"
)


def load_keypoint_names(module_config_path: Path) -> list[str]:
    """Single source of truth for keypoint names/order -- read from the
    module's own shipped config rather than re-typing the list here, so
    the two can never drift apart. Order matters: HailoPoseDetector reads
    a compiled model's heatmap channels in this exact order (see
    lightning_camera_module.py's own docstring), and it's also the column
    order Lightning Pose expects to find in the labels CSV -- which the
    real CollectedData_camN.csv files already match, confirmed by hand."""
    with open(module_config_path) as f:
        cfg = json.load(f)
    names = cfg["pose_estimation"]["keypoint_names"]
    if not names:
        sys.exit(f"No pose_estimation.keypoint_names found in {module_config_path}")
    return names


def assemble_project(source_dir: Path, camera: str, out_dir: Path) -> Path:
    """Copies frames + labels CSV into the on-disk layout Lightning Pose
    expects (data_dir/labeled-data/<video>/*.jpg, data_dir/CollectedData.csv
    with paths relative to data_dir). Returns the path to the copied CSV,
    read back so the caller can sanity-check row/keypoint counts."""
    labels_dir = source_dir / "training_labels"
    csv_src = labels_dir / f"CollectedData_{camera}.csv"
    frames_src = labels_dir / "frames" / camera
    if not csv_src.is_file():
        sys.exit(f"Not found: {csv_src} (is --camera correct, e.g. 'cam1'?)")
    if not frames_src.is_dir():
        sys.exit(f"Not found: {frames_src}")

    # The CSV's image-path column already reads
    # "labeled-data/single_unit_1_<camera>/imgNNNNNNNN.jpg" -- confirmed by
    # inspecting all 9 real CSVs, all follow this exact naming, so this is
    # not a guess -- reproduce that same subdirectory name on disk.
    video_name = f"single_unit_1_{camera}"
    dest_frames_dir = out_dir / "labeled-data" / video_name
    dest_frames_dir.mkdir(parents=True, exist_ok=True)

    n_copied = 0
    for jpg in sorted(frames_src.glob("*.jpg")):
        shutil.copy2(jpg, dest_frames_dir / jpg.name)
        n_copied += 1
    if n_copied == 0:
        sys.exit(f"No .jpg frames found in {frames_src}")

    csv_dest = out_dir / "CollectedData.csv"
    shutil.copy2(csv_src, csv_dest)

    n_labeled_rows = sum(1 for _ in open(csv_dest)) - 3  # 3 DLC header rows
    print(f"  Copied {n_copied} frame(s) -> {dest_frames_dir}")
    print(f"  Copied labels ({n_labeled_rows} row(s)) -> {csv_dest}")
    if n_copied != n_labeled_rows:
        print(
            f"  WARNING: frame count ({n_copied}) != labeled row count "
            f"({n_labeled_rows}) -- some labeled frames may be missing from "
            f"frames/{camera}/, or vice versa. Check before trusting a "
            f"trained model."
        )
    return csv_dest


class TrainOptions(NamedTuple):
    resize_dims: tuple[int, int]
    # Fraction of labelled frames used for train/validation; whatever's left
    # (1 - train_prob - val_prob) becomes the held-out test set. Upstream's
    # own default (0.95/0.05) is meant for datasets with thousands of
    # frames, where a 5% validation split is still hundreds of frames and a
    # separate test set isn't the only way accuracy gets checked. With only
    # ~92 labelled frames here, 0.95+0.05 leaves EXACTLY ZERO for test --
    # every non-training frame goes to validation (used for model
    # selection/early stopping during training, not a fair post-hoc check),
    # so "how well does it generalize" would have nothing held out to
    # answer that with. Default here is deliberately different from
    # upstream's for that reason.
    train_prob: float
    val_prob: float
    backbone: str


def build_config(out_dir: Path, keypoint_names: list[str], video_dir: Path,
                  opts: TrainOptions) -> dict:
    """A Lightning Pose config.yaml, in the same shape as upstream's own
    config_default.yaml (confirmed against the real file in the
    lightning-pose repo, not reconstructed from memory) -- only the fields
    that actually need a per-project value are filled in here; everything
    else is upstream's own documented default. model_type is pinned to
    "heatmap" deliberately: HailoPoseDetector._decode_heatmaps() (module
    side) only knows how to decode a heatmap output, not a regression one."""
    resize_height, resize_width = opts.resize_dims
    return {
        "data": {
            "image_resize_dims": {"height": resize_height, "width": resize_width},
            "data_dir": str(out_dir.resolve()),
            "video_dir": str(video_dir.resolve()),
            "csv_file": "CollectedData.csv",
            "num_keypoints": len(keypoint_names),
            "keypoint_names": keypoint_names,
            "mirrored_column_matches": None,
            "columns_for_singleview_pca": None,
        },
        "training": {
            "imgaug": "dlc",
            "imgaug_hflip": False,
            "train_batch_size": 16,
            "val_batch_size": 32,
            "test_batch_size": 32,
            "train_prob": opts.train_prob,
            "val_prob": opts.val_prob,
            "train_frames": 1,
            "num_gpus": 1,
            "unfreezing_epoch": 20,
            "min_epochs": 300,
            "max_epochs": 300,
            "log_every_n_steps": 10,
            "check_val_every_n_epoch": 5,
            "ckpt_every_n_epochs": None,
            "early_stopping": False,
            "early_stop_patience": 3,
            "rng_seed_data_pt": 0,
            "rng_seed_model_pt": 0,
            "optimizer": "Adam",
            "optimizer_params": {"learning_rate": 1e-3},
            "lr_scheduler": "multisteplr",
            "lr_scheduler_params": {
                "multisteplr": {"milestones": [150, 200, 250], "gamma": 0.5},
            },
            "uniform_heatmaps_for_nan_keypoints": True,
        },
        "model": {
            "losses_to_use": [],
            "backbone": opts.backbone,
            "model_type": "heatmap",
            "heatmap_loss_type": "mse",
            "model_name": "lightning_camera_single_view",
            "checkpoint": None,
        },
        "dali": {
            "base": {
                "train": {"sequence_length": 32},
                "predict": {"sequence_length": 96},
            },
            "context": {
                "train": {"batch_size": 16},
                "predict": {"sequence_length": 96},
            },
        },
        "losses": {
            "pca_multiview": {
                "log_weight": 11.0, "components_to_keep": 3, "epsilon": None,
            },
            "pca_singleview": {
                "log_weight": 11.0, "components_to_keep": 0.99, "epsilon": None,
            },
            "temporal": {
                "log_weight": 11.0, "epsilon": 20.0, "prob_threshold": 0.05,
            },
        },
        "eval": {
            "predict_vids_after_training": True,
            "test_videos_directory": str(video_dir.resolve()),
            "save_vids_after_training": False,
            "colormap": "cool",
            "confidence_thresh_for_vid": 0.9,
        },
        "callbacks": {
            "anneal_weight": {
                "attr_name": "total_unsupervised_importance",
                "init_val": 0.0, "increase_factor": 0.01,
                "final_val": 1.0, "freeze_until_epoch": 60,
            },
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source-dir", required=True, type=Path,
                     help="Path to LP3D_hailo_extras (contains training_labels/)")
    ap.add_argument("--camera", required=True,
                     help="Which view to train on, e.g. cam1 .. cam9 -- pick "
                          "whichever camera matches the Hailo Pi you'll deploy to")
    ap.add_argument("--out", required=True, type=Path,
                     help="Output Lightning Pose project directory "
                          "(created if missing)")
    ap.add_argument("--module-config", type=Path, default=DEFAULT_MODULE_CONFIG,
                     help="lightning_camera_config.json to read keypoint_names from "
                          f"(default: {DEFAULT_MODULE_CONFIG})")
    ap.add_argument("--resize-height", type=int, default=512,
                     help="Must be a multiple of 128 (Lightning Pose requirement). "
                          "Default 512x896 approximates the real frames' 1296x2304 "
                          "aspect ratio (16:9-ish) -- not validated against actual "
                          "training results, a starting point to tune from.")
    ap.add_argument("--resize-width", type=int, default=896)
    ap.add_argument("--train-prob", type=float, default=0.7,
                     help="Fraction of labelled frames used for training. "
                          "Default 0.7/0.15 (train/val), leaving 0.15 held out "
                          "as a test set -- deliberately different from "
                          "Lightning Pose's own upstream default (0.95/0.05, "
                          "which leaves zero frames for test on a small "
                          "dataset like this one; see TrainOptions for why)")
    ap.add_argument("--val-prob", type=float, default=0.15,
                     help="Fraction of labelled frames used for validation "
                          "during training. See --train-prob.")
    ap.add_argument("--backbone", default="resnet50_animal_ap10k",
                     help="Lightning Pose backbone -- resnet50_animal_ap10k is "
                          "pretrained on animal pose data specifically, a "
                          "reasonable starting point for a rodent, not "
                          "independently benchmarked here against alternatives")
    ap.add_argument("--copy-eval-video", action="store_true",
                     help="Also copy labelled_videos/<camera>_labeled.mp4 into "
                          "the project's video_dir, so Lightning Pose's "
                          "post-training prediction step has something to run "
                          "on (note: that video has the OLD multiview model's "
                          "predictions burned in, not ground truth -- fine for "
                          "an eyeball sanity check, not a real accuracy metric)")
    args = ap.parse_args()

    if args.resize_height % 128 or args.resize_width % 128:
        sys.exit(
            f"--resize-height/--resize-width must both be multiples of 128 "
            f"(got {args.resize_height}x{args.resize_width})"
        )
    if args.train_prob + args.val_prob >= 1.0:
        sys.exit(
            f"--train-prob + --val-prob must be < 1.0 so something is left "
            f"over for the test set (got {args.train_prob} + {args.val_prob} "
            f"= {args.train_prob + args.val_prob})"
        )

    keypoint_names = load_keypoint_names(args.module_config)
    print(f"Keypoints ({len(keypoint_names)}, from {args.module_config.name}): "
          f"{', '.join(keypoint_names)}")

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"\nAssembling project for {args.camera} -> {args.out}")
    csv_dest = assemble_project(args.source_dir, args.camera, args.out)

    video_dir = args.out / "videos"
    video_dir.mkdir(exist_ok=True)
    if args.copy_eval_video:
        vid_src = (args.source_dir / "labelled_videos"
                   / f"junetest_{args.camera}_labeled.mp4")
        if vid_src.is_file():
            shutil.copy2(vid_src, video_dir / vid_src.name)
            print(f"  Copied eval video -> {video_dir / vid_src.name}")
        else:
            print(f"  --copy-eval-video given but not found: {vid_src}")

    opts = TrainOptions(
        resize_dims=(args.resize_height, args.resize_width),
        train_prob=args.train_prob,
        val_prob=args.val_prob,
        backbone=args.backbone,
    )
    config = build_config(args.out, keypoint_names, video_dir, opts)
    config_path = args.out / "config.yaml"
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    print(f"  Wrote config -> {config_path}")

    print(f"\nProject ready. Verify the CSV parses as expected:\n"
          f"  head -3 {csv_dest}\n"
          f"Then, in a lightning-pose environment (this folder's requirements.txt):\n"
          f"  litpose train {config_path} --output_dir {args.out / 'outputs'}")


if __name__ == "__main__":
    main()
