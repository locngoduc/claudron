"""Anchor command construction and skip logic. Nothing here runs `claude`."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from claudron import anchor
from claudron.config import Config

ALL_FLAGS = set(anchor.OPTIONAL_FLAGS)


def with_claude(func):
    return mock.patch("claudron.anchor.shutil.which", return_value="/usr/bin/claude")(func)


class CommandBuilding(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    @with_claude
    def test_uses_every_supported_flag(self, _which):
        argv = anchor.build_command(self.cfg, ALL_FLAGS)
        self.assertEqual(argv[:3], ["/usr/bin/claude", "-p", "ok"])
        self.assertIn("--model", argv)
        self.assertIn("haiku", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--max-budget-usd", argv)

    @with_claude
    def test_omits_flags_the_installed_cli_does_not_have(self, _which):
        argv = anchor.build_command(self.cfg, {"--model"})
        self.assertIn("--model", argv)
        self.assertNotIn("--strict-mcp-config", argv)
        self.assertNotIn("--output-format", argv)
        self.assertNotIn("--tools", argv)

    @with_claude
    def test_variadic_tools_flag_stays_last(self, _which):
        # `--tools` is variadic: anything after its empty value would be eaten.
        self.cfg.anchor.extra_args = ["--verbose"]
        argv = anchor.build_command(self.cfg, ALL_FLAGS)
        self.assertEqual(argv[-2:], ["--tools", ""])

    @with_claude
    def test_empty_model_falls_back_to_the_user_default(self, _which):
        self.cfg.anchor.model = ""
        self.assertNotIn("--model", anchor.build_command(self.cfg, ALL_FLAGS))

    @with_claude
    def test_zero_budget_disables_the_cap(self, _which):
        self.cfg.anchor.max_budget_usd = 0
        self.assertNotIn("--max-budget-usd", anchor.build_command(self.cfg, ALL_FLAGS))

    def test_missing_executable_explains_the_fix(self):
        with (
            mock.patch("claudron.anchor.shutil.which", return_value=None),
            self.assertRaises(Exception) as ctx,
        ):
            anchor.build_command(self.cfg, ALL_FLAGS)
        self.assertIn("[anchor].executable", str(ctx.exception))


class SkipBehaviour(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.probe = mock.patch("claudron.anchor.probe_flags", return_value=ALL_FLAGS)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    @with_claude
    def test_does_not_send_while_a_window_is_open(self, _which):
        end = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
        result = anchor.fire(self.cfg, active_window_end=end, dry_run=False)
        self.assertFalse(result.fired)
        self.assertIn("already open", result.skipped)

    @with_claude
    def test_force_overrides_the_skip_in_dry_run(self, _which):
        end = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
        result = anchor.fire(self.cfg, active_window_end=end, dry_run=True, force=True)
        self.assertEqual(result.skipped, "dry run")

    @with_claude
    def test_dry_run_never_spawns_a_process(self, _which):
        with mock.patch("claudron.anchor.subprocess.run") as run:
            anchor.fire(self.cfg, dry_run=True)
        run.assert_not_called()


class ResultParsing(unittest.TestCase):
    def test_reads_only_cost_tokens_and_session(self):
        payload = (
            '{"type":"result","result":"SECRET REPLY","session_id":"abc",'
            '"total_cost_usd":0.0031,'
            '"usage":{"input_tokens":3,"output_tokens":7,'
            '"cache_creation_input_tokens":0,"cache_read_input_tokens":5}}'
        )
        cost, tokens, session = anchor._parse_result(payload)
        self.assertAlmostEqual(cost, 0.0031)
        self.assertEqual(tokens, 15)
        self.assertEqual(session, "abc")

    def test_plain_text_output_is_not_an_error(self):
        self.assertEqual(anchor._parse_result("hello there"), (None, None, ""))

    def test_truncated_json_is_not_an_error(self):
        self.assertEqual(anchor._parse_result('{"total_cost'), (None, None, ""))


class SlotBookkeeping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = mock.patch.dict(
            "os.environ", {"CLAUDRON_STATE_DIR": str(Path(self.tmp.name) / "state")}
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_slot_key_is_stable_per_day_and_label(self):
        moment = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        self.assertEqual(anchor.slot_key(moment, "12:00"), "2026-03-01T12:00")

    def test_a_skip_settles_the_slot(self):
        anchor.record_skip("slot", "window already open")
        self.assertTrue(
            anchor.handled_recently(
                "slot", settled_within=timedelta(hours=1), retry_after=timedelta(minutes=5)
            )
        )

    def test_a_failure_is_retried_sooner_than_a_success(self):
        anchor._record("slot", anchor.FireResult(fired=False, error="boom"))
        self.assertFalse(
            anchor.handled_recently(
                "slot", settled_within=timedelta(hours=1), retry_after=timedelta(seconds=0)
            )
        )

    def test_unknown_slot_is_not_handled(self):
        self.assertFalse(
            anchor.handled_recently(
                "never", settled_within=timedelta(hours=1), retry_after=timedelta(minutes=5)
            )
        )


if __name__ == "__main__":
    unittest.main()
