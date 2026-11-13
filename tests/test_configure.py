"""Tests for the interactive CDS credential helper."""

from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prometeo.cli.main import main


class ConfigureCommandTests(unittest.TestCase):
    """Verify `prometeo configure` without touching the real ~/.cdsapirc."""

    def _run(
        self,
        argv: list[str],
        input_side_effect: list[str],
        getpass_side_effect: list[str],
        *,
        isatty: bool = True,
    ) -> tuple[int, str, str]:
        """Run the CLI with mocked prompts and captured output."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = mock.Mock()
        stdin.isatty.return_value = isatty

        with (
            mock.patch("builtins.input", side_effect=input_side_effect),
            mock.patch(
                "prometeo.cli.main.getpass.getpass",
                side_effect=getpass_side_effect,
            ),
            mock.patch("prometeo.cli.main.sys.stdin", stdin),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_writes_credentials_with_600_permissions(self) -> None:
        """The helper writes url/key and never prints the key."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                code, out, err = self._run(
                    ["configure"],
                    [""],
                    ["12345:secret-key"],
                )

            self.assertEqual(code, 0, err)
            path = Path(tmp) / ".cdsapirc"
            self.assertTrue(path.is_file())
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "url: https://cds.climate.copernicus.eu/api\n"
                "key: 12345:secret-key\n",
            )
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn("secret-key", out)
            self.assertNotIn("secret-key", err)

    def test_declining_overwrite_keeps_existing_file(self) -> None:
        """Answering no must leave the existing credentials untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cdsapirc"
            path.write_text("old\n", encoding="utf-8")

            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                code, out, err = self._run(["configure"], ["n"], [])

            self.assertEqual(code, 0, err)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")

    def test_accepting_overwrite_replaces_file(self) -> None:
        """Answering yes must replace the file with 600 permissions."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".cdsapirc"
            path.write_text("old\n", encoding="utf-8")

            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                code, out, err = self._run(
                    ["configure"],
                    ["s", ""],
                    ["12345:new-key"],
                )

            self.assertEqual(code, 0, err)
            content = path.read_text(encoding="utf-8")
            self.assertIn("key: 12345:new-key", content)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_rejects_key_without_colon(self) -> None:
        """CDS keys use UID:CLAVE; a malformed key must not be written."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                code, out, err = self._run(
                    ["configure"],
                    [""],
                    ["not-a-cds-key"],
                )

            self.assertEqual(code, 1)
            self.assertIn("UID:CLAVE", err)
            self.assertFalse((Path(tmp) / ".cdsapirc").exists())

    def test_rejects_non_http_url(self) -> None:
        """The URL must be an HTTP(S) endpoint."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                code, out, err = self._run(
                    ["configure"],
                    ["ftp://example.com"],
                    ["12345:key"],
                )

            self.assertEqual(code, 1)
            self.assertIn("http://", err)
            self.assertFalse((Path(tmp) / ".cdsapirc").exists())

    def test_requires_interactive_terminal(self) -> None:
        """Running from cron/systemd must fail instead of hanging."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("pathlib.Path.home", return_value=Path(tmp)):
                code, out, err = self._run(
                    ["configure"],
                    [],
                    [],
                    isatty=False,
                )

            self.assertEqual(code, 1)
            self.assertIn("terminal interactiva", err)
            self.assertFalse((Path(tmp) / ".cdsapirc").exists())


if __name__ == "__main__":
    unittest.main()
