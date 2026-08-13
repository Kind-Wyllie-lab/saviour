"""
Tests for src/modules/variants/loom_camera/loom_stimulus.py.

Covers LoomBatchRunState (a pure enter/leave/round-completion state machine)
and LoomStimulusController's process-lifecycle orchestration. The renderer
itself (run_loom_stimulus_with_ipc, loom_stimulus_process_main) needs a
real GLFW/OpenGL context and is out of scope -- LoomStimulusController is
tested with multiprocessing.Process mocked (so no real subprocess/GL window
ever spawns) but real multiprocessing.Queue objects, since those are cheap
and let the tests verify actual put/get behaviour rather than a mock of it.
"""

import time
from unittest.mock import MagicMock, patch

from src.modules.variants.loom_camera.loom_stimulus import (
    LoomBatchRunState,
    LoomStimulusConfig,
    LoomStimulusController,
)


def _make_config(**overrides) -> LoomStimulusConfig:
    defaults = dict(
        texture_path="/tmp/loom.png",
        initial_size_cm=2.0,
        final_size_cm=40.0,
        initial_pos_ndc=(0.0, 0.0),
        final_pos_ndc=(0.0, 0.0),
        travel_time_s=0.25,
        loom_wait_time_s=1.0,
        round_size=5,
        image_angle_deg=0.0,
        background_rgba=(0.0, 0.0, 0.0, 1.0),
    )
    defaults.update(overrides)
    return LoomStimulusConfig(**defaults)


# ---------------------------------------------------------------------------
# LoomBatchRunState
# ---------------------------------------------------------------------------

class TestLoomBatchRunState:
    def test_on_enter_activates_and_clears_stop_flag(self):
        state = LoomBatchRunState(active=False, stop_after_current_round=True)
        state.on_enter()
        assert state.active is True
        assert state.stop_after_current_round is False

    def test_on_leave_while_active_requests_stop_after_round(self):
        state = LoomBatchRunState(active=True)
        state.on_leave()
        assert state.stop_after_current_round is True
        assert state.active is True  # doesn't stop immediately

    def test_on_leave_while_inactive_is_a_no_op(self):
        state = LoomBatchRunState(active=False)
        state.on_leave()
        assert state.stop_after_current_round is False

    def test_round_completion_tracking(self):
        state = LoomBatchRunState(round_size=3)
        assert state.current_round_completed() is False
        for _ in range(3):
            state.on_completed_round_trip()
        assert state.current_round_completed() is True
        state.reset_round()
        assert state.current_round_completed() is False
        assert state.round_trip_counter_in_round == 0

    def test_should_start_next_round_requires_active_and_not_stopping(self):
        state = LoomBatchRunState(active=True, stop_after_current_round=False)
        assert state.should_start_next_round() is True

        state.stop_after_current_round = True
        assert state.should_start_next_round() is False

        state.active = False
        state.stop_after_current_round = False
        assert state.should_start_next_round() is False

    def test_stop_now_resets_everything(self):
        state = LoomBatchRunState(
            active=True, stop_after_current_round=True, round_trip_counter_in_round=2
        )
        state.stop_now()
        assert state.active is False
        assert state.stop_after_current_round is False
        assert state.round_trip_counter_in_round == 0


# ---------------------------------------------------------------------------
# LoomStimulusController
# ---------------------------------------------------------------------------

class TestLoomStimulusControllerStart:
    def test_start_launches_a_daemon_process_with_expected_target(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)

        with patch(
            "src.modules.variants.loom_camera.loom_stimulus.mp.Process"
        ) as mock_process_cls:
            mock_proc = MagicMock(is_alive=MagicMock(return_value=False))
            mock_process_cls.return_value = mock_proc

            controller.start()

            mock_process_cls.assert_called_once()
            kwargs = mock_process_cls.call_args.kwargs
            assert kwargs["daemon"] is True
            assert kwargs["args"] == (controller._cmd_q, controller._status_q, cfg)
            mock_proc.start.assert_called_once()

    def test_start_is_a_no_op_when_already_alive(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        controller._proc = MagicMock(is_alive=MagicMock(return_value=True))

        with patch(
            "src.modules.variants.loom_camera.loom_stimulus.mp.Process"
        ) as mock_process_cls:
            controller.start()
            mock_process_cls.assert_not_called()


class TestLoomStimulusControllerSend:
    def test_send_starts_the_process_and_queues_the_command(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)

        with patch.object(controller, "start") as mock_start:
            controller.send("start", {"trial": 1})
            mock_start.assert_called_once()

        assert controller._cmd_q.get(timeout=1) == ("start", {"trial": 1})

    def test_send_defaults_payload_to_empty_dict(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)

        with patch.object(controller, "start"):
            controller.send("ping")

        assert controller._cmd_q.get(timeout=1) == ("ping", {})


class TestLoomStimulusControllerReconfigure:
    def test_no_op_when_process_never_started(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        controller.reconfigure({"final_size_cm": 50.0})
        assert controller._cmd_q.empty()

    def test_no_op_when_process_died(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        controller._proc = MagicMock(is_alive=MagicMock(return_value=False))
        controller.reconfigure({"final_size_cm": 50.0})
        assert controller._cmd_q.empty()

    def test_queues_reconfigure_command_when_alive(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        controller._proc = MagicMock(is_alive=MagicMock(return_value=True))

        controller.reconfigure({"final_size_cm": 50.0})

        expected = ("reconfigure", {"final_size_cm": 50.0})
        assert controller._cmd_q.get(timeout=1) == expected


class TestLoomStimulusControllerPollStatus:
    def test_drains_up_to_max_messages(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        for i in range(3):
            controller._status_q.put({"seq": i})
        # multiprocessing.Queue.put() hands off to a background feeder
        # thread rather than making the item visible to get_nowait()
        # immediately -- without this, poll_status() below can race and
        # see an empty queue under system load.
        time.sleep(0.2)

        messages = controller.poll_status(max_messages=2)

        assert len(messages) == 2
        assert messages[0]["seq"] == 0
        assert messages[1]["seq"] == 1

    def test_returns_empty_list_when_no_messages_pending(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        assert controller.poll_status() == []


class TestLoomStimulusControllerShutdown:
    def test_no_op_when_never_started(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        controller.shutdown()  # must not raise

    def test_joins_cleanly_when_process_exits_promptly(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        proc = MagicMock(is_alive=MagicMock(return_value=False))
        controller._proc = proc

        # shutdown()'s finally clause sets self._proc = None, so capture the
        # mock reference above and assert against that, not controller._proc.
        with patch.object(controller, "send") as mock_send:
            controller.shutdown(timeout_s=1.0)

        mock_send.assert_called_once_with("shutdown")
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_escalates_to_terminate_then_kill_if_still_alive(self):
        cfg = _make_config()
        controller = LoomStimulusController(cfg)
        # Reports alive on every check -- forces both escalation steps.
        proc = MagicMock(is_alive=MagicMock(return_value=True))
        controller._proc = proc

        with patch.object(controller, "send"):
            controller.shutdown(timeout_s=0.01)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
