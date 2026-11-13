"""Diagnostics tests: findings, checks, engine and conversion gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import xarray as xr

from prometeo.canonical.dataset import CanonicalDataset
from prometeo.catalog.repository import CatalogRepository
from prometeo.diagnostics.checks import check_canonical, check_file
from prometeo.diagnostics.engine import DiagnosticEngine, validate_file
from prometeo.diagnostics.findings import (
    DiagnosticReport,
    Severity,
    error,
    info,
    warning,
)
from prometeo.reader.reader import ReadRequest, read_netcdf
from prometeo.shared.errors import DiagnosticError
from tests.netcdf_fixtures import write_forecast, write_hindcast

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "models.yaml"


def _model():
    return CatalogRepository.from_path(CATALOG).get_model("ecmwf-seas5")


def _simple_canonical() -> CanonicalDataset:
    """Build a small valid canonical dataset for checks."""
    periods = ("2020-01", "2020-02", "2020-03")
    data = xr.Dataset(
        {
            "t2m": (
                ("target_period", "latitude", "longitude"),
                np.zeros((3, 1, 1), dtype="float64"),
                {"units": "K"},
            )
        },
        coords={
            "target_period": list(periods),
            "init_time": (
                "target_period",
                np.array(
                    [f"{period}-01" for period in periods],
                    dtype="datetime64[ns]",
                ),
            ),
            "lead": ("target_period", [1, 2, 3]),
            "latitude": [0.0],
            "longitude": [0.0],
        },
    )
    return CanonicalDataset(data)


def _findings_of(check_id: str, findings):
    return [finding for finding in findings if finding.check_id == check_id]


class FindingTests(unittest.TestCase):
    """Verify report value objects."""

    def test_finding_to_dict_and_blocking(self) -> None:
        finding = error("CHECK", "loc", "mensaje", "acción", value=1)
        payload = finding.to_dict()
        self.assertEqual(payload["severity"], "ERROR")
        self.assertTrue(finding.is_blocking())
        self.assertFalse(warning("W", "l", "m", "a").is_blocking())
        self.assertFalse(info("I", "l", "m", "a").is_blocking())

    def test_report_counts(self) -> None:
        report = DiagnosticReport()
        report.add(error("E", "loc", "m", "a"))
        report.add(warning("W", "loc", "m", "a"))
        report.add(info("I", "loc", "m", "a"))
        self.assertEqual(len(report.errors()), 1)
        self.assertEqual(len(report.warnings()), 1)
        self.assertEqual(report.exit_code(), 2)
        self.assertTrue(report.has_errors())

    def test_report_without_errors_exits_zero(self) -> None:
        report = DiagnosticReport()
        report.add(warning("W", "loc", "m", "a"))
        self.assertEqual(report.exit_code(), 0)

    def test_report_is_json_serializable(self) -> None:
        report = DiagnosticReport(files=["a.nc"], metadata={"x": np.int64(1)})
        report.add(error("E", "loc", "m", "a", value=np.float32(1.5)))
        encoded = json.dumps(report.to_dict())
        self.assertIn('"errors": 1', encoded)


class FileCheckTests(unittest.TestCase):
    """Verify raw file checks."""

    def _raw(self, path: Path, variables=("t2m",)):
        return read_netcdf(ReadRequest(files=(path,), variables=variables))

    def test_valid_forecast_has_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",), leads=(1, 2)),
                )
                self.assertFalse([f for f in findings if f.severity == Severity.ERROR])
            finally:
                raw.close()

    def test_missing_variable_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("tprate",)),
                )
                self.assertTrue(_findings_of("FILE-VAR-MISSING", findings))
            finally:
                raw.close()

    def test_units_mismatch_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc", units="degC")
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                )
                self.assertTrue(_findings_of("FILE-UNITS", findings))
            finally:
                raw.close()

    def test_missing_lead_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc", leads=(1, 2))
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",), leads=(3,)),
                )
                self.assertTrue(_findings_of("FILE-LEAD-MISSING", findings))
            finally:
                raw.close()

    def test_non_monotonic_longitude_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                longitude=(0.5, 2.5, 1.5, 3.5),
            )
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                )
                self.assertTrue(_findings_of("GRID-LON-ORDER", findings))
            finally:
                raw.close()

    def test_all_nan_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shape = (1, 2, 3, 4)
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                values=np.full(shape, np.nan, dtype="float64"),
            )
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                )
                self.assertTrue(_findings_of("DATA-NAN", findings))
                self.assertTrue(
                    any(f.severity == Severity.ERROR for f in _findings_of("DATA-NAN", findings))
                )
            finally:
                raw.close()

    def test_constant_field_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shape = (1, 2, 3, 4)
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                values=np.full(shape, 273.15, dtype="float64"),
            )
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                )
                warnings = _findings_of("DATA-CONST", findings)
                self.assertTrue(warnings)
                self.assertEqual(warnings[0].severity, Severity.WARNING)
            finally:
                raw.close()

    def test_member_count_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hindcast(Path(tmp) / "hindcast.nc", members=(0, 1))
            raw = self._raw(path)
            try:
                findings = check_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",), leads=(1, 2)),
                )
                member_findings = _findings_of("FILE-MEMBER-COUNT", findings)
                self.assertTrue(member_findings)
                self.assertEqual(member_findings[0].severity, Severity.WARNING)
            finally:
                raw.close()


class CanonicalCheckTests(unittest.TestCase):
    """Verify canonical checks."""

    def test_valid_canonical_has_no_errors(self) -> None:
        findings = check_canonical(_simple_canonical())
        self.assertFalse([f for f in findings if f.severity == Severity.ERROR])
        self.assertTrue(_findings_of("CANON-OK", findings))

    def test_duplicate_periods_are_error(self) -> None:
        canonical = _simple_canonical()
        canonical.data = canonical.data.assign_coords(
            target_period=("target_period", ["2020-01", "2020-01", "2020-03"])
        )
        findings = check_canonical(canonical)
        self.assertTrue(_findings_of("CANON-CONTRACT", findings))

    def test_missing_units_are_error(self) -> None:
        canonical = _simple_canonical()
        canonical.data["t2m"].attrs.pop("units")
        findings = check_canonical(canonical)
        self.assertTrue(_findings_of("CANON-CONTRACT", findings))


class EngineTests(unittest.TestCase):
    """Verify engine collection and conversion gate."""

    def test_run_collects_findings(self) -> None:
        engine = DiagnosticEngine()
        report = engine.run(
            [
                lambda: [info("A", "loc", "m", "a")],
                lambda: [warning("B", "loc", "m", "a")],
            ]
        )
        self.assertEqual(len(report.findings), 2)

    def test_check_exception_becomes_error(self) -> None:
        engine = DiagnosticEngine()

        def broken():
            raise ValueError("boom")

        report = engine.run([broken])
        self.assertTrue(report.has_errors())
        self.assertEqual(report.errors()[0].check_id, "INTERNAL")

    def test_force_allows_conversion(self) -> None:
        report = DiagnosticReport()
        report.add(error("E", "loc", "m", "a"))
        self.assertFalse(DiagnosticEngine().can_convert(report))
        self.assertTrue(DiagnosticEngine(force=True).can_convert(report))

    def test_assert_can_convert_raises(self) -> None:
        report = DiagnosticReport()
        report.add(error("E", "loc", "m", "a"))
        with self.assertRaises(DiagnosticError):
            DiagnosticEngine().assert_can_convert(report)

    def test_validate_file_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                report = validate_file(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",), leads=(1, 2)),
                )
                self.assertEqual(report.metadata["kind"], "file")
                self.assertFalse(report.has_errors())
            finally:
                raw.close()


if __name__ == "__main__":
    unittest.main()
