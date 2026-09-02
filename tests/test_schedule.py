"""The scheduling arithmetic - the part that must never be wrong."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from claudron.config import PRESETS, Config
from claudron.schedule import (
    BLOCKED,
    ERROR,
    OPENS,
    WARN,
    anchor_datetimes,
    cycle_issues,
    next_due_anchor,
    plan_day,
    simulate,
)

TZ = "Asia/Ho_Chi_Minh"


def make(anchors: list[str], **kwargs) -> Config:
    cfg = Config()
    cfg.schedule.timezone = TZ
    cfg.schedule.anchors = anchors
    for key, value in kwargs.items():
        section, _, name = key.partition("__")
        setattr(getattr(cfg, section), name, value)
    return cfg


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


class CycleValidation(unittest.TestCase):
    def test_balanced_preset_is_clean(self):
        cfg = make(list(PRESETS["balanced"][1]))
        issues = cycle_issues(cfg)
        self.assertNotIn("swallowed-anchor", codes(issues))
        self.assertNotIn("too-many-anchors", codes(issues))
        coverage = next(i for i in issues if i.code == "coverage")
        self.assertIn("20h", coverage.message)
        self.assertIn("4h idle", coverage.message)

    def test_reset_time_listed_as_anchor_is_reported(self):
        # The classic mistake: 10:00 is when the 05:00 window *closes*, so
        # listing it as an anchor swallows the 12:00 one.
        cfg = make(["05:00", "10:00", "12:00", "17:00", "22:00"])
        issues = cycle_issues(cfg)
        swallowed = [i for i in issues if i.code == "swallowed-anchor"]
        self.assertEqual(len(swallowed), 1)
        self.assertIn("12:00", swallowed[0].message)
        self.assertIn("10:00", swallowed[0].message)
        self.assertIn("15:00", swallowed[0].hint)
        self.assertEqual(swallowed[0].level, ERROR)

    def test_more_than_four_anchors_is_impossible(self):
        cfg = make(["00:00", "05:00", "10:00", "15:00", "20:00"])
        issues = cycle_issues(cfg)
        self.assertIn("too-many-anchors", codes(issues))

    def test_exactly_four_spaced_anchors_fit(self):
        cfg = make(["00:00", "06:00", "12:00", "18:00"])
        self.assertNotIn("too-many-anchors", codes(cycle_issues(cfg)))

    def test_wrap_around_gap_is_checked(self):
        # 22:00 -> 02:00 is only 4h, so the 02:00 anchor is swallowed.
        cfg = make(["02:00", "08:00", "14:00", "22:00"])
        swallowed = [i for i in cycle_issues(cfg) if i.code == "swallowed-anchor"]
        self.assertEqual(len(swallowed), 1)
        self.assertIn("02:00", swallowed[0].message)

    def test_off_hour_anchor_warns_about_lost_minutes(self):
        cfg = make(["05:30", "12:00", "17:00", "22:00"])
        warnings = [i for i in cycle_issues(cfg) if i.code == "off-hour-anchor"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].level, WARN)
        self.assertIn("30m", warnings[0].message)

    def test_off_hour_anchor_is_fine_without_flooring(self):
        cfg = make(["05:30", "12:00", "17:00", "22:00"], usage__floor_window_to_hour=False)
        self.assertNotIn("off-hour-anchor", codes(cycle_issues(cfg)))

    def test_window_length_is_configurable(self):
        cfg = make(["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"], schedule__window_hours=4)
        self.assertNotIn("too-many-anchors", codes(cycle_issues(cfg)))
        self.assertNotIn("swallowed-anchor", codes(cycle_issues(cfg)))


class Simulation(unittest.TestCase):
    def test_anchor_inside_an_open_window_opens_nothing(self):
        tz = ZoneInfo(TZ)
        anchors = anchor_datetimes(
            [datetime(2026, 1, 1, h).time() for h in (5, 10, 12)],
            ["05:00", "10:00", "12:00"],
            date(2026, 1, 1),
            tz,
        )
        sim = simulate(anchors, timedelta(hours=5))
        statuses = {plan.label: plan.status for plan in sim.anchors}
        self.assertEqual(statuses["05:00"], OPENS)
        self.assertEqual(statuses["10:00"], OPENS)
        self.assertEqual(statuses["12:00"], BLOCKED)
        self.assertEqual(len(sim.windows), 2)

    def test_gaps_are_the_intervals_with_no_window(self):
        cfg = make(list(PRESETS["balanced"][1]))
        plan = plan_day(cfg, date(2026, 1, 7))
        gaps = {(g.start.strftime("%H:%M"), g.end.strftime("%H:%M")) for g in plan.gaps}
        self.assertEqual(gaps, {("03:00", "05:00"), ("10:00", "12:00")})
        self.assertEqual(sum(g.length.total_seconds() for g in plan.gaps), 4 * 3600)

    def test_previous_day_window_spills_into_this_one(self):
        cfg = make(list(PRESETS["balanced"][1]))
        plan = plan_day(cfg, date(2026, 1, 7))
        # The 22:00 window from the day before covers 00:00-03:00.
        self.assertTrue(any(w.end.strftime("%H:%M") == "03:00" for w in plan.windows))

    def test_seed_window_blocks_the_anchors_it_covers(self):
        cfg = make(list(PRESETS["balanced"][1]))
        tz = ZoneInfo(TZ)
        from claudron.schedule import Window

        seed = Window(
            start=datetime(2026, 1, 7, 11, 0, tzinfo=tz),
            end=datetime(2026, 1, 7, 16, 0, tzinfo=tz),
            observed=True,
        )
        plan = plan_day(cfg, date(2026, 1, 7), seed=seed)
        statuses = {p.label: p.status for p in plan.anchors}
        self.assertEqual(statuses["12:00"], BLOCKED)
        self.assertEqual(statuses["17:00"], OPENS)

    def test_seed_truncates_the_window_it_interrupts_everywhere(self):
        # Reality: nothing was sent at 05:00, and the first message landed at
        # 09:00. The hypothetical 05:00 window cannot still be shown running to
        # 10:00 - the timeline and the anchor table must not disagree.
        cfg = make(list(PRESETS["balanced"][1]))
        tz = ZoneInfo(TZ)
        from claudron.schedule import Window

        seed = Window(
            start=datetime(2026, 1, 7, 9, 0, tzinfo=tz),
            end=datetime(2026, 1, 7, 14, 0, tzinfo=tz),
            observed=True,
        )
        plan = plan_day(cfg, date(2026, 1, 7), seed=seed)
        opener = next(p for p in plan.anchors if p.label == "05:00")
        self.assertEqual(opener.window.end.strftime("%H:%M"), "09:00")
        self.assertIn(opener.window, plan.windows)
        # No two windows may overlap once reality has been spliced in.
        ordered = sorted(plan.windows, key=lambda w: w.start)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            self.assertLessEqual(earlier.end, later.start)


class CatchUp(unittest.TestCase):
    def setUp(self):
        self.cfg = make(list(PRESETS["balanced"][1]))
        self.cfg.schedule.jitter_seconds = 0
        self.tz = ZoneInfo(TZ)

    def at(self, hour, minute):
        return datetime(2026, 1, 7, hour, minute, tzinfo=self.tz)

    def test_fires_on_time(self):
        due = next_due_anchor(self.cfg, self.at(12, 0))
        self.assertIsNotNone(due)
        self.assertEqual(due.label, "12:00")

    def test_fires_while_still_inside_the_grace_period(self):
        self.assertIsNotNone(next_due_anchor(self.cfg, self.at(12, 44)))

    def test_skips_once_too_late(self):
        # Beyond the grace period the window would open in the wrong clock hour.
        self.assertIsNone(next_due_anchor(self.cfg, self.at(12, 46)))

    def test_nothing_due_between_anchors(self):
        self.assertIsNone(next_due_anchor(self.cfg, self.at(14, 30)))

    def test_grace_never_reaches_the_next_hour_by_default(self):
        self.assertLess(self.cfg.schedule.catch_up_minutes, 60)


if __name__ == "__main__":
    unittest.main()
