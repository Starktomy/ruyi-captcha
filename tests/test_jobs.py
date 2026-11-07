import threading
import time
import unittest

from ruyi_captcha.errors import CaptchaError, SolveCancelled
from ruyi_captcha.jobs import IdempotencyConflict, JobManager, QueueFull, TaskNotFound

PARAMS = {"type": "turnstile", "site_url": "https://example.test/", "sitekey": "0x4AAAAAADogn3t3_JKwKkgS"}
RESULT = {"token": "offline-fixture-" + "x" * 240, "elapsed": 0.01, "attempts": 1}


def wait_for(manager, task_id, statuses=("succeeded", "failed", "cancelled")):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = manager.get(task_id)
        if state["status"] in statuses:
            return state
        time.sleep(0.005)
    raise AssertionError("Task did not reach expected state: " + repr(state))


class JobTests(unittest.TestCase):
    def manager(self, solver, **settings):
        manager = JobManager(solver, **settings)
        self.addCleanup(manager.close)
        return manager

    def test_success_is_acquisition_and_does_not_expose_input_credentials(self):
        manager = self.manager(lambda p, c: RESULT)
        job = manager.submit({**PARAMS, "proxy": "http://user:password@proxy.test:8000"})
        result = wait_for(manager, job["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["verification_status"], "token_obtained")
        self.assertNotIn("parameters", result)
        self.assertNotIn("password", repr(result))

    def test_provider_failure_preserves_error_code(self):
        def fail(parameters, cancel):
            raise CaptchaError("Challenge timed out", code="timeout")
        manager = self.manager(fail)
        job = wait_for(manager, manager.submit(PARAMS)["id"])
        self.assertEqual(job["error"], {"code": "timeout", "message": "Challenge timed out"})
        self.assertIsNone(job["result"])

    def test_cancelled_queued_work_still_bounds_actual_executor_queue(self):
        entered, release = threading.Event(), threading.Event()
        def block(parameters, cancel):
            entered.set()
            release.wait(3)
            return RESULT
        manager = self.manager(block, concurrency=1, queue_size=1)
        self.addCleanup(release.set)
        first = manager.submit(PARAMS)
        self.assertTrue(entered.wait(1))
        queued = manager.submit(PARAMS)
        self.assertEqual(manager.cancel(queued["id"])["status"], "cancelled")
        for _ in range(10):
            with self.assertRaises(QueueFull):
                manager.submit(PARAMS)
        release.set()
        wait_for(manager, first["id"])
        deadline = time.monotonic() + 1
        while manager._inflight and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(wait_for(manager, manager.submit(PARAMS)["id"])["status"], "succeeded")

    def test_running_cancellation_drops_a_late_token(self):
        entered, release = threading.Event(), threading.Event()
        def block(parameters, cancel):
            entered.set()
            release.wait(3)
            return RESULT
        manager = self.manager(block, concurrency=1)
        self.addCleanup(release.set)
        job = manager.submit(PARAMS)
        self.assertTrue(entered.wait(1))
        self.assertEqual(manager.cancel(job["id"])["status"], "cancelling")
        release.set()
        result = wait_for(manager, job["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["result"])

    def test_idempotency_reuses_job_and_rejects_different_parameters(self):
        calls = []
        def run(parameters, cancel):
            calls.append(parameters)
            return RESULT
        manager = self.manager(run)
        first = manager.submit(PARAMS, "request-1")
        second = manager.submit(dict(PARAMS), "request-1")
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(IdempotencyConflict):
            manager.submit({**PARAMS, "action": "changed"}, "request-1")
        wait_for(manager, first["id"])
        self.assertEqual(len(calls), 1)

    def test_queue_deadline_prevents_late_execution(self):
        now, calls = [0.0], []
        entered, release = threading.Event(), threading.Event()
        def run(parameters, cancel):
            calls.append(parameters)
            entered.set()
            release.wait(3)
            return RESULT
        manager = self.manager(run, concurrency=1, queue_size=1, queue_timeout=10, clock=lambda: now[0])
        self.addCleanup(release.set)
        first = manager.submit(PARAMS)
        self.assertTrue(entered.wait(1))
        second = manager.submit(PARAMS)
        now[0] = 11
        self.assertEqual(manager.get(second["id"])["error"]["code"], "queue_timeout")
        release.set()
        wait_for(manager, first["id"])
        expired = wait_for(manager, second["id"])
        self.assertEqual(expired["error"]["code"], "queue_timeout")
        self.assertEqual(len(calls), 1)

    def test_result_expiry_removes_idempotency_key(self):
        now = [0.0]
        manager = self.manager(lambda p, c: RESULT, result_ttl=10, clock=lambda: now[0])
        first = manager.submit(PARAMS, "key")
        wait_for(manager, first["id"])
        now[0] = 11
        with self.assertRaises(TaskNotFound):
            manager.get(first["id"])
        self.assertNotEqual(manager.submit(PARAMS, "key")["id"], first["id"])

    def test_worker_concurrency_and_pending_capacity(self):
        lock, release = threading.Lock(), threading.Event()
        counts = {"active": 0, "peak": 0}
        def run(parameters, cancel):
            with lock:
                counts["active"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])
            release.wait(3)
            with lock:
                counts["active"] -= 1
            return RESULT
        manager = self.manager(run, concurrency=2, queue_size=2)
        self.addCleanup(release.set)
        jobs = [manager.submit(PARAMS) for _ in range(4)]
        with self.assertRaises(QueueFull):
            manager.submit(PARAMS)
        release.set()
        for job in jobs:
            wait_for(manager, job["id"])
        self.assertLessEqual(counts["peak"], 2)

    def test_close_cancels_cooperative_work_and_refuses_submissions(self):
        entered = threading.Event()
        def run(parameters, cancel):
            entered.set()
            cancel.wait(3)
            raise SolveCancelled()
        manager = self.manager(run, concurrency=1)
        job = manager.submit(PARAMS)
        self.assertTrue(entered.wait(1))
        manager.close()
        self.assertEqual(manager.get(job["id"])["status"], "cancelled")
        with self.assertRaises(CaptchaError):
            manager.submit(PARAMS)

    def test_completed_storage_is_bounded(self):
        manager = self.manager(lambda p, c: RESULT, concurrency=1, queue_size=0, retained_results=1)
        first = manager.submit(PARAMS)
        wait_for(manager, first["id"])
        second = manager.submit(PARAMS)
        wait_for(manager, second["id"])
        third = manager.submit(PARAMS)
        wait_for(manager, third["id"])
        with self.assertRaises(TaskNotFound):
            manager.get(first["id"])


if __name__ == "__main__":
    unittest.main()
