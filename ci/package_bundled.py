"""Package the kernel artifact from this release run; install only the application wheel."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
kernel = list((ROOT / "kernel-artifact").rglob("ruyijs-*.whl"))
if len(kernel) != 1:
    raise SystemExit("Expected one kernel wheel from the pinned kernel build job")
subprocess.run([sys.executable, str(ROOT / "tools/build_bundled.py"),
                "--kernel-wheel", str(kernel[0]), "--output", str(ROOT / "dist/bundled")], check=True, cwd=ROOT)
wheels = list((ROOT / "dist/bundled").glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit("Expected exactly one application wheel")
subprocess.run([sys.executable, "-m", "pip", "install", str(wheels[0])+"[api,test]"], check=True)
