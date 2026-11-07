import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    from ruyi_captcha.runtime import load_bindings
    bindings = load_bindings()
    Context, Engine, use_engine, get_runtime_path = (bindings.Context, bindings.Engine,
                                                    bindings.use_engine, bindings.get_runtime_path)
    SHELL = Path(get_runtime_path()) / 'dist' / ('js.exe' if sys.platform == 'win32' else 'js')
except (ImportError, OSError, FileNotFoundError, __import__('ruyi_captcha').RuntimeUnavailable):
    Context = None
    SHELL = Path('/runtime-not-installed/js')
from ruyi_captcha import TurnstileError, solve_turnstile
from ruyi_captcha.providers import turnstile as solver

SITE_URL = "https://solver.test/"
SITEKEY = "0x4AAAAAADogn3t3_JKwKkgS"
API_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js"
FRAME_URL = "https://challenges.cloudflare.com/offline-test-widget"
# Local fixtures only; these strings are not usable Turnstile tokens.
TOKEN = "offline-test-" + "x" * 240
FRESH_TOKEN = "offline-refreshed-" + "y" * 240
SUCCESS_API = "globalThis.turnstile={render(target, options){options.callback(" + json.dumps(TOKEN) + ");return 'widget'}};"


class TurnstileApiTests(unittest.TestCase):
    def test_input_validation(self):
        for site_url in ("", "ftp://example.test", "https://user@example.test/"):
            with self.subTest(site_url=site_url), self.assertRaises(ValueError):
                solve_turnstile(site_url, "0x4AAAAAADogn3t3_JKwKkgS")
        for sitekey in ("", "short", "invalid sitekey!", "x" * 256):
            with self.subTest(sitekey=sitekey), self.assertRaises(ValueError):
                solve_turnstile("https://example.test/", sitekey)

    def test_action_and_metadata_validation(self):
        with self.assertRaises(ValueError):
            solve_turnstile(SITE_URL, SITEKEY, action="invalid action")
        for cdata in ("", "line\nbreak", "user 123", "session:123", '{"id":1}',
                      "用户", "x" * 256, 123):
            with self.subTest(cdata=cdata), patch.object(solver, "Context") as context:
                with self.assertRaises(ValueError):
                    solve_turnstile(SITE_URL, SITEKEY, cdata=cdata)
                context.assert_not_called()

    @unittest.skipUnless(SHELL.is_file(), "compatible native runtime is not installed")
    def test_startup_timeout_uses_all_attempts(self):
        with patch.object(solver, "Context", side_effect=TimeoutError("startup timeout")) as context:
            with self.assertRaises(TurnstileError) as caught:
                solve_turnstile(SITE_URL, SITEKEY, attempts=3, profile=None)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(context.call_count, 3)


@unittest.skipUnless(SHELL.is_file(), "build the native runtime first")
class TurnstileRuntimeTests(unittest.TestCase):
    def solve(self, scripts, *, resources=None, **options):
        scripts = [scripts] if isinstance(scripts, str) else scripts
        self.contexts = []
        self.clicks = []
        self.advances = []
        test = self

        class OfflineContext(Context):
            def __init__(self, **kwargs):
                script = scripts[min(len(test.contexts), len(scripts) - 1)]
                super().__init__(**kwargs)
                test.contexts.append(self)
                self.put_resource(API_URL, script)
                for url, body in (resources or {}).items():
                    self.put_resource(url, body)

            def advance(self, ms, **kwargs):
                test.advances.append((ms, self.timeout))
                return super().advance(ms, **kwargs)

            def click(self, target, **kwargs):
                test.clicks.append(target)
                return super().click(target, **kwargs)

        with patch.object(solver, "Context", OfflineContext):
            return solver.solve_turnstile_details(SITE_URL, SITEKEY, **{
                "shell": SHELL, "profile": None, "timeout": 2, "attempts": 1,
                "poll_ms": 20, **options,
            })

    def test_callback_names_remain_metadata_strings(self):
        for value in ("login", "ruyijsToken", "ruyijsError", "ruyijsExpired", "ruyijsTimeout"):
            with self.subTest(value=value):
                script = """globalThis.turnstile={render(target, options){
                    if (options.action!==VALUE || options.cData!==VALUE)
                        throw new Error('metadata was changed');
                    options.callback(TOKEN);return 'widget';
                }};""".replace("VALUE", json.dumps(value)).replace("TOKEN", json.dumps(TOKEN))
                solution = self.solve(script, action=value, cdata=value)
                self.assertEqual(solution.token, TOKEN)

    def test_metadata_maximum_lengths(self):
        script = """globalThis.turnstile={render(target, options){
            if(options.action!=='a'.repeat(32) || options.cData!=='b'.repeat(255))
                throw new Error('metadata was changed');
            options.callback(TOKEN);return 'widget';
        }};""".replace("TOKEN", json.dumps(TOKEN))
        self.assertEqual(self.solve(script, action="a" * 32, cdata="b" * 255).token, TOKEN)

    def test_poll_is_bounded_and_timeout_retries(self):
        with self.assertRaises(TurnstileError) as caught:
            self.solve("globalThis.turnstile={render(){return 'widget'}};",
                       timeout=0.5, poll_ms=1000, attempts=3)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(len(self.contexts), 3)
        self.assertTrue(all(context.closed for context in self.contexts))
        self.assertTrue(self.advances)
        for wait_ms, rpc_timeout in self.advances:
            self.assertLess(wait_ms, rpc_timeout * 1000)

    def test_runtime_timeout_retries_with_a_fresh_context(self):
        solution = self.solve([
            "globalThis.turnstile={render(){while(true){}}};", SUCCESS_API,
        ], timeout=0.5, attempts=2)
        self.assertEqual(solution.token, TOKEN)
        self.assertEqual(solution.attempts, 2)
        self.assertEqual(len(self.contexts), 2)
        self.assertTrue(all(context.closed for context in self.contexts))

    def test_nonblocking_rpc_deadline_uses_a_fresh_attempt(self):
        contexts = []

        class DeadlineContext(Context):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                contexts.append(self)
                self.put_resource(API_URL, SUCCESS_API)

            def advance(self, *args, **kwargs):
                if len(contexts) == 1:
                    raise BlockingIOError(11, "Resource temporarily unavailable")
                return super().advance(*args, **kwargs)

        with patch.object(solver, "Context", DeadlineContext):
            result = solver.solve_turnstile_details(SITE_URL, SITEKEY, shell=SHELL,
                profile=None, timeout=2, attempts=2)
        self.assertEqual(result.token, TOKEN)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(contexts), 2)
        self.assertTrue(all(context.closed for context in contexts))

    def test_refreshed_checkbox_can_be_clicked_at_the_same_path(self):
        api = """globalThis.turnstile={render(target, options){
            window.addEventListener('message', event=>{
                if(event.data==='test-timeout') options['timeout-callback']();
                if(event.data==='test-success') options.callback(TOKEN);
            });
            const frame=document.createElement('iframe');frame.src=FRAME_URL;
            document.querySelector(target).appendChild(frame);return 'widget';
        }};""".replace("TOKEN", json.dumps(TOKEN)).replace("FRAME_URL", json.dumps(FRAME_URL))
        frame = """<!doctype html><html><body><script>
            let clicks=0;
            function mount(){
                document.body.innerHTML='<input type="checkbox" style="width:24px;height:24px">';
                document.querySelector('input').addEventListener('click',()=>{
                    if(++clicks===1){
                        if(SIGNAL) parent.postMessage('test-timeout','*');
                        if(REPLACE) setTimeout(mount,1);
                    }else parent.postMessage('test-success','*');
                });
            }
            mount();
            </script></body></html>"""
        for signal, replace in ((True, True), (True, False), (False, True)):
            with self.subTest(timeout_callback=signal, replace_element=replace):
                resource = frame.replace("SIGNAL", json.dumps(signal)).replace("REPLACE", json.dumps(replace))
                solution = self.solve(api, resources={FRAME_URL: resource})
                self.assertEqual(solution.token, TOKEN)
                self.assertEqual(len(self.clicks), 2)
                self.assertEqual(self.clicks[0], self.clicks[1])

    def test_expired_token_is_replaced_before_returning(self):
        script = """globalThis.turnstile={render(target, options){
            options.callback(OLD_TOKEN);options['expired-callback']();
            setTimeout(()=>options.callback(FRESH_TOKEN),60);return 'widget';
        }};""".replace("OLD_TOKEN", json.dumps(TOKEN)).replace("FRESH_TOKEN", json.dumps(FRESH_TOKEN))
        self.assertEqual(self.solve(script).token, FRESH_TOKEN)

    def test_expired_token_without_refresh_times_out(self):
        script = """globalThis.turnstile={render(target, options){
            options.callback(TOKEN);options['expired-callback']();return 'widget';
        }};""".replace("TOKEN", json.dumps(TOKEN))
        with self.assertRaises(TurnstileError) as caught:
            self.solve(script, timeout=0.3)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertIn({"event": "expired"}, caught.exception.events)


    def test_cancellation_before_opening_an_attempt(self):
        import threading
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(__import__('ruyi_captcha').SolveCancelled):
            self.solve(SUCCESS_API, _cancel_event=cancel)
        self.assertEqual(self.contexts, [])

    def test_callback_cancellation_closes_a_running_context(self):
        import threading
        from ruyi_captcha import SolveCancelled
        cancel = threading.Event()
        contexts = []

        class CancelContext(Context):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                contexts.append(self)
                self.put_resource(API_URL, "globalThis.turnstile={render(){cancelFromPython();return 'widget'}};")

            def load_page(self, *args, **kwargs):
                result = super().load_page(*args, **kwargs)
                self.expose(cancel.set, "cancelFromPython")
                return result

        with patch.object(solver, "Context", CancelContext), self.assertRaises(SolveCancelled):
            solver.solve_turnstile_details(SITE_URL, SITEKEY, shell=SHELL, profile=None,
                                           timeout=2, attempts=3, _cancel_event=cancel)
        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0].closed)

    def test_solver_can_use_an_existing_engine_after_a_cold_probe(self):
        from ruyi_captcha.runtime import _probe
        _probe.cache_clear()
        with Engine(shell=SHELL, timeout=10, prewarm=0) as engine, use_engine(engine):
            solution = self.solve(SUCCESS_API, shell=None)
            self.assertEqual(solution.token, TOKEN)
            self.assertTrue(all(context.closed for context in self.contexts))


if __name__ == "__main__":
    unittest.main()
