"""Small persisted state: what fired when, and cached capability probes.

Nothing here is sensitive - it is timestamps, exit codes and flag names - but
it is still written 0600 so a shared machine cannot read your schedule.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from claudron import paths

STATE_VERSION = 1


def load() -> dict[str, Any]:
    path = paths.state_file()
    if not path.exists():
        return {"version": STATE_VERSION, "fires": {}, "probe": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "fires": {}, "probe": {}}
    if not isinstance(data, dict):
        return {"version": STATE_VERSION, "fires": {}, "probe": {}}
    data.setdefault("version", STATE_VERSION)
    data.setdefault("fires", {})
    data.setdefault("probe", {})
    return data


def save(data: dict[str, Any]) -> None:
    with contextlib.suppress(OSError):
        paths.write_private(paths.state_file(), json.dumps(data, indent=2, sort_keys=True))


def record_fire(slot: str, payload: dict[str, Any]) -> None:
    data = load()
    fires = data.setdefault("fires", {})
    fires[slot] = payload
    # Keep the file small: only the most recent 200 fires are useful.
    if len(fires) > 200:
        for key in sorted(fires)[: len(fires) - 200]:
            fires.pop(key, None)
    save(data)


def already_fired(slot: str) -> dict[str, Any] | None:
    entry = load().get("fires", {}).get(slot)
    return entry if isinstance(entry, dict) else None
