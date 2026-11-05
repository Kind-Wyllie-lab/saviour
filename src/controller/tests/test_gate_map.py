"""
Tests for the habitat RFID gate mapping: variants/habitat/gate_map.py
(layout, store, mapping run, review checks) and gate_map_service.py (wiring
to Socket.IO and the RFID modules, with fakes for both).

Both files import each other by bare name (as habitat_controller.py loads
them), so the tests do the same, via the variant directory on sys.path.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "variants",
                                "habitat"))

import gate_map as gm  # noqa: E402 -- needs the sys.path entry above
from gate_map_service import GateMapService  # noqa: E402

# ── Layout ──────────────────────────────────────────────────────────────────

def test_generate_4x4_layout_names_and_counts():
    lay = gm.generate_layout()
    assert [b["id"] for b in lay["boxes"]][:5] == ["A1", "B1", "C1", "D1", "A2"]
    walls = {w["id"]: w for w in lay["walls"]}
    # 3 vertical walls x 4 rows + 3 horizontal walls x 4 columns
    assert len(walls) == 24
    assert walls["A1B1"]["orientation"] == "vertical"
    assert walls["A1A2"]["orientation"] == "horizontal"
    assert "B1A1" not in walls
    assert len(lay["gates"]) == 96
    assert [g["id"] for g in lay["gates"][:4]] == [
        "A1B1_a", "A1B1_b", "A1B1_c", "A1B1_d"]


def test_generate_rejects_silly_sizes():
    with pytest.raises(gm.GateMapError):
        gm.generate_layout(0, 4)
    with pytest.raises(gm.GateMapError):
        gm.generate_layout(4, 4, gates_per_wall=5)


def test_validate_layout_keeps_only_fitted_flags():
    lay = gm.generate_layout(2, 1, 2)
    lay["gates"][1]["fitted"] = False
    lay["boxes"] = "tampered"
    clean = gm.validate_layout(lay)
    assert clean["boxes"] == gm.generate_layout(2, 1, 2)["boxes"]
    assert [g["fitted"] for g in clean["gates"]] == [True, False]


def test_validate_layout_rejects_wrong_gates():
    lay = gm.generate_layout(2, 1, 2)
    lay["gates"].append({"id": "Z9Z9_a", "fitted": True})
    with pytest.raises(gm.GateMapError):
        gm.validate_layout(lay)


def test_scope_gates():
    lay = gm.generate_layout(2, 2, 2)
    lay["gates"][0]["fitted"] = False      # A1B1_a
    assert gm.scope_gates(lay, {"wall": "A1B1"}) == ["A1B1_b"]
    assert gm.scope_gates(lay, "all")[0] == "A1B1_b"
    assert gm.scope_gates(lay, {"gates": ["A2B2_b", "A1A2_a"]}) == [
        "A1A2_a", "A2B2_b"]
    with pytest.raises(gm.GateMapError):
        gm.scope_gates(lay, {"gates": ["A1B1_a"]})   # not fitted
    with pytest.raises(gm.GateMapError):
        gm.scope_gates(lay, {"wall": "nope"})


# ── Mapping run ─────────────────────────────────────────────────────────────

PROBE = "000807DC1F"
ANIMAL = "1122334455"


def _run(scope=("A1B1_a", "A1B1_b"), base=None):
    return gm.MappingRun.start("AS", list(scope), base or {}, now=100.0)


def _pass(run, module, unit, ts, tag=PROBE):
    run.on_read(module, unit, tag, ts)


def _settled(run, *reads):
    for module, unit in reads:
        _pass(run, module, unit, 200.0)
    assert run.settle_due(200.0 + gm.SETTLE_S)
    run.settle()


def test_first_read_registers_probe_then_others_ignored():
    run = _run()
    assert run.on_read("rfid_1", 5, PROBE, 101.0)
    assert run.phase == "gate" and run.gate == "A1B1_a"
    assert not run.on_read("rfid_1", 6, ANIMAL, 102.0)
    assert run.capture.first is None


def test_zero_ids_are_not_a_probe():
    run = _run()
    assert not run.on_read("rfid_1", 5, "0000000000", 101.0)
    assert run.phase == "probe"


def test_confirm_maps_gate_and_advances_to_review():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)               # probe registration
    _settled(run, ("rfid_1", 34))
    assert run.issue() is None
    run.act("confirm", 201.0, serials={"rfid_1:34": "AABB0011"})
    assert run.gate == "A1B1_b"
    _settled(run, ("rfid_1", 35))
    run.act("confirm", 202.0)
    assert run.phase == "review"
    e = run.entries()
    assert e["A1B1_a"]["unit"] == 34 and e["A1B1_a"]["unit_serial"] == "AABB0011"
    assert e["A1B1_b"]["status"] == gm.MAPPED


def test_confirm_before_any_read_is_refused():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)
    with pytest.raises(gm.GateMapError):
        run.act("confirm", 150.0)


def test_reads_after_settling_are_ignored():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)
    _settled(run, ("rfid_1", 34))
    assert not run.on_read("rfid_1", 35, PROBE, 210.0)
    assert run.issue() is None


def test_two_units_reading_one_pass_needs_a_pick():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)
    _settled(run, ("rfid_1", 34), ("rfid_1", 35), ("rfid_1", 35))
    issue = run.issue()
    assert issue["kind"] == "multiple" and issue["units"] == ["rfid_1:35", "rfid_1:34"]
    with pytest.raises(gm.GateMapError):
        run.act("confirm", 201.0)
    run.act("confirm", 201.0, unit="rfid_1:34")
    assert run.entries()["A1B1_a"]["unit"] == 34


def test_unit_already_mapped_must_move_or_share():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)
    _settled(run, ("rfid_1", 34))
    run.act("confirm", 201.0)
    _settled(run, ("rfid_1", 34))                # same unit at the next gate
    assert run.issue() == {"kind": "already_mapped", "unit": "rfid_1:34",
                           "gates": ["A1B1_a"]}
    with pytest.raises(gm.GateMapError):
        run.act("confirm", 202.0)
    run.act("confirm", 202.0, resolve="move")
    e = run.entries()
    assert "A1B1_a" not in e and e["A1B1_b"]["unit"] == 34


def test_share_keeps_both_and_review_lists_it():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)
    _settled(run, ("rfid_1", 34))
    run.act("confirm", 201.0)
    _settled(run, ("rfid_1", 34))
    run.act("confirm", 202.0, resolve="share")
    review = run.review(None)
    assert review["shared_units"] == [{"unit": "rfid_1:34",
                                       "gates": ["A1B1_a", "A1B1_b"]}]


def test_conflict_with_the_old_map_is_caught():
    base = {"C1D1_a": {"gate": "C1D1_a", "status": gm.MAPPED,
                       "module_id": "rfid_1", "unit": 34}}
    run = _run(base=base)
    _pass(run, "rfid_1", 9, 101.0)
    _settled(run, ("rfid_1", 34))
    assert run.issue()["gates"] == ["C1D1_a"]


def test_skips_faulty_back_and_retry():
    run = _run(scope=("A1B1_a", "A1B1_b", "A1B1_c"))
    _pass(run, "rfid_1", 9, 101.0)
    run.act("skip_closed", 110.0)
    run.act("faulty", 111.0)
    assert run.entries()["A1B1_a"]["status"] == gm.CLOSED
    assert run.entries()["A1B1_b"]["status"] == gm.FAULTY
    run.act("back", 112.0)
    assert run.gate == "A1B1_b"
    _settled(run, ("rfid_1", 40))
    run.act("retry", 213.0)
    assert run.capture.first is None
    run.act("skip_not_fitted", 214.0)
    run.act("skip_not_fitted", 215.0)
    assert run.phase == "review"
    run.act("back", 216.0)
    assert run.phase == "gate" and run.gate == "A1B1_c"


def test_draft_round_trip_drops_capture():
    run = _run()
    _pass(run, "rfid_1", 9, 101.0)
    _settled(run, ("rfid_1", 34))
    run.act("confirm", 201.0)
    _pass(run, "rfid_1", 35, 202.0)
    back = gm.MappingRun.from_dict(json.loads(json.dumps(run.to_dict())), 300.0)
    assert back.gate == "A1B1_b" and back.probe_tag == PROBE
    assert back.capture.first is None
    assert back.entries() == run.entries()


def test_review_bus_checks():
    entries = {
        "A1B1_a": {"status": gm.MAPPED, "module_id": "r", "unit": 1,
                   "unit_serial": "OLD"},
        "A1B1_b": {"status": gm.MAPPED, "module_id": "r", "unit": 2,
                   "unit_serial": "S2"},
        "A1B1_c": {"status": gm.CLOSED, "module_id": None, "unit": None},
    }
    bus = {b.key: b for b in [gm.BusUnit("r", 1, "NEW"), gm.BusUnit("r", 3, "S3")]}
    r = gm.review_entries(entries, bus)
    assert r["on_bus_not_mapped"] == ["r:3"]
    assert r["mapped_not_on_bus"] == [{"unit": "r:2", "gates": ["A1B1_b"]}]
    assert r["serial_changed"][0]["gate"] == "A1B1_a"
    assert gm.review_entries(entries, None)["scanned"] is False


# ── Store ───────────────────────────────────────────────────────────────────

def test_store_defaults_and_round_trips(tmp_path):
    store = gm.GateMapStore(str(tmp_path / "habitat"), clock=lambda: 500.0)
    assert len(store.load_layout()["gates"]) == 96
    assert store.load_map()["entries"] == {}
    lay = gm.generate_layout(2, 2, 1)
    lay["gates"][0]["fitted"] = False
    store.save_layout(lay)
    assert not store.load_layout()["gates"][0]["fitted"]


def test_save_map_logs_only_changed_gates(tmp_path):
    store = gm.GateMapStore(str(tmp_path), clock=lambda: 500.0)
    a = {"gate": "A1B1_a", "status": gm.MAPPED, "module_id": "r", "unit": 1}
    b = {"gate": "A1B1_b", "status": gm.CLOSED}
    store.save_map({"A1B1_a": a, "A1B1_b": b}, run_id="r1", operator="AS",
                   reason="mapping_run")
    changes = store.save_map({"A1B1_a": a}, run_id="r2", operator="AS",
                             reason="mapping_run")
    assert [(c["gate"], c["after"]) for c in changes] == [("A1B1_b", None)]
    log = store.recent_changes()
    assert len(log) == 3 and log[0]["run_id"] == "r2"   # newest first
    assert store.load_map()["entries"] == {"A1B1_a": a}


def test_draft_save_load_delete(tmp_path):
    store = gm.GateMapStore(str(tmp_path))
    run = _run()
    store.save_draft(run)
    assert store.load_draft()["run_id"] == run.run_id
    store.delete_draft()
    assert store.load_draft() is None


# ── Service ─────────────────────────────────────────────────────────────────

class _Timer:
    """threading.Timer stand-in: tests fire it by hand."""
    made: list = []

    def __init__(self, interval, fn, args=()):
        self.interval, self.fn, self.args = interval, fn, args
        self.cancelled = False
        _Timer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args)


class _Svc:
    def __init__(self, tmp_path, modules=("rfid_1",)):
        _Timer.made = []
        self.emitted = []
        self.sent = []
        self.now = 1000.0
        self.modules = list(modules)
        self.svc = GateMapService(
            gm.GateMapStore(str(tmp_path), clock=lambda: self.now),
            emit=lambda ev, data: self.emitted.append((ev, data)),
            send_command=lambda m, c, p: self.sent.append((m, c, p)),
            rfid_module_ids=lambda: self.modules,
            clock=lambda: self.now, timer=_Timer)

    def last(self, event):
        return [d for e, d in self.emitted if e == event][-1]

    def commands(self, name):
        return [(m, p) for m, c, p in self.sent if c == name]

    def read(self, unit, tag=PROBE, module="rfid_1"):
        self.svc.on_module_status(module, {
            "type": "rfid_read", "unit_address": unit,
            "transponder_id": tag, "packet_ts": self.now})


def test_service_full_run(tmp_path):
    t = _Svc(tmp_path)
    t.svc.start_run("AS", {"gates": ["A1B1_a", "A1B1_b"]})
    assert t.commands("rfid_scan") == [("rfid_1", {})]
    assert t.commands("rfid_mapping_mode")[-1][1]["enabled"] is True
    # The scan's follow-up asks for the unit list; the module answers.
    _Timer.made[0].fire()
    assert t.commands("rfid_units") == [("rfid_1", {})]
    t.svc.on_module_status("rfid_1", {
        "type": "cmd_ack", "command": "rfid_units",
        "units": [{"address": 34, "serial_number": "S34"},
                  {"address": 35, "serial_number": "S35"}]})
    assert [u["unit"] for u in t.last("gate_map_state")["bus"]] == [
        "rfid_1:34", "rfid_1:35"]

    t.read(9)                                  # registers the probe
    t.read(34, tag=ANIMAL)                     # an animal: ignored
    t.read(34)
    assert t.last("gate_map_hit")["probe"] is True
    _Timer.made[-1].fire()                     # settle window ends
    assert t.last("gate_map_state")["run"]["capture"]["settled"]
    t.svc.act("confirm")
    t.read(35)
    _Timer.made[-1].fire()
    t.svc.act("confirm")
    state = t.last("gate_map_state")
    assert state["run"]["phase"] == "review"
    assert state["run"]["review"]["on_bus_not_mapped"] == []
    assert len(state["run"]["changes"]) == 2

    t.svc.save_run()
    state = t.last("gate_map_state")
    assert state["run"] is None
    assert state["map"]["entries"]["A1B1_a"]["unit_serial"] == "S34"
    assert t.commands("rfid_mapping_mode")[-1][1]["enabled"] is False
    assert {c["gate"] for c in state["changes"]} == {"A1B1_a", "A1B1_b"}


def test_service_resumes_draft_after_restart(tmp_path):
    t = _Svc(tmp_path)
    t.svc.start_run("AS", {"gates": ["A1B1_a"]})
    t.read(9)
    t2 = _Svc(tmp_path)
    assert t2.svc.run is not None and t2.svc.run.probe_tag == PROBE
    t2.svc.discard_run()
    assert _Svc(tmp_path).svc.run is None


def test_service_refuses_second_run_and_layout_change_mid_run(tmp_path):
    t = _Svc(tmp_path)
    t.svc.start_run("AS", "all")
    with pytest.raises(gm.GateMapError):
        t.svc.start_run("XY", "all")
    with pytest.raises(gm.GateMapError):
        t.svc.save_layout(gm.generate_layout(2, 2))


def test_service_needs_an_rfid_module(tmp_path):
    t = _Svc(tmp_path, modules=())
    with pytest.raises(gm.GateMapError):
        t.svc.start_run("AS", "all")


def test_walkthrough_reports_mapped_gate(tmp_path):
    t = _Svc(tmp_path)
    t.svc.store.save_map({"A1B1_a": {"gate": "A1B1_a", "status": gm.MAPPED,
                                     "module_id": "rfid_1", "unit": 34}},
                         run_id=None, operator="AS", reason="manual_edit")
    t.svc.map = t.svc.store.load_map()
    t.read(34)
    assert not any(e == "gate_map_hit" for e, _ in t.emitted)   # off: silent
    t.svc.set_walkthrough(True)
    t.read(34, tag=ANIMAL)
    assert t.last("gate_map_hit")["gates"] == ["A1B1_a"]
    t.read(99)
    assert t.last("gate_map_hit")["gates"] == []
    t.svc.set_walkthrough(False)
    assert t.commands("rfid_mapping_mode")[-1][1]["enabled"] is False


def test_keepalive_renews_only_when_needed_and_due(tmp_path):
    t = _Svc(tmp_path)
    t.svc.keepalive()
    assert t.commands("rfid_mapping_mode") == []
    t.svc.set_walkthrough(True)
    n = len(t.commands("rfid_mapping_mode"))
    t.svc.keepalive()                         # just renewed: not due
    assert len(t.commands("rfid_mapping_mode")) == n
    t.now += 31
    t.svc.keepalive()
    assert len(t.commands("rfid_mapping_mode")) == n + 1


def test_live_view_forwards_every_read_until_it_lapses(tmp_path):
    t = _Svc(tmp_path)
    t.svc.store.save_map({"A1B1_a": {"gate": "A1B1_a", "status": gm.MAPPED,
                                     "module_id": "rfid_1", "unit": 34}},
                         run_id=None, operator="AS", reason="manual_edit")
    t.svc.map = t.svc.store.load_map()
    t.svc.live_keepalive()
    assert t.commands("rfid_mapping_mode")[-1][1]["enabled"] is True
    t.read(34, tag=ANIMAL)
    assert t.last("gate_map_hit")["gates"] == ["A1B1_a"]
    assert t.svc.state()["hits"] == []          # Gates page list untouched
    t.now += 61                                 # dashboard closed: lapses
    n = len([e for e, _ in t.emitted if e == "gate_map_hit"])
    t.read(34, tag=ANIMAL)
    assert len([e for e, _ in t.emitted if e == "gate_map_hit"]) == n


def test_ending_walkthrough_keeps_mapping_mode_for_live_dashboard(tmp_path):
    t = _Svc(tmp_path)
    t.svc.live_keepalive()
    t.svc.set_walkthrough(True)
    t.svc.set_walkthrough(False)
    assert t.commands("rfid_mapping_mode")[-1][1]["enabled"] is True
    t.now += 61
    t.svc.set_walkthrough(True)
    t.svc.set_walkthrough(False)
    assert t.commands("rfid_mapping_mode")[-1][1]["enabled"] is False


def test_orphaned_entries_after_unfitting_a_gate(tmp_path):
    t = _Svc(tmp_path)
    t.svc.store.save_map({"A1B1_a": {"gate": "A1B1_a", "status": gm.CLOSED}},
                         run_id=None, operator="AS", reason="manual_edit")
    t.svc.map = t.svc.store.load_map()
    lay = gm.generate_layout()
    lay["gates"][0]["fitted"] = False
    t.svc.save_layout(lay)
    assert t.last("gate_map_state")["orphaned"] == ["A1B1_a"]


# ── Socket.IO wiring (habitat_controller._register_gate_map_handlers) ───────

def test_socket_handlers_auth_and_errors(tmp_path):
    from unittest.mock import MagicMock

    from gate_map_service import register_socket_handlers, rfid_module_ids

    from src.controller.tests.test_web import _connected_client, _login, _make_web

    web = _make_web()
    web.facade = MagicMock()
    web.facade.get_modules.return_value = {
        "rfid_1": {"type": "rfid", "status": "READY"},
        "cam_1": {"type": "camera", "status": "READY"},
        "rfid_2": {"type": "rfid", "status": "OFFLINE"},
    }
    svc = GateMapService(
        gm.GateMapStore(str(tmp_path)), emit=web.socketio.emit,
        send_command=web.facade.send_command,
        rfid_module_ids=lambda: rfid_module_ids(web.facade.get_modules()),
        timer=_Timer)
    register_socket_handlers(web.socketio, svc, require_auth=web._require_auth,
                             logger=MagicMock())

    client = _connected_client(web)
    client.emit("get_gate_map")
    state = [e for e in client.get_received() if e["name"] == "gate_map_state"]
    assert state[0]["args"][0]["rfid_modules"] == ["rfid_1"]

    client.emit("gate_map_start_run", {"operator": "AS", "scope": "all"})
    err = [e for e in client.get_received() if e["name"] == "gate_map_error"]
    assert "Login required" in err[0]["args"][0]["error"]

    _login(web, client, str(tmp_path))
    client.emit("gate_map_start_run", {"operator": "", "scope": "all"})
    err = [e for e in client.get_received() if e["name"] == "gate_map_error"]
    assert "name or initials" in err[0]["args"][0]["error"]

    client.emit("gate_map_start_run", {"operator": "AS", "scope": "all"})
    states = [e for e in client.get_received() if e["name"] == "gate_map_state"]
    assert states[-1]["args"][0]["run"]["phase"] == "probe"
    web.facade.send_command.assert_any_call("rfid_1", "rfid_scan", {})
