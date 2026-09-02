# Install

claudron is pure Python with **no runtime dependencies**. It needs Python 3.11
or newer and the [`claude`](https://claude.com/claude-code) CLI, already signed
in — claudron never handles your credentials, it shells out to the CLI you have
already authenticated.

## pipx (recommended)

Keeps claudron in its own virtual environment while putting `claudron` on your
`PATH`.

```bash
pipx install claudron
```

Upgrade, and remove:

```bash
pipx upgrade claudron
pipx uninstall claudron
```

## uv

```bash
uv tool install claudron
uv tool upgrade claudron
```

## pip

```bash
python3 -m pip install --user claudron
```

Installing into your system Python with `pip` is fine here because there are no
dependencies to conflict with, but on Debian and Ubuntu you will need
`--break-system-packages` or a virtual environment. Prefer `pipx`, or the
`.deb` below.

## Ubuntu and Debian

### From the apt repository

!!! warning "Available from the first tagged release onwards"
    The repository is published by CI from the `.deb` attached to each release.
    Until `v0.1.0` is tagged and the signing key is configured
    ([RELEASING.md](https://github.com/locngoduc/claudron/blob/main/RELEASING.md)),
    these URLs return 404 — use `pipx` in the meantime.

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://locngoduc.github.io/claudron/apt/claudron.gpg \
  | sudo tee /etc/apt/keyrings/claudron.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/claudron.gpg] https://locngoduc.github.io/claudron/apt ./" \
  | sudo tee /etc/apt/sources.list.d/claudron.list > /dev/null
sudo apt update
sudo apt install claudron
```

`apt upgrade` then keeps it current like any other package.

!!! note "Requires Ubuntu 24.04 or newer"
    The package declares `Depends: python3 (>= 3.11)`. Ubuntu 22.04 ships
    Python 3.10, so use `pipx` there instead.

### From a downloaded .deb

Every release attaches one. Download it, check it against the published
checksums, and install:

```bash
VERSION=0.1.0
BASE=https://github.com/locngoduc/claudron/releases/download/v$VERSION
curl -fLO $BASE/claudron_${VERSION}-1_all.deb
curl -fLO $BASE/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
sudo apt install ./claudron_${VERSION}-1_all.deb
```

Removing it:

```bash
sudo apt remove claudron
```

The package installs the module into `/usr/lib/python3/dist-packages/claudron`,
a launcher at `/usr/bin/claudron`, the manual page, and completions for bash,
zsh and fish.

!!! info "Not in the official Ubuntu archive"
    claudron is not in Ubuntu's own repositories and does not claim to be.
    Getting there requires Debian packaging and a sponsor, which is a long
    process; the apt repository above is the honest alternative, and it is
    hosted on this same documentation site.

## From source

```bash
git clone https://github.com/locngoduc/claudron
cd claudron
python3 -m pip install .
```

Or build the Debian package yourself — the same script CI runs, so you can
inspect exactly what would be installed:

```bash
packaging/build-deb.sh
dpkg-deb -c dist/claudron_*_all.deb
```

## Shell completion

The `.deb` installs completions automatically. For every other method, generate
them from the CLI itself — they are derived from the argument parser, so they
cannot drift out of date:

=== "bash"

    ```bash
    claudron completion bash | sudo tee /usr/share/bash-completion/completions/claudron > /dev/null
    ```

    Or, without root, add to `~/.bashrc`:

    ```bash
    eval "$(claudron completion bash)"
    ```

=== "zsh"

    ```bash
    claudron completion zsh > ~/.zfunc/_claudron
    ```

    With `fpath=(~/.zfunc $fpath)` and `autoload -Uz compinit && compinit` in
    your `~/.zshrc`.

=== "fish"

    ```bash
    claudron completion fish > ~/.config/fish/completions/claudron.fish
    ```

## Verify the install

```bash
claudron --version
claudron doctor
```

`doctor` checks that the `claude` CLI is present, probes which flags your build
of it supports, reads your transcripts, and reports whether your schedule is
valid.

## Then

```bash
claudron suggest --start-at 12:00 --sleep 23:00-05:00 --timezone Asia/Ho_Chi_Minh
claudron plan
claudron install
```
