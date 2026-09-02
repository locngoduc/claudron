"""Firing an anchor: the one short message that opens a usage window.

Design constraints, in priority order:

1. Never spend more than it has to. The anchor is skipped outright when a
   window is already open, uses a small model, and carries a hard budget cap.
2. Never leak context. It runs in a dedicated empty directory, with tools and
   MCP servers switched off, so nothing from your repositories is loaded.
3. Never guess at the CLI. Supported flags are probed from ``claude --help``
   rather than assumed, because they differ between Claude Code versions.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from claudron import paths, state
from claudron.config import Config
from claudron.errors import AnchorError, EnvironmentError_

log = logging.getLogger("claudron.anchor")

#: Flags claudron will use when the installed `claude` supports them.
OPTIONAL_FLAGS = (
    "--model",
    "--output-format",
    "--tools",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--max-budget-usd",
)

PROBE_TTL_SECONDS = 24 * 3600


@dataclass(slots=True)
class FireResult:
    fired: bool
    argv: list[str] = field(default_factory=list)
    skipped: str = ""
    returncode: int | None = None
    duration_s: float = 0.0
    cost_usd: float | None = None
    total_tokens: int | None = None
    session_id: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "fired": self.fired,
            "argv": self.argv,
            "skipped": self.skipped,
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 3),
            "cost_usd": self.cost_usd,
            "total_tokens": self.total_tokens,
            "session_id": self.session_id,
            "error": self.error,
        }


def resolve_executable(cfg: Config) -> str:
    found = shutil.which(cfg.anchor.executable)
    if not found:
        raise EnvironmentError_(
            f"`{cfg.anchor.executable}` was not found on PATH.\n"
            f"Install Claude Code, or set [anchor].executable to an absolute path."
        )
    return found


def probe_flags(cfg: Config, *, refresh: bool = False) -> set[str]:
    """Ask the installed `claude` which flags it understands.

    Cached for a day, keyed on the executable path and its reported version, so
    an upgrade re-probes automatically.
    """
    executable = resolve_executable(cfg)
    data = state.load()
    cached = data.get("probe", {})
    now = time.time()
    if (
        not refresh
        and cached.get("executable") == executable
        and now - float(cached.get("checked_at", 0)) < PROBE_TTL_SECONDS
        and isinstance(cached.get("flags"), list)
    ):
        return set(cached["flags"])

    try:
        proc = subprocess.run(
            [executable, "--help"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvironmentError_(f"could not run `{executable} --help`: {exc}") from exc

    text = proc.stdout + proc.stderr
    found = set(re.findall(r"--[a-zA-Z0-9][a-zA-Z0-9-]*", text))
    data["probe"] = {
        "executable": executable,
        "checked_at": now,
        "flags": sorted(found),
        "version": _version(executable),
    }
    state.save(data)
    return found


def _version(executable: str) -> str:
    try:
        proc = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=15, check=False
        )
        return proc.stdout.strip() or proc.stderr.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build_command(cfg: Config, flags: set[str] | None = None) -> list[str]:
    """Assemble the argv for one anchor message."""
    executable = resolve_executable(cfg)
    flags = flags if flags is not None else probe_flags(cfg)
    argv = [executable, "-p", cfg.anchor.prompt]

    if cfg.anchor.model and "--model" in flags:
        argv += ["--model", cfg.anchor.model]
    if "--output-format" in flags:
        argv += ["--output-format", "json"]
    if cfg.anchor.max_budget_usd > 0 and "--max-budget-usd" in flags:
        argv += ["--max-budget-usd", f"{cfg.anchor.max_budget_usd:g}"]
    if "--disable-slash-commands" in flags:
        argv += ["--disable-slash-commands"]
    if "--strict-mcp-config" in flags:
        argv += ["--strict-mcp-config"]
    argv += list(cfg.anchor.extra_args)
    # `--tools` is variadic, so it must stay last: an empty value followed by
    # nothing is unambiguous, an empty value followed by more arguments is not.
    if "--tools" in flags:
        argv += ["--tools", ""]
    return argv


def _parse_result(stdout: str) -> tuple[float | None, int | None, str]:
    """Pull cost/token/session metadata out of `--output-format json` output.

    Only these three fields are read. The model's reply text is present in the
    payload and is deliberately ignored.
    """
    stdout = stdout.strip()
    if not stdout.startswith("{"):
        return None, None, ""
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None, None, ""
    if not isinstance(payload, dict):
        return None, None, ""

    cost = payload.get("total_cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) else None
    session = payload.get("session_id")
    session = session if isinstance(session, str) else ""

    tokens = None
    usage = payload.get("usage")
    if isinstance(usage, dict):
        tokens = sum(
            int(usage.get(name, 0) or 0)
            for name in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
            if isinstance(usage.get(name, 0), int)
        )
    return cost, tokens, session


def fire(
    cfg: Config,
    *,
    dry_run: bool = False,
    force: bool = False,
    active_window_end: datetime | None = None,
    slot: str = "",
) -> FireResult:
    """Send one anchor message.

    ``active_window_end`` is the end of a window that is already open, as
    observed from the transcripts. When one is open the anchor is skipped: the
    message would cost money and would not move the reset time.
    """
    argv = build_command(cfg)

    if active_window_end is not None and cfg.anchor.skip_if_window_active and not force:
        return FireResult(
            fired=False,
            argv=argv,
            skipped=(
                f"a usage window is already open until {active_window_end:%H:%M} - "
                f"sending now would not change when your limits reset"
            ),
        )

    if dry_run:
        return FireResult(fired=False, argv=argv, skipped="dry run")

    workspace = paths.anchor_workspace()
    cwd = str(paths.ensure_private_dir(workspace)) if cfg.anchor.isolated_cwd else None

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=cfg.anchor.timeout_seconds,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        result = FireResult(
            fired=False,
            argv=argv,
            duration_s=time.perf_counter() - started,
            error=f"timed out after {cfg.anchor.timeout_seconds}s",
        )
        _record(slot, result)
        raise AnchorError(
            f"anchor timed out after {cfg.anchor.timeout_seconds}s.\n"
            f"Raise [anchor].timeout_seconds, or check that `claude` is authenticated "
            f"(`claude auth`)."
        ) from None
    except OSError as exc:
        raise AnchorError(f"could not run the anchor command: {exc}") from exc

    duration = time.perf_counter() - started
    cost, tokens, session = _parse_result(proc.stdout)
    result = FireResult(
        fired=proc.returncode == 0,
        argv=argv,
        returncode=proc.returncode,
        duration_s=duration,
        cost_usd=cost,
        total_tokens=tokens,
        session_id=session,
        error="" if proc.returncode == 0 else _short_error(proc),
    )
    _record(slot, result)
    if proc.returncode != 0:
        raise AnchorError(
            f"`claude` exited with code {proc.returncode}.\n{result.error}\n"
            f"Run `claudron fire --dry-run` to see the exact command, and "
            f"`claudron doctor` to check the environment."
        )
    return result


def _short_error(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-6:])[:800]


def _record(slot: str, result: FireResult) -> None:
    if not slot:
        return
    payload = result.as_dict()
    payload["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    payload.pop("argv", None)  # the command is reproducible; no need to persist it
    state.record_fire(slot, payload)


def record_skip(slot: str, reason: str) -> None:
    """Remember that an anchor was intentionally not fired, so it is not retried."""
    _record(slot, FireResult(fired=False, skipped=reason))


def slot_key(moment: datetime, label: str) -> str:
    """A stable id for 'the anchor labelled X on day Y', used for de-duplication."""
    return f"{moment.date().isoformat()}T{label}"


def handled_recently(slot: str, *, settled_within: timedelta, retry_after: timedelta) -> bool:
    """Has this anchor already been dealt with?

    A successful fire or a deliberate skip settles the slot for ``settled_within``.
    A failure only holds it for ``retry_after``, so a transient error (network,
    a laptop that woke mid-request) still gets another chance inside the
    catch-up grace period instead of being silently dropped.
    """
    entry = state.already_fired(slot)
    if not entry:
        return False
    try:
        when = datetime.fromisoformat(entry["at"])
    except (KeyError, ValueError):
        return False
    age = datetime.now().astimezone() - when
    settled = bool(entry.get("fired")) or bool(entry.get("skipped"))
    return age < (settled_within if settled else retry_after)
