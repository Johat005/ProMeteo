"""CPT mapping, rendering and structural validation tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import xarray as xr

from prometeo.canonical.dataset import CanonicalDataset
from prometeo.cpt.mapping import (
    precipitation_to_mm_per_month,
    replace_missing,
    resolve_field,
    temperature_to_celsius,
    to_cpt_values,
)
from prometeo.cpt.validator import validate_cpt, validate_text
from prometeo.cpt.writer import (
    _format_month,
    _format_number,
    render_cpt,
    write_cpt,
)
from prometeo.shared.errors import CPTError
from prometeo.shared.timeutils import YearMonth

NAMESPACE = "xmlns:cpt=http://iri.columbia.edu/CPT/v10/"


def _canonical(
    variable: str = "t2m",
    *,
    units: str = "K",
    periods: tuple[str, ...] = ("2026-11", "2026-12"),
    latitudes: tuple[float, ...] = (10.0, 0.0),
    longitudes: tuple[float, ...] = (-180.0, 0.0, 180.0),
    values: np.ndarray | None = None,
) -> CanonicalDataset:
    """Build a small valid canonical dataset for CPT tests."""
    shape = (len(periods), len(latitudes), len(longitudes))
    if values is None:
        values = np.full(shape, 273.15, dtype="float64")

    data = xr.Dataset(
        {
            variable: (
                ("target_period", "latitude", "longitude"),
                values,
                {"units": units},
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
            "lead": ("target_period", np.arange(1, len(periods) + 1)),
            "latitude": list(latitudes),
            "longitude": list(longitudes),
        },
    )
    return CanonicalDataset(data)


class MappingTests(unittest.TestCase):
    """Verify canonical-to-CPT transformations."""

    def test_resolve_field(self) -> None:
        field = resolve_field("t2m")
        self.assertEqual(field.cpt_name, "t2m")
        self.assertEqual(field.units, "C")
        self.assertEqual(resolve_field("tprate").cpt_name, "prate")

    def test_unknown_variable_is_rejected(self) -> None:
        with self.assertRaises(CPTError):
            resolve_field("rh2m")

    def test_temperature_to_celsius(self) -> None:
        np.testing.assert_allclose(
            temperature_to_celsius(np.array([273.15, 283.15])),
            [0.0, 10.0],
        )

    def test_precipitation_month_length(self) -> None:
        # February 2024 has 29 days: 29 * 86400 * 1000 mm per (m/s).
        np.testing.assert_allclose(
            precipitation_to_mm_per_month(np.array([1.0]), "2024-02"),
            [2_505_600_000.0],
        )

    def test_replace_missing(self) -> None:
        converted = replace_missing(np.array([np.nan, 1.5]))
        np.testing.assert_allclose(converted, [-999.0, 1.5])

    def test_to_cpt_values_returns_values_units_and_field(self) -> None:
        values, units, field = to_cpt_values(
            "t2m", np.array([273.15]), "2026-11"
        )
        np.testing.assert_allclose(values, [0.0])
        self.assertEqual(units, "C")
        self.assertEqual(field, "t2m")


class FormattingTests(unittest.TestCase):
    """Verify deterministic number/month formatting."""

    def test_format_month(self) -> None:
        self.assertEqual(_format_month(YearMonth(2026, 11)), "2026-11")
        self.assertEqual(_format_month("2026-11"), "2026-11")
        self.assertEqual(
            _format_month(np.datetime64("2026-11-01")), "2026-11"
        )

    def test_format_number(self) -> None:
        self.assertEqual(_format_number(float("nan")), "-999")
        self.assertEqual(_format_number(1.23456), "1.2346")

    def test_infinite_value_is_rejected(self) -> None:
        with self.assertRaises(CPTError):
            _format_number(float("inf"))


class RenderTests(unittest.TestCase):
    """Verify the generated CPT document."""

    def test_render_structure(self) -> None:
        canonical = _canonical()
        text = render_cpt(canonical, "t2m")
        lines = text.splitlines()

        self.assertEqual(lines[0], NAMESPACE)
        self.assertEqual(lines[1], "cpt:nfields=1")
        self.assertIn("cpt:field=t2m", lines[2])
        self.assertIn("cpt:S=2026-11", lines[2])
        self.assertIn("cpt:T=2026-11", lines[2])
        self.assertIn("cpt:nrow=2", lines[2])
        self.assertIn("cpt:ncol=3", lines[2])
        self.assertIn("cpt:units=C", lines[2])
        self.assertIn("cpt:missing=-999", lines[2])

        # Two blocks of 2 rows each, plus 2 header lines and 2 longitudes.
        blocks = [line for line in lines if line.startswith("cpt:field=")]
        self.assertEqual(len(blocks), 2)
        self.assertIn("cpt:T=2026-12", text)

        # t2m is converted from K to C.
        self.assertIn("0.0000", text)

        report = validate_text(text)
        self.assertTrue(report.valid, report.to_dict())
        self.assertEqual(report.blocks, 2)
        self.assertEqual(report.nfields, 1)

    def test_render_is_deterministic(self) -> None:
        canonical = _canonical()
        self.assertEqual(
            render_cpt(canonical, "t2m"), render_cpt(canonical, "t2m")
        )

    def test_missing_values_become_minus_999(self) -> None:
        values = np.full((2, 2, 3), 273.15, dtype="float64")
        values[0, 0, 0] = np.nan
        text = render_cpt(_canonical(values=values), "t2m")
        self.assertIn("-999", text)
        self.assertTrue(validate_text(text).valid)

    def test_precipitation_uses_cpt_field_and_units(self) -> None:
        canonical = _canonical(
            "tprate",
            units="m s-1",
            periods=("2024-02",),
            values=np.ones((1, 2, 3), dtype="float64"),
        )
        text = render_cpt(canonical, "tprate")
        self.assertIn("cpt:field=prate", text)
        self.assertIn("cpt:units=mm/month", text)
        self.assertIn("2505600000.0000", text)

    def test_multiple_variables_require_explicit_choice(self) -> None:
        canonical = _canonical()
        canonical.data["tprate"] = canonical.data["t2m"].copy()
        canonical.data["tprate"].attrs["units"] = "m s-1"
        with self.assertRaises(CPTError):
            render_cpt(canonical)
        self.assertIn("cpt:field=t2m", render_cpt(canonical, "t2m"))

    def test_write_cpt_and_validate_file(self) -> None:
        canonical = _canonical()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.cpt"
            written = write_cpt(canonical, path, "t2m")
            self.assertEqual(written, path)
            self.assertTrue(path.is_file())

            report = validate_cpt(path)
            self.assertTrue(report.valid, report.to_dict())
            self.assertEqual(
                path.read_text(encoding="utf-8"), render_cpt(canonical, "t2m")
            )


class ValidatorTests(unittest.TestCase):
    """Verify that structural corruption is detected."""

    def setUp(self) -> None:
        self.text = render_cpt(_canonical(), "t2m")
        self.lines = self.text.splitlines()

    def test_wrong_namespace(self) -> None:
        text = self.text.replace(NAMESPACE, "xmlns:cpt=wrong", 1)
        report = validate_text(text)
        self.assertFalse(report.valid)

    def test_wrong_nfields(self) -> None:
        lines = list(self.lines)
        lines[1] = "cpt:nfields=2"
        report = validate_text("\n".join(lines))
        self.assertFalse(report.valid)

    def test_missing_required_tag(self) -> None:
        lines = list(self.lines)
        lines[2] = lines[2].replace(" cpt:ncol=3,", "")
        report = validate_text("\n".join(lines))
        self.assertFalse(report.valid)

    def test_wrong_row_width(self) -> None:
        lines = list(self.lines)
        # The first data row follows the namespace, nfields, header, longitude.
        lines[4] = "10.00 0.0000"
        report = validate_text("\n".join(lines))
        self.assertFalse(report.valid)

    def test_non_numeric_value(self) -> None:
        lines = list(self.lines)
        lines[4] = lines[4].replace("0.0000", "abc", 1)
        report = validate_text("\n".join(lines))
        self.assertFalse(report.valid)

    def test_truncated_block(self) -> None:
        report = validate_text("\n".join(self.lines[:-1]))
        self.assertFalse(report.valid)

    def test_duplicate_period(self) -> None:
        text = self.text.replace("cpt:T=2026-12", "cpt:T=2026-11", 1)
        report = validate_text(text)
        self.assertFalse(report.valid)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(CPTError):
            validate_cpt(Path("/does/not/exist.cpt"))

    def test_report_is_json_serializable(self) -> None:
        report = validate_text(self.text)
        encoded = json.dumps(report.to_dict())
        self.assertIn('"valid": true', encoded)


if __name__ == "__main__":
    unittest.main()
