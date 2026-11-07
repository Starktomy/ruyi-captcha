"""The diagnostic scanner must not trip page-installed DOM access monitors."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from ruyi_captcha.errors import RuntimeUnavailable
from ruyi_captcha.runtime import load_bindings

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("perimeterx_probe", ROOT / "tools/probe_perimeterx.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
try:
    bindings = load_bindings()
    SHELL = Path(bindings.get_runtime_path()) / "dist" / ("js.exe" if sys.platform == "win32" else "js")
except (ImportError, OSError, FileNotFoundError, RuntimeUnavailable):
    bindings = None
    SHELL = Path("/runtime-not-installed/js")


class PerimeterXUrlTests(unittest.TestCase):
    def test_public_urls_remove_credentials_and_challenge_queries(self):
        self.assertEqual(probe.public_url("https://user:secret@example.test:8443/path?token=secret#secret"),
                         "https://example.test:8443/path")
        self.assertEqual(probe.public_url("http://user:secret@[::1]:8080/path?secret"),
                         "http://[::1]:8080/path")
        self.assertNotIn("secret", probe.public_url("data:image/png;base64,secret"))


@unittest.skipUnless(SHELL.is_file(), "compatible native runtime is not installed")
class PerimeterXProbeTests(unittest.TestCase):
    def test_hold_sequence_delivers_firefox_flags_to_the_frame(self):
        captured=[]
        class OfflineContext(bindings.Context):
            def navigate(self,url):
                self.put_resource('https://probe.test/child', '''<!doctype html>
                    <div id="px-captcha" role="button" aria-label="Press & Hold Human Challenge"
                        style="width:200px;height:80px">Press &amp; Hold</div><p role="alert"></p>
                    <script>
                    globalThis.events=[];
                    globalThis._pxAppId='PXfixture';
                    globalThis._fixturehandler={PX764(status,token){
                        if(status!==-1 || token!=='offline-observer-secret')throw new Error('callback arguments changed');
                        document.querySelector('p').textContent='Please try again';
                    }};
                    const b=document.querySelector('div');
                    for(const type of ['pointerover','pointerenter','mouseover','mouseenter',
                        'pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click'])
                        b.addEventListener(type,e=>events.push([e.type,e.detail,e.bubbles,
                            e.cancelable,e.isTrusted,e.view===window]));
                    b.addEventListener('pointerdown',()=>setTimeout(()=>
                        b.setAttribute('aria-label','Human Challenge completed, please wait'),80));
                    b.addEventListener('mouseup',()=>_fixturehandler.PX764(-1,'offline-observer-secret'));
                    </script>''')
                self.load_page(url,'<!doctype html><iframe src="https://probe.test/child"></iframe>')
            def close(self):
                if not self.closed:
                    try:captured.extend(self.eval("document.querySelector('iframe').contentWindow.events"))
                    finally:super().close()
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.json'
            argv=['probe','--url','https://probe.test/','--hold','1','--until-complete',
                  '--observe-server-result','--timeout','3','--output',str(output)]
            with patch.object(probe,'load_bindings',return_value=SimpleNamespace(
                    Context=OfflineContext,JSError=bindings.JSError)),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                probe.main()
            report=json.loads(output.read_text())
        expected=[['pointerover',0,True,True],['pointerenter',0,False,False],
                  ['mouseover',0,True,True],['mouseenter',0,False,False],
                  ['pointermove',0,True,True],['mousemove',0,True,True],
                  ['pointerdown',0,True,True],['mousedown',1,True,True],
                  ['pointerup',0,True,True],['mouseup',1,True,True],['click',1,True,True]]
        self.assertEqual(captured,[row+[True,True] for row in expected])
        self.assertEqual(report['outcome'],'rejected')
        self.assertFalse(report['verified'])
        self.assertTrue(report['instrumented'])
        self.assertEqual(report['server_results'],[{'status':-1}])
        self.assertNotIn('offline-observer-secret',json.dumps(report))
        self.assertEqual([row['event'] for row in report['input']],['down','up'])

    def test_closed_frame_scan_avoids_page_hooks_and_redacts_secrets(self):
        with bindings.Context(shell=SHELL) as context:
            context.put_resource("https://probe.test/widget", """<!doctype html>
                <div role="button" aria-label="Press & Hold Human Challenge"
                    style="width:200px;height:80px">Press &amp; Hold</div>
                <script>
                const query = Document.prototype.querySelectorAll;
                Document.prototype.querySelectorAll = function(...args) {
                    parent.hookReads++;return query.apply(this,args);
                };
                const attr = Element.prototype.getAttribute;
                Element.prototype.getAttribute = function(...args) {
                    parent.hookReads++;return attr.apply(this,args);
                };
                </script>""")
            context.load_page("https://probe.test/", """<!doctype html>
                <div id="px-captcha"></div><script>
                globalThis.hookReads=0;
                document.cookie='diagnostic=offline-cookie-secret';
                const root=document.querySelector('#px-captcha').attachShadow({mode:'closed'});
                const decoy=document.createElement('iframe');
                decoy.style.display='none';decoy.setAttribute('token','offline-test-secret');
                root.appendChild(decoy);
                for(const key of ['nodeType','nodeName','localName','textContent','id','className','getAttribute'])
                    Object.defineProperty(decoy,key,{get(){hookReads++;return undefined;}});
                const real=document.createElement('iframe');
                real.src='https://probe.test/widget';root.appendChild(real);
                </script>""")
            context.advance(20)
            encoded = context.eval("JSON.stringify(" + probe.STATE + ")")
            state = json.loads(encoded)
            self.assertEqual(context.eval("hookReads"), 0)
            self.assertEqual(len(state["buttons"]), 1)
            button = state["buttons"][0]
            self.assertEqual(button["aria"], "Press & Hold Human Challenge")
            self.assertTrue(button["visible"])
            self.assertGreater(button["rect"]["width"], 0)
            self.assertEqual(context.eval(button["path"] + ".textContent"), "Press & Hold")
            self.assertNotIn("offline-test-secret", encoded)
            self.assertNotIn("offline-cookie-secret", encoded)
            self.assertIn("diagnostic", state["documents"][0]["cookies"])


if __name__ == "__main__":
    unittest.main()
