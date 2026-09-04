"""
Tests for src/controller/framesync_check.py

Synthetic `*_timestamps.csv` / `*_health_metadata_*.csv` files with known
injected phase / drift / dropped frames drive the pure check functions.
`SyncCheckWorker` is exercised against a synthetic session directory.
"""

import json
import os
import time
from datetime import UTC, datetime

import numpy as np
import pytest

from src.controller import framesync_check as fc

# --------------------------------------------------------------------------- #
# Synthetic file builders                                                     #
# --------------------------------------------------------------------------- #


def _write_camera(day_dir, name, *, fps=30.0, n=6000, t0_ns=1_000_000_000_000,
                  jitter_ns=0, drift_ppm=0.0, dropped_at=(), sync_mode="server",
                  segments=1, rng_seed=0):
    """One camera module folder: `<name>/<stem>_(k_utc)_timestamps.csv` per
    segment + `<name>/config.json`."""
    d = os.path.join(day_dir, name)
    os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(rng_seed)
    step = 1e9 / fps
    per_seg = max(1, n // segments)
    frame_id = 0
    dropped_set = set(dropped_at)
    for k in range(segments):
        rows = []
        for _ in range(per_seg):
            drift = t0_ns * 0 + (frame_id * step) * (drift_ppm / 1e6)
            ts = int(t0_ns + frame_id * step + drift
                     + (rng.normal(0, jitter_ns) if jitter_ns else 0))
            dropped_before = 3 if frame_id in dropped_set else 0
            rows.append((frame_id, ts, dropped_before, round(step / 1e6, 4)))
            frame_id += 1
        path = os.path.join(d, f"{name}_({k}_20260903-000000)_timestamps.csv")
        with open(path, "w", newline="") as f:
            f.write("frame_id,timestamp_ns,timestamp_utc,wall_mono_offset_s,"
                    "delta_ms,dropped_before,sync_lag_us,exposure_time_us,"
                    "analogue_gain,colour_gain_r,colour_gain_b\n")
            for fid, ts, db, dm in rows:
                f.write(f"{fid},{ts},x,0,{dm},{db},0,0,0,0,0\n")
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump({"camera": {"sync_mode": sync_mode, "fps": fps}}, f)
    return d


def _write_health(day_dir, name, *, ptp4l_ns=8_000, phc2sys_ns=8_000, n=200,
                  col="phc2sys_offset_ns", make_dir=True):
    d = os.path.join(day_dir, name)
    if make_dir:
        os.makedirs(d, exist_ok=True)
    rng = np.random.default_rng(1)
    path = os.path.join(d, f"{name}_health_metadata_(0_20260903-000000).csv")
    with open(path, "w", newline="") as f:
        f.write(f"timestamp,cpu_temp,ptp4l_offset_ns,ptp4l_freq,{col},phc2sys_freq\n")
        for i in range(n):
            p4 = rng.normal(0, ptp4l_ns)
            ph = rng.normal(0, phc2sys_ns)
            f.write(f"{i},50,{p4:.0f},0,{ph:.0f},0\n")
    return path


# --------------------------------------------------------------------------- #
# check_session_day -- happy paths                                            #
# --------------------------------------------------------------------------- #


def test_two_clean_synced_cameras_are_green(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", jitter_ns=200)
    _write_camera(str(dd), "camera_b", jitter_ns=200)
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    assert v["status"] == "green"
    assert v["phase_lock_evaluated"] is True
    assert v["sync_mode"] == "server"
    assert v["pairs"][0]["detrended_p95_us"] < 20
    assert abs(v["pairs"][0]["drift_us_per_sec"]) < 1
    assert v["counts"]["cameras"] == 2


def test_small_clock_drift_is_recovered_by_the_fit(tmp_path):
    # Drift small enough that nearest-neighbour matching stays stable across a
    # long recording, so the elapsed-seconds polyfit recovers the slope.
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", fps=30, n=18000)          # ~600 s
    _write_camera(str(dd), "camera_b", fps=30, n=18000, drift_ppm=16.0)  # 16 us/s
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    assert abs(v["pairs"][0]["drift_us_per_sec"]) == pytest.approx(16.0, abs=3.0)
    # 16 us/s over 600 s = 9.6 ms: past half of a 16.7 ms half-frame, under it -> amber
    assert v["status"] == "amber"
    assert any("drift" in r for r in v["reasons"])


def test_fixed_phase_offset_is_in_mean_not_detrended(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", t0_ns=1_000_000_000_000)
    _write_camera(str(dd), "camera_b", t0_ns=1_000_000_003_000)  # +3 us
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    pair = v["pairs"][0]
    assert abs(pair["mean_offset_us"]) == pytest.approx(3.0, abs=0.5)
    assert pair["detrended_p95_us"] < 5


# --------------------------------------------------------------------------- #
# PTP branch                                                                  #
# --------------------------------------------------------------------------- #


def test_ptp_between_gate_and_threshold_is_amber(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", jitter_ns=200)
    _write_camera(str(dd), "camera_b", jitter_ns=200)
    _write_health(str(dd), "camera_a", ptp4l_ns=80_000, phc2sys_ns=8_000)  # p95 ~130 us
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    assert v["status"] == "amber"
    assert any("ptp4l" in r and "camera_a" in r for r in v["reasons"])


def test_ptp_above_threshold_is_red(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", jitter_ns=200)
    _write_camera(str(dd), "camera_b", jitter_ns=200)
    _write_health(str(dd), "camera_a", ptp4l_ns=400_000, phc2sys_ns=8_000)
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    assert v["status"] == "red"


def test_stale_phc2sys_column_is_amber(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", jitter_ns=200)
    _write_camera(str(dd), "camera_b", jitter_ns=200)
    _write_health(str(dd), "camera_a", col="phc2sys_offset")
    _write_health(str(dd), "camera_b", col="phc2sys_offset")

    v = fc.check_session_day(str(dd))
    assert v["status"] == "amber"
    assert any("phc2sys_offset column" in r for r in v["reasons"])
    assert v["cameras"][0]["ptp"]["column_used"] == "phc2sys_offset"


# --------------------------------------------------------------------------- #
# sync_mode none / mixed / single camera / mic-only                           #
# --------------------------------------------------------------------------- #


def test_all_unsynced_skips_phase_lock(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", sync_mode="none")
    _write_camera(str(dd), "camera_b", sync_mode="none", jitter_ns=200)
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    assert v["phase_lock_evaluated"] is False
    assert v["pairs"] == []
    assert v["sync_mode"] == "none"
    assert v["status"] == "green"     # PTP + rate both fine


def test_unstable_rate_unsynced_camera_is_amber(tmp_path):
    dd = tmp_path / "20260903"
    # heavy jitter -> gap CV blows past framesync_rate_cv_amber
    _write_camera(str(dd), "camera_a", sync_mode="none", jitter_ns=4_000_000)
    _write_health(str(dd), "camera_a")

    v = fc.check_session_day(str(dd))
    assert v["status"] == "amber"
    assert any("capture rate" in r for r in v["reasons"])


def test_mixed_sync_modes_evaluate_only_the_synced(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", sync_mode="server", jitter_ns=200)
    _write_camera(str(dd), "camera_b", sync_mode="server", jitter_ns=200)
    _write_camera(str(dd), "camera_c", sync_mode="none", jitter_ns=200)
    for n in ("camera_a", "camera_b", "camera_c"):
        _write_health(str(dd), n)

    v = fc.check_session_day(str(dd))
    assert v["sync_mode"] == "mixed"
    assert v["phase_lock_evaluated"] is True
    assert v["counts"]["cameras_synced"] == 2
    assert {p["client"] for p in v["pairs"]} | {v["pairs"][0]["ref"]} == {"camera_a", "camera_b"}
    assert any(c["name"] == "camera_c" and c["ptp"] for c in v["cameras"])


def test_single_camera_is_not_skipped(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", jitter_ns=200)
    _write_health(str(dd), "camera_a")

    v = fc.check_session_day(str(dd))
    assert v["status"] == "green"
    assert v["phase_lock_evaluated"] is False
    assert v["pairs"] == []


def test_mic_only_day_is_skipped(tmp_path):
    dd = tmp_path / "20260903"
    md = dd / "microphone_1"
    md.mkdir(parents=True)
    (md / "rec.flac").write_bytes(b"x")
    (md / "rec_timestamps.txt").write_text("STARTED 1.0\n")
    _write_health(str(dd), "microphone_1", make_dir=False)

    v = fc.check_session_day(str(dd))
    assert v["status"] == "skipped"
    assert v["counts"]["cameras"] == 0
    assert len(v["health_modules"]) == 1


# --------------------------------------------------------------------------- #
# dropped frames                                                              #
# --------------------------------------------------------------------------- #


def test_dropped_frames_summed_and_classified(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", n=1000, dropped_at=range(0, 300))  # 30% -> red
    _write_camera(str(dd), "camera_b", n=1000)
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))
    cam_a = next(c for c in v["cameras"] if c["name"] == "camera_a")
    assert cam_a["dropped_frames"] == 300 * 3
    assert v["status"] == "red"
    assert any("dropped" in r for r in v["reasons"])


# --------------------------------------------------------------------------- #
# cap / stride                                                                #
# --------------------------------------------------------------------------- #


def test_cap_and_stride_keeps_drift_recoverable(tmp_path):
    dd = tmp_path / "20260903"
    _write_camera(str(dd), "camera_a", fps=120, n=120_000)
    _write_camera(str(dd), "camera_b", fps=120, n=120_000, drift_ppm=30.0)
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd), cap_rows=5_000)
    cam = next(c for c in v["cameras"] if c["name"] == "camera_a")
    assert cam["n_frames"] == 120_000
    assert 5_000 <= cam["n_sampled"] <= 10_000
    assert cam["stride"] > 1
    # drift still recovered from the elapsed-seconds fit despite decimation
    assert abs(v["pairs"][0]["drift_us_per_sec"]) == pytest.approx(30.0, abs=4.0)


def test_out_of_order_segments_are_sorted(tmp_path):
    dd = tmp_path / "20260903"
    d = dd / "camera_a"
    d.mkdir(parents=True)
    (d / "config.json").write_text('{"camera":{"sync_mode":"server","fps":30}}')
    hdr = ("frame_id,timestamp_ns,timestamp_utc,wall_mono_offset_s,delta_ms,"
           "dropped_before,sync_lag_us,exposure_time_us,analogue_gain,"
           "colour_gain_r,colour_gain_b\n")
    # segment (1_...) written with LATER timestamps than (0_...) but a
    # filename that sorts before it would break searchsorted without a sort.
    (d / "camera_a_(0_20260903-000000)_timestamps.csv").write_text(
        hdr + "".join(f"{i},{2_000_000_000_000 + i*33_000_000},x,0,33,0,0,0,0,0,0\n"
                      for i in range(100)))
    (d / "camera_a_(1_20260903-000000)_timestamps.csv").write_text(
        hdr + "".join(f"{i},{1_000_000_000_000 + i*33_000_000},x,0,33,0,0,0,0,0,0\n"
                      for i in range(100)))
    _write_camera(str(dd), "camera_b", n=200, t0_ns=1_000_000_000_000)
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    v = fc.check_session_day(str(dd))          # must not raise
    cam_a = next(c for c in v["cameras"] if c["name"] == "camera_a")
    assert cam_a["first_ns"] == 1_000_000_000_000


# --------------------------------------------------------------------------- #
# classify() boundaries                                                       #
# --------------------------------------------------------------------------- #


def _cam(name, *, sync="server", ptp4l=8_000, phc=8_000, dropped=0, n=1000,
         cv=0.0, fps_dev=0.0):
    ptp = fc.PtpSummary(module=name, n=100, ptp4l_p95_ns=ptp4l, phc2sys_p95_ns=phc,
                        ptp4l_p50_ns=ptp4l / 2, phc2sys_p50_ns=phc / 2,
                        ptp4l_max_ns=ptp4l * 2, phc2sys_max_ns=phc * 2)
    return fc.CameraSync(
        name=name, sync_mode=sync, nominal_fps=30.0, real_fps=30.0,
        n_frames=n, n_sampled=n, stride=1, dropped_frames=dropped,
        rate_cv=cv, fps_dev=fps_dev, first_ns=0, last_ns=int(1e9 * 100),
        ts_ns=np.array([], dtype=np.int64), ptp=ptp, notes=[],
    )


def _pair(detrended, drift=0.0, n=1000, art=0):
    return fc.PairOffset(ref="camera_a", client="camera_b", n_frames=n,
                         n_artefacts=art, detrended_p95_us=detrended,
                         drift_us_per_sec=drift, p95_offset_us=detrended)


def test_classify_green_amber_red_by_detrended_p95():
    thr = dict(fc.DEFAULT_THRESHOLDS)
    cams = [_cam("camera_a"), _cam("camera_b")]
    span_s, fps = 100.0, 30.0
    assert fc.classify(cams, [_pair(10)], thr, True, span_s, fps)[0] == "green"
    assert fc.classify(cams, [_pair(120)], thr, True, span_s, fps)[0] == "amber"
    assert fc.classify(cams, [_pair(500)], thr, True, span_s, fps)[0] == "red"


def test_classify_ptp_boundaries():
    thr = dict(fc.DEFAULT_THRESHOLDS)
    assert fc.classify([_cam("c", ptp4l=30_000)], [], thr, False, 0, 30)[0] == "green"
    assert fc.classify([_cam("c", ptp4l=120_000)], [], thr, False, 0, 30)[0] == "amber"
    assert fc.classify([_cam("c", ptp4l=300_000)], [], thr, False, 0, 30)[0] == "red"


def test_classify_missing_health_is_amber():
    cam = _cam("c")
    cam.ptp = None
    assert fc.classify([cam], [], dict(fc.DEFAULT_THRESHOLDS), False, 0, 30)[0] == "amber"


# --------------------------------------------------------------------------- #
# day enumeration / completeness                                              #
# --------------------------------------------------------------------------- #


def test_enumerate_day_dirs_filters(tmp_path):
    for name in ("20260901", "20260902", "_recovered", ".hidden", "notadate"):
        (tmp_path / name).mkdir()
    (tmp_path / "session_events.log").write_text("x")
    (tmp_path / "20260903").write_text("x")   # a *file* named like a date
    assert fc.enumerate_day_dirs(str(tmp_path)) == ["20260901", "20260902"]


def test_day_has_cameras(tmp_path):
    dd = tmp_path / "20260901"
    (dd / "microphone_1").mkdir(parents=True)
    assert fc.day_has_cameras(str(tmp_path), "20260901") is False
    cam = dd / "camera_a"
    cam.mkdir()
    (cam / "x_timestamps.csv").write_text("frame_id,timestamp_ns\n0,1\n")
    assert fc.day_has_cameras(str(tmp_path), "20260901") is True


def test_day_is_complete_truth_table(tmp_path):
    now = datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC)
    sd = str(tmp_path)
    assert fc.day_is_complete(sd, "20260904", 0, now) is False            # today
    assert fc.day_is_complete(sd, "20260903", 0, now) is True             # past + settled + no pending
    early = datetime(2026, 9, 4, 0, 30, 0, tzinfo=UTC)
    assert fc.day_is_complete(sd, "20260903", 0, early) is False          # not settled
    assert fc.day_is_complete(sd, "20260903", 5, now) is False            # pending, <2 days old
    assert fc.day_is_complete(sd, "20260901", 5, now) is True             # pending but >=2 days old


# --------------------------------------------------------------------------- #
# write_report / slim                                                        #
# --------------------------------------------------------------------------- #


def test_write_report_is_atomic_and_valid(tmp_path):
    v = {"schema": 1, "status": "green", "worst": {"x": np.float64(1.5)}}
    path = fc.write_report(str(tmp_path / "20260903"), v)
    assert path and os.path.basename(path) == "framesync_report.json"
    with open(path) as f:
        loaded = json.load(f)
    assert loaded["status"] == "green"
    assert loaded["worst"]["x"] == 1.5
    assert not any(p.endswith(".tmp") for p in os.listdir(os.path.dirname(path)))


def test_slim_drops_arrays():
    full = {"schema": 1, "status": "amber", "scope": "day", "date_dir": "20260903",
            "generated_at": "x", "sync_mode": "server", "phase_lock_evaluated": True,
            "reasons": list("abcdefg"), "worst": {"a": 1}, "counts": {"cameras": 4},
            "cameras": [{"big": "obj"}], "pairs": [{"big": "obj"}],
            "thresholds_used": {"x": 1}, "report_rel": "s/20260903/framesync_report.json"}
    s = fc.slim(full)
    assert "cameras" not in s and "pairs" not in s and "thresholds_used" not in s
    assert s["reasons"] == list("abcde")
    assert s["report_rel"].endswith("framesync_report.json")


# --------------------------------------------------------------------------- #
# check_session roll-up                                                       #
# --------------------------------------------------------------------------- #


def test_check_session_rolls_up_worst_of_days(tmp_path):
    sd = tmp_path / "sess"
    # day 1 clean; day 2 has a camera dropping 30% of frames -> red
    for day, drop in (("20260901", ()), ("20260902", range(0, 300))):
        dd = sd / day
        _write_camera(str(dd), "camera_a", n=1000, dropped_at=drop)
        _write_camera(str(dd), "camera_b", n=1000)
        _write_health(str(dd), "camera_a")
        _write_health(str(dd), "camera_b")

    v = fc.check_session(str(sd))
    assert v["scope"] == "session"
    assert set(v["days"]) == {"20260901", "20260902"}
    assert v["days"]["20260901"]["status"] == "green"
    assert v["days"]["20260902"]["status"] == "red"
    assert v["status"] == "red"                       # worst-of
    assert v["counts"]["green_days"] == 1


def test_check_session_no_date_dir_is_skipped(tmp_path):
    (tmp_path / "sess").mkdir()
    v = fc.check_session(str(tmp_path / "sess"))
    assert v["status"] == "skipped"
    assert v["days"] == {}


# --------------------------------------------------------------------------- #
# SyncCheckWorker                                                             #
# --------------------------------------------------------------------------- #


def _wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_worker_runs_day_check_and_writes_report(tmp_path):
    share = tmp_path
    dd = share / "mysess" / "20260903"
    _write_camera(str(dd), "camera_a", jitter_ns=200)
    _write_camera(str(dd), "camera_b", jitter_ns=200)
    _write_health(str(dd), "camera_a")
    _write_health(str(dd), "camera_b")

    results = []
    w = fc.SyncCheckWorker(
        share_path=str(share),
        thresholds_provider=lambda: dict(fc.DEFAULT_THRESHOLDS),
        on_result=lambda *a: results.append(a),
    )
    job = w.submit({"session_name": "mysess", "scope": "day", "date_dir": "20260903"})
    assert _wait_for(lambda: results)
    name, scope, date_dir, verdict, report_rel = results[0]
    assert (name, scope, date_dir) == ("mysess", "day", "20260903")
    assert verdict["status"] == "green"
    assert report_rel == os.path.join("mysess", "20260903", "framesync_report.json")
    assert os.path.isfile(str(dd / "framesync_report.json"))
    assert w.list()[0]["state"] == "done"


def test_worker_survives_a_corrupt_csv(tmp_path):
    share = tmp_path
    dd = share / "mysess" / "20260903"
    d = dd / "camera_a"
    d.mkdir(parents=True)
    (d / "config.json").write_text('{"camera":{"sync_mode":"server","fps":30}}')
    (d / "camera_a_(0_x)_timestamps.csv").write_text(
        "frame_id,timestamp_ns,delta_ms,dropped_before\n"
        "0,notanumber,33,0\n1,also bad,33,0\n")

    results = []
    w = fc.SyncCheckWorker(share_path=str(share),
                           on_result=lambda *a: results.append(a))
    w.submit({"session_name": "mysess", "scope": "day", "date_dir": "20260903"})
    assert _wait_for(lambda: results)
    # a camera folder with only unparseable rows -> no usable frames -> skipped,
    # not a crash; the worker thread is still alive for the next job.
    verdict = results[0][3]
    assert verdict["status"] in ("skipped", "amber", "error")

    dd2 = share / "mysess" / "20260904"
    _write_camera(str(dd2), "camera_a", jitter_ns=200)
    _write_camera(str(dd2), "camera_b", jitter_ns=200)
    _write_health(str(dd2), "camera_a")
    _write_health(str(dd2), "camera_b")
    w.submit({"session_name": "mysess", "scope": "day", "date_dir": "20260904"})
    assert _wait_for(lambda: len(results) == 2)
    assert results[1][3]["status"] == "green"


def test_worker_rejects_bad_input(tmp_path):
    w = fc.SyncCheckWorker(share_path=str(tmp_path))
    with pytest.raises(fc.SyncCheckError):
        w.submit({"session_name": "has space", "scope": "session"})
    with pytest.raises(fc.SyncCheckError):
        w.submit({"session_name": "ok", "scope": "day", "date_dir": "../x"})
    with pytest.raises(fc.SyncCheckError):
        w.submit({"session_name": "ok", "scope": "day"})   # no date_dir


def test_worker_dedupes_in_flight_jobs(tmp_path):
    share = tmp_path
    (share / "s" / "20260903" / "camera_a").mkdir(parents=True)
    w = fc.SyncCheckWorker(share_path=str(share), on_result=lambda *a: None)
    a = w.submit({"session_name": "s", "scope": "day", "date_dir": "20260903"})
    b = w.submit({"session_name": "s", "scope": "day", "date_dir": "20260903"})
    assert a.id == b.id
