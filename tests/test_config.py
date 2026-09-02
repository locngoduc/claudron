"""Config loading, validation, and round-tripping the generated file."""

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from claudron import config
from claudron.errors import ConfigError


class Defaults(unittest.TestCase):
    def test_default_config_is_valid(self):
        config.validate(config.Config())

    def test_every_preset_is_valid_and_fits_a_day(self):
        for name, (_, anchors) in config.PRESETS.items():
            with self.subTest(preset=name):
                cfg = config.Config()
                cfg.schedule.anchors = list(anchors)
                config.validate(cfg)
                self.assertLessEqual(
                    len(anchors) * cfg.schedule.window_hours, 24, f"{name} overruns a day"
                )


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_generated_file_parses_back_to_the_same_values(self):
        original = config.Config()
        original.schedule.timezone = "Asia/Ho_Chi_Minh"
        original.schedule.anchors = ["05:00", "12:00", "17:00", "22:00"]
        original.anchor.model = "sonnet"
        original.anchor.extra_args = ["--verbose"]
        original.notify.command = ["notify-send", "{title}", "{body}"]
        config.save(original, self.path)

        loaded = config.load(self.path)
        self.assertEqual(loaded.schedule.anchors, original.schedule.anchors)
        self.assertEqual(loaded.schedule.timezone, "Asia/Ho_Chi_Minh")
        self.assertEqual(loaded.anchor.model, "sonnet")
        self.assertEqual(loaded.anchor.extra_args, ["--verbose"])
        self.assertEqual(loaded.notify.command, original.notify.command)
        self.assertEqual(loaded.usage.floor_window_to_hour, True)

    def test_config_file_is_owner_only(self):
        config.save(config.Config(), self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_missing_file_reports_how_to_fix_it(self):
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.path)
        self.assertIn("claudron init", str(ctx.exception))

    def test_missing_file_is_allowed_when_not_required(self):
        self.assertEqual(config.load(self.path, required=False).schedule.anchors[0], "05:00")


class Validation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def assertRejects(self, text: str, fragment: str):
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.write(text))
        self.assertIn(fragment, str(ctx.exception))

    def test_rejects_bad_toml(self):
        self.assertRejects("[schedule\nanchors = ", "invalid TOML")

    def test_rejects_unknown_key(self):
        self.assertRejects('[schedule]\nanchros = ["05:00"]\n', "unknown key")

    def test_rejects_malformed_anchor(self):
        self.assertRejects('[schedule]\nanchors = ["5am"]\n', "HH:MM")

    def test_rejects_duplicate_anchors(self):
        self.assertRejects('[schedule]\nanchors = ["05:00", "05:00"]\n', "duplicates")

    def test_rejects_empty_anchors(self):
        self.assertRejects("[schedule]\nanchors = []\n", "empty")

    def test_rejects_unknown_timezone(self):
        self.assertRejects('[schedule]\ntimezone = "Mars/Olympus"\n', "unknown timezone")

    def test_rejects_wrong_type(self):
        self.assertRejects('[schedule]\nwindow_hours = "five"\n', "must be an integer")

    def test_rejects_future_config_version(self):
        self.assertRejects("version = 99\n", "newer claudron")

    def test_rejects_out_of_range_window(self):
        self.assertRejects("[schedule]\nwindow_hours = 0\n", "between 1 and 24")

    def test_rejects_catch_up_and_jitter_out_of_range(self):
        self.assertRejects("[schedule]\ncatch_up_minutes = 999\n", "catch_up_minutes")
        self.assertRejects("[schedule]\njitter_seconds = 9999\n", "jitter_seconds")

    def test_local_timezone_resolves(self):
        cfg = config.load(self.write('[schedule]\ntimezone = "local"\n'))
        self.assertIsNotNone(cfg.tz())
        self.assertIn("local", cfg.tz_label())


if __name__ == "__main__":
    unittest.main()
