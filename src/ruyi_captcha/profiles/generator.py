"""Generate reproducible Firefox 159 desktop profiles for Linux or Windows."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_TEMPLATES = {
    "linux": "linux_firefox159.json",
    "windows": "windows_firefox159.json",
}
# Complete display choices in CSS pixels; do not divide the viewport by DPR
# again. OS work areas and browser chrome are subtracted below in CSS pixels.
_DISPLAYS = {
    "linux": ((1280, 800, 1), (1366, 768, 1), (1920, 1080, 1),
              (2560, 1440, 1), (1920, 1080, 2)),
    "windows": ((1366, 768, 1), (1536, 864, 1.25), (1920, 1080, 1),
                (1707, 960, 1.5), (1920, 1080, 2)),
}
_LOCALES = {
    "en-US": ("America/New_York", ("en-US", "en")),
    "en-GB": ("Europe/London", ("en-GB", "en")),
    "zh-CN": ("Asia/Shanghai", ("zh-CN", "zh", "en-US", "en")),
    "zh-TW": ("Asia/Taipei", ("zh-TW", "zh", "en-US", "en")),
    "ja-JP": ("Asia/Tokyo", ("ja-JP", "ja", "en-US", "en")),
    "ko-KR": ("Asia/Seoul", ("ko-KR", "ko", "en-US", "en")),
    "de-DE": ("Europe/Berlin", ("de-DE", "de", "en-US", "en")),
    "fr-FR": ("Europe/Paris", ("fr-FR", "fr", "en-US", "en")),
}
# These built-in choices also work on Python installations without a timezone
# database (common on Windows). Other names are checked against zoneinfo.
_KNOWN_TIMEZONES = {entry[0] for entry in _LOCALES.values()} | {
    "UTC", "America/Los_Angeles", "America/Chicago", "Asia/Hong_Kong",
    "Asia/Singapore", "Australia/Sydney",
}


def _timezone(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValueError("timezone must be a named IANA identifier")
    if value not in _KNOWN_TIMEZONES:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError(
                f"Unknown timezone {value!r}; use a built-in timezone or install tzdata"
            ) from error
    return value


def generate_profile(os_name: str = "linux", *, seed: int | None = None,
                     locale: str = "en-US", timezone: str | None = None,
                     hardware_concurrency: int | None = None,
                     screen_size: tuple[int, int] | None = None,
                     device_pixel_ratio: float | None = None) -> dict[str, Any]:
    """Return a fresh dictionary accepted by Context.configure_fingerprint.

    A seed reproduces a profile with the same options and generator version.
    None uses a locally seeded RNG; the application's random state is untouched.
    Display dimensions are CSS pixels. Hardware concurrency is a reported
    browser value, not a limit on native threads or CPU consumption.

    UA and WebGL data come from bundled Firefox 159 templates. Generation
    varies desktop geometry and CPU count, while locale/timezone overrides stay
    explicit. This configures browser properties; it does not install another
    OS, its fonts, rendering stack or GPU. Use Context(tls="159") with it.
    """
    if not isinstance(os_name, str) or os_name not in _TEMPLATES:
        raise ValueError("os_name must be 'linux' or 'windows'")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise ValueError("seed must be a non-negative integer or None")
    if not isinstance(locale, str) or locale not in _LOCALES:
        raise ValueError("locale must be one of: " + ", ".join(_LOCALES))
    zone, languages = _LOCALES[locale]
    zone = _timezone(zone if timezone is None else timezone)
    if hardware_concurrency is not None and (
            isinstance(hardware_concurrency, bool) or not isinstance(hardware_concurrency, int)
            or not 1 <= hardware_concurrency <= 64):
        raise ValueError("hardware_concurrency must be an integer from 1 to 64")
    if screen_size is not None:
        if (not isinstance(screen_size, (tuple, list)) or len(screen_size) != 2
                or any(isinstance(n, bool) or not isinstance(n, int) for n in screen_size)
                or not 800 <= screen_size[0] <= 7680 or not 600 <= screen_size[1] <= 4320):
            raise ValueError("screen_size must contain CSS dimensions within 800x600 and 7680x4320")
    if device_pixel_ratio is not None and (
            isinstance(device_pixel_ratio, bool) or not isinstance(device_pixel_ratio, (int, float))
            or not 0.5 <= device_pixel_ratio <= 4 or not math.isfinite(device_pixel_ratio)):
        raise ValueError("device_pixel_ratio must be a finite number from 0.5 to 4")

    rng = random.Random(seed)
    width, height, ratio = rng.choice(_DISPLAYS[os_name])
    cores = rng.choice((4, 8, 12, 16))
    inset = rng.choice((0, 16, 160))
    chrome = rng.randint(80, 140)
    if screen_size is not None:
        width, height = screen_size
    if device_pixel_ratio is not None:
        ratio = device_pixel_ratio
    # JSON parsing gives every call independent nested lists and dictionaries.
    path = Path(__file__).with_name(_TEMPLATES[os_name])
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["navigator"].update(
        language=locale, languages=list(languages),
        hardwareConcurrency=cores if hardware_concurrency is None else hardware_concurrency,
        maxTouchPoints=0,
    )
    available_height = height - (32 if os_name == "linux" else 48)
    profile["screen"].update(width=width, height=height, availWidth=width,
                             availHeight=available_height, availLeft=0, availTop=0,
                             colorDepth=24, pixelDepth=24, left=0, top=0)
    profile["viewport"].update(width=width-inset, height=available_height-chrome,
                               devicePixelRatio=ratio)
    profile["locale"] = {"timezone": zone, "osLocale": locale}
    return profile


def _screen(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x")
        return int(width), int(height)
    except ValueError as error:
        raise argparse.ArgumentTypeError("screen must be WIDTHxHEIGHT, for example 1920x1080") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--os", choices=tuple(_TEMPLATES), default="linux", dest="os_name")
    parser.add_argument("--seed", type=int, help="reproduce profiles; batches use seed, seed+1, ...")
    parser.add_argument("--locale", choices=tuple(_LOCALES), default="en-US")
    parser.add_argument("--timezone", help="IANA name; default follows --locale")
    parser.add_argument("--cpu", type=int, dest="hardware_concurrency", help="reported logical CPUs (1-64)")
    parser.add_argument("--screen", type=_screen, dest="screen_size", help="CSS pixels, e.g. 1920x1080")
    parser.add_argument("--dpr", type=float, dest="device_pixel_ratio", help="device pixel ratio (0.5-4)")
    parser.add_argument("--count", type=int, default=1, help="number of profiles (1-10000)")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output", type=Path, help="write one profile to a JSON file")
    output.add_argument("--output-dir", type=Path, help="write one JSON file per profile")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 10000:
        parser.error("count must be between 1 and 10000")
    if args.output and args.count != 1:
        parser.error("--output accepts one profile; use --output-dir for batches")
    try:
        def make(index):
            return generate_profile(
                args.os_name, seed=None if args.seed is None else args.seed + index,
                locale=args.locale, timezone=args.timezone,
                hardware_concurrency=args.hardware_concurrency, screen_size=args.screen_size,
                device_pixel_ratio=args.device_pixel_ratio,
            )

        # Validate before opening outputs; directory batches hold one profile at
        # a time instead of retaining every WebGL table in memory.
        first = make(0)
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            for index in range(args.count):
                profile = first if index == 0 else make(index)
                path = args.output_dir / f"{args.os_name}-{index+1:04d}.json"
                path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(first, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif args.count == 1:
            print(json.dumps(first, ensure_ascii=False, indent=2))
        else:
            print("[")
            for index in range(args.count):
                profile = first if index == 0 else make(index)
                print(json.dumps(profile, ensure_ascii=False, indent=2),
                      end=",\n" if index+1 < args.count else "\n")
            print("]")
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
