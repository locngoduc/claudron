"""Choosing the anchors for you.

A day holds at most ``24 // window_hours`` anchors, so the interesting question
is never "how many" - it is *where*. This module takes what you know about your
own day (when you sleep, when you must not be interrupted, when you want a
fresh window waiting) and searches every legal arrangement for the one that
puts the unavoidable idle hours where they cost you least.

Everything here works on a whole-hour grid, for two reasons: window starts are
rounded down to the hour by default, so an anchor at 12:30 simply wastes 30
minutes; and a schedule you cannot state in whole hours is a schedule you will
not remember.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from claudron.config import Config, parse_hhmm
from claudron.errors import ClaudronError

#: Cost of one idle hour, by what you told us about that hour.
COST_IDLE = 0.0  # asleep or on a break - the hour was free anyway
COST_NEUTRAL = 1.5  # awake, nothing declared
COST_BUSY = 4.0  # you said you work then; idle time here really hurts

#: Preference - not a penalty - about the moment you start your day.
#:
#: Inheriting a window that opened while you slept is barely a downside: you
#: spent none of its budget, you can work immediately, and it resets sooner.
#: Being *idle* at wake-up is the genuinely awkward case, and it is already
#: priced in, because an idle hour while you are awake costs COST_NEUTRAL. So
#: the tie-break below stays deliberately small: it must never outweigh where
#: the idle hours actually land.
BONUS_WAKE_IS_ANCHOR = 1.0  # a full window opens exactly as you sit down
PENALTY_PER_STALE_HOUR = 0.1  # mild preference for more of the window remaining


@dataclass(slots=True)
class Preferences:
    window_hours: int = 5
    count: int | None = None
    #: Hours that must be anchors: "a fresh window starts here".
    start_at: list[int] = field(default_factory=list)
    #: Hours where a fresh budget must be available: an anchor, or idle time.
    free_at: list[int] = field(default_factory=list)
    #: Hour ranges where idle time is free (asleep, lunch, a standing meeting).
    idle: list[tuple[int, int]] = field(default_factory=list)
    #: Hour ranges where idle time is expensive (your real working hours).
    busy: list[tuple[int, int]] = field(default_factory=list)
    #: The hour your day starts. Derived from the first `idle` range if unset.
    wake: int | None = None

    def max_anchors(self) -> int:
        return 24 // self.window_hours

    def anchors_wanted(self) -> int:
        # `or` would silently turn an explicit 0 into the maximum.
        return self.max_anchors() if self.count is None else self.count


@dataclass(slots=True)
class Candidate:
    anchors: tuple[int, ...]
    window_hours: int
    cost: float
    covered: set[int] = field(default_factory=set)
    idle_hours: set[int] = field(default_factory=set)

    def windows(self) -> list[tuple[int, int]]:
        return [(a, (a + self.window_hours) % 24) for a in self.anchors]

    def idle_ranges(self) -> list[tuple[int, int]]:
        return _ranges(self.idle_hours)

    def as_dict(self) -> dict:
        return {
            "anchors": [f"{h:02d}:00" for h in self.anchors],
            "cost": round(self.cost, 2),
            "windows": [f"{a:02d}:00-{b:02d}:00" for a, b in self.windows()],
            "idle": [f"{a:02d}:00-{b:02d}:00" for a, b in self.idle_ranges()],
        }


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_hour(value: str) -> int:
    """Accept ``14``, ``14:00`` or ``14:30``; minutes are dropped, loudly."""
    text = str(value).strip()
    if ":" not in text:
        try:
            hour = int(text)
        except ValueError:
            raise ClaudronError(f"invalid time {value!r}: expected HH or HH:MM") from None
    else:
        hour = parse_hhmm(text).hour
    if not 0 <= hour <= 23:
        raise ClaudronError(f"invalid time {value!r}: hour must be 0-23")
    return hour


def parse_range(value: str) -> tuple[int, int]:
    """``23:00-06:00`` -> ``(23, 6)``. Ranges may wrap past midnight."""
    text = str(value).strip().replace("–", "-")
    parts = [p for p in text.split("-") if p.strip()]
    if len(parts) != 2:
        raise ClaudronError(f"invalid range {value!r}: expected START-END, e.g. 23:00-06:00")
    start, end = parse_hour(parts[0]), parse_hour(parts[1])
    if start == end:
        raise ClaudronError(f"invalid range {value!r}: start and end are the same hour")
    return start, end


def hours_in(span: tuple[int, int]) -> set[int]:
    start, end = span
    out: set[int] = set()
    hour = start
    while hour != end:
        out.add(hour)
        hour = (hour + 1) % 24
    return out


def _ranges(hours: set[int]) -> list[tuple[int, int]]:
    """Collapse a set of hours into contiguous, midnight-wrapping ranges."""
    if not hours or len(hours) == 24:
        return [(0, 0)] if hours else []
    starts = sorted(h for h in hours if (h - 1) % 24 not in hours)
    out = []
    for start in starts:
        end = start
        while end in hours:
            end = (end + 1) % 24
        out.append((start, end))
    return out


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _arrangements(count: int, window: int):
    """Every legal anchor set, as sorted tuples, each generated exactly once."""
    if count * window > 24:
        return

    def extend(prefix: list[int]):
        remaining = count - len(prefix) - 1
        if remaining < 0:
            if 24 - prefix[-1] + prefix[0] >= window:
                yield tuple(prefix)
            return
        # The last anchor must leave room for the rest plus the wrap-around gap.
        latest = prefix[0] + 24 - window * (remaining + 1)
        for hour in range(prefix[-1] + window, min(latest, 23) + 1):
            yield from extend([*prefix, hour])

    for first in range(24):
        yield from extend([first])


def _hour_costs(prefs: Preferences) -> dict[int, float]:
    idle = set().union(*(hours_in(span) for span in prefs.idle)) if prefs.idle else set()
    busy = set().union(*(hours_in(span) for span in prefs.busy)) if prefs.busy else set()
    costs = {}
    for hour in range(24):
        if hour in idle:
            costs[hour] = COST_IDLE
        elif hour in busy:
            costs[hour] = COST_BUSY
        else:
            costs[hour] = COST_NEUTRAL
    return costs


def _score(anchors: tuple[int, ...], prefs: Preferences, costs: dict[int, float]) -> Candidate:
    window = prefs.window_hours
    covered: set[int] = set()
    for anchor in anchors:
        covered |= {(anchor + offset) % 24 for offset in range(window)}
    idle_hours = set(range(24)) - covered

    cost = sum(costs[hour] for hour in idle_hours)

    if prefs.wake is not None:
        wake = prefs.wake
        if wake in anchors:
            cost -= BONUS_WAKE_IS_ANCHOR
        elif wake not in idle_hours:
            # You start the day inside a window that opened while you slept.
            # Mildly prefer more of it remaining; being idle instead is already
            # charged for as an awake idle hour.
            opened = max(
                (a for a in anchors if (wake - a) % 24 < window), key=lambda a: (wake - a) % 24
            )
            cost += ((wake - opened) % 24) * PENALTY_PER_STALE_HOUR

    return Candidate(
        anchors=anchors,
        window_hours=window,
        cost=cost,
        covered=covered,
        idle_hours=idle_hours,
    )


def _satisfies(candidate: Candidate, prefs: Preferences) -> bool:
    if any(hour not in candidate.anchors for hour in prefs.start_at):
        return False
    return all(hour in candidate.anchors or hour in candidate.idle_hours for hour in prefs.free_at)


def search(prefs: Preferences, *, limit: int = 3) -> list[Candidate]:
    """Rank every legal schedule and return the best few."""
    count = prefs.anchors_wanted()
    if count < 1:
        raise ClaudronError("a schedule needs at least one anchor")
    if count > prefs.max_anchors():
        raise ClaudronError(
            f"{count} anchors x {prefs.window_hours}h = {count * prefs.window_hours}h of window, "
            f"but a day only has 24h. The most a daily-repeating schedule can hold is "
            f"{prefs.max_anchors()}."
        )

    costs = _hour_costs(prefs)
    scored = [
        candidate
        for candidate in (_score(a, prefs, costs) for a in _arrangements(count, prefs.window_hours))
        if _satisfies(candidate, prefs)
    ]
    # Ties are broken by the earliest anchor, so the output is deterministic.
    scored.sort(key=lambda c: (round(c.cost, 6), c.anchors))
    return scored[:limit]


def explain(candidate: Candidate, prefs: Preferences) -> list[str]:
    """Plain-language notes about what this schedule does for the times you named."""
    notes: list[str] = []
    window = candidate.window_hours
    for hour in sorted(set(prefs.start_at)):
        notes.append(
            f"{hour:02d}:00 opens a fresh {window}h window, running to "
            f"{(hour + window) % 24:02d}:00"
        )
    for hour in sorted(set(prefs.free_at) - set(prefs.start_at)):
        if hour in candidate.anchors:
            notes.append(
                f"{hour:02d}:00 opens a fresh {window}h window, running to "
                f"{(hour + window) % 24:02d}:00"
            )
        else:
            notes.append(
                f"{hour:02d}:00 is inside an idle gap - nothing is counting, and your "
                f"next message opens a full window"
            )
    if prefs.wake is not None:
        wake = prefs.wake
        if wake in candidate.anchors:
            notes.append(f"you start the day at {wake:02d}:00 on a window that opens right then")
        elif wake in candidate.idle_hours:
            gap_end = next(
                (end for start, end in candidate.idle_ranges() if wake in hours_in((start, end))),
                None,
            )
            notes.append(
                f"at {wake:02d}:00 nothing is running"
                + (
                    f", and the plan wants you quiet until {gap_end:02d}:00"
                    if gap_end is not None
                    else ""
                )
            )
        else:
            opened = max(
                (a for a in candidate.anchors if (wake - a) % 24 < window),
                key=lambda a: (wake - a) % 24,
            )
            left = window - (wake - opened) % 24
            notes.append(
                f"at {wake:02d}:00 you pick up the window opened at {opened:02d}:00 while you "
                f"slept - {left}h left on it, none of its budget spent, resetting at "
                f"{(opened + window) % 24:02d}:00"
            )
    return notes


def from_config(cfg: Config) -> Preferences:
    return Preferences(window_hours=cfg.schedule.window_hours)
