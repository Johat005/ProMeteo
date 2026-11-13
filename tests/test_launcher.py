"""Tests for the no-install ``python3 prometeo`` launcher."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "prometeo"


class LauncherTests(unittest.TestCase):
    """Run the launcher in a subprocess, exactly as a user would."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        # Force the current interpreter so the test does not depend on .venv.
        env = dict(os.environ)
        env["PROMETEO_NO_VENV"] = "1"
        return subprocess.run(
            [sys.executable, str(LAUNCHER), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_version(self) -> None:
        """`python3 prometeo --version` must work without PYTHONPATH."""
        result = self._run("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prometeo", result.stdout.lower())

    def test_help_lists_configure(self) -> None:
        """The new credential helper must appear in the command list."""
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configure", result.stdout)


if __name__ == "__main__":
    unittest.main()
