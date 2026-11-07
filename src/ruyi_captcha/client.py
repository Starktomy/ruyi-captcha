"""Small standard-library client for the HTTP service."""
import json
import math
import time
import urllib.error
import urllib.request

from .errors import CaptchaError
from .sdk import _url


class ApiClient:
    def __init__(self, base_url, api_key=None, *, request_timeout=15, poll_interval=0.25):
        if not all(math.isfinite(x) and x > 0 for x in (request_timeout, poll_interval)):
            raise ValueError("Timeout and poll interval must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval

    def _request(self, method, path, payload=None, *, idempotency_key=None, timeout=None):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        body = None
        if payload is not None:
            body = json.dumps(payload, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.request_timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error).get("detail", {})
            except (ValueError, OSError):
                detail = {}
            if not isinstance(detail, dict):
                detail = {"message": "Invalid API request", "code": "invalid_request"}
            raise CaptchaError(detail.get("message", f"HTTP {error.code}"),
                               code=detail.get("code", f"http_{error.code}")) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise CaptchaError(f"API request failed: {error}", code="transport_error") from error

    def submit(self, site_url=None, sitekey=None, *, surl=None, idempotency_key=None,
               _request_timeout=None, **options):
        return self._request("POST", "/v1/tasks", {"type": "turnstile",
            "site_url": _url(site_url, surl), "sitekey": sitekey, **options},
            idempotency_key=idempotency_key, timeout=_request_timeout)

    def get(self, task_id):
        return self._request("GET", "/v1/tasks/" + task_id)

    def cancel(self, task_id, *, _request_timeout=None):
        return self._request("DELETE", "/v1/tasks/" + task_id, timeout=_request_timeout)

    def solve_turnstile_details(self, site_url=None, sitekey=None, *, surl=None,
                               wait_timeout=200, idempotency_key=None, **options):
        if not math.isfinite(wait_timeout) or wait_timeout <= 0:
            raise ValueError("wait_timeout must be positive")
        deadline = time.monotonic() + wait_timeout
        task = self.submit(site_url, sitekey, surl=surl, idempotency_key=idempotency_key,
                           _request_timeout=min(self.request_timeout, wait_timeout), **options)
        while True:
            if task["status"] == "succeeded":
                return task["result"]
            if task["status"] in ("failed", "cancelled"):
                error = task.get("error") or {"code": "cancelled", "message": "Task cancelled"}
                raise CaptchaError(error["message"], code=error["code"])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self.cancel(task["id"], _request_timeout=min(0.5, self.request_timeout))
                except (CaptchaError, OSError):
                    pass
                raise CaptchaError(f"Timed out waiting for task {task['id']}", code="client_timeout")
            time.sleep(min(self.poll_interval, remaining))
            remaining = deadline-time.monotonic()
            if remaining > 0:
                task = self._request("GET", "/v1/tasks/" + task["id"], timeout=min(self.request_timeout, remaining))

    def solve_turnstile(self, site_url=None, sitekey=None, **options):
        return self.solve_turnstile_details(site_url, sitekey, **options)["token"]
