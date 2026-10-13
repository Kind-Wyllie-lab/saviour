"""Tests for src/controller/camera_segments.py: pairing a camera folder's
segment videos with their timestamp CSVs, in recording order."""

from src.controller.camera_segments import (
    camera_segments,
    csv_bounds,
    segment_at,
    timestamps_csv_for,
)

T0 = 1_700_000_000_000_000_000


def _csv(path, first, n=3, step=1000):
    rows = "\n".join(f"{i},{first + i * step},x" for i in range(n))
    path.write_text(f"frame_id,timestamp_ns,timestamp_utc\n{rows}\n")


def _segment(d, tag, first, n=3, partial=False, video_bytes=b"v"):
    suffix = "_PARTIAL" if partial else ""
    (d / f"s_cam_({tag}){suffix}.ts").write_bytes(video_bytes)
    _csv(d / f"s_cam_({tag})_timestamps{suffix}.csv", first, n)


def test_timestamps_csv_for():
    assert timestamps_csv_for("/x/a_(0_1).ts") == "/x/a_(0_1)_timestamps.csv"
    assert (timestamps_csv_for("/x/a_(9_1)_PARTIAL.ts")
            == "/x/a_(9_1)_timestamps_PARTIAL.csv")


def test_segments_ordered_by_time_not_name(tmp_path):
    # Index restarts after a re-arm: (0_…) after (10_…) in time.
    _segment(tmp_path, "10_a", T0)
    _segment(tmp_path, "2_b", T0 - 5_000)
    _segment(tmp_path, "0_c", T0 + 5_000)
    segs = camera_segments(str(tmp_path))
    assert [s.video_path.rsplit("(", 1)[1][:4] for s in segs] == ["2_b)", "10_a", "0_c)"]
    assert segs[0].first_ns == T0 - 5_000 and segs[0].last_ns == T0 - 5_000 + 2_000


def test_partial_paired_empty_and_pending_skipped(tmp_path):
    _segment(tmp_path, "0_a", T0)
    _segment(tmp_path, "1_b", T0 + 10_000, partial=True)
    _segment(tmp_path, "2_c", T0 + 20_000, video_bytes=b"")        # zero bytes
    (tmp_path / "PENDING_s_cam_(3_d).ts").write_bytes(b"v")         # mid-export
    _csv(tmp_path / "PENDING_s_cam_(3_d)_timestamps.csv", T0 + 30_000)
    (tmp_path / "s_cam_(4_e).ts").write_bytes(b"v")                 # no CSV
    segs = camera_segments(str(tmp_path))
    assert len(segs) == 2
    assert segs[1].video_path.endswith("_PARTIAL.ts")


def test_csv_bounds_reads_head_and_tail(tmp_path):
    p = tmp_path / "x_timestamps.csv"
    _csv(p, T0, n=5000)
    assert csv_bounds(str(p)) == (T0, T0 + 4999 * 1000)
    q = tmp_path / "no_ts.csv"
    q.write_text("a,b\n1,2\n")
    assert csv_bounds(str(q)) is None
    e = tmp_path / "empty_timestamps.csv"
    e.write_text("frame_id,timestamp_ns\n")
    assert csv_bounds(str(e)) is None


def test_segment_at(tmp_path):
    _segment(tmp_path, "0_a", T0)
    _segment(tmp_path, "1_b", T0 + 10_000)
    segs = camera_segments(str(tmp_path))
    assert segment_at(segs, T0 - 1) is segs[0]          # before: first
    assert segment_at(segs, T0 + 1_500) is segs[0]
    assert segment_at(segs, T0 + 5_000) is segs[0]      # in the gap: the one before
    assert segment_at(segs, T0 + 10_000) is segs[1]
    assert segment_at(segs, T0 + 99_000) is segs[1]
