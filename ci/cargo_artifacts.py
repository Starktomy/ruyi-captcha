"""Find target rlibs using Cargo's stable JSON output, independent of its layout."""
import json
from pathlib import Path


def target_rlibs(output, directory):
    directory = Path(directory).resolve()
    libraries = set()
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        message = json.loads(line)
        if message.get("reason") != "compiler-artifact":
            continue
        for filename in message.get("filenames", []):
            path = Path(filename).resolve()
            if path.suffix == ".rlib" and path.is_relative_to(directory):
                if not path.is_file():
                    raise RuntimeError(f"Cargo reported a missing rlib: {path}")
                libraries.add(path)
    if not libraries:
        raise RuntimeError(f"Cargo reported no target rlibs under {directory}")
    return sorted(libraries)
