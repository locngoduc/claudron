"""End-to-end smoke tests. Fully isolated: fake HOME, fake transcripts, no network."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claudron import cli


class CliHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.projects = root / "projects" / "-home-me-repo"
        self.projects.mkdir(parents=True)
        (self.projects / "a.jsonl").write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-03-01T10:11:00.000Z",
                    "requestId": "req_1",
                    "message": {
                        "model": "claude-x",
                        "id": "m1",
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 6,
                            "cache_creation_input_tokens": 7,
                            "cache_read_input_tokens": 8,
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.env = mock.patch.dict(
            os.environ,
            {
                "CLAUDRON_CONFIG_DIR": str(root / "config"),
                "CLAUDRON_STATE_DIR": str(root / "state"),
                "NO_COLOR": "1",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def init(self, *extra):
        code, out, _ = self.run_cli("init", "--timezone", "UTC", *extra)
        config_path = Path(os.environ["CLAUDRON_CONFIG_DIR"]) / "config.toml"
        text = config_path.read_text(encoding="utf-8")
        text = text.replace(
            'projects_dir = "~/.claude/projects"',
            f'projects_dir = "{self.projects.parent}"',
        )
        config_path.write_text(text, encoding="utf-8")
        return code, out


class Commands(CliHarness):
    def test_init_then_plan_then_status_then_usage(self):
        code, out = self.init("--preset", "balanced")
        self.assertEqual(code, 0)
        self.assertIn("anchors", out)

        code, out, _ = self.run_cli("plan")
        self.assertEqual(code, 0)
        self.assertIn("stay quiet", out)
        self.assertIn("05:00", out)

        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)

        code, out, _ = self.run_cli("usage", "--days", "3650")
        self.assertEqual(code, 0)
        self.assertIn("usage windows", out)

    def test_init_refuses_to_clobber(self):
        self.init()
        code, _, err = self.run_cli("init")
        self.assertEqual(code, 2)
        self.assertIn("--force", err)

    def test_plan_exits_nonzero_on_a_broken_schedule(self):
        self.init("--anchors", "05:00,10:00,12:00,17:00,22:00")
        code, out, _ = self.run_cli("plan")
        self.assertEqual(code, 1)
        self.assertIn("no effect", out)
        self.assertIn("resets nothing", out)

    def test_json_output_is_machine_readable(self):
        self.init()
        for command in (["plan", "--json"], ["status", "--json"], ["usage", "--json"]):
            with self.subTest(command=command):
                _, out, _ = self.run_cli(*command)
                json.loads(out)

    def test_status_short_is_a_single_line(self):
        self.init()
        _, out, _ = self.run_cli("status", "--short")
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_fire_dry_run_sends_nothing(self):
        self.init()
        with (
            mock.patch("claudron.anchor.shutil.which", return_value="/usr/bin/claude"),
            mock.patch("claudron.anchor.probe_flags", return_value={"--model"}),
            mock.patch("claudron.anchor.subprocess.run") as run,
        ):
            code, out, _ = self.run_cli("fire", "--dry-run")
        run.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("would run", out)

    def test_install_dry_run_writes_nothing(self):
        self.init()
        code, out, _ = self.run_cli("install", "--dry-run", "--backend", "systemd")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertFalse((Path.home() / ".config/systemd/user/claudron.timer").exists())

    def test_config_subcommands(self):
        self.init()
        code, out, _ = self.run_cli("config", "path")
        self.assertEqual(code, 0)
        self.assertIn("config.toml", out)
        code, out, _ = self.run_cli("config", "presets")
        self.assertIn("balanced", out)
        code, out, _ = self.run_cli("config", "show")
        self.assertIn("[schedule]", out)

    def test_missing_config_is_a_friendly_error(self):
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("claudron init", err)

    def test_suggest_works_before_any_config_exists(self):
        code, out, _ = self.run_cli(
            "suggest", "--start-at", "12:00", "--sleep", "23:00-05:00", "--timezone", "UTC"
        )
        self.assertEqual(code, 0)
        self.assertIn("12:00", out)
        self.assertIn("stay quiet", out)
        self.assertIn("UTC", out)

    def test_suggest_reports_impossible_constraints(self):
        code, _, err = self.run_cli("suggest", "--start-at", "12:00", "--start-at", "15:00")
        self.assertEqual(code, 1)
        self.assertIn("no schedule satisfies", err)
        self.assertIn("3h apart", err)

    def test_suggest_apply_writes_the_anchors(self):
        self.init()
        code, _, _ = self.run_cli(
            "suggest", "--start-at", "12:00", "--sleep", "23:00-05:00", "--apply", "1"
        )
        self.assertEqual(code, 0)
        from claudron import config

        anchors = config.load().schedule.anchors
        self.assertIn("12:00", anchors)
        self.assertEqual(len(anchors), 4)

    def test_suggest_json_is_machine_readable(self):
        _, out, _ = self.run_cli("suggest", "--json", "--start-at", "12:00", "--timezone", "UTC")
        payload = json.loads(out)
        self.assertTrue(payload["candidates"])
        self.assertIn("12:00", payload["candidates"][0]["anchors"])

    def test_doctor_runs_without_a_claude_cli(self):
        self.init()
        with mock.patch("claudron.cli.shutil.which", return_value=None):
            code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 2)
        self.assertIn("not found on PATH", out)


if __name__ == "__main__":
    unittest.main()
