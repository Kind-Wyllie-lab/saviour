"""
Tests for src/controller/export_queue.py

Covers retry-on-failure, give-up after MAX_RETRIES, and concurrency cap.
"""

from unittest.mock import MagicMock, call
from src.controller.export_queue import ExportQueue


def _make_queue(max_concurrent: int = 2) -> tuple:
    """Return (queue, mock_facade). Facade records send_command calls."""
    cfg = MagicMock()
    cfg.get.return_value = max_concurrent
    q = ExportQueue(cfg)
    facade = MagicMock()
    q.facade = facade
    return q, facade


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestEnqueueAndDispatch:
    def test_enqueue_dispatches_immediately_when_under_limit(self):
        q, facade = _make_queue(max_concurrent=2)
        q.enqueue("mod_a", "session/2026/mod_a")
        facade.send_command.assert_called_once_with(
            "mod_a", "start_export", {"export_path": "session/2026/mod_a"}
        )

    def test_second_export_held_when_at_limit(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        q.enqueue("mod_b", "path_b")
        # Only mod_a dispatched; mod_b is queued
        assert facade.send_command.call_count == 1
        assert facade.send_command.call_args[0][0] == "mod_a"

    def test_complete_releases_slot_and_dispatches_next(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        q.enqueue("mod_b", "path_b")
        q.on_export_complete("mod_a")
        assert facade.send_command.call_count == 2
        assert facade.send_command.call_args[0][0] == "mod_b"

    def test_complete_does_not_reenqueue(self):
        q, facade = _make_queue()
        q.enqueue("mod_a", "path_a")
        q.on_export_complete("mod_a")
        # Only the initial dispatch, no re-dispatch
        assert facade.send_command.call_count == 1


# ---------------------------------------------------------------------------
# Retry on failure
# ---------------------------------------------------------------------------

class TestRetryOnFailure:
    def test_first_failure_reenqueues(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        q.enqueue("mod_b", "path_b")         # held in queue
        q.on_export_failed("mod_a")
        # mod_a re-queued; but mod_b was next in line — depends on queue order
        # Both mod_b and the retry of mod_a should eventually dispatch.
        # After the failure, either mod_b or mod_a retry dispatches.
        assert facade.send_command.call_count == 2

    def test_second_failure_reenqueues_again(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        q.on_export_failed("mod_a")   # attempt 1 → re-queue as attempt 2
        q.on_export_failed("mod_a")   # attempt 2 → re-queue as attempt 3
        # Three dispatches so far (initial + 2 retries)
        assert facade.send_command.call_count == 3

    def test_gives_up_after_max_retries(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        for _ in range(ExportQueue.MAX_RETRIES - 1):
            q.on_export_failed("mod_a")
        # All retries exhausted; one more failure should NOT dispatch again
        q.on_export_failed("mod_a")
        assert facade.send_command.call_count == ExportQueue.MAX_RETRIES
        # Nothing left in queue or active
        assert len(q._queue) == 0
        assert len(q._active) == 0

    def test_retry_uses_same_export_path(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "my/path")
        q.on_export_failed("mod_a")
        # Both calls (original + retry) use the same path
        for c in facade.send_command.call_args_list:
            assert c[0][2] == {"export_path": "my/path"}

    def test_failed_without_metadata_does_not_crash(self):
        q, facade = _make_queue()
        # Call on_export_failed for a module that was never enqueued
        q.on_export_failed("ghost_module")  # should not raise


# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------

class TestConcurrencyCap:
    def test_max_concurrent_respected(self):
        q, facade = _make_queue(max_concurrent=2)
        for i in range(5):
            q.enqueue(f"mod_{i}", f"path_{i}")
        assert len(q._active) == 2
        assert len(q._queue) == 3

    def test_slots_refilled_as_exports_complete(self):
        q, facade = _make_queue(max_concurrent=2)
        for i in range(4):
            q.enqueue(f"mod_{i}", f"path_{i}")
        q.on_export_complete("mod_0")
        q.on_export_complete("mod_1")
        # All 4 should have been dispatched by now
        assert facade.send_command.call_count == 4
