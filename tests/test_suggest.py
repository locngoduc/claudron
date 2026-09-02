"""The schedule solver: every answer it gives must be a legal schedule."""

from __future__ import annotations

import unittest

from claudron import suggest
from claudron.errors import ClaudronError


def prefs(**kwargs) -> suggest.Preferences:
    kwargs.setdefault("window_hours", 5)
    return suggest.Preferences(**kwargs)


def rng(*values: str) -> list[tuple[int, int]]:
    return [suggest.parse_range(v) for v in values]


class Parsing(unittest.TestCase):
    def test_hours_accept_several_spellings(self):
        self.assertEqual(suggest.parse_hour("7"), 7)
        self.assertEqual(suggest.parse_hour("07:00"), 7)
        self.assertEqual(suggest.parse_hour("07:45"), 7)  # minutes are dropped

    def test_bad_hours_are_rejected(self):
        for value in ("noon", "25", "24:00", ""):
            with self.subTest(value=value), self.assertRaises(ClaudronError):
                suggest.parse_hour(value)

    def test_ranges_wrap_past_midnight(self):
        self.assertEqual(suggest.parse_range("23:00-06:00"), (23, 6))
        self.assertEqual(suggest.hours_in((23, 6)), {23, 0, 1, 2, 3, 4, 5})

    def test_ranges_accept_an_en_dash(self):
        self.assertEqual(suggest.parse_range("23–06"), (23, 6))

    def test_degenerate_range_is_rejected(self):
        with self.assertRaises(ClaudronError):
            suggest.parse_range("06:00-06:00")

    def test_contiguous_ranges_collapse_across_midnight(self):
        self.assertEqual(suggest._ranges({22, 23, 0, 1}), [(22, 2)])
        self.assertEqual(sorted(suggest._ranges({3, 4, 10, 11})), [(3, 5), (10, 12)])


class Legality(unittest.TestCase):
    """Whatever the preferences, the solver may only return valid schedules."""

    def assertLegal(self, candidate, window=5, count=4):
        anchors = list(candidate.anchors)
        self.assertEqual(len(anchors), count)
        self.assertEqual(anchors, sorted(set(anchors)))
        gaps = [(anchors[(i + 1) % count] - anchors[i]) % 24 or 24 for i in range(count)]
        for gap in gaps:
            self.assertGreaterEqual(gap, window, f"{anchors} has a {gap}h gap")
        self.assertEqual(sum(gaps), 24)
        self.assertEqual(len(candidate.covered), count * window)
        self.assertEqual(len(candidate.idle_hours), 24 - count * window)

    def test_every_result_is_legal_across_many_inputs(self):
        cases = [
            prefs(),
            prefs(start_at=[12]),
            prefs(start_at=[12, 17]),
            prefs(free_at=[12], idle=rng("23-06")),
            prefs(idle=rng("23-05"), busy=rng("08-18")),
            prefs(idle=rng("12-13", "00-06")),
            prefs(start_at=[0]),
        ]
        for preference in cases:
            with self.subTest(prefs=preference):
                results = suggest.search(preference, limit=5)
                self.assertTrue(results)
                for candidate in results:
                    self.assertLegal(candidate)

    def test_results_are_deterministic(self):
        preference = prefs(start_at=[12], idle=rng("23-05"))
        first = [c.anchors for c in suggest.search(preference, limit=5)]
        second = [c.anchors for c in suggest.search(preference, limit=5)]
        self.assertEqual(first, second)

    def test_windows_never_overlap(self):
        for candidate in suggest.search(prefs(start_at=[12]), limit=5):
            hours = []
            for start, _ in candidate.windows():
                hours.extend((start + offset) % 24 for offset in range(candidate.window_hours))
            self.assertEqual(len(hours), len(set(hours)))


class Constraints(unittest.TestCase):
    def test_start_at_always_appears(self):
        for candidate in suggest.search(prefs(start_at=[12, 17]), limit=5):
            self.assertIn(12, candidate.anchors)
            self.assertIn(17, candidate.anchors)

    def test_free_at_is_an_anchor_or_idle(self):
        for candidate in suggest.search(prefs(free_at=[9]), limit=5):
            self.assertTrue(9 in candidate.anchors or 9 in candidate.idle_hours)

    def test_conflicting_start_times_have_no_solution(self):
        self.assertEqual(suggest.search(prefs(start_at=[12, 15])), [])

    def test_too_many_fixed_anchors_has_no_solution(self):
        self.assertEqual(suggest.search(prefs(start_at=[0, 5, 10, 15, 20])), [])

    def test_asking_for_too_many_anchors_explains_the_limit(self):
        with self.assertRaises(ClaudronError) as ctx:
            suggest.search(prefs(count=5))
        self.assertIn("24h", str(ctx.exception))
        self.assertIn("most", str(ctx.exception))

    def test_zero_anchors_is_rejected(self):
        with self.assertRaises(ClaudronError):
            suggest.search(prefs(count=0))

    def test_a_shorter_window_allows_more_anchors(self):
        results = suggest.search(prefs(window_hours=4), limit=1)
        self.assertEqual(len(results[0].anchors), 6)
        self.assertEqual(results[0].idle_hours, set())


class Ranking(unittest.TestCase):
    def test_idle_time_lands_in_the_hours_you_said_were_free(self):
        best = suggest.search(prefs(idle=rng("01-06")), limit=1)[0]
        self.assertTrue(best.idle_hours <= suggest.hours_in((1, 6)))

    def test_idle_time_avoids_the_hours_you_said_you_work(self):
        best = suggest.search(prefs(idle=rng("23-05"), busy=rng("08-18")), limit=1)[0]
        self.assertFalse(best.idle_hours & suggest.hours_in((8, 18)))

    def test_lunch_break_is_used_as_idle_time(self):
        best = suggest.search(prefs(idle=rng("00-03", "12-13"), busy=rng("06-23")), limit=1)[0]
        self.assertIn(12, best.idle_hours)

    def test_the_users_own_schedule_is_reachable(self):
        # 05:00 / 12:00 / 17:00 / 22:00 with the mornings free and work at night.
        best = suggest.search(
            prefs(start_at=[12, 17], idle=rng("03-05", "10-12"), wake=5), limit=1
        )[0]
        self.assertEqual(best.anchors, (5, 12, 17, 22))

    def test_a_window_opening_at_wake_up_is_preferred_all_else_equal(self):
        best = suggest.search(prefs(idle=rng("01-05"), wake=5), limit=1)[0]
        self.assertIn(5, best.anchors)

    def test_being_forced_to_stay_quiet_after_waking_is_penalised(self):
        # Waking into an idle gap means an awake hour spent unable to work; the
        # solver must not prefer that over inheriting a part-used window.
        quiet_at_wake = suggest._score(
            (7, 12, 17, 22),
            prefs(idle=rng("23-05"), wake=5),
            suggest._hour_costs(prefs(idle=rng("23-05"), wake=5)),
        )
        inherits = suggest._score(
            (2, 7, 12, 17),
            prefs(idle=rng("23-05"), wake=5),
            suggest._hour_costs(prefs(idle=rng("23-05"), wake=5)),
        )
        self.assertLess(inherits.cost, quiet_at_wake.cost)


class Explanations(unittest.TestCase):
    def test_names_what_happens_at_each_requested_time(self):
        preference = prefs(start_at=[12], idle=rng("23-05"), wake=5)
        best = suggest.search(preference, limit=1)[0]
        notes = " ".join(suggest.explain(best, preference))
        self.assertIn("12:00 opens a fresh 5h window", notes)
        self.assertIn("05:00", notes)


if __name__ == "__main__":
    unittest.main()
