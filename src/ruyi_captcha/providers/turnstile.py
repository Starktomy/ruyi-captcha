"""Solve a Cloudflare Turnstile challenge for a known site URL and sitekey."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..runtime import create_context as Context, load_bindings, check_runtime
from ..errors import CaptchaError, SolveCancelled


_ACTION = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_CDATA = re.compile(r"^[a-zA-Z0-9_-]{1,255}$")
_SITEKEY = re.compile(r"^[0-9A-Za-z_-]{16,255}$")


class TurnstileError(CaptchaError):
    """Turnstile did not produce a non-dummy token."""

    def __init__(self, message: str, *, code: str | None = None,
                 events: list[dict[str, Any]] | None = None):
        super().__init__(message, code=code or "turnstile_error")
        self.events = list(events or [])


@dataclass(frozen=True)
class TurnstileSolution:
    token: str
    elapsed: float
    attempts: int


def _validated_url(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("site_url must be a string")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("site_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("site_url must not contain credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _linux_profile() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "profiles/linux_firefox159.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["navigator"]["hardwareConcurrency"] = min(os.cpu_count() or 1, 32)
    return profile


def _page() -> str:
    return """<!doctype html><html><head><meta name=viewport
      content="width=device-width,initial-scale=1"><title>Sign in</title></head><body>
      <div id=turnstile-target></div><script>
      globalThis.ruyijsTurnstile={token:'',error:'',rendered:false,generation:0,
        events:[],clicks:0,targets:new WeakMap(),nextTarget:0};
      function ruyijsToken(value){ruyijsTurnstile.token=value;
        ruyijsTurnstile.error='';
        ruyijsTurnstile.events.push({event:'token',length:value.length})}
      function ruyijsError(code){ruyijsTurnstile.token='';ruyijsTurnstile.error=String(code);
        ruyijsTurnstile.events.push({event:'error',code:String(code)})}
      function ruyijsExpired(){ruyijsTurnstile.token='';ruyijsTurnstile.generation++;
        ruyijsTurnstile.events.push({event:'expired'})}
      function ruyijsTimeout(){ruyijsTurnstile.token='';ruyijsTurnstile.generation++;
        ruyijsTurnstile.events.push({event:'timeout'})}
      </script><script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async>
      </script></body></html>""".replace("\n", " ")


_STATE = r'''(() => {
  function scopes(node, expression) {
    const roots=[{node, expression}];
    for (let i=0; i<roots.length && roots.length<128; i++) {
      const root=roots[i];
      const children=[root.node, ...root.node.querySelectorAll('*')];
      for (let j=0; j<children.length; j++) if (children[j].shadowRoot) {
        const path=j ? root.expression+'.querySelectorAll("*")['+(j-1)+']' : root.expression;
        roots.push({node:children[j].shadowRoot, expression:path+'.shadowRoot'});
      }
    }
    return roots;
  }
  const targets=[];
  for (const root of scopes(document, 'document')) {
    const children=[root.node, ...root.node.querySelectorAll('*')];
    for (const [i, frame] of children.entries()) {
      if (!frame.localName || frame.localName!=='iframe' ||
          !String(frame.src||'').includes('challenges.cloudflare.com/')) continue;
      const framePath=i ? root.expression+'.querySelectorAll("*")['+(i-1)+']' : root.expression;
      const doc=frame.contentDocument;
      if (!doc) continue;
      for (const inner of scopes(doc, framePath+'.contentDocument')) {
        const nodes=[inner.node, ...inner.node.querySelectorAll('*')];
        for (const [j, input] of nodes.entries()) {
          if (input.localName!=='input' || input.type!=='checkbox') continue;
          const rect=input.getBoundingClientRect();
          if (rect.width>0 && rect.height>0) {
            const path=j ? inner.expression+'.querySelectorAll("*")['+(j-1)+']' : inner.expression;
            let id=ruyijsTurnstile.targets.get(input);
            if (id===undefined) {
              id=++ruyijsTurnstile.nextTarget;
              ruyijsTurnstile.targets.set(input,id);
            }
            targets.push({id,path});
          }
        }
      }
    }
  }
  return {ready:document.readyState, rendered:ruyijsTurnstile.rendered,
    token:ruyijsTurnstile.token, error:ruyijsTurnstile.error,
    generation:ruyijsTurnstile.generation, events:ruyijsTurnstile.events,
    api:typeof turnstile!=='undefined', frames:document.querySelectorAll('iframe').length,
    targets};
})()'''


def _render_options(sitekey: str, action: str | None, cdata: str | None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "sitekey": sitekey,
        "theme": "light",
        "size": "normal",
        "appearance": "always",
        "language": "auto",
        "retry": "auto",
    }
    if action is not None:
        options["action"] = action
    if cdata is not None:
        options["cData"] = cdata
    return options


def _solve_once(site_url: str, sitekey: str, *, action: str | None, cdata: str | None,
                timeout: float, proxy: str | None, profile: dict[str, Any] | None,
                shell: str | Path | None, poll_ms: int, cancel_event=None) -> TurnstileSolution:
    bindings = load_bindings()
    if cancel_event is not None and cancel_event.is_set():
        raise SolveCancelled()
    started = monotonic()
    deadline = started + timeout
    events: list[dict[str, Any]] = []
    script_errors: list[str] = []
    clicked: set[int] = set()
    generation = 0
    rendered = False
    options = _render_options(sitekey, action, cdata)

    try:
        with Context(shell=shell, timeout=min(30, timeout), proxy=proxy, tls="159") as context:
            def request(method, *args, **kwargs):
                if cancel_event is not None and cancel_event.is_set():
                    raise SolveCancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Turnstile attempt deadline reached")
                context.timeout = min(30, remaining)
                return method(*args, **kwargs)

            try:
                request(context.configure_clock, "system")
                if profile is not None:
                    request(context.configure_fingerprint, profile)
                request(context.load_page, site_url, _page())
                while (remaining := deadline - monotonic()) > 0:
                    try:
                        # Leave time for the RPC response and state inspection.
                        request(context.advance, min(poll_ms, remaining * 500), max_tasks=400)
                    except bindings.JSError as error:
                        script_errors.append(error.message)
                    state = json.loads(request(context.eval, "JSON.stringify(" + _STATE + ")"))
                    events = state.get("events", [])
                    if state.get("error"):
                        raise TurnstileError(f"Turnstile error {state['error']}",
                                             code=state["error"], events=events)
                    token = state.get("token")
                    if token:
                        if not isinstance(token, str) or len(token) < 200 or "DUMMY" in token:
                            raise TurnstileError("Turnstile returned an invalid token", events=events)
                        return TurnstileSolution(token, monotonic() - started, 1)
                    if not rendered and state.get("api"):
                        encoded = json.dumps(options, separators=(",", ":"))
                        request(context.eval,
                                "ruyijsTurnstile.widgetId=turnstile.render('#turnstile-target',"
                                "Object.assign(" + encoded + ",{callback:ruyijsToken,"
                                "'error-callback':ruyijsError,'expired-callback':ruyijsExpired,"
                                "'timeout-callback':ruyijsTimeout}));ruyijsTurnstile.rendered=true")
                        rendered = True
                        continue
                    if state["generation"] != generation:
                        clicked.clear()
                        generation = state["generation"]
                    for target in state.get("targets", []):
                        if target["id"] not in clicked:
                            request(context.eval, "ruyijsTurnstile.clicks++")
                            hold_ms = min(80, max(0, deadline - monotonic()) * 500)
                            request(context.click, target["path"], hold_ms=hold_ms)
                            clicked.add(target["id"])
            finally:
                # Cleanup has a separate bounded wait, even at the deadline.
                context.timeout = 1
    except TimeoutError as error:
        raise TurnstileError("Turnstile timed out before returning a token",
                             code="timeout", events=events) from error

    code = "timeout"
    message = "Turnstile timed out before returning a token"
    if script_errors:
        message += f"; last script error: {script_errors[-1]}"
        code = "script-error"
    raise TurnstileError(message, code=code, events=events)


def solve_turnstile_details(site_url: str, sitekey: str, *, action: str | None = None,
                            cdata: str | None = None, timeout: float = 90.0,
                            attempts: int = 3, poll_ms: int = 100,
                            proxy: str | None = None,
                            profile: dict[str, Any] | str | None = "linux",
                            shell: str | Path | None = None,
                            _cancel_event=None) -> TurnstileSolution:
    """Return a non-dummy Turnstile token and timing details.

    ``site_url`` must use the hostname registered for ``sitekey``. The path and
    query are preserved so location-sensitive sitekeys behave as on the target
    page. Each attempt uses a fresh Context; no cookie jar or token is reused.
    ``timeout`` is the budget in seconds for each attempt, including startup;
    process cleanup has a separate bounded wait. Runtime timeouts use the same
    ``attempts`` limit as challenge failures; exhausted timeouts are reported
    as TurnstileError with code "timeout".
    """
    site_url = _validated_url(site_url)
    if not isinstance(sitekey, str) or not _SITEKEY.fullmatch(sitekey):
        raise ValueError("sitekey must contain 16-255 URL-safe characters")
    if action is not None and (not isinstance(action, str) or not _ACTION.fullmatch(action)):
        raise ValueError("action must be 1-32 letters, digits, underscores or hyphens")
    if cdata is not None and (not isinstance(cdata, str) or not _CDATA.fullmatch(cdata)):
        raise ValueError("cdata must be 1-255 letters, digits, underscores or hyphens")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    if isinstance(poll_ms, bool) or not isinstance(poll_ms, int) or not 20 <= poll_ms <= 1000:
        raise ValueError("poll_ms must be an integer from 20 to 1000")

    if profile == "linux":
        configured = _linux_profile()
    elif profile in (None, "default"):
        configured = None
    elif isinstance(profile, dict):
        configured = profile
    else:
        raise ValueError("profile must be 'linux', 'default', None, or a fingerprint dictionary")

    check_runtime(shell)
    bindings = load_bindings()
    started = monotonic()
    last_error: TurnstileError | None = None
    for attempt in range(1, attempts + 1):
        if _cancel_event is not None and _cancel_event.is_set():
            raise SolveCancelled()
        try:
            solution = _solve_once(site_url, sitekey, action=action, cdata=cdata,
                                   timeout=timeout, proxy=proxy, profile=configured,
                                   shell=shell, poll_ms=poll_ms, cancel_event=_cancel_event)
            return TurnstileSolution(solution.token, monotonic() - started, attempt)
        except TurnstileError as error:
            last_error = error
        except bindings.JSError as error:
            last_error = TurnstileError(f"Script error: {error.message}",
                                        code="script-error")
    assert last_error is not None
    raise last_error


def solve_turnstile(site_url: str, sitekey: str, *, action: str | None = None,
                    cdata: str | None = None, timeout: float = 90.0,
                    attempts: int = 3, poll_ms: int = 100,
                    proxy: str | None = None,
                    profile: dict[str, Any] | str | None = "linux",
                    shell: str | Path | None = None) -> str:
    """Return only a non-dummy Turnstile token string."""
    return solve_turnstile_details(site_url, sitekey, action=action, cdata=cdata,
                                   timeout=timeout, attempts=attempts, poll_ms=poll_ms,
                                   proxy=proxy, profile=profile, shell=shell).token
