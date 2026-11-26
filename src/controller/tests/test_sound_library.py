"""Tests for src/controller/sound_library.py (controller-side sound library)."""

import io
import json

import numpy as np
import pytest
import soundfile

from src.controller.sound_library import (
    SoundLibrary,
    SoundLibraryError,
    protocol_sound_files,
)


def _wav(secs=0.2, rate=48000, amp=0.25):
    t = np.arange(int(rate * secs)) / rate
    x = (amp * np.sin(2 * np.pi * 2000 * t) * np.hanning(len(t))).astype(np.float32)
    buf = io.BytesIO()
    soundfile.write(buf, x, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def lib(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "8k.wav").write_bytes(_wav())
    return SoundLibrary(str(tmp_path / "lib"), str(builtin), clock=lambda: 1000.0)


class TestUpload:
    def test_stage_then_add(self, lib, tmp_path):
        staged = lib.stage("tone.wav", _wav())
        assert staged["name"] == "tone.wav" and staged["replaces"] is False
        assert staged["analysis"]["peak_dbfs"] == pytest.approx(-12, abs=0.2)
        assert "envelope" in staged["analysis"]
        assert lib.library_names() == []           # nothing written until added
        entry = lib.add("tone.wav")
        assert entry["sha256"] == staged["analysis"]["sha256"]
        assert "envelope" not in entry["analysis"]  # list/summary form
        side = json.loads((tmp_path / "lib" / "tone.json").read_text())
        assert side["source"] == "upload" and "spectrogram" in side["analysis"]
        assert lib.manifest() == [{"name": "tone.wav", "sha256": entry["sha256"]}]
        assert lib.stage("tone.wav", _wav(amp=0.5))["replaces"] is True

    def test_add_without_upload_refused(self, lib):
        with pytest.raises(SoundLibraryError, match="upload it again"):
            lib.add("tone.wav")

    def test_discard(self, lib):
        lib.stage("tone.wav", _wav())
        lib.discard("tone.wav")
        with pytest.raises(SoundLibraryError):
            lib.add("tone.wav")

    @pytest.mark.parametrize("name", ["8k.wav", "8K.WAV", "x.mp3", "a b.wav", ""])
    def test_bad_or_builtin_names_refused(self, lib, name):
        with pytest.raises(SoundLibraryError):
            lib.stage(name, _wav())

    def test_not_a_wav_refused_with_reason(self, lib):
        with pytest.raises(SoundLibraryError, match="WAV"):
            lib.stage("x.wav", b"ID3" + b"\0" * 100)


class TestListing:
    def test_builtins_listed_read_only(self, lib):
        lib.stage("tone.wav", _wav())
        lib.add("tone.wav")
        items = {e["name"]: e for e in lib.list()}
        assert items["8k.wav"]["builtin"] is True and items["tone.wav"]["builtin"] is False
        assert items["8k.wav"]["analysis"]["sample_rate"] == 48000
        assert "envelope" in lib.get("8k.wav")["analysis"]
        assert lib.manifest() == [{"name": "tone.wav", "sha256": items["tone.wav"]["sha256"]}]
        with pytest.raises(SoundLibraryError, match="built in"):
            lib.delete("8k.wav")

    def test_file_copied_in_by_hand_gets_analysed(self, lib, tmp_path):
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "hand.wav").write_bytes(_wav())
        entry = lib.get("hand.wav")
        assert entry["source"] == "manual" and entry["sha256"]


class TestDelete:
    def test_delete_and_refuse_while_used(self, lib, tmp_path):
        lib.stage("tone.wav", _wav())
        lib.add("tone.wav")
        with pytest.raises(SoundLibraryError, match="Startle"):
            lib.delete("tone.wav", used_by=["Startle"])
        lib.delete("tone.wav")
        assert lib.library_names() == [] and not (tmp_path / "lib" / "tone.json").exists()
        with pytest.raises(SoundLibraryError):
            lib.delete("tone.wav")


def test_protocol_sound_files_finds_nested_play_steps():
    protocol = {"steps": [
        {"at_s": 0, "action": "play", "params": {"file": "a.wav"}},
        {"at_s": 1, "action": "repeat", "params": {"times": 3, "steps": [
            {"at_s": 0, "action": "play", "params": {"file": "b.wav", "output": 2}},
            {"at_s": 0, "action": "pulse", "params": {"pin": 17}}]}}]}
    assert protocol_sound_files(protocol) == {"a.wav", "b.wav"}


class TestSocketFlow:
    """upload chunks -> analysis -> add -> broadcast + push, through a real
    Flask-SocketIO test client."""

    def _setup(self, lib, protocols=None):
        from types import SimpleNamespace

        from flask import Flask
        from flask_socketio import SocketIO

        from src.controller.sound_library import register

        app = Flask(__name__)
        sio = SocketIO(app, async_mode="threading")
        web = SimpleNamespace(app=app, socketio=sio, _require_auth=lambda *a, **k: True)
        sent = []
        protos = protocols or SimpleNamespace(list=lambda: [], get=lambda n: None)
        push = register(web, lib, protocols=protos,
                        get_modules=lambda: {"sound_1": {"type": "sound", "online": True},
                                             "ttl_1": {"type": "ttl", "online": True}},
                        send_command=lambda mid, cmd, p: sent.append((mid, cmd, p)),
                        controller_url=lambda: "http://10.0.0.1:5000",
                        logger=__import__("logging").getLogger("t"))
        return app, sio, sent, push

    def _events(self, client, name):
        return [e["args"][0] for e in client.get_received() if e["name"] == name]

    def test_upload_analyse_add_push_and_serve(self, lib):
        app, sio, sent, _ = self._setup(lib)
        client = sio.test_client(app)
        data = _wav(secs=1.0)
        chunk = 4096
        chunks = [data[i:i + chunk] for i in range(0, len(data), chunk)]
        client.emit("upload_sound_start", {"filename": "tone.wav", "total_bytes": len(data),
                                           "total_chunks": len(chunks)})
        for i, c in enumerate(chunks):
            client.emit("upload_sound_chunk", {"index": i, "data": c})
        received = client.get_received()
        analysis = [e["args"][0] for e in received if e["name"] == "sound_upload_analysis"]
        assert len(analysis) == 1 and analysis[0]["name"] == "tone.wav"
        assert "spectrogram" in analysis[0]["analysis"]
        assert sent == []                                  # nothing pushed before add

        client.emit("add_sound", {"name": "tone.wav"})
        lists = self._events(client, "sound_library")
        assert lists and lists[-1]["added"] == "tone.wav"
        assert [(m, c) for m, c, _ in sent] == [("sound_1", "sync_sound_library")]
        params = sent[0][2]
        assert params["controller_url"] == "http://10.0.0.1:5000"
        assert params["files"] == [{"name": "tone.wav", "sha256": analysis[0]["analysis"]["sha256"]}]

        resp = app.test_client().get("/sound_library/tone.wav")
        assert resp.status_code == 200 and resp.data == data
        assert app.test_client().get("/sound_library/..%2Fx.wav").status_code == 404

    def test_errors_reach_the_caller(self, lib):
        from types import SimpleNamespace
        app, sio, _, _ = self._setup(lib, SimpleNamespace(
            list=lambda: [{"name": "Startle"}],
            get=lambda n: {"steps": [{"action": "play", "params": {"file": "tone.wav"}}]}))
        lib.stage("tone.wav", _wav())
        lib.add("tone.wav")
        client = sio.test_client(app)
        client.emit("upload_sound_start", {"filename": "8k.wav", "total_bytes": 10, "total_chunks": 1})
        client.emit("delete_sound", {"name": "tone.wav"})
        errors = [e["error"] for e in self._events(client, "sound_library_error")]
        assert any("built-in" in e for e in errors)
        assert any("Startle" in e for e in errors)


class TestGenerate:
    def test_generate_stage_add_records_parameters(self, lib, tmp_path):
        staged = lib.generate({"type": "noise", "colour": "pink", "duration_s": 0.5})
        assert staged["name"].startswith("noise_pink_") and staged["generator"]["seed"] >= 0
        assert staged["analysis"]["rms_dbfs"] == pytest.approx(-20, abs=0.1)
        entry = lib.add(staged["name"])
        assert entry["source"] == "generator" and entry["generator"]["colour"] == "pink"
        side = json.loads((tmp_path / "lib" / (staged["name"][:-4] + ".json")).read_text())
        assert side["generator"]["generator_version"] >= 1

    def test_bad_parameters_explained(self, lib):
        with pytest.raises(SoundLibraryError, match="freq_hz"):
            lib.generate({"type": "tone", "freq_hz": 40000})

    def test_name_rules_apply(self, lib):
        with pytest.raises(SoundLibraryError, match="File names"):
            lib.generate({"type": "tone"}, "has space")
        assert lib.generate({"type": "tone"}, "startle_tone")["name"] == "startle_tone.wav"


def test_generate_sound_over_socketio(lib):
    flow = TestSocketFlow()
    app, sio, sent, _ = flow._setup(lib)
    client = sio.test_client(app)
    client.emit("generate_sound", {"params": {"type": "noise", "colour": "brown",
                                              "low_hz": 500, "duration_s": 0.2, "seed": 9}})
    got = flow._events(client, "sound_upload_analysis")
    assert len(got) == 1 and got[0]["generator"]["seed"] == 9
    client.emit("add_sound", {"name": got[0]["name"]})
    assert sent and sent[0][1] == "sync_sound_library"
    client.emit("generate_sound", {"params": {"type": "tone", "freq_hz": 99999}})
    assert any("freq_hz" in e["error"] for e in flow._events(client, "sound_library_error"))
