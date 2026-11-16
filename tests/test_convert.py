"""End-to-end convert command tests using local synthetic files."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prometeo.cli.main import main
from prometeo.cpt.validator import validate_cpt
from prometeo.download.service import DownloadResult
from tests.netcdf_fixtures import write_forecast, write_hindcast


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI with captured stdout/stderr."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _write_pair(root: Path, *, forecast_units: str = "K") -> tuple[Path, Path]:
    """Write a small hindcast/forecast pair with matching leads."""
    hindcast = write_hindcast(
        root / "hindcast.nc",
        inits=("1981-11-01", "1981-12-01"),
        leads=(1, 2),
        members=(0, 1),
    )
    forecast = write_forecast(
        root / "forecast.nc", init="2026-11-01", leads=(1, 2), units=forecast_units
    )
    return hindcast, forecast


def _first_data_latitude(path: Path) -> str:
    """Return the first data row's latitude token from a CPT file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("cpt:field="):
            return lines[index + 2].split()[0]
    raise AssertionError(f"No CPT block found in {path}")


class ConvertCommandTests(unittest.TestCase):
    """Verify local-file conversion, partitions and the diagnostic gate."""

    def test_convert_writes_x_and_z_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, forecast = _write_pair(root)
            out = root / "out"

            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "1-2",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["partition"], "both")
            self.assertEqual([o["partition"] for o in payload["outputs"]], ["x", "z"])
            self.assertEqual([o["blocks"] for o in payload["outputs"]], [2, 2])
            for output in payload["outputs"]:
                path = Path(output["path"])
                self.assertTrue(path.is_file())
                self.assertTrue(validate_cpt(path).valid)

    def test_latitude_order_matches_between_x_and_z(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Deliberately opposite latitude orders in the two source files.
            hindcast = write_hindcast(
                root / "hindcast.nc",
                inits=("1981-11-01",),
                leads=(1,),
                latitudes=(3.5, 2.5, 1.5),
            )
            forecast = write_forecast(
                root / "forecast.nc",
                init="2026-11-01",
                leads=(1,),
                latitudes=(1.5, 2.5, 3.5),
            )
            out = root / "out"
            code, _stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1",
                    "--forecast-leads",
                    "1",
                    "--output-dir",
                    str(out),
                ]
            )
            self.assertEqual(code, 0, stderr)
            x_path = next(out.glob("*_x_*.cpt"))
            z_path = next(out.glob("*_z_*.cpt"))
            self.assertEqual(_first_data_latitude(x_path), "3.50")
            self.assertEqual(_first_data_latitude(z_path), "3.50")

    def test_convert_x_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, _forecast = _write_pair(root)
            out = root / "out"
            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--partition",
                    "x",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual([o["partition"] for o in payload["outputs"]], ["x"])

    def test_convert_z_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _hindcast, forecast = _write_pair(root)
            out = root / "out"
            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--forecast-leads",
                    "1-2",
                    "--partition",
                    "z",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual([o["partition"] for o in payload["outputs"]], ["z"])

    def test_convert_single(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, forecast = _write_pair(root)
            out = root / "out"
            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "1-2",
                    "--partition",
                    "single",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(len(payload["outputs"]), 1)
            self.assertEqual(payload["outputs"][0]["partition"], "xz")
            self.assertEqual(payload["outputs"][0]["blocks"], 4)

    def test_mismatched_leads_warn_and_proceed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, forecast = _write_pair(root)
            out = root / "out"
            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "1",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            checks = [f["check_id"] for f in payload["diagnostics"]["findings"]]
            self.assertIn("LEADS-MISMATCH", checks)
            self.assertEqual(
                [o["blocks"] for o in payload["outputs"]], [2, 1]
            )

    def test_hindcast_init_month_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, forecast = _write_pair(root)
            out = root / "out"
            code, _stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "1-2",
                    "--hindcast-init-month",
                    "12",
                    "--partition",
                    "x",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            cpt = next(out.glob("*_x_*.cpt"))
            text = cpt.read_text(encoding="utf-8")
            lines = text.splitlines()
            # The mandatory CPT date-list line must precede the first block.
            self.assertEqual(lines[2], "cpt:T 1981-12 1982-01")
            self.assertIn("cpt:T=1981-12", text)
            self.assertIn("cpt:T=1982-01", text)

    def test_bad_units_block_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, forecast = _write_pair(root, forecast_units="degC")
            out = root / "out"
            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "1-2",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 2)
            payload = json.loads(stdout)
            self.assertEqual(payload["exit_code"], 2)
            self.assertEqual(payload["counts"]["errors"], 1)
            self.assertFalse(list(out.glob("*.cpt")))

    def test_bad_units_forced_writes_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hindcast, forecast = _write_pair(root, forecast_units="degC")
            out = root / "out"
            code, stdout, stderr = run_cli(
                [
                    "convert",
                    "--hindcast-file",
                    str(hindcast),
                    "--forecast-file",
                    str(forecast),
                    "--variables",
                    "t2m",
                    "--hindcast-leads",
                    "1-2",
                    "--forecast-leads",
                    "1-2",
                    "--force",
                    "--output-dir",
                    str(out),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertTrue(payload["forced"])
            self.assertEqual(payload["forced_errors"], 1)
            self.assertTrue(list(out.glob("*.cpt")))

    def test_auto_download_path(self) -> None:
        """convert can use the download service without real network access."""
        written = {"paths": []}

        class FakeDownloadService:
            def __init__(self, repository, destination, client_factory=None):
                self.destination = Path(destination)
                self.destination.mkdir(parents=True, exist_ok=True)

            def download_products(
                self,
                reference_month,
                *,
                model_id,
                variables,
                separate,
                area,
                leads,
                hindcast_leads,
                forecast_leads,
                targets,
                force,
                dry_run,
            ):
                results = []
                if "hindcast" in targets:
                    path = write_hindcast(
                        self.destination / "auto_hindcast.nc",
                        inits=("1981-11-01", "1981-12-01"),
                        leads=tuple(hindcast_leads),
                        members=(0, 1),
                    )
                    written["paths"].append(path)
                    results.append(DownloadResult(path=path, cached=False))
                if "forecast" in targets:
                    path = write_forecast(
                        self.destination / "auto_forecast.nc",
                        init=f"{reference_month}-01",
                        leads=tuple(forecast_leads),
                    )
                    written["paths"].append(path)
                    results.append(DownloadResult(path=path, cached=False))
                return results

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            with mock.patch("prometeo.cli.main.DownloadService", FakeDownloadService):
                code, stdout, stderr = run_cli(
                    [
                        "convert",
                        "--variables",
                        "t2m",
                        "--hindcast-leads",
                        "1-2",
                        "--forecast-leads",
                        "1-2",
                        "--dest",
                        str(root / "cache"),
                        "--output-dir",
                        str(out),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(len(payload["outputs"]), 2)
            self.assertEqual(len(written["paths"]), 2)


if __name__ == "__main__":
    unittest.main()
