"""Running claudron unattended: systemd, launchd, or plain cron.

Two shapes are offered because they fail differently:

``timer``   One short-lived process per anchor. Nothing runs in between, so it
            survives reboots and crashes for free. It cannot warn you about
            drift while you work, because it is not running then.
``daemon``  One long-lived process. Costs a few MB of RAM and gives you the
            blackout guard and catch-up after suspend.

Every generated unit runs ``claudron`` with the same config you already have;
none of them touches system-wide state - user units only.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from claudron import paths
from claudron.config import Config
from claudron.errors import ClaudronError

SERVICE_NAME = "claudron"
LAUNCHD_LABEL = "dev.claudron.anchor"


@dataclass(slots=True)
class Plan:
    files: list[tuple[Path, str]]
    commands: list[list[str]]
    notes: list[str]


def executable() -> list[str]:
    """How to invoke claudron from a unit file, as an absolute command."""
    found = shutil.which("claudron")
    if found:
        return [found]
    return [sys.executable, "-m", "claudron"]


def _env_overrides() -> list[str]:
    """Preserve any non-default directories so the unit sees the same config."""
    import os

    out = []
    for name in ("CLAUDRON_CONFIG_DIR", "CLAUDRON_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        value = os.environ.get(name)
        if value:
            out.append(f"{name}={value}")
    return out


# ---------------------------------------------------------------------------
# systemd
# ---------------------------------------------------------------------------


def systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def systemd_plan(cfg: Config, mode: str) -> Plan:
    exe = " ".join(executable())
    env = "\n".join(f"Environment={pair}" for pair in _env_overrides())
    env = env + "\n" if env else ""
    directory = systemd_dir()

    if mode == "daemon":
        service = f"""\
[Unit]
Description=claudron - keep Claude usage windows aligned with the working day
Documentation=https://github.com/locngoduc/claudron
After=network-online.target

[Service]
Type=simple
{env}ExecStart={exe} daemon
Restart=on-failure
RestartSec=30
# The process only reads Claude's transcripts and runs the claude CLI.
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""
        return Plan(
            files=[(directory / f"{SERVICE_NAME}.service", service)],
            commands=[
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"],
            ],
            notes=[
                "Anchors fire even while you are logged out only if lingering is enabled: "
                f"`loginctl enable-linger {_user()}`.",
                f"Logs: `journalctl --user -u {SERVICE_NAME} -f`",
            ],
        )

    calendars = "\n".join(f"OnCalendar=*-*-* {value}:00" for value in cfg.schedule.anchors)
    service = f"""\
[Unit]
Description=claudron - open a Claude usage window at a scheduled time
Documentation=https://github.com/locngoduc/claudron

[Service]
Type=oneshot
{env}ExecStart={exe} fire --scheduled
NoNewPrivileges=true
PrivateTmp=true
"""
    timer = f"""\
[Unit]
Description=claudron anchor schedule
Documentation=https://github.com/locngoduc/claudron

[Timer]
{calendars}
AccuracySec=15s
# Deliberately NOT Persistent: a missed anchor fired hours late would open a
# window in the wrong hour and push every later reset out of alignment.
# `claudron fire --scheduled` applies its own catch-up grace instead.
Persistent=false
Unit={SERVICE_NAME}.service

[Install]
WantedBy=timers.target
"""
    return Plan(
        files=[
            (directory / f"{SERVICE_NAME}.service", service),
            (directory / f"{SERVICE_NAME}.timer", timer),
        ],
        commands=[
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.timer"],
        ],
        notes=[
            "Anchors fire even while you are logged out only if lingering is enabled: "
            f"`loginctl enable-linger {_user()}`.",
            f"Next runs: `systemctl --user list-timers {SERVICE_NAME}.timer`",
            f"Logs: `journalctl --user -u {SERVICE_NAME} -f`",
        ],
    )


def _user() -> str:
    import getpass

    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "$USER"


# ---------------------------------------------------------------------------
# launchd
# ---------------------------------------------------------------------------


def launchd_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_plan(cfg: Config, mode: str) -> Plan:
    from xml.sax.saxutils import escape

    args = executable() + (["daemon"] if mode == "daemon" else ["fire", "--scheduled"])
    args_xml = "\n".join(f"    <string>{escape(a)}</string>" for a in args)

    if mode == "daemon":
        trigger = "  <key>KeepAlive</key>\n  <true/>\n  <key>RunAtLoad</key>\n  <true/>"
    else:
        entries = []
        for value in cfg.anchor_times():
            entries.append(
                "    <dict>\n"
                f"      <key>Hour</key><integer>{value.hour}</integer>\n"
                f"      <key>Minute</key><integer>{value.minute}</integer>\n"
                "    </dict>"
            )
        trigger = (
            "  <key>StartCalendarInterval</key>\n  <array>\n" + "\n".join(entries) + "\n  </array>"
        )

    env_pairs = _env_overrides()
    env_xml = ""
    if env_pairs:
        rows = "\n".join(
            f"    <key>{escape(p.split('=', 1)[0])}</key>"
            f"<string>{escape(p.split('=', 1)[1])}</string>"
            for p in env_pairs
        )
        env_xml = f"  <key>EnvironmentVariables</key>\n  <dict>\n{rows}\n  </dict>\n"

    log = paths.log_file()
    plist = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
{env_xml}{trigger}
  <key>StandardOutPath</key>
  <string>{escape(str(log))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(log))}</string>
</dict>
</plist>
"""
    target = launchd_path()
    return Plan(
        files=[(target, plist)],
        commands=[
            ["launchctl", "unload", str(target)],
            ["launchctl", "load", "-w", str(target)],
        ],
        notes=[
            "launchd does not wake a sleeping Mac for these; anchors fire on wake, and "
            "claudron skips any that are more than [schedule].catch_up_minutes overdue.",
            f"Logs: `tail -f {log}`",
        ],
    )


# ---------------------------------------------------------------------------
# cron fallback
# ---------------------------------------------------------------------------


def cron_lines(cfg: Config) -> list[str]:
    exe = " ".join(executable())
    lines = ["# claudron - generated by `claudron install --backend cron`"]
    for value in cfg.anchor_times():
        lines.append(f"{value.minute} {value.hour} * * * {exe} fire --scheduled >/dev/null 2>&1")
    return lines


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def detect_backend() -> str:
    system = platform.system()
    if system == "Darwin":
        return "launchd"
    if system == "Linux" and shutil.which("systemctl"):
        return "systemd"
    return "cron"


def build_plan(cfg: Config, backend: str, mode: str) -> Plan:
    if backend == "systemd":
        return systemd_plan(cfg, mode)
    if backend == "launchd":
        return launchd_plan(cfg, mode)
    if backend == "cron":
        return Plan(
            files=[],
            commands=[],
            notes=[
                "cron cannot be edited safely from a script. Run `crontab -e` and add:",
                *[f"    {line}" for line in cron_lines(cfg)],
                "cron does not run missed jobs after a suspend; `fire --scheduled` will "
                "refuse to fire an anchor that is too far overdue.",
            ],
        )
    raise ClaudronError(f"unknown backend {backend!r}")


def apply(plan: Plan, *, dry_run: bool) -> None:
    for path, content in plan.files:
        if dry_run:
            continue
        paths.ensure_private_dir(path.parent)
        path.write_text(content, encoding="utf-8")
    if dry_run:
        return
    for command in plan.commands:
        subprocess.run(command, check=False, capture_output=True)


def uninstall_plan(backend: str) -> Plan:
    if backend == "systemd":
        directory = systemd_dir()
        return Plan(
            files=[],
            commands=[
                ["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.timer"],
                ["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"],
                ["systemctl", "--user", "daemon-reload"],
            ],
            notes=[
                f"Remove {directory / (SERVICE_NAME + '.service')} and "
                f"{directory / (SERVICE_NAME + '.timer')} to delete the unit files."
            ],
        )
    if backend == "launchd":
        target = launchd_path()
        return Plan(
            files=[],
            commands=[["launchctl", "unload", "-w", str(target)]],
            notes=[f"Remove {target} to delete the agent."],
        )
    return Plan(files=[], commands=[], notes=["Remove the claudron lines from `crontab -e`."])
