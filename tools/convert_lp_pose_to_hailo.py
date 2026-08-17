#!/usr/bin/env python3
"""
Convert a single-view Lightning Pose checkpoint to a Hailo HEF for use on
Raspberry Pi 5 with the Hailo AI HAT (hailo8l) or Hailo-8 (hailo8).

Sibling to tools/convert_to_hailo.py (YOLO object detection -> HEF), kept as
a SEPARATE script rather than a mode of that one: that tool is
Ultralytics-specific end-to-end (its ONNX export calls `ultralytics.YOLO(...)
.export()`, and its DFC quantization step hardcodes a YOLO NMS postprocess
model script). A pose heatmap model needs neither — the DFC parse/quantize/
compile shape below is copied from that tool's compile_hef(), not shared.

IMPORTANT ARCHITECTURE NOTE — read before using this on a real model:
This is for a SINGLE-VIEW 2D pose CNN (one camera in -> per-keypoint
heatmaps out), NOT the multiview transformer in
src/modules/variants/lightning_camera/SHARE_lp3d_package/. That multiview
model cannot be compiled for Hailo at all (see that package's README) — it
needs 5 synchronized views at once with cross-view attention, which has no
single-camera equivalent. This script is only useful once a *separate*
single-view Lightning Pose model (ResNet/MobileNet backbone) exists.

IMPORTANT — run the DFC step (Step 2) on x86-64 Linux, not the Pi itself.
  The Hailo Dataflow Compiler (DFC) does not support ARM.

Two steps, can run on different machines:

  Step 1 — ONNX export (needs the lightning-pose Python env; CPU is fine):
    python tools/convert_lp_pose_to_hailo.py export-onnx \\
        --checkpoint path/to/model.ckpt --config path/to/config.yaml \\
        --out-dir path/to/out

    load_lp_model() below is a TEMPLATE, not verified against a real
    checkpoint — no single-view model exists yet as of writing this. Lightning
    Pose's model-loading API differs between versions (the shared multiview
    package's own README warns branch/version matters: "lp3d_context v2.0.8
    — NOT main/2.3.1, which removed the API it uses"). A single-view model
    will most likely use current mainline lightning-pose, not the lp3d_context
    branch, so expect to adjust load_lp_model() for whatever API that version
    actually exposes — the rest of this step (wrapping in a heatmap-only
    forward pass, the torch.onnx.export call itself) is standard and shouldn't
    need changes.

  Step 2 — ONNX -> HEF (needs the Hailo DFC; x86-64 Linux only):
    python tools/convert_lp_pose_to_hailo.py compile-hef \\
        --onnx path/to/out/model.onnx --num-keypoints 11 \\
        --hw-arch hailo8l --calib-dir path/to/calibration/images

Requirements:
    Step 1: pip install torch lightning-pose
            (whatever version the model was trained with)
    Step 2: pip install hailo_dataflow_compiler   # wheel from https://developer.hailo.ai
            pip install onnx onnxruntime opencv-python numpy

Calibration images (Step 2):
    Quantization accuracy improves significantly with real images. Supply
    64-128 frames representative of the actual camera view (the arena, with
    and without the animal) via --calib-dir. Without them, random calibration
    is used — still functional but likely to hurt keypoint accuracy more than
    it would for a simpler detection task, since heatmap peaks are sensitive
    to exact pixel intensities.

After compiling, verify the HEF's actual output shape/layout matches what
LightningCameraModule's HailoPoseDetector._decode_heatmaps() assumes
((K, Hm, Wm) or (Hm, Wm, K), one channel per keypoint in keypoint_names
order) — inspect it with `hailortcli parse-hef model.hef` on the Pi, or via
the ONNX graph dump this script prints in Step 1. If the DFC's own
postprocessing produces something else (e.g. direct (x, y, conf) triplets
instead of raw heatmaps), only that one decode method needs to change.
"""

import argparse
import platform
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Step 1: ONNX export
# ---------------------------------------------------------------------------

def load_lp_model(checkpoint_path: Path, config_path: Path):
    """Load a single-view Lightning Pose checkpoint and return a plain
    torch.nn.Module whose forward(image_tensor) returns ONLY the raw
    per-keypoint heatmap tensor (shape (1, K, Hm, Wm)) -- no LP-internal
    postprocessing (soft-argmax, confidence calibration, etc.), since those
    steps either don't trace cleanly to ONNX or aren't things the Hailo DFC
    needs to reproduce (the module's own _decode_heatmaps does the
    argmax-based decode instead).

    TEMPLATE — see the module docstring above. Adjust the import/loading
    calls for whatever lightning-pose version the actual single-view
    checkpoint was trained with; the shape below is a reasonable starting
    point for a typical LP heatmap-tracker checkpoint (a PyTorch Lightning
    module wrapping a ResNet/MobileNet backbone + deconv heatmap head).
    """
    import torch
    import yaml

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    try:
        from lightning_pose.models import HeatmapTracker
    except ImportError as e:
        raise ImportError(
            "Could not import lightning_pose.models.HeatmapTracker -- this "
            "is the part most likely to need adjusting for your installed "
            "lightning-pose version. Check `python -c \"import lightning_pose; "
            "print(lightning_pose.__file__)\"` and inspect the models module "
            "for the correct class/loading call."
        ) from e

    lp_model = HeatmapTracker.load_from_checkpoint(
        str(checkpoint_path),
        map_location="cpu",
        strict=False,
    )
    lp_model.eval()

    class HeatmapOnly(torch.nn.Module):
        """Thin wrapper: forward() returns just the raw heatmap tensor, so
        the traced ONNX graph has a single, simple output the Hailo DFC can
        quantize/compile without needing to understand LP's own output
        dataclasses/postprocessing."""
        def __init__(self, backbone):
            super().__init__()
            self.backbone = backbone

        def forward(self, x):
            out = self.backbone(x)
            # LP heatmap models typically return the heatmap tensor directly,
            # or a dataclass/tuple with it as the first element -- handle both.
            if isinstance(out, (tuple, list)):
                out = out[0]
            return out

    image_size = cfg.get("data", {}).get("image_resize_dims", {})
    print(f"      Loaded checkpoint: {checkpoint_path.name}")
    print(f"      Config image size: {image_size}")
    return HeatmapOnly(lp_model), image_size


def export_onnx(checkpoint_path: Path, config_path: Path, out_dir: Path) -> Path:
    import torch

    model, image_size = load_lp_model(checkpoint_path, config_path)
    height = image_size.get("height", 256)
    width = image_size.get("width", 256)

    dummy_input = torch.zeros(1, 3, height, width)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / f"{checkpoint_path.stem}.onnx"

    print(f"\n[1/1] Exporting -> ONNX (input {width}x{height})")
    torch.onnx.export(
        model,
        dummy_input,
        str(onnx_path),
        input_names=["image"],
        output_names=["heatmaps"],
        opset_version=11,
        dynamic_axes=None,  # fixed shape -- Hailo DFC requires static input size
    )
    print(f"      Saved: {onnx_path}")

    inspect_onnx(onnx_path)
    return onnx_path


def inspect_onnx(onnx_path: Path):
    """Print the ONNX graph's input/output shapes -- use this to confirm
    what HailoPoseDetector._decode_heatmaps should expect."""
    try:
        import onnx
        model = onnx.load(str(onnx_path))
        for t in model.graph.input:
            dims = [d.dim_value for d in t.type.tensor_type.shape.dim]
            print(f"      ONNX input:  {t.name} {dims}")
        for t in model.graph.output:
            dims = [d.dim_value for d in t.type.tensor_type.shape.dim]
            print(f"      ONNX output: {t.name} {dims}")
    except ImportError:
        print("[WARN] onnx not installed -- skipping graph inspection")


# ---------------------------------------------------------------------------
# Step 2: ONNX -> HEF (same DFC shape as tools/convert_to_hailo.py)
# ---------------------------------------------------------------------------

def check_x86():
    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        print(f"[ERROR] Hailo DFC requires x86-64 Linux. This machine is {machine}.")
        print("        Run Step 2 on your development/lab PC, not the Pi.")
        sys.exit(1)


def load_calibration(calib_dir: Path, imgsz: tuple[int, int], n: int = 64):
    """Load calibration images as uint8 (N, H, W, 3). Same helper shape as
    tools/convert_to_hailo.py's load_calibration."""
    import numpy as np
    h, w = imgsz
    try:
        import cv2
    except ImportError:
        print("[WARN] opencv not found -- using random calibration data")
        return np.random.randint(0, 256, (n, h, w, 3), dtype=np.uint8)

    paths = sorted(calib_dir.glob("*.jpg")) + sorted(calib_dir.glob("*.png"))
    if not paths:
        print(f"[WARN] No .jpg/.png found in {calib_dir} -- using random calibration")
        return np.random.randint(0, 256, (n, h, w, 3), dtype=np.uint8)

    paths = paths[:n]
    images = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (w, h))
        images.append(img)

    print(f"      Loaded {len(images)} calibration images from {calib_dir}")
    return np.stack(images).astype(np.uint8)


def compile_hef(onnx_path: Path, num_keypoints: int, hw_arch: str,
                 calib_dir: Path | None, out_dir: Path) -> Path:
    """Parse ONNX -> quantize -> compile to HEF using the Hailo DFC Python SDK.

    No NMS/postprocess model script needed (unlike the YOLO tool) -- a pose
    heatmap head is just a plain conv stack, so quantization only needs input
    normalization.
    """
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        print("[ERROR] hailo_dataflow_compiler not installed.")
        print("        Download the wheel from https://developer.hailo.ai and:")
        print("        pip install hailo_dataflow_compiler-*.whl")
        sys.exit(1)

    import numpy as np
    import onnx

    model = onnx.load(str(onnx_path))
    input_tensor = model.graph.input[0]
    dims = [d.dim_value for d in input_tensor.type.tensor_type.shape.dim]
    if len(dims) != 4:
        print(f"[ERROR] Unexpected ONNX input rank {len(dims)}: {dims}")
        sys.exit(1)
    _, _, height, width = dims

    model_name = onnx_path.stem
    har_path = out_dir / f"{model_name}.har"
    hef_path = out_dir / f"{model_name}.hef"

    print(f"\n[1/3] Parsing ONNX -> HAR  ({hw_arch})")
    runner = ClientRunner(hw_arch=hw_arch)
    runner.translate_onnx_model(
        str(onnx_path), model_name,
        net_input_shapes={input_tensor.name: [1, 3, height, width]},
    )
    runner.save_har(str(har_path))
    print(f"      Saved HAR: {har_path}")

    print(f"\n[2/3] Quantizing  (keypoints={num_keypoints}, input={width}x{height})")
    runner = ClientRunner(hw_arch=hw_arch, har=str(har_path))
    # Plain normalization only -- no NMS/postprocess script, unlike the YOLO tool.
    runner.load_model_script(
        "normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])\n"
    )

    if calib_dir and calib_dir.exists():
        calib_data = load_calibration(calib_dir, (height, width))
    else:
        print("      No --calib-dir supplied -- using random calibration data.")
        print("      For best accuracy supply 64+ representative arena frames.")
        calib_data = np.random.randint(0, 256, (64, height, width, 3), dtype=np.uint8)

    runner.optimize(calib_data)
    runner.save_har(str(har_path))
    print(f"      Quantized HAR saved: {har_path}")

    print("\n[3/3] Compiling -> HEF")
    hef_bytes = runner.compile()
    hef_path.write_bytes(hef_bytes)
    print(f"      Saved HEF: {hef_path}")
    return hef_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    src_dir = Path(__file__).resolve().parent.parent
    default_out = src_dir / "src/modules/variants/lightning_camera"

    parser = argparse.ArgumentParser(
        description="Convert a single-view Lightning Pose checkpoint -> Hailo HEF",
    )
    sub = parser.add_subparsers(dest="step", required=True)

    p1 = sub.add_parser("export-onnx", help="Step 1: LP checkpoint -> ONNX")
    p1.add_argument("--checkpoint", type=Path, required=True)
    p1.add_argument("--config", type=Path, required=True,
                     help="Lightning Pose config.yaml (for image_resize_dims)")
    p1.add_argument("--out-dir", type=Path, default=default_out)

    p2 = sub.add_parser("compile-hef", help="Step 2: ONNX -> HEF (x86 Linux only)")
    p2.add_argument("--onnx", type=Path, required=True)
    p2.add_argument("--num-keypoints", type=int, required=True)
    p2.add_argument("--hw-arch", default="hailo8l", choices=["hailo8l", "hailo8"],
                     help="Hailo hardware target (default: hailo8l = AI HAT for Pi 5)")
    p2.add_argument("--calib-dir", type=Path, default=None)
    p2.add_argument("--out-dir", type=Path, default=default_out)

    args = parser.parse_args()

    if args.step == "export-onnx":
        if not args.checkpoint.exists():
            print(f"[ERROR] Checkpoint not found: {args.checkpoint}")
            sys.exit(1)
        if not args.config.exists():
            print(f"[ERROR] Config not found: {args.config}")
            sys.exit(1)
        onnx_path = export_onnx(args.checkpoint, args.config, args.out_dir)
        print(f"""
Done (ONNX export). Copy {onnx_path.name} to an x86-64 Linux machine with
the Hailo DFC installed and run:
  python tools/convert_lp_pose_to_hailo.py compile-hef \\
      --onnx {onnx_path.name} --num-keypoints <K> --hw-arch hailo8l
""")

    elif args.step == "compile-hef":
        check_x86()
        if not args.onnx.exists():
            print(f"[ERROR] ONNX file not found: {args.onnx}")
            sys.exit(1)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        hef_path = compile_hef(
            args.onnx, args.num_keypoints, args.hw_arch, args.calib_dir, args.out_dir,
        )
        print(f"""
Done! HEF written to:
  {hef_path}

To use it on the Pi, update the module config:
  pose_estimation.model_path = "{hef_path.name}"
  pose_estimation.backend    = "hailo"
  pose_estimation.enabled    = true

Then verify the actual output shape (see this script's module docstring)
matches what HailoPoseDetector._decode_heatmaps() in
lightning_camera_module.py assumes, before trusting the keypoints live.
""")


if __name__ == "__main__":
    main()
