"""Canonical build tests: ensemble mean, units, grids, periods and partitions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import xarray as xr

from prometeo.canonical.build import (
    _convert_units,
    _normalize_longitudes,
    build_canonical,
    combine_canonical,
    partition_canonical,
    select_partition,
    validate_canonical,
)
from prometeo.canonical.dataset import CanonicalDataset
from prometeo.catalog.repository import CatalogRepository
from prometeo.reader.reader import ReadRequest, read_netcdf
from prometeo.shared.errors import CanonicalError
from prometeo.shared.timeutils import YearMonth
from tests.netcdf_fixtures import write_forecast, write_hindcast

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "models.yaml"


def _model():
    """Load the Sprint 1 model once per test."""
    return CatalogRepository.from_path(CATALOG).get_model("ecmwf-seas5")


def _simple_canonical(
    periods: tuple[str, ...] = ("2020-01", "2020-02", "2020-03"),
    *,
    longitude: tuple[float, ...] = (0.0,),
    latitudes: tuple[float, ...] = (0.0,),
) -> CanonicalDataset:
    """Build a valid canonical dataset directly, without NetCDF input."""
    shape = (len(periods), len(latitudes), len(longitude))
    data = xr.Dataset(
        {
            "t2m": (
                ("target_period", "latitude", "longitude"),
                np.zeros(shape, dtype="float64"),
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
            "longitude": list(longitude),
        },
    )
    data["t2m"].attrs["units"] = "K"
    return CanonicalDataset(data)


class BuildForecastTests(unittest.TestCase):
    """Verify forecast normalization."""

    def test_forecast_target_periods_and_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                init="2026-11-01",
                leads=(1, 2),
                longitude=(0.5, 1.5, 2.5, 3.5),
            )
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                canonical = build_canonical(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                    target="forecast",
                    reference_month=YearMonth(2026, 11),
                )
                validate_canonical(canonical)

                self.assertEqual(canonical.data.sizes["target_period"], 2)
                self.assertEqual(
                    list(canonical.data["target_period"].values),
                    ["2026-11", "2026-12"],
                )
                self.assertEqual(list(canonical.data["lead"].values), [1, 2])
                self.assertEqual(
                    list(canonical.data["longitude"].values),
                    [0.5, 1.5, 2.5, 3.5],
                )
                # Latitude is normalized to descending (north -> south).
                self.assertEqual(
                    list(canonical.data["latitude"].values), [3.5, 2.5, 1.5]
                )
                self.assertEqual(canonical.data["t2m"].dims, (
                    "target_period",
                    "latitude",
                    "longitude",
                ))
                self.assertEqual(canonical.data["t2m"].attrs["units"], "K")
                # Scalar ensemble_mean member is not part of the contract.
                self.assertNotIn("number", canonical.data.coords)
                self.assertEqual(canonical.data.attrs["ensemble_members"], 1)
                self.assertEqual(
                    canonical.data.attrs["product_type"], "ensemble_mean"
                )
            finally:
                raw.close()

    def test_longitude_0_360_is_normalized_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                longitude=(350.5, 351.5, 352.5, 353.5),
            )
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                canonical = build_canonical(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                    target="forecast",
                    reference_month=YearMonth(2026, 11),
                )
                validate_canonical(canonical)
                self.assertEqual(
                    list(canonical.data["longitude"].values),
                    [-9.5, -8.5, -7.5, -6.5],
                )
            finally:
                raw.close()

    def test_ascending_latitude_is_normalized_to_descending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                leads=(1,),
                latitudes=(1.5, 2.5, 3.5),
            )
            request = ReadRequest(files=(path,), variables=("t2m",), leads=(1,))
            raw = read_netcdf(request)
            try:
                canonical = build_canonical(
                    raw,
                    _model(),
                    request,
                    target="forecast",
                    reference_month=YearMonth(2026, 11),
                )
                validate_canonical(canonical)
                self.assertEqual(
                    list(canonical.data["latitude"].values), [3.5, 2.5, 1.5]
                )
            finally:
                raw.close()


class BuildHindcastTests(unittest.TestCase):
    """Verify hindcast month filtering and ensemble averaging."""

    def test_default_uses_reference_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hindcast(
                Path(tmp) / "hindcast.nc",
                inits=("1981-11-01", "1981-12-01"),
                leads=(1, 2),
                longitude=(-74.5, -73.5, -72.5, -71.5),
                members=(0, 1),
            )
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                canonical = build_canonical(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                    target="hindcast",
                    reference_month=YearMonth(2026, 11),
                )
                validate_canonical(canonical)

                # Only November initializations are kept: 1 init × 2 leads.
                self.assertEqual(canonical.data.sizes["target_period"], 2)
                self.assertEqual(
                    list(canonical.data["target_period"].values),
                    ["1981-11", "1981-12"],
                )
                self.assertEqual(canonical.data.attrs["ensemble_members"], 2)
                self.assertEqual(canonical.data.attrs["hindcast_init_month"], "11")

                # Compare against an explicit ensemble mean of the raw file.
                expected = (
                    raw.data["t2m"]
                    .isel(forecast_reference_time=0)
                    .mean(dim="number", skipna=True)
                    .transpose("forecastMonth", "latitude", "longitude")
                    .values
                )
                np.testing.assert_allclose(
                    canonical.data["t2m"].values, expected
                )
            finally:
                raw.close()

    def test_explicit_init_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hindcast(
                Path(tmp) / "hindcast.nc",
                inits=("1981-11-01", "1981-12-01"),
                leads=(1, 2),
            )
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                canonical = build_canonical(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                    target="hindcast",
                    reference_month=YearMonth(2026, 11),
                    hindcast_init_month=12,
                )
                self.assertEqual(
                    list(canonical.data["target_period"].values),
                    ["1981-12", "1982-01"],
                )
            finally:
                raw.close()

    def test_missing_init_month_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hindcast(
                Path(tmp) / "hindcast.nc",
                inits=("1981-11-01", "1981-12-01"),
            )
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                with self.assertRaises(CanonicalError):
                    build_canonical(
                        raw,
                        _model(),
                        ReadRequest(files=(path,), variables=("t2m",)),
                        target="hindcast",
                        reference_month=YearMonth(2026, 11),
                        hindcast_init_month=1,
                    )
            finally:
                raw.close()

    def test_all_init_months_can_duplicate_periods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hindcast(
                Path(tmp) / "hindcast.nc",
                inits=("1981-11-01", "1981-12-01"),
                leads=(1, 2),
            )
            raw = read_netcdf(ReadRequest(files=(path,), variables=("t2m",)))
            try:
                canonical = build_canonical(
                    raw,
                    _model(),
                    ReadRequest(files=(path,), variables=("t2m",)),
                    target="hindcast",
                    reference_month=YearMonth(2026, 11),
                    hindcast_init_month="all",
                )
                # Nov/lead1, Nov/lead2, Dec/lead1, Dec/lead2 -> Dec repeats.
                self.assertEqual(canonical.data.sizes["target_period"], 4)
                with self.assertRaises(CanonicalError):
                    validate_canonical(canonical)
            finally:
                raw.close()


class UnitTests(unittest.TestCase):
    """Verify unit conversion helpers."""

    def test_temperature_celsius_to_kelvin(self) -> None:
        values, units = _convert_units(
            "t2m", np.array([0.0, 10.0]), "degC"
        )
        np.testing.assert_allclose(values, [273.15, 283.15])
        self.assertEqual(units, "K")

    def test_precipitation_mm_per_day_to_mps(self) -> None:
        values, units = _convert_units(
            "tprate", np.array([86400.0]), "mm/day"
        )
        np.testing.assert_allclose(values, [0.001])
        self.assertEqual(units, "m s-1")

    def test_wind_kmh_to_mps(self) -> None:
        values, units = _convert_units("u10", np.array([3.6]), "km/h")
        np.testing.assert_allclose(values, [1.0])
        self.assertEqual(units, "m s-1")

    def test_unknown_units_are_rejected(self) -> None:
        with self.assertRaises(CanonicalError):
            _convert_units("t2m", np.array([1.0]), "furlongs")

    def test_longitude_normalization(self) -> None:
        values, order = _normalize_longitudes(
            np.array([350.5, 0.5, 10.5, 180.0])
        )
        np.testing.assert_allclose(values, [-9.5, 0.5, 10.5, 180.0])
        np.testing.assert_array_equal(order, [0, 1, 2, 3])


class PartitionTests(unittest.TestCase):
    """Verify X/Z splitting and selection."""

    def test_partition_by_cutoff(self) -> None:
        canonical = _simple_canonical()
        hindcast, forecast = partition_canonical(
            canonical, YearMonth(2020, 2)
        )
        self.assertEqual(
            list(hindcast.data["target_period"].values), ["2020-01"]
        )
        self.assertEqual(
            list(forecast.data["target_period"].values),
            ["2020-02", "2020-03"],
        )
        self.assertEqual(hindcast.data.attrs["partition"], "x")
        self.assertEqual(forecast.data.attrs["partition"], "z")

    def test_select_partition(self) -> None:
        canonical = _simple_canonical()
        self.assertEqual(
            list(
                select_partition(canonical, "x", YearMonth(2020, 2))
                .data["target_period"]
                .values
            ),
            ["2020-01"],
        )
        self.assertEqual(
            list(
                select_partition(canonical, "z", YearMonth(2020, 2))
                .data["target_period"]
                .values
            ),
            ["2020-02", "2020-03"],
        )
        self.assertEqual(
            select_partition(canonical, "both", YearMonth(2020, 2)).data.sizes[
                "target_period"
            ],
            3,
        )

    def test_invalid_partition_is_rejected(self) -> None:
        with self.assertRaises(CanonicalError):
            select_partition(_simple_canonical(), "q", YearMonth(2020, 2))

    def test_combine_canonical(self) -> None:
        first = _simple_canonical(periods=("2020-01",))
        second = _simple_canonical(periods=("2020-02",))
        combined = combine_canonical(first, second)
        self.assertEqual(
            list(combined.data["target_period"].values), ["2020-01", "2020-02"]
        )
        self.assertEqual(combined.data.attrs["partition"], "xz")

    def test_combine_duplicate_periods_is_rejected(self) -> None:
        first = _simple_canonical(periods=("2020-01",))
        with self.assertRaises(CanonicalError):
            combine_canonical(first, first)


class ValidationTests(unittest.TestCase):
    """Verify canonical invariants."""

    def test_valid_dataset(self) -> None:
        validate_canonical(_simple_canonical())

    def test_duplicate_target_periods_are_rejected(self) -> None:
        canonical = _simple_canonical()
        canonical.data = canonical.data.assign_coords(
            target_period=("target_period", ["2020-01", "2020-01", "2020-03"])
        )
        with self.assertRaises(CanonicalError):
            validate_canonical(canonical)

    def test_non_monotonic_longitude_is_rejected(self) -> None:
        canonical = _simple_canonical(longitude=(0.0, -1.0))
        with self.assertRaises(CanonicalError):
            validate_canonical(canonical)

    def test_ascending_latitude_is_rejected(self) -> None:
        canonical = _simple_canonical(latitudes=(1.0, 2.0))
        with self.assertRaises(CanonicalError):
            validate_canonical(canonical)

    def test_missing_lead_coordinate_is_rejected(self) -> None:
        canonical = _simple_canonical()
        canonical.data = canonical.data.drop_vars("lead")
        with self.assertRaises(CanonicalError):
            validate_canonical(canonical)

    def test_summary_is_json_serializable(self) -> None:
        summary = _simple_canonical().summary()
        encoded = json.dumps(summary)
        self.assertIn("target_period", encoded)
        self.assertEqual(summary["target_period"]["count"], 3)
        self.assertEqual(summary["variables"]["t2m"]["units"], "K")

    def test_copy_is_independent(self) -> None:
        original = _simple_canonical()
        copied = original.copy()
        copied.data.attrs["extra"] = "x"
        self.assertNotIn("extra", original.data.attrs)


if __name__ == "__main__":
    unittest.main()
