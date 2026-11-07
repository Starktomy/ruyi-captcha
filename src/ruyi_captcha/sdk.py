"""The SDK and HTTP workers dispatch through the same provider functions."""
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .errors import CaptchaError

if TYPE_CHECKING:
    from .providers.turnstile import TurnstileSolution


def _url(site_url, surl):
    if site_url is not None and surl is not None and site_url != surl:
        raise ValueError("site_url and surl must refer to the same URL")
    return site_url if site_url is not None else surl


def solve_turnstile_details(site_url: str | None = None, sitekey: str | None = None, *,
                            surl: str | None = None, action: str | None = None,
                            cdata: str | None = None, timeout: float = 60,
                            attempts: int = 3, poll_ms: int = 100,
                            proxy: str | None = None,
                            profile: dict[str, Any] | str | None = "linux",
                            shell: str | Path | None = None,
                            _cancel_event=None) -> "TurnstileSolution":
    """Return a token and timing details; timeout is the per-attempt budget."""
    from .providers.turnstile import solve_turnstile_details as run
    return run(_url(site_url, surl), sitekey, action=action, cdata=cdata, timeout=timeout,
               attempts=attempts, poll_ms=poll_ms, proxy=proxy, profile=profile, shell=shell,
               _cancel_event=_cancel_event)


def solve_turnstile(site_url=None, sitekey=None, *, surl=None, **options):
    return solve_turnstile_details(site_url, sitekey, surl=surl, **options).token


def solve(task_type="turnstile", **parameters):
    if task_type != "turnstile":
        raise CaptchaError(f"Unsupported captcha type: {task_type}", code="unsupported_type")
    return solve_turnstile_details(**parameters)


def execute_task(parameters, cancel_event):
    parameters = dict(parameters)
    task_type = parameters.pop("type", "turnstile")
    return solve(task_type, _cancel_event=cancel_event, **parameters)
