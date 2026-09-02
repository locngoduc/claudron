<div align="center">
  <img src="assets/logo.png" alt="claudron" width="128">
  <h1>claudron</h1>
  <p><strong>Align your Claude usage windows with your working day.</strong></p>
  <p>
    <a href="https://pypi.org/project/claudron/"><img alt="PyPI" src="https://img.shields.io/pypi/v/claudron?color=fe7342&label=pypi"></a>
    <a href="https://pypi.org/project/claudron/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/claudron?color=1b468e"></a>
    <a href="https://github.com/locngoduc/claudron/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/locngoduc/claudron/actions/workflows/ci.yml/badge.svg"></a>
    <a href="https://locngoduc.github.io/claudron/"><img alt="Docs" src="https://img.shields.io/badge/docs-locngoduc.github.io-1b468e"></a>
    <a href="https://github.com/locngoduc/claudron/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-black"></a>
  </p>
</div>

Claude's usage limits reset on a rolling window: the window opens when you send
your **first message** after the previous one expired, and lasts 5 hours. Nobody
starts it for you. If you stop working at 22:00 and come back at 09:00, your
window does not open at 03:00 — it opens at 09:00, and your next reset lands at
14:00, in the middle of the afternoon.

claudron puts the window boundaries where *you* want them. It sends one tiny
message at the times you choose, reads your **real** usage from Claude Code's
local transcripts, and tells you when to stay quiet so an accidental message
does not open a window early and drag the rest of the day out of alignment.

```
── Tuesday 02 September 2026 ────────────────────────────────────
  00    03    06    09    12    15    18    21
  |     |     |     |     |     |     |     |
  ████████░░░░██████████░░░░████████████████████████████░░░░░░████
        ▲                  ▲         ▲       ▲
                                │ now

  █ window open   · idle   ▲ anchor   x anchor that opens nothing
```

---

## The one rule

> A usage window opens on the first message sent while no window is open, and
> lasts 5 hours. A message sent while a window is already open changes nothing.

Two consequences that trip everybody up:

1. **Your anchor times are not your reset times.** If you want limits to free up
   at 10:00, the anchor is at **05:00** — 10:00 is when that window *closes*.
2. **Silence is part of the schedule.** To open a window at 12:00 you must send
   nothing between 10:00 and 12:00. One stray message at 10:30 opens a window
   that runs to 15:30, and every anchor before then does nothing.

claudron models both halves. `claudron plan` shows the windows *and* the idle
gaps; the daemon warns you when you type into one.

### Why 4 anchors is the maximum

A day is 24 hours and a window is 5. `24 / 5 = 4.8`, so a schedule that repeats
every day holds **at most 4 anchors covering 20 of 24 hours**, leaving at least
4 hours idle. There is no arrangement that beats it. `claudron doctor` tells you
if your anchor list is over budget, and `claudron plan` shows exactly which
anchors would be swallowed.

---

## Install

Needs Python 3.11+ and the [`claude`](https://claude.com/claude-code) CLI,
already signed in. claudron has **no runtime dependencies** and never touches
your credentials — it shells out to the CLI you have already authenticated.

```bash
pipx install claudron          # or: uv tool install claudron
```

Ubuntu and Debian, from the apt repository:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://locngoduc.github.io/claudron/apt/claudron.gpg | sudo tee /etc/apt/keyrings/claudron.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/claudron.gpg] https://locngoduc.github.io/claudron/apt ./" | sudo tee /etc/apt/sources.list.d/claudron.list > /dev/null
sudo apt update && sudo apt install claudron
```

Every release also attaches a `.deb`, a wheel, an sdist and `SHA256SUMS`.
Full options — uv, pip, from source, shell completions, verifying a download —
are in the [installation guide](https://locngoduc.github.io/claudron/install/).

## Quick start

```bash
claudron suggest --start-at 12:00 --sleep 23:00-05:00 --timezone Asia/Ho_Chi_Minh
claudron init --preset balanced --timezone Asia/Ho_Chi_Minh
claudron plan
claudron doctor
claudron install
```

`suggest` proposes anchors that fit your day, `init` writes a fully commented
config, `plan` simulates the day it produces,
`doctor` checks your environment, `install` sets up a systemd user timer
(or a launchd agent on macOS, or prints cron lines).

### Or let it work the anchors out for you

You know your day; you should not have to do the arithmetic. Tell `suggest`
what is true about your day and it searches every legal schedule for the one
that puts the unavoidable idle hours where they cost you least.

```bash
claudron suggest --start-at 12:00 --sleep 23:00-05:00 --busy 08:00-18:00
```

```
  1  02:00  07:00  12:00  17:00  ← best fit
     00    03    06    09    12    15    18    21
     ····████████████████████████████████████████····
         ▲         ▲         ▲         ▲
     stay quiet  22:00-02:00   4h idle, 0h of it inside your working hours
     • 12:00 opens a fresh 5h window, running to 17:00
     • at 05:00 you pick up the window opened at 02:00 while you slept -
       2h left on it, none of its budget spent, resetting at 07:00
```

| flag           | meaning                                                              |
| -------------- | -------------------------------------------------------------------- |
| `--start-at`   | a fresh window **must** start here (repeatable)                       |
| `--free-at`    | a full budget must be available here — an anchor, or idle time         |
| `--sleep A-B`  | hours you are asleep; idle time here is free                           |
| `--idle A-B`   | hours you are away — lunch, a standing meeting                         |
| `--busy A-B`   | hours you really work; idle time here is heavily penalised             |
| `--wake HH:MM` | when your day starts (inferred from `--sleep` otherwise)               |
| `--apply N`    | write option N into the config file                                    |

Ranges wrap past midnight (`23:00-05:00`), and everything runs on a whole-hour
grid, because an anchor at 12:30 just throws away 30 minutes.

**`--start-at` and `--free-at` are not the same thing.** `--start-at 12:00`
means *a new 5-hour window begins at noon*. `--free-at 12:00` is looser: it only
asks that nothing is counting against you at noon — an anchor there satisfies
it, and so does an idle gap, because your next message would then open a full
window. If you are not sure which you mean, `--free-at` is the safer one.

If nothing satisfies your constraints, `suggest` says which two conflict rather
than shrugging:

```
no schedule satisfies those constraints
  --start-at 12:00 and 15:00 are only 3h apart, but a window lasts 5h -
  the second one would land inside the first.
```

> **Timezone matters more than anything else here.** Anchors are wall-clock
> times. Set `[schedule].timezone` to your IANA zone (`Asia/Ho_Chi_Minh`,
> `Europe/Berlin`, …) or pass `--timezone`; an hour out is a wasted window every
> single day. `claudron doctor` also warns if your zone observes daylight
> saving, because two days a year one window is an hour short.

### Presets

If you would rather not think about it, start from a named schedule:

| preset     | anchors                     | shape                                          |
| ---------- | --------------------------- | ---------------------------------------------- |
| `balanced` | 05:00 12:00 17:00 22:00     | early start, protected midday, long evening    |
| `office`   | 08:00 13:00 18:00 23:00     | office hours first, overnight window for jobs  |
| `nightowl` | 10:00 15:00 20:00 01:00     | late start, window running past midnight       |
| `workday`  | 08:00 13:00 18:00           | one working day, nothing overnight             |

`claudron config presets` prints them with descriptions. All four are `suggest`
answers for a common shape of day.

---

## Commands

| command             | what it does                                                     |
| ------------------- | ---------------------------------------------------------------- |
| `claudron suggest`  | propose anchor times that fit your day, and apply the one you pick |
| `claudron status`   | the open window, tokens spent in it, the next anchor, the next gap |
| `claudron plan`     | a 24-hour timeline, the anchors, the idle gaps, and warnings       |
| `claudron usage`    | real token usage per window, read from local transcripts           |
| `claudron fire`     | send an anchor now (`--dry-run` prints the exact command)          |
| `claudron daemon`   | supervisor loop: fires anchors, catches up after suspend, warns on drift |
| `claudron install`  | systemd timer / systemd service / launchd agent / cron lines       |
| `claudron doctor`   | environment and schedule check                                     |
| `claudron config`   | `path`, `show`, `presets`                                          |

`status`, `plan` and `usage` all accept `--json` for scripting.

### Status bar

`claudron status --short` prints one line, suitable for a Claude Code
statusline, tmux, or a shell prompt:

```
05:00→10:00 2h13m • 412.7k • next 12:00
```

---

## `timer` mode or `daemon` mode

`claudron install --mode timer` (default) runs one short-lived process per
anchor. Nothing runs in between, so it survives reboots and crashes for free.

`claudron install --mode daemon` keeps a single small process alive. It costs a
few MB of RAM and adds two things a timer cannot do:

- **catch-up after suspend** — a laptop that wakes at 12:20 still gets its 12:00
  window, because 12:20 is inside the same clock hour;
- **the blackout guard** — a warning the moment a window opens inside an idle
  gap, while you can still adjust the rest of the day.

Missed anchors are *not* fired blindly. An anchor more than
`catch_up_minutes` overdue (45 by default) is skipped on purpose: firing it late
would open the window in the wrong hour and push every later reset out of place
for the rest of the day. That is also why the generated systemd timer sets
`Persistent=false`.

---

## What claudron reads, and what it never touches

Usage numbers come from Claude Code's own transcripts in `~/.claude/projects`.
From each line, claudron reads exactly four things:

    timestamp · message type (user/assistant) · model id · token counts

Message content, tool arguments, file paths inside conversations, titles and
attachments are **never parsed, never stored, never transmitted**. The parser is
one short function — [`_parse_line`](https://github.com/locngoduc/claudron/blob/main/src/claudron/usage.py) — deliberately kept
small enough to audit in a minute.

- **No credentials.** claudron never reads, stores or asks for an API key,
  OAuth token, or password. It shells out to the `claude` CLI you have already
  authenticated, exactly as you would from your own terminal.
- **No network.** claudron itself makes no network requests. The only outbound
  traffic is the `claude` process it starts.
- **No telemetry.** Nothing is reported anywhere. Ever.
- **Owner-only files.** Config, state and cache live under XDG directories with
  `0700` directories and `0600` files. `claudron doctor` verifies the modes.
- **Isolated anchors.** The anchor message runs in a dedicated empty directory
  with tools and MCP servers switched off, so nothing from your repositories —
  no source, no `CLAUDE.md`, no project settings — is loaded into it.
- **Hard spend cap.** Each anchor carries `--max-budget-usd` (default `$0.05`)
  when your `claude` build supports the flag.

### What claudron honestly cannot do

**It cannot tell you how much of your limit is left.** Anthropic does not
publish your rate limit to the local machine, and claudron does not guess. What
it reports is the number of tokens in the current window and how that compares
to *your own* busiest window on record. Use Claude Code's `/usage` for the real
figure.

Two behaviours are documented heuristics rather than published guarantees, and
both are configurable:

- **Window starts are rounded down to the hour**
  (`[usage].floor_window_to_hour`). This matches how usage blocks are commonly
  reconstructed from transcripts, and it is why a 12:00 anchor firing at 12:00:20
  loses nothing. Set it to `false` to use exact timestamps.
- **The window length is a config value** (`[schedule].window_hours = 5`), not a
  constant, so claudron survives a change to Claude's limits without a release.

---

## Configuration

`claudron config path` prints the location (`~/.config/claudron/config.toml` by
default). The generated file is commented throughout; the essentials:

```toml
[schedule]
timezone = "Asia/Ho_Chi_Minh"
window_hours = 5
anchors = ["05:00", "12:00", "17:00", "22:00"]
jitter_seconds = 20
catch_up_minutes = 45

[anchor]
prompt = "ok"
model = "haiku"          # a small model keeps anchoring nearly free
max_budget_usd = 0.05
skip_if_window_active = true
isolated_cwd = true

[usage]
projects_dir = "~/.claude/projects"
floor_window_to_hour = true

[warnings]
guard_blackouts = true
high_usage_ratio = 0.85

[notify]
command = ["notify-send", "{title}", "{body}"]
```

Every path can be overridden for testing or sandboxing with
`CLAUDRON_CONFIG_DIR` and `CLAUDRON_STATE_DIR`.

---

## Cost of anchoring

Four anchors a day, each a two-token prompt on a small model. `claudron usage`
reports the exact figure — it separates windows that contain nothing but an
anchor, so you can see what the schedule itself costs you rather than take
anyone's word for it.

---

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

The test suite is stdlib-only and touches no real transcripts, no real config
and no network.

---

## Documentation

- [Vietnamese README](https://locngoduc.github.io/claudron/README.vi/)
- [Designing a schedule](https://locngoduc.github.io/claudron/schedules/) — the arithmetic, worked examples,
  and how to recover when the day drifts.

## License

MIT — see [LICENSE](https://github.com/locngoduc/claudron/blob/main/LICENSE).
