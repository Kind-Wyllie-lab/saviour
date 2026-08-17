#!/usr/bin/env python3
"""
Train (fine-tune) a single-class "rat" detector for apa_camera.

Thin wrapper around Ultralytics' YOLO training API with defaults sized for
Hailo-8L deployment (small base model, square input matching
convert_to_hailo.py's --imgsz default of 640). Run this on a GPU workstation
or Colab, not the Pi -- training itself has nothing to do with Hailo, that
conversion is a separate step afterwards (tools/convert_to_hailo.py).

Expects a labelled dataset in standard Ultralytics YOLO format:
    dataset/
      images/train/*.jpg
      images/val/*.jpg
      labels/train/*.txt   # "0 x_center y_center width height" per box (normalised 0-1)
      labels/val/*.txt
      dataset.yaml         # see dataset.yaml.example in this folder

See this folder's README.md / docs/readthedocs/apa_rat_detector_training.md
for how to get from raw recordings (extract_frames.py) to a labelled
dataset in this shape.

Usage:
    python train.py --data dataset.yaml
    python train.py --data dataset.yaml --base yolo11n.pt --epochs 150 --imgsz 640
    python train.py --data dataset.yaml --resume runs/detect/train/weights/last.pt
"""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True,
                         help="Path to dataset.yaml (see dataset.yaml.example)")
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

    try:
        from ultralytics import YOLO
    except ImportError:
        print("[ERROR] ultralytics not installed: pip install 'ultralytics>=8.3'")
        return

    if not args.data.exists():
        print(f"[ERROR] Dataset config not found: {args.data}")
        return

    model = YOLO(str(args.resume) if args.resume else args.base)

    print(f"Training on {args.data} ({args.epochs} epochs, imgsz={args.imgsz})")
    results = model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        single_cls=True,  # one class: "rat" -- see dataset.yaml.example
    )

    best = Path(results.save_dir) / "weights" / "best.pt"
    if best.exists():
        dest = args.out_dir / "ratnet.pt"
        dest.write_bytes(best.read_bytes())
        print(f"""
Done! Best checkpoint copied to:
  {dest}

Next: convert to a Hailo HEF and deploy --
  python tools/convert_to_hailo.py --model {dest}
See docs/readthedocs/apa_rat_detector_training.md for the full walkthrough,
including calibration images and updating object_detection.model_path.
""")
    else:
        print(f"[WARN] Expected checkpoint not found at {best} -- "
              f"check {results.save_dir}")


if __name__ == "__main__":
    main()
