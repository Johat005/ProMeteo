"""Shared utilities: errors, config, I/O, logging and time arithmetic.

These tests are deliberately offline and deterministic.  They document the
contracts that every higher layer relies on: stable exit codes, safe file
writes, correct month arithmetic and no credential leakage.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np

from prometeo.shared.config import (
    AppConfig,
    default_config_path,
    load_config,
    merge_cli_overrides,
    validate_config,
)
from prometeo.shared.errors import (
    ConfigurationError,
    DiagnosticError,
    DownloadError,
    ProMeteoError,
    exit_code_for,
    format_error,
)
from prometeo.shared.io import (
    atomic_output_path,
    atomic_write_bytes,
    atomic_write_text,
    checksum_matches,
    ensure_directory,
    safe_filename_component,
    sha256_file,
)
from prometeo.shared.logging import new_run_id, redact_secrets
from prometeo.shared.timeutils import (
    YearMonth,
    add_months,
    current_reference_month,
    derive_target_month,
    month_length,
    parse_year_month,
)


class ErrorTests(unittest.TestCase):
    """Verify the error/exit-code contract used by the CLI."""

    def test_exit_codes(self) -> None:
        """Diagnostic failures must return 2; everything else returns 1."""
        self.assertEqual(exit_code_for(DiagnosticError("x")), 2)
        self.assertEqual(exit_code_for(ConfigurationError("x")), 1)
        self.assertEqual(exit_code_for(ValueError("x")), 1)

    def test_format_includes_hint(self) -> None:
        """Hints must be visible so errors are actionable."""
        text = format_error(DownloadError("falló", hint="revise la red"))
        self.assertIn("falló", text)
        self.assertIn("revise la red", text)

    def test_format_redacts_credentials(self) -> None:
        """Secrets must never survive error formatting."""
        # Inline assignments such as key=... and token: ... are redacted.
        text = format_error(ConfigurationError("key=abc123 token: xyz"))
        self.assertNotIn("abc123", text)
        self.assertNotIn("xyz", text)
        self.assertIn("***", text)

        # URL userinfo is redacted while scheme and host remain readable.
        url = format_error(ProMeteoError("https://user:secret@example.org/api"))
        self.assertNotIn("secret", url)
        self.assertIn("***", url)


class YearMonthTests(unittest.TestCase):
    """Verify parsing, formatting and calendar arithmetic."""

    def test_parse_and_format(self) -> None:
        """The canonical representation is YYYY-MM."""
        value = parse_year_month("2026-11")
        self.assertEqual(value, YearMonth(2026, 11))
        self.assertEqual(str(value), "2026-11")

    def test_parse_rejects_bad_input(self) -> None:
        """The parser is strict and the dataclass rejects impossible months."""
        with self.assertRaises(ProMeteoError):
            parse_year_month("2026/11")
        with self.assertRaises(ValueError):
            YearMonth(2026, 13)

    def test_add_months_crosses_year(self) -> None:
        """Adding months must roll over year boundaries in both directions."""
        self.assertEqual(add_months(YearMonth(2026, 11), 3), YearMonth(2027, 2))
        self.assertEqual(add_months(YearMonth(2026, 1), -1), YearMonth(2025, 12))

    def test_month_length(self) -> None:
        """February length must respect leap years."""
        self.assertEqual(month_length(YearMonth(2024, 2)), 29)
        self.assertEqual(month_length(YearMonth(2025, 2)), 28)

    def test_current_reference_month_override(self) -> None:
        """The override exists for tests and must bypass the real clock."""
        self.assertEqual(
            current_reference_month(override="2026-11"), YearMonth(2026, 11)
        )

    def test_reference_month_uses_timezone(self) -> None:
        """The current month must be computed in the configured timezone."""
        self.assertEqual(
            current_reference_month(timezone="America/Bogota").month,
            datetime.now(ZoneInfo("America/Bogota")).month,
        )


class TargetMonthTests(unittest.TestCase):
    """Verify the C3S forecastMonth → target month convention."""

    def test_forecast_month_one_is_initialization(self) -> None:
        """forecastMonth=1 is the init month, not the following month."""
        init = datetime(2026, 11, 1)
        self.assertEqual(
            derive_target_month(init, 1, "c3s_forecastmonth_1_is_init"),
            YearMonth(2026, 11),
        )
        self.assertEqual(
            derive_target_month(init, 6, "c3s_forecastmonth_1_is_init"),
            YearMonth(2027, 4),
        )

    def test_numpy_datetime64_is_supported(self) -> None:
        """NetCDF times often arrive as numpy datetime64 values."""
        self.assertEqual(
            derive_target_month(
                np.datetime64("2026-11-01"), 2, "c3s_forecastmonth_1_is_init"
            ),
            YearMonth(2026, 12),
        )

    def test_numpy_integer_forecast_month_is_supported(self) -> None:
        """xarray/NetCDF leads are numpy integers, not Python ints."""
        self.assertEqual(
            derive_target_month(
                datetime(2026, 11, 1),
                np.int64(2),
                "c3s_forecastmonth_1_is_init",
            ),
            YearMonth(2026, 12),
        )

    def test_unknown_convention_is_rejected(self) -> None:
        """Unsupported conventions must fail loudly, never guess."""
        with self.assertRaises(ProMeteoError):
            derive_target_month(datetime(2026, 11, 1), 1, "unknown")

    def test_invalid_forecast_month_is_rejected(self) -> None:
        """forecastMonth starts at 1; zero is invalid."""
        with self.assertRaises(ProMeteoError):
            derive_target_month(datetime(2026, 11, 1), 0, "c3s_forecastmonth_1_is_init")


class IOTests(unittest.TestCase):
    """Verify atomic writes, checksums and filename safety."""

    def test_atomic_write_and_checksum(self) -> None:
        """Text and bytes writes must publish complete files."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "file.txt"

            # Parent directories are created automatically.
            atomic_write_text(target, "hola\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "hola\n")

            # Checksums are case-insensitive and detect wrong values.
            digest = sha256_file(target)
            self.assertTrue(checksum_matches(target, digest.upper()))
            self.assertFalse(checksum_matches(target, "0" * 64))

            # Binary writes follow the same atomic path.
            binary = Path(tmp) / "data.bin"
            atomic_write_bytes(binary, b"\x00\x01")
            self.assertEqual(binary.read_bytes(), b"\x00\x01")

    def test_atomic_write_cleans_up_on_error(self) -> None:
        """A failed write must leave neither the target nor a temp file."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            with self.assertRaises(RuntimeError):
                with atomic_output_path(target) as temporary:
                    temporary.write_text("parcial", encoding="utf-8")
                    raise RuntimeError("boom")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_safe_filename_component(self) -> None:
        """Invalid path characters and Windows device names are handled."""
        self.assertEqual(safe_filename_component("  a/b:c  "), "a_b_c")
        self.assertEqual(safe_filename_component("CON"), "_CON")
        self.assertEqual(safe_filename_component("con.txt"), "_con.txt")
        self.assertEqual(safe_filename_component(""), "_")

        # Long names are capped but keep a digest to avoid collisions.
        first = safe_filename_component("a" * 200)
        second = safe_filename_component("a" * 199 + "b")
        self.assertLessEqual(len(first), 120)
        self.assertNotEqual(first, second)

    def test_ensure_directory(self) -> None:
        """Nested directories are created idempotently."""
        with tempfile.TemporaryDirectory() as tmp:
            path = ensure_directory(Path(tmp) / "a" / "b")
            self.assertTrue(path.is_dir())


class ConfigTests(unittest.TestCase):
    """Verify configuration loading, validation and CLI overrides."""

    def test_load_config_resolves_relative_paths(self) -> None:
        """Relative paths in YAML resolve from the config file's directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Minimal catalog and directories required by validation.
            (root / "models.yaml").write_text("version: 0\nmodels: {}\n", encoding="utf-8")
            (root / "cache").mkdir()
            (root / "out").mkdir()

            config_file = root / "config.yaml"
            config_file.write_text(
                "catalog_path: models.yaml\n"
                "cache_dir: cache\n"
                "output_dir: out\n"
                "log_level: DEBUG\n",
                encoding="utf-8",
            )

            config = load_config(config_file)
            self.assertEqual(config.catalog_path, root / "models.yaml")
            self.assertEqual(config.cache_dir, root / "cache")
            self.assertEqual(config.output_dir, root / "out")
            self.assertEqual(config.log_level, "DEBUG")

    def test_missing_explicit_config_is_rejected(self) -> None:
        """An explicit missing --config path is a user error."""
        with self.assertRaises(ConfigurationError):
            load_config(Path("/does/not/exist.yaml"))

    def test_unknown_key_is_rejected(self) -> None:
        """Typos in configuration keys must not be silently ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models.yaml").write_text("version: 0\nmodels: {}\n", encoding="utf-8")
            config_file = root / "config.yaml"
            config_file.write_text("unknown: 1\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(config_file)

    def test_null_required_path_is_rejected(self) -> None:
        """Null path fields must raise a config error, not a TypeError."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models.yaml").write_text("version: 0\nmodels: {}\n", encoding="utf-8")
            config_file = root / "config.yaml"
            config_file.write_text("cache_dir: null\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(config_file)

            # Direct AppConfig construction is validated the same way.
            with self.assertRaises(ConfigurationError):
                validate_config(AppConfig(catalog_path=None))  # type: ignore[arg-type]

    def test_string_boolean_is_rejected(self) -> None:
        """A quoted YAML boolean such as "false" is truthy and must be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "models.yaml").write_text("version: 0\nmodels: {}\n", encoding="utf-8")
            config_file = root / "config.yaml"
            config_file.write_text('json_output: "false"\n', encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(config_file)

    def test_merge_cli_overrides_ignores_none(self) -> None:
        """Flags not supplied by the user are None and must be ignored."""
        config = AppConfig()
        merged = merge_cli_overrides(config, {"log_level": None, "force": True})
        self.assertTrue(merged.force)
        self.assertEqual(merged.log_level, config.log_level)

    def test_invalid_timezone_is_rejected(self) -> None:
        """Invalid IANA names are configuration errors."""
        with self.assertRaises(ConfigurationError):
            validate_config(AppConfig(timezone="Not/AZone"))

    def test_default_config_path_honors_environment(self) -> None:
        """PROMETEO_CONFIG overrides the default user-level path."""
        with mock.patch.dict(os.environ, {"PROMETEO_CONFIG": "/tmp/custom.yaml"}):
            self.assertEqual(default_config_path(), Path("/tmp/custom.yaml"))


class LoggingTests(unittest.TestCase):
    """Verify secret redaction and run identifiers."""

    def test_redact_secrets(self) -> None:
        """Credential keys and URL userinfo are masked recursively."""
        redacted = redact_secrets(
            {
                "CDSAPI_KEY": "secret",
                "nested": {"token": "abc"},
                "url": "https://user:pass@example.org/api",
                "centre": "ecmwf",
            }
        )
        self.assertEqual(redacted["CDSAPI_KEY"], "***")
        self.assertEqual(redacted["nested"]["token"], "***")
        self.assertEqual(redacted["url"], "https://user:***@example.org/api")
        self.assertEqual(redacted["centre"], "ecmwf")

    def test_run_id(self) -> None:
        """Run ids are short enough for log correlation."""
        self.assertEqual(len(new_run_id()), 8)


if __name__ == "__main__":
    unittest.main()
