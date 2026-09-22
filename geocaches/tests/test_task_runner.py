"""Tests for the background task runner — result handling around cancellation.

Regression coverage for a bug where cancel_task() flipping state to CANCELLED
caused the wrapper to silently drop whatever fn() returned if it happened to
finish (fully or partially) just after the cancel request went out — leaving
callers with no data even though the work had actually completed.
"""

import threading
import time

from django.test import TestCase, override_settings

from geocaches.tasks import runner


class CancelRaceTest(TestCase):
    def _submit_via_executor(self, fn):
        """Submit through the real background-executor path.

        The test settings force TASKS_RUN_SYNC=True (tasks run inline) so the
        in-memory test DB isn't raced by executor threads — but that also runs
        fn() to completion before submit_task() returns, making it impossible
        to call cancel_task() while a task is actually RUNNING. Override it
        for this test, which needs the real race.
        """
        with override_settings(TASKS_RUN_SYNC=False):
            return runner.submit_task("test task", fn)

    def test_result_preserved_when_fn_finishes_after_cancel(self):
        started = threading.Event()
        release = threading.Event()

        def fn(task_info=None):
            started.set()
            release.wait(timeout=5)
            return {"caches": [1, 2, 3]}

        task_id = self._submit_via_executor(fn)
        self.assertTrue(started.wait(timeout=5))
        self.assertTrue(runner.cancel_task(task_id))

        info = runner.get_task(task_id)
        self.assertEqual(info["state"], "cancelled")
        # Set immediately by cancel_task(), not left dangling until fn() exits.
        self.assertIsNotNone(info["completed_at"])

        release.set()  # let fn() finish "too late", after the cancel request
        for _ in range(50):
            info = runner.get_task(task_id)
            if info["result"] is not None:
                break
            time.sleep(0.05)

        # Cancellation intent is preserved (state stays "cancelled")...
        self.assertEqual(info["state"], "cancelled")
        # ...but the data fn() actually produced isn't discarded.
        self.assertEqual(info["result"], {"caches": [1, 2, 3]})

    def test_cancel_returns_false_for_unknown_or_finished_task(self):
        self.assertFalse(runner.cancel_task("does-not-exist"))
