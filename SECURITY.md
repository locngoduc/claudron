# Security policy

## Reporting a vulnerability

Report privately through
[GitHub's security advisories](https://github.com/locngoduc/claudron/security/advisories/new).
Please do not open a public issue for anything exploitable.

Expect an acknowledgement within a week. If a fix is warranted it ships as a
patch release with the advisory published alongside it.

## What claudron touches

Knowing the boundaries makes it easier to judge whether something is a
vulnerability at all.

- **No credentials.** claudron never reads, stores, or asks for an API key,
  OAuth token, or password. It shells out to the `claude` CLI you have already
  authenticated, exactly as you would from your own terminal.
- **No network.** claudron itself makes no network requests. The only outbound
  traffic is the `claude` process it starts.
- **No telemetry.** Nothing is reported anywhere.
- **Transcripts are read for metadata only.** From each line of
  `~/.claude/projects/**/*.jsonl` claudron parses the timestamp, the message
  type, the model id and the token counts. Message content, tool arguments,
  file paths inside conversations, titles and attachments are never parsed,
  stored or transmitted. This happens in one short function,
  [`usage._parse_line`](https://github.com/locngoduc/claudron/blob/main/src/claudron/usage.py),
  kept small enough to audit in a minute.
- **Owner-only files.** Config, state and cache live under XDG directories with
  `0700` directories and `0600` files. `claudron doctor` verifies the modes.
- **Isolated subprocess.** The anchor message runs in a dedicated empty
  directory with tools and MCP servers switched off, so nothing from your
  repositories is loaded into it. The command is built with an argument list,
  never a shell string.
- **Spend ceiling.** Each anchor carries `--max-budget-usd` when your `claude`
  build supports the flag.

## Supply chain

- Releases are published to PyPI by GitHub Actions using
  [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so no
  long-lived PyPI token exists to leak.
- `.deb` packages are built by the same workflow from the tagged commit, and
  `SHA256SUMS` is attached to every release.
- claudron has no runtime dependencies, so there is no third-party code in the
  installed package to audit beyond claudron itself.

## Scope

Things that are **not** vulnerabilities in claudron:

- Claude's own rate limits, billing, or availability.
- A schedule that does not suit you — that is a configuration question; see
  [Designing a schedule](https://locngoduc.github.io/claudron/schedules/).
- The `claude` CLI's behaviour, which claudron only invokes.
