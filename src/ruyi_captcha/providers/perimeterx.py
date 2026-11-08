"""Solve a PerimeterX/HUMAN press-and-hold challenge with ff-ruyijs.

The solver navigates to the target page, locates the press-and-hold control in
the light DOM, closed shadow roots, or same-origin challenge frames, and sends
trusted pointer/mouse input until the page reports a successful challenge.

The Walmart PX build used by the NopeCHA demo (v2.9.8) does not put its
``NS4Fa3BCClk=`` metrics event into the Bundle#3 batch even though the
``crcldu.com`` auditor returns the metrics JSON. This adapter installs a
page-side, append-only event hook for that build, matching the userscript
approach used in the ff-ruyijs investigation. The hook is a compatibility
workaround for the client queue, not a replacement for the browser engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..proxy import proxy_context
from ..runtime import create_context as Context, load_bindings, check_runtime
from ..errors import CaptchaError, SolveCancelled
from .turnstile import _linux_profile

_DEFAULT_PRESS_MS = 6000
_MIN_PRESS_MS = 500
_MAX_PRESS_MS = 15000

# Metrics event skeleton observed in the accepted Walmart PX build. The
# session-specific fields are replaced by the live PX561 event before the hook
# appends it. The server accepts an empty encrypted payload (``{"en": ""}``).
_METRICS_TEMPLATE: dict[str, Any] = {
    "bHcccikaGkk=": 5363,
    "cyxDaTZBRls=": 7426,
    "eyRLYT5JTlI=": 7428,
    "ZR5VGyBzUCs=": 9150,
    "EFMgFlU/LyU=": {"en": ""},
    "T3A/NQkeOwY=": 0,
    "ajUaMCxWHQA=": 3,
    "OkVKAHwqSzI=": 0,
    "dW5FKzMDQxA=": True,
    "bHcccikfE0A=": 0,
    "IUIRR2csH3M=": "",
    "OkVKAH8qSzY=": "",
    "HCcsIllILRU=": 1084.0999999046326,
    "RT41ewNXNUA=": False,
    "WQopDxxjLjo=": "PX11745",
    "HUZtQ1sqbHk=": "pxhc",
    "QSIxZwRLMl0=": False,
    "FU5lS1AjZ3k=": {"PX12738": 10614, "PX12739": 0, "PX12740": 0,
                       "PX12741": -1},
}

_METRICS_HOOK = r'''(() => {
  if (globalThis.__ruyiPxMetricsInstalled) return true;
  globalThis.__ruyiPxMetricsInstalled = true;
  const template = {"bHcccikaGkk=":5363,"cyxDaTZBRls=":7426,"eyRLYT5JTlI=":7428,"ZR5VGyBzUCs=":9150,"EFMgFlU/LyU=":{"en":""},"T3A/NQkeOwY=":0,"ajUaMCxWHQA=":3,"OkVKAHwqSzI=":0,"dW5FKzMDQxA=":true,"bHcccikfE0A=":0,"IUIRR2csH3M=":"","OkVKAH8qSzY=":"","HCcsIllILRU=":1084.0999999046326,"RT41ewNXNUA=":false,"WQopDxxjLjo=":"PX11745","HUZtQ1sqbHk=":"pxhc","QSIxZwRLMl0=":false,"FU5lS1AjZ3k=":{"PX12738":10614,"PX12739":0,"PX12740":0,"PX12741":-1}};
  const original = Array.prototype.push;
  globalThis.__ruyiPxMetricsDone = false;
  globalThis.__ruyiPxMetricsLog = [];
  Array.prototype.push = function() {
    const result = original.apply(this, arguments);
    try {
      for (const event of arguments) {
        if (event && event.t === 'PX561') {
          globalThis.__ruyiPxSensor = event.d || event;
        }
        if (event && event.t === 'P2APJXkNABU=' &&
            globalThis.__ruyiPxSensor && !globalThis.__ruyiPxMetricsDone) {
          globalThis.__ruyiPxMetricsDone = true;
          const sensor = globalThis.__ruyiPxSensor;
          const metrics = Object.assign({}, template);
          metrics['IUIRR2csH3M='] = sensor['IUIRR2csH3M='];
          metrics['OkVKAH8qSzY='] = sensor['OkVKAH8qSzY='];
          metrics['bHcccikfE0A='] = sensor['bHcccikfE0A='];
          metrics['T3A/NQkeOwY='] = sensor['T3A/NQkeOwY='];
          metrics['OkVKAHwqSzI='] = sensor['OkVKAHwqSzI='] ||
            (performance.now() | 0);
          metrics['ajUaMCxWHQA='] = (sensor['ajUaMCxWHQA='] || 0) + 1;
          original.call(this, {t: 'NS4Fa3BCClk=', d: metrics, ts: Date.now()});
        }
      }
    } catch (error) {
      globalThis.__ruyiPxMetricsLog.push(String(error));
    }
    return result;
  };
  return true;
})()'''

# Filled from tools/probe_perimeterx.py when the package is built. Keeping the
# scanner here means the provider does not depend on the tools package.
_SCAN_JS = r'''(capture => {
 const localName=Object.getOwnPropertyDescriptor(Element.prototype,'localName').get;
 const attribute=Element.prototype.getAttribute;
 const frameDocument=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentDocument').get;
 const shadow=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get;
 const nodeType=Object.getOwnPropertyDescriptor(Node.prototype,'nodeType').get;
 const nodeName=Object.getOwnPropertyDescriptor(Node.prototype,'nodeName').get;
 const textContent=Object.getOwnPropertyDescriptor(Node.prototype,'textContent').get;
 const bounds=Element.prototype.getBoundingClientRect;
 const query=(root,selector)=>Reflect.apply(nodeType.call(root)===9?Document.prototype.querySelectorAll:
   nodeType.call(root)===11?DocumentFragment.prototype.querySelectorAll:Element.prototype.querySelectorAll,root,[selector]);
 const queryPath=(path,type)=>'(Reflect.apply('+(type===9?'Document':type===11?'DocumentFragment':'Element')+
   '.prototype.querySelectorAll,'+path+',["*"]))';
 const text=e=>textContent.call(e)||'';
 const name=e=>localName.call(e),attr=(e,k)=>attribute.call(e,k);
 const scopes=[{node:document,path:'document',depth:0,visible:true}],documents=[],buttons=[];
 if(capture)globalThis.__ruyiPxCandidates={};
 for(let index=0;index<scopes.length && scopes.length<80;index++){
   const scope=scopes[index],root=scope.node;
   const type=nodeType.call(root);
   if(type===9){
     const win=root.defaultView;
     documents.push({url:root.URL,title:root.title,ready:root.readyState,
       text:[...query(root,'h1,h2,p,label,button')].map(e=>text(e).trim()).join('\n').slice(0,1800),
       cookies:(root.cookie||'').split(';').map(s=>s.trim().split('=')[0]).filter(Boolean),
       scripts:[...root.scripts].map(s=>s.src).filter(Boolean).slice(0,20),
       appId:win?._pxAppId,pxCaptcha:!!query(root,'#px-captcha').length});
   }
   const nodes=[...query(root,'*')];
   for(let i=0;i<nodes.length;i++){
     const node=nodes[i],path=queryPath(scope.path,type)+'['+i+']';
     const label=text(node).trim();
     if(name(node)==='button' || attr(node,'role')==='button' ||
        name(node)==='input' && ['button','checkbox'].includes(attr(node,'type'))){
       const rect=bounds.call(node);
       if(capture && scope.visible){
         const aria=attr(node,'aria-label')||'';
         if(aria.includes('Press & Hold'))globalThis.__ruyiPxCandidates.hold=node;
         if(aria==='Accessible challenge')globalThis.__ruyiPxCandidates.accessible=node;
         if(aria==='Press again')globalThis.__ruyiPxCandidates.confirm=node;
       }
       buttons.push({path,tag:name(node),id:attr(node,'id'),role:attr(node,'role'),
         label:label.slice(0,300),aria:attr(node,'aria-label'),visible:scope.visible,rect:rect.toJSON()});
     }
     const inner=shadow.call(node);
     if(inner && scope.depth<5)scopes.push({node:inner,
       path:'Object.getOwnPropertyDescriptor(Element.prototype,"shadowRoot").get.call('+path+')',
       depth:scope.depth+1,visible:scope.visible});
     if(name(node)==='iframe' && scope.depth<5 && !/display\s*:\s*none/.test(attr(node,'style')||'')){
       try{const doc=frameDocument.call(node);if(doc)scopes.push({node:doc,
         path:'Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,"contentDocument").get.call('+path+')',
         depth:scope.depth+1,visible:scope.visible&&!/display\s*:\s*none/.test(attr(node,'style')||'')})}catch(e){}
     }
   }
 }
 return {documents,buttons,scopes:scopes.length,
   roots:scopes.map(s=>({path:s.path,type:nodeType.call(s.node),
     markup:nodeType.call(s.node)===11?(Object.getOwnPropertyDescriptor(ShadowRoot.prototype,'innerHTML').get.call(s.node)||'').replace(/<style\b[\s\S]*?<\/style>/gi,'[style]')
       .replace(/<script\b[\s\S]*?<\/script>/gi,'[script]').replace(/\btoken="[^"]*"/gi,'token="[omitted]"')
       .replace(/data:[^"'<>\s]+/g,'data:[omitted]').slice(0,7000):undefined,
     elements:[...query(s.node,'*')]
     .filter(e=>!['script','style','link','meta'].includes(name(e))).slice(0,100)
     .map(e=>({tag:name(e),nodeName:nodeName.call(e),
               id:attr(e,'id'),role:attr(e,'role'),tabindex:attr(e,'tabindex'),
               aria:attr(e,'aria-label'),class:attr(e,'class'),
               text:['p','label','button','span','div'].includes(name(e))?text(e).trim().slice(0,300):undefined}))}))};
})'''
_STATE_JS = _SCAN_JS + '(true)'
_TARGET_STATE_JS = r'''(() => {
  const target = globalThis.__ruyiPxHoldTarget;
  return {aria: Element.prototype.getAttribute.call(target, 'aria-label'),
          connected: Object.getOwnPropertyDescriptor(Node.prototype,
            'isConnected').get.call(target)};
})()'''
_OBSERVE_JS = r'''(win => {
 const app=win._pxAppId;
 if(typeof app!=='string')return false;
 const handler=win['_'+app.replace(/px|PX/,'')+'handler'];
 if(!handler || typeof handler.PX764!=='function')return false;
 const observed=globalThis.__ruyiPxObservedHandlers ||= new WeakSet();
 const results=globalThis.__ruyiPxServerResults ||= [];
 if(observed.has(handler))return true;
 const original=handler.PX764;
 handler.PX764=function(status,...args){
   // Store only a bounded numeric status, never the cookie/token arguments.
   const value=typeof status==='number'?status:
     typeof status==='string' && /^-?\d{1,4}$/.test(status)?Number(status):NaN;
   if(Number.isInteger(value) && Math.abs(value)<=9999 && results.length<64)
     {results.push({status:value});
      try{globalThis.__ruyiPxRecordStatus?.(value)}catch(e){}}
   return Reflect.apply(original,this,[status,...args]);
 };
 observed.add(handler);
 return true;
})'''


def _validated_url(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("site_url must be a string")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("site_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("site_url must not contain credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/",
                       parsed.query, ""))


def _input_point(rect: dict[str, Any], origin) -> dict[str, Any]:
    x = float(rect["x"]) + float(rect["width"]) / 2
    y = float(rect["y"]) + float(rect["height"]) / 2
    return {
        "clientX": x,
        "clientY": y,
        "screenX": round(float(origin[0]) + x),
        "screenY": round(float(origin[1]) + y),
    }


class PerimeterXError(CaptchaError):
    """PerimeterX did not produce an accepted challenge cookie."""

    def __init__(self, message: str, *, code: str | None = None,
                 statuses: list[int] | None = None):
        super().__init__(message, code=code or "perimeterx_error")
        self.statuses = list(statuses or [])


@dataclass(frozen=True)
class PerimeterXSolution:
    token: str
    cookies: dict[str, str]
    elapsed: float
    attempts: int
    verified: bool


def _cookies(context) -> dict[str, str]:
    raw = context.eval("document.cookie") or ""
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name:
            cookies[name] = value
    return cookies


def _install_page_hooks(context, state: dict[str, Any]) -> None:
    """Install the metrics compatibility hook and PX result observer."""
    try:
        context.eval(_METRICS_HOOK)
    except Exception:
        # A page without the Walmart metrics build can still expose PX764.
        pass
    windows = ["window"]
    for root in state.get("roots", []):
        if root.get("type") == 9 and root.get("path"):
            windows.append("(" + root["path"] + ").defaultView")
    for window in windows:
        try:
            context.eval("(" + _OBSERVE_JS + ")(" + window + ")")
        except Exception:
            continue
        try:
            context.eval(
                "(() => { const w = " + window + "; try { const old = "
                "w._pxOnCaptchaSuccess; w._pxOnCaptchaSuccess = function(v) { "
                "w.__ruyiPxCallback = v; if (v === true) "
                "w.__ruyiPxAccepted = true; try { return old && "
                "old.apply(this, arguments); } catch (e) {} }; } catch (e) {} "
                "})()")
        except Exception:
            continue


def _press(context, target: str, rect: dict[str, Any], origin,
           press_ms: int, statuses: list[int], cancel_event) -> None:
    """Send the trusted pointer/mouse sequence and hold for press_ms."""
    bindings = load_bindings()
    point = _input_point(rect, origin)
    init = {**point, "button": 0, "buttons": 0, "bubbles": True,
            "composed": True, "cancelable": True, "detail": 1}
    pointer = {"pointerId": 0, "pointerType": "mouse", "isPrimary": True,
               "width": 1, "height": 1}
    for event in ("pointerover", "pointerenter", "mouseover", "mouseenter",
                  "pointermove", "mousemove", "pointerdown", "mousedown"):
        fields = {**init, **(pointer if event.startswith("pointer") else {})}
        if event in ("pointerover", "pointerenter", "pointermove"):
            fields["button"] = -1
        if event in ("pointerenter", "mouseenter"):
            fields["bubbles"] = False
            fields["cancelable"] = False
            fields["composed"] = False
        fields["detail"] = 1 if event == "mousedown" else 0
        if event in ("pointerdown", "mousedown"):
            fields["buttons"] = 1
        if event.startswith("pointer"):
            fields["pressure"] = 0.5 if fields["buttons"] else 0
        context.dispatch_input(event, target=target, **fields)
        if event == "mousedown":
            context.eval("globalThis.__ruyiPxHoldTarget.focus();true")
    started = monotonic()
    step = 0
    while monotonic() - started < press_ms / 1000:
        if cancel_event is not None and cancel_event.is_set():
            raise SolveCancelled()
        if any(value == 0 for value in statuses):
            break
        if any(value == -1 for value in statuses):
            break
        step += 1
        x = point["clientX"] + math.sin(step * 0.19) * 1.4
        y = point["clientY"] + math.cos(step * 0.23) * 1.1
        for event in ("pointermove", "mousemove"):
            fields = {**init, **point, "clientX": x, "clientY": y,
                      "buttons": 1, "button": 0, "detail": 0}
            if event.startswith("pointer"):
                fields.update(pointer)
                fields["pressure"] = 0.5
            context.dispatch_input(event, target=target, **fields)
        context.advance(20, max_tasks=250)
    # Release even on a deadline; the page needs the up/click pair.
    for event in ("pointerup", "mouseup", "click"):
        fields = {**init, **(pointer if event.startswith("pointer")
                            or event == "click" else {})}
        fields["buttons"] = 0
        fields["detail"] = 0 if event == "pointerup" else 1
        if event.startswith("pointer") or event == "click":
            fields["pressure"] = 0
        context.dispatch_input(event, target=target, **fields)


def _solve_once(site_url: str, *, press_ms: int, timeout: float,
                proxy: str | None, profile: dict[str, Any] | None,
                shell, cancel_event) -> PerimeterXSolution:
    bindings = load_bindings()
    if cancel_event is not None and cancel_event.is_set():
        raise SolveCancelled()
    started = monotonic()
    deadline = started + timeout
    statuses: list[int] = []
    hold = None
    accepted = False
    rejected = False
    cookies: dict[str, str] = {}

    def record_status(value):
        if type(value) is int and abs(value) <= 9999:
            statuses.append(value)

    try:
        with proxy_context(Context, proxy, shell=shell,
                           timeout=min(30, timeout), tls="159") as context:
            def request(method, *args, **kwargs):
                if cancel_event is not None and cancel_event.is_set():
                    raise SolveCancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("PerimeterX attempt deadline reached")
                context.timeout = min(30, remaining)
                return method(*args, **kwargs)

            request(context.configure_clock, "system")
            if profile is not None:
                request(context.configure_fingerprint, profile)
            request(context.expose, record_status, "__ruyiPxRecordStatus")
            request(context.navigate, site_url)

            while monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise SolveCancelled()
                request(context.advance, 100, max_tasks=400)
                if any(value == 0 for value in statuses):
                    accepted = True
                    break
                if any(value == -1 for value in statuses):
                    rejected = True
                    break
                if hold is None:
                    state = json.loads(request(
                        context.eval, "JSON.stringify(" + _STATE_JS + ")"))
                    _install_page_hooks(context, state)
                    candidates = [
                        button for button in state.get("buttons", [])
                        if button.get("visible") and button.get("rect", {}).get("width", 0) > 0
                        and "Press & Hold" in (button.get("aria") or "")
                    ]
                    if not candidates:
                        continue
                    hold = candidates[0]
                    request(context.eval,
                            "globalThis.__ruyiPxHoldTarget="
                            "globalThis.__ruyiPxCandidates.hold;true")
                    rect = hold["rect"]
                    origin = request(
                        context.eval,
                        "(()=>{const w=globalThis.__ruyiPxHoldTarget"
                        ".ownerDocument.defaultView;return [w.mozInnerScreenX,"
                        "w.mozInnerScreenY]})()")
                    target = "globalThis.__ruyiPxHoldTarget"
                    _press(context, target, rect, origin, press_ms, statuses,
                           cancel_event)
                    if any(value == -1 for value in statuses):
                        rejected = True
                        break
                    # The challenge may have completed during the hold; keep
                    # polling after release for the server status and cookie.
                    while monotonic() < deadline:
                        request(context.advance, 100, max_tasks=300)
                        if any(value == 0 for value in statuses):
                            accepted = True
                            break
                        if any(value == -1 for value in statuses):
                            rejected = True
                            break
                        cookies = _cookies(context)
                        if "_px3" in cookies:
                            accepted = True
                            break
                    break
            if accepted:
                try:
                    cookies = _cookies(context)
                except Exception:
                    pass
    except (TimeoutError, BlockingIOError) as error:
        raise PerimeterXError(
            "PerimeterX timed out before the challenge was accepted",
            code="timeout", statuses=statuses) from error

    if rejected:
        raise PerimeterXError("PerimeterX rejected the challenge",
                              code="rejected", statuses=statuses)
    if not accepted:
        raise PerimeterXError(
            "PerimeterX challenge did not reach an accepted state",
            code="timeout", statuses=statuses)
    token = cookies.get("_px3")
    if not token:
        raise PerimeterXError(
            "PerimeterX accepted the challenge without a _px3 cookie",
            code="missing_cookie", statuses=statuses)
    return PerimeterXSolution(token, cookies, monotonic() - started, 1, True)


def solve_perimeterx_details(site_url: str, *, press_ms: int = _DEFAULT_PRESS_MS,
                             timeout: float = 90.0, attempts: int = 3,
                             proxy: str | None = None,
                             profile: dict[str, Any] | str | None = "linux",
                             shell=None, _cancel_event=None) -> PerimeterXSolution:
    """Return the accepted ``_px3`` cookie and timing details.

    ``site_url`` must be the page that serves the PerimeterX challenge
    directly (for the Walmart demo this is ``https://www.walmart.com/blocked``,
    not the NopeCHA wrapper iframe). Each attempt uses a fresh Context and the
    caller-supplied proxy. Timeout is the per-attempt budget, including
    navigation and input.
    """
    site_url = _validated_url(site_url)
    if isinstance(press_ms, bool) or not isinstance(press_ms, int) or not (
            _MIN_PRESS_MS <= press_ms <= _MAX_PRESS_MS):
        raise ValueError(f"press_ms must be an integer from {_MIN_PRESS_MS} "
                         f"to {_MAX_PRESS_MS}")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not \
            math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
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
    last_error: PerimeterXError | None = None
    for attempt in range(1, attempts + 1):
        if _cancel_event is not None and _cancel_event.is_set():
            raise SolveCancelled()
        try:
            solution = _solve_once(site_url, press_ms=press_ms, timeout=timeout,
                                   proxy=proxy, profile=configured, shell=shell,
                                   cancel_event=_cancel_event)
            return PerimeterXSolution(solution.token, solution.cookies,
                                      monotonic() - started, attempt, True)
        except PerimeterXError as error:
            last_error = error
        except bindings.JSError as error:
            last_error = PerimeterXError(f"Script error: {error.message}",
                                         code="script-error")
    assert last_error is not None
    raise last_error


def solve_perimeterx(site_url: str, **options) -> str:
    """Return only the accepted PerimeterX ``_px3`` cookie value."""
    return solve_perimeterx_details(site_url, **options).token
