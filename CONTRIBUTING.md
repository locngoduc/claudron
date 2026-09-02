# Contributing

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

There are no runtime dependencies and there should not be any. The test suite is
stdlib `unittest`, runs in well under a second, and touches no real transcripts,
no real config, and no network.

## Documentation

The site at <https://locngoduc.github.io/claudron/> is built from this
repository with MkDocs Material:

```bash
pip install mkdocs-material
mkdocs serve          # http://127.0.0.1:8000
mkdocs build --strict # what CI runs
```

`docs/index.md`, `docs/changelog.md` and friends are one-line includes of the
root documents, so the website and the repository can never disagree. Links
between root documents are absolute GitHub or site URLs on purpose: relative
ones break on PyPI and on the site.

## Releasing

Bump `__version__` in `src/claudron/__init__.py`, add the `CHANGELOG.md`
section, and push a `vX.Y.Z` tag. The release workflow refuses to publish if
the tag and the version disagree, then builds the wheel, the sdist and the
`.deb`, and uploads them.

## Ground rules

**Never send a message during a test.** Nothing in the suite may invoke the
`claude` CLI. Mock `claudron.anchor.probe_flags` and
`claudron.anchor.subprocess.run` instead — a test that costs the contributor
tokens is a bug.

**Read only metadata.** `usage._parse_line` is the single place transcript lines
are interpreted. It reads timestamp, message type, model id and token counts,
and it must stay that way. A change that widens it needs a very good reason and
a note in the README's privacy section.

**Keep the packaging honest.** `packaging/build-deb.sh` runs anywhere dpkg is
installed, so the package CI ships is the one you can build and inspect
locally, and `tests/test_packaging.py` builds it for real. If you add a command
or an exit code, the man page test will fail until `packaging/claudron.1`
covers it - that is the point.

**Do not claim to know the limit.** Anthropic does not expose your rate limit to
the local machine. claudron reports token counts and comparisons against the
user's own history, and says so. Please keep any new output equally honest.

**State the assumptions.** Window length and hour-rounding are configuration,
not constants, because they are observed behaviour rather than published
guarantees. New behavioural assumptions should follow the same pattern.

## Artwork

Every logo asset is generated from one file:

```bash
pip install pillow
python tools/make_logo.py
```

`assets/logo-source.png` is the original render. The script keys out its
backdrop, quantises it onto a real 64x46 grid with a fixed nine-colour palette,
and writes `assets/logo.png`, `assets/logo-small.png`, `assets/icon.png` and
`src/claudron/artwork.py`.

Do not hand-edit any of those four - edit the script and re-run it. The source
render only *looks* like pixel art; its edges are anti-aliased, so there is no
native grid to recover (`python tools/make_logo.py --report` shows the
measurement that establishes this). Imposing our own grid is what makes every
export an integer upscale, and therefore crisp at any size.

Pillow is a development dependency only. claudron ships the generated text and
keeps zero runtime dependencies.

The terminal banner has three tiers and all three must keep working: the pixel
clock (24-bit colour, box-drawing, and a wide enough terminal), the line-art
clock (box-drawing only), and plain ASCII. A logo that renders as garbage is
worse than no logo.

## Style

`ruff` config lives in `pyproject.toml`; line length 100.

```bash
ruff check . && ruff format --check .
```
