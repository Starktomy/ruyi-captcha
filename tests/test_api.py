import threading
import time
import unittest

from fastapi.testclient import TestClient

from ruyi_captcha.api import Settings, create_app
from ruyi_captcha.errors import CaptchaError, RuntimeUnavailable, SolveCancelled

PARAMS = {"type": "turnstile", "site_url": "https://example.test/", "sitekey": "0x4AAAAAADogn3t3_JKwKkgS"}
RESULT = {"token": "offline-api-" + "x" * 240, "elapsed": 0.01, "attempts": 1}
AUTH = {"Authorization": "Bearer test-key"}


class ApiTests(unittest.TestCase):
    def client(self, solver=None, **settings):
        app = create_app(Settings(api_key="test-key", **settings),
                         solver=solver or (lambda p, c: RESULT), runtime_probe=lambda: {"compatible": True})
        client = TestClient(app)
        self.addCleanup(client.__exit__, None, None, None)
        client.__enter__()
        return client

    def wait(self, client, task_id):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = client.get("/v1/tasks/" + task_id, headers=AUTH).json()
            if job["status"] in ("succeeded", "failed", "cancelled"):
                return job
            time.sleep(0.005)
        self.fail("HTTP task did not complete")

    def test_health_is_public_and_data_endpoints_require_auth(self):
        client = self.client()
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/v1/runtime").status_code, 401)
        for path in ("/v1/tasks", "/v1/solve"):
            self.assertEqual(client.post(path, json=PARAMS).status_code, 401)
        self.assertEqual(client.get("/v1/tasks/unknown").status_code, 401)
        self.assertEqual(client.delete("/v1/tasks/unknown").status_code, 401)
        self.assertEqual(client.get("/v1/runtime", headers=AUTH).json(), {"compatible": True})

    def test_sync_result_contains_token_but_not_backend_success_claim(self):
        client = self.client()
        response = client.post("/v1/solve", headers=AUTH, json=PARAMS)
        self.assertEqual(response.status_code, 200)
        job = response.json()
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["token"], RESULT["token"])
        self.assertEqual(job["result"]["verification_status"], "token_obtained")
        self.assertNotIn("backend_verified", job["result"])

    def test_async_submission_accepts_surl_alias(self):
        client = self.client()
        params = {"surl": PARAMS["site_url"], "sitekey": PARAMS["sitekey"]}
        response = client.post("/v1/tasks", headers=AUTH, json=params)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.wait(client, response.json()["id"])["status"], "succeeded")

    def test_sync_wait_timeout_returns_pollable_202(self):
        release = threading.Event()
        def run(parameters, cancel):
            while not release.wait(0.005):
                if cancel.is_set():
                    raise SolveCancelled()
            return RESULT
        client = self.client(run)
        self.addCleanup(release.set)
        response = client.post("/v1/solve?wait_timeout=0", headers=AUTH, json=PARAMS)
        self.assertEqual(response.status_code, 202)
        release.set()
        self.assertEqual(self.wait(client, response.json()["id"])["status"], "succeeded")

    def test_validation_rejects_unsupported_or_unsafe_parameter_shapes(self):
        calls = []
        client = self.client(lambda p, c: calls.append(p) or RESULT)
        cases = ({"site_url": "ftp://example.test"}, {"site_url": "https://u:p@example.test"},
                 {"site_url": "https://example.test:bad/"},
                 {"sitekey": "short"}, {"cdata": "contains spaces"}, {"action": ""},
                 {"timeout": 0}, {"timeout": True}, {"attempts": False}, {"attempts": 4},
                 {"type": "not-implemented"}, {"proxy": "socks5://proxy.test:1000"},
                 {"shell": "/bin/anything"})
        for invalid in cases:
            with self.subTest(invalid=invalid):
                response = client.post("/v1/tasks", headers=AUTH, json={**PARAMS, **invalid})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(calls, [])

    def test_idempotency_header_reuses_and_conflicts(self):
        client = self.client()
        headers = {**AUTH, "Idempotency-Key": "one-request"}
        first = client.post("/v1/tasks", headers=headers, json=PARAMS).json()
        second = client.post("/v1/tasks", headers=headers, json=PARAMS).json()
        self.assertEqual(first["id"], second["id"])
        conflict = client.post("/v1/tasks", headers=headers, json={**PARAMS, "action": "different"})
        self.assertEqual(conflict.status_code, 409)

    def test_capacity_returns_429_and_cancel_is_observable(self):
        entered = threading.Event()
        def run(parameters, cancel):
            entered.set()
            cancel.wait(2)
            raise SolveCancelled()
        client = self.client(run, concurrency=1, queue_size=0)
        first = client.post("/v1/tasks", headers=AUTH, json=PARAMS).json()
        self.assertTrue(entered.wait(1))
        full = client.post("/v1/tasks", headers=AUTH, json=PARAMS)
        self.assertEqual(full.status_code, 429)
        self.assertEqual(full.headers["retry-after"], "2")
        cancel = client.delete("/v1/tasks/" + first["id"], headers=AUTH)
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(self.wait(client, first["id"])["status"], "cancelled")

    def test_failure_is_a_task_outcome_with_error_code(self):
        def fail(parameters, cancel):
            raise CaptchaError("Challenge timed out", code="timeout")
        client = self.client(fail)
        response = client.post("/v1/solve", headers=AUTH, json=PARAMS)
        self.assertEqual(response.status_code, 200)
        job = response.json()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "timeout")

    def test_unknown_and_expired_tasks_return_404(self):
        now = [0.0]
        client = self.client(result_ttl=1)
        client.app.state.manager.clock = lambda: now[0]
        self.assertEqual(client.get("/v1/tasks/unknown", headers=AUTH).status_code, 404)
        job = client.post("/v1/tasks", headers=AUTH, json=PARAMS).json()
        self.wait(client, job["id"])
        now[0] = 2
        self.assertEqual(client.get("/v1/tasks/" + job["id"], headers=AUTH).status_code, 404)

    def test_nonfinite_profile_is_a_validation_error(self):
        client = self.client()
        body = '{"site_url":"https://example.test/","sitekey":"0x4AAAAAADogn3t3_JKwKkgS","profile":{"screen":{"width":NaN}}}'
        response = client.post("/v1/tasks", headers={**AUTH, "Content-Type": "application/json"}, content=body)
        self.assertEqual(response.status_code, 422)

    def test_runtime_failure_prevents_service_start(self):
        def missing():
            raise RuntimeUnavailable("Kernel missing")
        app = create_app(runtime_probe=missing)
        with self.assertRaises(RuntimeUnavailable):
            with TestClient(app):
                pass


if __name__ == "__main__":
    unittest.main()
