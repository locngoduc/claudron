#!/usr/bin/env python3
"""Regenerate every logo asset from one source render.

    python tools/make_logo.py

Inputs
    assets/logo-source.png   the original artwork, background included

Outputs
    assets/logo.png          1024x736 transparent, the main asset
    assets/logo-small.png    256x184 transparent, for inline use
    assets/icon.png          512x512 transparent, square avatar crop
    docs/assets/logo.png     the same logo, where the docs site can serve it
    assets/social-preview.png 1280x640, for GitHub's social preview card
    src/claudron/artwork.py  the clock mark for the terminal, as plain text

Why a generator rather than hand-tuned files: the source render only *looks*
like pixel art - its edges are anti-aliased, so there is no native grid to
recover (`--report` shows the measurement). This script quantises it onto a
real 64x46 grid with a fixed eight-colour palette, after which every export is
an integer upscale and stays perfectly crisp at any size, and the terminal
version is provably the same artwork rather than a hand-drawn lookalike.

Requires Pillow. It is a development dependency only: claudron itself ships the
generated text and has no runtime dependencies.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, deque
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - developer tooling
    sys.exit("this script needs Pillow:  pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "logo-source.png"
ASSETS = ROOT / "assets"
ARTWORK = ROOT / "src" / "claudron" / "artwork.py"
# README.md points at assets/logo.png relatively, which GitHub resolves against
# the repository. MkDocs resolves it against the built site, so the site needs
# its own copy at the same relative position.
DOCS_ASSETS = ROOT / "docs" / "assets"

#: The brand palette, sampled from the source render and then rounded to eight
#: flat colours. Keys are the single characters used in the generated grids.
PALETTE: dict[str, tuple[int, int, int]] = {
    "o": (0xFE, 0x73, 0x42),  # body
    "d": (0xC4, 0x59, 0x39),  # body shadow
    "k": (0x17, 0x1C, 0x28),  # ink: outlines, eyes, toolbox
    "b": (0x1B, 0x46, 0x8E),  # clock blue
    "n": (0x1B, 0x3B, 0x71),  # clock blue, shaded
    "w": (0xF5, 0xF2, 0xEE),  # clock face
    "S": (0xCD, 0xD2, 0xDC),  # steel, lit: the wrench body
    "s": (0x88, 0x8D, 0x9B),  # steel, shaded
    "y": (0xFE, 0xB8, 0x33),  # accent: the alarm marks
}
TRANSPARENT = "."

#: Master grid. Big enough to keep the eyes, the clock hands, the toolbox
#: handle and the alarm bells; 64 wide so every export is a power-of-two
#: multiple. Coarser grids smear the toolbox into an unreadable blob.
MASTER = (64, 46)
#: Terminal grid: the clock mark alone. The whole scene needs fourteen text
#: rows before the toolbox and the eyes survive, which is far too much banner
#: for a CLI; the clock is the one element that still reads at seven rows, and
#: it is the part that means something here anyway.
TERMINAL = (14, 14)
TERMINAL_CROP = (0.49, 0.37, 0.93, 0.98)  # left, top, right, bottom, as fractions
#: Square avatar: 64 cells at 8x is exactly 512px.
ICON_GRID = 64
ICON_SCALE = 8

#: GitHub's social preview card. The logo is placed at a whole-number scale on
#: a flat background, so the result is reproducible on any machine - no fonts
#: are involved, which is what would otherwise make CI's drift check flaky.
SOCIAL_SIZE = (1280, 640)
SOCIAL_SCALE = 10
SOCIAL_BACKGROUND = (0xF7, 0xF4, 0xF1, 0xFF)


# ---------------------------------------------------------------------------


def load_source(path: Path) -> Image.Image:
    if not path.exists():
        sys.exit(f"missing {path}")
    return Image.open(path).convert("RGB")


def cut_background(image: Image.Image) -> Image.Image:
    """Make the backdrop transparent without punching holes in the artwork.

    A plain colour key would also erase the clock face, which is nearly white.
    Flooding inwards from the border instead only removes the backdrop and the
    ground shadow, because everything else is enclosed by darker outlines.
    """
    width, height = image.size
    pixels = image.load()

    def backdrop(colour) -> bool:
        red, green, blue = colour
        return min(red, green, blue) > 0xC8 and (max(colour) - min(colour)) < 0x18

    seen = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def push(x: int, y: int) -> None:
        if not seen[y * width + x] and backdrop(pixels[x, y]):
            seen[y * width + x] = 1
            queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                push(nx, ny)

    out = image.convert("RGBA")
    target = out.load()
    for y in range(height):
        row = y * width
        for x in range(width):
            if seen[row + x]:
                target[x, y] = (0, 0, 0, 0)
    return out.crop(out.getbbox())


def nearest(colour: tuple[int, int, int]) -> str:
    def distance(key: str) -> int:
        return sum((a - b) ** 2 for a, b in zip(PALETTE[key], colour, strict=True))

    return min(PALETTE, key=distance)


def quantise(image: Image.Image, cols: int, rows: int) -> list[str]:
    """Reduce to a grid of palette characters, one per cell, by majority vote."""
    width, height = image.size
    grid = []
    for gy in range(rows):
        line = []
        for gx in range(cols):
            cell = image.crop(
                (
                    round(gx * width / cols),
                    round(gy * height / rows),
                    round((gx + 1) * width / cols),
                    round((gy + 1) * height / rows),
                )
            )
            raw = cell.tobytes()  # RGBA, four bytes per pixel
            total = len(raw) // 4
            opaque = [
                (raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 4) if raw[i + 3] > 128
            ]
            if len(opaque) * 2 < total:
                line.append(TRANSPARENT)
            else:
                line.append(Counter(nearest(p) for p in opaque).most_common(1)[0][0])
        grid.append("".join(line))
    return grid


def render(grid: list[str], scale: int) -> Image.Image:
    """Draw a grid at an integer scale. Nearest-neighbour by construction."""
    cols, rows = len(grid[0]), len(grid)
    image = Image.new("RGBA", (cols, rows), (0, 0, 0, 0))
    pixels = image.load()
    for y, line in enumerate(grid):
        for x, key in enumerate(line):
            if key != TRANSPARENT:
                pixels[x, y] = (*PALETTE[key], 255)
    return image.resize((cols * scale, rows * scale), Image.NEAREST)


def square(grid: list[str], side: int | None = None) -> list[str]:
    """Pad a grid to a square, centred, for use as an avatar."""
    cols, rows = len(grid[0]), len(grid)
    side = side or max(cols, rows)
    left = (side - cols) // 2
    top = (side - rows) // 2
    blank = TRANSPARENT * side
    padded = [blank] * top
    padded += [TRANSPARENT * left + line + TRANSPARENT * (side - cols - left) for line in grid]
    padded += [blank] * (side - rows - top)
    return padded


def write_artwork(terminal: list[str]) -> None:
    palette = "\n".join(
        f'    "{key}": (0x{r:02X}, 0x{g:02X}, 0x{b:02X}),' for key, (r, g, b) in PALETTE.items()
    )
    rows = "\n".join(f'    "{line}",' for line in terminal)
    ARTWORK.write_text(
        f'''"""The clock mark, as data. Generated by tools/make_logo.py - do not edit.

This is the clock from the project logo, quantised onto a 14x14 grid. Each
string is one row of pixels; each character indexes PALETTE, and "." means
transparent. Two rows become one line of terminal output, drawn with half-block
characters so the pixels stay square.

The full mascot lives in assets/logo.png - it needs fourteen text rows to stay
legible, which is more banner than any command should print.
"""

from __future__ import annotations

PALETTE: dict[str, tuple[int, int, int]] = {{
{palette}
}}

TRANSPARENT = "{TRANSPARENT}"

CLOCK: tuple[str, ...] = (
{rows}
)

WIDTH = len(CLOCK[0])
HEIGHT = len(CLOCK)
''',
        encoding="utf-8",
    )


def measure_grid(image: Image.Image) -> str:
    """Show that the source has no native pixel grid to recover."""
    from PIL import ImageChops, ImageStat

    width, height = image.size
    lines = ["cells   round-trip rms"]
    for cells in (24, 32, 40, 48, 56, 64, 80, 96):
        small = image.resize((cells, max(1, round(height * cells / width))), Image.BOX)
        back = small.resize((width, height), Image.NEAREST)
        rms = sum(ImageStat.Stat(ImageChops.difference(image, back)).rms) / 3
        lines.append(f"{cells:5d}   {rms:6.2f}")
    lines.append("no knee in the curve => anti-aliased source, so we impose a grid instead")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true", help="print the grid measurement and exit")
    args = parser.parse_args()

    source = load_source(SOURCE)
    if args.report:
        print(measure_grid(source))
        return 0

    trimmed = cut_background(source)
    master = quantise(trimmed, *MASTER)

    ASSETS.mkdir(exist_ok=True)
    outputs = [
        ("logo.png", render(master, 16)),
        ("logo-small.png", render(master, 4)),
        # 64 cells at 8x lands exactly on 512, the size every avatar wants.
        ("icon.png", render(square(master, ICON_GRID), ICON_SCALE)),
    ]
    for name, image in outputs:
        image.save(ASSETS / name, optimize=True)
        print(f"{ASSETS / name}  {image.size[0]}x{image.size[1]}")

    card = Image.new("RGBA", SOCIAL_SIZE, SOCIAL_BACKGROUND)
    mascot = render(master, SOCIAL_SCALE)
    card.paste(
        mascot,
        ((SOCIAL_SIZE[0] - mascot.width) // 2, (SOCIAL_SIZE[1] - mascot.height) // 2),
        mascot,
    )
    card.convert("RGB").save(ASSETS / "social-preview.png", optimize=True)
    print(f"{ASSETS / 'social-preview.png'}  {SOCIAL_SIZE[0]}x{SOCIAL_SIZE[1]}")

    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    logo = dict(outputs)["logo.png"]
    logo.save(DOCS_ASSETS / "logo.png", optimize=True)
    print(f"{DOCS_ASSETS / 'logo.png'}  {logo.size[0]}x{logo.size[1]}")

    left, top, right, bottom = TERMINAL_CROP
    width, height = trimmed.size
    cropped = trimmed.crop(
        (int(left * width), int(top * height), int(right * width), int(bottom * height))
    )
    terminal = quantise(cropped, *TERMINAL)
    write_artwork(terminal)
    print(f"{ARTWORK}  {TERMINAL[0]}x{TERMINAL[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
