"""Bounded in-process task execution for a single API instance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import threading
import time
import uuid

from .errors import CaptchaError

TERMINAL = {"succeeded", "failed", "cancelled"}


class QueueFull(CaptchaError):
    def __init__(self):
        super().__init__("Task capacity reached; retry later", code="queue_full")


class TaskNotFound(CaptchaError):
    def __init__(self):
        super().__init__("Task not found or result expired", code="task_not_found")


class IdempotencyConflict(CaptchaError):
    def __init__(self):
        super().__init__("Idempotency key was used with different parameters", code="idempotency_conflict")


def utc_now():
    return datetime.now(timezone.utc)


@dataclass
class _Job:
    id: str
    parameters: dict
    fingerprint: str
    created: float
    created_at: datetime = field(default_factory=utc_now)
    status: str = "queued"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    expires: float | None = None
    result: dict | None = None
    error: dict | None = None
    idempotency_key: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self):
        return {"id": self.id, "status": self.status,
                "created_at": self.created_at.isoformat(),
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
                "result_expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "result": deepcopy(self.result), "error": deepcopy(self.error)}


class JobManager:
    def __init__(self, solver, *, concurrency=2, queue_size=16, queue_timeout=30,
                 result_ttl=120, retained_results=256, clock=time.monotonic):
        if (concurrency < 1 or queue_size < 0 or retained_results < 1 or
                not all(math.isfinite(x) and x > 0 for x in (queue_timeout, result_ttl))):
            raise ValueError("Invalid worker capacity or task lifetime")
        self.solver = solver
        self.capacity = concurrency + queue_size
        self.queue_timeout = queue_timeout
        self.result_ttl = result_ttl
        self.max_stored = self.capacity + retained_results
        self.clock = clock
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="captcha")
        self._jobs = {}
        self._keys = {}
        self._inflight = 0
        self._closed = False

    def _remove(self, job_id):
        job = self._jobs.pop(job_id)
        if job.idempotency_key and self._keys.get(job.idempotency_key) == job_id:
            del self._keys[job.idempotency_key]

    def _prune(self):
        for job_id, job in list(self._jobs.items()):
            if job.status == "queued" and self.clock() - job.created >= self.queue_timeout:
                job.cancel.set()
                self._finish(job, "failed", error={"code": "queue_timeout", "message": "Task expired in queue"})
            if job.expires is not None and job.expires <= self.clock():
                self._remove(job_id)

    def submit(self, parameters, idempotency_key=None):
        parameters = deepcopy(parameters)
        fingerprint = hashlib.sha256(json.dumps(parameters, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self._lock:
            if self._closed:
                raise CaptchaError("Service is shutting down", code="service_closed")
            self._prune()
            if idempotency_key and idempotency_key in self._keys:
                job = self._jobs[self._keys[idempotency_key]]
                if job.fingerprint != fingerprint:
                    raise IdempotencyConflict()
                return job.public()
            if self._inflight >= self.capacity:
                raise QueueFull()
            while len(self._jobs) >= self.max_stored:
                completed = next((key for key, job in self._jobs.items() if job.status in TERMINAL), None)
                if completed is None:
                    raise QueueFull()
                self._remove(completed)
            job = _Job(uuid.uuid4().hex, parameters, fingerprint, self.clock(),
                       idempotency_key=idempotency_key)
            self._jobs[job.id] = job
            if idempotency_key:
                self._keys[idempotency_key] = job.id
            self._inflight += 1
            try:
                self._pool.submit(self._run, job)
            except BaseException:
                self._inflight -= 1
                self._remove(job.id)
                raise
            return job.public()

    def _finish(self, job, status, *, result=None, error=None):
        job.status, job.result, job.error = status, result, error
        job.completed_at = utc_now()
        job.expires_at = job.completed_at + timedelta(seconds=self.result_ttl)
        job.expires = self.clock() + self.result_ttl
        # Proxy credentials and other input data need not survive completion.
        job.parameters = {}

    def _run(self, job):
        try:
            with self._lock:
                if job.cancel.is_set():
                    if job.status not in TERMINAL:
                        self._finish(job, "cancelled")
                    return
                if self.clock() - job.created >= self.queue_timeout:
                    self._finish(job, "failed", error={"code": "queue_timeout", "message": "Task expired in queue"})
                    return
                job.status = "running"
                job.started_at = utc_now()
                parameters = job.parameters
            solution = self.solver(parameters, job.cancel)
            result = asdict(solution) if is_dataclass(solution) else dict(solution)
            result.update(type=parameters.get("type", "turnstile"), verification_status="token_obtained")
            if "token" in result:
                result["token_length"] = len(result["token"])
            with self._lock:
                if job.cancel.is_set():
                    self._finish(job, "cancelled")
                else:
                    self._finish(job, "succeeded", result=result)
        except Exception as error:
            with self._lock:
                if job.cancel.is_set():
                    self._finish(job, "cancelled")
                else:
                    self._finish(job, "failed", error={"code": getattr(error, "code", None) or "solver_error",
                                                       "message": str(error)})
        finally:
            with self._lock:
                self._inflight -= 1

    def get(self, task_id):
        with self._lock:
            self._prune()
            if task_id not in self._jobs:
                raise TaskNotFound()
            return self._jobs[task_id].public()

    def cancel(self, task_id):
        with self._lock:
            self._prune()
            if task_id not in self._jobs:
                raise TaskNotFound()
            job = self._jobs[task_id]
            if job.status in TERMINAL:
                return job.public()
            job.cancel.set()
            if job.status == "queued":
                self._finish(job, "cancelled")
            else:
                job.status = "cancelling"
            # A cancelled queued work item still owns capacity until a worker
            # drains it. Repeated cancel/submit cannot grow the executor queue.
            return job.public()

    def close(self):
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                if job.status not in TERMINAL:
                    job.cancel.set()
                    if job.status == "queued":
                        self._finish(job, "cancelled")
                    else:
                        job.status = "cancelling"
        self._pool.shutdown(wait=True, cancel_futures=False)
