#!/usr/bin/env python3
"""
Convert a YOLOv8/YOLO11 .pt model to a Hailo HEF for use on Raspberry Pi 5
with the Hailo AI HAT (hailo8l) or Hailo-8 (hailo8).

The output HEF includes Hailo's NMS post-processing, making it directly
compatible with picamera2's Hailo integration and SAVIOUR's HailoDetector.

IMPORTANT — run this on x86-64 Linux, not the Pi itself.
  The Hailo Dataflow Compiler (DFC) does not support ARM.

Requirements:
    pip install "ultralytics>=8.3"
    pip install hailo_dataflow_compiler   # wheel from https://developer.hailo.ai
    pip install onnx onnxruntime          # for ONNX inspection

Usage:
    python tools/convert_to_hailo.py
    python tools/convert_to_hailo.py --model src/modules/examples/apa_camera/ratnet.pt
    python tools/convert_to_hailo.py --imgsz 416 --hw-arch hailo8l
    python tools/convert_to_hailo.py --calib-dir /path/to/calibration/images
    python tools/convert_to_hailo.py --onnx-only   # stop after ONNX export (can run on Pi)

Calibration images:
    Quantization accuracy improves significantly with real images.
    Supply 64–128 images representative of your experimental setup
    (top-down arena shots with and without a rat) via --calib-dir.
    Without them, random calibration is used — still functional but
    may reduce detection accuracy slightly.
"""

import argparse
import platform
import sys
import os
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_x86():
    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        print(f"[ERROR] Hailo DFC requires x86-64 Linux. This machine is {machine}.")
        print("        Run this script on your development/lab PC, not the Pi.")
        print("        Use --onnx-only on the Pi to stop after ONNX export.")
        sys.exit(1)


def export_onnx(model_path: Path, imgsz: int, out_dir: Path) -> Path:
    """Export .pt → .onnx using Ultralytics."""
    try:
        from ultralytics import YOLO
    except ImportError:
        print("[ERROR] ultralytics not installed: pip install 'ultralytics>=8.3'")
        sys.exit(1)

    print(f"\n[1/4] Exporting {model_path.name} → ONNX (imgsz={imgsz})")
    model = YOLO(str(model_path))

    # Report model metadata
    nc = model.model.nc if hasattr(model.model, "nc") else "?"
    names = model.names if hasattr(model, "names") else {}
    print(f"      Classes: {nc} → {list(names.values()) if names else '(unknown)'}")

    onnx_path = out_dir / model_path.with_suffix(".onnx").name
    model.export(
        format="onnx",
        imgsz=imgsz,
        opset=11,
        simplify=True,
        dynamic=False,
    )
    # Ultralytics writes the ONNX next to the .pt
    default_out = model_path.with_suffix(".onnx")
    if default_out.exists() and default_out != onnx_path:
        shutil.move(str(default_out), str(onnx_path))

    print(f"      Saved:  {onnx_path}")
    return onnx_path, int(nc) if isinstance(nc, int) else 1


def inspect_onnx(onnx_path: Path):
    """Return (input_name, output_names) from the ONNX graph."""
    try:
        import onnx
        model = onnx.load(str(onnx_path))
        inputs  = [i.name for i in model.graph.input]
        outputs = [o.name for o in model.graph.output]
        print(f"      ONNX inputs:  {inputs}")
        print(f"      ONNX outputs: {outputs}")
        return inputs[0], outputs
    except ImportError:
        print("[WARN] onnx not installed — skipping graph inspection")
        return "images", ["output0"]


def load_calibration(calib_dir: Path, imgsz: int, n: int = 64):
    """Load calibration images as uint8 (N, H, W, 3) array."""
    import numpy as np
    try:
        import cv2
    except ImportError:
        print("[WARN] opencv not found — using random calibration data")
        return np.random.randint(0, 256, (n, imgsz, imgsz, 3), dtype=np.uint8)

    paths = sorted(calib_dir.glob("*.jpg")) + sorted(calib_dir.glob("*.png"))
    if not paths:
        print(f"[WARN] No .jpg/.png found in {calib_dir} — using random calibration")
        return np.random.randint(0, 256, (n, imgsz, imgsz, 3), dtype=np.uint8)

    paths = paths[:n]
    images = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (imgsz, imgsz))
        images.append(img)

    print(f"      Loaded {len(images)} calibration images from {calib_dir}")
    arr = np.stack(images).astype(np.uint8)
    return arr


def compile_hef(onnx_path: Path, num_classes: int, imgsz: int,
                hw_arch: str, calib_dir: Path, out_dir: Path) -> Path:
    """
    Parse ONNX → quantize → compile to HEF using the Hailo DFC Python SDK.

    The NMS post-processing is added as a model script (.alls) so the output
    format matches what picamera2's Hailo integration expects:
        results[class_id] = ndarray(N, 5)  [y1, x1, y2, x2, score]  normalised 0-1
    """
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        print("[ERROR] hailo_dataflow_compiler not installed.")
        print("        Download the wheel from https://developer.hailo.ai and:")
        print("        pip install hailo_dataflow_compiler-*.whl")
        sys.exit(1)

    import numpy as np

    model_name = onnx_path.stem
    har_path   = out_dir / f"{model_name}.har"
    hef_path   = out_dir / f"{model_name}.hef"

    # ── 2. Parse ONNX → HAR ──────────────────────────────────────────────────
    print(f"\n[2/4] Parsing ONNX → HAR  ({hw_arch})")
    runner = ClientRunner(hw_arch=hw_arch)

    input_name, output_names = inspect_onnx(onnx_path)

    hn, npz = runner.translate_onnx_model(
        str(onnx_path),
        model_name,
        net_input_shapes={input_name: [1, 3, imgsz, imgsz]},
    )
    runner.save_har(str(har_path))
    print(f"      Saved HAR: {har_path}")

    # ── 3. Quantize with NMS post-processing ─────────────────────────────────
    print(f"\n[3/4] Quantizing  (classes={num_classes}, imgsz={imgsz})")
    runner = ClientRunner(hw_arch=hw_arch, har=str(har_path))

    # YOLO11/v8 anchor-free NMS — same meta_arch as yolov8
    # Normalisation is applied inside the model script so calibration data
    # should be supplied as uint8 [0–255] RGB images.
    nms_script = f"""\
normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])
nms_postprocess(meta_arch=yolov8, engine=cpu, \
nms_scores_th=0.3, nms_iou_th=0.7, \
image_dims=[{imgsz}, {imgsz}], classes={num_classes})
"""
    runner.load_model_script(nms_script)

    # Build calibration dataset
    if calib_dir and calib_dir.exists():
        calib_data = load_calibration(calib_dir, imgsz)
    else:
        print("      No --calib-dir supplied — using random calibration data.")
        print("      For best accuracy supply 64+ representative arena images.")
        calib_data = np.random.randint(0, 256, (64, imgsz, imgsz, 3), dtype=np.uint8)

    runner.optimize(calib_data)
    runner.save_har(str(har_path))
    print(f"      Quantized HAR saved: {har_path}")

    # ── 4. Compile → HEF ─────────────────────────────────────────────────────
    print(f"\n[4/4] Compiling → HEF")
    hef_bytes = runner.compile()
    hef_path.write_bytes(hef_bytes)
    print(f"      Saved HEF: {hef_path}")
    return hef_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    src_dir = Path(__file__).resolve().parent.parent
    default_model = src_dir / "src/modules/examples/apa_camera/ratnet.pt"

    parser = argparse.ArgumentParser(
        description="Convert YOLOv8/YOLO11 .pt → Hailo HEF"
    )
    parser.add_argument("--model",     type=Path, default=default_model,
                        help="Path to .pt weights (default: apa_camera/ratnet.pt)")
    parser.add_argument("--imgsz",     type=int, default=640,
                        help="Inference input size in pixels (default: 640)")
    parser.add_argument("--hw-arch",   default="hailo8l",
                        choices=["hailo8l", "hailo8"],
                        help="Hailo hardware target (default: hailo8l = AI HAT for Pi 5)")
    parser.add_argument("--calib-dir", type=Path, default=None,
                        help="Directory of calibration images (jpg/png)")
    parser.add_argument("--out-dir",   type=Path,
                        default=src_dir / "src/modules/examples/apa_camera",
                        help="Output directory for .onnx, .har, .hef")
    parser.add_argument("--onnx-only", action="store_true",
                        help="Stop after ONNX export (safe to run on Pi/ARM)")
    args = parser.parse_args()

    if not args.onnx_only:
        check_x86()

    if not args.model.exists():
        print(f"[ERROR] Model not found: {args.model}")
        sys.exit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: ONNX
    onnx_path, num_classes = export_onnx(args.model, args.imgsz, args.out_dir)

    if args.onnx_only:
        print("\nDone (ONNX only). Copy the .onnx to an x86 machine and run:")
        print(f"  python tools/convert_to_hailo.py --model {args.model.name} \\")
        print(f"      --imgsz {args.imgsz} --hw-arch {args.hw_arch}")
        return

    # Steps 2-4: HAR + HEF
    hef_path = compile_hef(
        onnx_path, num_classes, args.imgsz,
        args.hw_arch, args.calib_dir, args.out_dir
    )

    print(f"""
Done!  HEF written to:
  {hef_path}

To use it on the Pi, update the module config:
  object_detection.model_path = "{hef_path.name}"
  object_detection.enabled    = true

Or copy it to the Pi and update active_config.json directly.
""")


if __name__ == "__main__":
    main()
