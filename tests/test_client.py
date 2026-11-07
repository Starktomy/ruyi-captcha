import io
import json
import unittest
from unittest.mock import patch
import urllib.error

from ruyi_captcha import ApiClient, CaptchaError


class ClientTests(unittest.TestCase):
    def test_unauthorised_error_preserves_server_error_code(self):
        body = io.BytesIO(json.dumps({"detail": {"code": "unauthorised", "message": "Key required"}}).encode())
        error = urllib.error.HTTPError("http://api.test/v1/tasks", 401, "Unauthorized", {}, body)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(CaptchaError) as caught:
                ApiClient("http://api.test").submit("https://site.test", "key")
        self.assertEqual(caught.exception.code, "unauthorised")

    def test_transport_failure_has_a_distinct_code(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with self.assertRaises(CaptchaError) as caught:
                ApiClient("http://api.test").get("task")
        self.assertEqual(caught.exception.code, "transport_error")

    def test_failed_and_cancelled_remote_tasks_raise(self):
        client = ApiClient("http://api.test")
        for state, error in (("failed", {"code": "timeout", "message": "timed out"}), ("cancelled", None)):
            with self.subTest(state=state), patch.object(client, "submit", return_value={"id": "x", "status": state, "error": error}):
                with self.assertRaises(CaptchaError) as caught:
                    client.solve_turnstile("https://site.test", "key")
                self.assertEqual(caught.exception.code, "timeout" if state == "failed" else "cancelled")

    def test_wait_deadline_survives_a_failed_cleanup_request(self):
        client = ApiClient("http://api.test")
        with patch.object(client, "submit", return_value={"id": "x", "status": "running"}), \
             patch.object(client, "cancel", side_effect=CaptchaError("offline")) as cancel, \
             patch("ruyi_captcha.client.time.monotonic", side_effect=[0, 2]):
            with self.assertRaises(CaptchaError) as caught:
                client.solve_turnstile("https://site.test", "key", wait_timeout=1)
        self.assertEqual(caught.exception.code, "client_timeout")
        cancel.assert_called_once()

    def test_completed_remote_result_returns_a_token(self):
        client = ApiClient("http://api.test")
        with patch.object(client, "submit", return_value={"id": "x", "status": "succeeded", "result": {"token": "offline-fixture"}}):
            self.assertEqual(client.solve_turnstile(surl="https://site.test", sitekey="key"), "offline-fixture")

    def test_nonfinite_wait_and_http_timeouts_are_rejected(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ApiClient("http://api.test", request_timeout=value)
            with self.subTest(wait=value), self.assertRaises(ValueError):
                ApiClient("http://api.test").solve_turnstile("https://site.test", "key", wait_timeout=value)


if __name__ == "__main__":
    unittest.main()
