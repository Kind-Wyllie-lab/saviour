"""
Tests for the loom crossing-zone state machine in
src/modules/examples/loom_camera/loom_camera_module.py

Covers the enter-confirm-frames debounce: a real, sustained entry must
still fire 'enter', while a transient blob (a hand withdrawing, a shadow,
a lighting flicker) that only touches the 'in' side for a frame or two
must not.
"""

from src.modules.examples.loom_camera.loom_camera_module import (
    LoomCrossingState,
    loom_update_crossing_state,
)

LINE = {"kind": "vertical", "x": 100.0, "direction": "left_is_in"}
IN_POINT = (50.0, 0.0)    # left of the line -> 'in'
OUT_POINT = (150.0, 0.0)  # right of the line -> 'out'


def _step(prev, center, *, confirm_frames=1, track_valid=True):
    return loom_update_crossing_state(
        crossing_line_src=LINE,
        center_src=center,
        prev=prev,
        track_valid=track_valid,
        enter_confirm_frames=confirm_frames,
    )


def test_default_confirm_frames_of_one_fires_immediately():
    """enter_confirm_frames=1 reproduces the old undebounced behavior."""
    state = LoomCrossingState()
    state = _step(state, IN_POINT, confirm_frames=1)
    assert state.last_event == "enter"
    assert state.in_zone_prev is True


def test_single_frame_blip_does_not_fire_with_debounce():
    """A one-frame crossing (e.g. a hand withdrawing) must not fire 'enter'
    when a confirm window is configured, and must not leave any state behind
    once the raw position returns to 'out'."""
    state = LoomCrossingState()
    state = _step(state, IN_POINT, confirm_frames=3)
    assert state.last_event is None
    assert state.in_zone_prev is False
    assert state.pending_frames == 1

    # blip ends — raw position back on the 'out' side before confirming
    state = _step(state, OUT_POINT, confirm_frames=3)
    assert state.last_event is None
    assert state.in_zone_prev is False
    assert state.pending_frames == 0
    assert state.state == "out"


def test_sustained_entry_fires_after_n_frames():
    """A real, sustained entry still fires 'enter', just on the Nth frame."""
    state = LoomCrossingState()
    confirm_frames = 3
    for _ in range(confirm_frames - 1):
        state = _step(state, IN_POINT, confirm_frames=confirm_frames)
        assert state.last_event is None
        assert state.in_zone_prev is False

    state = _step(state, IN_POINT, confirm_frames=confirm_frames)
    assert state.last_event == "enter"
    assert state.in_zone_prev is True
    assert state.pending_frames == 0


def test_leave_fires_immediately_no_debounce():
    """Leaving is not debounced — once confirmed present, the very next
    frame off the 'in' side fires 'leave' immediately."""
    state = LoomCrossingState(in_zone_prev=True, state="in")
    state = _step(state, OUT_POINT, confirm_frames=5)
    assert state.last_event == "leave"
    assert state.in_zone_prev is False


def test_track_invalid_forces_out_and_clears_pending():
    state = LoomCrossingState(in_zone_prev=False, state="entering", pending_frames=2)
    state = _step(state, IN_POINT, confirm_frames=3, track_valid=False)
    assert state.state == "out"
    assert state.in_zone_prev is False
    assert state.pending_frames == 0
    assert state.last_event is None
