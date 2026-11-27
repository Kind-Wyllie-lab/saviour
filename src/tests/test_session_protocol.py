"""Tests for src/shared/session_protocol.py (plans/session-protocol.md, phase 1)."""

import itertools

import pytest

from src.shared.session_protocol import (
    MAX_EDGES,
    Mulberry32,
    expand,
    state_at,
)


def _p(*steps, **top):
    return {"version": 1, "steps": list(steps), **top}


def _edges(exp):
    return [(round(e.t_s, 6), e.pin, e.level) for e in exp.edges]


class TestExpansion:
    def test_set_pulse_train(self):
        exp = expand(_p(
            {"at_s": 0, "action": "set", "params": {"pin": 17, "level": "high"}},
            {"at_s": 10, "action": "set", "params": {"pin": 17, "level": "low"}},
            {"at_s": 20, "action": "pulse", "params": {"pin": 22, "width_s": 0.05}},
            {"at_s": 30, "action": "train",
             "params": {"pin": 22, "rate_hz": 10, "width_s": 0.02, "count": 3}},
        ))
        assert exp.ok, exp.errors
        assert _edges(exp) == [
            (0, 17, "high"), (10, 17, "low"),
            (20, 22, "high"), (20.05, 22, "low"),
            (30, 22, "high"), (30.02, 22, "low"),
            (30.1, 22, "high"), (30.12, 22, "low"),
            (30.2, 22, "high"), (30.22, 22, "low"),
        ]
        assert exp.duration_s == pytest.approx(30.22)
        assert exp.pins == [17, 22]
        assert exp.end_state == "all_inactive"

    def test_steps_sorted_regardless_of_listing_order(self):
        exp = expand(_p(
            {"at_s": 5, "action": "pulse", "params": {"pin": 1, "width_s": 0.1}},
            {"at_s": 1, "action": "pulse", "params": {"pin": 1, "width_s": 0.1}},
        ))
        assert exp.ok
        assert [e.t_s for e in exp.edges] == pytest.approx([1, 1.1, 5, 5.1])

    def test_repeat_without_jitter(self):
        exp = expand(_p({"at_s": 60, "action": "repeat", "times": 3, "every_s": 2,
                         "steps": [{"at_s": 0.5, "action": "pulse",
                                    "params": {"pin": 4, "width_s": 0.1}}]}))
        assert exp.ok
        assert [e.t_s for e in exp.edges if e.level == "high"] == \
            pytest.approx([60.5, 62.5, 64.5])
        assert [e.step for e in exp.edges if e.level == "high"] == \
            ["0.0.0", "0.1.0", "0.2.0"]

    def test_jitter_is_seeded_and_in_range(self):
        proto = _p({"at_s": 0, "action": "repeat", "times": 20, "every_s": 2,
                    "jitter_s": [0, 3],
                    "steps": [{"at_s": 0, "action": "pulse",
                               "params": {"pin": 4, "width_s": 0.05}}]},
                   seed=1234)
        a, b = expand(proto), expand(proto)
        assert a.ok and _edges(a) == _edges(b)
        onsets = [e.t_s for e in a.edges if e.level == "high"]
        gaps = [y - x for x, y in itertools.pairwise(onsets)]
        assert onsets[0] == 0
        assert all(2 <= g <= 5 for g in gaps)
        assert len({round(g, 6) for g in gaps}) > 10  # actually random
        other = expand({**proto, "seed": 99})
        assert _edges(other) != _edges(a)

    def test_mulberry32_matches_reference(self):
        # Reference values from the JavaScript mulberry32 (seed 1234), which
        # the frontend twin must reproduce (checked with node, 2026-11-16).
        r = Mulberry32(1234)
        got = [round(r.random(), 10) for _ in range(3)]
        js = [0.0732949781, 0.7034119898, 0.9028560191]
        assert got == js

    def test_module_filter(self):
        exp = expand(_p(
            {"at_s": 0, "module": "ttl_a", "action": "pulse",
             "params": {"pin": 1, "width_s": 0.1}},
            {"at_s": 1, "module": "ttl_b", "action": "pulse",
             "params": {"pin": 1, "width_s": 0.1}},
            {"at_s": 2, "action": "pulse", "params": {"pin": 1, "width_s": 0.1}},
        ), module_id="ttl_a")
        assert [e.t_s for e in exp.edges if e.level == "high"] == [0, 2]

    def test_modules_draw_the_same_random_gaps(self):
        # Block 0 is only for ttl_b; ttl_a must still consume its draws, or
        # block 1's shared trials land at different times on the two modules.
        def block(**extra):
            return {"at_s": 0, "action": "repeat", "times": 5, "every_s": 5,
                    "jitter_s": [0, 20], **extra}
        proto = _p(
            block(module="ttl_b", steps=[
                {"at_s": 0, "action": "pulse", "params": {"pin": 9, "width_s": 1}}]),
            block(at_s=200, steps=[
                {"at_s": 0, "module": "ttl_a", "action": "pulse",
                 "params": {"pins": [17, 22], "width_s": 2}},
                {"at_s": 0, "module": "ttl_b", "action": "pulse",
                 "params": {"pin": 5, "width_s": 2}}]),
            seed=4711)
        a = expand(proto, module_id="ttl_a")
        b = expand(proto, module_id="ttl_b")
        assert a.ok and b.ok, (a.errors, b.errors)
        on_a = [e.t_s for e in a.edges if e.level == "high" and e.pin == 17]
        on_b = [e.t_s for e in b.edges if e.level == "high" and e.pin == 5]
        assert len(on_a) == 5 and on_a == on_b
        assert a.pins == [17, 22] and b.pins == [5, 9]

    def test_repeat_module_applies_inside(self):
        proto = _p({"at_s": 0, "module": "ttl_b", "action": "repeat", "times": 2,
                    "every_s": 5, "steps": [{"at_s": 0, "action": "blink"}]})
        # ttl_a neither runs nor reports ttl_b's steps.
        exp = expand(proto, module_id="ttl_a")
        assert exp.ok and exp.edges == []
        assert not expand(proto, module_id="ttl_b").ok

    def test_pins_drive_several_pins_together(self):
        exp = expand(_p({"at_s": 1, "action": "pulse",
                         "params": {"pins": [22, 17], "width_s": 2}}))
        assert exp.ok, exp.errors
        assert _edges(exp) == [(1, 22, "high"), (1, 17, "high"),
                               (3, 22, "low"), (3, 17, "low")]

    def test_pins_errors(self):
        for params in ({"pin": 1, "pins": [2]}, {"pins": []}, {"pins": [1, 1]},
                       {"pins": [1, 9]}, {"pins": 1}):
            exp = expand(_p({"at_s": 0, "action": "pulse",
                             "params": {**params, "width_s": 1}}),
                         allowed_pins={1, 2})
            assert not exp.ok and len(exp.errors) == 1, (params, exp.errors)

    def test_back_to_back_pulses_are_not_an_overlap(self):
        exp = expand(_p(
            {"at_s": 0, "action": "pulse", "params": {"pin": 1, "width_s": 1}},
            {"at_s": 1, "action": "pulse", "params": {"pin": 1, "width_s": 1}},
        ))
        assert exp.ok, exp.errors

    def test_state_at(self):
        exp = expand(_p(
            {"at_s": 0, "action": "set", "params": {"pin": 17, "level": "high"}},
            {"at_s": 5, "action": "pulse", "params": {"pin": 22, "width_s": 1}},
            {"at_s": 10, "action": "set", "params": {"pin": 17, "level": "low"}},
        ))
        assert state_at(exp.edges, 5.5) == {17: "high", 22: "high"}
        assert state_at(exp.edges, 7) == {17: "high", 22: "low"}
        assert state_at(exp.edges, 11) == {17: "low", 22: "low"}


class TestValidation:
    @pytest.mark.parametrize("step, fragment", [
        ({"at_s": 0, "action": "blink", "params": {"pin": 1}}, "unknown action"),
        ({"at_s": -1, "action": "pulse", "params": {"pin": 1, "width_s": 1}}, "at_s"),
        ({"at_s": 0, "action": "pulse", "params": {"pin": 1, "width_s": 0}}, "width_s"),
        ({"at_s": 0, "action": "pulse", "params": {"pin": "x", "width_s": 1}}, "pin"),
        ({"at_s": 0, "action": "set", "params": {"pin": 1, "level": "on"}}, "level"),
        ({"at_s": 0, "action": "pulse"}, "params"),
        ({"at_s": 0, "action": "train",
          "params": {"pin": 1, "rate_hz": 10, "width_s": 0.1, "count": 3}},
         "shorter than the period"),
        ({"at_s": 0, "action": "train",
          "params": {"pin": 1, "rate_hz": 10, "width_s": 0.01, "count": 0}}, "count"),
        ({"at_s": 0, "action": "repeat", "times": 2, "every_s": 1,
          "jitter_s": [3, 1], "steps": [{"action": "pulse",
                                         "params": {"pin": 1, "width_s": 0.1}}]},
         "jitter_s"),
        ({"at_s": 0, "action": "repeat", "times": 0, "every_s": 1,
          "steps": [{"action": "pulse", "params": {"pin": 1, "width_s": 0.1}}]},
         "times"),
        ({"at_s": 0, "action": "repeat", "times": 2, "every_s": 1, "steps": []},
         "non-empty"),
    ])
    def test_bad_steps(self, step, fragment):
        exp = expand(_p(step))
        assert not exp.ok
        assert any(fragment in e for e in exp.errors), exp.errors

    def test_top_level(self):
        assert not expand({"version": 2, "steps": []}).ok
        assert not expand({"version": 1}).ok
        assert not expand(_p(end_state="explode")).ok
        assert not expand("nope").ok
        assert expand(_p(end_state="hold")).end_state == "hold"

    def test_pin_must_be_scheduled(self):
        exp = expand(_p({"at_s": 0, "action": "pulse",
                         "params": {"pin": 19, "width_s": 0.1}}),
                     allowed_pins={17, 22})
        assert not exp.ok
        assert "not a 'scheduled' output pin" in exp.errors[0]
        assert "17, 22" in exp.errors[0]

    def test_pulse_inside_held_set_overlaps(self):
        exp = expand(_p(
            {"at_s": 0, "action": "set", "params": {"pin": 17, "level": "high"}},
            {"at_s": 5, "action": "pulse", "params": {"pin": 17, "width_s": 0.1}},
            {"at_s": 10, "action": "set", "params": {"pin": 17, "level": "low"}},
        ))
        assert not exp.ok
        assert any("overlap" in e for e in exp.errors)

    def test_overlapping_trains(self):
        exp = expand(_p(
            {"at_s": 0, "action": "train",
             "params": {"pin": 1, "rate_hz": 10, "width_s": 0.05, "count": 10}},
            {"at_s": 0.42, "action": "pulse", "params": {"pin": 1, "width_s": 0.1}},
        ))
        assert not exp.ok

    def test_redundant_set_low_is_only_a_warning(self):
        exp = expand(_p({"at_s": 0, "action": "set",
                         "params": {"pin": 1, "level": "low"}}))
        assert exp.ok
        assert exp.warnings

    def test_fast_timing_warns(self):
        exp = expand(_p({"at_s": 0, "action": "train",
                         "params": {"pin": 1, "rate_hz": 100, "width_s": 0.002,
                                    "count": 5}}))
        assert exp.ok
        assert len(exp.warnings) == 2

    def test_edge_cap(self):
        exp = expand(_p({"at_s": 0, "action": "train",
                         "params": {"pin": 1, "rate_hz": 40, "width_s": 0.01,
                                    "count": MAX_EDGES}}))
        assert not exp.ok
        assert any("too many edges" in e for e in exp.errors)


# ---------------------------------------------------------------------------
# The frontend twin (sessionProtocol.js) must expand every fixture identically
# ---------------------------------------------------------------------------

import json  # noqa: E402 -- section-local imports for the twin check
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from itertools import pairwise  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_FIXTURES = os.path.join(_ROOT, "src", "shared", "session_protocol_fixtures.json")
_JS = os.path.join(_ROOT, "src", "controller", "frontend", "src", "basic",
                   "utils", "sessionProtocol.js")


def _py_result(case):
    exp = expand(case["protocol"], module_id=case.get("module_id"),
                 allowed_pins=set(case["allowed_pins"]) if "allowed_pins" in case
                 else None,
                 module_type=case.get("module_type"),
                 sound_files=case.get("sound_files"), outputs=case.get("outputs"),
                 stretchable=set(case["stretchable"]) if "stretchable" in case else None)
    return {"edges": [[round(e.t_s, 9), e.pin, e.level, e.step, e.action]
                      for e in exp.edges],
            "cues": [[round(c.t_s, 9), c.output, c.file, c.volume, c.duration_s,
                      c.length_s, c.step] for c in exp.cues],
            "ok": exp.ok, "errors": len(exp.errors), "warnings": len(exp.warnings),
            "end_state": exp.end_state, "duration": round(exp.duration_s, 9)}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_twin_matches_python_on_every_fixture():
    script = f"""
import {{ expand }} from {json.dumps("file://" + os.path.abspath(_JS))};
import fs from "fs";
const doc = JSON.parse(fs.readFileSync({json.dumps(os.path.abspath(_FIXTURES))}, "utf8"));
const out = doc.cases.map((c) => {{
  const r = expand(c.protocol, {{ moduleId: c.module_id ?? null,
                                 allowedPins: c.allowed_pins ?? null,
                                 moduleType: c.module_type ?? null,
                                 soundFiles: c.sound_files ?? null,
                                 outputs: c.outputs ?? null,
                                 stretchable: c.stretchable ?? null }});
  const r9 = (x) => Math.round(x * 1e9) / 1e9;
  return {{ edges: r.edges.map((e) => [r9(e.t_s), e.pin, e.level, e.step, e.action]),
           cues: r.cues.map((q) => [r9(q.t_s), q.output, q.file, q.volume, q.duration_s,
                                   q.length_s, q.step]),
           ok: r.ok, errors: r.errors.length, warnings: r.warnings.length,
           end_state: r.endState, duration: r9(r.durationS) }};
}});
console.log(JSON.stringify(out));
"""
    res = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    cases = json.load(open(_FIXTURES))["cases"]
    assert len(js) == len(cases)
    for case, js_result in zip(cases, js):
        py = _py_result(case)
        assert js_result == py, f"fixture {case['name']!r} differs"
        if case["name"] == "bad steps":
            assert py["errors"] >= 8


# ---------------------------------------------------------------------------
# repeat spread over a window (spread_s / min_gap_s)
# ---------------------------------------------------------------------------

def _conditioning(seed=1, times=10, spread=1130, gap=60):
    return {"version": 1, "seed": seed, "end_state": "all_inactive", "steps": [
        {"at_s": 60, "action": "repeat", "times": times, "spread_s": spread,
         "min_gap_s": gap, "steps": [
             {"at_s": 0, "action": "pulse", "params": {"pin": 17, "width_s": 10}},
             {"at_s": 9, "action": "pulse", "params": {"pin": 2, "width_s": 1}}]}]}


def _starts(exp, pin=17):
    return [e.t_s for e in exp.edges if e.pin == pin and e.level == "high"]


class TestSpreadRepeat:
    @pytest.mark.parametrize("seed", range(1, 30))
    def test_trials_inside_the_window_and_at_least_min_gap_apart(self, seed):
        exp = expand(_conditioning(seed=seed))
        assert exp.ok, exp.errors
        starts = _starts(exp)
        assert len(starts) == 10
        assert 60 <= starts[0] and starts[-1] <= 60 + 1130 + 1e-9
        assert min(b - a for a, b in pairwise(starts)) >= 60 - 1e-9
        # co-terminating shock: each shock ends with its light
        light_off = [e.t_s for e in exp.edges if e.pin == 17 and e.level == "low"]
        shock_off = [e.t_s for e in exp.edges if e.pin == 2 and e.level == "low"]
        assert light_off == pytest.approx(shock_off)

    def test_same_seed_same_schedule_different_seed_different(self):
        a, b, c = (expand(_conditioning(seed=s)) for s in (5, 5, 6))
        assert _starts(a) == _starts(b) != _starts(c)

    def test_placement_is_random_not_bunched(self):
        # first trial isn't always at the window start
        firsts = {round(_starts(expand(_conditioning(seed=s)))[0]) for s in range(1, 20)}
        assert len(firsts) > 5

    def test_too_many_trials_for_the_window_is_an_error(self):
        exp = expand(_conditioning(times=21, spread=1130, gap=60))
        assert not exp.ok and "need a spread of at least 1200" in exp.errors[0]

    def test_every_and_spread_together_is_an_error(self):
        p = _conditioning()
        p["steps"][0]["every_s"] = 5
        assert "not both" in expand(p).errors[0]

    def test_gap_shorter_than_a_trial_is_caught_as_an_overlap(self):
        exp = expand(_conditioning(times=10, spread=40, gap=2))
        assert not exp.ok


class TestSound:
    TRIAL = {"at_s": 30, "action": "repeat", "times": 5, "every_s": 5,
             "jitter_s": [0, 20], "steps": [
                 {"at_s": 0, "action": "pulse",
                  "params": {"pins": [4, 6], "width_s": 2}},
                 {"at_s": 0, "action": "play",
                  "params": {"file": "tone_5k.wav", "duration_s": 2}}]}

    def test_tone_lands_on_the_ttl_trials(self):
        proto = _p(self.TRIAL, seed=4711)
        ttl = expand(proto, module_id="ttl_a", module_type="ttl", allowed_pins={4, 6})
        snd = expand(proto, module_id="sound_b", module_type="sound",
                     sound_files={"tone_5k.wav": 3.0}, outputs=1)
        assert ttl.ok and snd.ok, (ttl.errors, snd.errors)
        assert ttl.cues == [] and snd.edges == []
        onsets = [e.t_s for e in ttl.edges if e.level == "high" and e.pin == 4]
        assert [c.t_s for c in snd.cues] == onsets
        assert all(c.length_s == 2 and c.output == 1 for c in snd.cues)
        assert snd.duration_s == pytest.approx(onsets[-1] + 2)

    def test_whole_protocol_check_keeps_both(self):
        exp = expand(_p(self.TRIAL, seed=4711))
        assert exp.ok and len(exp.edges) == 20 and len(exp.cues) == 5

    def test_length_from_file_and_overlap(self):
        files = {"a.wav": 1.5}
        ok = expand(_p({"at_s": 0, "action": "play", "params": {"file": "a.wav"}},
                       {"at_s": 1.5, "action": "play", "params": {"file": "a.wav"}}),
                    module_type="sound", sound_files=files)
        assert ok.ok and [c.length_s for c in ok.cues] == [1.5, 1.5]
        bad = expand(_p({"at_s": 0, "action": "play", "params": {"file": "a.wav"}},
                        {"at_s": 1, "action": "play", "params": {"file": "a.wav"}}),
                     module_type="sound", sound_files=files)
        assert not bad.ok and "overlap" in bad.errors[0]
        # Different outputs may overlap.
        two = expand(_p({"at_s": 0, "action": "play", "params": {"file": "a.wav"}},
                        {"at_s": 1, "action": "play",
                         "params": {"file": "a.wav", "output": 2}}),
                     module_type="sound", sound_files=files, outputs=2)
        assert two.ok

    def test_paths_refused(self):
        for name in ("../x.wav", "/etc/passwd", "a/b.wav", ".hidden", "a\\b.wav"):
            exp = expand(_p({"at_s": 0, "action": "play", "params": {"file": name}}))
            assert not exp.ok, name

    def test_output_and_volume_limits(self):
        exp = expand(_p({"at_s": 0, "action": "play",
                         "params": {"file": "a.wav", "output": 2}}), outputs=1)
        assert not exp.ok and "1 output" in exp.errors[0]
        loud = expand(_p({"at_s": 0, "action": "play",
                          "params": {"file": "a.wav", "volume": 2}}))
        assert loud.ok and loud.warnings
        assert not expand(_p({"at_s": 0, "action": "play",
                              "params": {"file": "a.wav", "volume": 5}})).ok

    def test_unknown_module_type(self):
        assert not expand(_p(), module_type="camera").ok


# ---------------------------------------------------------------------------
# set with duration_s
# ---------------------------------------------------------------------------

class TestSetDuration:
    def _edges(self, params, at=1.0):
        exp = expand({"version": 1, "steps": [
            {"at_s": at, "action": "set", "params": {"pin": 17, **params}}]})
        return exp, [(e.t_s, e.level) for e in exp.edges]

    def test_high_for_a_while_then_low(self):
        exp, edges = self._edges({"level": "high", "duration_s": 2.5})
        assert exp.ok, exp.errors
        assert edges == [(1.0, "high"), (3.5, "low")]

    def test_zero_or_absent_holds(self):
        for extra in ({}, {"duration_s": 0}, {"duration_s": None}):
            exp, edges = self._edges({"level": "high", **extra})
            assert exp.ok and edges == [(1.0, "high")]

    def test_low_for_a_while_then_high(self):
        exp = expand({"version": 1, "steps": [
            {"at_s": 0, "action": "set", "params": {"pin": 17, "level": "high"}},
            {"at_s": 5, "action": "set",
             "params": {"pin": 17, "level": "low", "duration_s": 1}}]})
        assert exp.ok, exp.errors
        assert [(e.t_s, e.level) for e in exp.edges] == [
            (0.0, "high"), (5.0, "low"), (6.0, "high")]

    def test_negative_or_text_refused(self):
        for bad in (-1, "5", True):
            exp, edges = self._edges({"level": "high", "duration_s": bad})
            assert not exp.ok and edges == []

    def test_overlap_with_a_later_step_is_caught(self):
        exp = expand({"version": 1, "steps": [
            {"at_s": 0, "action": "set",
             "params": {"pin": 17, "level": "high", "duration_s": 10}},
            {"at_s": 5, "action": "pulse", "params": {"pin": 17, "width_s": 1}}]})
        assert not exp.ok


class TestRandomWithMaxGap:
    """repeat with spread_s + min_gap_s + max_gap_s (the editor's "Random")."""

    @staticmethod
    def _starts(times, spread, lo, hi, seed, at=0):
        exp = expand({"version": 1, "seed": seed, "steps": [
            {"at_s": at, "action": "repeat", "times": times, "spread_s": spread,
             "min_gap_s": lo, "max_gap_s": hi,
             "steps": [{"at_s": 0, "action": "pulse",
                        "params": {"pin": 17, "width_s": 0.5}}]}]})
        assert exp.ok, exp.errors
        return [e.t_s for e in exp.edges if e.level == "high"]

    @pytest.mark.parametrize("seed", range(20))
    def test_gaps_within_bounds_and_window(self, seed):
        starts = self._starts(8, 1189, 60, 180, seed, at=10)
        assert len(starts) == 8
        gaps = [b - a for a, b in itertools.pairwise(starts)]
        assert all(60 - 1e-6 <= g <= 180 + 1e-6 for g in gaps)
        assert 10 <= starts[0] and starts[-1] <= 10 + 1189 + 1e-6

    @pytest.mark.parametrize("seed", range(10))
    def test_squeezed_to_fit_the_window(self, seed):
        starts = self._starts(6, 400, 30, 200, seed)
        gaps = [b - a for a, b in itertools.pairwise(starts)]
        assert all(g >= 30 - 1e-6 for g in gaps) and starts[-1] <= 400 + 1e-6

    def test_equal_min_and_max_is_regular(self):
        starts = self._starts(4, 500, 100, 100, 1)
        assert [round(b - a, 6) for a, b in itertools.pairwise(starts)] == [100] * 3

    def test_max_below_min_is_refused(self):
        exp = expand({"version": 1, "steps": [
            {"at_s": 0, "action": "repeat", "times": 3, "spread_s": 100,
             "min_gap_s": 30, "max_gap_s": 10,
             "steps": [{"at_s": 0, "action": "pulse", "params": {"pin": 17, "width_s": 1}}]}]})
        assert any("max_gap_s (10) is below min_gap_s (30)" in e for e in exp.errors)
