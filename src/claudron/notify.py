"""Optional desktop notifications.

Fully opt-in: with no ``[notify].command`` configured nothing ever runs, and
claudron never spawns a notifier it discovered on its own.
"""

from __future__ import annotations

import logging
import subprocess

from claudron.config import Config

log = logging.getLogger("claudron.notify")


def send(cfg: Config, title: str, body: str) -> bool:
    template = cfg.notify.command
    if not template:
        return False
    argv = [part.replace("{title}", title).replace("{body}", body) for part in template]
    try:
        subprocess.run(argv, check=False, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("notification command failed: %s", exc)
        return False
