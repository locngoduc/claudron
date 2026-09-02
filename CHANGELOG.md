# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-09-02

### Fixed
- The logo in `README.md` used a path relative to the repository, which
  GitHub and the documentation site resolve but the PyPI project page
  cannot. It now points at an absolute URL, so the logo renders on PyPI too.
- Removed a dead link to a Vietnamese README that was never published (the
  file is gitignored, kept local-only).

## [0.1.0] - 2026-09-02

First release.

### Added
- `claudron completion bash|zsh|fish`, generated from the argument parser
  itself so the scripts cannot drift out of date.
- `claudron suggest` — a schedule solver. Describe your day (`--sleep`, `--busy`,
  `--idle`, `--wake`) and the times you need (`--start-at`, `--free-at`), and it
  searches every legal arrangement of anchors, ranks them by where the
  unavoidable idle hours land, explains what each one does for the times you
  named, and can write the winner into your config with `--apply`. Conflicting
  constraints are diagnosed by name rather than returning nothing.
- A logo: a pixel-art mascot with an alarm clock. `assets/logo.png`,
  `assets/logo-small.png` and `assets/icon.png` are generated from a single
  source render by `tools/make_logo.py`, which imposes a real 64x46 pixel grid
  and a nine-colour palette so every export is an integer upscale.
- A terminal banner, shown by `claudron init` and `claudron install`, in three
  tiers: the logo's clock drawn with half-block characters in 24-bit colour, a
  box-drawing clock, and plain ASCII. Suppressed whenever output is not a
  terminal.
- `claudron init` with four presets and a fully commented TOML config.
- `claudron plan` — a 24-hour timeline that simulates the day, lists the idle
  gaps, and reports anchors that would open nothing.
- `claudron status` — the open window, tokens spent in it, alignment against the
  plan, and the next anchor. `--short` for status bars, `--json` for scripts.
- `claudron usage` — real per-window token usage read from Claude Code's local
  transcripts, with a file-level cache keyed on size and mtime.
- `claudron fire` — send an anchor, with `--dry-run`, `--scheduled` and
  `--force`. Skips automatically when a window is already open.
- `claudron daemon` — supervisor loop with catch-up after suspend and a warning
  when a window opens inside an idle gap.
- `claudron install` / `uninstall` — systemd user timer or service, launchd
  agent, or printed cron lines.
- `claudron doctor` — environment and schedule check, including a probe of the
  installed `claude` CLI's supported flags.
- Schedule validation: swallowed anchors, more than `24 / window_hours` anchors,
  off-the-hour anchors, and daylight-saving timezones.

### Distribution
- Published to PyPI via GitHub Actions [trusted publishing][tp], so no
  long-lived upload token exists.
- A Debian package built by `packaging/build-deb.sh`, attached to every release
  and served from an apt repository on the documentation site. It installs the
  launcher, the manual page and completions for bash, zsh and fish.
- Documentation site at <https://locngoduc.github.io/claudron/>, built from the
  same documents that live in the repository.
- `SHA256SUMS` attached to every release.
- The version is single-sourced from `src/claudron/__init__.py`; the release
  workflow refuses to publish when the tag disagrees with it.

[tp]: https://docs.pypi.org/trusted-publishers/
