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
    def test_fractional_frame_origin_does_not_make_screen_coordinates_fractional(self):
        point=probe.input_point({'x':0,'y':0,'width':240,'height':90},[32.25,267.4])
        self.assertEqual([point['screenX'],point['screenY']],[152,312])
        self.assertAlmostEqual(point['clientX'],119.75)
        self.assertAlmostEqual(point['clientY'],44.6)

    def test_public_urls_remove_credentials_and_challenge_queries(self):
        self.assertEqual(probe.public_url("https://user:secret@example.test:8443/path?token=secret#secret"),
                         "https://example.test:8443/path")
        self.assertEqual(probe.public_url("http://user:secret@[::1]:8080/path?secret"),
                         "http://[::1]:8080/path")
        self.assertNotIn("secret", probe.public_url("data:image/png;base64,secret"))


class PerimeterXReportLifecycleTests(unittest.TestCase):
    def test_runtime_startup_failure_still_writes_a_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'nested/report.json'
            argv=['probe','--profile','default','--output',str(output)]
            with patch.object(probe,'load_bindings',side_effect=RuntimeUnavailable('offline missing runtime')),\
                    patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                probe.main()
            report=json.loads(output.read_text())
        self.assertEqual(report['outcome'],'error')
        self.assertEqual(report['stage'],'startup')
        self.assertIn('offline missing runtime',report['error'])
        self.assertFalse(report['verified'])
        self.assertEqual(report['input'],[])

    def test_navigation_failure_closes_context_and_redacts_proxy(self):
        opened=[]
        closed=[]
        proxy_url='http://offline-user:offline-password@proxy.invalid:8888'
        class FailedNavigation:
            def __init__(self,**kwargs):opened.append(kwargs)
            def __enter__(self):return self
            def __exit__(self,*exc):closed.append(True)
            def configure_clock(self,mode):pass
            def navigate(self,url):raise TimeoutError('offline navigation through '+proxy_url)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.json'
            argv=['probe','--url','https://probe.test/','--profile','default','--timeout','1',
                  '--proxy-env','RUYI_PROBE_TEST_PROXY','--output',str(output)]
            with patch.dict('os.environ',{'RUYI_PROBE_TEST_PROXY':proxy_url}),\
                    patch.object(probe,'load_bindings',return_value=SimpleNamespace(Context=FailedNavigation)),\
                    patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                probe.main()
            encoded=output.read_text()
            report=json.loads(encoded)
        self.assertEqual(closed,[True])
        self.assertEqual(opened[0]['timeout'],1)
        self.assertEqual(report['outcome'],'error')
        self.assertEqual(report['stage'],'navigation')
        self.assertEqual(list(report['timings']),['startup','configuration','navigation'])
        self.assertIn('[proxy omitted]',report['error'])
        self.assertNotIn('offline-password',encoded)
        self.assertFalse(report['verified'])


@unittest.skipUnless(SHELL.is_file(), "compatible native runtime is not installed")
class PerimeterXProbeTests(unittest.TestCase):
    def test_diagnostics_failure_does_not_overwrite_rejection(self):
        class OfflineContext(bindings.Context):
            def navigate(self,url):
                self.load_page(url,'<!doctype html><p role="alert">Please try again</p>')
            def net_log(self):raise TimeoutError('offline log timeout')
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.json'
            argv=['probe','--url','https://probe.test/','--timeout','1','--output',str(output)]
            with patch.object(probe,'load_bindings',return_value=SimpleNamespace(
                    Context=OfflineContext,JSError=bindings.JSError)),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                probe.main()
            report=json.loads(output.read_text())
        self.assertEqual(report['outcome'],'rejected')
        self.assertFalse(report['verified'])
        self.assertEqual(report['stage'],'loading')
        self.assertEqual(report['diagnostic_errors'],[
            {'source':'network','error':'TimeoutError: offline log timeout'}])
        self.assertIn('console',report)

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
                            e.cancelable,e.composed,e.isTrusted,e.view===window]));
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
        self.assertEqual(captured,[row+[row[0] not in ('pointerenter','mouseenter'),True,True] for row in expected])
        self.assertEqual(report['outcome'],'rejected')
        self.assertFalse(report['verified'])
        self.assertTrue(report['instrumented'])
        self.assertEqual(report['server_results'],[{'status':-1}])
        self.assertNotIn('offline-observer-secret',json.dumps(report))
        self.assertEqual([row['event'] for row in report['input']],['down','up'])

    def test_hit_target_changes_and_server_result_survives_navigation(self):
        captured=[]
        closed_paths=[]
        class OfflineContext(bindings.Context):
            def navigate(self,url):
                self.put_resource('https://probe.test/done','<!doctype html><p>Done</p>')
                self.put_resource('https://probe.test/child','''<!doctype html>
                    <div id="px-captcha" role="button" aria-label="Press & Hold Human Challenge"
                      style="position:relative;width:200px;height:80px">
                        <div id="before" style="position:absolute;inset:0"></div>
                    </div><script>
                    const button=document.querySelector('#px-captcha'),events=[];
                    globalThis._pxAppId='PXfixture';
                    globalThis._fixturehandler={PX764(status,token){
                        if(status!==0||token!=='offline-private-token')throw new Error('callback arguments changed');
                        top.location.href='https://probe.test/done';
                    }};
                    for(const type of ['pointerdown','mouseup','click'])
                        button.addEventListener(type,e=>events.push([e.type,e.target.id]));
                    button.addEventListener('pointerdown',()=>setTimeout(()=>{
                        button.innerHTML='<div id="after" style="position:absolute;inset:0"></div>';
                        button.setAttribute('aria-label','Human Challenge completed, please wait');
                    },80));
                    button.addEventListener('click',()=>{
                        parent.__testRecordEvents(events);
                        _fixturehandler.PX764(0,'offline-private-token');
                    });
                    </script>''')
                self.load_page(url,'<!doctype html><iframe src="https://probe.test/child"></iframe>')
                self.expose(lambda rows:captured.extend(rows),'__testRecordEvents')
            def dispatch_input(self,event_type,**kwargs):
                delivered=super().dispatch_input(event_type,**kwargs)
                if event_type=='click':self.advance(100,max_tasks=400)
                return delivered
            def close(self):
                if not self.closed:
                    try:closed_paths.append(self.eval('location.pathname'))
                    finally:super().close()
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.json'
            argv=['probe','--url','https://probe.test/','--hit-test','--hold','1','--until-complete',
                  '--observe-server-result','--timeout','3','--output',str(output)]
            with patch.object(probe,'load_bindings',return_value=SimpleNamespace(
                    Context=OfflineContext,JSError=bindings.JSError)),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                probe.main()
            report=json.loads(output.read_text())
        self.assertEqual(captured,[['pointerdown','before'],['mouseup','after'],['click','px-captcha']])
        self.assertEqual(closed_paths,['/done'])
        self.assertEqual(report['server_results'],[{'status':0}])
        self.assertEqual(report['outcome'],'server_accepted_instrumented')
        self.assertTrue(report['instrumented'])
        self.assertFalse(report['verified'])
        self.assertTrue(report['input_targets']['down']['descendant'])
        self.assertTrue(report['input_targets']['up']['descendant'])
        self.assertNotIn('offline-private-token',json.dumps(report))

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
