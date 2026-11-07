"""Optional FastAPI service; each solve uses the same SDK provider."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hmac
import json
import math
import os
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .errors import CaptchaError
from .jobs import IdempotencyConflict, JobManager, QueueFull, TERMINAL, TaskNotFound
from .runtime import check_runtime
from .sdk import execute_task


@dataclass(frozen=True)
class Settings:
    concurrency: int = 2
    queue_size: int = 16
    queue_timeout: float = 30
    result_ttl: float = 120
    api_key: str | None = None

    def __post_init__(self):
        if self.concurrency < 1 or self.queue_size < 0:
            raise ValueError("Concurrency must be positive and queue_size nonnegative")
        if not all(math.isfinite(x) and x > 0 for x in (self.queue_timeout, self.result_ttl)):
            raise ValueError("Queue timeout and result TTL must be finite and positive")

    @classmethod
    def from_env(cls):
        return cls(concurrency=int(os.getenv("RUYI_CAPTCHA_CONCURRENCY", "2")),
                   queue_size=int(os.getenv("RUYI_CAPTCHA_QUEUE_SIZE", "16")),
                   queue_timeout=float(os.getenv("RUYI_CAPTCHA_QUEUE_TIMEOUT", "30")),
                   result_ttl=float(os.getenv("RUYI_CAPTCHA_RESULT_TTL", "120")),
                   api_key=os.getenv("RUYI_CAPTCHA_API_KEY") or None)


class SolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["turnstile"] = "turnstile"
    site_url: str = Field(validation_alias=AliasChoices("site_url", "surl"), min_length=1, max_length=8192)
    sitekey: str = Field(pattern=r"^[0-9A-Za-z_-]{16,255}$")
    action: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,32}$")
    cdata: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,255}$")
    timeout: float = Field(default=60, gt=0, le=120, allow_inf_nan=False, strict=True)
    attempts: int = Field(default=3, ge=1, le=3, strict=True)
    proxy: str | None = Field(default=None, max_length=4096)
    profile: Literal["linux", "default"] | dict[str, Any] | None = "linux"

    @field_validator("site_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("site_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("site_url must not contain credentials")
        if parsed.port is not None and not 0 < parsed.port < 65536:
            raise ValueError("site_url port is invalid")
        if any(ord(c) < 32 for c in value):
            raise ValueError("site_url contains control characters")
        return value

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, value):
        if isinstance(value, dict):
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ValueError("profile must contain finite JSON values") from error
        return value

    @field_validator("proxy")
    @classmethod
    def valid_proxy(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or any(ord(c) < 32 for c in value):
            raise ValueError("proxy must be an HTTP(S) proxy URL")
        if parsed.port is not None and not 0 < parsed.port < 65536:
            raise ValueError("proxy port is invalid")
        return value


def create_app(settings=None, *, solver=execute_task, runtime_probe=check_runtime):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        info = await asyncio.to_thread(runtime_probe)
        app.state.runtime = info.to_dict() if hasattr(info, "to_dict") else info
        manager = JobManager(solver, concurrency=settings.concurrency, queue_size=settings.queue_size,
                             queue_timeout=settings.queue_timeout, result_ttl=settings.result_ttl)
        app.state.manager = manager
        try:
            yield
        finally:
            await asyncio.to_thread(manager.close)

    app = FastAPI(title="ruyi-captcha", version=__version__, lifespan=lifespan,
                  description="Turnstile token acquisition. Backend acceptance remains the caller's responsibility.")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Keep errors serializable even for nonfinite JSON input, and return
        # field paths/messages without echoing proxy credentials or raw input.
        details = [{key: item[key] for key in ("type", "loc", "msg") if key in item}
                   for item in error.errors()]
        return JSONResponse(status_code=422, content={"detail": details})

    bearer = HTTPBearer(auto_error=False)

    def authorised(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if settings.api_key and (credentials is None or credentials.scheme.lower() != "bearer" or
                not hmac.compare_digest(credentials.credentials.encode(), settings.api_key.encode())):
            raise HTTPException(401, detail={"code": "unauthorised", "message": "A valid bearer API key is required"},
                                headers={"WWW-Authenticate": "Bearer"})

    def call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except QueueFull as error:
            raise HTTPException(429, detail={"code": error.code, "message": str(error)},
                                headers={"Retry-After": "2"}) from error
        except TaskNotFound as error:
            raise HTTPException(404, detail={"code": error.code, "message": str(error)}) from error
        except IdempotencyConflict as error:
            raise HTTPException(409, detail={"code": error.code, "message": str(error)}) from error
        except CaptchaError as error:
            raise HTTPException(503, detail={"code": error.code, "message": str(error)}) from error

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/v1/runtime", dependencies=[Depends(authorised)])
    async def runtime():
        return app.state.runtime

    @app.post("/v1/tasks", status_code=202, dependencies=[Depends(authorised)])
    async def submit(request: SolveRequest,
                     idempotency_key: str | None = Header(default=None, max_length=128, alias="Idempotency-Key")):
        return call(app.state.manager.submit, request.model_dump(), idempotency_key)

    @app.get("/v1/tasks/{task_id}", dependencies=[Depends(authorised)])
    async def task(task_id: str):
        return call(app.state.manager.get, task_id)

    @app.delete("/v1/tasks/{task_id}", dependencies=[Depends(authorised)])
    async def cancel(task_id: str):
        return call(app.state.manager.cancel, task_id)

    @app.post("/v1/solve", dependencies=[Depends(authorised)])
    async def solve(request: SolveRequest,
                    wait_timeout: float = Query(default=30, ge=0, le=120, allow_inf_nan=False),
                    idempotency_key: str | None = Header(default=None, max_length=128, alias="Idempotency-Key")):
        job = call(app.state.manager.submit, request.model_dump(), idempotency_key)
        deadline = asyncio.get_running_loop().time() + wait_timeout
        while job["status"] not in TERMINAL:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return JSONResponse(status_code=202, content=job)
            await asyncio.sleep(min(0.05, remaining))
            job = call(app.state.manager.get, job["id"])
        return job

    return app
