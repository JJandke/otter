"""Exercise setup in disposable directories; never install packages or browsers."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Spaces exercise the shell quoting used for project paths.
        self.root = Path(self.temp.name) / 'synthetic checkout'
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/setup',
                        self.root / 'scripts/setup')
        self.pin = '.'.join(map(str, sys.version_info[:3]))
        (self.root / '.python-version').write_text(self.pin + '\n')
        self.calls = self.root / 'calls'
        bin_dir = self.root / 'fake-bin'
        bin_dir.mkdir()
        self.wrapper = bin_dir / 'synthetic-python'
        self.executable(self.wrapper,
            '#!/bin/sh\n'
            'if test "$1" = "-m"; then\n'
            '  printf "browser %s\\n" "$*" >> "$SETUP_CALLS"\n'
            '  exit 0\n'
            'fi\n'
            f'exec {shlex.quote(sys.executable)} "$@"\n')
        self.executable(bin_dir / 'uv',
            '#!/bin/sh\n'
            'printf "uv %s\\n" "$*" >> "$SETUP_CALLS"\n'
            'if test "$1" = "venv"; then\n'
            '  mkdir -p .venv/bin\n'
            '  cp "$SETUP_WRAPPER" .venv/bin/python\n'
            'fi\n')
        self.executable(bin_dir / 'git',
            '#!/bin/sh\nprintf "git %s\\n" "$*" >> "$SETUP_CALLS"\n')
        self.env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                    'SETUP_CALLS': str(self.calls), 'SETUP_WRAPPER': str(self.wrapper)}

    def executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def existing_environment(self):
        (self.root / '.venv/bin').mkdir(parents=True)
        shutil.copy2(self.wrapper, self.root / '.venv/bin/python')

    def run_setup(self):
        # Start outside the project; setup must resolve its own directory.
        return subprocess.run(['sh', str(self.root / 'scripts/setup')],
                              cwd=self.temp.name, env=self.env,
                              capture_output=True, text=True)

    def test_matching_environment_is_reused_without_recreating_it(self):
        self.existing_environment()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls.read_text()
        self.assertNotIn('uv venv', calls)
        self.assertIn('uv pip sync --python .venv/bin/python requirements.lock', calls)
        self.assertIn('browser -m playwright install chromium', calls)
        self.assertIn('git config --local core.hooksPath .githooks', calls)

    def test_wrong_version_stops_before_installation_and_preserves_environment(self):
        self.existing_environment()
        (self.root / '.python-version').write_text('0.0.0\n')
        before = (self.root / '.venv/bin/python').read_bytes()
        result = self.run_setup()
        self.assertEqual(result.returncode, 6)
        self.assertIn('not Python 0.0.0', result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual((self.root / '.venv/bin/python').read_bytes(), before)

    def test_missing_environment_uses_the_same_configured_pin(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('uv venv --python ' + self.pin, self.calls.read_text())

    def test_unusable_environment_is_not_replaced(self):
        (self.root / '.venv').mkdir()
        sentinel = self.root / '.venv/preserve'
        sentinel.write_text('synthetic local work')
        result = self.run_setup()
        self.assertEqual(result.returncode, 6)
        self.assertFalse(self.calls.exists())
        self.assertEqual(sentinel.read_text(), 'synthetic local work')
