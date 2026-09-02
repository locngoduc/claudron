"""Presentation: degrade cleanly, and never decorate output nobody is reading."""

from __future__ import annotations

import re
import unittest
import unittest.mock

from claudron import artwork, render


class Fake:
    def __init__(self, tty: bool, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._tty


class Logo(unittest.TestCase):
    def test_unicode_and_ascii_variants_both_render(self):
        for unicode_ in (True, False):
            with self.subTest(unicode=unicode_):
                art = render.logo(render.Style(color=False, unicode_=unicode_))
                self.assertIn(render.TAGLINE, art)
                self.assertEqual(len(art.splitlines()), 5)

    def test_ascii_variant_is_pure_ascii(self):
        art = render.logo(render.Style(color=False, unicode_=False))
        art.encode("ascii")  # raises if not

    def test_colour_is_only_added_when_asked(self):
        plain = render.logo(render.Style(color=False, unicode_=True))
        self.assertNotIn("\033", plain)
        self.assertIn("\033", render.logo(render.Style(color=True, unicode_=True)))

    def test_banner_appears_on_a_terminal(self):
        style = render.Style(color=False, unicode_=True)
        self.assertIn(render.TAGLINE, render.banner(style, stream=Fake(tty=True)))

    def test_banner_is_empty_when_output_is_piped(self):
        style = render.Style(color=False, unicode_=True)
        self.assertEqual(render.banner(style, stream=Fake(tty=False)), "")


class ClockMark(unittest.TestCase):
    """The pixel clock: shown only when the terminal can actually display it."""

    truecolor = render.Style(color=True, unicode_=True, truecolor=True)

    def test_pixel_clock_is_used_when_the_terminal_can_show_it(self):
        art = render.logo(self.truecolor, columns=100)
        self.assertIn("▀", art)
        self.assertIn("\033[38;2;", art)
        self.assertEqual(len(art.splitlines()), artwork.HEIGHT // 2)

    def test_line_art_is_used_when_the_terminal_is_too_narrow(self):
        art = render.logo(self.truecolor, columns=40)
        self.assertNotIn("\033[38;2;", art)
        self.assertEqual(len(art.splitlines()), 5)

    def test_line_art_is_used_without_truecolor(self):
        art = render.logo(render.Style(color=True, unicode_=True, truecolor=False), columns=100)
        self.assertNotIn("\033[38;2;", art)

    def test_every_row_covers_the_full_width(self):
        for row in render.clock_mark(self.truecolor):
            visible = re.sub(r"\033\[[0-9;]*m", "", row)
            self.assertEqual(len(visible), artwork.WIDTH)

    def test_colour_is_always_reset_at_the_end_of_a_row(self):
        # A row that leaves a background colour set would bleed into the
        # wordmark beside it, and into the shell prompt afterwards.
        for row in render.clock_mark(self.truecolor):
            if "\033[" in row:
                # Trailing transparent pixels are plain spaces after the reset.
                self.assertTrue(row.rstrip().endswith("\033[0m"), repr(row))

    def test_transparent_pixels_leave_the_terminal_background_alone(self):
        rows = render.clock_mark(self.truecolor)
        for row, line in zip(rows, artwork.CLOCK[::2], strict=True):
            if line.startswith(artwork.TRANSPARENT):
                self.assertTrue(row.startswith(" ") or row.startswith("\033[38"), row)

    def test_every_palette_key_in_the_artwork_is_defined(self):
        used = {ch for line in artwork.CLOCK for ch in line} - {artwork.TRANSPARENT}
        self.assertTrue(used <= set(artwork.PALETTE), used - set(artwork.PALETTE))

    def test_artwork_is_rectangular_and_an_even_number_of_rows(self):
        self.assertEqual(len({len(line) for line in artwork.CLOCK}), 1)
        self.assertEqual(artwork.HEIGHT % 2, 0)

    def test_truecolor_needs_an_explicit_signal(self):
        for env, expected in (
            ({"COLORTERM": "truecolor"}, True),
            ({"COLORTERM": "24bit"}, True),
            ({"TERM": "xterm-256color"}, False),  # claims a lot, promises nothing
            ({}, False),
        ):
            with (
                self.subTest(env=env),
                unittest.mock.patch.dict("os.environ", env, clear=True),
                unittest.mock.patch.object(render, "supports_color", lambda: True),
            ):
                self.assertEqual(render.supports_truecolor(), expected)


class Capabilities(unittest.TestCase):
    def test_ascii_only_terminals_are_detected(self):
        self.assertFalse(render.supports_unicode(Fake(tty=True, encoding="ascii")))
        self.assertTrue(render.supports_unicode(Fake(tty=True, encoding="utf-8")))

    def test_no_color_is_respected(self):
        with unittest.mock.patch.dict("os.environ", {"NO_COLOR": "1"}):
            self.assertFalse(render.supports_color(Fake(tty=True)))

    def test_dumb_terminals_get_no_colour(self):
        with unittest.mock.patch.dict("os.environ", {"TERM": "dumb"}, clear=True):
            self.assertFalse(render.supports_color(Fake(tty=True)))


class HourBar(unittest.TestCase):
    def test_bar_marks_covered_hours_and_anchors(self):
        style = render.Style(color=False, unicode_=True)
        covered = set(range(5, 10))
        bar = render.hour_bar(covered, {5}, style)
        lines = bar.splitlines()
        self.assertEqual(len(lines), 3)
        strip = lines[1].strip()
        self.assertEqual(strip.count(style.full), len(covered) * render.CELLS_PER_HOUR)
        self.assertEqual(lines[2].strip(), style.anchor)


class Formatting(unittest.TestCase):
    def test_durations_read_naturally(self):
        from datetime import timedelta

        self.assertEqual(render.human_delta(timedelta(hours=2, minutes=13)), "2h13m")
        self.assertEqual(render.human_delta(timedelta(hours=3)), "3h")
        self.assertEqual(render.human_delta(timedelta(minutes=45)), "45m")

    def test_token_counts_are_abbreviated(self):
        self.assertEqual(render.human_tokens(999), "999")
        self.assertEqual(render.human_tokens(1500), "1.5k")
        self.assertEqual(render.human_tokens(2_340_000), "2.34M")

    def test_small_amounts_keep_their_precision(self):
        self.assertEqual(render.human_money(0.0031), "$0.0031")
        self.assertEqual(render.human_money(None), "-")


if __name__ == "__main__":
    unittest.main()
