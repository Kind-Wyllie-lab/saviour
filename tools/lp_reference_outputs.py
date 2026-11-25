#!/usr/bin/env python3
"""
Reference outputs from Lightning Pose itself, for testing hailo_camera's
NumPy decoder (src/modules/lp_decode.py) against LP directly.

Run on the box where lightning-pose is installed (the version the model was
trained with). Writes an .npz the tests pick up from
src/modules/tests/fixtures/lp_reference_*.npz.

  synthetic   no model needed: hand-made raw heatmaps (sub-pixel blobs,
              border peaks, two peaks, weak/flat, noise, every downsample
              factor, non-square) through LP's own head softmax +
              run_subpixelmaxima. Checks the decoder against the LP version
              you actually train with.

  model       a trained single-view checkpoint on real frames: saves the raw
              heatmaps BEFORE the final softmax (what the .hef will output,
              channels-last), LP's softmaxed heatmaps, and LP's keypoints and
              confidences, in model-input and in frame pixels.

  python tools/lp_reference_outputs.py synthetic --out lp_reference_synthetic.npz
  python tools/lp_reference_outputs.py model --checkpoint .../model.ckpt \\
      --config .../config.yaml --n 32 --out lp_reference_model.npz

Then copy the .npz into src/modules/tests/fixtures/ and run
`pytest src/modules/tests/test_lp_decode.py`.

--impl transcription (synthetic only) swaps LP for a torch + kornia
transcription of run_subpixelmaxima, for a machine without lightning-pose;
the fixture it writes is marked as such.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


# ---------------------------------------------------------------------------
# LP functions (real, or the transcription)
# ---------------------------------------------------------------------------

def lp_functions(impl: str):
    """(head_softmax(raw NCHW) -> NCHW, run_subpixelmaxima(soft, df, temp)
    -> (preds (B, 2K), conf (B, K)), version string)."""
    import torch
    from kornia.geometry.subpix import spatial_softmax2d

    def head_softmax(x):
        # HeatmapHead.forward: spatial_softmax2d(heatmaps, temperature=torch.tensor([1.0]))
        return spatial_softmax2d(x, temperature=torch.tensor([1.0]))

    if impl == "lp":
        import lightning_pose
        from lightning_pose.models.heads.heatmap import run_subpixelmaxima
        return head_softmax, run_subpixelmaxima, f"lightning_pose {lightning_pose.__version__}"

    import kornia
    from kornia.filters import filter2d
    from kornia.geometry.subpix import spatial_expectation2d
    from kornia.geometry.transform.pyramid import _get_pyramid_gaussian_kernel

    def upsample(x):
        _, _, h, w = x.shape
        up = torch.nn.functional.interpolate(x, size=(h * 2, w * 2), mode="bicubic",
                                             align_corners=False)
        return filter2d(up, _get_pyramid_gaussian_kernel(), border_type="constant")

    def evaluate_at(heatmaps, locs, pix=2):
        b, k, h, w = heatmaps.shape
        padded = torch.zeros((b, k, h + 2 * pix, w + 2 * pix))
        padded[:, :, pix:-pix, pix:-pix] = heatmaps
        xi = locs[:, :, 0].type(torch.int64) + pix
        yi = locs[:, :, 1].type(torch.int64) + pix
        out = torch.zeros((b, k))
        for bi in range(b):
            for ki in range(k):
                out[bi, ki] = padded[bi, ki, yi[bi, ki] - pix:yi[bi, ki] + pix + 1,
                                     xi[bi, ki] - pix:xi[bi, ki] + pix + 1].sum()
        return out

    def run_subpixelmaxima(heatmaps, downsample_factor, temperature):
        for _ in range(downsample_factor):
            heatmaps = upsample(heatmaps)
        soft = spatial_softmax2d(heatmaps * temperature)
        preds = spatial_expectation2d(soft, normalized_coordinates=False)
        conf = evaluate_at(soft, preds)
        preds = preds - {1: 0.5, 2: 1.5, 3: 2.5}[downsample_factor]
        return preds.reshape(-1, heatmaps.shape[1] * 2), conf

    return head_softmax, run_subpixelmaxima, (
        f"TRANSCRIPTION of lightning_pose v2.4.4 run_subpixelmaxima "
        f"(torch {torch.__version__}, kornia {kornia.__version__})")


# ---------------------------------------------------------------------------
# synthetic cases
# ---------------------------------------------------------------------------

def _blob(hm, wm, x0, y0, sigma=1.25, amp=1.0):
    """Raw logits whose softmax is a Gaussian centred on (x0, y0) in
    heatmap-pixel coordinates (pixel centres at integers)."""
    yy, xx = np.mgrid[0:hm, 0:wm].astype(np.float64)
    return amp * -((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma ** 2)


def synthetic_cases(seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    cases = []

    def add(name, chans, df, truth=None):
        raw = np.stack(chans, axis=-1).astype(np.float32)     # (Hm, Wm, K)
        cases.append({"name": name, "raw": raw, "df": df,
                      "truth": None if truth is None else np.asarray(truth, np.float64)})

    # sub-pixel peaks well inside the map; truth = LP's mapping u -> 2**df * u
    pts = [(10.3, 20.7), (31.5, 31.5), (40.12, 12.88), (22.0, 50.25), (5.6, 5.4),
           (57.9, 33.33), (18.75, 44.1), (33.0, 9.95), (47.5, 47.5), (12.2, 58.6),
           (52.05, 21.45)]
    add("subpixel_blobs", [_blob(64, 64, x, y) for x, y in pts], 2,
        truth=[(4 * x, 4 * y) for x, y in pts])
    add("subpixel_sharp_wide", [_blob(64, 64, 20.37, 30.81, sigma=s) for s in (0.8, 1.25, 2.0, 3.0)], 2,
        truth=[(4 * 20.37, 4 * 30.81)] * 4)
    add("near_border", [_blob(64, 64, x, y) for x, y in
                        [(0.4, 30.0), (63.2, 30.0), (30.0, 0.0), (30.0, 63.6), (0.2, 63.8), (1.5, 1.5)]], 2)
    add("two_peaks", [np.logaddexp(_blob(64, 64, 15.2, 20.0), _blob(64, 64, 45.7, 41.3) + d)
                      for d in (0.0, -0.5, -2.0)], 2)
    add("weak_flat", [_blob(64, 64, 30.3, 25.6, sigma=1.25, amp=a) for a in (0.02, 0.1, 0.3)]
        + [np.zeros((64, 64))], 2)
    add("noise", [rng.normal(0, s, (64, 64)) for s in (0.5, 2.0, 5.0)], 2)
    add("df1", [_blob(128, 128, x, y) for x, y in [(60.3, 70.6), (10.1, 120.9), (127.4, 3.3)]], 1,
        truth=[(2 * 60.3, 2 * 70.6), (2 * 10.1, 2 * 120.9), (2 * 127.4, 2 * 3.3)])
    add("df3", [_blob(32, 32, x, y) for x, y in [(15.3, 16.6), (4.1, 27.9), (31.0, 0.5)]], 3,
        truth=[(8 * 15.3, 8 * 16.6), (8 * 4.1, 8 * 27.9), (8 * 31.0, 8 * 0.5)])
    add("non_square", [_blob(48, 80, x, y) for x, y in [(70.4, 10.2), (5.5, 40.7), (40.0, 24.0), (79.0, 47.0)]], 2)
    return cases


def run_lp(raw_hwk: np.ndarray, df: int, fns, temperature: float = 1000.0):
    import torch
    head_softmax, run_subpixelmaxima, _ = fns
    x = torch.from_numpy(np.ascontiguousarray(np.moveaxis(raw_hwk, -1, 0)))[None]
    with torch.no_grad():
        soft = head_softmax(x)
        preds, conf = run_subpixelmaxima(soft, df, torch.tensor(temperature))
    k = raw_hwk.shape[-1]
    return preds.numpy().reshape(-1, k, 2)[0], conf.numpy()[0]


def cmd_synthetic(args) -> None:
    import torch
    fns = lp_functions(args.impl)
    out = {"source": np.array(fns[2]), "kind": np.array("synthetic"),
           "torch_version": np.array(torch.__version__)}
    names = []
    for c in synthetic_cases():
        preds, conf = run_lp(c["raw"], c["df"], fns)
        n = c["name"]
        names.append(n)
        out[f"{n}__raw"] = c["raw"]
        out[f"{n}__df"] = np.array(c["df"])
        out[f"{n}__preds"] = preds.astype(np.float64)
        out[f"{n}__conf"] = conf.astype(np.float64)
        if c["truth"] is not None:
            out[f"{n}__truth"] = c["truth"]
        print(f"  {n:<20} df={c['df']} raw {c['raw'].shape}  first pred {preds[0]} conf {conf[0]:.4f}")
    out["cases"] = np.array(names)
    np.savez_compressed(args.out, **out)
    print(f"Wrote {args.out} ({fns[2]})")


# ---------------------------------------------------------------------------
# model mode
# ---------------------------------------------------------------------------

def cmd_model(args) -> None:
    import cv2
    import torch
    from convert_lp_pose_to_hailo import (
        _labelled_images,
        bgr_input_array,
        build_export_module,
        load_lp_checkpoint,
        lp_frame_tensor_rgb,
        lp_normalisation,
    )
    fns = lp_functions("lp")
    lp_model, cfg = load_lp_checkpoint(args.checkpoint, args.config)
    head = lp_model.head
    dims = cfg["data"]["image_resize_dims"]
    input_size = (int(dims["height"]), int(dims["width"]))
    names = list(cfg["data"]["keypoint_names"])
    mean, std = lp_normalisation()
    export, (mean_bgr, std_bgr) = build_export_module(lp_model)

    frames = [Path(p) for p in args.frames] if args.frames else _labelled_images(cfg)
    if args.n:
        idx = np.linspace(0, len(frames) - 1, min(args.n, len(frames))).round().astype(int)
        frames = [frames[i] for i in sorted(set(idx))]
    raws, softs, preds_in, preds_frame, confs, sizes, paths, diffs = [], [], [], [], [], [], [], []
    for p in frames:
        im = cv2.imread(str(p))
        if im is None:
            continue
        with torch.no_grad():
            x = lp_frame_tensor_rgb(im, input_size, mean, std)
            feats = lp_model.get_representations(x)
            logits = head.upsampling_layers(feats)              # BEFORE the final softmax
            soft = lp_model(x)                                  # LP forward (softmaxed)
            preds, conf = head.run_subpixelmaxima(soft)
            exp_logits = export(torch.from_numpy(bgr_input_array(im, input_size, mean_bgr, std_bgr)))
        diffs.append(float((exp_logits - logits).abs().max()))
        k = len(names)
        pr = preds.numpy().reshape(k, 2)
        h, w = im.shape[:2]
        raws.append(np.moveaxis(logits.numpy()[0], 0, -1))       # (Hm, Wm, K) as the .hef
        softs.append(np.moveaxis(soft.numpy()[0], 0, -1))
        preds_in.append(pr)
        preds_frame.append(pr * np.array([w / input_size[1], h / input_size[0]]))
        confs.append(conf.numpy()[0])
        sizes.append((w, h))
        paths.append(str(p))
    if not raws:
        sys.exit("no readable frames")
    print(f"  {len(raws)} frames; BGR export vs LP logits: max abs diff {max(diffs):.2e}")
    np.savez_compressed(
        args.out,
        source=np.array(fns[2]), kind=np.array("model"),
        checkpoint=np.array(args.checkpoint.name),
        keypoint_names=np.array(names), input_size=np.array(input_size),
        downsample_factor=np.array(int(head.downsample_factor)),
        temperature=np.array(float(head.temperature)),
        raw=np.stack(raws).astype(np.float32), soft=np.stack(softs).astype(np.float32),
        preds_input=np.stack(preds_in), preds_frame=np.stack(preds_frame),
        conf=np.stack(confs), frame_sizes=np.array(sizes), frame_paths=np.array(paths),
        export_max_abs_diff=np.array(max(diffs)),
    )
    print(f"Wrote {args.out}")
    print(json.dumps({"keypoints": names, "input_size": input_size,
                      "downsample_factor": int(head.downsample_factor)}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("synthetic")
    p.add_argument("--out", type=Path, default=Path("lp_reference_synthetic.npz"))
    p.add_argument("--impl", choices=["lp", "transcription"], default="lp")
    p = sub.add_parser("model")
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--frames", nargs="*", help="default: the config's labelled frames")
    p.add_argument("--n", type=int, default=32, help="frames to use (spread evenly)")
    p.add_argument("--out", type=Path, default=Path("lp_reference_model.npz"))
    args = ap.parse_args()
    {"synthetic": cmd_synthetic, "model": cmd_model}[args.cmd](args)


if __name__ == "__main__":
    main()
