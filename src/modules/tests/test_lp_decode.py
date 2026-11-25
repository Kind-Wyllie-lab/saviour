"""Tests for modules/lp_decode.py: Lightning Pose heatmap decoding.

Three layers of evidence:
  - analytic: a heatmap blob at a known sub-pixel position must decode to
    LP's mapping of it (heatmap coordinate u -> input pixel 2**df * u) and
    then scale to frame pixels;
  - the OpenCV live path must equal the slow NumPy port of LP's maths;
  - fixtures/lp_reference_*.npz from tools/lp_reference_outputs.py: LP's own
    outputs for the same raw heatmaps (synthetic cases, and a real model's
    pre-softmax heatmaps when a model fixture is present).
"""

from pathlib import Path

import numpy as np
import pytest

from modules.lp_decode import (
    LPModelError,
    check_input_shape,
    check_output_shape,
    decode,
    decode_heatmaps,
    prepare_input,
    upsample2x,
    validate_meta,
)

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("lp_reference_*.npz"))


def _meta(k=3, input_size=(256, 256), df=2, **kw):
    d = {"type": "lp_heatmap", "keypoint_names": [f"k{i}" for i in range(k)],
         "input_size": list(input_size), "downsample_factor": df,
         "input_channel_order": "BGR"}
    d.update(kw)
    return validate_meta(d)


def _blob(hm, wm, x0, y0, sigma=1.25):
    yy, xx = np.mgrid[0:hm, 0:wm].astype(np.float64)
    return -((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma ** 2)


# ---------------------------------------------------------------------------
# metadata + shape contract
# ---------------------------------------------------------------------------

class TestMeta:
    def test_valid_round_trip(self):
        m = _meta(skeleton=[["k0", "k1"]], source_frame_size=[1080, 1080])
        assert m.heatmap_stride == 4
        assert m.heatmap_shape == (64, 64, 3)
        assert m.csv_columns()[:3] == ["kp_k0_x", "kp_k0_y", "kp_k0_conf"]
        assert validate_meta(m.to_dict()) == m

    @pytest.mark.parametrize("change, match", [
        ({"type": "detection"}, "type"),
        ({"downsample_factor": 4}, "downsample_factor"),
        ({"heatmap_stride": 8}, "heatmap_stride"),
        ({"input_size": [250, 256]}, "multiple"),
        ({"input_channel_order": "RGB"}, "BGR"),
        ({"keypoint_names": ["a", "a"]}, "duplicates"),
        ({"keypoint_names": ["a b"]}, "letters"),
        ({"skeleton": [["k0", "nope"]]}, "skeleton"),
        ({"source_frame_size": [0, 10]}, "source_frame_size"),
        ({"softmax_temperature": -1}, "temperature"),
    ])
    def test_rejects(self, change, match):
        d = _meta().to_dict()
        d.update(change)
        with pytest.raises(LPModelError, match=match):
            validate_meta(d)

    def test_output_shape_channels_last_with_or_without_batch(self):
        m = _meta()
        check_output_shape(m, (64, 64, 3))
        check_output_shape(m, (1, 64, 64, 3))

    def test_output_shape_channels_first_fails_loudly(self):
        with pytest.raises(LPModelError, match="channels-first"):
            check_output_shape(_meta(), (3, 64, 64))

    def test_output_shape_wrong_k_or_stride(self):
        with pytest.raises(LPModelError):
            check_output_shape(_meta(), (64, 64, 4))
        with pytest.raises(LPModelError):
            check_output_shape(_meta(), (32, 32, 3))

    def test_input_shape(self):
        check_input_shape(_meta(), (256, 256, 3))
        with pytest.raises(LPModelError, match="input"):
            check_input_shape(_meta(), (224, 224, 3))


# ---------------------------------------------------------------------------
# analytic: known sub-pixel peaks
# ---------------------------------------------------------------------------

PEAKS = [(10.3, 20.7), (31.5, 31.5), (40.12, 12.88), (22.0, 50.25)]


class TestKnownSubpixelPeak:
    @pytest.mark.parametrize("method", ["exact", "windowed", "reference"])
    def test_peak_maps_to_stride_times_heatmap_coordinate(self, method):
        meta = _meta(k=len(PEAKS))
        raw = np.stack([_blob(64, 64, x, y) for x, y in PEAKS], -1).astype(np.float32)
        out = (decode_heatmaps(np.moveaxis(raw, -1, 0), meta) if method == "reference"
               else decode(raw, meta, method=method))
        want = np.array([(4 * x, 4 * y) for x, y in PEAKS])
        assert np.abs(out[:, :2] - want).max() < 0.01
        assert (out[:, 2] > 0.99).all()       # a sharp blob: confidence ~1

    def test_off_grid_peak_is_not_snapped(self):
        """argmax would put both on the same grid point; soft-argmax must not."""
        meta = _meta(k=2)
        raw = np.stack([_blob(64, 64, 20.0, 20.0), _blob(64, 64, 20.4, 20.0)], -1)
        out = decode(raw.astype(np.float32), meta)
        assert out[1, 0] - out[0, 0] == pytest.approx(1.6, abs=0.01)

    def test_downsample_factor_1_small_lp_bias(self):
        """One bicubic upsample leaves LP's soft-argmax a sub-pixel bias of up
        to ~0.2 input px (LP's own behaviour: the NumPy port agrees)."""
        meta = _meta(k=1, df=1)
        raw = _blob(128, 128, 47.36, 77.9)[..., None].astype(np.float32)
        out = decode(raw, meta)
        assert out[0, :2] == pytest.approx([2 * 47.36, 2 * 77.9], abs=0.25)
        ref = decode_heatmaps(np.moveaxis(raw, -1, 0), meta)
        assert np.abs(out - ref).max() < 1e-3

    def test_downsample_factor_3_reproduces_lp_one_pixel_offset(self):
        """LP subtracts 2.5 for downsample_factor 3, where three x2 upsamples
        put a coarse pixel centre at 8u + 3.5: LP's keypoints come out +1
        input px on both axes. We match LP, not geometry (see lp_decode)."""
        meta = _meta(k=1, df=3)
        raw = _blob(32, 32, 16.0, 11.0)[..., None].astype(np.float32)
        out = decode(raw, meta)
        assert out[0, :2] == pytest.approx([8 * 16.0 + 1, 8 * 11.0 + 1], abs=0.01)

    def test_out_size_scales_each_axis(self):
        meta = _meta(k=1)
        raw = _blob(64, 64, 16.0, 40.0)[..., None].astype(np.float32)
        out = decode(raw, meta, out_size=(1280, 960))   # x * 5, y * 3.75
        assert out[0, :2] == pytest.approx([64 * 5, 160 * 3.75], abs=0.05)


# ---------------------------------------------------------------------------
# live (OpenCV) path == NumPy port
# ---------------------------------------------------------------------------

def test_cv2_upsampling_matches_numpy_port():
    import cv2

    from modules.lp_decode import _upsample_hwk
    rng = np.random.default_rng(1)
    h = rng.random((5, 40, 56), dtype=np.float32)
    ref = upsample2x(upsample2x(h))
    fast = _upsample_hwk(np.ascontiguousarray(np.moveaxis(h, 0, -1)), 2)
    assert np.abs(np.moveaxis(fast, -1, 0) - ref).max() < 1e-5 * np.abs(ref).max()
    assert cv2.INTER_CUBIC  # (cv2's cubic is torch's bicubic; see lp_decode)


def test_single_keypoint_model():
    """cv2 drops a channel axis of size 1; the decoder must cope."""
    meta = _meta(k=1)
    out = decode(_blob(64, 64, 9.5, 9.5)[..., None].astype(np.float32), meta)
    assert out.shape == (1, 3)
    assert out[0, :2] == pytest.approx([38.0, 38.0], abs=0.01)


def test_prepare_input_keeps_bgr_and_resizes():
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[..., 0] = 200                       # blue only
    x = prepare_input(frame, (256, 256))
    assert x.shape == (256, 256, 3)
    assert x[..., 0].min() == 200 and x[..., 1:].max() == 0


# ---------------------------------------------------------------------------
# against Lightning Pose's own outputs
# ---------------------------------------------------------------------------

def _cases(path):
    f = np.load(path)
    if str(f["kind"]) != "synthetic":
        return []
    return [(str(n), f[f"{n}__raw"], int(f[f"{n}__df"]), f[f"{n}__preds"],
             f[f"{n}__conf"]) for n in f["cases"]]


SYNTH = [(p.name, *c) for p in FIXTURES for c in _cases(p)]
MODEL = [p for p in FIXTURES if str(np.load(p)["kind"]) == "model"]


@pytest.mark.skipif(not SYNTH, reason="no synthetic LP reference fixture")
class TestAgainstLPSynthetic:
    @pytest.mark.parametrize("fixture,name,raw,df,preds,conf", SYNTH,
                             ids=[f"{s[0]}:{s[1]}" for s in SYNTH])
    def test_exact_matches_lp(self, fixture, name, raw, df, preds, conf):
        hm, wm, k = raw.shape
        meta = _meta(k=k, input_size=(hm * 2 ** df, wm * 2 ** df), df=df)
        for out in (decode(raw, meta), decode_heatmaps(np.moveaxis(raw, -1, 0), meta)):
            # 0.05 px covers float32 summation over near-flat maps; sharp
            # peaks agree to ~1e-4 px
            assert np.abs(out[:, :2] - preds).max() < 0.05
            assert np.abs(out[:, 2] - conf).max() < 1e-4

    @pytest.mark.parametrize("fixture,name,raw,df,preds,conf", SYNTH,
                             ids=[f"{s[0]}:{s[1]}" for s in SYNTH])
    def test_windowed_matches_lp_where_confident(self, fixture, name, raw, df, preds, conf):
        """The windowed decode is stated to match LP within 0.01 px wherever
        LP's confidence is >= 0.3; below that the two may differ."""
        hm, wm, k = raw.shape
        meta = _meta(k=k, input_size=(hm * 2 ** df, wm * 2 ** df), df=df)
        out = decode(raw, meta, method="windowed")
        ok = conf >= 0.3
        if ok.any():
            assert np.abs(out[ok, :2] - preds[ok]).max() < 0.01
            assert np.abs(out[ok, 2] - conf[ok]).max() < 1e-3


@pytest.mark.skipif(not MODEL, reason="no model LP reference fixture "
                    "(tools/lp_reference_outputs.py model ...)")
@pytest.mark.parametrize("path", MODEL, ids=[p.name for p in MODEL])
def test_against_lp_model_outputs(path):
    f = np.load(path)
    names = [str(n) for n in f["keypoint_names"]]
    h, w = (int(v) for v in f["input_size"])
    meta = validate_meta({"type": "lp_heatmap", "keypoint_names": names,
                          "input_size": [h, w],
                          "downsample_factor": int(f["downsample_factor"]),
                          "softmax_temperature": float(f["temperature"]),
                          "input_channel_order": "BGR"})
    for i in range(len(f["raw"])):
        out = decode(f["raw"][i], meta)
        assert np.abs(out[:, :2] - f["preds_input"][i]).max() < 0.05
        assert np.abs(out[:, 2] - f["conf"][i]).max() < 1e-3
        fw, fh = (int(v) for v in f["frame_sizes"][i])
        out = decode(f["raw"][i], meta, out_size=(fw, fh))
        assert np.abs(out[:, :2] - f["preds_frame"][i]).max() < 0.05 * max(fw / w, fh / h)
