"""Habitat placement (variants/habitat/placement.py): which camera and which
AudioMoth is in which box, from module names and AudioMoth labels."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "variants", "habitat"))

import placement as pl  # noqa: E402

BOXES = [f"{c}{r}" for r in range(1, 5) for c in "ABCD"]


def _cam(name, online=True, status="WAITING"):
    return {"type": "camera", "name": name, "online": online, "status": status,
            "config": {"module": {"name": name}, "_private": 1}}


def _mic(labels, online=True):
    return {"type": "microphone", "name": "mic", "online": online, "status": "WAITING",
            "config": {"audiomoth_labels": dict(labels)}}


def _modules():
    return {
        "camera_1": _cam("A1"),
        "camera_2": _cam("b2"),                       # typo: not on the map
        "camera_3": _cam("camera_3"),                 # unplaced
        "microphone_a": _mic({"S1": "A1", "S2": ""}),
        "rfid_1": {"type": "rfid", "name": "rfid_1", "config": {}},
    }


def test_state_reads_names_and_labels_and_flags_problems():
    s = pl.placement_state(_modules(), BOXES, {"microphone_a": {"S1", "S3"}})
    a1 = next(b for b in s["boxes"] if b["id"] == "A1")
    assert a1 == {"id": "A1", "cameras": ["camera_1"], "audiomoths": ["S1"]}
    assert {a["serial"] for a in s["audiomoths"]} == {"S1", "S2", "S3"}   # S3 only heard
    assert any("camera_2" in p and "did you mean B2" in p for p in s["problems"])
    assert "A1" not in s["empty"]["cameras"] and "B2" in s["empty"]["cameras"]


def test_state_flags_placed_audiomoth_not_heard_and_two_cameras_in_a_box():
    mods = _modules()
    mods["camera_9"] = _cam("A1")
    s = pl.placement_state(mods, BOXES, {})
    assert any(p.startswith("A1: 2 cameras") for p in s["problems"])
    assert any("AudioMoth S1 isn't sending levels" in p for p in s["problems"])


def test_plan_moves_and_displaces_the_occupant():
    s = pl.placement_state(_modules(), BOXES, {"microphone_a": {"S1", "S2"}})
    out = pl.plan([{"kind": "camera", "module_id": "camera_3", "box": "a1"},
                   {"kind": "audiomoth", "module_id": "microphone_a",
                    "serial": "S2", "box": "A1"}], s, BOXES)
    assert out["patches"]["camera_3"] == {"module": {"name": "A1"}}
    assert out["patches"]["camera_1"] == {"module": {"name": ""}}         # displaced
    assert out["patches"]["microphone_a"] == {"audiomoth_labels": {"S1": "", "S2": "A1"}}
    kinds = {(c["module_id"], c["serial"], c["before"], c["after"]) for c in out["changes"]}
    assert ("camera_1", None, "A1", None) in kinds
    assert ("microphone_a", "S2", None, "A1") in kinds


def test_plan_swap_within_one_batch_keeps_both():
    mods = _modules()
    mods["camera_3"] = _cam("B1")
    s = pl.placement_state(mods, BOXES, {})
    out = pl.plan([{"kind": "camera", "module_id": "camera_1", "box": "B1"},
                   {"kind": "camera", "module_id": "camera_3", "box": "A1"}], s, BOXES)
    assert out["patches"] == {"camera_1": {"module": {"name": "B1"}},
                              "camera_3": {"module": {"name": "A1"}}}


def test_plan_rejects_bad_input():
    s = pl.placement_state(_modules(), BOXES, {})
    with pytest.raises(pl.PlacementError):
        pl.plan([{"kind": "camera", "module_id": "camera_1", "box": "Z9"}], s, BOXES)
    with pytest.raises(pl.PlacementError):
        pl.plan([{"kind": "camera", "module_id": "nope", "box": "A1"}], s, BOXES)
    with pytest.raises(pl.PlacementError):
        pl.plan([{"kind": "camera", "module_id": "camera_1", "box": "B1"},
                 {"kind": "camera", "module_id": "camera_3", "box": "B1"}], s, BOXES)


def test_plan_no_op_when_already_placed():
    s = pl.placement_state(_modules(), BOXES, {})
    assert pl.plan([{"kind": "camera", "module_id": "camera_1", "box": "A1"}],
                   s, BOXES) == {"patches": {}, "changes": []}


def test_parse_import_resolves_ids_and_serials():
    s = pl.placement_state(_modules(), BOXES, {"microphone_a": {"S1", "S2"}})
    text = """module, box
    camera_3, C4   # the new one
    s2\tb 2
    unknown_thing, A1
    camera_1, Z9
    """
    assignments, errors = pl.parse_import(text, s, BOXES)
    assert assignments == [
        {"kind": "camera", "module_id": "camera_3", "box": "C4"},
        {"kind": "audiomoth", "module_id": "microphone_a", "serial": "S2", "box": "B2"}]
    assert len(errors) == 2 and "unknown_thing" in errors[0] and "Z9" in errors[1]


class _Fake:
    def __init__(self, tmp_path, recording=()):
        self.mods = _modules()
        self.sent, self.targets = [], []
        self.svc = pl.PlacementService(
            pl.PlacementLog(str(tmp_path), clock=lambda: 5.0),
            get_modules=lambda: self.mods, get_boxes=lambda: BOXES,
            set_target_config=lambda m, c: self.targets.append((m, c)),
            send_command=lambda m, c, p: self.sent.append((m, c, p)),
            is_recording=lambda m: m in recording, emit=lambda *a: None,
            clock=lambda: 100.0)


def test_service_apply_merges_onto_config_and_logs(tmp_path):
    f = _Fake(tmp_path)
    f.svc.on_module_status("microphone_a", {"type": "audio_levels",
                                            "levels": [{"serial": "S2"}]})
    f.svc.apply([{"kind": "audiomoth", "module_id": "microphone_a",
                  "serial": "S2", "box": "B1"}], reason="panel", operator="10.0.0.5")
    mid, cmd, cfg = f.sent[0]
    assert (mid, cmd) == ("microphone_a", "set_config")
    assert cfg["audiomoth_labels"] == {"S1": "A1", "S2": "B1"}
    log = f.svc.state()["changes"]
    assert log[0]["serial"] == "S2" and log[0]["after"] == "B1"
    assert log[0]["operator"] == "10.0.0.5"


def test_service_apply_strips_private_keys_and_refuses_recording(tmp_path):
    f = _Fake(tmp_path)
    f.svc.apply([{"kind": "camera", "module_id": "camera_3", "box": "D4"}],
                reason="import", operator="x")
    assert "_private" not in f.sent[0][2]
    f = _Fake(tmp_path, recording=("camera_3",))
    with pytest.raises(pl.PlacementError, match="records"):
        f.svc.apply([{"kind": "camera", "module_id": "camera_3", "box": "D4"}],
                    reason="panel", operator="x")
    assert f.sent == []


def test_service_preview_import(tmp_path):
    f = _Fake(tmp_path)
    p = f.svc.preview_import("camera_3, A1\nbogus, B1")
    assert p["assignments"] == [{"kind": "camera", "module_id": "camera_3", "box": "A1"}]
    assert {c["module_id"] for c in p["changes"]} == {"camera_1", "camera_3"}
    assert len(p["errors"]) == 1


def _col_modules():
    mods = _modules()
    mods["microphone_a"]["name"] = "A"
    mods["microphone_b"] = _mic({"T1": "B1"})
    mods["microphone_b"]["name"] = "b"                # near-miss column
    return mods


def test_state_reads_mic_columns_and_flags_cross_column_labels():
    mods = _col_modules()
    mods["microphone_a"]["config"]["audiomoth_labels"]["S2"] = "C3"
    s = pl.placement_state(mods, BOXES, {"microphone_a": {"S1", "S2"}})
    assert s["columns"] == ["A", "B", "C", "D"]
    cols = {x["module_id"]: x["column"] for x in s["mics"]}
    assert cols == {"microphone_a": "A", "microphone_b": None}
    assert any("S2 is on microphone_a, which is column A" in p for p in s["problems"])
    assert any("microphone_b" in p and "did you mean B" in p for p in s["problems"])


def test_plan_keeps_audiomoths_in_their_modules_column():
    s = pl.placement_state(_col_modules(), BOXES, {"microphone_a": {"S1", "S2"}})
    with pytest.raises(pl.PlacementError, match="isn't in column A"):
        pl.plan([{"kind": "audiomoth", "module_id": "microphone_a",
                  "serial": "S2", "box": "B2"}], s, BOXES)
    # Moving the module to column B in the same batch makes B2 fine.
    out = pl.plan([{"kind": "mic", "module_id": "microphone_a", "column": "b"},
                   {"kind": "audiomoth", "module_id": "microphone_a",
                    "serial": "S2", "box": "B2"}], s, BOXES)
    assert out["patches"]["microphone_a"] == {
        "module": {"name": "B"}, "audiomoth_labels": {"S2": "B2"}}


def test_plan_mic_column_displaces_the_previous_module():
    mods = _col_modules()
    mods["microphone_b"]["name"] = "B"
    s = pl.placement_state(mods, BOXES, {})
    out = pl.plan([{"kind": "mic", "module_id": "microphone_a", "column": "B"}], s, BOXES)
    assert out["patches"]["microphone_b"] == {"module": {"name": ""}}
    assert ("mic", "microphone_a", "A", "B") in {
        (c["kind"], c["module_id"], c["before"], c["after"]) for c in out["changes"]}
    with pytest.raises(pl.PlacementError, match="not a column"):
        pl.plan([{"kind": "mic", "module_id": "microphone_a", "column": "Q"}], s, BOXES)


def test_unplaced_module_may_use_any_box():
    s = pl.placement_state(_modules(), BOXES, {"microphone_a": {"S2"}})   # named "mic"
    out = pl.plan([{"kind": "audiomoth", "module_id": "microphone_a",
                    "serial": "S2", "box": "D4"}], s, BOXES)
    assert out["patches"]["microphone_a"] == {"audiomoth_labels": {"S2": "D4"}}


def test_parse_import_takes_mic_columns():
    s = pl.placement_state(_col_modules(), BOXES, {"microphone_a": {"S2"}})
    assignments, errors = pl.parse_import(
        "microphone_b, C\nS2, A3\nmicrophone_a, A9", s, BOXES)
    assert assignments == [
        {"kind": "mic", "module_id": "microphone_b", "column": "C"},
        {"kind": "audiomoth", "module_id": "microphone_a", "serial": "S2", "box": "A3"}]
    assert errors == ["Line 3: 'A9' is not a column on the layout"]
