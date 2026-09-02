"""Configuration: load, validate, and generate a commented TOML file.

Reading uses the stdlib `tomllib`. Writing is done from a template rather than
a general TOML serialiser, so the file people edit by hand stays commented and
readable.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, time, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from claudron import paths
from claudron.errors import ConfigError

CONFIG_VERSION = 1

#: Times of day, per preset. See ``docs/schedules.md`` for the reasoning.
PRESETS: dict[str, tuple[str, list[str]]] = {
    "balanced": (
        "Early start, protected midday, long evening. 4h idle (10:00-12:00, 03:00-05:00).",
        ["05:00", "12:00", "17:00", "22:00"],
    ),
    "office": (
        "Office hours first, evening block, overnight window for long jobs.",
        ["08:00", "13:00", "18:00", "23:00"],
    ),
    "nightowl": (
        "Late start, window running past midnight.",
        ["10:00", "15:00", "20:00", "01:00"],
    ),
    "workday": (
        "Three windows covering a single working day, nothing overnight.",
        ["08:00", "13:00", "18:00"],
    ),
}

DEFAULT_PRESET = "balanced"


@dataclass(slots=True)
class ScheduleConfig:
    timezone: str = "local"
    window_hours: int = 5
    anchors: list[str] = field(default_factory=lambda: list(PRESETS[DEFAULT_PRESET][1]))
    jitter_seconds: int = 20
    catch_up_minutes: int = 45


@dataclass(slots=True)
class AnchorConfig:
    prompt: str = "ok"
    model: str = "haiku"
    extra_args: list[str] = field(default_factory=list)
    timeout_seconds: int = 180
    max_budget_usd: float = 0.05
    skip_if_window_active: bool = True
    isolated_cwd: bool = True
    executable: str = "claude"


@dataclass(slots=True)
class UsageConfig:
    projects_dir: str = "~/.claude/projects"
    floor_window_to_hour: bool = True
    lookback_days: int = 30
    cache_enabled: bool = True


@dataclass(slots=True)
class WarningsConfig:
    enabled: bool = True
    guard_blackouts: bool = True
    high_usage_ratio: float = 0.85


@dataclass(slots=True)
class NotifyConfig:
    #: argv template; {title} and {body} are substituted. Empty = no notifications.
    command: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Config:
    version: int = CONFIG_VERSION
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    anchor: AnchorConfig = field(default_factory=AnchorConfig)
    usage: UsageConfig = field(default_factory=UsageConfig)
    warnings: WarningsConfig = field(default_factory=WarningsConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    source: Path | None = None

    # -- derived -----------------------------------------------------------
    def tz(self) -> tzinfo:
        name = self.schedule.timezone
        if name in ("", "local", "system"):
            local = datetime.now().astimezone().tzinfo
            if local is None:  # pragma: no cover - astimezone always sets one
                raise ConfigError("could not determine the system timezone")
            return local
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(
                f"unknown timezone {name!r}. Use an IANA name such as "
                f"'Asia/Ho_Chi_Minh', or 'local' for the system timezone."
            ) from exc

    def tz_label(self) -> str:
        if self.schedule.timezone in ("", "local", "system"):
            now = datetime.now().astimezone()
            return f"local ({now.tzname()}, UTC{now.strftime('%z')})"
        return self.schedule.timezone

    def anchor_times(self) -> list[time]:
        return [parse_hhmm(value) for value in self.schedule.anchors]

    def projects_path(self) -> Path:
        return Path(self.usage.projects_dir).expanduser()


def parse_hhmm(value: str) -> time:
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ConfigError(f"invalid anchor time {value!r}: expected HH:MM (24h)")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        return time(hour=hour, minute=minute)
    except ValueError as exc:
        raise ConfigError(f"invalid anchor time {value!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _fill(target: Any, data: dict[str, Any], section: str) -> Any:
    known = {f for f in target.__slots__}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(
                f"unknown key [{section}].{key}. Known keys: {', '.join(sorted(known))}"
            )
        current = getattr(target, key)
        if isinstance(current, bool) and not isinstance(value, bool):
            raise ConfigError(f"[{section}].{key} must be true or false")
        if (
            isinstance(current, int)
            and not isinstance(current, bool)
            and (not isinstance(value, int) or isinstance(value, bool))
        ):
            raise ConfigError(f"[{section}].{key} must be an integer")
        if isinstance(current, float) and not isinstance(value, (int, float)):
            raise ConfigError(f"[{section}].{key} must be a number")
        if isinstance(current, str) and not isinstance(value, str):
            raise ConfigError(f"[{section}].{key} must be a string")
        if isinstance(current, list) and (
            not isinstance(value, list) or not all(isinstance(v, str) for v in value)
        ):
            raise ConfigError(f"[{section}].{key} must be a list of strings")
        setattr(target, key, float(value) if isinstance(current, float) else value)
    return target


def load(path: Path | None = None, *, required: bool = True) -> Config:
    """Load the config file, or return defaults when it does not exist."""
    path = path or paths.config_file()
    if not path.exists():
        if required:
            raise ConfigError(
                f"no config at {path}\n"
                f"Run `claudron init` to create one (it will not overwrite anything)."
            )
        cfg = Config()
        cfg.source = None
        return cfg
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc

    version = raw.get("version", CONFIG_VERSION)
    if not isinstance(version, int):
        raise ConfigError("`version` must be an integer")
    if version > CONFIG_VERSION:
        raise ConfigError(
            f"{path} was written by a newer claudron (config version {version}, "
            f"this build understands {CONFIG_VERSION}). Upgrade claudron."
        )

    cfg = Config(version=version)
    _fill(cfg.schedule, _section(raw, "schedule"), "schedule")
    _fill(cfg.anchor, _section(raw, "anchor"), "anchor")
    _fill(cfg.usage, _section(raw, "usage"), "usage")
    _fill(cfg.warnings, _section(raw, "warnings"), "warnings")
    _fill(cfg.notify, _section(raw, "notify"), "notify")
    cfg.source = path
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    """Reject values that would silently produce a broken schedule."""
    if not 1 <= cfg.schedule.window_hours <= 24:
        raise ConfigError("[schedule].window_hours must be between 1 and 24")
    if not cfg.schedule.anchors:
        raise ConfigError("[schedule].anchors is empty - nothing would ever be scheduled")
    cfg.anchor_times()  # raises on malformed entries
    if len(set(cfg.schedule.anchors)) != len(cfg.schedule.anchors):
        raise ConfigError("[schedule].anchors contains duplicates")
    if not 0 <= cfg.schedule.jitter_seconds <= 600:
        raise ConfigError("[schedule].jitter_seconds must be between 0 and 600")
    if not 0 <= cfg.schedule.catch_up_minutes <= 300:
        raise ConfigError("[schedule].catch_up_minutes must be between 0 and 300")
    if not cfg.anchor.prompt.strip():
        raise ConfigError("[anchor].prompt must not be empty")
    if cfg.anchor.timeout_seconds < 10:
        raise ConfigError("[anchor].timeout_seconds must be at least 10")
    if cfg.anchor.max_budget_usd < 0:
        raise ConfigError("[anchor].max_budget_usd must not be negative")
    if not 0 < cfg.warnings.high_usage_ratio <= 1:
        raise ConfigError("[warnings].high_usage_ratio must be in (0, 1]")
    if cfg.usage.lookback_days < 1:
        raise ConfigError("[usage].lookback_days must be at least 1")
    cfg.tz()


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

TEMPLATE = """\
# claudron configuration
# Docs: https://github.com/locngoduc/claudron
#
# claudron keeps your Claude usage windows aligned with your working day. A
# usage window opens on your FIRST message after the previous one expired, so
# the times you send that first message decide when your limits reset.

version = {version}

[schedule]
# IANA timezone name, or "local" to follow the system clock.
timezone = {timezone}

# Length of a Claude usage window, in hours. 5 is the documented value; it is
# configurable so claudron survives a change without a new release.
window_hours = {window_hours}

# The times (24h, local) at which a NEW window should open.
#
# Rule of thumb: consecutive anchors must be at least `window_hours` apart,
# otherwise the later one lands inside a window that is already open and does
# nothing. Because 24 / 5 = 4.8, a schedule that repeats daily can hold at most
# 4 anchors and therefore covers at most 20 of 24 hours.
#
# Run `claudron plan` after any edit - it simulates the day and tells you
# exactly which anchors would be swallowed.
anchors = {anchors}

# Fire this many seconds after the anchor time. Small offset only; it keeps the
# message inside the same clock hour, which matters when window starts are
# rounded down to the hour (see [usage].floor_window_to_hour).
jitter_seconds = {jitter_seconds}

# If the machine was asleep and claudron wakes up late, still fire the anchor
# when it is less than this many minutes overdue. Keep this under 60: firing in
# a later clock hour shifts every window for the rest of the day.
catch_up_minutes = {catch_up_minutes}

[anchor]
# The `claude` executable. An absolute path works too.
executable = {executable}

# The message sent to open a window. Keep it short - you pay for it.
prompt = {prompt}

# Model alias for the anchor message ("haiku", "sonnet", "opus", "fable", a
# full model id, or "" to use your configured default). A small model keeps the
# cost of anchoring negligible.
model = {model}

# Hard spend ceiling for a single anchor, passed to `claude --max-budget-usd`
# when that flag is supported. 0 disables the ceiling.
max_budget_usd = {max_budget_usd}

# Extra arguments appended to the `claude` invocation. claudron already passes
# --print, and adds --model / --tools "" / --strict-mcp-config /
# --disable-slash-commands / --output-format json when your `claude` build
# supports them (checked by `claudron doctor`).
extra_args = {extra_args}

timeout_seconds = {timeout_seconds}

# Never fire when a window is already open: the message would be wasted spend
# and would not move the reset time. Turn this off only for debugging.
skip_if_window_active = {skip_if_window_active}

# Run the anchor in an empty, dedicated directory so no repository files,
# project settings, or CLAUDE.md are loaded into it.
isolated_cwd = {isolated_cwd}

[usage]
# Where Claude Code stores its local transcripts. claudron reads ONLY metadata
# from these files: timestamp, model id, token counts, request id. It never
# reads, stores, or transmits message content.
projects_dir = {projects_dir}

# Round a window's start down to the top of the hour. This mirrors how usage
# blocks are commonly reconstructed from transcripts; set to false to treat the
# exact timestamp of the first message as the window start.
floor_window_to_hour = {floor_window_to_hour}

# How far back `claudron usage` looks by default.
lookback_days = {lookback_days}

# Cache parsed transcript metadata (keyed by file size + mtime) so repeated
# commands stay fast. The cache holds token counts only, never content.
cache_enabled = {cache_enabled}

[warnings]
enabled = {enabled}

# Warn when you send a message during an idle gap. Doing so opens an unplanned
# window and pushes every later reset out of alignment for the rest of the day.
guard_blackouts = {guard_blackouts}

# Warn once the current window's token use passes this fraction of your own
# historical busiest window. claudron cannot see your real limit - Anthropic
# does not expose it locally - so this is measured against your own history.
high_usage_ratio = {high_usage_ratio}

[notify]
# Optional desktop notification command. {{title}} and {{body}} are substituted.
# Linux:   command = ["notify-send", "{{title}}", "{{body}}"]
# macOS:   command = ["terminal-notifier", "-title", "{{title}}", "-message", "{{body}}"]
command = {notify_command}
"""


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def render(cfg: Config) -> str:
    """Render a fully commented config file for ``cfg``."""
    return TEMPLATE.format(
        version=cfg.version,
        timezone=_toml_str(cfg.schedule.timezone),
        window_hours=cfg.schedule.window_hours,
        anchors=_toml_list(cfg.schedule.anchors),
        jitter_seconds=cfg.schedule.jitter_seconds,
        catch_up_minutes=cfg.schedule.catch_up_minutes,
        executable=_toml_str(cfg.anchor.executable),
        prompt=_toml_str(cfg.anchor.prompt),
        model=_toml_str(cfg.anchor.model),
        max_budget_usd=cfg.anchor.max_budget_usd,
        extra_args=_toml_list(cfg.anchor.extra_args),
        timeout_seconds=cfg.anchor.timeout_seconds,
        skip_if_window_active=str(cfg.anchor.skip_if_window_active).lower(),
        isolated_cwd=str(cfg.anchor.isolated_cwd).lower(),
        projects_dir=_toml_str(cfg.usage.projects_dir),
        floor_window_to_hour=str(cfg.usage.floor_window_to_hour).lower(),
        lookback_days=cfg.usage.lookback_days,
        cache_enabled=str(cfg.usage.cache_enabled).lower(),
        enabled=str(cfg.warnings.enabled).lower(),
        guard_blackouts=str(cfg.warnings.guard_blackouts).lower(),
        high_usage_ratio=cfg.warnings.high_usage_ratio,
        notify_command=_toml_list(cfg.notify.command),
    )


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or paths.config_file()
    paths.write_private(path, render(cfg))
    cfg.source = path
    return path
