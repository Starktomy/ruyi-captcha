"""Validate a release tag, publish the kernel pin as an output, and inventory assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    lock = json.loads((ROOT / "src/ruyi_captcha/runtime-lock.json").read_text())
    if len(lock["commit"]) != 40 or any(c not in "0123456789abcdef" for c in lock["commit"]):
        raise SystemExit("The kernel must be pinned to an exact commit")
    ref = os.getenv("GITHUB_REF", "")
    if ref.startswith("refs/tags/") and ref != "refs/tags/v"+version:
        raise SystemExit("Release tag does not match pyproject.toml")
    if output := os.getenv("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write("kernel_ref="+lock["commit"]+"\n")
    if args.assets:
        assets = sorted(path for path in args.assets.iterdir() if path.suffix == ".whl" or path.name.endswith(".tar.gz"))
        expected = ("-any.whl", "-win_amd64.whl", "-macosx_26_0_arm64.whl", "-manylinux_2_39_x86_64.whl", ".tar.gz")
        for suffix in expected:
            if sum(path.name.endswith(suffix) for path in assets) != 1:
                raise SystemExit("Missing or duplicate release artifact: "+suffix)
        if len(assets) != 5 or any(not (path.name.startswith("ruyi_captcha-"+version+"-")
                or path.name == "ruyi_captcha-"+version+".tar.gz") for path in assets):
            raise SystemExit("Unexpected wheel version or release artifact")
        checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
        (args.assets / "SHA256SUMS").write_text("".join(f"{sha}  {name}\n" for name, sha in checksums.items()))
        (args.assets / "release-manifest.json").write_text(json.dumps({"version": version,
            "kernel": lock, "artifacts": checksums}, indent=2)+'\n')
    print(json.dumps({"version": version, "kernel_ref": lock["commit"]}))


if __name__ == "__main__":
    main()
