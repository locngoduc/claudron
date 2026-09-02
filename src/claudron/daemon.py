"""The supervisor loop.

It wakes often and does very little: check the clock, check the transcripts,
fire an anchor if one is due and no window is open. Waking every 20 seconds
(rather than sleeping until the next anchor) is deliberate - laptops suspend,
and a process that slept through a whole window would never notice.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import threading
from datetime import datetime, timedelta

from claudron import anchor, notify, usage
from claudron.config import Config
from claudron.errors import ClaudronError
from claudron.schedule import Gap, next_due_anchor, plan_day

log = logging.getLogger("claudron.daemon")

TICK_SECONDS = 20
#: Once an anchor has fired or been deliberately skipped, leave it alone.
SETTLED_FOR = timedelta(hours=1)
#: A failed attempt is retried after this long, while the catch-up window lasts.
RETRY_AFTER = timedelta(minutes=5)


class Daemon:
    def __init__(self, cfg: Config, *, once: bool = False) -> None:
        self.cfg = cfg
        self.once = once
        self.stop = threading.Event()
        self._warned_blocks: set[str] = set()

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError, OSError):  # not on the main thread
                signal.signal(sig, self._handle_signal)

    def _handle_signal(self, signum, _frame) -> None:
        log.info("received signal %s, shutting down", signum)
        self.stop.set()

    # -- main loop ---------------------------------------------------------

    def run(self) -> int:
        self.install_signal_handlers()
        tz = self.cfg.tz()
        log.info(
            "claudron daemon started; timezone=%s anchors=%s window=%sh",
            self.cfg.tz_label(),
            ",".join(self.cfg.schedule.anchors),
            self.cfg.schedule.window_hours,
        )
        while not self.stop.is_set():
            try:
                self.tick(datetime.now(tz))
            except ClaudronError as exc:
                log.error("%s", exc)
            except Exception:  # keep the loop alive; a bad tick is not fatal
                log.exception("unexpected error during tick")
            if self.once:
                break
            self.stop.wait(TICK_SECONDS)
        log.info("claudron daemon stopped")
        return 0

    def tick(self, now: datetime) -> None:
        blocks = current_blocks(self.cfg)
        active = usage.active_block(blocks, now)

        self._guard(now, blocks)

        due = next_due_anchor(self.cfg, now)
        if due is None:
            return

        slot = anchor.slot_key(due.at, due.label)
        if anchor.handled_recently(slot, settled_within=SETTLED_FOR, retry_after=RETRY_AFTER):
            return

        if active is not None:
            log.info(
                "anchor %s skipped: a window opened at %s is still open until %s",
                due.label,
                active.window.start.strftime("%H:%M"),
                active.window.end.strftime("%H:%M"),
            )
            anchor.record_skip(slot, "window already open")
            return

        log.info("firing anchor %s", due.label)
        result = anchor.fire(self.cfg, active_window_end=None, slot=slot)
        detail = []
        if result.total_tokens is not None:
            detail.append(f"{result.total_tokens} tokens")
        if result.cost_usd is not None:
            detail.append(f"${result.cost_usd:.4f}")
        log.info(
            "anchor %s fired in %.1fs%s; window now runs to %s",
            due.label,
            result.duration_s,
            f" ({', '.join(detail)})" if detail else "",
            (now + timedelta(hours=self.cfg.schedule.window_hours)).strftime("%H:%M"),
        )
        notify.send(
            self.cfg,
            "claudron",
            f"Window opened at {due.label}; resets "
            f"{(now + timedelta(hours=self.cfg.schedule.window_hours)):%H:%M}",
        )

    # -- blackout guard ----------------------------------------------------

    def _guard(self, now: datetime, blocks: list[usage.Block]) -> None:
        if not (self.cfg.warnings.enabled and self.cfg.warnings.guard_blackouts):
            return
        if not blocks:
            return
        latest = blocks[-1]
        key = latest.window.start.isoformat()
        if key in self._warned_blocks or not latest.is_active(now):
            return
        gap = _gap_containing(self.cfg, latest.window.start)
        if gap is None:
            return
        self._warned_blocks.add(key)
        message = (
            f"a window opened at {latest.window.start:%H:%M}, inside the idle gap "
            f"{gap.start:%H:%M}-{gap.end:%H:%M}. It now runs to "
            f"{latest.window.end:%H:%M}, so the anchors before then cannot open a window."
        )
        log.warning("%s", message)
        notify.send(self.cfg, "claudron: schedule drifted", message)


def _gap_containing(cfg: Config, moment: datetime) -> Gap | None:
    plan = plan_day(cfg, moment.date())
    for gap in plan.gaps:
        if gap.start <= moment < gap.end:
            return gap
    return None


def current_blocks(cfg: Config) -> list[usage.Block]:
    """Blocks from the recent past only - enough to know the current window."""
    since = datetime.now().astimezone() - timedelta(days=2)
    events = usage.read_events(cfg, since=since)
    return usage.build_blocks(events, cfg)
