#!/usr/bin/env bash
# Build a .deb for claudron.
#
#     packaging/build-deb.sh [--revision N] [--outdir DIR]
#
# claudron is pure Python with no dependencies, so the package is a plain
# `all`-architecture install into /usr/lib/python3/dist-packages plus a
# launcher, a man page and shell completions. That is simple enough to assemble
# with dpkg-deb directly, which means this script runs anywhere dpkg is
# installed - including on a developer machine, so the package that CI ships is
# the same one you can build and inspect locally.
#
# For a Launchpad PPA you would need a signed Debian *source* package instead;
# see RELEASING.md, which is honest about what that involves.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
revision=1
outdir="$root/dist"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --revision) revision="$2"; shift 2 ;;
        --outdir) outdir="$2"; shift 2 ;;
        -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/src/claudron/__init__.py")"
if [[ -z "$version" ]]; then
    echo "could not read __version__ from src/claudron/__init__.py" >&2
    exit 1
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

install -d "$stage/DEBIAN"
install -d "$stage/usr/bin"
install -d "$stage/usr/lib/python3/dist-packages/claudron"
install -d "$stage/usr/share/man/man1"
install -d "$stage/usr/share/doc/claudron"
install -d "$stage/usr/share/bash-completion/completions"
install -d "$stage/usr/share/zsh/vendor-completions"
install -d "$stage/usr/share/fish/vendor_completions.d"

install -m 644 "$root"/src/claudron/*.py "$stage/usr/lib/python3/dist-packages/claudron/"

cat > "$stage/usr/bin/claudron" <<'LAUNCHER'
#!/usr/bin/python3
import sys

from claudron.cli import main

sys.exit(main())
LAUNCHER
chmod 755 "$stage/usr/bin/claudron"

gzip -9nc "$root/packaging/claudron.1" > "$stage/usr/share/man/man1/claudron.1.gz"

for shell in bash zsh fish; do
    case "$shell" in
        bash) target="$stage/usr/share/bash-completion/completions/claudron" ;;
        zsh)  target="$stage/usr/share/zsh/vendor-completions/_claudron" ;;
        fish) target="$stage/usr/share/fish/vendor_completions.d/claudron.fish" ;;
    esac
    PYTHONPATH="$root/src" python3 -m claudron completion "$shell" > "$target"
    chmod 644 "$target"
done

install -m 644 "$root/README.md" "$stage/usr/share/doc/claudron/README.md"

cat > "$stage/usr/share/doc/claudron/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: claudron
Source: https://github.com/locngoduc/claudron

Files: *
Copyright: 2026 claudron contributors
License: MIT

License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
COPYRIGHT

{
    echo "claudron ($version-$revision) unstable; urgency=medium"
    echo
    echo "  * Release $version. See /usr/share/doc/claudron/README.md and"
    echo "    https://github.com/locngoduc/claudron/blob/main/CHANGELOG.md"
    echo
    echo " -- claudron contributors <noreply@github.com>  $(date -uR)"
} | gzip -9nc > "$stage/usr/share/doc/claudron/changelog.Debian.gz"

# Normalise modes: mktemp and the umask leave 0700/0775 behind, and Debian
# expects 0755 directories and 0644 files with exactly one executable.
find "$stage" -type d -exec chmod 755 {} +
find "$stage" -type f -exec chmod 644 {} +
chmod 755 "$stage/usr/bin/claudron"

size="$(du -ks "$stage/usr" | cut -f1)"
cat > "$stage/DEBIAN/control" <<CONTROL
Package: claudron
Version: $version-$revision
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.11)
Installed-Size: $size
Maintainer: claudron contributors <noreply@github.com>
Homepage: https://github.com/locngoduc/claudron
Description: align Claude usage windows with your working day
 A Claude usage window opens on the first message sent while no window is
 open and lasts five hours, so the times you send that first message decide
 when your limits reset.
 .
 claudron sends that message at times you choose, reads real usage from
 Claude Code's local transcripts, and warns when an accidental message would
 open a window early and push every later reset out of alignment. It can also
 search every legal arrangement of anchor times and propose the one that fits
 your day.
 .
 Pure Python with no runtime dependencies.
CONTROL

( cd "$stage" && find usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums )
chmod 644 "$stage/DEBIAN/md5sums" "$stage/DEBIAN/control"

mkdir -p "$outdir"
package="$outdir/claudron_${version}-${revision}_all.deb"
dpkg-deb --root-owner-group --build "$stage" "$package" >/dev/null
echo "$package"
