"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import platform
import shutil
import sys
from datetime import date, datetime, timedelta

from claudron import (
    __version__,
    anchor,
    completion,
    config,
    paths,
    render,
    service,
    state,
    suggest,
    usage,
)
from claudron.daemon import Daemon, current_blocks
from claudron.errors import ClaudronError, ConfigError
from claudron.schedule import (
    BLOCKED,
    ERROR,
    INFO,
    WARN,
    cycle_issues,
    next_anchor,
    next_due_anchor,
    plan_day,
)

log = logging.getLogger("claudron")


# ---------------------------------------------------------------------------
# infrastructure
# ---------------------------------------------------------------------------


def setup_logging(verbose: bool, *, to_file: bool) -> None:
    root = logging.getLogger("claudron")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(console)

    if to_file:
        try:
            paths.ensure_private_dir(paths.state_dir())
            handler = logging.handlers.RotatingFileHandler(
                paths.log_file(), maxBytes=512_000, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
            )
            root.addHandler(handler)
        except OSError as exc:  # pragma: no cover
            root.warning("cannot write log file: %s", exc)


def emit(payload: dict, as_json: bool) -> bool:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return True
    return False


def load_config(args, *, required: bool = True) -> config.Config:
    path = args.config if getattr(args, "config", None) else None
    return config.load(path, required=required)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    style = render.Style()
    print(render.banner(style), end="")
    target = args.config or paths.config_file()
    if target.exists() and not args.force:
        raise ConfigError(
            f"{target} already exists. Edit it directly, or pass --force to replace it "
            f"(a backup is written alongside)."
        )

    cfg = config.Config()
    preset = args.preset
    if args.anchors:
        cfg.schedule.anchors = [a.strip() for a in args.anchors.split(",") if a.strip()]
        preset = "custom"
    elif preset:
        cfg.schedule.anchors = list(config.PRESETS[preset][1])
    if args.timezone:
        cfg.schedule.timezone = args.timezone
    if args.model:
        cfg.anchor.model = args.model

    config.validate(cfg)

    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)
        print(f"backed up existing config to {backup}")

    config.save(cfg, target)
    print(style(f"wrote {target}", "green", "bold"))
    print(f"  preset      {preset or config.DEFAULT_PRESET}")
    print(f"  timezone    {cfg.tz_label()}")
    print(f"  anchors     {', '.join(cfg.schedule.anchors)}")
    print()

    issues = cycle_issues(cfg)
    lines = render.issue_lines(issues, style)
    if lines:
        print(render.rule("schedule check", style))
        print("\n".join(lines))
        print()

    print(render.rule("next steps", style))
    print(f"  1. `claudron plan`     {style('see the day this produces', 'grey')}")
    print(f"  2. `claudron doctor`   {style('check the environment', 'grey')}")
    print(f"  3. `claudron install`  {style('run it unattended', 'grey')}")
    return 0 if not any(i.level == ERROR for i in issues) else 1


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def cmd_plan(args) -> int:
    cfg = load_config(args)
    style = render.Style()
    tz = cfg.tz()
    now = datetime.now(tz)
    day = date.fromisoformat(args.date) if args.date else now.date()

    seed = None
    if args.live and day == now.date():
        blocks = current_blocks(cfg)
        active = usage.active_block(blocks, now)
        if active is not None:
            seed = active.window

    sim = plan_day(cfg, day, seed=seed)
    issues = cycle_issues(cfg) + sim.issues

    if emit(
        {
            "date": day.isoformat(),
            "timezone": cfg.tz_label(),
            "window_hours": cfg.schedule.window_hours,
            "windows": [
                {"start": w.start.isoformat(), "end": w.end.isoformat(), "observed": w.observed}
                for w in sim.windows
            ],
            "anchors": [
                {"at": a.at.isoformat(), "label": a.label, "status": a.status} for a in sim.anchors
            ],
            "idle_gaps": [
                {
                    "start": g.start.isoformat(),
                    "end": g.end.isoformat(),
                    "minutes": int(g.length.total_seconds() // 60),
                }
                for g in sim.gaps
            ],
            "issues": [i.as_dict() for i in issues],
        },
        args.json,
    ):
        return 1 if any(i.level == ERROR for i in issues) else 0

    heading = f"{day:%A %d %B %Y}"
    if seed is not None:
        heading += "  (seeded from the window currently open)"
    print(render.rule(heading, style))
    print()
    print(render.timeline(sim, day, tz, style, now if day == now.date() else None))
    print()
    print(
        f"  {style(style.full, 'green')} window open   "
        f"{style(style.empty, 'grey')} idle   "
        f"{style(style.anchor, 'cyan')} anchor   "
        f"{style('x', 'red', 'bold')} anchor that opens nothing"
    )
    print()

    rows = []
    for plan in sim.anchors:
        if plan.status == BLOCKED and plan.blocked_by is not None:
            rows.append(
                [
                    plan.label,
                    style("no effect", "red"),
                    f"inside the window {plan.blocked_by.start:%H:%M}-{plan.blocked_by.end:%H:%M}",
                ]
            )
        elif plan.window is not None:
            rows.append(
                [
                    plan.label,
                    style("opens", "green"),
                    f"{plan.window.start:%H:%M} {style.arrow} {plan.window.end:%H:%M}",
                ]
            )
    if rows:
        print(render.rule("anchors", style))
        print(render.table(rows, ["time", "effect", "window"], style))
        print()

    if sim.gaps:
        total = sum((g.length for g in sim.gaps), timedelta())
        print(render.rule("stay quiet", style))
        for gap in sim.gaps:
            print(
                f"  {gap.start:%H:%M} {style.arrow} {gap.end:%H:%M}   "
                f"{render.human_delta(gap.length)}"
            )
        print(
            style(
                f"  Anything you send in these gaps opens a window early and shifts every\n"
                f"  later reset. Total idle today: {render.human_delta(total)}.",
                "grey",
            )
        )
        print()

    lines = render.issue_lines(issues, style)
    if lines:
        print(render.rule("notes", style))
        print("\n".join(lines))
    return 1 if any(i.level == ERROR for i in issues) else 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args) -> int:
    cfg = load_config(args)
    style = render.Style()
    tz = cfg.tz()
    now = datetime.now(tz)

    events = usage.read_events(cfg, since=now - timedelta(days=cfg.usage.lookback_days))
    blocks = usage.build_blocks(events, cfg)
    active = usage.active_block(blocks, now)
    baseline = usage.baseline_tokens(blocks, exclude=active)
    sim = plan_day(cfg, now.date(), seed=active.window if active else None)
    upcoming = next_anchor(cfg, now)

    on_plan = None
    if active is not None:
        on_plan = any(
            a.window is not None and a.window.start == active.window.start for a in sim.anchors
        )

    payload = {
        "now": now.isoformat(),
        "timezone": cfg.tz_label(),
        "window": None,
        "next_anchor": None,
        "baseline_tokens": baseline,
    }
    if active is not None:
        payload["window"] = {
            "start": active.window.start.isoformat(),
            "end": active.window.end.isoformat(),
            "remaining_seconds": int((active.window.end - now).total_seconds()),
            "messages": active.events,
            "input_tokens": active.input_tokens,
            "output_tokens": active.output_tokens,
            "cache_creation_tokens": active.cache_creation_tokens,
            "cache_read_tokens": active.cache_read_tokens,
            "total_tokens": active.total_tokens,
            "on_plan": on_plan,
        }
    if upcoming:
        payload["next_anchor"] = {"at": upcoming[0].isoformat(), "label": upcoming[1]}

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    if args.short:
        parts = []
        if active is not None:
            parts.append(
                f"{active.window.start:%H:%M}{style.arrow}{active.window.end:%H:%M} "
                f"{render.human_delta(active.window.end - now)}"
            )
            parts.append(render.human_tokens(active.total_tokens))
        else:
            parts.append("no window")
        if upcoming:
            parts.append(f"next {upcoming[1]}")
        print(f" {style.bullet} ".join(parts))
        return 0

    print(render.rule("now", style))
    if active is None:
        print(f"  {style('no window open', 'yellow', 'bold')}")
        print(
            style(
                "  Your limits are free. The next message you send - from anywhere,\n"
                "  including a normal Claude Code session - opens the next window.",
                "grey",
            )
        )
    else:
        remaining = active.window.end - now
        print(
            f"  window      {active.window.start:%H:%M} {style.arrow} "
            f"{active.window.end:%H:%M}   "
            f"{style(render.human_delta(remaining) + ' left', 'bold')}"
        )
        if on_plan:
            print(f"  alignment   {style('on plan', 'green')}")
        else:
            print(f"  alignment   {style('off plan', 'yellow')}  (opened outside your anchors)")
        share = f" ({active.total_tokens / baseline:.0%} of your busiest)" if baseline else ""
        print(
            f"  used        {render.human_tokens(active.total_tokens)} tokens over "
            f"{active.events} messages{style(share, 'grey')}"
        )
        print(
            style(
                f"              fresh {render.human_tokens(active.fresh_tokens)} "
                f"{style.bullet} cache read {render.human_tokens(active.cache_read_tokens)}",
                "grey",
            )
        )
        if baseline and active.total_tokens > baseline * cfg.warnings.high_usage_ratio:
            print(
                f"  {style('heads up', 'yellow', 'bold')}    this window is already near your "
                f"busiest on record."
            )
            print(
                style(
                    "              claudron cannot see your real limit - Anthropic does not\n"
                    "              expose it locally - this compares against your own history.",
                    "grey",
                )
            )
    print()

    print(render.rule("next", style))
    gap = next((g for g in sim.gaps if g.end > now), None)
    if gap is not None and gap.start <= now < gap.end:
        print(
            f"  {style('idle gap', 'yellow', 'bold')}    until {gap.end:%H:%M} "
            f"({render.human_delta(gap.end - now)})"
        )
        print(style("              stay quiet, or the next window opens early", "grey"))
    if upcoming:
        into = upcoming[0] - now
        print(
            f"  anchor      {upcoming[1]} in {render.human_delta(into)}   "
            f"{style.arrow} window until "
            f"{(upcoming[0] + timedelta(hours=cfg.schedule.window_hours)):%H:%M}"
        )
    return 0


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------


def cmd_usage(args) -> int:
    cfg = load_config(args)
    style = render.Style()
    tz = cfg.tz()
    now = datetime.now(tz)
    days = args.days or cfg.usage.lookback_days

    stats = usage.ParseStats()
    events = usage.read_events(cfg, since=now - timedelta(days=days), stats=stats)
    blocks = usage.build_blocks(events, cfg)
    anchor_project = usage.anchor_project_name()

    if args.json:
        print(
            json.dumps(
                {
                    "days": days,
                    "windows": [
                        {
                            "start": b.window.start.isoformat(),
                            "end": b.window.end.isoformat(),
                            "messages": b.events,
                            "input_tokens": b.input_tokens,
                            "output_tokens": b.output_tokens,
                            "cache_creation_tokens": b.cache_creation_tokens,
                            "cache_read_tokens": b.cache_read_tokens,
                            "total_tokens": b.total_tokens,
                            "models": b.models,
                        }
                        for b in blocks
                    ],
                    "parse": {
                        "files_seen": stats.files_seen,
                        "files_parsed": stats.files_parsed,
                        "files_cached": stats.files_cached,
                        "lines": stats.lines,
                        "malformed": stats.malformed,
                        "duration_s": round(stats.duration_s, 3),
                    },
                },
                indent=2,
            )
        )
        return 0

    if not blocks:
        print(f"no usage found in {cfg.projects_path()} over the last {days} days")
        return 0

    rows = []
    for block in blocks[-args.limit :]:
        marker = ""
        if block.is_active(now):
            marker = style(" ← open", "green", "bold")
        anchor_only = set(block.projects) == {anchor_project}
        rows.append(
            [
                f"{block.window.start:%a %d %b %H:%M}",
                f"{block.window.end:%H:%M}",
                str(block.events),
                render.human_tokens(block.fresh_tokens),
                render.human_tokens(block.cache_read_tokens),
                render.human_tokens(block.total_tokens) + (" (anchor)" if anchor_only else ""),
                marker,
            ]
        )
    print(render.rule(f"usage windows, last {days} days", style))
    headers = ["window start", "ends", "msgs", "fresh", "cached", "total", ""]
    print(render.table(rows, headers, style))
    print()

    total = sum(b.total_tokens for b in blocks)
    busiest = max(blocks, key=lambda b: b.total_tokens)
    anchors_only = [b for b in blocks if set(b.projects) == {anchor_project}]

    print(render.rule("summary", style))
    print(f"  windows     {len(blocks)}")
    print(f"  tokens      {render.human_tokens(total)} total")
    print(
        f"  busiest     {busiest.window.start:%d %b %H:%M} with "
        f"{render.human_tokens(busiest.total_tokens)}"
    )
    if anchors_only:
        print(
            f"  anchors     {len(anchors_only)} window(s) opened by claudron alone, "
            f"{render.human_tokens(sum(b.total_tokens for b in anchors_only))} tokens"
        )
    print(
        style(
            f"  parsed {stats.lines} lines from {stats.files_parsed} file(s) "
            f"({stats.files_cached} cached) in {stats.duration_s * 1000:.0f}ms"
            + (f", {stats.malformed} unreadable" if stats.malformed else ""),
            "grey",
        )
    )
    print()
    print(
        style(
            "  These are token counts read from your local transcripts. They are not a\n"
            "  percentage of your plan limit: that number is not available on this machine.",
            "grey",
        )
    )
    return 0


# ---------------------------------------------------------------------------
# fire
# ---------------------------------------------------------------------------


def cmd_fire(args) -> int:
    cfg = load_config(args)
    style = render.Style()
    tz = cfg.tz()
    now = datetime.now(tz)

    slot = ""
    if args.scheduled:
        due = next_due_anchor(cfg, now)
        if due is None:
            upcoming = next_anchor(cfg, now)
            message = "no anchor is due right now"
            if upcoming:
                message += f"; next is {upcoming[1]} at {upcoming[0]:%H:%M}"
            print(
                style(message, "grey")
                + style(
                    f"\n(an anchor more than {cfg.schedule.catch_up_minutes}m overdue is skipped "
                    f"on purpose: firing late would move the window into the wrong hour)",
                    "grey",
                )
            )
            return 0
        slot = anchor.slot_key(due.at, due.label)
        if anchor.handled_recently(
            slot, settled_within=timedelta(hours=1), retry_after=timedelta(minutes=5)
        ):
            print(style(f"anchor {due.label} was already handled", "grey"))
            return 0

    active_end = None
    if not args.force:
        blocks = current_blocks(cfg)
        active = usage.active_block(blocks, now)
        if active is not None:
            active_end = active.window.end

    result = anchor.fire(
        cfg,
        dry_run=args.dry_run,
        force=args.force,
        active_window_end=active_end,
        slot=slot,
    )

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return 0

    if args.dry_run:
        print(render.rule("would run", style))
        print("  " + " ".join(_quote(part) for part in result.argv))
        print(
            style(
                f"\n  cwd: {paths.anchor_workspace()}"
                f"\n  An empty directory, so no repository files or CLAUDE.md are loaded.",
                "grey",
            )
        )
        if active_end is not None:
            print(
                style(
                    f"\n  Note: a window is open until {active_end:%H:%M}; a real run would skip.",
                    "yellow",
                )
            )
        return 0

    if not result.fired:
        print(style(f"skipped: {result.skipped}", "yellow"))
        return 0

    ends = now + timedelta(hours=cfg.schedule.window_hours)
    detail = []
    if result.total_tokens is not None:
        detail.append(f"{result.total_tokens} tokens")
    if result.cost_usd is not None:
        detail.append(render.human_money(result.cost_usd))
    suffix = f"  ({', '.join(detail)})" if detail else ""
    print(
        style("window opened", "green", "bold")
        + f" - resets around {ends:%H:%M}{style(suffix, 'grey')}"
    )
    return 0


def _quote(part: str) -> str:
    if part == "":
        return '""'
    return part if all(c.isalnum() or c in "-_./=:@" for c in part) else f'"{part}"'


# ---------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------

DEFAULT_SLEEP = "00:00-06:00"


def _preferences(args, cfg: config.Config) -> tuple[suggest.Preferences, list[str]]:
    """Turn the flags into search preferences, and say what was assumed."""
    notes: list[str] = []
    idle = [suggest.parse_range(value) for value in (args.sleep or []) + (args.idle or [])]
    busy = [suggest.parse_range(value) for value in (args.busy or [])]
    if not idle and not busy:
        idle = [suggest.parse_range(DEFAULT_SLEEP)]
        notes.append(
            f"Assuming you are asleep {DEFAULT_SLEEP}. Pass --sleep, --idle and --busy to "
            f"describe your own day - the answer depends on it."
        )

    wake = suggest.parse_hour(args.wake) if args.wake else _infer_wake(idle)
    if args.wake is None and wake is not None:
        notes.append(f"Taking {wake:02d}:00 as the start of your day (--wake overrides it).")

    prefs = suggest.Preferences(
        window_hours=cfg.schedule.window_hours,
        count=args.count,
        start_at=[suggest.parse_hour(v) for v in (args.start_at or [])],
        free_at=[suggest.parse_hour(v) for v in (args.free_at or [])],
        idle=idle,
        busy=busy,
        wake=wake,
    )
    return prefs, notes


def _infer_wake(idle: list[tuple[int, int]]) -> int | None:
    """The end of the overnight idle range, if there is one."""
    if not idle:
        return None
    overnight = [span for span in idle if 3 in suggest.hours_in(span)]
    chosen = overnight[0] if overnight else max(idle, key=lambda s: len(suggest.hours_in(s)))
    return chosen[1]


def cmd_suggest(args) -> int:
    cfg = load_config(args, required=False)
    if args.timezone:
        cfg.schedule.timezone = args.timezone
    style = render.Style()
    prefs, notes = _preferences(args, cfg)
    results = suggest.search(prefs, limit=max(args.top, args.apply or 0))

    if args.json:
        print(
            json.dumps(
                {
                    "timezone": cfg.tz_label(),
                    "window_hours": prefs.window_hours,
                    "assumptions": notes,
                    "candidates": [c.as_dict() for c in results],
                },
                indent=2,
            )
        )
        return 0 if results else 1

    if not results:
        print(style("no schedule satisfies those constraints", "red", "bold"), file=sys.stderr)
        print(_impossible_hint(prefs, style), file=sys.stderr)
        return 1

    print(render.rule(f"suggested schedules  ({cfg.tz_label()})", style))
    print(
        style(
            "  Anchors are wall-clock times in this timezone. Set [schedule].timezone (or\n"
            "  pass --timezone) before you trust these - an hour out is a wasted window.",
            "grey",
        )
    )
    for note in notes:
        print(style(f"  {style.bullet} {note}", "grey"))
    print()

    for index, candidate in enumerate(results[: args.top], start=1):
        _print_candidate(index, candidate, prefs, style)

    best = results[0]
    joined = ",".join(f"{h:02d}:00" for h in best.anchors)
    if args.apply:
        chosen = results[args.apply - 1]
        _apply_anchors(args, cfg, chosen, style)
        return 0

    print(render.rule("apply one", style))
    command = " ".join(part for part in ("claudron suggest", _replay(args), "--apply 1") if part)
    print(f"  {command}")
    print(style(f"  or by hand:  anchors = {list(f'{h:02d}:00' for h in best.anchors)}", "grey"))
    print(style(f"  or fresh:    claudron init --anchors {joined}", "grey"))
    return 0


def _print_candidate(index, candidate, prefs, style: render.Style) -> None:
    times = "  ".join(style(f"{h:02d}:00", "bold") for h in candidate.anchors)
    tag = style("  ← best fit", "green", "bold") if index == 1 else ""
    print(f"  {style(str(index), 'cyan', 'bold')}  {times}{tag}")
    print(render.hour_bar(candidate.covered, set(candidate.anchors), style, indent="     "))
    ranges = ", ".join(f"{a:02d}:00-{b:02d}:00" for a, b in candidate.idle_ranges())
    costly = sum(1 for h in candidate.idle_hours if h in _busy_hours(prefs))
    detail = f"{len(candidate.idle_hours)}h idle"
    if prefs.busy:
        detail += f", {costly}h of it inside your working hours"
    print(f"     {style('stay quiet', 'yellow')}  {ranges}   {style(detail, 'grey')}")
    for line in suggest.explain(candidate, prefs):
        print(style(f"     {style.bullet} {line}", "grey"))
    print()


def _busy_hours(prefs) -> set[int]:
    hours: set[int] = set()
    for span in prefs.busy:
        hours |= suggest.hours_in(span)
    return hours


def _replay(args) -> str:
    """Rebuild the flags the user just typed, so --apply can be pasted onto it."""
    parts = []
    for flag, values in (
        ("--start-at", args.start_at),
        ("--free-at", args.free_at),
        ("--sleep", args.sleep),
        ("--idle", args.idle),
        ("--busy", args.busy),
    ):
        for value in values or []:
            parts.append(f"{flag} {value}")
    if args.wake:
        parts.append(f"--wake {args.wake}")
    if args.count:
        parts.append(f"--count {args.count}")
    return " ".join(parts)


def _apply_anchors(args, cfg: config.Config, candidate, style: render.Style) -> None:
    anchors = [f"{h:02d}:00" for h in candidate.anchors]
    target = args.config or paths.config_file()
    cfg.schedule.anchors = anchors
    config.validate(cfg)
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)
        print(style(f"backed up {target} to {backup}", "grey"))
        print(
            style(
                "  the file is regenerated from its values, so hand-written comments are "
                "replaced by the standard ones",
                "grey",
            )
        )
    config.save(cfg, target)
    print(style(f"anchors set to {', '.join(anchors)}", "green", "bold") + f"  in {target}")
    print(f"  next: {style('claudron plan', 'bold')} to see the day, then `claudron install`")


def _impossible_hint(prefs, style: render.Style) -> str:
    lines = []
    fixed = sorted(set(prefs.start_at))
    window = prefs.window_hours
    for earlier, later in zip(fixed, fixed[1:], strict=False):
        if later - earlier < window:
            lines.append(
                f"  {style('--start-at', 'bold')} {earlier:02d}:00 and {later:02d}:00 are only "
                f"{later - earlier}h apart, but a window lasts {window}h - the second one would "
                f"land inside the first."
            )
    if len(fixed) > prefs.max_anchors():
        lines.append(
            f"  you fixed {len(fixed)} anchors, but a day holds at most {prefs.max_anchors()}."
        )
    if not lines:
        lines.append(
            "  Try relaxing one constraint: drop a --start-at, widen --sleep, or narrow --busy."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# daemon / install
# ---------------------------------------------------------------------------


def cmd_daemon(args) -> int:
    cfg = load_config(args)
    return Daemon(cfg, once=args.once).run()


def cmd_install(args) -> int:
    cfg = load_config(args)
    style = render.Style()
    print(render.banner(style), end="")
    backend = args.backend or service.detect_backend()
    plan = service.build_plan(cfg, backend, args.mode)

    print(render.rule(f"{backend} / {args.mode} mode", style))
    for path, content in plan.files:
        print(f"  write {path}")
        if args.show:
            print("\n".join("      " + line for line in content.splitlines()))
    for command in plan.commands:
        print(f"  run   {' '.join(command)}")
    if args.dry_run:
        print(style("\n  dry run: nothing was written", "yellow"))
    else:
        service.apply(plan, dry_run=False)
        print(style("\ninstalled", "green", "bold"))
    for note in plan.notes:
        print(f"  {style.bullet} {note}")
    return 0


def cmd_uninstall(args) -> int:
    style = render.Style()
    backend = args.backend or service.detect_backend()
    plan = service.uninstall_plan(backend)
    for command in plan.commands:
        print(f"  run   {' '.join(command)}")
    if not args.dry_run:
        service.apply(plan, dry_run=False)
        print(style("\nremoved", "green", "bold"))
    for note in plan.notes:
        print(f"  {style.bullet} {note}")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args) -> int:
    style = render.Style()
    checks: list[tuple[str, str, str]] = []  # level, title, detail

    def add(level: str, title: str, detail: str = "") -> None:
        checks.append((level, title, detail))

    add(
        INFO,
        f"claudron {__version__}",
        f"python {platform.python_version()} on {platform.system()}",
    )

    try:
        cfg = load_config(args)
        add("ok", f"config {cfg.source}", f"timezone {cfg.tz_label()}")
    except ConfigError as exc:
        add(ERROR, "config", str(exc))
        _print_checks(checks, style)
        return 2

    issues = cycle_issues(cfg)
    errors = [i for i in issues if i.level == ERROR]
    if errors:
        for issue in errors:
            add(ERROR, "schedule", f"{issue.message}\n{issue.hint}")
    else:
        covered = len(cfg.schedule.anchors) * cfg.schedule.window_hours
        add("ok", "schedule", f"{len(cfg.schedule.anchors)} anchors, {covered}h/24h covered")
    for issue in issues:
        if issue.level == WARN:
            add(WARN, "schedule", f"{issue.message}\n{issue.hint}")

    executable = shutil.which(cfg.anchor.executable)
    if not executable:
        add(ERROR, "claude CLI", f"`{cfg.anchor.executable}` not found on PATH")
    else:
        try:
            flags = anchor.probe_flags(cfg, refresh=args.refresh)
            version = state.load().get("probe", {}).get("version", "")
            missing = [f for f in anchor.OPTIONAL_FLAGS if f not in flags]
            add("ok", f"claude CLI {version}".strip(), executable)
            if missing:
                add(
                    WARN,
                    "claude flags",
                    "this build does not support "
                    + ", ".join(missing)
                    + "\nclaudron simply omits them; the anchor still works.",
                )
        except ClaudronError as exc:
            add(ERROR, "claude CLI", str(exc))

    projects = cfg.projects_path()
    if not projects.exists():
        add(
            WARN,
            "transcripts",
            f"{projects} does not exist yet\n"
            f"It appears the first time Claude Code runs. Usage tracking stays empty "
            f"until then.",
        )
    else:
        stats = usage.ParseStats()
        week_ago = datetime.now().astimezone() - timedelta(days=7)
        events = usage.read_events(cfg, since=week_ago, stats=stats)
        blocks = usage.build_blocks(events, cfg)
        add(
            "ok" if events else WARN,
            "transcripts",
            f"{len(events)} events in {stats.files_seen} file(s) over 7 days "
            f"({stats.duration_s * 1000:.0f}ms, {stats.files_cached} cached)"
            + (f"\n{stats.malformed} line(s) could not be parsed" if stats.malformed else "")
            + (f"\n{len(blocks)} usage window(s) reconstructed" if blocks else ""),
        )

    backend = service.detect_backend()
    installed = _service_installed(backend)
    add(
        "ok" if installed else WARN,
        f"scheduler ({backend})",
        "installed and enabled" if installed else "not installed - run `claudron install`",
    )

    for directory in (paths.config_dir(), paths.state_dir()):
        if directory.exists():
            mode = oct(directory.stat().st_mode & 0o777)
            add(
                "ok" if mode == "0o700" else WARN,
                f"permissions {directory}",
                f"mode {mode}" + ("" if mode == "0o700" else " (expected 0o700)"),
            )

    add(
        INFO,
        "unverified until first run",
        f"the model alias {cfg.anchor.model or '(default)'!r} and your authentication are only\n"
        f"proven by an actual send. Run `claudron fire --force` once, at a moment when\n"
        f"opening a window is harmless.",
    )

    _print_checks(checks, style)
    return 2 if any(level == ERROR for level, _, _ in checks) else 0


def _service_installed(backend: str) -> bool:
    if backend == "systemd":
        directory = service.systemd_dir()
        return (directory / "claudron.timer").exists() or (directory / "claudron.service").exists()
    if backend == "launchd":
        return service.launchd_path().exists()
    return False


def _print_checks(checks, style: render.Style) -> None:
    marks = {
        "ok": (style("ok", "green"), 2),
        WARN: (style("warn", "yellow"), 4),
        ERROR: (style("fail", "red", "bold"), 4),
        INFO: (style("note", "grey"), 4),
    }
    for level, title, detail in checks:
        mark, width = marks.get(level, (level, len(level)))
        print(f"  [{mark}] {style(title, 'bold')}")
        for line in detail.splitlines():
            if line.strip():
                print(f"         {style(line, 'grey')}")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def cmd_config(args) -> int:
    style = render.Style()
    if args.action == "path":
        print(paths.config_file())
        return 0
    if args.action == "presets":
        for name, (description, anchors) in config.PRESETS.items():
            marker = style(" (default)", "grey") if name == config.DEFAULT_PRESET else ""
            print(f"  {style(name, 'bold')}{marker}")
            print(f"    anchors  {', '.join(anchors)}")
            print(f"    {style(description, 'grey')}")
        return 0
    cfg = load_config(args)
    print(config.render(cfg) if args.action == "show" else cfg.source)
    return 0


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


def cmd_completion(args) -> int:
    print(completion.script(args.shell, build_parser()), end="")
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

EPILOG = """\
how it works
  A Claude usage window opens on your first message after the previous window
  expired, and lasts 5 hours. claudron sends that first message at times you
  choose, so your resets land where your working day needs them - and tells you
  when to stay quiet so an accidental message does not open a window early.

typical first run
  claudron suggest --start-at 12:00 --sleep 23:00-05:00
  claudron init --preset balanced
  claudron plan
  claudron doctor
  claudron install
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claudron",
        description="Align Claude usage windows with your working day.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"claudron {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "-c", "--config", type=_path, metavar="FILE", help="use an alternate config file"
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("init", help="create a config file")
    p.add_argument(
        "--preset",
        choices=sorted(config.PRESETS),
        help="start from a named schedule (see `claudron config presets`)",
    )
    p.add_argument("--anchors", metavar="HH:MM,...", help="explicit anchor times")
    p.add_argument("--timezone", metavar="TZ", help="IANA timezone, or 'local'")
    p.add_argument("--model", metavar="ALIAS", help="model used for anchor messages")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init, log_file=False)

    p = sub.add_parser("status", help="what is open right now, and what is next")
    p.add_argument("--short", action="store_true", help="one line, for a status bar")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status, log_file=False)

    p = sub.add_parser("plan", help="simulate a day and show the idle gaps")
    p.add_argument("--date", metavar="YYYY-MM-DD")
    p.add_argument(
        "--live",
        action="store_true",
        help="seed the simulation with the window that is actually open",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan, log_file=False)

    p = sub.add_parser("suggest", help="propose anchor times that fit your day")
    p.add_argument(
        "--start-at",
        action="append",
        metavar="HH:MM",
        help="a fresh window must start here (repeatable)",
    )
    p.add_argument(
        "--free-at",
        action="append",
        metavar="HH:MM",
        help="a full budget must be available here - an anchor, or idle time (repeatable)",
    )
    p.add_argument(
        "--sleep", action="append", metavar="A-B", help="hours you are asleep (repeatable)"
    )
    p.add_argument(
        "--idle",
        action="append",
        metavar="A-B",
        help="hours you are away - lunch, a standing meeting (repeatable)",
    )
    p.add_argument(
        "--busy",
        action="append",
        metavar="A-B",
        help="hours you really work; idle time here is penalised (repeatable)",
    )
    p.add_argument("--wake", metavar="HH:MM", help="the hour your day starts")
    p.add_argument("--count", type=int, metavar="N", help="how many anchors (default: the maximum)")
    p.add_argument("--top", type=int, default=3, metavar="N", help="how many options to show")
    p.add_argument("--apply", type=int, metavar="N", help="write option N into the config file")
    p.add_argument("--timezone", metavar="TZ", help="IANA timezone, or 'local'")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_suggest, log_file=False)

    p = sub.add_parser("usage", help="real token usage, read from local transcripts")
    p.add_argument("--days", type=int, metavar="N")
    p.add_argument("--limit", type=int, default=20, metavar="N", help="windows to list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_usage, log_file=False)

    p = sub.add_parser("fire", help="send an anchor message now")
    p.add_argument("--dry-run", action="store_true", help="print the command, send nothing")
    p.add_argument(
        "--scheduled",
        action="store_true",
        help="only fire if an anchor is due (used by the timer/cron unit)",
    )
    p.add_argument("--force", action="store_true", help="send even if a window is already open")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fire, log_file=True)

    p = sub.add_parser("daemon", help="run the supervisor in the foreground")
    p.add_argument("--once", action="store_true", help="run a single tick and exit")
    p.set_defaults(func=cmd_daemon, log_file=True)

    p = sub.add_parser("install", help="install a systemd/launchd/cron schedule")
    p.add_argument("--mode", choices=("timer", "daemon"), default="timer")
    p.add_argument("--backend", choices=("systemd", "launchd", "cron"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show", action="store_true", help="print the generated unit files")
    p.set_defaults(func=cmd_install, log_file=False)

    p = sub.add_parser("uninstall", help="remove the installed schedule")
    p.add_argument("--backend", choices=("systemd", "launchd", "cron"))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_uninstall, log_file=False)

    p = sub.add_parser("doctor", help="check the environment and the schedule")
    p.add_argument("--refresh", action="store_true", help="re-probe the claude CLI")
    p.set_defaults(func=cmd_doctor, log_file=False)

    p = sub.add_parser("completion", help="print a shell completion script")
    p.add_argument("shell", choices=completion.SHELLS)
    p.set_defaults(func=cmd_completion, log_file=False)

    p = sub.add_parser("config", help="inspect configuration")
    p.add_argument("action", choices=("path", "show", "presets"))
    p.set_defaults(func=cmd_config, log_file=False)

    return parser


def _path(value: str):
    from pathlib import Path

    return Path(value).expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose, to_file=getattr(args, "log_file", False))
    try:
        return args.func(args)
    except ClaudronError as exc:
        style = render.Style(color=render.supports_color(sys.stderr))
        print(style("error: ", "red", "bold") + str(exc), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # `claudron usage | head`
        os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
