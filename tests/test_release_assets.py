"""The complete release must contain the Linux wheel built for Debian 12."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.version_info >= (3, 11), "the release tool runs on Python 3.11+")
class ReleaseAssetsTests(unittest.TestCase):
    def assets(self, folder, platform):
        version = re.search(r'^version\s*=\s*"([^"]+)"',
                            (ROOT / "pyproject.toml").read_text(), re.MULTILINE).group(1)
        for suffix in ("py3-none-any.whl", "py3-none-win_amd64.whl", "py3-none-macosx_26_0_arm64.whl",
                       "py3-none-" + platform + ".whl"):
            (folder / ("ruyi_captcha-" + version + "-" + suffix)).write_bytes(b"release fixture")
        (folder / ("ruyi_captcha-" + version + ".tar.gz")).write_bytes(b"source fixture")
        return version

    def run_release(self, folder):
        environment = {**os.environ, "GITHUB_REF": "", "GITHUB_OUTPUT": str(folder / "job-output")}
        return subprocess.run([sys.executable, "-B", str(ROOT / "ci/release_metadata.py"), "--assets", str(folder)],
                              text=True, capture_output=True, env=environment)

    def test_release_inventories_the_236_wheel(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            version = self.assets(folder, "manylinux_2_36_x86_64")
            result = self.run_release(folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((folder / "release-manifest.json").read_text())
            self.assertEqual(manifest["version"], version)
            self.assertEqual(len(manifest["artifacts"]), 5)
            self.assertTrue(any(name.endswith("-manylinux_2_36_x86_64.whl") for name in manifest["artifacts"]))
            self.assertEqual(len((folder / "SHA256SUMS").read_text().splitlines()), 5)

    def test_older_release_configuration_cannot_publish_a_239_wheel(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.assets(folder, "manylinux_2_39_x86_64")
            result = self.run_release(folder)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing or duplicate release artifact: -manylinux_2_36_x86_64.whl", result.stderr)
            self.assertFalse((folder / "release-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
