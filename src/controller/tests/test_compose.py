"""
Tests for src/controller/compose.py

The layout planner and spec validation are pure. The worker is exercised
with a fake `_render` so nothing here needs OpenCV or ffmpeg.
"""

import os
import sys
import time
import types
from dataclasses import dataclass

import pytest

from src.controller.compose import (
    DEFAULT_FPS,
    AudioSpec,
    ComposeError,
    ComposeSpec,
    ComposeWorker,
    any_session_busy_reason,
    camera_window,
    discover_streams,
    find_microphone,
    list_mic_folders,
    plan_regions,
    render_preview,
    resolve_date_dir,
    stream_fps,
    streams_info,
    suggest_fps,
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
# AudioSpec                                                                   #
# --------------------------------------------------------------------------- #


def test_audio_spec_defaults_to_none():
    assert AudioSpec.from_dict(None).mode == "none"
    assert AudioSpec.from_dict({}).mode == "none"


def test_audio_spec_valid_with_spectrogram():
    a = AudioSpec.from_dict({
        "mode": "panel", "source": "microphone_1",
        "spectrogram": {"color": "viridis", "fmin_hz": 15000, "fmax_hz": 96000},
    })
    assert a.mode == "panel"
    opts = a.spec_opts()
    assert opts.color == "viridis" and opts.fmax_hz == 96000


@pytest.mark.parametrize("bad", [
    {"mode": "waveform"},
    {"mode": "strip", "source": "../x"},
    {"mode": "strip", "spectrogram": {"color": "sparkle"}},
    {"mode": "strip", "spectrogram": {"fmin_hz": 90000, "fmax_hz": 20000}},
    {"mode": "strip", "spectrogram": {"nonsense": 1}},
])
def test_audio_spec_rejects_bad(bad):
    with pytest.raises(ComposeError):
        AudioSpec.from_dict(bad)


def test_compose_spec_embeds_audio():
    spec = ComposeSpec.from_dict(
        {"session_name": "s", "audio": {"mode": "track"}}
    )
    assert spec.audio["mode"] == "track"


# --------------------------------------------------------------------------- #
# render_preview                                                              #
# --------------------------------------------------------------------------- #


def test_render_preview_composites_one_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.controller.compose.probe_dimensions", lambda _p: (640, 480)
    )
    dd = tmp_path / "sess" / "20260805" / "camera_a"
    dd.mkdir(parents=True)
    (dd / "v.ts").write_bytes(b"x")
    (dd / "v_timestamps.csv").write_text("timestamp_ns\n1\n2\n")

    captured = {}

    def fake_preview(date_dir, out_png, **kw):
        captured.update(out=out_png, kw=kw)
        with open(out_png, "wb") as f:
            f.write(b"\x89PNG-preview")
        return out_png

    # video_compose pulls in OpenCV; stand in a fake module so this test
    # runs without it (render_preview imports it lazily).
    fake_vc = types.SimpleNamespace(compose_preview_frame=fake_preview)
    monkeypatch.setitem(sys.modules, "src.controller.video_compose", fake_vc)

    spec = ComposeSpec.from_dict({"session_name": "sess", "layout": "grid"})
    data = render_preview(str(tmp_path), spec, max_width=800)
    assert data == b"\x89PNG-preview"
    assert captured["kw"]["streams"] == ["camera_a"]
    assert not os.path.exists(captured["out"])  # temp preview cleaned up


# --------------------------------------------------------------------------- #
# fps derivation                                                              #
# --------------------------------------------------------------------------- #


def _ts_csv(path, rate_hz, n=50, t0=1_000_000_000):
    step = round(1e9 / rate_hz)
    rows = "\n".join(f"{i},{t0 + i * step}" for i in range(n))
    path.write_text(f"frame_id,timestamp_ns\n{rows}\n")


def test_stream_fps_from_median_gap(tmp_path):
    p = tmp_path / "a_timestamps.csv"
    _ts_csv(p, 30.0)
    assert stream_fps(str(p)) == pytest.approx(30.0, abs=0.1)


def test_stream_fps_none_when_too_short(tmp_path):
    p = tmp_path / "a_timestamps.csv"
    p.write_text("frame_id,timestamp_ns\n0,1\n1,2\n")
    assert stream_fps(str(p)) is None


def test_suggest_fps_takes_the_fastest_camera(tmp_path, monkeypatch):
    from src.controller.compose import SessionStream

    monkeypatch.setattr(
        "src.controller.compose.probe_dimensions", lambda _p: (640, 480)
    )
    slow, fast = tmp_path / "slow.csv", tmp_path / "fast.csv"
    _ts_csv(slow, 15.0)
    _ts_csv(fast, 48.7)
    streams = [
        SessionStream("slow", "s.ts", str(slow), 640, 480),
        SessionStream("fast", "f.ts", str(fast), 640, 480),
    ]
    assert suggest_fps(streams) == 49          # max, rounded
    assert suggest_fps(streams, hi=30) == 30   # clamped


def test_suggest_fps_falls_back_to_default(tmp_path):
    from src.controller.compose import SessionStream

    p = tmp_path / "short.csv"
    p.write_text("frame_id,timestamp_ns\n0,1\n")
    assert suggest_fps([SessionStream("x", "x.ts", str(p), 1, 1)]) == DEFAULT_FPS


def test_streams_info_reports_cameras_mics_and_fps(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.controller.compose.probe_dimensions", lambda _p: (1280, 720)
    )
    dd = tmp_path / "sess" / "20260901"
    (dd / "cam_a").mkdir(parents=True)
    (dd / "cam_a" / "v.ts").write_bytes(b"x")
    _ts_csv(dd / "cam_a" / "v_timestamps.csv", 25.0)
    (dd / "mic_a").mkdir()
    (dd / "mic_a" / "r.flac").write_bytes(b"x")
    (dd / "mic_a" / "r_timestamps.txt").write_text("STARTED 1.0\n")

    assert list_mic_folders(str(dd)) == ["mic_a"]
    info = streams_info(str(tmp_path), "sess", None)
    assert info["dates"] == ["20260901"]
    assert info["cameras"][0]["name"] == "cam_a"
    assert info["cameras"][0]["width"] == 1280
    assert info["mics"] == ["mic_a"]
    assert info["suggested_fps"] == 25


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


def test_camera_window_is_the_overlap(tmp_path):
    from src.controller.compose import SessionStream

    def _csv(p, first, last):
        p.write_text(f"frame_id,timestamp_ns\n0,{first}\n1,{last}\n")

    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    _csv(a, 1_000, 9_000)
    _csv(b, 2_000, 7_000)
    streams = [
        SessionStream("a", "a.ts", str(a), 640, 480),
        SessionStream("b", "b.ts", str(b), 640, 480),
    ]
    assert camera_window(streams) == (2_000, 7_000)


def test_camera_window_drops_prestage_rows(tmp_path):
    from src.controller.compose import SessionStream

    p = tmp_path / "a_timestamps.csv"
    # 3 pre-stage rows (0..2) with no video frame, then the real ones
    p.write_text("frame_id,timestamp_ns\n"
                 + "".join(f"{i},{1000 + i}\n" for i in range(3))
                 + "".join(f"{i},{5000 + i * 100}\n" for i in range(10)))
    s = SessionStream("a", "a.ts", str(p), 640, 480, csv_skip=3)
    start, end = camera_window([s])
    assert start == 5000               # first real frame, not the pre-stage 1000
    assert end == 5000 + 9 * 100


def test_prestage_skip_from_frame_count(monkeypatch):
    import src.controller.compose as c
    monkeypatch.setattr(c, "_video_frame_count", lambda _p: 100)
    assert c._prestage_skip("x.ts", 130) == 30     # 30 pre-stage rows
    assert c._prestage_skip("x.ts", 100) == 0
    assert c._prestage_skip("x.ts", 95) == 0       # never negative
    monkeypatch.setattr(c, "_video_frame_count", lambda _p: 0)
    assert c._prestage_skip("x.ts", 130) == 0      # unknowable -> don't guess


def test_video_frame_count_handles_duplicate_ffprobe_lines(monkeypatch):
    """ffprobe 7.x prints the MPEG-TS stream's csv row twice (once under
    programs[].streams[], once under the top-level streams[]), with a blank
    line between -- int()-ing the whole blob raises ValueError and used to
    silently collapse to 0, disabling the pre-stage-row skip entirely."""
    import src.controller.compose as c

    class _Result:
        stdout = "453\n\n453\n"

    monkeypatch.setattr(
        c.subprocess, "run", lambda *a, **kw: _Result()
    )
    assert c._video_frame_count("x.ts") == 453


def test_find_microphone_picks_first_or_named(tmp_path):
    dd = tmp_path / "20260805"
    for name in ("microphone_1", "microphone_2"):
        d = dd / name
        d.mkdir(parents=True)
        (d / "rec.flac").write_bytes(b"x")
        (d / "rec_timestamps.txt").write_text("STARTED 1.0\n")
    (dd / "microphone_3").mkdir()
    (dd / "microphone_3" / "rec.flac").write_bytes(b"x")  # no sidecar

    audio, sidecar = find_microphone(str(dd), None)
    assert "microphone_1" in audio and audio.endswith("rec.flac")
    assert sidecar.endswith("rec_timestamps.txt")
    audio2, _ = find_microphone(str(dd), "microphone_2")
    assert "microphone_2" in audio2
    with pytest.raises(ComposeError):
        find_microphone(str(dd), "microphone_3")  # sidecar-less
    with pytest.raises(ComposeError):
        find_microphone(str(dd), "microphone_9")


# --------------------------------------------------------------------------- #
# any_session_busy_reason                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class _FakeSession:
    state: str
    pending_exports: int = 0


def test_busy_reason_ignores_finished_sessions():
    # a stopped / errored session is finished -- it's exactly what compose
    # targets, so it must not block.
    assert any_session_busy_reason({"s1": _FakeSession("stopped")}) is None
    assert any_session_busy_reason({"s1": _FakeSession("error")}) is None
    assert any_session_busy_reason(
        {"a": _FakeSession("stopped"), "b": _FakeSession("stopped")}
    ) is None


def test_busy_reason_blocks_stopped_with_exports_in_flight():
    reason = any_session_busy_reason(
        {"s1": _FakeSession("stopped", pending_exports=3)}
    )
    assert reason and "in flight" in reason


def test_busy_reason_blocks_on_running_sessions():
    assert "s1" in any_session_busy_reason({"s1": _FakeSession("active")})
    assert "s1" in any_session_busy_reason({"s1": _FakeSession("paused")})
    assert any_session_busy_reason({"s1": {"state": "scheduled"}}) is not None
    assert any_session_busy_reason({"s2": {"state": "pending"}}) is not None


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
