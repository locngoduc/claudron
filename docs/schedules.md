# Designing a schedule

## The arithmetic

A window is 5 hours. A day is 24. So:

- A schedule that repeats every day holds **at most 4 anchors** — `24 / 5 = 4.8`.
- 4 anchors cover **20 hours**, leaving **4 hours idle**. That 4 hours is not
  waste you can optimise away; it is the remainder of the division.
- Consecutive anchors must be **at least 5 hours apart**. Anything closer lands
  inside a window that is already open and does nothing at all.

`claudron doctor` and `claudron plan` enforce both rules.

## Worked example

Say you want your limits to free up at **12:00** and at **17:00**, you start
around 05:00, and you work into the evening.

A first attempt often looks like this:

```toml
anchors = ["05:00", "10:00", "12:00", "17:00", "22:00"]
```

which claudron rejects:

```
error  anchor 12:00 is only 2h00m after 10:00, so it lands inside the window
       10:00 opens (which runs to 15:00) and resets nothing
       Drop it, or move it to 15:00 or later.
```

The confusion is that **10:00 is a reset time, not an anchor**. It is when the
05:00 window closes. Writing it down as an anchor makes claudron open a window
at 10:00 that runs to 15:00 — and swallows the 12:00 one you actually wanted.

The fix is to *remove* 10:00 and stay quiet from 10:00 to 12:00:

```toml
anchors = ["05:00", "12:00", "17:00", "22:00"]
```

```
  00    03    06    09    12    15    18    21
  |     |     |     |     |     |     |     |
  ██████····██████████····████████████████████████
        ▲                  ▲         ▲       ▲

05:00 → 10:00     work
10:00 → 12:00     idle — send nothing
12:00 → 17:00     work
17:00 → 22:00     work
22:00 → 03:00     work / long jobs
03:00 → 05:00     idle — asleep anyway
```

Four windows, 20 hours of coverage, and both idle gaps land where they cost
nothing: one over a late breakfast, one in the middle of the night. This is the
`balanced` preset, and it is optimal — no daily-repeating schedule does better.

## Let the solver choose

`claudron suggest` searches every legal arrangement of anchors and ranks them by
where the idle hours land. Describe your day rather than your schedule:

```bash
claudron suggest --start-at 12:00 --sleep 23:00-05:00 --busy 08:00-18:00
```

- `--start-at H` — a fresh window **must** begin at H.
- `--free-at H` — at H nothing may be counting against you. An anchor satisfies
  this; so does an idle gap, because your next message then opens a full window.
  Use this one when you mean "I want full budget available at H" rather than "a
  window starts at H".
- `--sleep A-B`, `--idle A-B` — idle hours here are free.
- `--busy A-B` — idle hours here are expensive.
- `--wake H` — when your day starts. Inferred from the overnight `--sleep` range.

Ranking, in order of weight:

1. **Where the idle hours land.** An idle hour inside `--busy` costs far more
   than one inside `--sleep`, which costs nothing at all.
2. **A window opening exactly at wake-up**, as a mild tie-break.

Two things the ranking deliberately does *not* do. It does not penalise waking
up inside a window that opened while you slept — you spent none of its budget,
you can work immediately, and it resets sooner. And it does not reward waking up
into an idle gap, which sounds tidy but means an hour awake and unable to work
without breaking the plan.

Add `--apply 1` to write the chosen option into your config (the previous file
is backed up to `config.toml.bak`).

## Choosing where the idle gaps go

You get to place 4 hours of idle time. Put them where you were not going to work
anyway:

- **Overnight.** Any gap between about 01:00 and 06:00 is free.
- **A meal.** A 2-hour gap over lunch or dinner costs nothing if you are away.
- **A standing meeting.** A recurring block where you are not typing.

Avoid putting a gap in the middle of a focused stretch: you will send a message
without thinking, a window will open early, and the rest of the day shifts.

## When the day drifts

You will type into a gap eventually. Here is what happens and what to do.

Suppose you send a message at 10:30, inside the 10:00–12:00 gap. A window opens
at 10:00 (rounded down to the hour) and runs to **15:00**. Your 12:00 anchor is
now dead — claudron will skip it rather than waste a message on it.

```bash
claudron plan --live
```

`--live` seeds the simulation with the window that is actually open, so you see
the real rest of the day rather than the ideal one. From there:

- **Ride it out.** Your 17:00 anchor still works, because 15:00 + the idle gap
  15:00–17:00 gets you back on plan by the evening. Most drifts self-heal within
  one window.
- **Re-anchor deliberately.** If the drift lands somewhere painful, change the
  anchors for the rest of the day and re-run `claudron plan`.

The daemon (`claudron install --mode daemon`) warns you at the moment a window
opens inside a gap, which is the only time you can still act on it cheaply.

## Rounding to the hour

By default claudron treats a window as starting at the top of the hour of its
first message (`[usage].floor_window_to_hour = true`). Two consequences:

- A 12:00 anchor firing at 12:00:20 loses nothing.
- An anchor at 12:30 loses 30 minutes — the window is treated as 12:00–17:00.
  claudron warns about off-the-hour anchors for exactly this reason.

This rounding is how usage blocks are commonly reconstructed from transcripts,
not a published guarantee. If you would rather use exact timestamps, set the
option to `false`; `claudron usage` will then show windows starting at the
minute of your first message.

## Non-5-hour windows

`[schedule].window_hours` exists so claudron survives a change to Claude's
limits without waiting for a release. Every rule above rescales: with a 4-hour
window you get 6 anchors and full coverage; with 6 hours you get 4 anchors and
no idle time at all.
