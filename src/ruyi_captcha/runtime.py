"""Check browser capabilities independently of a captcha provider."""
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path

from .errors import RuntimeUnavailable


def load_bindings():
    selected = os.getenv("RUYI_CAPTCHA_KERNEL", "auto")
    if selected not in ("auto", "bundled", "external"):
        raise RuntimeUnavailable("RUYI_CAPTCHA_KERNEL must be auto, bundled or external")
    name = "ruyi_captcha._vendor.ruyijs"
    if selected != "external":
        if importlib.util.find_spec(name) is not None:
            return importlib.import_module(name)
        if selected == "bundled":
            raise RuntimeUnavailable("This installation has no bundled kernel; install the platform wheel")
    try:
        import ruyijs
    except ImportError as error:
        raise RuntimeUnavailable("Install the compatible ff-ruyijs kernel described in README.md") from error
    return ruyijs


def create_context(*args, **kwargs):
    return load_bindings().Context(*args, **kwargs)


@dataclass(frozen=True)
class RuntimeInfo:
    version: str
    sha256: str
    trusted_types: bool
    closed_shadow_host: bool
    trusted_input: bool
    frame_input: bool
    bindings_source: str

    def to_dict(self):
        return asdict(self)


def check_runtime(shell=None):
    bindings = load_bindings()
    try:
        if shell is None:
            engine = bindings._engine.default_engine()
            if engine is not None:
                shell = engine.shell
            else:
                name = "js.exe" if os.name == "nt" else "js"
                shell = Path(bindings.get_runtime_path()) / "dist" / name
        path = Path(shell).resolve()
        stat = path.stat()
        return _probe(str(path), stat.st_size, stat.st_mtime_ns)
    except RuntimeUnavailable:
        raise
    except Exception as error:
        raise RuntimeUnavailable(f"Cannot start the ff-ruyijs kernel: {error}") from error


@lru_cache(maxsize=8)
def _probe(shell, size, modified):
    bindings = load_bindings()
    # An explicit Engine keeps this probe independent of a caller's use_engine.
    with bindings.Engine(shell=shell, timeout=10, prewarm=0) as engine, engine.context(timeout=10, proxy=None) as context:
        context.load_page("https://runtime-check.invalid/", '''<!doctype html><div id=host></div><script>
          globalThis.__probeClick=false;
          (()=>{const host=document.getElementById('host');
            const root=host.attachShadow({mode:'closed'});
            root.innerHTML='<button style="width:40px;height:20px">ok</button>';
            root.querySelector('button').onclick=e=>__probeClick=e.isTrusted;
          })();</script>''')
        shadow = context.eval("document.getElementById('host').shadowRoot!==null")
        trusted_input = False
        if shadow:
            context.click("document.getElementById('host').shadowRoot.querySelector('button')", hold_ms=0)
            trusted_input = context.eval("__probeClick") is True
        context.load_page("https://runtime-check.invalid/trusted", '''<!doctype html>
          <meta http-equiv="Content-Security-Policy" content="require-trusted-types-for 'script'">''')
        trusted = context.eval('''(()=>{let mask=0;
          trustedTypes.createPolicy('default',{
            createHTML(s){mask|=1;return s},createScript(s){mask|=2;return s},
            createScriptURL(s){mask|=4;return s}});
          document.createElement('div').innerHTML='<b>ok</b>';
          window.eval('1+1');document.createElement('script').src='data:,0';return mask===7;
        })()''') is True
        context.put_resource("https://runtime-check.invalid/child", '''<!doctype html>
          <style>html,body{margin:0}button{display:block;width:100%;height:40px}</style>
          <button>frame</button><script>
          document.querySelector('button').onclick=e=>globalThis.__frameX=e.clientX;
          </script>''')
        context.load_page("https://runtime-check.invalid/frame", '''<!doctype html>
          <iframe style="width:300px;height:100px" src="https://runtime-check.invalid/child"></iframe>''')
        context.advance(10)
        context.click("document.querySelector('iframe').contentDocument.querySelector('button')", hold_ms=0)
        frame_input = context.eval("document.querySelector('iframe').contentWindow.__frameX") == 150
    info = RuntimeInfo(str(bindings.__version__), hashlib.sha256(Path(shell).read_bytes()).hexdigest(),
                       trusted, bool(shadow), bool(trusted_input), bool(frame_input),
                       "bundled" if bindings.__name__.startswith("ruyi_captcha._vendor.") else "external")
    if not (info.trusted_types and info.closed_shadow_host and info.trusted_input and info.frame_input):
        raise RuntimeUnavailable("The runtime lacks required Trusted Types or trusted-input capabilities; "
                                 "install the patched kernel pinned in runtime-lock.json")
    return info
