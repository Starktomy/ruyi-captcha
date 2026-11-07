"""Profile generation is portable and does not require a native runtime."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ruyi_captcha import generate_profile
from ruyi_captcha import cli
from ruyi_captcha.profiles import generator


class ProfileGeneratorTests(unittest.TestCase):
    def test_seed_reproduces_independent_profiles_without_changing_global_random(self):
        state = random.getstate()
        for os_name in ("linux", "windows"):
            original = generate_profile(os_name, seed=42)
            duplicate = generate_profile(os_name, seed=42)
            self.assertEqual(original, duplicate)
            self.assertNotEqual(original, generate_profile(os_name, seed=43))
            duplicate["navigator"]["languages"].clear()
            duplicate["webgl"]["attributes"]["alpha"] = False
            duplicate["webgl"]["extensions"].clear()
            self.assertEqual(original, generate_profile(os_name, seed=42))
        self.assertEqual(random.getstate(), state)

    def test_os_identity_display_bounds_and_locale_are_consistent(self):
        for os_name in ("linux", "windows"):
            profiles = [generate_profile(os_name, seed=seed, locale="ko-KR") for seed in range(50)]
            self.assertGreater(len({json.dumps(p, sort_keys=True) for p in profiles}), 40)
            for profile in profiles:
                nav, screen, viewport = (profile[k] for k in ("navigator", "screen", "viewport"))
                self.assertIn("Firefox/159.0", nav["userAgent"])
                self.assertIn("rv:159.0", nav["userAgent"])
                self.assertEqual(nav["platform"], "Linux x86_64" if os_name == "linux" else "Win32")
                self.assertIn("Linux" if os_name == "linux" else "Windows", nav["oscpu"])
                self.assertIn("llvmpipe" if os_name == "linux" else "Direct3D11", profile["webgl"]["renderer"])
                self.assertEqual(nav["language"], nav["languages"][0])
                self.assertEqual(profile["locale"], {"timezone": "Asia/Seoul", "osLocale": "ko-KR"})
                self.assertLessEqual(viewport["width"], screen["availWidth"])
                self.assertLess(viewport["height"], screen["availHeight"])
                self.assertLessEqual(screen["availHeight"], screen["height"])
                self.assertGreater(viewport["width"], 0)
                self.assertGreater(viewport["height"], 0)
                self.assertEqual(screen["colorDepth"], screen["pixelDepth"])

    def test_explicit_options_and_builtin_timezones_work_without_system_tzdata(self):
        with patch.object(generator, "ZoneInfo", side_effect=AssertionError("no system tzdata")):
            for locale in generator._LOCALES:
                profile = generate_profile("windows", seed=7, locale=locale)
                self.assertEqual(profile["navigator"]["language"], locale)
        profile = generate_profile("windows", seed=7, locale="zh-CN", timezone="UTC",
                                   hardware_concurrency=6, screen_size=(1920, 1080), device_pixel_ratio=1.25)
        self.assertEqual(profile["navigator"]["hardwareConcurrency"], 6)
        self.assertEqual(profile["locale"], {"timezone": "UTC", "osLocale": "zh-CN"})
        self.assertEqual([profile["screen"][k] for k in ("width", "height")], [1920, 1080])
        self.assertEqual(profile["viewport"]["devicePixelRatio"], 1.25)
        self.assertGreaterEqual(profile["viewport"]["width"], 1760)

    def test_invalid_options_are_rejected(self):
        options = [{"os_name": v} for v in ("macos", "", None, [])]
        options += [{"seed": v} for v in (-1, True, "42", 1.2)]
        options += [{"locale": v} for v in ("invalid", "", None, [])]
        options += [{"hardware_concurrency": v} for v in (0, 65, True, 2.5, "4")]
        options += [{"screen_size": v} for v in ((0, 1080), (1920, 0), (8000, 600),
                                                (800, 5000), (True, 800), (800,), "1920x1080")]
        options += [{"device_pixel_ratio": v} for v in (0, 5, float("nan"), float("inf"),
                                                        True, "1.25", 10 ** 400)]
        options += [{"timezone": v} for v in ("", "../UTC", "Mars/Olympus", "\0", "+08:00", 3)]
        for kwargs in options:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                generate_profile(**kwargs)

    def test_cli_exports_single_and_batch_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            single = root / "nested/profile.json"
            self.assertEqual(cli.main(["profile", "--os", "windows", "--seed", "42",
                                      "--locale", "zh-CN", "--output", str(single)]), 0)
            self.assertEqual(json.loads(single.read_text()), generate_profile("windows", seed=42, locale="zh-CN"))
            self.assertEqual(generator.main(["--seed", "10", "--count", "3", "--output-dir", str(root)]), 0)
            for index, seed in enumerate(range(10, 13), 1):
                self.assertEqual(json.loads((root / f"linux-{index:04d}.json").read_text()), generate_profile(seed=seed))
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(generator.main(["--seed", "10", "--count", "2"]), 0)
            self.assertEqual(json.loads(output.getvalue()), [generate_profile(seed=seed) for seed in (10, 11)])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                generator.main(["--count", "2", "--output", str(single)])
            self.assertEqual(caught.exception.code, 2)

    def test_module_entry_point_emits_json_without_a_native_runtime(self):
        process = subprocess.run([sys.executable, "-B", "-m", "ruyi_captcha.profiles",
                                  "--os", "windows", "--seed", "23"],
                                 env={**os.environ, "RUYI_CAPTCHA_KERNEL": "bundled"},
                                 capture_output=True, text=True, check=True)
        self.assertEqual(process.stderr, "")
        self.assertEqual(json.loads(process.stdout), generate_profile("windows", seed=23))

    def test_solve_cli_passes_profile_file_without_overriding_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            profile = generate_profile("windows", seed=11, hardware_concurrency=6)
            path.write_text(json.dumps(profile))
            args = ["solve", "https://solver.test/", "0x4AAAAAADogn3t3_JKwKkgS", "--profile-file", str(path)]
            with patch("ruyi_captcha.sdk.solve_turnstile_details") as solve, redirect_stdout(io.StringIO()):
                solve.return_value = __import__('ruyi_captcha').TurnstileSolution("offline-test-token", 0.1, 1)
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(solve.call_args.kwargs["profile"], profile)
            for value in ("[]", "null", "{broken", '"linux"'):
                path.write_text(value)
                with patch("ruyi_captcha.sdk.solve_turnstile_details") as solve, redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(args), 1)
                    solve.assert_not_called()
            path.unlink()
            with patch("ruyi_captcha.sdk.solve_turnstile_details") as solve, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(args), 1)
                solve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
