"""Filesystem locations.

Everything claudron writes lives under XDG directories and is created with
owner-only permissions (0700 dirs / 0600 files). Every location can be
overridden with an environment variable, which keeps the test-suite and any
sandboxed run fully self-contained.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

APP = "claudron"


def _base(env: str, xdg: str, fallback: str) -> Path:
    override = os.environ.get(env)
    if override:
        return Path(override).expanduser()
    root = os.environ.get(xdg)
    return (Path(root) if root else Path.home() / fallback).expanduser() / APP


def config_dir() -> Path:
    return _base("CLAUDRON_CONFIG_DIR", "XDG_CONFIG_HOME", ".config")


def state_dir() -> Path:
    return _base("CLAUDRON_STATE_DIR", "XDG_STATE_HOME", ".local/state")


def config_file() -> Path:
    return config_dir() / "config.toml"


def state_file() -> Path:
    return state_dir() / "state.json"


def index_file() -> Path:
    return state_dir() / "usage-index.json"


def log_file() -> Path:
    return state_dir() / "claudron.log"


def anchor_workspace() -> Path:
    """A stable, empty directory used as the CWD for anchor messages.

    Using one fixed directory (instead of a fresh temp dir each time) keeps all
    anchor transcripts in a single Claude project folder, so they are easy to
    audit and easy to exclude from usage stats. It is empty on purpose: no
    project files and no CLAUDE.md are picked up from it.
    """
    return state_dir() / "anchor-workspace"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):  # e.g. a filesystem without POSIX modes
        os.chmod(path, 0o700)
    return path


def write_private(path: Path, text: str) -> None:
    """Write a file atomically with 0600 permissions."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
