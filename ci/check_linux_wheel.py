"""Check the glibc floor and ELF dependency closure of a Linux wheel."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


SYSTEM_LIBRARY = re.compile(
    r"^(?:lib(?:c|m|dl|pthread|rt|resolv|util|anl|nss_\w+)\.so(?:\.[\d.]+)?|ld-linux-x86-64\.so\.2)$")


def version(text):
    if not re.fullmatch(r"\d+\.\d+", text):
        raise ValueError("glibc version must be MAJOR.MINOR")
    return tuple(map(int, text.split(".")))


def check_wheel(wheel, maximum="2.36"):
    wheel = Path(wheel)
    limit = version(maximum)
    with zipfile.ZipFile(wheel) as archive, tempfile.TemporaryDirectory(prefix="wheel-abi-") as folder:
        metadata = [entry for entry in archive.namelist()
                    if len(Path(entry).parts) == 2 and entry.endswith(".dist-info/WHEEL")]
        if len(metadata) != 1:
            raise ValueError("expected one top-level WHEEL metadata file")
        tags = [line[5:].strip() for line in archive.read(metadata[0]).decode().splitlines()
                if line.startswith("Tag: ")]
        platforms = {tag.rsplit("-", 1)[-1] for tag in tags}
        if not platforms:
            raise ValueError("wheel has no compatibility tags")
        floors = []
        for platform in platforms:
            match = re.fullmatch(r"manylinux_(\d+)_(\d+)_x86_64", platform)
            if not match:
                raise ValueError(f"expected a manylinux x86_64 tag, got {platform}")
            floor = tuple(map(int, match.groups()))
            if floor > limit:
                raise ValueError(f"wheel tag {platform} exceeds glibc {maximum}")
            floors.append(floor)
        filename_platforms = set(wheel.name.removesuffix(".whl").rsplit("-", 1)[-1].split("."))
        if platforms != filename_platforms:
            raise ValueError("wheel filename and WHEEL platform tags disagree")

        binaries = []
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            with archive.open(entry) as source:
                header = source.read(20)
                if not header.startswith(b"\x7fELF"):
                    continue
                if len(header) < 20 or header[4:6] != b"\x02\x01" or int.from_bytes(header[18:20], "little") != 62:
                    raise ValueError(f"ELF is not Linux x86_64: {entry.filename}")
                target = Path(folder) / f"{len(binaries)}.elf"
                with target.open("wb") as output:
                    output.write(header)
                    shutil.copyfileobj(source, output)
            if SYSTEM_LIBRARY.fullmatch(Path(entry.filename).name):
                raise ValueError(f"wheel must use the target system's glibc: {entry.filename}")
            result = subprocess.check_output(
                ["readelf", "--wide", "--version-info", "--dynamic", str(target)],
                text=True, env={**os.environ, "LC_ALL": "C"})
            needs = False
            required = []
            for line in result.splitlines():
                if line.startswith("Version "):
                    needs = line.startswith("Version needs section")
                if not needs:
                    continue
                if re.search(r"Name: GLIBC_PRIVATE\b", line):
                    raise ValueError(f"ELF requires private glibc symbols: {entry.filename}")
                for name in re.findall(r"Name: (GLIBC_[A-Za-z0-9_.]+)", line):
                    numeric = re.fullmatch(r"GLIBC_(\d+\.\d+)(?:\.\d+)*", name)
                    if numeric:
                        required.append(version(numeric[1]))
                    elif name == "GLIBC_ABI_DT_RELR":
                        required.append((2, 36))
                    else:
                        raise ValueError(f"unsupported glibc requirement {name}: {entry.filename}")
            floor = max(required, default=(0, 0))
            if floor > min(floors):
                raise ValueError(f"{entry.filename} requires GLIBC_{floor[0]}.{floor[1]}, above its wheel tag")
            paths = re.findall(r"\((?:RUNPATH|RPATH)\).*?\[(.*?)\]", result)
            if any(part not in ("$ORIGIN", "${ORIGIN}") and not part.startswith(("$ORIGIN/", "${ORIGIN}/"))
                   for path in paths for part in path.split(":")):
                raise ValueError(f"ELF has a non-relocatable library path: {entry.filename}")
            needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", result)
            binaries.append({"path": entry.filename, "required": floor, "needed": needed})
        if not binaries:
            raise ValueError("Linux runtime wheel contains no ELF binaries")
        provided = {Path(item["path"]).name for item in binaries}
        for binary in binaries:
            missing = [name for name in binary["needed"] if name not in provided and not SYSTEM_LIBRARY.fullmatch(name)]
            if missing:
                raise ValueError(f"{binary['path']} has unbundled dependencies: {', '.join(missing)}")
        required = max(item["required"] for item in binaries)
        return {"wheel": wheel.name, "platforms": sorted(platforms), "elf_files": len(binaries),
                "maximum_required_glibc": ".".join(map(str, required)), "allowed_glibc": maximum}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", nargs="+", type=Path)
    parser.add_argument("--glibc", default="2.36")
    args = parser.parse_args()
    for wheel in args.wheels:
        print(json.dumps(check_wheel(wheel, args.glibc)), flush=True)


if __name__ == "__main__":
    main()
