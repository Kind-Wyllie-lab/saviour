import numpy as np

from src.modules.exposure_check import assess, verdict_of


def _frame(background, subject_values, subject_frac=0.1, n=10000):
    k = int(n * subject_frac)
    rng = np.random.default_rng(0)
    return np.concatenate([np.full(n - k, background, np.uint8),
                           rng.choice(subject_values, k).astype(np.uint8)])


def test_white_arena_dark_rat_is_fine():
    # 90 % of the frame clipped white, the rat spread over 10-200: right.
    r = assess(_frame(255, np.arange(10, 200)), warn_pct=5)
    assert r["over_pct"] > 80 and r["verdict"] == "ok"


def test_washed_out_subject_is_over():
    r = assess(_frame(255, np.arange(180, 250)), warn_pct=5)
    assert r["verdict"] == "over"


def test_dark_arena_white_rat_is_fine_but_all_black_is_under():
    assert assess(_frame(0, np.arange(60, 250)), warn_pct=5)["verdict"] == "ok"
    assert assess(_frame(0, np.arange(5, 60)), warn_pct=5)["verdict"] == "under"


def test_squeezed_greys_are_low_contrast():
    # Contrast turned down / aperture closed: everything within 90-150.
    r = assess(_frame(120, np.arange(90, 150), subject_frac=0.3), warn_pct=5)
    assert r["verdict"] == "low_contrast"


def test_histogram_has_64_bins_summing_to_the_sample():
    r = assess(np.arange(256, dtype=np.uint8))
    assert len(r["hist"]) == 64 and sum(r["hist"]) == 256 and r["hist"][0] == 4


def test_verdict_needs_every_sample_to_agree():
    ok = {"over_pct": 0, "under_pct": 0, "span": 200, "verdict": "ok"}
    over = {"over_pct": 90, "under_pct": 0, "span": 20, "verdict": "over"}
    assert verdict_of([over, over, over])["verdict"] == "over"
    assert verdict_of([over, ok, over])["verdict"] == "ok"
    assert verdict_of([])["verdict"] == "ok"
