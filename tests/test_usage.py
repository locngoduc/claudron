"""Transcript parsing: only metadata, and never a crash on a bad line."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from claudron import paths, usage
from claudron.config import Config


def line(ts: str, *, kind="assistant", request=None, tokens=(10, 20, 30, 40), model="claude-x"):
    payload = {
        "type": kind,
        "timestamp": ts,
        "sessionId": "s1",
        "message": {
            "role": "assistant" if kind == "assistant" else "user",
            "content": "SECRET CONTENT THAT MUST NEVER BE READ",
            "model": model,
            "id": "msg_1",
            "usage": {
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
                "cache_creation_input_tokens": tokens[2],
                "cache_read_input_tokens": tokens[3],
            },
        },
    }
    if request:
        payload["requestId"] = request
    return json.dumps(payload)


class TranscriptReading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.projects = root / "projects"
        (self.projects / "-home-me-repo").mkdir(parents=True)
        os.environ["CLAUDRON_STATE_DIR"] = str(root / "state")
        self.cfg = Config()
        self.cfg.schedule.timezone = "UTC"
        self.cfg.usage.projects_dir = str(self.projects)
        self.cfg.usage.cache_enabled = False

    def tearDown(self):
        os.environ.pop("CLAUDRON_STATE_DIR", None)
        self.tmp.cleanup()

    def write(self, name: str, lines: list[str]) -> Path:
        path = self.projects / "-home-me-repo" / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def read(self, **kwargs):
        return usage.read_events(self.cfg, since=datetime(2020, 1, 1, tzinfo=UTC), **kwargs)

    def test_reads_token_counts(self):
        self.write("a.jsonl", [line("2026-03-01T10:00:00.000Z", request="req_1")])
        events = self.read()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.input_tokens, 10)
        self.assertEqual(event.output_tokens, 20)
        self.assertEqual(event.cache_creation_tokens, 30)
        self.assertEqual(event.cache_read_tokens, 40)
        self.assertEqual(event.total_tokens, 100)
        self.assertEqual(event.fresh_tokens, 60)
        self.assertEqual(event.project, "-home-me-repo")

    def test_never_retains_message_content(self):
        self.write("a.jsonl", [line("2026-03-01T10:00:00.000Z", request="req_1")])
        event = self.read()[0]
        blob = repr(event)
        self.assertNotIn("SECRET", blob)
        for value in vars(usage.Event).values():  # no stray attribute holds it
            self.assertNotIn("content", str(value))

    def test_ignores_non_conversation_records(self):
        self.write(
            "a.jsonl",
            [
                json.dumps({"type": "ai-title", "timestamp": "2026-03-01T10:00:00Z"}),
                json.dumps({"type": "attachment", "timestamp": "2026-03-01T10:00:01Z"}),
                json.dumps({"type": "queue-operation", "timestamp": "2026-03-01T10:00:02Z"}),
                line("2026-03-01T10:00:03.000Z", request="req_1"),
            ],
        )
        self.assertEqual(len(self.read()), 1)

    def test_survives_malformed_and_truncated_lines(self):
        self.write(
            "a.jsonl",
            [
                "not json at all",
                "{broken",
                "",
                line("2026-03-01T10:00:00.000Z", request="req_1"),
                json.dumps({"type": "assistant"}),  # no timestamp
                json.dumps({"type": "assistant", "timestamp": "nonsense"}),
            ],
        )
        stats = usage.ParseStats()
        events = self.read(stats=stats)
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(stats.malformed, 1)

    def test_deduplicates_by_request_id_across_files(self):
        self.write("a.jsonl", [line("2026-03-01T10:00:00.000Z", request="req_1")])
        self.write("b.jsonl", [line("2026-03-01T10:00:00.000Z", request="req_1")])
        self.assertEqual(len(self.read()), 1)

    def test_missing_projects_dir_is_not_an_error(self):
        self.cfg.usage.projects_dir = str(Path(self.tmp.name) / "nope")
        self.assertEqual(self.read(), [])


class BlockReconstruction(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.schedule.timezone = "UTC"

    def events(self, *offsets_minutes: int) -> list[usage.Event]:
        base = datetime(2026, 3, 1, 9, 17, tzinfo=UTC)
        return [
            usage.Event(
                ts=base + timedelta(minutes=offset),
                kind=usage.ASSISTANT,
                model="claude-x",
                input_tokens=1,
                output_tokens=1,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                key=f"k{index}",
                project="p",
            )
            for index, offset in enumerate(offsets_minutes)
        ]

    def test_window_start_is_floored_to_the_hour(self):
        blocks = usage.build_blocks(self.events(0), self.cfg)
        self.assertEqual(blocks[0].window.start.strftime("%H:%M"), "09:00")
        self.assertEqual(blocks[0].window.end.strftime("%H:%M"), "14:00")

    def test_exact_timestamps_when_flooring_is_off(self):
        self.cfg.usage.floor_window_to_hour = False
        blocks = usage.build_blocks(self.events(0), self.cfg)
        self.assertEqual(blocks[0].window.start.strftime("%H:%M"), "09:17")

    def test_messages_inside_the_window_do_not_extend_it(self):
        blocks = usage.build_blocks(self.events(0, 60, 120, 240), self.cfg)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].events, 4)
        self.assertEqual(blocks[0].window.end.strftime("%H:%M"), "14:00")

    def test_a_message_after_expiry_opens_a_new_window(self):
        blocks = usage.build_blocks(self.events(0, 60, 300), self.cfg)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[1].window.start.strftime("%H:%M"), "14:00")

    def test_active_block_and_baseline(self):
        blocks = usage.build_blocks(self.events(0, 60, 300, 310), self.cfg)
        now = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
        active = usage.active_block(blocks, now)
        self.assertIsNotNone(active)
        self.assertEqual(active.window.start.strftime("%H:%M"), "14:00")
        self.assertEqual(usage.baseline_tokens(blocks, exclude=active), 4)


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["CLAUDRON_STATE_DIR"] = str(root / "state")
        self.projects = root / "projects" / "-p"
        self.projects.mkdir(parents=True)
        self.cfg = Config()
        self.cfg.schedule.timezone = "UTC"
        self.cfg.usage.projects_dir = str(root / "projects")

    def tearDown(self):
        os.environ.pop("CLAUDRON_STATE_DIR", None)
        self.tmp.cleanup()

    def test_second_read_is_served_from_cache_and_matches(self):
        (self.projects / "a.jsonl").write_text(
            line("2026-03-01T10:00:00.000Z", request="req_1") + "\n", encoding="utf-8"
        )
        since = datetime(2020, 1, 1, tzinfo=UTC)
        first = usage.read_events(self.cfg, since=since)
        stats = usage.ParseStats()
        second = usage.read_events(self.cfg, since=since, stats=stats)
        self.assertEqual([e.total_tokens for e in first], [e.total_tokens for e in second])
        self.assertEqual(stats.files_cached, 1)
        self.assertEqual(stats.files_parsed, 0)

    def test_cache_file_is_owner_only(self):
        (self.projects / "a.jsonl").write_text(
            line("2026-03-01T10:00:00.000Z", request="req_1") + "\n", encoding="utf-8"
        )
        usage.read_events(self.cfg, since=datetime(2020, 1, 1, tzinfo=UTC))
        mode = paths.index_file().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_edited_file_is_reparsed(self):
        target = self.projects / "a.jsonl"
        target.write_text(
            line("2026-03-01T10:00:00.000Z", request="req_1") + "\n", encoding="utf-8"
        )
        since = datetime(2020, 1, 1, tzinfo=UTC)
        usage.read_events(self.cfg, since=since)
        target.write_text(
            line("2026-03-01T10:00:00.000Z", request="req_1")
            + "\n"
            + line("2026-03-01T10:05:00.000Z", request="req_2")
            + "\n",
            encoding="utf-8",
        )
        stats = usage.ParseStats()
        events = usage.read_events(self.cfg, since=since, stats=stats)
        self.assertEqual(len(events), 2)
        self.assertEqual(stats.files_parsed, 1)


if __name__ == "__main__":
    unittest.main()
