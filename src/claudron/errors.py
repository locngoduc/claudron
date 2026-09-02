"""Typed errors. Every one carries a message meant to be read by a human."""

from __future__ import annotations


class ClaudronError(Exception):
    """Base class for all expected failures. The CLI prints these without a traceback."""

    exit_code = 1


class ConfigError(ClaudronError):
    """The config file is missing, unreadable, or semantically invalid."""

    exit_code = 2


class EnvironmentError_(ClaudronError):
    """Something in the environment is missing (the `claude` CLI, transcripts, ...)."""

    exit_code = 3


class AnchorError(ClaudronError):
    """Firing an anchor failed."""

    exit_code = 4
