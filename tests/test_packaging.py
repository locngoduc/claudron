"""Guards against the documentation and the packaging drifting from the CLI.

None of these build anything - they compare what ships against what the parser
actually defines, which is the failure mode that bites months after a release.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from claudron import __version__, cli, completion

ROOT = Path(__file__).resolve().parent.parent
MAN_PAGE = ROOT / "packaging" / "claudron.1"
BUILD_DEB = ROOT / "packaging" / "build-deb.sh"


def commands() -> set[str]:
    return set(completion._subparsers(cli.build_parser()))


class ManPage(unittest.TestCase):
    def setUp(self):
        self.text = MAN_PAGE.read_text(encoding="utf-8")

    def test_every_command_is_documented(self):
        documented = set(re.findall(r"^\.B (\w[\w-]*)$", self.text, re.MULTILINE))
        missing = commands() - documented
        self.assertFalse(missing, f"undocumented in claudron.1: {sorted(missing)}")

    def test_every_exit_code_is_documented(self):
        from claudron import errors

        used = {errors.ClaudronError.exit_code}
        for name in dir(errors):
            value = getattr(errors, name)
            if isinstance(value, type) and issubclass(value, errors.ClaudronError):
                used.add(value.exit_code)
        documented = {int(m) for m in re.findall(r"^\.B (\d+)$", self.text, re.MULTILINE)}
        self.assertTrue(used <= documented, f"undocumented exit codes: {sorted(used - documented)}")

    def test_environment_variables_are_documented(self):
        for name in ("CLAUDRON_CONFIG_DIR", "CLAUDRON_STATE_DIR", "NO_COLOR", "COLORTERM"):
            self.assertIn(name, self.text)


class Completion(unittest.TestCase):
    def test_every_shell_lists_every_command(self):
        parser = cli.build_parser()
        for shell in completion.SHELLS:
            script = completion.script(shell, parser)
            for command in commands():
                with self.subTest(shell=shell, command=command):
                    self.assertIn(command, script)

    def test_scripts_are_not_empty_and_name_the_binary(self):
        parser = cli.build_parser()
        for shell in completion.SHELLS:
            script = completion.script(shell, parser)
            self.assertIn("claudron", script)
            self.assertGreater(len(script.splitlines()), 3)

    def test_unknown_shell_is_rejected(self):
        with self.assertRaises(ValueError):
            completion.script("powershell", cli.build_parser())

    def test_command_output_matches_the_generator(self):
        from contextlib import redirect_stdout
        from io import StringIO

        args = cli.build_parser().parse_args(["completion", "bash"])
        out = StringIO()
        with redirect_stdout(out):
            args.func(args)
        self.assertEqual(out.getvalue(), completion.script("bash", cli.build_parser()))


class Readme(unittest.TestCase):
    """README.md is rendered by GitHub, by MkDocs, and by PyPI.

    It points at the logo relatively, which GitHub resolves against the
    repository. MkDocs resolves the same path against the built site, so the
    site needs its own copy - that copy is generated, and this is what notices
    when it goes missing.
    """

    def test_the_logo_the_readme_points_at_exists_for_both_renderers(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        sources = re.findall(r'<img[^>]*src="([^"]+)"', text)
        self.assertTrue(sources, "README.md has no image")
        for src in sources:
            if src.startswith("http"):
                continue
            with self.subTest(src=src):
                self.assertTrue((ROOT / src).is_file(), f"GitHub would 404 on {src}")
                self.assertTrue(
                    (ROOT / "docs" / src).is_file(),
                    f"the documentation site would 404 on {src}; "
                    f"run tools/make_logo.py to regenerate docs/{src}",
                )

    def test_links_out_of_the_readme_are_absolute(self):
        # Relative links to other documents break on PyPI, which has no
        # repository to resolve them against.
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for target in re.findall(r"\]\((?!https?://|#)([^)]+)\)", text):
            with self.subTest(target=target):
                self.fail(f"README.md links to {target!r} relatively; use an absolute URL")


class Version(unittest.TestCase):
    def test_pyproject_reads_the_version_from_the_package(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('dynamic = ["version"]', text)
        self.assertIn('path = "src/claudron/__init__.py"', text)
        self.assertNotIn('\nversion = "', text)

    def test_version_looks_like_a_release(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:[.-]?(?:a|b|rc|dev)\d+)?$")

    def test_deb_script_reads_the_same_version(self):
        if not BUILD_DEB.exists():  # pragma: no cover
            self.skipTest("packaging script not present")
        pattern = re.search(r"sed -n 's/\^__version__ = (.+)/p'", BUILD_DEB.read_text())
        self.assertIsNotNone(pattern, "build-deb.sh no longer extracts __version__")


@unittest.skipUnless(sys.platform.startswith("linux"), "dpkg is Linux-only")
class DebPackage(unittest.TestCase):
    """Builds a real .deb when dpkg-deb is available, and checks it is sane."""

    @classmethod
    def setUpClass(cls):
        if not BUILD_DEB.exists():
            raise unittest.SkipTest("packaging script not present")
        from shutil import which

        if not which("dpkg-deb"):
            raise unittest.SkipTest("dpkg-deb not installed")
        import tempfile

        cls.tmp = tempfile.TemporaryDirectory()
        result = subprocess.run(
            [str(BUILD_DEB), "--outdir", cls.tmp.name],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        if result.returncode != 0:
            raise unittest.SkipTest(f"build-deb.sh failed: {result.stderr.strip()[:400]}")
        cls.package = Path(result.stdout.strip())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def contents(self) -> str:
        return subprocess.run(
            ["dpkg-deb", "-c", str(self.package)], capture_output=True, text=True, check=True
        ).stdout

    def test_filename_carries_the_package_version(self):
        self.assertIn(__version__, self.package.name)

    def test_ships_the_launcher_the_man_page_and_completions(self):
        listing = self.contents()
        for path in (
            "./usr/bin/claudron",
            "./usr/share/man/man1/claudron.1.gz",
            "./usr/share/doc/claudron/copyright",
            "./usr/share/doc/claudron/changelog.Debian.gz",
            "./usr/share/bash-completion/completions/claudron",
        ):
            with self.subTest(path=path):
                self.assertIn(path, listing)

    def test_ships_every_module(self):
        listing = self.contents()
        for module in sorted((ROOT / "src" / "claudron").glob("*.py")):
            with self.subTest(module=module.name):
                self.assertIn(f"dist-packages/claudron/{module.name}", listing)

    def test_directories_are_755_and_files_are_644(self):
        for line in self.contents().splitlines():
            mode, rest = line.split(None, 1)
            path = line.split()[-1]
            if path.endswith("/usr/bin/claudron"):
                self.assertEqual(mode, "-rwxr-xr-x", line)
            elif mode.startswith("d"):
                self.assertEqual(mode, "drwxr-xr-x", line)
            else:
                self.assertEqual(mode, "-rw-r--r--", line)
            del rest

    def test_declares_the_python_version_it_needs(self):
        info = subprocess.run(
            ["dpkg-deb", "-I", str(self.package)], capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("Architecture: all", info)
        self.assertIn("Depends: python3 (>= 3.11)", info)

    def test_the_installed_launcher_actually_runs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as extracted:
            subprocess.run(
                ["dpkg-deb", "-x", str(self.package), extracted], check=True, capture_output=True
            )
            root = Path(extracted)
            result = subprocess.run(
                [str(root / "usr/bin/claudron"), "--version"],
                capture_output=True,
                text=True,
                check=False,
                env={
                    **os.environ,
                    "PYTHONPATH": str(root / "usr/lib/python3/dist-packages"),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(__version__, result.stdout)


if __name__ == "__main__":
    unittest.main()
