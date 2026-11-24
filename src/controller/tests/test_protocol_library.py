"""Tests for src/controller/protocol_library.py (named protocol store)."""

import json

import pytest

from src.controller.protocol_library import (
    MAX_NAME_LEN,
    ProtocolLibrary,
    ProtocolLibraryError,
    clean_name,
)

P = {"version": 1, "steps": [{"at_s": 0, "action": "pulse",
                              "params": {"pin": 17, "width_s": 0.1}}]}


def _lib(tmp_path, t=[1000.0]):  # noqa: B006 -- a shared clock the tests advance
    return ProtocolLibrary(str(tmp_path / "c" / "protocols.json"), clock=lambda: t[0])


class TestNames:
    def test_trims_and_collapses_spaces(self):
        assert clean_name("  tone   trials ") == "tone trials"

    @pytest.mark.parametrize("bad", ["", "   ", None, 3, "a/b", "a\\b", "a\x01b",
                                     "x" * (MAX_NAME_LEN + 1)])
    def test_refuses_bad_names(self, bad):
        with pytest.raises(ProtocolLibraryError):
            clean_name(bad)


class TestLibrary:
    def test_save_persists_with_the_name_inside(self, tmp_path):
        lib = _lib(tmp_path)
        assert lib.save(" Habituation ", P) == "Habituation"
        again = _lib(tmp_path)
        assert again.get("Habituation")["name"] == "Habituation"
        assert again.get("Habituation")["steps"] == P["steps"]
        raw = json.loads((tmp_path / "c" / "protocols.json").read_text())
        assert list(raw["protocols"]) == ["Habituation"]

    def test_list_sorted_case_insensitively(self, tmp_path):
        lib = _lib(tmp_path)
        for n in ["beta", "Alpha", "gamma"]:
            lib.save(n, P)
        assert [i["name"] for i in lib.list()] == ["Alpha", "beta", "gamma"]

    def test_clash_refused_unless_overwrite(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("A", P)
        with pytest.raises(ProtocolLibraryError, match="already exists"):
            lib.save("A", {**P, "seed": 2})
        lib.save("A", {**P, "seed": 2}, overwrite=True)
        assert lib.get("A")["seed"] == 2

    def test_saving_an_edit_under_its_own_name_is_not_a_clash(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("A", P)
        lib.save("A", {**P, "seed": 5}, previous_name="A")
        assert lib.get("A")["seed"] == 5

    def test_rename_keeps_created_at_and_drops_old(self, tmp_path):
        t = [1000.0]
        lib = _lib(tmp_path, t)
        lib.save("A", P)
        t[0] = 2000.0
        lib.save("B", P, previous_name="A")
        items = {i["name"]: i for i in lib.list()}
        assert set(items) == {"B"}
        assert items["B"]["created_at"] == 1000.0 and items["B"]["updated_at"] == 2000.0

    def test_rename_onto_an_existing_name_is_refused(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("A", P)
        lib.save("B", P)
        with pytest.raises(ProtocolLibraryError):
            lib.save("B", P, previous_name="A")
        assert {i["name"] for i in lib.list()} == {"A", "B"}

    def test_copy_numbers_until_free(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("A", P)
        assert lib.copy("A") == "A (copy)"
        assert lib.copy("A") == "A (copy 2)"
        assert lib.get("A (copy 2)")["name"] == "A (copy 2)"
        assert lib.copy("A", "Mine") == "Mine"
        with pytest.raises(ProtocolLibraryError):
            lib.copy("A", "Mine")
        with pytest.raises(ProtocolLibraryError):
            lib.copy("nope")

    def test_delete(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("A", P)
        lib.delete("A")
        assert lib.list() == [] and _lib(tmp_path).list() == []
        with pytest.raises(ProtocolLibraryError):
            lib.delete("A")

    def test_drafts_that_dont_validate_can_be_saved(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("draft", {"version": 1, "steps": [{"action": "pulse"}]})
        assert lib.get("draft")["steps"] == [{"action": "pulse"}]

    def test_non_object_protocol_refused(self, tmp_path):
        with pytest.raises(ProtocolLibraryError):
            _lib(tmp_path).save("A", ["not", "a", "dict"])

    def test_unreadable_file_starts_empty_and_is_left_alone(self, tmp_path):
        path = tmp_path / "c" / "protocols.json"
        path.parent.mkdir()
        path.write_text("{not json")
        assert _lib(tmp_path).list() == []
        assert path.read_text() == "{not json"

    def test_returned_protocols_are_copies(self, tmp_path):
        lib = _lib(tmp_path)
        lib.save("A", P)
        lib.get("A")["steps"].clear()
        lib.list()[0]["protocol"]["steps"].clear()
        assert len(lib.get("A")["steps"]) == 1


class TestCheckForSession:
    """check_for_session with sound steps (plans/session-protocol.md)."""

    TRIAL = {"version": 1, "seed": 4711, "steps": [
        {"at_s": 30, "action": "repeat", "times": 5, "every_s": 5, "jitter_s": [0, 20],
         "steps": [
             {"at_s": 0, "action": "pulse", "params": {"pins": [4, 6], "width_s": 2}},
             {"at_s": 0, "action": "play",
              "params": {"file": "tone_5k.wav", "duration_s": 2}}]}]}
    MODULES = {"ttl_1": {"type": "ttl"}, "sound_1": {"type": "sound"},
               "cam": {"type": "camera"}}

    @staticmethod
    def _configs(files=None, outputs=1):
        snd = {} if files is None else {
            "sound_hardware": {"files": files, "outputs": outputs, "channels": 2 * outputs}}
        return {"ttl_1": {"ttl": {"pins": {"4": {"mode": "scheduled"},
                                           "6": {"mode": "scheduled"}}}},
                "sound_1": snd}

    def _check(self, protocol=None, modules=None, configs=None):
        from src.controller.protocol_library import check_for_session
        return check_for_session(protocol or self.TRIAL, modules or self.MODULES,
                                 configs if configs is not None
                                 else self._configs({"tone_5k.wav": 3.0}))

    def test_ttl_and_sound_together(self):
        assert self._check() == ([], [])

    def test_needs_a_sound_module(self):
        errors, _ = self._check(modules={"ttl_1": {"type": "ttl"}})
        assert errors == ["The protocol plays sounds, but no sound module is in this "
                          "session's target"]

    def test_missing_file_names_the_module(self):
        errors, _ = self._check(configs=self._configs({"other.wav": 1.0}))
        assert errors and errors[0].startswith("sound_1: ") and "tone_5k.wav" in errors[0]

    def test_output_beyond_the_card(self):
        proto = {"version": 1, "steps": [
            {"at_s": 1, "action": "play", "params": {"file": "tone_5k.wav", "output": 3}}]}
        errors, _ = self._check(proto, configs=self._configs({"tone_5k.wav": 1.0}, 1))
        assert errors and "output 3" in errors[0]
        assert self._check(proto, configs=self._configs({"tone_5k.wav": 1.0}, 4)) == ([], [])

    def test_unreported_hardware_is_a_warning(self):
        errors, warnings = self._check(configs=self._configs(None))
        assert errors == [] and "hasn't reported" in warnings[0]

    def test_step_for_a_module_not_in_the_target(self):
        proto = {"version": 1, "steps": [
            {"at_s": 1, "module": "sound_9", "action": "play", "params": {"file": "a.wav"}}]}
        errors, _ = self._check(proto)
        assert errors == ["A step is for module sound_9, which isn't in this session's target"]

    def test_sound_only_protocol_needs_no_ttl(self):
        proto = {"version": 1, "steps": [
            {"at_s": 1, "action": "play", "params": {"file": "tone_5k.wav"}}]}
        assert self._check(proto, modules={"sound_1": {"type": "sound"}}) == ([], [])
