"""
Tests for src/controller/video_compose.py — specifically `_StreamCursor`'s
proportional remap for a camera whose `.ts` frame count disagrees with its
per-frame CSV row count (a libcamera sync-client discard skew).
See plans/multicam-frame-alignment-and-sync-provenance.md.
"""

import csv
import os

import cv2
import numpy as np
import pytest

from src.controller.video_compose import CameraStream, _StreamCursor


def _write_video(path: str, n_frames: int, size=(64, 48)) -> bool:
    w, h = size
    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h)
    )
    if not writer.isOpened():
        return False
    for _ in range(n_frames):
        writer.write(np.zeros((h, w, 3), dtype=np.uint8))
    writer.release()
    return True


def _write_csv(path: str, n_rows: int, t0_ns: int = 1_700_000_000_000_000_000,
               step_ns: int = 33_333_333) -> None:
    with open(path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame_id", "timestamp_ns", "timestamp_utc"])
        for i in range(n_rows):
            wr.writerow([i, t0_ns + i * step_ns, ""])


@pytest.fixture
def stream(tmp_path):
    def _make(n_frames: int, n_rows: int):
        vp = str(tmp_path / f"cam_{n_frames}_{n_rows}.mp4")
        cp = str(tmp_path / f"cam_{n_frames}_{n_rows}_timestamps.csv")
        if not _write_video(vp, n_frames):
            pytest.skip("cv2.VideoWriter unavailable (no codec)")
        _write_csv(cp, n_rows)
        return CameraStream(name="cam", video_path=vp, csv_path=cp)
    return _make


def test_no_remap_when_counts_match(stream):
    s = stream(50, 50)
    c = _StreamCursor(s, frame_count=50)
    assert c._remap is False
    assert c.deficit == 0
    assert c.mismatch_ms == 0.0
    assert c._frame_for_row(17) == 17          # identity


def test_no_remap_for_a_single_frame_deficit(stream):
    s = stream(49, 50)
    c = _StreamCursor(s, frame_count=49)
    assert c._remap is False                    # <= 1 is within tolerance
    assert c._frame_for_row(30) == 30


def test_remap_spreads_a_client_discard_deficit(stream):
    # 45 encoded frames, 50 CSV rows -> 5-frame sync-client deficit.
    s = stream(45, 50)
    c = _StreamCursor(s, frame_count=45)
    assert c._remap is True
    assert c.deficit == 5
    assert c._frame_for_row(0) == 0
    assert c._frame_for_row(49) == 44           # last row -> last frame
    # middle row maps ~proportionally, never past the real frame count
    assert c._frame_for_row(25) == round(25 * 44 / 49)
    assert c.mismatch_ms == pytest.approx(2.5 * (1000 / 30), rel=0.1)


def test_sync_to_stays_within_the_decoded_frames(stream):
    s = stream(45, 50)
    c = _StreamCursor(s, frame_count=45)
    ts = c.timestamps_ns
    # Walk a monotonic grid across the whole window.
    for j in range(0, len(ts)):
        c.sync_to(ts[j])
        assert c.idx <= 44                       # never runs off the end
    assert c.idx == 44                           # reached the last real frame
    c.release()


def test_probe_count_zero_falls_back_to_naive_mapping(stream):
    s = stream(45, 50)
    c = _StreamCursor(s, frame_count=0)          # ffprobe unavailable
    assert c._remap is False
    assert c.n_frames == c.n_rows                # trusts 1:1 (old behaviour)
    c.release()


def _cv2_frame_count(path: str) -> int:
    cap = cv2.VideoCapture(path)
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def test_compose_session_video_reports_the_mismatch(tmp_path, stream, monkeypatch):
    """A session with one clean camera and one deficit camera: the
    composite is still produced and the deficit camera is warned about."""
    from src.controller import video_compose
    from src.controller.video_compose import compose_session_video

    date_dir = tmp_path / "20260907"
    for name, nf, nr in (("camA", 50, 50), ("camB", 45, 50)):
        d = date_dir / name
        d.mkdir(parents=True)
        vp = str(d / f"s_{name}_(0_x).mp4")
        if not _write_video(vp, nf):
            pytest.skip("cv2.VideoWriter unavailable")
        _write_csv(str(d / f"s_{name}_(0_x)_timestamps.csv"), nr)

    # No ffprobe in the dev env -- stand in the real decoded frame count.
    monkeypatch.setattr(video_compose, "_probe_frame_count", _cv2_frame_count)

    warnings: list[str] = []
    out = str(tmp_path / "out.mp4")
    try:
        compose_session_video(str(date_dir), out, fps=15, warnings=warnings)
    except RuntimeError as e:
        if "VideoWriter" in str(e):
            pytest.skip("cv2.VideoWriter unavailable for the canvas")
        raise

    assert os.path.isfile(out)
    assert any("camB" in w and "remapped proportionally" in w for w in warnings)
    assert not any("camA" in w for w in warnings)


# --------------------------------------------------------------------------- #
# Layout preview: seek-based thumbnail, audio panel height                    #
# --------------------------------------------------------------------------- #


def _write_ramp_video(path: str, n_frames: int, size=(64, 48)) -> bool:
    """Frame i is a flat grey of brightness 2*i, so a decoded frame says
    roughly which index it came from."""
    w, h = size
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    if not writer.isOpened():
        return False
    for i in range(n_frames):
        writer.write(np.full((h, w, 3), 2 * i, dtype=np.uint8))
    writer.release()
    return True


def test_representative_frame_seeks_near_the_requested_time(tmp_path):
    from src.controller.video_compose import _representative_frame

    vp = str(tmp_path / "cam.mp4")
    cp = str(tmp_path / "cam_timestamps.csv")
    if not _write_ramp_video(vp, 90):
        pytest.skip("cv2.VideoWriter unavailable (no codec)")
    t0, step = 1_700_000_000_000_000_000, 33_333_333
    _write_csv(cp, 90, t0_ns=t0, step_ns=step)
    s = CameraStream(name="cam", video_path=vp, csv_path=cp)

    frame = _representative_frame(s, t0 + 60 * step)
    # ~frame 60 -> brightness ~120; allow a few frames of seek slop and
    # codec error, but far from frame 0 (what a failed seek would give).
    assert abs(float(frame.mean()) - 120) < 16


def test_representative_frame_falls_back_to_first_frame(tmp_path):
    from src.controller.video_compose import _representative_frame

    vp = str(tmp_path / "cam.mp4")
    cp = str(tmp_path / "cam_timestamps.csv")
    if not _write_ramp_video(vp, 10):
        pytest.skip("cv2.VideoWriter unavailable (no codec)")
    t0, step = 1_700_000_000_000_000_000, 33_333_333
    _write_csv(cp, 10, t0_ns=t0, step_ns=step)
    s = CameraStream(name="cam", video_path=vp, csv_path=cp)

    # Far past the end of the video: the seek reads nothing.
    frame = _representative_frame(s, t0 + 10_000 * step)
    assert frame is not None and frame.shape[:2] == (48, 64)


@pytest.mark.parametrize("mode,height_px,expect_h", [
    ("panel", 60, 100 + 60),   # stacked below the video
    ("strip", 30, 100),        # overlaid; frame height unchanged
    ("strip", 500, 100),       # clamped to the frame
])
def test_attach_audio_preview_uses_the_given_height(tmp_path, mode, height_px, expect_h):
    from src.controller.video_compose import _attach_audio_preview

    png = str(tmp_path / "spec.png")
    cv2.imwrite(png, np.full((20, 40, 3), 200, dtype=np.uint8))
    frame = np.zeros((100, 160, 3), dtype=np.uint8)
    out = _attach_audio_preview(frame, png, mode, height_px)
    assert out.shape[:2] == (expect_h, 160)
    if mode == "strip":
        strip_h = min(height_px, 100)
        assert out[100 - strip_h:, :].mean() > 150   # spectrogram drawn
        if strip_h < 100:
            assert out[: 100 - strip_h, :].mean() == 0  # video untouched
