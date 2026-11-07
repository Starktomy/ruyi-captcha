"""Build one self-contained application wheel from a clean, pinned ff-ruyijs checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, default=Path("/root/ff-ruyijs"))
    parser.add_argument("--kernel-wheel", type=Path, help="Package an artifact produced by the pinned kernel CI job")
    parser.add_argument("--output", type=Path, default=Path("dist/bundled"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "src/ruyi_captcha/runtime-lock.json").read_text())
    commit = lock["commit"]
    if args.kernel_wheel:
        wheel = args.kernel_wheel.resolve()
    else:
        kernel = args.kernel.resolve()
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=kernel, text=True).strip()
        if actual != commit:
            raise SystemExit("Kernel checkout does not match runtime-lock.json")
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], cwd=kernel, check=True)
        cache = root / "artifacts/kernel-wheel" / commit
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                        "--wheel-dir", str(cache), str(kernel)], check=True)
        wheels = list(cache.glob("ruyijs-*.whl"))
        if len(wheels) != 1:
            raise SystemExit("Expected exactly one kernel wheel")
        wheel = wheels[0]
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    environment = dict(os.environ, RUYI_CAPTCHA_KERNEL_WHEEL=str(wheel), RUYI_CAPTCHA_KERNEL_WHEEL_SHA256=sha)
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir",
                    str(args.output.resolve())], cwd=root, env=environment, check=True)
    print(json.dumps({"kernel_commit": commit, "kernel_wheel": str(wheel), "sha256": sha,
                      "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
