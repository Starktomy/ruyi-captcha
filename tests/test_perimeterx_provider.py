import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    from ruyi_captcha.runtime import load_bindings
    bindings = load_bindings()
    Context = bindings.Context
    SHELL = Path(bindings.get_runtime_path()) / "dist" / ("js.exe" if sys.platform == "win32" else "js")
except (ImportError, OSError, FileNotFoundError, __import__("ruyi_captcha").RuntimeUnavailable):
    bindings = None
    Context = None
    SHELL = Path("/runtime-not-installed/js")

from ruyi_captcha.providers import perimeterx as provider
from ruyi_captcha.providers.perimeterx import PerimeterXError, solve_perimeterx_details


def page(body: str) -> str:
    return "<!doctype html><html><body>" + body + "</body></html>"


ACCEPTED = page("""
<div id="px-captcha" role="button" tabindex="0"
     aria-label="Press & Hold Human Challenge"
     style="width:200px;height:80px">Press &amp; Hold</div>
<script>
globalThis._pxAppId = 'PXfixture';
globalThis._fixturehandler = {PX764(status, token) {
  document.documentElement.setAttribute('data-status', String(status));
  if (status === 0) document.cookie = '_px3=offline-px3-token; path=/';
}};
const button = document.getElementById('px-captcha');
button.addEventListener('pointerdown', () => setTimeout(() => {
  button.setAttribute('aria-label', 'Human Challenge completed, please wait');
}, 100));
button.addEventListener('mouseup', () => _fixturehandler.PX764(0, 'offline-token'));
</script>
""")

REJECTED = page("""
<div id="px-captcha" role="button" tabindex="0"
     aria-label="Press & Hold Human Challenge"
     style="width:200px;height:80px">Press &amp; Hold</div>
<script>
globalThis._pxAppId = 'PXfixture';
globalThis._fixturehandler = {PX764(status, token) {
  document.documentElement.setAttribute('data-status', String(status));
}};
const button = document.getElementById('px-captcha');
button.addEventListener('mouseup', () => _fixturehandler.PX764(-1, 'rejected'));
</script>
""")

ACCEPTED_NO_COOKIE = page("""
<div id="px-captcha" role="button" tabindex="0"
     aria-label="Press & Hold Human Challenge"
     style="width:200px;height:80px">Press &amp; Hold</div>
<script>
globalThis._pxAppId = 'PXfixture';
globalThis._fixturehandler = {PX764(status, token) {
  document.documentElement.setAttribute('data-status', String(status));
}};
const button = document.getElementById('px-captcha');
button.addEventListener('mouseup', () => _fixturehandler.PX764(0, 'accepted'));
</script>
""")


class PerimeterXInputTests(unittest.TestCase):
    def test_input_validation_does_not_open_a_context(self):
        base = "https://example.test/blocked"
        for kwargs in ({"site_url": "ftp://example.test/"}, {"site_url": "https://u:p@example.test/"},
                       {"site_url": base, "press_ms": 100},
                       {"site_url": base, "press_ms": 20000},
                       {"site_url": base, "timeout": 0},
                       {"site_url": base, "attempts": 0},
                       {"site_url": base, "profile": "windows"}):
            with self.subTest(kwargs=kwargs), patch.object(provider, "Context") as context, \
                    patch.object(provider, "check_runtime"):
                with self.assertRaises((ValueError, TypeError)):
                    solve_perimeterx_details(**kwargs)
                context.assert_not_called()

    def test_cookie_parser_keeps_values_with_colons_and_equals(self):
        class Context:
            closed = False
            def eval(self, expression):
                return "a=1; _px3=hash:token:330:sig; empty=; b=x=y"
        self.assertEqual(provider._cookies(Context()),
                         {"a": "1", "_px3": "hash:token:330:sig",
                          "empty": "", "b": "x=y"})


@unittest.skipUnless(SHELL.is_file(), "build the native runtime first")
class PerimeterXRuntimeTests(unittest.TestCase):
    def solve(self, html, *, press_ms=600, timeout=8, attempts=1, proxy=None,
              cookies=None, cookie_domain=None):
        opened = []
        proxies = []
        self.proxies = proxies

        class OfflineContext(Context):
            def __init__(self, **kwargs):
                proxies.append(kwargs.get("proxy"))
                super().__init__(**kwargs)
                opened.append(self)

            def navigate(self, url):
                self.load_page(url, html)

        with patch.object(provider, "Context", OfflineContext), \
                patch.object(provider, "check_runtime", lambda shell=None: None):
            try:
                return solve_perimeterx_details(
                    "https://px.test/blocked", shell=SHELL, profile=None,
                    timeout=timeout, attempts=attempts, press_ms=press_ms,
                    proxy=proxy, cookies=cookies, cookie_domain=cookie_domain)
            finally:
                self.assertTrue(opened)
                self.assertTrue(all(context.closed for context in opened))

    def test_accepted_challenge_returns_px3_cookie(self):
        solution = self.solve(ACCEPTED)
        self.assertEqual(solution.token, "offline-px3-token")
        self.assertEqual(solution.cookies, {"_px3": "offline-px3-token"})
        self.assertTrue(solution.verified)
        self.assertEqual(solution.attempts, 1)

    def test_socks_proxy_is_bridged_to_loopback_http(self):
        self.solve(ACCEPTED, proxy="socks5h://proxy.test:1080")
        self.assertRegex(self.proxies[0], r"^http://127\.0\.0\.1:\d+$")

    def test_http_proxy_is_passed_through_to_the_context(self):
        value = "http://user:pass@proxy.test:8000"
        self.solve(ACCEPTED, proxy=value)
        self.assertEqual(self.proxies[0], value)

    def test_seeded_cookies_reach_the_challenge_page(self):
        solution = self.solve(
            ACCEPTED,
            cookies=[{"name": "_pxvid", "value": "registration-vid",
                      "domain": ".px.test", "path": "/", "secure": True}],
        )
        self.assertEqual(solution.cookies["_pxvid"], "registration-vid")
        self.assertEqual(solution.token, "offline-px3-token")

    def test_seeded_px3_is_not_mistaken_for_the_press_token(self):
        solution = self.solve(
            ACCEPTED,
            cookies={"_px3": "silent-token"},
            cookie_domain=".px.test",
        )
        self.assertEqual(solution.token, "offline-px3-token")

    def test_seeded_px3_without_a_fresh_token_is_a_failure(self):
        with self.assertRaises(PerimeterXError) as caught:
            self.solve(ACCEPTED_NO_COOKIE,
                       cookies={"_px3": "silent-token"},
                       cookie_domain=".px.test")
        self.assertEqual(caught.exception.code, "missing_cookie")

    def test_metrics_hook_can_be_disabled_for_other_px_builds(self):
        class HookContext(Context):
            def navigate(self, url):
                self.load_page(url, page("<p>hook fixture</p>"))

        with HookContext(shell=SHELL, timeout=10) as context:
            provider._install_page_hooks(
                context, {"roots": []}, metrics_hook=False)
            context.eval("""
              globalThis.events = [];
              events.push({t:'PX561', d:{}});
              events.push({t:'P2APJXkNABU=', d:{}});
            """)
            types = json.loads(context.eval(
                "JSON.stringify(events.map(function(e){return e.t}))"))
        self.assertEqual(types, ["PX561", "P2APJXkNABU="])

    def test_rejected_status_is_not_a_solution(self):
        with self.assertRaises(PerimeterXError) as caught:
            self.solve(REJECTED)
        self.assertEqual(caught.exception.code, "rejected")

    def test_missing_press_target_times_out(self):
        with self.assertRaises(PerimeterXError) as caught:
            self.solve(page("<p>no challenge here</p>"), timeout=1)
        self.assertEqual(caught.exception.code, "timeout")

    def test_metrics_hook_appends_ns4f_without_reordering(self):
        # The PX build used by the Walmart demo requires a metrics event that
        # its own queue misses. This checks the page hook in isolation.
        class HookContext(Context):
            def navigate(self, url):
                self.load_page(url, page("<p>hook fixture</p>"))

        with HookContext(shell=SHELL, timeout=10) as context:
            context.eval(provider._METRICS_HOOK)
            context.eval("""
              globalThis.events = [];
              events.push({t:'CzR7cU5WfEM=', d:{}});
              events.push({t:'PX561', d:{
                'IUIRR2csH3M=':'uuid', 'OkVKAH8qSzY=':'sensor',
                'bHcccikfE0A=':123, 'T3A/NQkeOwY=':456,
                'OkVKAHwqSzI=':789, 'ajUaMCxWHQA=':4}});
              events.push({t:'P2APJXkNABU=', d:{}});
            """)
            types = json.loads(context.eval(
                "JSON.stringify(events.map(function(e){return e.t}))"))
            metrics = json.loads(context.eval(
                "JSON.stringify(events[events.length - 1].d)"))
        self.assertEqual(types, ["CzR7cU5WfEM=", "PX561", "P2APJXkNABU=",
                                 "NS4Fa3BCClk="])
        self.assertEqual(metrics["IUIRR2csH3M="], "uuid")
        self.assertEqual(metrics["OkVKAH8qSzY="], "sensor")
        self.assertEqual(metrics["EFMgFlU/LyU="], {"en": ""})


if __name__ == "__main__":
    unittest.main()
