"""Tests for the one-time Linux/macOS setup script.

The whole module is skipped on Windows when ``bash`` is not available; the
native Windows setup is documented in ``docs/windows-guide.md``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is required to test setup.sh")
class SetupScriptTests(unittest.TestCase):
    """Validate the script without creating a real virtualenv."""

    def test_script_exists_and_is_executable(self) -> None:
        self.assertTrue(SETUP.is_file())
        if os.name == "posix":
            self.assertTrue(os.access(SETUP, os.X_OK))

    def test_shell_syntax(self) -> None:
        result = subprocess.run(
            [BASH, "-n", str(SETUP)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help(self) -> None:
        result = subprocess.run(
            [BASH, str(SETUP), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--recreate", result.stdout)
        self.assertIn("--configure", result.stdout)

    def test_unknown_option_fails(self) -> None:
        result = subprocess.run(
            [BASH, str(SETUP), "--not-an-option"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Opción desconocida", result.stderr)


if __name__ == "__main__":
    unittest.main()
