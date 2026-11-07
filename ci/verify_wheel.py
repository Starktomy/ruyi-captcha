"""Run outside the source tree to verify a lightweight or bundled wheel installation."""
import argparse
from importlib import util
from importlib.resources import files
import json
from pathlib import Path

import ruyi_captcha
from ruyi_captcha import ApiClient
from ruyi_captcha.runtime import check_runtime, load_bindings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--kind", choices=("lightweight", "bundled"), required=True)
args = parser.parse_args()
package = Path(ruyi_captcha.__file__).resolve()
assert "site-packages" in str(package), package
assert util.find_spec("ruyijs") is None, "An external kernel would hide a broken bundled installation"
assert ApiClient("http://127.0.0.1:8000").base_url
if args.kind == "lightweight":
    assert util.find_spec("ruyi_captcha._vendor.ruyijs") is None
else:
    bindings = load_bindings()
    assert bindings.__name__ == "ruyi_captcha._vendor.ruyijs"
    info = check_runtime()
    assert info.bindings_source == "bundled"
    manifest = json.loads(files("ruyi_captcha._vendor").joinpath("kernel-build.json").read_text())
    lock = json.loads(files("ruyi_captcha").joinpath("runtime-lock.json").read_text())
    assert manifest["kernel_commit"] == lock["commit"]
    assert manifest["packaged_shell_sha256"] == info.sha256
    with bindings.Context(timeout=20) as context:
        context.put_resource("https://package.test/a.js", "window.answer=42")
        context.load_page("https://package.test/", '<!doctype html><script src="/a.js"></script>')
        assert context.eval("answer") == 42
    from fastapi.testclient import TestClient
    from ruyi_captcha.api import create_app
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/v1/runtime").json()["bindings_source"] == "bundled"
    print(json.dumps(info.to_dict()))
print("Verified installed", args.kind, "wheel:", package)
