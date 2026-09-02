"""Reading real usage out of Claude Code's local transcripts.

What is read
------------
Claude Code writes one JSONL file per session under ``~/.claude/projects``.
claudron reads four things from each line and nothing else:

    timestamp, message type (user/assistant), model id, token counts

Message content, tool arguments, file paths inside the conversation, titles and
attachments are never parsed, never stored and never sent anywhere. The parser
below is deliberately short so that claim is easy to verify by reading it.

What cannot be read
-------------------
Your actual rate limit. Anthropic does not publish it to the local machine, so
claudron never claims to know "how much is left". It reports what you have
spent in the current window and compares it against *your own* busiest windows.
"""

from __future__ import annotations

import contextlib
import json
import time as _time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

from claudron import paths
from claudron.config import Config
from claudron.schedule import Window, floor_hour

CACHE_VERSION = 2
#: Files bigger than this many events are re-parsed each time instead of cached.
MAX_CACHED_EVENTS = 20_000

USER = 0
ASSISTANT = 1


@dataclass(frozen=True, slots=True)
class Event:
    ts: datetime
    kind: int
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    key: str
    project: str

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    @property
    def fresh_tokens(self) -> int:
        """Tokens that were not served from cache."""
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens


@dataclass(slots=True)
class ParseStats:
    files_seen: int = 0
    files_parsed: int = 0
    files_cached: int = 0
    lines: int = 0
    malformed: int = 0
    duration_s: float = 0.0


@dataclass(slots=True)
class Block:
    """A reconstructed usage window."""

    window: Window
    first_event: datetime
    last_event: datetime
    events: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    models: dict[str, int] = field(default_factory=dict)
    projects: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    @property
    def fresh_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens

    def is_active(self, now: datetime) -> bool:
        return self.window.contains(now)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: str) -> datetime | None:
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        moment = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _parse_line(line: str) -> tuple | None:
    """Return a compact tuple of metadata, or None when the line is not usage.

    The returned tuple is exactly what gets cached:
    ``(epoch_seconds, kind, model, input, output, cache_create, cache_read, key)``
    """
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None

    kind_name = record.get("type")
    if kind_name == "user":
        kind = USER
    elif kind_name == "assistant":
        kind = ASSISTANT
    else:
        return None

    moment = _parse_timestamp(record.get("timestamp", ""))
    if moment is None:
        return None

    message = record.get("message")
    model = ""
    usage: dict = {}
    if isinstance(message, dict):
        raw_model = message.get("model")
        if isinstance(raw_model, str):
            model = raw_model
        raw_usage = message.get("usage")
        if isinstance(raw_usage, dict):
            usage = raw_usage

    def count(name: str) -> int:
        value = usage.get(name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    key = record.get("requestId")
    if not isinstance(key, str) or not key:
        key = message.get("id") if isinstance(message, dict) else None
    if not isinstance(key, str) or not key:
        key = record.get("uuid")
    if not isinstance(key, str) or not key:
        key = f"{moment.timestamp():.3f}:{kind}"

    return (
        moment.timestamp(),
        kind,
        model,
        count("input_tokens"),
        count("output_tokens"),
        count("cache_creation_input_tokens"),
        count("cache_read_input_tokens"),
        key,
    )


def _parse_file(path: Path, stats: ParseStats) -> list[tuple]:
    rows: list[tuple] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                stats.lines += 1
                row = _parse_line(line)
                if row is None:
                    if not line.startswith("{"):
                        stats.malformed += 1
                    continue
                rows.append(row)
    except OSError:
        stats.malformed += 1
    return rows


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def _load_cache() -> dict:
    path = paths.index_file()
    if not path.exists():
        return {"version": CACHE_VERSION, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": CACHE_VERSION, "files": {}}
    if data.get("version") != CACHE_VERSION or not isinstance(data.get("files"), dict):
        return {"version": CACHE_VERSION, "files": {}}
    return data


def _save_cache(cache: dict) -> None:
    # A cache that cannot be written must never break the command that wrote it.
    with contextlib.suppress(OSError):
        paths.write_private(paths.index_file(), json.dumps(cache, separators=(",", ":")))


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def read_events(
    cfg: Config, *, since: datetime | None = None, stats: ParseStats | None = None
) -> list[Event]:
    """Collect usage events newer than ``since`` (default: config lookback)."""
    started = _time.perf_counter()
    stats = stats if stats is not None else ParseStats()
    root = cfg.projects_path()
    if since is None:
        since = datetime.now(UTC) - timedelta(days=cfg.usage.lookback_days)
    cutoff = since.timestamp()

    cache = _load_cache() if cfg.usage.cache_enabled else {"version": CACHE_VERSION, "files": {}}
    files = cache["files"]
    fresh: dict[str, dict] = {}
    seen_keys: set[str] = set()
    events: list[Event] = []

    if not root.exists():
        stats.duration_s = _time.perf_counter() - started
        return events

    for path in sorted(root.rglob("*.jsonl")):
        try:
            info = path.stat()
        except OSError:
            continue
        stats.files_seen += 1
        # Skip whole files that cannot contain anything new.
        if info.st_mtime < cutoff:
            continue

        project = path.parent.name
        entry = files.get(str(path))
        unchanged = (
            entry is not None
            and entry.get("mtime_ns") == info.st_mtime_ns
            and entry.get("size") == info.st_size
        )
        if unchanged:
            rows = [tuple(row) for row in entry.get("rows", [])]
            stats.files_cached += 1
        else:
            rows = _parse_file(path, stats)
            stats.files_parsed += 1
        if len(rows) <= MAX_CACHED_EVENTS:
            fresh[str(path)] = {
                "mtime_ns": info.st_mtime_ns,
                "size": info.st_size,
                "rows": [list(row) for row in rows],
            }

        for ts, kind, model, inp, out, cc, cr, key in rows:
            if ts < cutoff:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(
                Event(
                    ts=datetime.fromtimestamp(ts, tz=UTC),
                    kind=kind,
                    model=model,
                    input_tokens=inp,
                    output_tokens=out,
                    cache_creation_tokens=cc,
                    cache_read_tokens=cr,
                    key=key,
                    project=project,
                )
            )

    if cfg.usage.cache_enabled:
        _save_cache({"version": CACHE_VERSION, "files": fresh})

    events.sort(key=lambda e: e.ts)
    stats.duration_s = _time.perf_counter() - started
    return events


def build_blocks(events: list[Event], cfg: Config) -> list[Block]:
    """Group events into usage windows.

    A new window starts when an event falls outside the previous window. This
    mirrors the rule Claude applies: the window is anchored on the first
    message and lasts a fixed number of hours regardless of what happens next.
    """
    tz = cfg.tz()
    span = timedelta(hours=cfg.schedule.window_hours)
    blocks: list[Block] = []
    current: Block | None = None

    for event in events:
        local = event.ts.astimezone(tz)
        if current is None or not current.window.contains(local):
            start = floor_hour(local) if cfg.usage.floor_window_to_hour else local
            current = Block(
                window=Window(start=start, end=start + span, observed=True),
                first_event=local,
                last_event=local,
            )
            blocks.append(current)
        current.last_event = local
        current.events += 1
        current.input_tokens += event.input_tokens
        current.output_tokens += event.output_tokens
        current.cache_creation_tokens += event.cache_creation_tokens
        current.cache_read_tokens += event.cache_read_tokens
        if event.model:
            current.models[event.model] = current.models.get(event.model, 0) + 1
        current.projects[event.project] = current.projects.get(event.project, 0) + 1
    return blocks


def active_block(blocks: list[Block], now: datetime) -> Block | None:
    for block in reversed(blocks):
        if block.is_active(now):
            return block
    return None


def baseline_tokens(blocks: list[Block], *, exclude: Block | None = None) -> int:
    """The busiest window you have had. Used as a personal reference point."""
    values = [b.total_tokens for b in blocks if b is not exclude and b.events > 1]
    return max(values) if values else 0


def anchor_project_name(tz: tzinfo | None = None) -> str:
    """The Claude project folder name that anchor messages land in."""
    return str(paths.anchor_workspace().resolve()).replace("/", "-")
