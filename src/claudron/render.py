"""Terminal presentation.

Colour is used only when the output is a TTY, ``NO_COLOR`` is unset and the
terminal is not dumb. Box-drawing characters degrade to ASCII when the output
encoding cannot represent them, so piping to a file or a minimal console never
produces mojibake.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import date, datetime, time, timedelta, tzinfo

from claudron import artwork
from claudron.schedule import BLOCKED, ERROR, INFO, WARN, Simulation

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
}


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("CLICOLOR_FORCE"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def supports_truecolor() -> bool:
    """24-bit colour, which the mascot needs to look like itself.

    Only the two standard signals are trusted. Guessing from TERM produces
    mojibake-coloured artwork on terminals that merely claim to be xterm.
    """
    if not supports_color():
        return False
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def supports_unicode(stream=None) -> bool:
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "█▲·│".encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


class Style:
    def __init__(
        self,
        color: bool | None = None,
        unicode_: bool | None = None,
        truecolor: bool | None = None,
    ) -> None:
        self.color = supports_color() if color is None else color
        self.unicode = supports_unicode() if unicode_ is None else unicode_
        self.truecolor = (self.color and supports_truecolor()) if truecolor is None else truecolor

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        prefix = "".join(_CODES.get(s, "") for s in styles)
        return f"{prefix}{text}{_CODES['reset']}"

    # glyphs -------------------------------------------------------------
    @property
    def full(self) -> str:
        return "█" if self.unicode else "#"

    @property
    def empty(self) -> str:
        return "·" if self.unicode else "."

    @property
    def anchor(self) -> str:
        return "▲" if self.unicode else "^"

    @property
    def now(self) -> str:
        return "│" if self.unicode else "|"

    @property
    def bullet(self) -> str:
        return "•" if self.unicode else "*"

    @property
    def arrow(self) -> str:
        return "→" if self.unicode else "->"


LEVEL_STYLE = {ERROR: ("red", "bold"), WARN: ("yellow",), INFO: ("grey",)}
LEVEL_TAG = {ERROR: "error", WARN: "warn", INFO: "note"}


def human_delta(delta: timedelta, *, short: bool = False) -> str:
    total = int(delta.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{sign}{hours}h{minutes:02d}m"
    if hours:
        return f"{sign}{hours}h"
    if minutes or short:
        return f"{sign}{minutes}m"
    return f"{sign}{total}s"


def human_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def human_money(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def rule(title: str, style: Style, width: int = 64) -> str:
    dash = "─" if style.unicode else "-"
    head = f"{dash}{dash} {title} "
    return style(head + dash * max(3, width - len(head)), "grey")


# ---------------------------------------------------------------------------
# day timeline
# ---------------------------------------------------------------------------

CELLS_PER_HOUR = 2
DAY_CELLS = 24 * CELLS_PER_HOUR


def _cell(moment: datetime, day_start: datetime) -> int:
    offset = (moment - day_start).total_seconds() / 3600.0
    return int(offset * CELLS_PER_HOUR)


def timeline(sim: Simulation, day: date, tz: tzinfo, style: Style, now: datetime | None) -> str:
    """A 24-hour strip: filled where a window is open, dotted where it is not."""
    day_start = datetime.combine(day, time(0, 0), tzinfo=tz)
    cells = [style.empty] * DAY_CELLS

    for window in sim.windows:
        begin = max(_cell(window.start, day_start), 0)
        finish = min(_cell(window.end, day_start), DAY_CELLS)
        for index in range(begin, finish):
            cells[index] = style.full

    bar = "".join(cells)
    if style.color:
        bar = "".join(
            style(char, "green") if char == style.full else style(char, "grey") for char in cells
        )

    markers = [" "] * DAY_CELLS
    for plan in sim.anchors:
        index = _cell(plan.at, day_start)
        if 0 <= index < DAY_CELLS:
            markers[index] = style.anchor if plan.status != BLOCKED else "x"
    marker_line = "".join(
        style(char, "red", "bold") if char == "x" else style(char, "cyan") for char in markers
    )

    now_line = ""
    if now is not None and day_start <= now < day_start + timedelta(days=1):
        index = _cell(now, day_start)
        now_line = " " * index + style(style.now + " now", "magenta", "bold")

    ruler_ticks = [
        ("|" if (h % 3 == 0 and c == 0) else " ") for h in range(24) for c in range(CELLS_PER_HOUR)
    ]
    ruler = "".join(ruler_ticks)
    labels = [" "] * DAY_CELLS
    for hour in range(0, 24, 3):
        text = f"{hour:02d}"
        start = hour * CELLS_PER_HOUR
        for offset, char in enumerate(text):
            if start + offset < DAY_CELLS:
                labels[start + offset] = char

    pad = "  "
    lines = [
        pad + style("".join(labels), "grey"),
        pad + style(ruler, "grey"),
        pad + bar,
        pad + marker_line,
    ]
    if now_line:
        lines.append(pad + now_line)
    return "\n".join(line.rstrip() for line in lines if line.strip())


def issue_lines(issues, style: Style, *, limit: int | None = None) -> list[str]:
    out: list[str] = []
    ordered = sorted(issues, key=lambda i: {ERROR: 0, WARN: 1, INFO: 2}.get(i.level, 3))
    if limit is not None:
        ordered = ordered[:limit]
    for issue in ordered:
        label = LEVEL_TAG.get(issue.level, issue.level)
        tag = style(f"{label:>5}", *LEVEL_STYLE.get(issue.level, ()))
        out.append(f"  {tag}  {issue.message}")
        if issue.hint:
            out.append(f"         {style(issue.hint, 'grey')}")
    return out


def table(rows: list[list[str]], headers: list[str], style: Style) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    head = "  ".join(style(h.ljust(widths[i]), "bold") for i, h in enumerate(headers))
    body = ["  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows]
    return "\n".join([head, *body])


# ---------------------------------------------------------------------------
# wordmark
# ---------------------------------------------------------------------------

_LOGO_UNICODE = """
     ╭───────╮
    ╱    │    ╲     ┌─┐┬  ┌─┐┬ ┬┌┬┐┬─┐┌─┐┌┐┌
   │     ●     │    │  │  ├─┤│ │ ││├┬┘│ ││││
    ╲     ╲   ╱     └─┘┴─┘┴ ┴└─┘─┴┘┴└─└─┘┘└┘
     ╰───────╯      {tagline}
"""

_LOGO_ASCII = """
      .-----.
     /   |   \\
    |    o    |     C L A U D R O N
     \\    \\  /
      '-----'       {tagline}
"""

TAGLINE = "your Claude resets, on your clock"

#: Column where the clock face ends and the wordmark begins.
_LOGO_SPLIT = 18

#: The wordmark on its own, drawn once and reused by both logo variants.
_WORDMARK = (
    "┌─┐┬  ┌─┐┬ ┬┌┬┐┬─┐┌─┐┌┐┌",
    "│  │  ├─┤│ │ ││├┬┘│ ││││",
    "└─┘┴─┘┴ ┴└─┘─┴┘┴└─└─┘┘└┘",
)


def _truecolor(rgb: tuple[int, int, int], *, background: bool = False) -> str:
    red, green, blue = rgb
    return f"\033[{48 if background else 38};2;{red};{green};{blue}m"


def clock_mark(style: Style) -> list[str]:
    """The clock from the project logo, drawn with half-block characters.

    Each character carries two stacked pixels - the upper half as the
    foreground colour, the lower half as the background - which keeps the
    pixels square in a terminal cell that is twice as tall as it is wide.
    Transparent pixels leave the colour unset, so the artwork sits on whatever
    background the user actually has.

    Colour codes are emitted only when the colour changes. Repeating them per
    cell would work, but it turns an eight-line banner into eight kilobytes of
    escape sequences.
    """
    del style  # the caller has already decided this terminal can show it
    rows = []
    grid = artwork.CLOCK
    for y in range(0, len(grid) - 1, 2):
        upper, lower = grid[y], grid[y + 1]
        line: list[str] = []
        foreground = background = None
        for top, bottom in zip(upper, lower, strict=True):
            solid_top = top != artwork.TRANSPARENT
            solid_bottom = bottom != artwork.TRANSPARENT
            if not solid_top and not solid_bottom:
                if foreground is not None or background is not None:
                    line.append(_CODES["reset"])
                    foreground = background = None
                line.append(" ")
                continue
            if solid_top and solid_bottom:
                want_fg, want_bg, glyph = top, bottom, "▀"
            elif solid_top:
                want_fg, want_bg, glyph = top, None, "▀"
            else:
                want_fg, want_bg, glyph = bottom, None, "▄"
            if want_bg is None and background is not None:
                line.append(_CODES["reset"])
                foreground = background = None
            if want_fg != foreground:
                line.append(_truecolor(artwork.PALETTE[want_fg]))
                foreground = want_fg
            if want_bg is not None and want_bg != background:
                line.append(_truecolor(artwork.PALETTE[want_bg], background=True))
                background = want_bg
            line.append(glyph)
        if foreground is not None or background is not None:
            line.append(_CODES["reset"])
        rows.append("".join(line))
    return rows


def _lockup(art: list[str], style: Style, tagline: str) -> str:
    """Place the wordmark beside the artwork, vertically centred."""
    text = [*_WORDMARK, tagline]
    top = max(0, (len(art) - len(text)) // 2)
    lines = []
    for index, row in enumerate(art):
        position = index - top
        if 0 <= position < len(text):
            body = text[position]
            colour = ("grey",) if position == len(text) - 1 else ("cyan", "bold")
            lines.append(row + "  " + style(body, *colour))
        else:
            lines.append(row.rstrip())
    return "\n".join(lines)


def logo(style: Style, *, tagline: str = TAGLINE, columns: int | None = None) -> str:
    """The best wordmark this terminal can actually display.

    Three tiers, because a logo that renders as garbage is worse than no logo:
    the pixel clock needs 24-bit colour and room for the lockup beside it; the
    line-art clock needs box-drawing characters; the last one needs nothing.
    """
    if columns is None:
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    if style.truecolor and style.unicode and columns >= artwork.WIDTH + len(tagline) + 4:
        return _lockup(clock_mark(style), style, tagline)

    template = _LOGO_UNICODE if style.unicode else _LOGO_ASCII
    art = template.format(tagline=tagline).strip("\n")
    if not style.color:
        return art
    lines = []
    for line in art.splitlines():
        face, rest = line[:_LOGO_SPLIT], line[_LOGO_SPLIT:]
        accent = "grey" if tagline in rest else "cyan"
        weight = () if tagline in rest else ("bold",)
        lines.append(style(face, "yellow") + style(rest, accent, *weight))
    return "\n".join(lines)


def banner(style: Style, *, tagline: str = TAGLINE, stream=None) -> str:
    """The wordmark, but only when a person is actually looking at it.

    Piping to a file or into another command gets nothing: decoration in
    captured output is noise, and in a log file it is worse than noise.
    """
    stream = stream or sys.stdout
    if not getattr(stream, "isatty", lambda: False)():
        return ""
    return logo(style, tagline=tagline) + "\n"


# ---------------------------------------------------------------------------
# hour-grid bar (used by the schedule solver, which works in whole hours)
# ---------------------------------------------------------------------------


def hour_bar(covered: set[int], anchors: set[int], style: Style, *, indent: str = "  ") -> str:
    cells = [
        style.full if hour in covered else style.empty
        for hour in range(24)
        for _ in range(CELLS_PER_HOUR)
    ]
    bar = "".join(
        style(char, "green") if char == style.full else style(char, "grey") for char in cells
    )
    markers = [" "] * DAY_CELLS
    for hour in anchors:
        markers[hour * CELLS_PER_HOUR] = style.anchor
    marker_line = style("".join(markers), "cyan")

    labels = [" "] * DAY_CELLS
    for hour in range(0, 24, 3):
        for offset, char in enumerate(f"{hour:02d}"):
            labels[hour * CELLS_PER_HOUR + offset] = char
    return "\n".join(
        line.rstrip()
        for line in (indent + style("".join(labels), "grey"), indent + bar, indent + marker_line)
    )
