"""CLI parsing, catalog commands and download dry-run.

The tests capture stdout/stderr and use a temporary cache directory.  They never
contact CDS: ``--dry-run`` stops before the client is constructed.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prometeo.cli.main import main
from tests.netcdf_fixtures import write_forecast


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI with captured stdout/stderr and return all three values."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class CatalogCommandTests(unittest.TestCase):
    """Verify list-models and describe-model."""

    def test_list_models_json(self) -> None:
        """The catalog must expose the Sprint 1 model as JSON."""
        code, out, err = run_cli(["list-models", "--json"])
        self.assertEqual(code, 0, err)

        payload = json.loads(out)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], "ecmwf-seas5")

    def test_describe_model_json(self) -> None:
        """describe-model must include variables, products and years."""
        code, out, err = run_cli(["describe-model", "ecmwf-seas5", "--json"])
        self.assertEqual(code, 0, err)

        payload = json.loads(out)
        self.assertEqual(payload["centre"], "ecmwf")
        self.assertEqual(payload["system"], "51")
        self.assertEqual(payload["products"]["hindcast"], "monthly_mean")
        self.assertEqual(payload["products"]["forecast"], "ensemble_mean")
        self.assertEqual(payload["hindcast_years"], [1981, 2016])
        self.assertIn("tprate", payload["variables"])

    def test_describe_unknown_model_fails(self) -> None:
        """Unknown models must return exit code 1 with an actionable message."""
        code, out, err = run_cli(["describe-model", "does-not-exist"])
        self.assertEqual(code, 1)
        self.assertIn("Modelo desconocido", err)

    def test_list_models_human_output(self) -> None:
        """Without --json, the command prints one compact line per model."""
        code, out, err = run_cli(["list-models"])
        self.assertEqual(code, 0, err)
        self.assertIn("ecmwf-seas5", out)


class DownloadCommandTests(unittest.TestCase):
    """Verify the download command's flags and dry-run output."""

    def test_download_default_destination_is_cwd(self) -> None:
        """Without --dest, files are placed in the current working directory."""
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                # Avoid picking up a real user config from ~/.config.
                with mock.patch.dict(
                    os.environ,
                    {"PROMETEO_CONFIG": str(Path(tmp) / "no-config.yaml")},
                ):
                    code, out, err = run_cli(
                        [
                            "download",
                            "--dry-run",
                            "--json",
                            "--only",
                            "forecast",
                            "--variables",
                            "t2m",
                            "--leads",
                            "1",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertEqual(
                Path(payload["destination"]).resolve(), Path(tmp).resolve()
            )

            target = Path(payload["results"][0]["target"]).resolve()
            self.assertTrue(target.is_relative_to(Path(tmp).resolve()))

    def test_download_dest_flag(self) -> None:
        """--dest selects an explicit download directory."""
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "downloads"
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--only",
                    "forecast",
                    "--variables",
                    "t2m",
                    "--leads",
                    "1",
                    "--dest",
                    str(destination),
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(Path(payload["destination"]), destination)
            target = Path(payload["results"][0]["target"])
            self.assertTrue(target.is_relative_to(destination))

    def test_download_dry_run_json(self) -> None:
        """A dry run must return the two products without network access."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                ["download", "--dry-run", "--json", "--cache-dir", tmp]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertRegex(payload["reference_month"], r"^\d{4}-\d{2}$")
            self.assertEqual(len(payload["results"]), 2)

            # Hindcast first, forecast second (service order).
            hindcast = payload["results"][0]
            forecast = payload["results"][1]
            self.assertEqual(hindcast["payload"]["product_type"], ["monthly_mean"])
            self.assertEqual(len(hindcast["payload"]["year"]), 36)
            self.assertEqual(len(hindcast["payload"]["month"]), 12)
            self.assertEqual(forecast["payload"]["product_type"], ["ensemble_mean"])
            self.assertEqual(len(forecast["payload"]["year"]), 1)

    def test_download_dry_run_separate(self) -> None:
        """--separate must produce one request per variable and product."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--separate",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(len(payload["results"]), 10)
            # Each request now contains exactly one CDS variable.
            for result in payload["results"]:
                self.assertEqual(len(result["payload"]["variable"]), 1)

    def test_download_region_payload(self) -> None:
        """--region must appear as an explicit CDS area."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--region",
                    "10,-75,-5,-66",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["region"], "10,-75,-5,-66")
            self.assertEqual(
                payload["results"][0]["payload"]["area"],
                [10.0, -75.0, -5.0, -66.0],
            )

    def test_download_leads_payload(self) -> None:
        """--leads must be parsed into the CDS leadtime_month list."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--leads",
                    "1,3,5",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["leads"], [1, 3, 5])
            self.assertEqual(
                payload["results"][0]["payload"]["leadtime_month"],
                ["1", "3", "5"],
            )

    def test_download_only_forecast_and_variable(self) -> None:
        """--only and --variables allow a single small live request."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--only",
                    "forecast",
                    "--variables",
                    "t2m",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["only"], "forecast")
            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(
                payload["results"][0]["payload"]["product_type"],
                ["ensemble_mean"],
            )
            self.assertEqual(
                payload["results"][0]["payload"]["variable"],
                ["2m_temperature"],
            )

    def test_download_variable_subset(self) -> None:
        """A comma-separated subset is sent as multiple CDS variables."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--variables",
                    "t2m,tprate",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(len(payload["results"]), 2)
            for result in payload["results"]:
                self.assertEqual(
                    set(result["payload"]["variable"]),
                    {"2m_temperature", "total_precipitation"},
                )

    def test_download_only_hindcast(self) -> None:
        """--only hindcast skips the forecast request."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--only",
                    "hindcast",
                    "--variables",
                    "t2m",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(
                payload["results"][0]["payload"]["product_type"],
                ["monthly_mean"],
            )

    def test_download_unknown_variable_fails(self) -> None:
        """Unknown variables fail before any network request."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--variables",
                    "rh2m",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("Variable desconocida", err)

    def test_download_per_product_leads(self) -> None:
        """--hindcast-leads and --forecast-leads override independently."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--json",
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "6",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["hindcast_leads"], [1, 2])
            self.assertEqual(payload["forecast_leads"], [6])
            self.assertEqual(
                payload["results"][0]["payload"]["leadtime_month"], ["1", "2"]
            )
            self.assertEqual(
                payload["results"][1]["payload"]["leadtime_month"], ["6"]
            )

    def test_download_invalid_region_fails(self) -> None:
        """Invalid areas return exit code 1 before any download is attempted."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                [
                    "download",
                    "--dry-run",
                    "--region",
                    "not-an-area",
                    "--cache-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("Área inválida", err)

    def test_download_human_dry_run(self) -> None:
        """Human output shows the request and destination without JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                ["download", "--dry-run", "--cache-dir", tmp]
            )
            self.assertEqual(code, 0, err)
            self.assertIn("Mes de referencia", out)
            self.assertIn("[dry-run]", out)
            self.assertIn("petición:", out)


class InspectCommandTests(unittest.TestCase):
    """Verify the inspect command on a synthetic NetCDF file."""

    def test_inspect_json(self) -> None:
        """inspect must report dimensions, variables and layout as JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            code, out, err = run_cli(["inspect", str(path), "--json"])
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["dimensions"]["forecastMonth"], 2)
            self.assertEqual(payload["variables"]["t2m"]["units"], "K")
            self.assertEqual(payload["layout"]["member"], "number")
            self.assertEqual(payload["members"], [0])

    def test_inspect_applies_selections(self) -> None:
        """inspect must honor variable, lead and area selections."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            code, out, err = run_cli(
                [
                    "inspect",
                    str(path),
                    "--json",
                    "--variables",
                    "t2m",
                    "--leads",
                    "2",
                    "--region",
                    "4,0,2,3",
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["dimensions"]["forecastMonth"], 1)
            self.assertEqual(payload["leads"], [2])
            self.assertEqual(payload["latitude"]["count"], 2)
            self.assertEqual(payload["longitude"]["count"], 3)

    def test_inspect_human_output(self) -> None:
        """Without --json, inspect prints a readable summary."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            code, out, err = run_cli(["inspect", str(path)])
            self.assertEqual(code, 0, err)
            self.assertIn("Dimensiones:", out)
            self.assertIn("t2m", out)
            self.assertIn("Plazos:", out)


class ValidateCommandTests(unittest.TestCase):
    """Verify the validate command and its exit codes."""

    def test_validate_valid_file(self) -> None:
        """A valid file returns 0 and a JSON report with no errors."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            code, out, err = run_cli(
                [
                    "validate",
                    str(path),
                    "--variables",
                    "t2m",
                    "--leads",
                    "1-2",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)

            payload = json.loads(out)
            self.assertEqual(payload["counts"]["errors"], 0)
            self.assertEqual(payload["exit_code"], 0)

    def test_validate_units_error_exits_2(self) -> None:
        """A units mismatch is a blocking validation error (exit 2)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc", units="degC")
            code, out, err = run_cli(
                [
                    "validate",
                    str(path),
                    "--variables",
                    "t2m",
                    "--leads",
                    "1-2",
                    "--json",
                ]
            )
            self.assertEqual(code, 2)
            payload = json.loads(out)
            self.assertEqual(payload["counts"]["errors"], 1)
            self.assertTrue(
                any(
                    finding["check_id"] == "FILE-UNITS"
                    for finding in payload["findings"]
                )
            )

    def test_validate_unreadable_file_exits_2(self) -> None:
        """An unreadable file becomes a FILE-READ finding, not a traceback."""
        code, out, err = run_cli(
            ["validate", "/does/not/exist.nc", "--json"]
        )
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["findings"][0]["check_id"], "FILE-READ")

    def test_validate_human_output(self) -> None:
        """Without --json, validate prints findings and a summary."""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            code, out, err = run_cli(
                ["validate", str(path), "--variables", "t2m", "--leads", "1-2"]
            )
            self.assertEqual(code, 0, err)
            self.assertIn("Resumen:", out)


if __name__ == "__main__":
    unittest.main()
