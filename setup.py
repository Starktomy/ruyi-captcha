"""Optional platform wheel assembly from a pinned ff-ruyijs wheel.

Source repositories stay independent. RUYI_CAPTCHA_KERNEL_WHEEL selects a
previously built kernel artifact; ordinary builds remain lightweight.
"""
import hashlib
import json
from email.parser import BytesParser
import os
from pathlib import Path, PurePosixPath
import stat
import shutil
import zipfile

from packaging.utils import parse_wheel_filename
from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py


def kernel_input():
    raw = os.getenv("RUYI_CAPTCHA_KERNEL_WHEEL")
    if not raw:
        return None
    path = Path(raw).resolve()
    expected = os.getenv("RUYI_CAPTCHA_KERNEL_WHEEL_SHA256")
    if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Kernel wheel SHA256 does not match the build's pinned input")
    name, version, _, tags = parse_wheel_filename(path.name)
    lock = json.loads((Path(__file__).parent / "src/ruyi_captcha/runtime-lock.json").read_text())
    platforms = {tag.platform for tag in tags}
    if name != "ruyijs" or str(version) != lock["version"] or len(platforms) != 1 or "any" in platforms:
        raise ValueError("Expected one platform-specific ruyijs wheel matching runtime-lock.json")
    return path, next(iter(platforms)), lock, expected


class BuildPython(build_py):
    def run(self):
        super().run()
        vendor = Path(self.build_lib) / "ruyi_captcha/_vendor"
        target = vendor / "ruyijs"
        # Building a lightweight wheel after a platform build must not retain
        # an old native runtime from build/.
        for old in (target, vendor / "kernel-licenses", *(Path(self.build_lib) / name
                     for name in ("necko", "ruyijs_necko", "necko_win"))):
            if old.exists():
                shutil.rmtree(old)
        manifest = vendor / "kernel-build.json"
        if manifest.exists():
            manifest.unlink()
        bundle = kernel_input()
        if bundle is None:
            return
        wheel, platform_tag, lock, expected = bundle
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    raise ValueError("Unsafe kernel wheel member")
                if member.is_dir():
                    continue
                if path.parts[0] == "ruyijs":
                    if len(path.parts) > 1 and path.parts[1] == "examples":
                        continue
                    destination = vendor / path
                elif path.parts[0] in ("necko", "ruyijs_necko", "necko_win"):
                    # macOS's helper uses the installing Python and -I -m
                    # ruyijs_necko.oneshot, as in the kernel's own wheel.
                    destination = Path(self.build_lib) / path
                elif len(path.parts) >= 3 and path.parts[0].endswith(".dist-info") and path.parts[1] == "licenses":
                    destination = vendor / "kernel-licenses" / Path(*path.parts[2:])
                else:
                    continue
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError("Kernel wheels must contain files, not symlinks")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
                if mode & 0o111:
                    destination.chmod(0o755)
        binary = target / "_runtime/dist" / ("js.exe" if platform_tag == "win_amd64" else "js")
        if not binary.is_file() or not (target / "__init__.py").is_file():
            raise ValueError("Kernel wheel lacks Python bindings or a bundled native runtime")
        manifest.write_text(json.dumps({"kernel_commit": lock["commit"], "kernel_wheel": wheel.name,
            "kernel_wheel_sha256": expected, "platform": platform_tag,
            "packaged_shell_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2)+'\n')

    def get_outputs(self, include_bytecode=True):
        outputs = list(super().get_outputs(include_bytecode=include_bytecode))
        vendor = Path(self.build_lib) / "ruyi_captcha/_vendor"
        if vendor.exists():
            outputs.extend(str(path) for path in vendor.rglob("*") if path.is_file())
        for name in ("necko", "ruyijs_necko", "necko_win"):
            folder = Path(self.build_lib) / name
            if folder.exists():
                outputs.extend(str(path) for path in folder.rglob("*") if path.is_file())
        return sorted(set(outputs))


class PlatformDistribution(Distribution):
    def has_ext_modules(self):
        return bool(os.getenv("RUYI_CAPTCHA_KERNEL_WHEEL"))


class PlatformWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        bundle = kernel_input()
        if bundle:
            self.root_is_pure = False
            self.plat_name = bundle[1]

    def get_tag(self):
        bundle = kernel_input()
        return ("py3", "none", bundle[1]) if bundle else super().get_tag()


def kernel_dependencies():
    bundle = kernel_input()
    if bundle is None:
        return []
    with zipfile.ZipFile(bundle[0]) as archive:
        metadata = next(name for name in archive.namelist()
                        if len(PurePosixPath(name).parts) == 2
                        and name.startswith("ruyijs-") and name.endswith(".dist-info/METADATA"))
        return BytesParser().parsebytes(archive.read(metadata)).get_all("Requires-Dist", [])


setup(distclass=PlatformDistribution, install_requires=kernel_dependencies(),
      cmdclass={"build_py": BuildPython, "bdist_wheel": PlatformWheel})
