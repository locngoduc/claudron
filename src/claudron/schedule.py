"""The window model.

The one rule everything here follows:

    A usage window opens on the first message sent while no window is open,
    and lasts ``window_hours``. A message sent while a window is already open
    changes nothing.

So the schedule is not "reset at these times" - it is "send the first message
at these times, and stay quiet in between". Both halves matter, and the second
one is the half people get wrong, which is why claudron computes the idle gaps
explicitly and warns when you type into one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

from claudron.config import Config

ERROR = "error"
WARN = "warn"
INFO = "info"

OPENS = "opens"
BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    observed: bool = False

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    @property
    def length(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class AnchorPlan:
    at: datetime
    label: str
    status: str
    window: Window | None = None
    blocked_by: Window | None = None


@dataclass(frozen=True, slots=True)
class Gap:
    """An interval with no open window. Sending a message here opens one early."""

    start: datetime
    end: datetime

    @property
    def length(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Issue:
    level: str
    code: str
    message: str
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message, "hint": self.hint}


@dataclass(slots=True)
class Simulation:
    windows: list[Window] = field(default_factory=list)
    anchors: list[AnchorPlan] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def window_at(self, moment: datetime) -> Window | None:
        for window in self.windows:
            if window.contains(moment):
                return window
        return None

    def next_anchor_after(self, moment: datetime) -> AnchorPlan | None:
        for plan in self.anchors:
            if plan.at > moment:
                return plan
        return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def local_now(tz: tzinfo) -> datetime:
    return datetime.now(tz)


def anchor_datetimes(
    anchors: list[time],
    labels: list[str],
    day: date,
    tz: tzinfo,
    *,
    days: int = 1,
    offset_days: int = 0,
) -> list[tuple[datetime, str]]:
    """Materialise anchor times into concrete datetimes across ``days`` days."""
    out: list[tuple[datetime, str]] = []
    for index in range(days):
        current = day + timedelta(days=offset_days + index)
        for anchor, label in zip(anchors, labels, strict=True):
            out.append((datetime.combine(current, anchor, tzinfo=tz), label))
    out.sort(key=lambda item: item[0])
    return out


def simulate(
    anchors: list[tuple[datetime, str]],
    window: timedelta,
    *,
    floor: bool = True,
    seed: Window | None = None,
) -> Simulation:
    """Replay the anchors in order and record which of them actually open a window.

    ``seed`` is an already-open window observed from real usage; pass it so the
    simulation knows the day did not start from a clean slate.
    """
    sim = Simulation()
    active: Window | None = None
    pending_seed = seed

    def adopt_seed() -> None:
        """Splice the observed window in, replacing whatever was simulated for it."""
        nonlocal active, pending_seed
        assert pending_seed is not None
        if active is not None and active.end > pending_seed.start:
            # Reality overrides the simulation: the earlier window cannot have
            # run past the moment a new one was observed to open. Rewrite both
            # the window and the anchor that reports it, so the two never
            # disagree about the same interval.
            truncated = Window(active.start, pending_seed.start, active.observed)
            sim.windows[-1] = truncated
            for index, plan in enumerate(sim.anchors):
                if plan.window is active:
                    sim.anchors[index] = AnchorPlan(
                        at=plan.at, label=plan.label, status=plan.status, window=truncated
                    )
        sim.windows.append(pending_seed)
        active = pending_seed
        pending_seed = None

    for at, label in sorted(anchors, key=lambda item: item[0]):
        if pending_seed is not None and at >= pending_seed.start:
            adopt_seed()
        if active is not None and active.contains(at):
            sim.anchors.append(AnchorPlan(at=at, label=label, status=BLOCKED, blocked_by=active))
            continue
        start = floor_hour(at) if floor else at
        if active is not None and start < active.end:
            # Rounding down would overlap the previous window; the window can
            # only begin once the previous one has actually expired.
            start = active.end
            sim.issues.append(
                Issue(
                    WARN,
                    "clamped-start",
                    f"anchor {label} rounds down into the previous window; "
                    f"treating {start:%H:%M} as the start instead",
                    "Align your anchors to the top of the hour to avoid this.",
                )
            )
        new = Window(start=start, end=start + window)
        sim.windows.append(new)
        sim.anchors.append(AnchorPlan(at=at, label=label, status=OPENS, window=new))
        active = new

    if pending_seed is not None:
        adopt_seed()

    for previous, following in zip(sim.windows, sim.windows[1:], strict=False):
        if following.start > previous.end:
            sim.gaps.append(Gap(previous.end, following.start))
    return sim


# ---------------------------------------------------------------------------
# static analysis of the anchor set
# ---------------------------------------------------------------------------


def cycle_issues(cfg: Config) -> list[Issue]:
    """Check the anchor set on its own, independent of any particular day.

    This is the check that catches the mistake almost everyone makes: listing
    the *reset* times rather than the *first message* times, which produces
    anchors closer together than a window is long.
    """
    issues: list[Issue] = []
    window_hours = cfg.schedule.window_hours
    times = cfg.anchor_times()
    labels = list(cfg.schedule.anchors)
    order = sorted(range(len(times)), key=lambda i: (times[i].hour, times[i].minute))
    times = [times[i] for i in order]
    labels = [labels[i] for i in order]

    max_anchors = 24 // window_hours
    if len(times) > max_anchors:
        issues.append(
            Issue(
                ERROR,
                "too-many-anchors",
                f"{len(times)} anchors x {window_hours}h = {len(times) * window_hours}h of "
                f"window, but a day only has 24h",
                f"A daily-repeating schedule holds at most {max_anchors} anchors "
                f"({max_anchors * window_hours}h of coverage). Remove "
                f"{len(times) - max_anchors}.",
            )
        )

    minutes = [t.hour * 60 + t.minute for t in times]
    window_minutes = window_hours * 60
    for index, current in enumerate(minutes):
        nxt = minutes[(index + 1) % len(minutes)]
        gap = (nxt - current) % (24 * 60) or 24 * 60
        if gap < window_minutes:
            closes_at = (current + window_minutes) % (24 * 60)
            issues.append(
                Issue(
                    ERROR,
                    "swallowed-anchor",
                    f"anchor {labels[(index + 1) % len(labels)]} is only "
                    f"{gap // 60}h{gap % 60:02d}m after {labels[index]}, so it lands inside "
                    f"the window {labels[index]} opens (which runs to "
                    f"{closes_at // 60:02d}:{closes_at % 60:02d}) and resets nothing",
                    f"Drop it, or move it to {closes_at // 60:02d}:{closes_at % 60:02d} "
                    f"or later. Note that {closes_at // 60:02d}:{closes_at % 60:02d} is when "
                    f"your limits free up - it is not itself an anchor unless you want the "
                    f"next window to start exactly then.",
                )
            )

    for label, moment in zip(labels, times, strict=True):
        if cfg.usage.floor_window_to_hour and moment.minute != 0:
            issues.append(
                Issue(
                    WARN,
                    "off-hour-anchor",
                    f"anchor {label} is not on the hour; with "
                    f"[usage].floor_window_to_hour the window is treated as starting at "
                    f"{moment.hour:02d}:00, so you lose {moment.minute}m of it",
                    f"Use {moment.hour:02d}:00 instead.",
                )
            )

    covered = len(times) * window_hours
    if not any(i.level == ERROR for i in issues):
        idle = 24 - covered
        issues.append(
            Issue(
                INFO,
                "coverage",
                f"{covered}h of the day covered by {len(times)} windows, {idle}h idle",
                (
                    f"{idle}h idle is the minimum any daily-repeating {window_hours}h "
                    f"schedule can reach with {len(times)} anchors."
                    if idle == 24 - max_anchors * window_hours and len(times) == max_anchors
                    else f"Adding an anchor is possible while you stay under {max_anchors} of them."
                ),
            )
        )

    issues.extend(_timezone_issues(cfg))
    return issues


def _timezone_issues(cfg: Config) -> list[Issue]:
    """Flag DST, which silently moves every anchor by an hour twice a year."""
    tz = cfg.tz()
    year = datetime.now(tz).year
    offsets = set()
    for month in (1, 4, 7, 10):
        probe = datetime(year, month, 15, 12, 0, tzinfo=tz)
        offsets.add(probe.utcoffset())
    if len(offsets) > 1:
        return [
            Issue(
                WARN,
                "dst",
                f"timezone {cfg.tz_label()} observes daylight saving time",
                "Anchors follow local wall-clock time, so on the two changeover days one "
                "window is an hour short or an hour long. Nothing breaks, but expect one "
                "off day each spring and autumn.",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# day plan
# ---------------------------------------------------------------------------


def plan_day(cfg: Config, day: date, *, seed: Window | None = None) -> Simulation:
    """Simulate one calendar day, including the spill-over from the day before."""
    tz = cfg.tz()
    window = timedelta(hours=cfg.schedule.window_hours)
    anchors = anchor_datetimes(
        cfg.anchor_times(), list(cfg.schedule.anchors), day, tz, days=3, offset_days=-1
    )
    sim = simulate(anchors, window, floor=cfg.usage.floor_window_to_hour, seed=seed)

    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    end = start + timedelta(days=1)
    visible = Simulation(
        windows=[w for w in sim.windows if w.end > start and w.start < end],
        anchors=[a for a in sim.anchors if start <= a.at < end],
        gaps=[g for g in sim.gaps if g.end > start and g.start < end],
        issues=list(sim.issues),
    )
    return visible


def next_due_anchor(
    cfg: Config, now: datetime, *, catch_up: timedelta | None = None
) -> AnchorPlan | None:
    """The anchor that should fire now, if any.

    Returns an anchor whose time has passed but is still within the catch-up
    grace period. Anything older is deliberately skipped: firing it would open
    a window in the wrong clock hour and drag every later reset out of place.
    """
    tz = cfg.tz()
    grace = catch_up if catch_up is not None else timedelta(minutes=cfg.schedule.catch_up_minutes)
    jitter = timedelta(seconds=cfg.schedule.jitter_seconds)
    candidates = anchor_datetimes(
        cfg.anchor_times(), list(cfg.schedule.anchors), now.date(), tz, days=3, offset_days=-1
    )
    due = [
        AnchorPlan(at=at, label=label, status=OPENS)
        for at, label in candidates
        if at + jitter <= now <= at + jitter + grace
    ]
    return due[-1] if due else None


def next_anchor(cfg: Config, now: datetime) -> tuple[datetime, str] | None:
    tz = cfg.tz()
    candidates = anchor_datetimes(
        cfg.anchor_times(), list(cfg.schedule.anchors), now.date(), tz, days=3, offset_days=-1
    )
    jitter = timedelta(seconds=cfg.schedule.jitter_seconds)
    for at, label in candidates:
        if at + jitter > now:
            return at + jitter, label
    return None
