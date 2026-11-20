"""NWB export (plans/nwb-export.md): nwb_convert's pure helpers, the
NwbJobs guards, and -- when an NWB venv is installed (SAVIOUR_NWB_ENV or
<repo>/env-nwb) -- a real conversion read back with pynwb."""

import json
import os
import shutil
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile

from src.controller import nwb_convert, nwb_export

T0 = 1_790_850_290  # 2026-10-01T10:24:50Z
FRAME_NUM = 4096


def _write_session(root: Path, *, with_video: bool = True) -> Path:
    sess = root / "sess"
    day = sess / "20261001"
    for d in ("cam1", "mic1", "ttl1", "apa1"):
        (day / d).mkdir(parents=True)
    cols = ("frame_id,timestamp_ns,timestamp_utc,wall_mono_offset_s,delta_ms,"
            "dropped_before,sync_lag_us,exposure_time_us,analogue_gain,colour_gain_r,"
            "colour_gain_b,det_cx,det_cy,in_zone,det_conf,det_frame_id")
    stem = "sess_cam1_(1_20261001-102450Z)"
    with open(day / "cam1" / f"{stem}_timestamps.csv", "w") as f:
        f.write(cols + "\n")
        for i in range(12):
            f.write(f"{i},{T0 * 10**9 + i * 100_000_000},x,0,100,0,0,1,1,1,1,"
                    f"{10 + i},{20 + i},{int(i > 5)},0.9,{i}\n")
    if with_video:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
             "testsrc=size=64x48:rate=10", "-frames:v", "10", "-c:v", "libx264",
             "-f", "mpegts", str(day / "cam1" / f"{stem}.ts")], check=True)
    # 3 blocks of mono 16-bit audio at 48 kHz, its sidecar
    rate = 48_000
    audio = (np.sin(np.arange(3 * FRAME_NUM) / 10) * 8000).astype(np.int16)
    mstem = "sess_mic1_AM01_(1_20261001-102450Z)"
    soundfile.write(day / "mic1" / f"{mstem}.flac", audio, rate, subtype="PCM_16")
    with open(day / "mic1" / f"{mstem}_timestamps.txt", "w") as f:
        f.write(f"STARTED {T0 + 2:.6f}\n")
        for k in range(3):
            f.write(f"{T0 + 2 + k * FRAME_NUM / rate:.6f}\n")
            if k == 0:
                f.write(f"FIRST_RECORD_SAMPLES {FRAME_NUM}\n")
    with open(day / "ttl1" / "sess_ttl1_(1_20261001-102450Z).csv", "w") as f:
        f.write("Timestamp_nanoseconds,pin_number,pin_mode,pin_state,pin_description,"
                "pin_role\n")
        f.write(f"{(T0 + 1) * 10**9},17,output,TTLValue.HIGH,sync,sync\n")
        f.write(f"{(T0 + 1) * 10**9 + 10**7},17,output,TTLValue.LOW,sync,sync\n")
        f.write(f"{(T0 + 3) * 10**9},22,input,1,lever,\n")
    with open(day / "apa1" / "sess_apa1_shock_events.csv", "w") as f:
        f.write("Timestamp_nanoseconds, event, rotation speed (rpm)\n")
        f.write(f"{(T0 + 4) * 10**9},SENDING_SHOCK,1.0\n")
    with open(sess / "markers.csv", "w") as f:
        f.write("recv_wall_ns,recv_iso,label,source,client_wall_ns\n")
        f.write(f"{(T0 + 5) * 10**9},x,rat placed,ui,\n")
    (sess / "session_metadata.json").write_text(json.dumps({
        "session_name": "sess", "created_at": "2026-10-01T10:24:40+00:00",
        "experimenter": "A. Tester", "rat_id": "R7", "strain": "Lister Hooded"}))
    return sess


# --------------------------------------------------------------------------- #
# Pure helpers                                                                #
# --------------------------------------------------------------------------- #

def test_inspect_session_lists_every_stream(tmp_path):
    info = nwb_convert.inspect_session(str(_write_session(tmp_path)))
    assert info["days"] == ["20261001"]
    assert info["cameras"] == [{"module": "cam1", "segments": 1,
                                "bytes": info["cameras"][0]["bytes"], "tracking": True}]
    assert [a["label"] for a in info["audio"]] and info["audio"][0]["rate"] == 48_000
    assert info["audio_pcm_bytes"] == 3 * FRAME_NUM * 2
    assert {(e["module"], e["kind"], e["rows"]) for e in info["events"]} == {
        ("ttl1", "ttl", 3), ("apa1", "apa_shocks", 1)}
    assert info["markers"] == 1


def test_clean_options_defaults_species_and_checks_fields():
    o = nwb_convert.clean_options({}, "sess")
    assert o["species"] == "Rattus norvegicus"
    assert o["sex"] == "U" and o["session_description"] == "sess"
    assert all(o["include"].values())
    o = nwb_convert.clean_options({"sex": "f", "age": "p12w", "include": {"audio": False}},
                                  "sess")
    assert (o["sex"], o["age"], o["include"]["audio"], o["include"]["video"]) == \
        ("F", "P12W", False, True)
    for bad in ({"sex": "X"}, {"age": "90 days"}, {"species": "x" * 500}):
        with pytest.raises(ValueError):
            nwb_convert.clean_options(bad, "sess")


def test_resolve_start_prefers_the_session_record():
    md = {"created_at": "2026-10-01T10:24:40+00:00"}
    assert nwb_convert.resolve_start({"session_start": "20261001-102445Z"}, md) == \
        datetime(2026, 10, 1, 10, 24, 45, tzinfo=UTC)
    # legacy, zone-less (BST): an hour ahead of UTC
    assert nwb_convert.resolve_start({"session_start": "20261001-112445"}, md) == \
        datetime(2026, 10, 1, 10, 24, 45, tzinfo=UTC)
    assert nwb_convert.resolve_start({}, md) == datetime(2026, 10, 1, 10, 24, 40, tzinfo=UTC)
    assert nwb_convert.resolve_start({}, {}) is None


def test_read_ttl_csv_accepts_enum_and_numeric_states(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(textwrap.dedent("""\
        Timestamp_nanoseconds,pin_number,pin_mode,pin_state,pin_description,pin_role
        10,17,output,TTLValue.HIGH,sync,sync
        20,17,output,TTLValue.LOW,sync,sync
        30,22,input,1,lever,
        40,22,input,0,lever,
        """))
    pins = nwb_convert.read_ttl_csv(str(p))
    assert pins[17]["v"] == [1, -1] and pins[17]["role"] == "sync"
    assert pins[22]["t"] == [30, 40] and pins[22]["v"] == [1, -1]


def test_nwb_name_strips_path_characters():
    assert nwb_convert.nwb_name("audio", "a/b:c d") == "audio_a_b_c_d"


# --------------------------------------------------------------------------- #
# NwbJobs guards (no venv needed)                                             #
# --------------------------------------------------------------------------- #

def test_session_dir_rejects_traversal_and_missing(tmp_path):
    jobs = nwb_export.NwbJobs(str(tmp_path), env=tmp_path / "no-env")
    for bad in ("../etc", "a/b", "", "missing"):
        with pytest.raises(nwb_export.NwbError):
            jobs.session_dir(bad)


def test_export_needs_the_package(tmp_path):
    _write_session(tmp_path, with_video=False)
    jobs = nwb_export.NwbJobs(str(tmp_path), env=tmp_path / "no-env")
    assert nwb_export.status(tmp_path / "no-env")["installed"] is False
    with pytest.raises(nwb_export.NwbError, match="Install"):
        jobs.start_export("sess", {})


def test_session_info_reports_an_existing_file(tmp_path):
    sess = _write_session(tmp_path, with_video=False)
    jobs = nwb_export.NwbJobs(str(tmp_path), env=tmp_path / "no-env")
    assert jobs.session_info("sess")["existing"] is None
    (sess / "sess.nwb").write_bytes(b"x" * 10)
    assert jobs.session_info("sess")["existing"]["size_bytes"] == 10


def test_only_one_job_at_a_time(tmp_path):
    jobs = nwb_export.NwbJobs(str(tmp_path), env=tmp_path / "no-env")
    jobs._new_job("install")
    with pytest.raises(nwb_export.NwbError, match="already running"):
        jobs._new_job("export")


# --------------------------------------------------------------------------- #
# A real conversion (only where the NWB venv is installed)                    #
# --------------------------------------------------------------------------- #

_ENV = Path(os.environ.get("SAVIOUR_NWB_ENV", nwb_export.REPO / "env-nwb"))
needs_nwb = pytest.mark.skipif(
    not nwb_export.env_python(_ENV).exists() or shutil.which("ffmpeg") is None,
    reason="NWB venv (env-nwb) or ffmpeg not installed")


@needs_nwb
def test_conversion_round_trip(tmp_path):
    sess = _write_session(tmp_path)
    out = sess / "sess.nwb"
    opts = {"session_start": "20261001-102440Z", "sex": "M", "age": "P90D"}
    r = subprocess.run(
        [str(nwb_export.env_python(_ENV)), "-m", "src.controller.nwb_convert", str(sess),
         "--options-json", json.dumps(opts), "--json-progress"],
        cwd=nwb_export.REPO, capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": str(nwb_export.REPO)})
    assert r.returncode == 0, r.stdout + r.stderr
    done = json.loads(r.stdout.strip().splitlines()[-1])
    assert done["validation"] == {"ran": True, "errors": []}
    assert done["summary"] == {"video": 1, "tracking": 1, "audio": 1, "ttl": 2,
                               "events": 1, "markers": 1}

    check = textwrap.dedent(f"""
        import json
        from pynwb import NWBHDF5IO
        with NWBHDF5IO({str(out)!r}, "r") as io:
            f = io.read()
            v = f.acquisition["video_cam1_1"]
            a = [x for k, x in f.acquisition.items() if k.startswith("audio")][0]
            print(json.dumps({{
                "start": f.session_start_time.isoformat(),
                "species": f.subject.species, "subject": f.subject.subject_id,
                "video_file": list(v.external_file[:]), "n_ts": len(v.timestamps),
                "ts0": float(v.timestamps[0]),
                "audio_start": float(a.starting_time), "audio_n": int(a.data.shape[0]),
                "ttl17": [int(x) for x in f.acquisition["ttl_ttl1_pin17"].data[:]],
                "marker": f.events["markers"].to_dataframe()["annotation"].tolist(),
            }}))
        """)
    r = subprocess.run([str(nwb_export.env_python(_ENV)), "-c", check],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["start"] == "2026-10-01T10:24:40+00:00"
    assert (got["species"], got["subject"]) == ("Rattus norvegicus", "R7")
    assert got["video_file"] == ["20261001/cam1/sess_cam1_(1_20261001-102450Z).ts"]
    # 12 CSV rows, 10 frames: the 2 pre-stage rows are dropped
    assert got["n_ts"] == 10 and got["ts0"] == pytest.approx(10.2)
    assert got["audio_start"] == pytest.approx(12.0, abs=0.01)
    assert got["audio_n"] == 3 * FRAME_NUM
    assert got["ttl17"] == [1, -1]
    assert got["marker"] == ["rat placed [ui]"]


# --------------------------------------------------------------------------- #
# Socket handlers                                                             #
# --------------------------------------------------------------------------- #

def _socket_client(tmp_path, admin: bool):
    import logging

    from flask import Flask
    from flask_socketio import SocketIO

    app = Flask(__name__)
    sio = SocketIO(app, async_mode="threading")
    jobs = nwb_export.NwbJobs(str(tmp_path), env=tmp_path / "no-env")
    nwb_export.register_socket_handlers(
        sio, jobs, is_admin=lambda: admin,
        get_session=lambda name: {"start_time": "20261001-102440Z", "state": "stopped"},
        logger=logging.getLogger("test"))
    return sio.test_client(app)


def _received(client, name):
    return [m["args"][0] for m in client.get_received() if m["name"] == name]


def test_status_reply_carries_session_streams_and_metadata(tmp_path):
    _write_session(tmp_path, with_video=False)
    client = _socket_client(tmp_path, admin=False)
    client.emit("get_nwb_status", {"session_name": "sess"})
    (reply,) = _received(client, "nwb_status")
    assert reply["installed"] is False and reply["default_species"] == "Rattus norvegicus"
    assert reply["session"]["audio"] and reply["metadata"]["rat_id"] == "R7"
    assert reply["record"]["start_time"] == "20261001-102440Z"


def test_install_needs_admin_and_export_needs_the_package(tmp_path):
    _write_session(tmp_path, with_video=False)
    client = _socket_client(tmp_path, admin=False)
    client.emit("install_nwb_package", {})
    client.emit("start_nwb_export", {"session_name": "sess", "options": {}})
    errors = [e["error"] for e in _received(client, "nwb_error")]
    assert "admin" in errors[0] and "Install" in errors[1]


def test_status_for_an_unknown_session_says_so(tmp_path):
    client = _socket_client(tmp_path, admin=True)
    client.emit("get_nwb_status", {"session_name": "nope"})
    (reply,) = _received(client, "nwb_status")
    assert reply["session"] is None and "No exported data" in reply["session_error"]
