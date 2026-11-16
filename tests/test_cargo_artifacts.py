"""Link real Cargo output as a C library, including Cargo 1.100's new layout."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from cargo_artifacts import target_rlibs


class CargoArtifactTests(unittest.TestCase):
    def test_no_artifacts_fails_before_archiving(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "no target rlibs"):
                target_rlibs('{"reason":"build-finished","success":true}\n', folder)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cargo")
                         and shutil.which("cc") and shutil.which("ar"),
                         "Cargo and a C toolchain are required")
    def test_real_target_rlibs_link_and_fresh_output_still_reports_them(self):
        cargo = os.environ.get("CARGO_TEST_BIN", "cargo")
        host = subprocess.check_output(["rustc", "-vV"], text=True).split("host: ")[1].splitlines()[0]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dep/src").mkdir(parents=True)
            (root / "src").mkdir()
            (root / "dep/Cargo.toml").write_text(
                '[package]\nname="fixture_dep"\nversion="0.1.0"\nedition="2021"\n')
            (root / "dep/src/lib.rs").write_text(
                '#![no_std]\n#[inline(never)] pub fn answer() -> u32 {42}\n')
            (root / "Cargo.toml").write_text(
                '[package]\nname="fixture_probe"\nversion="0.1.0"\nedition="2021"\n'
                '[dependencies]\nfixture_dep={path="dep"}\n[lib]\ncrate-type=["rlib"]\n'
                '[profile.release]\npanic="abort"\n')
            (root / "src/lib.rs").write_text(
                '#![no_std]\n#[no_mangle] pub extern "C" fn fixture_answer() -> u32 '
                '{fixture_dep::answer()}\n')
            command = [cargo, "build", "--release", "--offline", "--target", host,
                       "--message-format=json-render-diagnostics"]
            first = subprocess.check_output(command, cwd=root, text=True)
            libraries = target_rlibs(first, root / "target" / host / "release")
            self.assertEqual(len(libraries), 2)
            second = subprocess.check_output(command, cwd=root, text=True)
            self.assertEqual(target_rlibs(second, root / "target" / host / "release"), libraries)
            archive = root / "fixture.a"
            script = f"CREATE {archive}\n" + "".join(f"ADDLIB {lib}\n" for lib in libraries) + "SAVE\nEND\n"
            subprocess.run(["ar", "-M"], input=script, text=True, check=True)
            (root / "main.c").write_text(
                'extern unsigned fixture_answer(void); int main(void) {return fixture_answer()!=42;}\n')
            executable = root / "check"
            subprocess.run(["cc", str(root / "main.c"), str(archive), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
