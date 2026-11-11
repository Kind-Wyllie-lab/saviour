"""Ephys recording selection (ephys_data) and headstage accelerometer
processing (ephys_accel) on small synthetic Open Ephys recordings."""

import csv
import json
import os

import numpy as np
import pytest

from src.controller import ephys_accel, ephys_data

FS = 30000
BV_AUX = 3.74e-5
OFFSET_V = np.array([0.49, 0.50, 0.48])
V_PER_G = 0.34
CHANNELS = ["CH1", "CH2", "A1_AUX1", "A1_AUX2", "A1_AUX3", "ADC1"]


def _unit(pitch_deg, roll_deg):
    """Body-frame gravity direction ('up' as the accelerometer reads it)
    for a headstage at this pitch/roll (the module's own convention)."""
    p, r = np.radians(pitch_deg), np.radians(roll_deg)
    return np.array([np.sin(p), np.cos(p) * np.sin(r), np.cos(p) * np.cos(r)])


def make_recording(root, rel, start_ms, poses, seg_s=2.0, burst_at_s=None,
                   dips=True, ttl_times=None):
    """Write an Open Ephys binary recording whose AUX1-3 hold the
    accelerometer at each (pitch, roll) pose for seg_s seconds."""
    rec = os.path.join(root, rel)
    stream = os.path.join(rec, "continuous", "Acquisition_Board-100.acquisition_board")
    os.makedirs(stream)
    n_seg = int(seg_s * FS)
    n = n_seg * len(poses)
    rng = np.random.default_rng(0)
    g = np.concatenate([np.tile(_unit(*p), (n_seg, 1)) for p in poses])
    v = OFFSET_V + V_PER_G * g + rng.normal(0, 0.002, (n, 3))
    aux = np.round(v / BV_AUX).astype(np.int32)
    aux = np.repeat(aux[::4], 4, axis=0)[:n]  # AUX holds each value 4 samples
    if dips:  # ~1 ms below-0 V dips: must not move the 10 ms median
        for k in range(FS // 2, n, FS):
            aux[k:k + 30] = -2000
    if burst_at_s is not None:  # 0.2 s saturation burst: clipped / garbage
        a = int(burst_at_s * FS)
        aux[a:a + FS // 5: 2] = 32767
        aux[a + 1:a + FS // 5: 2] = -30000
    data = np.zeros((n, len(CHANNELS)), np.int16)
    data[:, 2:5] = np.clip(aux, -32768, 32767)
    data.tofile(os.path.join(stream, "continuous.dat"))
    first = 100_000
    np.save(os.path.join(stream, "sample_numbers.npy"), np.arange(first, first + n))
    np.save(os.path.join(stream, "timestamps.npy"), (first + np.arange(n)) / FS)
    oebin = {"continuous": [{
        "folder_name": "Acquisition_Board-100.acquisition_board/",
        "stream_name": "acquisition_board", "sample_rate": float(FS),
        "num_channels": len(CHANNELS),
        "channels": [{"channel_name": c,
                      "bit_volts": BV_AUX if "AUX" in c else 0.195,
                      "units": "mV" if "AUX" in c else "uV"} for c in CHANNELS],
    }]}
    with open(os.path.join(rec, "structure.oebin"), "w") as f:
        json.dump(oebin, f)
    with open(os.path.join(rec, "sync_messages.txt"), "w") as f:
        f.write(f"Software Time (milliseconds since midnight Jan 1st 1970 UTC): "
                f"{start_ms}\nStart Time for Acquisition Board (100) @ 30000 Hz: "
                f"{first}\n")
    ttl = os.path.join(rec, "events", "Acquisition_Board-100.acquisition_board", "TTL")
    os.makedirs(ttl)
    t = np.asarray(ttl_times if ttl_times is not None else [first / FS + 1.0])
    np.save(os.path.join(ttl, "timestamps.npy"), t)
    np.save(os.path.join(ttl, "states.npy"), np.ones(len(t), np.int16))
    return rec


def make_session(root, start_s, end_s, pins=((19, "pseudorandom"),)):
    d = os.path.join(root, "sess", "20260624", "ttl_x")
    os.makedirs(d)
    with open(os.path.join(d, "sess_ttl_x_(0_20260624-113853).csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp_nanoseconds", "pin_number", "pin_mode", "pin_state",
                    "pin_description"])
        for t in np.linspace(start_s, end_s, 20):
            for pin, mode in pins:
                w.writerow([int(t * 1e9), pin, mode, "TTLValue.HIGH", "None"])
    return os.path.join(root, "sess")


POSES = [(0, 0), (40, 0), (-40, 0), (0, 40), (0, -40), (60, 20), (0, 0)]


# ------------------------------------------------------- ephys_data ----

def test_pick_recording_by_overlap(tmp_path):
    root = str(tmp_path / "oe")
    make_recording(root, "exp1/recording1", 1_000_000, [(0, 0)] * 3)
    make_recording(root, "exp2/recording1", 1_100_000, [(0, 0)] * 3)
    recs = ephys_data.list_recordings(root)
    assert [r.rel for r in recs] == ["exp1/recording1", "exp2/recording1"]
    assert recs[0].summary()["aux_ports"] == ["A1"]
    rec, why = ephys_data.pick_recording(recs, (1_100.5, 1_105.0))
    assert rec.rel == "exp2/recording1"
    assert "overlaps" in why


def test_pick_recording_refuses_to_guess(tmp_path):
    root = str(tmp_path / "oe")
    make_recording(root, "a/recording1", 1_000_000, [(0, 0)] * 3)
    make_recording(root, "b/recording1", 1_000_000, [(0, 0)] * 3)
    recs = ephys_data.list_recordings(root)
    with pytest.raises(ephys_data.EphysSelectionError, match="about equally"):
        ephys_data.pick_recording(recs, (1_000.5, 1_004.0))
    with pytest.raises(ephys_data.EphysSelectionError, match="no recording overlaps"):
        ephys_data.pick_recording(recs, (5_000.0, 5_010.0))
    with pytest.raises(ephys_data.EphysSelectionError, match="no TTL log"):
        ephys_data.pick_recording(recs, None)


def test_saviour_span_and_sync_pin(tmp_path):
    sess = make_session(str(tmp_path), 1000.0, 1010.0,
                        pins=((19, "pseudorandom"), (5, "input")))
    assert ephys_data.saviour_span(sess) == pytest.approx((1000.0, 1010.0))
    csv_path = ephys_data.saviour_ttl_csvs(sess)[0]
    assert ephys_data.sync_pin(csv_path) == 19


def test_sync_pin_ambiguous(tmp_path):
    sess = make_session(str(tmp_path), 0, 1, pins=((1, "input"), (2, "input")))
    with pytest.raises(ephys_data.EphysSelectionError):
        ephys_data.sync_pin(ephys_data.saviour_ttl_csvs(sess)[0])


# ------------------------------------------------------ ephys_accel ----

def _process(tmp_path, poses, **kw):
    root = str(tmp_path / "oe")
    make_recording(root, "exp1/recording1", 1_000_000, poses, **kw)
    sess = make_session(str(tmp_path), 1000.5, 1010.0)
    s = ephys_accel.process(root, sess)
    out = os.path.join(sess, "_ephys", "accel", "A1")
    return s, out


def test_auto_calibration_and_tilt(tmp_path):
    s, out = _process(tmp_path, POSES, burst_at_s=4.9)
    cal = s["calibration"]
    assert cal["method"] == "auto"
    np.testing.assert_allclose(cal["offset_v"], OFFSET_V, atol=0.01)
    assert cal["v_per_g"] == pytest.approx(V_PER_G, abs=0.01)
    with open(os.path.join(out, "accel.csv")) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(POSES) * 200

    def at(t_s, col):
        return float(rows[int(t_s * 100)][col])

    # middle of each still pose, well away from the gravity filter's edges
    assert at(3.0, "pitch_deg") == pytest.approx(40, abs=1)
    assert at(7.0, "roll_deg") == pytest.approx(40, abs=1)
    assert at(9.0, "roll_deg") == pytest.approx(-40, abs=1)
    assert at(1.0, "az_g") == pytest.approx(1.0, abs=0.02)
    # 1 ms dips don't make bins invalid; the 0.2 s burst does, and is filled
    burst = [r for r in rows if r["invalid"] == "1"]
    assert 5 <= len(burst) <= 25
    assert all(abs(float(r["ax_g"])) < 1.5 for r in burst)
    assert not s["aligned"] and rows[0]["timestamp_ns"] == ""


def test_still_headstage_falls_back_to_default(tmp_path):
    s, _ = _process(tmp_path, [(0, 0)] * 5)
    assert s["calibration"]["method"] == "default"
    assert any("auto calibration not reliable" in w for w in s["warnings"])


def test_alignment_applies_only_to_matching_recording(tmp_path):
    root = str(tmp_path / "oe")
    make_recording(root, "exp1/recording1", 1_000_000, POSES)
    sess = make_session(str(tmp_path), 1000.5, 1010.0)
    os.makedirs(os.path.join(sess, "_ephys"))
    model = {"scale": 1.0001, "offset": 2.0, "sv_t0_s": 1000.5,
             "oe_recording": "exp1/recording1"}
    with open(os.path.join(sess, "_ephys", "model.json"), "w") as f:
        json.dump(model, f)
    s = ephys_accel.process(root, sess)
    assert s["aligned"]
    t_oe = 100_000 / FS + 0.005
    want = ((t_oe - 2.0) / 1.0001 + 1000.5) * 1e9
    assert s["start_ns"] == pytest.approx(want, abs=1e3)

    model["oe_recording"] = "other/recording1"
    with open(os.path.join(sess, "_ephys", "model.json"), "w") as f:
        json.dump(model, f)
    s = ephys_accel.process(root, sess, recording="exp1/recording1")
    assert not s["aligned"]
    assert any("fitted on other/recording1" in w for w in s["warnings"])


def test_plot_json_is_reduced(tmp_path):
    _s, out = _process(tmp_path, POSES)
    with open(os.path.join(out, "plot.json")) as f:
        plot = json.load(f)
    assert set(plot["series"]) == set(ephys_accel.SERIES)
    n = len(plot["t_s"])
    assert n <= ephys_accel.PLOT_BUCKETS
    for v in plot["series"].values():
        assert len(v["min"]) == len(v["max"]) == len(v["mean"]) == n
        assert all(a <= m + 1e-9 <= b + 2e-9
                   for a, m, b in zip(v["min"], v["mean"], v["max"], strict=True))


def test_no_aux_channels(tmp_path):
    root = str(tmp_path / "oe")
    rec = make_recording(root, "exp1/recording1", 1_000_000, [(0, 0)] * 3)
    with open(os.path.join(rec, "structure.oebin")) as f:
        oebin = json.load(f)
    for ch in oebin["continuous"][0]["channels"]:
        ch["channel_name"] = ch["channel_name"].replace("A1_AUX", "X")
    with open(os.path.join(rec, "structure.oebin"), "w") as f:
        json.dump(oebin, f)
    sess = make_session(str(tmp_path), 1000.5, 1002.0)
    with pytest.raises(ephys_accel.AccelError, match="no headstage AUX"):
        ephys_accel.process(root, sess)
