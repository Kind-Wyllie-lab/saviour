#!/usr/bin/env python3
"""
Single-view Lightning Pose checkpoint -> Hailo .hef for hailo_camera.

Four subcommands; the first runs wherever lightning-pose is installed, the
last two need the Hailo Dataflow Compiler (x86-64 Linux only):

  export-onnx   LP checkpoint -> <stem>.onnx + <stem>.lpmeta.json
  inspect-onnx  print an ONNX graph's input/output shapes
  compile-hef   ONNX -> quantised <stem>.har + <stem>.hef + <stem>.json sidecar
  eval-quant    float ONNX (onnxruntime) vs quantised HAR (DFC emulator) on
                held-out labelled frames, through hailo_camera's own decoder

What the .hef is (the contract src/modules/lp_decode.py checks at load):
  input   (H, W, 3) uint8, **BGR** (picamera2's "RGB888" is B,G,R in memory,
          and hailo_camera hands the model that buffer unchanged). LP trains
          on RGB, so the RGB->BGR swap is folded into the first conv's weights
          here: exact, and no per-frame colour conversion on the Pi.
  normalisation  LP's ImageNet mean/std (read from the installed
          lightning_pose, after checking its dataset transform uses them),
          in BGR order, as the .hef's normalization layer.
  output  the raw per-keypoint heatmaps, (Hm, Wm, K) channels-last from
          HailoRT, Hm = H / 2**downsample_factor. The export is built from the
          backbone + the head's upsampling layers ONLY, so LP's final spatial
          softmax is NOT in the graph (it would squash every value into
          ~1e-4 and quantise terribly); hailo_camera applies it on the CPU.

Typical run:

  # GPU/CPU box with lightning-pose (the version the model was trained with)
  python tools/convert_lp_pose_to_hailo.py export-onnx \\
      --checkpoint outputs/.../model.ckpt --config outputs/.../config.yaml \\
      --out-dir out/

  # x86-64 box with the Hailo DFC
  python tools/convert_lp_pose_to_hailo.py compile-hef \\
      --onnx out/model.onnx --hw-arch hailo8l \\
      --calib-dir frames/session_a frames/session_b frames/session_c
  python tools/convert_lp_pose_to_hailo.py eval-quant \\
      --onnx out/model.onnx --har out/model_quantized.har \\
      --labels lp_project/CollectedData.csv --frames-root lp_project \\
      --split-from outputs/.../predictions.csv

Then upload model.hef and model.json from the hailo_camera AI tab.

Calibration frames (--calib-dir, required, >= 64): sample them across
several sessions and lighting conditions, including the animal in unusual
postures and near the walls; not 64 consecutive frames of one clip. Each
directory is searched recursively and sampled evenly.

Requirements:
  export-onnx  torch, lightning-pose, onnx, onnxruntime, opencv-python
  compile-hef  hailo_dataflow_compiler wheel (developer.hailo.ai), onnx,
               opencv-python, numpy
  eval-quant   as compile-hef, plus onnxruntime
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import platform
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from modules.lp_decode import (  # noqa: E402 -- needs the sys.path entry above
    FEED_CHANNEL_ORDER,
    LPModelError,
    decode,
    load_meta,
    prepare_input,
    validate_meta,
)

MIN_CALIB_FRAMES = 64
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


def die(msg: str) -> None:
    print(f"[ERROR] {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# LP-side helpers (export-onnx; also imported by tools/lp_reference_outputs.py)
# ---------------------------------------------------------------------------

def lp_normalisation() -> tuple[list[float], list[float]]:
    """ImageNet mean/std (0-1, RGB) from the INSTALLED lightning_pose, after
    checking its labelled-frame dataset really normalises with them."""
    import inspect

    from lightning_pose import data as lp_data
    from lightning_pose.data import datasets as lp_datasets
    mean = getattr(lp_data, "_IMAGENET_MEAN", None)
    std = getattr(lp_data, "_IMAGENET_STD", None)
    if mean is None or std is None:
        die("lightning_pose.data has no _IMAGENET_MEAN/_IMAGENET_STD: this LP "
            "version normalises differently; check its datasets.py by hand")
    src = inspect.getsource(lp_datasets)
    if "transforms.Normalize(mean=_IMAGENET_MEAN, std=_IMAGENET_STD)" not in src:
        die("lightning_pose.data.datasets no longer contains "
            "transforms.Normalize(mean=_IMAGENET_MEAN, std=_IMAGENET_STD): "
            "check how this LP version normalises images before exporting")
    return [float(x) for x in mean], [float(x) for x in std]


def load_lp_checkpoint(checkpoint: Path, config: Path):
    """Load a single-view heatmap model the way LP itself does
    (load_model_from_checkpoint: semi-supervised classes, old-key remap) and
    refuse model types whose forward isn't backbone -> HeatmapHead on one
    frame. Returns (model, cfg)."""
    from omegaconf import OmegaConf
    try:
        from lightning_pose.api.model_runtime import load_model_from_checkpoint
    except ImportError:          # LP < 2.4
        from lightning_pose.utils.predictions import load_model_from_checkpoint
    from lightning_pose.models.heads import HeatmapHead

    cfg = OmegaConf.load(str(config))
    model_type = cfg.model.get("model_type")
    if model_type != "heatmap":
        die(f"model.model_type is {model_type!r}: only 'heatmap' (single frame, "
            "single view) can run on the Hailo; not heatmap_mhcrnn (temporal "
            "context) or heatmap_multiview_transformer")
    model = load_model_from_checkpoint(cfg, str(checkpoint), eval=True,
                                       skip_data_module=True)
    model.eval()
    if getattr(model, "do_context", False):
        die("context (temporal) model: not supported on the Hailo")
    if not isinstance(getattr(model, "head", None), HeatmapHead):
        die(f"model.head is {type(getattr(model, 'head', None)).__name__}, not HeatmapHead")
    return model, OmegaConf.to_container(cfg, resolve=True)


def _first_rgb_conv(module):
    import torch
    for m in module.modules():
        if isinstance(m, torch.nn.Conv2d):
            if m.in_channels != 3:
                die(f"first Conv2d has {m.in_channels} input channels, not 3")
            return m
    die("no Conv2d found in the backbone")


def build_export_module(lp_model):
    """backbone + head.upsampling_layers, first conv taking BGR. Returns
    (module, (mean_bgr_255, std_bgr_255))."""
    import copy

    import torch

    class LPHeatmapLogits(torch.nn.Module):
        def __init__(self, backbone, upsampling):
            super().__init__()
            self.backbone = backbone
            self.upsampling = upsampling

        def forward(self, x):
            feats = self.backbone(x)
            if feats.dim() != 4:
                raise RuntimeError(f"backbone output is {tuple(feats.shape)}, not 4-D")
            return self.upsampling(feats)

    backbone = copy.deepcopy(lp_model.backbone).eval()
    conv = _first_rgb_conv(backbone)
    with torch.no_grad():
        conv.weight.copy_(conv.weight[:, [2, 1, 0]])   # consume B, G, R
    module = LPHeatmapLogits(backbone, copy.deepcopy(lp_model.head.upsampling_layers)).eval()
    mean, std = lp_normalisation()
    mean_bgr = [round(m * 255.0, 4) for m in mean[::-1]]
    std_bgr = [round(s * 255.0, 4) for s in std[::-1]]
    return module, (mean_bgr, std_bgr)


def lp_frame_tensor_rgb(img_bgr: np.ndarray, input_size, mean, std):
    """What LP's forward sees for this frame: resized, RGB, ImageNet-normalised,
    NCHW float. (mean/std: 0-1 RGB.)"""
    import torch
    x = prepare_input(img_bgr, input_size)[:, :, ::-1].astype(np.float32) / 255.0
    x = (x - np.array(mean, np.float32)) / np.array(std, np.float32)
    return torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1)))[None]


def bgr_input_array(img_bgr: np.ndarray, input_size, mean_bgr, std_bgr) -> np.ndarray:
    """What the exported ONNX takes: resized BGR, normalised (0-255 stats),
    NCHW float32 -- the same arithmetic the .hef's normalization layer does."""
    x = prepare_input(img_bgr, input_size).astype(np.float32)
    x = (x - np.array(mean_bgr, np.float32)) / np.array(std_bgr, np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1))[None]


def _labelled_images(config: dict) -> list[Path]:
    data_dir = Path(config.get("data", {}).get("data_dir", ""))
    csv_name = config.get("data", {}).get("csv_file", "CollectedData.csv")
    p = data_dir / csv_name
    if not p.is_file():
        return []
    out = []
    with open(p, newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i >= 3 and row and row[0]:
                img = data_dir / row[0]
                if img.is_file():
                    out.append(img)
    return out


DEFAULT_SKELETON = REPO / "src/modules/variants/hailo_camera/training/rat_keypoints.json"


def _parse_skeleton(arg: str | None, names: list[str]) -> list[list[str]]:
    """Edges from a JSON file's 'skeleton' list or 'a-b,b-c'. An explicitly
    given skeleton must fit the model; the default file's edges that don't
    are dropped with a warning (a model with other keypoints)."""
    if not arg:
        return []
    p = Path(arg)
    if p.is_file():
        edges = json.loads(p.read_text()).get("skeleton", [])
    else:
        edges = [e.split("-", 1) for e in arg.split(",") if e.strip()]
    ok = [list(e) for e in edges if len(e) == 2 and e[0] in names and e[1] in names]
    if len(ok) != len(edges):
        bad = [e for e in edges if list(e) not in ok]
        if p.resolve() != DEFAULT_SKELETON.resolve():
            die(f"skeleton edges {bad} do not name two keypoints of {names}")
        print(f"[WARN] default skeleton edges {bad} don't fit this model's "
              "keypoints: left out (pass --skeleton for this model)")
    return ok


def export_onnx(args) -> None:
    import cv2
    import torch

    lp_model, cfg = load_lp_checkpoint(args.checkpoint, args.config)
    head = lp_model.head

    dims = cfg.get("data", {}).get("image_resize_dims", {})
    in_h, in_w = int(dims.get("height", 0)), int(dims.get("width", 0))
    if not in_h or not in_w:
        die("config.yaml has no data.image_resize_dims height/width")
    names = list(cfg.get("data", {}).get("keypoint_names") or [])
    if len(names) != head.out_channels:
        die(f"config has {len(names)} keypoint_names but the head outputs "
            f"{head.out_channels} channels")

    module, (mean_bgr, std_bgr) = build_export_module(lp_model)
    mean, std = lp_normalisation()

    # -- real frames for the checks (labelled frames from the config, or --frames)
    frames = [Path(p) for p in (args.frames or [])] or _labelled_images(cfg)
    imgs = [im for im in (cv2.imread(str(p)) for p in frames[:8]) if im is not None]
    if not imgs:
        print("[WARN] no labelled frames found: checking on random images")
        rng = np.random.default_rng(0)
        imgs = [rng.integers(0, 256, (in_h * 2, in_w * 2, 3), dtype=np.uint8) for _ in range(4)]
    src_size = None
    if frames and (im0 := cv2.imread(str(frames[0]))) is not None:
        src_size = [int(im0.shape[1]), int(im0.shape[0])]

    # -- check 1: the export (BGR in) reproduces LP's forward (RGB in) up to
    #    the final softmax we deliberately dropped
    from kornia.geometry.subpix import spatial_softmax2d
    worst = 0.0
    with torch.no_grad():
        for im in imgs:
            ref = lp_model(lp_frame_tensor_rgb(im, (in_h, in_w), mean, std))
            logits = module(torch.from_numpy(bgr_input_array(im, (in_h, in_w), mean_bgr, std_bgr)))
            if not head.final_softmax:
                die("this head has final_softmax=False: the export would still "
                    "be right, but lp_decode assumes LP's default (True)")
            mine = spatial_softmax2d(logits, temperature=torch.tensor([1.0]))
            worst = max(worst, float((mine - ref).abs().max() / ref.abs().max()))
    print(f"      export vs LP forward (after softmax): max rel diff {worst:.2e}")
    if worst > 1e-4:
        die("the exported graph does not reproduce LP's forward pass")

    # -- export
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.checkpoint.stem if args.name is None else args.name
    onnx_path = args.out_dir / f"{stem}.onnx"
    dummy = torch.from_numpy(bgr_input_array(imgs[0], (in_h, in_w), mean_bgr, std_bgr))
    kw = dict(input_names=["image"], output_names=["heatmaps"], opset_version=13,
              dynamic_axes=None)
    try:
        torch.onnx.export(module, dummy, str(onnx_path), dynamo=False, **kw)
    except TypeError:          # torch < 2.5 has no `dynamo` argument
        torch.onnx.export(module, dummy, str(onnx_path), **kw)
    print(f"      Saved: {onnx_path}")

    # -- check 2: the ONNX output shape, and that no softmax crept back in
    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    stride = 2 ** head.downsample_factor
    want = (1, len(names), in_h // stride, in_w // stride)
    for im in imgs:
        x = bgr_input_array(im, (in_h, in_w), mean_bgr, std_bgr)
        out = sess.run(None, {"image": x})[0]
        if tuple(out.shape) != want:
            die(f"ONNX output shape {out.shape} != expected (1, K, H/stride, W/stride) {want}")
        with torch.no_grad():
            ref = module(torch.from_numpy(x)).numpy()
        if np.abs(out - ref).max() > 1e-3 * max(1.0, np.abs(ref).max()):
            die("onnxruntime output differs from the torch export module")
        sums = out[0].reshape(len(names), -1).sum(axis=1)
        if out.min() >= 0 and np.allclose(sums, 1.0, atol=1e-3):
            die("ONNX output is non-negative and sums to 1 per channel: the "
                "final softmax is in the graph; it must end at the raw heatmaps")
    print(f"      ONNX output {want}: raw logits (min {out.min():.3g}, "
          f"max {out.max():.3g}), matches torch")

    import lightning_pose
    meta = validate_meta({
        "type": "lp_heatmap",
        "keypoint_names": names,
        "skeleton": _parse_skeleton(args.skeleton, names),
        "input_size": [in_h, in_w],
        "downsample_factor": int(head.downsample_factor),
        "heatmap_stride": stride,
        "softmax_temperature": float(head.temperature),
        "input_channel_order": FEED_CHANNEL_ORDER,
        "source_frame_size": src_size,
        "normalisation_bgr_255": {"mean": mean_bgr, "std": std_bgr},
        "lightning_pose_version": getattr(lightning_pose, "__version__", "unknown"),
        "checkpoint": args.checkpoint.name,
        "exported_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    })
    meta_path = args.out_dir / f"{stem}.lpmeta.json"
    meta_path.write_text(json.dumps(meta.to_dict(), indent=2) + "\n")
    print(f"      Saved: {meta_path}")
    inspect_onnx(onnx_path)
    print(f"\nNext, on the x86 DFC box:\n  python tools/convert_lp_pose_to_hailo.py "
          f"compile-hef --onnx {onnx_path.name} --calib-dir <dirs...>")


def inspect_onnx(onnx_path: Path) -> None:
    import onnx
    model = onnx.load(str(onnx_path))
    for kind, tensors in (("input", model.graph.input), ("output", model.graph.output)):
        for t in tensors:
            dims = [d.dim_value for d in t.type.tensor_type.shape.dim]
            print(f"      ONNX {kind}: {t.name} {dims}")


# ---------------------------------------------------------------------------
# DFC side
# ---------------------------------------------------------------------------

def check_x86() -> None:
    if platform.machine() not in ("x86_64", "AMD64"):
        die(f"the Hailo DFC needs x86-64 Linux; this is {platform.machine()}")


def _onnx_meta(onnx_path: Path, meta_arg: Path | None):
    path = meta_arg or onnx_path.with_suffix(".lpmeta.json")
    try:
        meta = load_meta(str(path))
    except LPModelError as e:
        die(f"{e}\n        (export-onnx writes <stem>.lpmeta.json; pass --meta)")
    norm = meta.extra.get("normalisation_bgr_255") or {}
    if len(norm.get("mean", [])) != 3 or len(norm.get("std", [])) != 3:
        die(f"{path} has no normalisation_bgr_255 mean/std (re-run export-onnx)")
    return meta, norm["mean"], norm["std"]


def sample_calibration(dirs: list[Path], input_size, max_frames: int) -> np.ndarray:
    """BGR uint8 (N, H, W, 3), sampled evenly across `dirs` (recursive)."""
    import cv2
    per_dir = []
    for d in dirs:
        if not d.is_dir():
            die(f"--calib-dir {d} is not a directory")
        files = sorted(p for p in d.rglob("*") if p.suffix.lower() in IMAGE_EXTS)
        if not files:
            die(f"--calib-dir {d} has no images")
        per_dir.append(files)
    total = sum(len(f) for f in per_dir)
    if total < MIN_CALIB_FRAMES:
        die(f"only {total} calibration images; need at least {MIN_CALIB_FRAMES}, "
            "sampled across sessions, lighting, postures and wall positions")
    quota = max(1, max_frames // len(per_dir))
    chosen = []
    for files in per_dir:
        idx = np.linspace(0, len(files) - 1, min(quota, len(files))).round().astype(int)
        chosen += [files[i] for i in sorted(set(idx))]
    imgs = []
    for p in chosen:
        im = cv2.imread(str(p))          # BGR: the order the .hef will be fed
        if im is not None:
            imgs.append(prepare_input(im, input_size))
    if len(imgs) < MIN_CALIB_FRAMES:
        die(f"only {len(imgs)} calibration images could be read")
    print(f"      Calibration: {len(imgs)} frames from {len(per_dir)} dir(s) "
          f"({', '.join(str(len(f)) for f in per_dir)} available)")
    return np.stack(imgs)


def _output_producer(runner) -> str:
    """Name of the layer feeding the network's single output, from the HN."""
    hn = runner.get_hn_dict()
    layers = hn["layers"]
    outs = [n for n, l in layers.items() if l.get("type") == "output_layer"]
    if len(outs) != 1:
        die(f"expected one output layer in the HN, found {outs}")
    return layers[outs[0]]["input"][0]


def compile_hef(args) -> None:
    check_x86()
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        die("hailo_dataflow_compiler not installed (wheel from developer.hailo.ai)")
    import onnx

    meta, mean_bgr, std_bgr = _onnx_meta(args.onnx, args.meta)
    in_h, in_w = meta.input_size
    model = onnx.load(str(args.onnx))
    inp = model.graph.input[0]
    dims = [d.dim_value for d in inp.type.tensor_type.shape.dim]
    if dims != [1, 3, in_h, in_w]:
        die(f"ONNX input {dims} != [1, 3, {in_h}, {in_w}] from the metadata")

    calib = sample_calibration(args.calib_dir, meta.input_size, args.calib_max)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.onnx.stem
    har = args.out_dir / f"{stem}.har"
    qhar = args.out_dir / f"{stem}_quantized.har"
    hef = args.out_dir / f"{stem}.hef"

    print(f"\n[1/3] ONNX -> HAR ({args.hw_arch})")
    runner = ClientRunner(hw_arch=args.hw_arch)
    runner.translate_onnx_model(str(args.onnx), stem,
                                net_input_shapes={inp.name: [1, 3, in_h, in_w]})
    runner.save_har(str(har))

    print("\n[2/3] Quantising")
    script = f"normalization1 = normalization({mean_bgr}, {std_bgr})\n"
    if args.output_16bit:
        # UNVERIFIED against a real DFC run: 16-bit activations on the layer
        # producing the heatmaps. hailo_camera reads outputs as FLOAT32, so a
        # uint16 output needs no change on the Pi.
        script += f"quantization_param({_output_producer(runner)}, precision_mode=a16_w16)\n"
    print("      model script:\n        " + script.strip().replace("\n", "\n        "))
    runner.load_model_script(script)
    runner.optimize(calib)
    runner.save_har(str(qhar))
    print(f"      Quantised HAR: {qhar}  (eval-quant uses this)")

    print("\n[3/3] Compiling")
    hef.write_bytes(runner.compile())

    d = meta.to_dict()
    d.update({"hw_arch": args.hw_arch, "output_16bit": bool(args.output_16bit),
              "calibration_frames": int(len(calib)),
              "compiled_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")})
    sidecar = args.out_dir / f"{stem}.json"
    sidecar.write_text(json.dumps(d, indent=2) + "\n")
    print(f"\nDone: {hef}\n      {sidecar}\nRun eval-quant before deploying, then "
          "upload both files from the hailo_camera AI tab.")


# ---------------------------------------------------------------------------
# eval-quant
# ---------------------------------------------------------------------------

def read_lp_labels(csv_paths: list[Path], names: list[str]) -> list[tuple[str, np.ndarray]]:
    """DLC/LP CollectedData CSV(s) (scorer / bodyparts / coords header rows)
    -> [(image path, (K, 2) x, y in frame px; NaN where unlabelled)], in
    `names` order."""
    out = []
    for p in csv_paths:
        with open(p, newline="") as f:
            rows = list(csv.reader(f))
        if len(rows) < 4:
            die(f"{p}: not a scorer/bodyparts/coords labels CSV")
        parts, coords = rows[1], rows[2]
        col = {}
        for j in range(1, len(parts)):
            col[(parts[j], coords[j])] = j
        missing = [n for n in names if (n, "x") not in col or (n, "y") not in col]
        if missing:
            die(f"{p}: no x/y columns for {missing}")
        for r in rows[3:]:
            if not r or not r[0]:
                continue
            xy = np.full((len(names), 2), np.nan, dtype=np.float64)
            for k, n in enumerate(names):
                for a, axis in enumerate("xy"):
                    v = r[col[(n, axis)]]
                    xy[k, a] = float(v) if v not in ("", "nan", "NaN") else np.nan
            out.append((r[0], xy))
    return out


def _split_filter(split_csv: Path, sets: set[str]) -> set[str]:
    """Image paths whose LP predictions.csv `set` column is in `sets`."""
    with open(split_csv, newline="") as f:
        rows = list(csv.reader(f))
    for hdr in range(min(4, len(rows))):
        if "set" in rows[hdr]:
            j = rows[hdr].index("set")
            return {r[0] for r in rows[hdr + 1:] if len(r) > j and r[j] in sets}
    die(f"{split_csv} has no 'set' column")


def _stats(err: np.ndarray) -> dict:
    e = err[np.isfinite(err)]
    if not len(e):
        return {"n": 0}
    return {"n": int(len(e)), "median": float(np.median(e)), "mean": float(e.mean()),
            "p90": float(np.percentile(e, 90)), "max": float(e.max())}


def eval_quant(args) -> None:
    check_x86()
    import cv2
    import onnxruntime as ort

    meta, mean_bgr, std_bgr = _onnx_meta(args.onnx, args.meta)
    names = list(meta.keypoint_names)
    labels = read_lp_labels(args.labels, names)
    if args.split_from:
        keep = _split_filter(args.split_from, set(args.sets))
        labels = [lab for lab in labels if lab[0] in keep]
        print(f"      {len(labels)} frames in set(s) {args.sets} of {args.split_from.name}")
    else:
        print("[WARN] no --split-from: evaluating every labelled frame, "
              "including ones the model trained on")
    if args.max_frames:
        labels = labels[:args.max_frames]
    if not labels:
        die("no labelled frames to evaluate")

    imgs, gts, sizes = [], [], []
    for rel, xy in labels:
        im = cv2.imread(str(args.frames_root / rel))
        if im is None:
            print(f"[WARN] unreadable: {rel}")
            continue
        imgs.append(im)
        gts.append(xy)
        sizes.append((im.shape[1], im.shape[0]))
    gts = np.stack(gts)

    sess = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    float_kp = []
    for im, size in zip(imgs, sizes):
        out = sess.run(None, {"image": bgr_input_array(im, meta.input_size, mean_bgr, std_bgr)})[0]
        float_kp.append(decode(np.moveaxis(out[0], 0, -1), meta, out_size=size,
                               method=args.decode))
    float_kp = np.stack(float_kp)

    from hailo_sdk_client import ClientRunner, InferenceContext
    runner = ClientRunner(har=str(args.har))
    batch = np.stack([prepare_input(im, meta.input_size) for im in imgs])  # BGR uint8
    with runner.infer_context(InferenceContext.SDK_QUANTIZED) as ctx:
        q_out = runner.infer(ctx, batch)
    if isinstance(q_out, (list, tuple)):
        q_out = q_out[0]
    q_out = np.asarray(q_out)
    print(f"      emulator output {q_out.shape} (expected (N, Hm, Wm, K) = "
          f"({len(imgs)}, {', '.join(map(str, meta.heatmap_shape))}))")
    quant_kp = np.stack([decode(q_out[i], meta, out_size=sizes[i], method=args.decode)
                         for i in range(len(imgs))])

    report = {"frames": len(imgs), "decode": args.decode, "keypoints": {}}
    hdr = (f"{'keypoint':<16}{'n':>4} | {'float vs label (px)':^22} | "
           f"{'int vs label (px)':^22} | {'int vs float (px)':^22}")
    print("\n" + hdr + "\n" + "-" * len(hdr))
    for k, n in enumerate(names):
        e_f = np.linalg.norm(float_kp[:, k, :2] - gts[:, k], axis=1)
        e_q = np.linalg.norm(quant_kp[:, k, :2] - gts[:, k], axis=1)
        e_qf = np.linalg.norm(quant_kp[:, k, :2] - float_kp[:, k, :2], axis=1)
        sf, sq, sqf = _stats(e_f), _stats(e_q), _stats(e_qf)
        report["keypoints"][n] = {"float_vs_label": sf, "quant_vs_label": sq,
                                  "quant_vs_float": sqf,
                                  "float_conf_mean": float(np.nanmean(float_kp[:, k, 2])),
                                  "quant_conf_mean": float(np.nanmean(quant_kp[:, k, 2]))}

        def fmt(s):
            return (f"med {s['median']:5.2f} p90 {s['p90']:6.2f}" if s.get("n") else "   (no labels)")
        print(f"{n:<16}{sf.get('n', 0):>4} | {fmt(sf):^22} | {fmt(sq):^22} | {fmt(sqf):^22}")
    all_f = np.linalg.norm(float_kp[:, :, :2] - gts, axis=2).ravel()
    all_q = np.linalg.norm(quant_kp[:, :, :2] - gts, axis=2).ravel()
    all_qf = np.linalg.norm(quant_kp[:, :, :2] - float_kp[:, :, :2], axis=2).ravel()
    report["all"] = {"float_vs_label": _stats(all_f), "quant_vs_label": _stats(all_q),
                     "quant_vs_float": _stats(all_qf)}
    a = report["all"]
    print(f"\nAll keypoints: float median {a['float_vs_label'].get('median', float('nan')):.2f} px, "
          f"quantised median {a['quant_vs_label'].get('median', float('nan')):.2f} px, "
          f"quantised vs float median {a['quant_vs_float'].get('median', float('nan')):.2f} px.")
    print("If quantisation adds error comparable to the float model's own, try "
          "--output-16bit, more varied calibration frames, or quantisation-aware "
          "fine-tuning before deploying.")
    out = args.report or args.har.with_name(args.har.stem + "_eval.json")
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Single-view Lightning Pose checkpoint -> Hailo .hef for hailo_camera",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="step", required=True)

    p = sub.add_parser("export-onnx", help="LP checkpoint -> ONNX + .lpmeta.json")
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True, help="the model's LP config.yaml")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--name", help="output file stem (default: checkpoint stem)")
    p.add_argument("--frames", nargs="*", help="frames for the export checks "
                   "(default: the config's labelled frames)")
    p.add_argument("--skeleton", default=str(DEFAULT_SKELETON),
                   help="skeleton edges for the overlay: a JSON file with a "
                   "'skeleton' list, or 'a-b,b-c'; '' for none")

    p = sub.add_parser("inspect-onnx", help="print ONNX input/output shapes")
    p.add_argument("onnx", type=Path)

    p = sub.add_parser("compile-hef", help="ONNX -> HEF (x86-64 + Hailo DFC)")
    p.add_argument("--onnx", type=Path, required=True)
    p.add_argument("--meta", type=Path, help="default: <onnx stem>.lpmeta.json")
    p.add_argument("--hw-arch", default="hailo8l", choices=["hailo8l", "hailo8"])
    p.add_argument("--calib-dir", type=Path, nargs="+", required=True,
                   help=f"one or more image dirs (recursive), >= {MIN_CALIB_FRAMES} "
                   "frames in all, sampled evenly across them")
    p.add_argument("--calib-max", type=int, default=1024)
    p.add_argument("--output-16bit", action="store_true",
                   help="16-bit activations on the heatmap output layer (unverified)")
    p.add_argument("--out-dir", type=Path, help="default: next to the ONNX")

    p = sub.add_parser("eval-quant", help="float ONNX vs quantised HAR on labelled frames")
    p.add_argument("--onnx", type=Path, required=True)
    p.add_argument("--har", type=Path, required=True, help="<stem>_quantized.har")
    p.add_argument("--meta", type=Path)
    p.add_argument("--labels", type=Path, nargs="+", required=True,
                   help="CollectedData CSV(s) (scorer/bodyparts/coords headers)")
    p.add_argument("--frames-root", type=Path, required=True,
                   help="directory the CSV image paths are relative to")
    p.add_argument("--split-from", type=Path,
                   help="LP predictions.csv with a 'set' column, to keep held-out rows")
    p.add_argument("--sets", nargs="+", default=["test"])
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--decode", choices=["exact", "windowed"], default="exact")
    p.add_argument("--report", type=Path)

    args = ap.parse_args()
    if args.step == "export-onnx":
        for f in (args.checkpoint, args.config):
            if not f.exists():
                die(f"not found: {f}")
        export_onnx(args)
    elif args.step == "inspect-onnx":
        inspect_onnx(args.onnx)
    elif args.step == "compile-hef":
        if args.out_dir is None:
            args.out_dir = args.onnx.parent
        compile_hef(args)
    else:
        eval_quant(args)


if __name__ == "__main__":
    main()
