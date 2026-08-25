"""
experimental_scorers.py -- candidate motion-scoring algorithms being
evaluated as replacements/additions to HabitatMotionDetector's frame_diff/
MOG2 (see ../motion_detector.py). All CPU-only, no ML accelerator assumed --
habitat_camera runs on a plain Pi 5 with no Hailo HAT (unlike apa_camera/
lightning_camera). None of this is wired into the live module; it exists
purely for evaluation via score_video.py + sweep_motion_params.py against
label_activity.py's ground truth. Each scorer exposes the same
score(frame_bgr) -> float interface as HabitatMotionDetector, so they're
interchangeable in the tooling.

Not yet checked: actual per-frame timing on real Pi 5 hardware for the
heavier scorers (optical flow especially) -- accuracy is being evaluated
first since that's cheap to test on labeled footage from a dev machine;
before any of this could ever be proposed for the live module, whichever
wins on accuracy needs a real timing check against the camera's frame
interval (e.g. 40ms at 25fps) on the actual hardware, not just "should be
fine" reasoning.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # .../src
from modules.variants.habitat_camera.motion_detector import HabitatMotionDetector  # noqa: E402


def _resize_to_width(w0: int, h0: int, target_width: int) -> tuple[int, int]:
    w = max(1, int(target_width))
    h = max(1, round(w * h0 / w0))
    return w, h


class TunableFrameDiffScorer:
    """Same pipeline as HabitatMotionDetector's frame_diff path, but with a
    settable pixel-change threshold -- hardcoded at 15 in production, not
    currently exposed as a config option there. Lets that constant be swept
    like any other parameter without touching the production file."""

    def __init__(self, process_width: int = 256, pixel_threshold: int = 15):
        self.process_width = max(1, int(process_width))
        self.pixel_threshold = pixel_threshold
        self._prev_gray = None

    def score(self, frame_bgr: np.ndarray) -> float:
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = _resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.GaussianBlur(cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA), (5, 5), 0)
        if self._prev_gray is None or self._prev_gray.shape != proc.shape:
            self._prev_gray = proc
            return 0.0
        diff = cv2.absdiff(proc, self._prev_gray)
        self._prev_gray = proc
        changed = int(np.count_nonzero(diff >= self.pixel_threshold))
        return changed / float(nx * ny)


class ThreeFrameDiffScorer:
    """Requires a pixel to have changed in BOTH consecutive frame-pairs
    (t-2->t-1 and t-1->t) before counting it as changed -- a classic
    noise-rejection trick: real motion tends to persist (a moving edge keeps
    moving for at least 2-3 frames), while single-frame sensor/IR noise
    rarely lands on the exact same pixel two frame-pairs running."""

    def __init__(self, process_width: int = 256, pixel_threshold: int = 15):
        self.process_width = max(1, int(process_width))
        self.pixel_threshold = pixel_threshold
        self._f0 = None  # t-2
        self._f1 = None  # t-1

    def score(self, frame_bgr: np.ndarray) -> float:
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = _resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.GaussianBlur(cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA), (5, 5), 0)

        if self._f1 is None or self._f1.shape != proc.shape:
            self._f0, self._f1 = None, proc
            return 0.0
        if self._f0 is None:
            self._f0, self._f1 = self._f1, proc
            return 0.0

        changed_a = cv2.absdiff(self._f1, self._f0) >= self.pixel_threshold
        changed_b = cv2.absdiff(proc, self._f1) >= self.pixel_threshold
        both = int(np.count_nonzero(changed_a & changed_b))
        self._f0, self._f1 = self._f1, proc
        return both / float(nx * ny)


class KnnScorer:
    """cv2's KNN background subtractor -- an alternative to MOG2's Gaussian
    mixture model, sometimes more sensitive to slow/subtle foreground change
    since it works by per-pixel sample history rather than a fitted
    distribution. Worth checking against MOG2's weakness here (absorbing a
    persistently-present sleeping resident into its background model)."""

    def __init__(self, process_width: int = 256, history: int = 500,
                 dist2_threshold: float = 400.0):
        self.process_width = max(1, int(process_width))
        self._bg = cv2.createBackgroundSubtractorKNN(
            history=int(history), dist2Threshold=float(dist2_threshold), detectShadows=True
        )

    def score(self, frame_bgr: np.ndarray) -> float:
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = _resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.GaussianBlur(cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA), (5, 5), 0)
        mask = self._bg.apply(proc)
        changed = int(np.count_nonzero(mask >= 200))  # 255=foreground, 127=shadow (excluded)
        return changed / float(nx * ny)


class EdgeDiffScorer:
    """Diffs consecutive Canny edge maps instead of raw pixel brightness.
    Real motion moves edges (a silhouette boundary shifts); a uniform
    AE-driven brightness/gain change generally doesn't, since Canny edges
    come from local gradients and gradient-based detection is close to
    invariant under a global additive/multiplicative brightness shift. This
    is the candidate most likely to sidestep the AE-gate tension found
    live (real motion getting rejected because it also nudges auto-exposure
    slightly) -- if edge-diff doesn't react to that kind of change in the
    first place, the gate may not even be needed for this scoring path."""

    def __init__(self, process_width: int = 256, canny_lo: int = 50, canny_hi: int = 150):
        self.process_width = max(1, int(process_width))
        self.canny_lo = canny_lo
        self.canny_hi = canny_hi
        self._prev_edges = None

    def score(self, frame_bgr: np.ndarray) -> float:
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = _resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.GaussianBlur(cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA), (5, 5), 0)
        edges = cv2.Canny(proc, self.canny_lo, self.canny_hi)

        if self._prev_edges is None or self._prev_edges.shape != edges.shape:
            self._prev_edges = edges
            return 0.0
        diff = cv2.bitwise_xor(edges, self._prev_edges)
        self._prev_edges = edges
        changed = int(np.count_nonzero(diff))
        return changed / float(nx * ny)


class BlobSizeScorer:
    """Same downsample/blur/frame-diff pipeline as TunableFrameDiffScorer,
    but scores by the size of the LARGEST single connected blob of changed
    pixels rather than the total changed-pixel fraction. A moving animal is
    one coherent shape; scattered per-pixel sensor/IR noise generally isn't
    -- this targets the frame-to-frame jitter seen in raw score traces
    (score spiking then dropping every other frame even during real,
    sustained motion) by being insensitive to how many total pixels
    changed, only whether they changed together in one place."""

    def __init__(self, process_width: int = 256, pixel_threshold: int = 15):
        self.process_width = max(1, int(process_width))
        self.pixel_threshold = pixel_threshold
        self._prev_gray = None

    def score(self, frame_bgr: np.ndarray) -> float:
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = _resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.GaussianBlur(cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA), (5, 5), 0)
        if self._prev_gray is None or self._prev_gray.shape != proc.shape:
            self._prev_gray = proc
            return 0.0
        diff = cv2.absdiff(proc, self._prev_gray)
        self._prev_gray = proc
        mask = (diff >= self.pixel_threshold).astype(np.uint8)
        n_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n_labels <= 1:  # only the background label -- nothing changed
            return 0.0
        largest = int(stats[1:, cv2.CC_STAT_AREA].max())
        return largest / float(nx * ny)


class OpticalFlowScorer:
    """Dense Farneback optical flow -- scores mean flow MAGNITUDE (pixels of
    displacement) rather than raw brightness change, so random sensor/IR
    noise (no coherent displacement) shouldn't score highly even if it's
    bright, while real physical movement should, even if subtle. Heavier per
    frame than frame differencing -- see module docstring on the unverified
    Pi 5 timing caveat before this could ever be a real candidate."""

    def __init__(self, process_width: int = 256):
        self.process_width = max(1, int(process_width))
        self._prev_gray = None

    def score(self, frame_bgr: np.ndarray) -> float:
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = _resize_to_width(w0, h0, self.process_width)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA)

        if self._prev_gray is None or self._prev_gray.shape != proc.shape:
            self._prev_gray = proc
            return 0.0

        flow = cv2.calcOpticalFlowFarneback(
            self._prev_gray, proc, None, 0.5, 3, 15, 3, 5, 1.2, 0
        )
        self._prev_gray = proc
        mag = cv2.magnitude(flow[..., 0], flow[..., 1])
        return float(np.mean(mag))


# Shared registry so score_video.py and evaluate_dataset.py don't each keep
# their own drifting copy of "which algorithm names exist." process_width
# and pixel_threshold are the two knobs every non-production scorer accepts;
# canny_lo/canny_hi (edge_diff) and history/dist2_threshold (knn) stay at
# their class defaults here -- expose them the same way if they ever need
# sweeping too.
ALGORITHM_NAMES = (
    "frame_diff", "three_frame_diff", "knn", "optical_flow",
    "edge_diff", "blob_size", "mog2",
)


def build_scorer(algorithm: str, process_width: int = 256, pixel_threshold: int = 15):
    if algorithm == "frame_diff":
        return TunableFrameDiffScorer(process_width=process_width, pixel_threshold=pixel_threshold)
    if algorithm == "three_frame_diff":
        return ThreeFrameDiffScorer(process_width=process_width, pixel_threshold=pixel_threshold)
    if algorithm == "knn":
        return KnnScorer(process_width=process_width)
    if algorithm == "optical_flow":
        return OpticalFlowScorer(process_width=process_width)
    if algorithm == "edge_diff":
        return EdgeDiffScorer(process_width=process_width)
    if algorithm == "blob_size":
        return BlobSizeScorer(process_width=process_width, pixel_threshold=pixel_threshold)
    if algorithm == "mog2":
        return HabitatMotionDetector(algorithm="mog2", process_width=process_width)
    raise ValueError(f"Unknown algorithm {algorithm!r} -- choose from {ALGORITHM_NAMES}")
