"""Reader tests: layout discovery, concatenation, selection and inspection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from prometeo.reader.reader import (
    ReadRequest,
    discover_layout,
    inspect_netcdf,
    open_netcdf,
    read_netcdf,
)
from prometeo.shared.errors import ReaderError
from tests.netcdf_fixtures import write_forecast, write_hindcast


class LayoutTests(unittest.TestCase):
    """Verify tolerant discovery of C3S dimension names."""

    def test_forecast_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            dataset = open_netcdf(path)
            try:
                layout = discover_layout(dataset)
            finally:
                dataset.close()

            self.assertEqual(layout.latitude, "latitude")
            self.assertEqual(layout.longitude, "longitude")
            self.assertEqual(layout.init_time, "forecast_reference_time")
            self.assertEqual(layout.lead, "forecastMonth")
            self.assertEqual(layout.member, "number")
            self.assertIsNone(layout.valid_time)

    def test_aliases_are_discovered(self) -> None:
        import xarray as xr

        dataset = xr.Dataset(
            {"t2m": (("lat", "lon"), np.zeros((2, 2), dtype="float32"))},
            coords={
                "lat": [1.0, 0.0],
                "lon": [0.0, 1.0],
                "init_time": np.array(["2026-11-01"], dtype="datetime64[ns]"),
                "lead": [1],
            },
        )
        layout = discover_layout(dataset)
        self.assertEqual(layout.latitude, "lat")
        self.assertEqual(layout.longitude, "lon")
        self.assertEqual(layout.init_time, "init_time")
        self.assertEqual(layout.lead, "lead")


class ReadTests(unittest.TestCase):
    """Verify raw selection without unit conversion or reordering."""

    def test_hindcast_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_hindcast(Path(tmp) / "hindcast.nc")
            raw = read_netcdf(
                ReadRequest(
                    files=(path,),
                    variables=("t2m",),
                    leads=(2,),
                    init_start="1982-11",
                    init_end="1982-11",
                    members=(1,),
                )
            )
            try:
                self.assertEqual(raw.data.sizes["number"], 1)
                self.assertEqual(raw.data.sizes["forecast_reference_time"], 1)
                self.assertEqual(raw.data.sizes["forecastMonth"], 1)
                self.assertEqual(raw.data["t2m"].dims, (
                    "number",
                    "forecast_reference_time",
                    "forecastMonth",
                    "latitude",
                    "longitude",
                ))
                self.assertEqual(int(raw.data["forecastMonth"].values[0]), 2)
                self.assertEqual(int(raw.data["number"].values[0]), 1)
            finally:
                raw.close()

    def test_area_selection_0_360(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                longitude=(350.5, 351.5, 352.5, 353.5),
            )
            raw = read_netcdf(
                ReadRequest(
                    files=(path,),
                    variables=("t2m",),
                    area=(4.0, 350.0, 2.0, 353.0),
                )
            )
            try:
                # Values-based masking keeps the original order.
                self.assertEqual(
                    list(raw.data["latitude"].values), [3.5, 2.5]
                )
                self.assertEqual(
                    list(raw.data["longitude"].values),
                    [350.5, 351.5, 352.5],
                )
            finally:
                raw.close()

    def test_area_selection_180_180(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(
                Path(tmp) / "forecast.nc",
                longitude=(-74.5, -73.5, -72.5, -71.5),
            )
            raw = read_netcdf(
                ReadRequest(
                    files=(path,),
                    variables=("t2m",),
                    area=(4.0, -74.0, 2.0, -72.0),
                )
            )
            try:
                self.assertEqual(
                    list(raw.data["longitude"].values),
                    [-73.5, -72.5],
                )
            finally:
                raw.close()

    def test_multiple_files_are_concatenated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = write_forecast(
                Path(tmp) / "first.nc", init="2026-11-01"
            )
            second = write_forecast(
                Path(tmp) / "second.nc", init="2026-12-01"
            )
            raw = read_netcdf(
                ReadRequest(files=(first, second), variables=("t2m",))
            )
            try:
                self.assertEqual(raw.data.sizes["forecast_reference_time"], 2)
                self.assertEqual(raw.data.sizes["forecastMonth"], 2)
            finally:
                raw.close()

    def test_duplicate_init_times_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = write_forecast(
                Path(tmp) / "first.nc", init="2026-11-01"
            )
            second = write_forecast(
                Path(tmp) / "second.nc", init="2026-11-01"
            )
            with self.assertRaises(ReaderError):
                read_netcdf(
                    ReadRequest(files=(first, second), variables=("t2m",))
                )

    def test_missing_variable_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            with self.assertRaises(ReaderError) as caught:
                read_netcdf(
                    ReadRequest(files=(path,), variables=("prate",))
                )
            self.assertIn("t2m", caught.exception.hint or "")

    def test_inspection_is_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_forecast(Path(tmp) / "forecast.nc")
            summary = inspect_netcdf(path)

            # No numpy scalar should leak into the JSON payload.
            encoded = json.dumps(summary)
            self.assertIn('"t2m"', encoded)
            self.assertEqual(summary["layout"]["member"], "number")
            self.assertEqual(summary["members"], [0])
            self.assertEqual(summary["leads"], [1, 2])
            self.assertEqual(summary["init_time"]["count"], 1)
            self.assertEqual(summary["latitude"]["order"], "descending")


if __name__ == "__main__":
    unittest.main()
