"""
Tests for src/controller/compose.py

The layout planner and spec validation are pure. The worker is exercised
with a fake `_render` so nothing here needs OpenCV or ffmpeg.
"""

import time
from dataclasses import dataclass

import pytest

from src.controller.compose import (
    ComposeError,
    ComposeSpec,
    ComposeWorker,
    any_session_busy_reason,
    discover_streams,
    plan_regions,
    resolve_date_dir,
)

# --------------------------------------------------------------------------- #
# ComposeSpec.from_dict                                                       #
# --------------------------------------------------------------------------- #


def test_spec_defaults_and_roundtrip():
    spec = ComposeSpec.from_dict({"session_name": "my-sess_01"})
    assert spec.layout == "auto"
    assert spec.fps == 15
    assert spec.fmt == "mp4"
    assert spec.streams is None


@pytest.mark.parametrize("bad", [
    {},
    {"session_name": "has space"},
    {"session_name": "ok", "layout": "mosaic"},
    {"session_name": "ok", "fps": 0},
    {"session_name": "ok", "fps": 999},
    {"session_name": "ok", "fmt": "avi"},
    {"session_name": "ok", "streams": "camera_a"},
    {"session_name": "ok", "streams": ["../etc"]},
    {"session_name": "ok", "date_dir": "../../x"},
])
def test_spec_rejects_bad_input(bad):
    with pytest.raises(ComposeError):
        ComposeSpec.from_dict(bad)


def test_spec_keeps_valid_stream_list():
    spec = ComposeSpec.from_dict(
        {"session_name": "s", "streams": ["camera_a", "camera_b"], "layout": "grid"}
    )
    assert spec.streams == ["camera_a", "camera_b"]


# --------------------------------------------------------------------------- #
# plan_regions                                                                #
# --------------------------------------------------------------------------- #


def test_side_layout_preserves_each_aspect():
    # a 16:9 and a 1:1 camera, side by side
    regions, cw, ch = plan_regions([(1920, 1080), (1000, 1000)], "side")
    assert len(regions) == 2
    (x0, y0, w0, h0), (x1, y1, w1, h1) = regions
    assert x0 == 0 and x1 == w0          # laid left-to-right, no gap
    assert h0 == h1 == ch                # same row height
    assert w0 / h0 == pytest.approx(16 / 9, abs=0.02)
    assert w1 / h1 == pytest.approx(1.0, abs=0.02)
    assert cw == w0 + w1
    assert all(v % 2 == 0 for v in (cw, ch, w0, h0, w1, h1))


def test_stack_layout_is_a_single_column():
    regions, cw, ch = plan_regions([(1280, 720), (1280, 720)], "stack")
    xs = {r[0] for r in regions}
    assert xs == {0}
    assert regions[1][1] == regions[0][3]   # second box starts below the first
    assert ch == sum(r[3] for r in regions)


def test_grid_layout_uniform_cells():
    regions, cw, ch = plan_regions([(1000, 1000)] * 4, "grid", canvas_width=1920)
    ws = {r[2] for r in regions}
    hs = {r[3] for r in regions}
    assert len(ws) == 1 and len(hs) == 1   # every cell identical
    assert regions[0][:2] == (0, 0)
    assert regions[3][0] == regions[1][0]   # 2x2: cell 3 under cell 1


def test_auto_two_streams_is_side_by_side():
    regions, _, _ = plan_regions([(1920, 1080), (1920, 1080)], "auto")
    assert regions[0][1] == regions[1][1] == 0   # same row


def test_plan_regions_rejects_empty():
    with pytest.raises(ComposeError):
        plan_regions([], "grid")


# --------------------------------------------------------------------------- #
# session layout resolution                                                   #
# --------------------------------------------------------------------------- #


def test_resolve_date_dir_picks_latest(tmp_path):
    sess = tmp_path / "sess"
    (sess / "20260701").mkdir(parents=True)
    (sess / "20260805").mkdir()
    assert resolve_date_dir(str(sess), None).endswith("20260805")
    assert resolve_date_dir(str(sess), "20260701").endswith("20260701")
    with pytest.raises(ComposeError):
        resolve_date_dir(str(sess), "19990101")


def test_discover_streams_filters_and_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.controller.compose.probe_dimensions", lambda _p: (640, 480)
    )
    dd = tmp_path / "20260805"
    for name in ("camera_a", "camera_b", "microphone_x"):
        d = dd / name
        d.mkdir(parents=True)
    (dd / "camera_a" / "v.ts").write_bytes(b"x")
    (dd / "camera_a" / "v_timestamps.csv").write_text("timestamp_ns\n1\n")
    (dd / "camera_b" / "v.mp4").write_bytes(b"x")
    (dd / "camera_b" / "v_timestamps.csv").write_text("timestamp_ns\n1\n")
    (dd / "microphone_x" / "a.flac").write_bytes(b"x")  # not a camera -> skipped

    everything = discover_streams(str(dd), None)
    assert {s.name for s in everything} == {"camera_a", "camera_b"}
    assert everything[0].width == 640

    just_b = discover_streams(str(dd), ["camera_b"])
    assert [s.name for s in just_b] == ["camera_b"]

    with pytest.raises(ComposeError):
        discover_streams(str(dd), ["camera_a", "camera_missing"])


# --------------------------------------------------------------------------- #
# any_session_busy_reason                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeSession:
    state: str


def test_busy_reason_blocks_on_active_and_exporting():
    assert any_session_busy_reason({"s1": _FakeSession("ended")}) is None
    assert any_session_busy_reason({"s1": _FakeSession("error")}) is None
    assert "s1" in any_session_busy_reason({"s1": _FakeSession("active")})
    assert any_session_busy_reason({"s1": _FakeSession("stopped")}) is not None
    assert any_session_busy_reason({"s1": {"state": "scheduled"}}) is not None


# --------------------------------------------------------------------------- #
# ComposeWorker                                                               #
# --------------------------------------------------------------------------- #


def _worker(tmp_path, render=None, busy_check=None):
    updates = []
    w = ComposeWorker(
        share_path=str(tmp_path),
        busy_check=busy_check,
        on_update=updates.append,
    )
    if render is not None:
        w._render = render  # type: ignore[assignment]
    return w, updates


def _wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_worker_runs_job_to_done(tmp_path):
    w, updates = _worker(tmp_path, render=lambda job: "sess/20260805/out.mp4")
    job = w.submit(ComposeSpec.from_dict({"session_name": "sess"}))
    assert w._render is not None
    assert _wait_for(lambda: w.get(job.id).state == "done")
    done = w.get(job.id)
    assert done.output_rel == "sess/20260805/out.mp4"
    assert done.progress == 1.0
    assert any(u["state"] == "running" for u in updates)
    assert updates[-1]["state"] == "done"


def test_worker_reports_render_error(tmp_path):
    def boom(_job):
        raise RuntimeError("codec unavailable")

    w, _ = _worker(tmp_path, render=boom)
    job = w.submit(ComposeSpec.from_dict({"session_name": "sess"}))
    assert _wait_for(lambda: w.get(job.id).state == "error")
    assert "codec unavailable" in w.get(job.id).error


def test_worker_rejects_when_busy(tmp_path):
    w, _ = _worker(
        tmp_path, render=lambda j: "x",
        busy_check=lambda: "a session is recording",
    )
    with pytest.raises(ComposeError, match="recording"):
        w.submit(ComposeSpec.from_dict({"session_name": "sess"}))


def test_worker_queue_full(tmp_path):
    gate = {"go": False}

    def slow(_job):
        while not gate["go"]:
            time.sleep(0.01)
        return "x"

    w, _ = _worker(tmp_path, render=slow)
    for _ in range(4):
        w.submit(ComposeSpec.from_dict({"session_name": "sess"}))
    with pytest.raises(ComposeError, match="queue is full"):
        w.submit(ComposeSpec.from_dict({"session_name": "sess"}))
    gate["go"] = True


def test_worker_cancel_queued_job(tmp_path):
    gate = {"go": False}

    def slow(_job):
        while not gate["go"]:
            time.sleep(0.01)
        return "x"

    w, _ = _worker(tmp_path, render=slow)
    first = w.submit(ComposeSpec.from_dict({"session_name": "sess"}))
    second = w.submit(ComposeSpec.from_dict({"session_name": "sess"}))
    assert w.cancel(second.id) is True
    assert w.get(second.id).state == "cancelled"
    gate["go"] = True
    assert _wait_for(lambda: w.get(first.id).state == "done")
