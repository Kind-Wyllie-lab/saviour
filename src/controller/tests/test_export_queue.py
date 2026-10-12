"""
Tests for src/controller/export_queue.py

Covers retry-on-failure, give-up after MAX_RETRIES, concurrency cap, and
queue persistence across restarts.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

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
# Return value: callers (facade.enqueue_export) use this to decide whether
# to count a new "pending" export -- a signal dropped as a duplicate here
# must not be counted, or pending_exports drifts upward forever since no
# complete/failed ever arrives for the dropped duplicate.
# ---------------------------------------------------------------------------

class TestEnqueueReturnValue:
    def test_returns_true_when_actually_queued(self):
        q, _ = _make_queue()
        assert q.enqueue("mod_a", "path_a") is True

    def test_signal_while_active_schedules_one_follow_up(self):
        """The first signal while active schedules a follow-up (it gets its
        own complete later, so it counts); further ones are duplicates."""
        q, _ = _make_queue(max_concurrent=1)
        assert q.enqueue("mod_a", "path_a") is True
        assert q.enqueue("mod_a", "path_a_second_clip") is True
        assert q.enqueue("mod_a", "path_a_third_clip") is False

    def test_returns_false_when_already_queued(self):
        q, _ = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")  # dispatched, now active
        assert q.enqueue("mod_b", "path_b") is True  # held in queue
        assert q.enqueue("mod_b", "path_b_second_clip") is False  # still queued, duplicate


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

    def test_on_export_failed_return_value_signals_final_vs_retrying(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        for _ in range(ExportQueue.MAX_RETRIES - 1):
            assert q.on_export_failed("mod_a") is False  # still retrying
        assert q.on_export_failed("mod_a") is True  # retries exhausted

    def test_on_export_failed_without_metadata_returns_final(self):
        q, facade = _make_queue()
        assert q.on_export_failed("ghost_module") is True


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


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stale dispatch timeout
# ---------------------------------------------------------------------------

class TestStaleDispatch:
    def test_stale_active_entry_is_cleared_and_redispatched(self):
        """If a dispatched start_export is never acked within _STALE_DISPATCH_SECS,
        the next enqueue() for that module should clear the stale entry and
        re-dispatch immediately (treating it as a fresh slot)."""
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "path_a")
        assert facade.send_command.call_count == 1

        # Backdate the dispatch time to simulate a very stale entry
        with q._lock:
            path, attempt, _ = q._active_meta["mod_a"]
            q._active_meta["mod_a"] = (path, attempt, 0)  # epoch = ancient

        # Enqueueing again should detect stale, clear, and re-dispatch
        q.enqueue("mod_a", "path_a_new")
        assert facade.send_command.call_count == 2
        assert "mod_a" in q._active


class TestPersistence:
    def _make_queue_with_tmp_file(self, max_concurrent: int = 2):
        """Return (queue, facade, tmp_path) using a temp file for persistence."""
        cfg = MagicMock()
        cfg.get.return_value = max_concurrent
        q = ExportQueue(cfg)
        facade = MagicMock()
        q.facade = facade
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        return q, facade, tmp.name

    def test_pending_entries_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = os.path.join(tmpdir, "export_queue.json")
            with patch("src.controller.export_queue.QUEUE_FILE", queue_path):
                # First run: enqueue 3, only 2 dispatch (max_concurrent=2)
                q1, facade1 = _make_queue(max_concurrent=2)
                q1.enqueue("mod_a", "path_a")
                q1.enqueue("mod_b", "path_b")
                q1.enqueue("mod_c", "path_c")   # held in queue
                assert facade1.send_command.call_count == 2

                # Simulate restart: new queue loads from file
                q2, facade2 = _make_queue(max_concurrent=2)
                q2.start()

            # mod_a and mod_b were active → re-queued; mod_c was pending → re-queued
            # All 3 should now be dispatched (max_concurrent=2 means 2 fire immediately,
            # 1 held until a slot opens — but we just want all 3 present)
            dispatched = {c[0][0] for c in facade2.send_command.call_args_list}
            assert "mod_c" in dispatched

    def test_active_entries_requeued_on_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = os.path.join(tmpdir, "export_queue.json")
            with patch("src.controller.export_queue.QUEUE_FILE", queue_path):
                q1, facade1 = _make_queue(max_concurrent=2)
                q1.enqueue("mod_a", "path_a")
                q1.enqueue("mod_b", "path_b")
                # Both are active (dispatched) but no acks received before restart

                q2, facade2 = _make_queue(max_concurrent=2)
                q2.start()

            dispatched = {c[0][0] for c in facade2.send_command.call_args_list}
            assert "mod_a" in dispatched
            assert "mod_b" in dispatched

    def test_completed_entries_not_requeued(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = os.path.join(tmpdir, "export_queue.json")
            with patch("src.controller.export_queue.QUEUE_FILE", queue_path):
                q1, facade1 = _make_queue(max_concurrent=2)
                q1.enqueue("mod_a", "path_a")
                q1.on_export_complete("mod_a")   # completes cleanly

                q2, facade2 = _make_queue(max_concurrent=2)
                q2.start()

            # Nothing should be re-dispatched after restart
            assert facade2.send_command.call_count == 0

    def test_empty_queue_file_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = os.path.join(tmpdir, "export_queue.json")
            # Write corrupt JSON
            with open(queue_path, "w") as f:
                f.write("not json {{{")
            with patch("src.controller.export_queue.QUEUE_FILE", queue_path):
                q, facade = _make_queue()
                q.start()   # should not raise
            assert facade.send_command.call_count == 0

    def test_missing_queue_file_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = os.path.join(tmpdir, "nonexistent.json")
            with patch("src.controller.export_queue.QUEUE_FILE", queue_path):
                q, facade = _make_queue()
                q.start()   # should not raise
            assert facade.send_command.call_count == 0


# ---------------------------------------------------------------------------
# Follow-up export for files staged while a module's export was running
# (2026-10-02: a session stop landed 2 s into a rotation export; the final
# segment was staged after export_staged had listed to_export/, the second
# export_ready was dropped as a duplicate, and the file stayed on the module
# while the session reported every export complete.)
# ---------------------------------------------------------------------------

class TestFollowUpWhenBusy:
    def test_signal_during_export_runs_again_after_complete(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "s/20261002/cam")
        q.enqueue("mod_a", "s/20261002/cam")       # stop staged more files
        assert facade.send_command.call_count == 1
        q.on_export_complete("mod_a")
        assert facade.send_command.call_count == 2
        assert facade.send_command.call_args[0] == (
            "mod_a", "start_export", {"export_path": "s/20261002/cam"})
        q.on_export_complete("mod_a")
        assert facade.send_command.call_count == 2  # no third pass

    def test_follow_up_waits_behind_its_own_retry(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "p")
        q.enqueue("mod_a", "p")
        assert q.on_export_failed("mod_a") is False   # retry queued
        assert facade.send_command.call_count == 2    # the retry
        q.on_export_complete("mod_a")                 # retry done
        assert facade.send_command.call_count == 3    # then the follow-up
        q.on_export_complete("mod_a")
        assert facade.send_command.call_count == 3

    def test_follow_up_still_runs_after_final_failure(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("mod_a", "p")
        q.enqueue("mod_a", "p")
        for _ in range(ExportQueue.MAX_RETRIES - 1):
            q.on_export_failed("mod_a")
        calls = facade.send_command.call_count
        assert q.on_export_failed("mod_a") is True    # gave up on this pass
        assert facade.send_command.call_count == calls + 1  # follow-up runs


class TestLostDispatchAfterReboot:
    """Test D (2026-10-06): a start_export lost in a module's hard reboot kept
    the module 'active' for 15 min and leaked a pending export."""

    def test_back_online_redispatches_the_unanswered_export(self):
        q, facade = _make_queue(max_concurrent=1)
        q.enqueue("cam", "s/20261006/camera")          # dispatched, then reboot
        q.enqueue("cam", "s/20261006/camera")          # salvage signal -> follow-up
        q.module_back_online("cam")
        assert facade.send_command.call_count == 2      # re-dispatched at once
        q.on_export_complete("cam")                     # re-dispatched pass
        q.on_export_complete("cam")                     # follow-up pass
        assert facade.send_command.call_count == 3
        assert "cam" not in q._active and not q._queue  # nothing left hanging

    def test_back_online_with_nothing_active_is_a_noop(self):
        q, facade = _make_queue()
        q.module_back_online("cam")
        facade.send_command.assert_not_called()
