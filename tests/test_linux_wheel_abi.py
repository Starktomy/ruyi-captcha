"""Exercise the Linux wheel ABI gate with real versioned shared libraries."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wheel_abi", ROOT / "ci/check_linux_wheel.py")
abi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(abi)


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("cc") and shutil.which("readelf"),
                     "ELF fixture checks require a Linux compiler and readelf")
class LinuxWheelAbiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "fixture.c"
        source.write_text("#define _GNU_SOURCE\n#include <unistd.h>\n"
                          "int versioned_call(void) { return close_range(3, 4, 0); }\n")
        self.library = self.root / "libfixture.so"
        subprocess.run(["cc", "-shared", "-fPIC", str(source), "-o", str(self.library),
                        "-Wl,-z,nopack-relative-relocs"], check=True)

    def wheel(self, floor="2_36", library=None):
        platform = "manylinux_" + floor + "_x86_64"
        target = self.root / ("fixture-1-py3-none-" + platform + ".whl")
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("fixture-1.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-" + platform + "\n")
            archive.write(library or self.library, "fixture/libfixture.so")
        return target

    def test_accepts_real_glibc_234_requirements_on_a_236_floor(self):
        result = abi.check_wheel(self.wheel())
        self.assertEqual(result["maximum_required_glibc"], "2.34")
        self.assertEqual(result["elf_files"], 1)

    def test_rejects_newer_symbols_hidden_by_an_older_wheel_tag(self):
        with self.assertRaisesRegex(ValueError, "requires GLIBC_2.34"):
            abi.check_wheel(self.wheel("2_28"))

    def test_rejects_a_newer_platform_tag(self):
        with self.assertRaisesRegex(ValueError, "exceeds glibc 2.36"):
            abi.check_wheel(self.wheel("2_39"))

    def test_packed_relative_relocations_require_glibc_236(self):
        output = self.root / "librelr.so"
        subprocess.run(["cc", "-shared", "-fPIC", str(self.root / "fixture.c"), "-o", str(output),
                        "-Wl,-z,pack-relative-relocs"], check=True)
        result = abi.check_wheel(self.wheel(library=output))
        self.assertEqual(result["maximum_required_glibc"], "2.36")
        with self.assertRaisesRegex(ValueError, "requires GLIBC_2.36"):
            abi.check_wheel(self.wheel("2_35", library=output))

    def test_rejects_missing_bundled_library(self):
        provider = self.root / "provider.c"
        provider.write_text("int provided(void) { return 42; }\n")
        subprocess.run(["cc", "-shared", "-fPIC", str(provider), "-o", str(self.root / "libprovider.so"),
                        "-Wl,-soname,libprovider.so"], check=True)
        consumer = self.root / "consumer.c"
        consumer.write_text("extern int provided(void); int use(void) { return provided(); }\n")
        output = self.root / "libconsumer.so"
        subprocess.run(["cc", "-shared", "-fPIC", str(consumer), "-L" + str(self.root),
                        "-lprovider", "-o", str(output)], check=True)
        with self.assertRaisesRegex(ValueError, "unbundled dependencies: libprovider.so"):
            abi.check_wheel(self.wheel(library=output))

    def test_rejects_absolute_library_search_paths(self):
        output = self.root / "libabsolute.so"
        source = self.root / "fixture.c"
        subprocess.run(["cc", "-shared", "-fPIC", str(source), "-o", str(output),
                        "-Wl,-rpath,/build/host/lib"], check=True)
        with self.assertRaisesRegex(ValueError, "non-relocatable"):
            abi.check_wheel(self.wheel(library=output))


if __name__ == "__main__":
    unittest.main()
