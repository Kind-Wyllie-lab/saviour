#!/usr/bin/env python3
"""
Download a labelled dataset from Roboflow (optional) and train
(fine-tune) a single-class "rat" detector for apa_camera -- one command
covering steps 4 in docs/readthedocs/apa_rat_detector_training.md.

Thin wrapper around Ultralytics' YOLO training API with defaults sized for
Hailo-8L deployment (small base model, square input matching
convert_to_hailo.py's --imgsz default of 640). Run this on a GPU workstation
or Colab, not the Pi -- training itself has nothing to do with Hailo, that
conversion is a separate step afterwards (tools/convert_to_hailo.py) that
needs x86-64 Linux and the Hailo Dataflow Compiler.

Two ways to point this at a dataset:
  1. --data path/to/dataset.yaml -- an already-assembled standard
     Ultralytics YOLO layout (see dataset.yaml.example in this folder, or
     this folder's README.md / docs/readthedocs/apa_rat_detector_training.md
     for how to get there from raw recordings via extract_frames.py).
  2. --roboflow-workspace/--roboflow-project/--roboflow-version -- downloads
     the dataset from Roboflow first (via download_roboflow_dataset.py in
     this same folder), then trains on it. Needs `pip install roboflow
     pyyaml` and ROBOFLOW_API_KEY set in the environment. Omit --roboflow-version
     to list available versions for the project and exit.

Usage:
    python train.py --data dataset.yaml
    python train.py --data dataset.yaml --base yolo11n.pt --epochs 150 --imgsz 640
    python train.py --data dataset.yaml --resume runs/detect/train/weights/last.pt

    python train.py --roboflow-workspace sidb-workshop \
        --roboflow-project rat-tracker-zh4ex   # lists versions, then exit
    python train.py --roboflow-workspace sidb-workshop \
        --roboflow-project rat-tracker-zh4ex --roboflow-version 1
"""

import argparse
import sys
from pathlib import Path

from download_roboflow_dataset import download_dataset, get_project, list_versions


def train_model(data: Path, base: str = "yolo11n.pt", epochs: int = 150,
                 imgsz: int = 640, batch: int = 16, device: str | None = None,
                 resume: Path | None = None, out_dir: Path | None = None) -> Path | None:
    """Train/fine-tune the detector and copy the best checkpoint to
    out_dir/ratnet.pt. Returns that path, or None if training didn't
    produce a checkpoint. Importable directly (e.g. from a notebook) as
    well as used by this script's own CLI."""
    from ultralytics import YOLO

    out_dir = out_dir or Path(__file__).resolve().parent
    model = YOLO(str(resume) if resume else base)

    print(f"Training on {data} ({epochs} epochs, imgsz={imgsz})")
    results = model.train(
        data=str(data),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        single_cls=True,  # one class: "rat" -- see dataset.yaml.example
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        print(f"[WARN] Expected checkpoint not found at {best} -- check {results.save_dir}")
        return None

    dest = out_dir / "ratnet.pt"
    dest.write_bytes(best.read_bytes())
    print(f"""
Done! Best checkpoint copied to:
  {dest}

Next: convert to a Hailo HEF and deploy --
  python tools/convert_to_hailo.py --model {dest}
See docs/readthedocs/apa_rat_detector_training.md for the full walkthrough,
including calibration images and updating object_detection.model_path.
""")
    return dest


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=None,
                         help="Path to dataset.yaml (see dataset.yaml.example). "
                              "Not needed if using --roboflow-* instead.")
    parser.add_argument("--roboflow-workspace", default=None,
                         help="Roboflow workspace slug -- downloads the "
                              "dataset before training instead of --data")
    parser.add_argument("--roboflow-project", default=None,
                         help="Roboflow project slug")
    parser.add_argument("--roboflow-version", type=int, default=None,
                         help="Roboflow dataset version. Omit to list "
                              "available versions and exit.")
    parser.add_argument("--roboflow-format", default="yolov11",
                         help="Roboflow export format (default: yolov11)")
    parser.add_argument("--roboflow-out", type=Path, default=Path("training_data"),
                         help="Where to download the Roboflow dataset "
                              "(default: training_data)")
    parser.add_argument("--base", default="yolo11n.pt",
                         help="Base checkpoint to fine-tune from (default: "
                              "yolo11n.pt -- the smallest YOLO11 model, a "
                              "good fit for hailo8l). Ultralytics downloads "
                              "it automatically if not already local.")
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--imgsz", type=int, default=640,
                         help="Must match what you'll later pass to "
                              "tools/convert_to_hailo.py's --imgsz")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None,
                         help="e.g. 0 for first GPU, cpu for CPU "
                              "(default: Ultralytics auto-picks)")
    parser.add_argument("--resume", type=Path, default=None,
                         help="Resume/continue training from an existing checkpoint")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent,
                         help="Where to copy the final best.pt (default: this folder)")
    args = parser.parse_args()

    if args.data and args.roboflow_workspace:
        print("[ERROR] Pass either --data or --roboflow-workspace/--roboflow-project, not both")
        sys.exit(1)

    if args.roboflow_workspace:
        if not args.roboflow_project:
            print("[ERROR] --roboflow-project is required alongside --roboflow-workspace")
            sys.exit(1)
        try:
            project = get_project(args.roboflow_workspace, args.roboflow_project)
            if args.roboflow_version is None:
                list_versions(project)
                return
            args.data = download_dataset(
                project, args.roboflow_version, args.roboflow_format, args.roboflow_out)
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
    elif not args.data:
        print("[ERROR] Need either --data or --roboflow-workspace/--roboflow-project/--roboflow-version")
        sys.exit(1)

    try:
        from ultralytics import YOLO  # noqa: F401 -- import checked here, used inside train_model
    except ImportError:
        print("[ERROR] ultralytics not installed: pip install 'ultralytics>=8.3'")
        return

    if not args.data.exists():
        print(f"[ERROR] Dataset config not found: {args.data}")
        return

    train_model(
        data=args.data,
        base=args.base,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        resume=args.resume,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
