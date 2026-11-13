"""Synthetic NetCDF fixtures for reader and CLI tests.

These files mimic the structure we observed from real C3S downloads:

* forecast: a scalar ``number`` coordinate and no member dimension;
* hindcast: ``number`` as a real dimension with several members.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr


def write_forecast(
    path: Path,
    *,
    init: str = "2026-11-01",
    longitude: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5),
    leads: tuple[int, ...] = (1, 2),
    units: str = "K",
    values: np.ndarray | None = None,
) -> Path:
    """Write a minimal ensemble_mean-style forecast file."""
    latitudes = (3.5, 2.5, 1.5)
    shape = (1, len(leads), len(latitudes), len(longitude))
    if values is None:
        values = np.arange(np.prod(shape), dtype="float32").reshape(shape)

    dataset = xr.Dataset(
        {
            "t2m": (
                ("forecast_reference_time", "forecastMonth", "latitude", "longitude"),
                values,
                {"units": units, "long_name": "2 metre temperature"},
            )
        },
        coords={
            # Scalar coordinate, exactly as returned for ensemble_mean.
            "number": 0,
            "forecast_reference_time": np.array([init], dtype="datetime64[ns]"),
            "forecastMonth": np.array(leads, dtype="int64"),
            "latitude": np.array(latitudes, dtype="float64"),
            "longitude": np.array(longitude, dtype="float64"),
        },
        attrs={"Conventions": "CF-1.7", "institution": "test"},
    )
    dataset.to_netcdf(path)
    dataset.close()
    return Path(path)


def write_hindcast(
    path: Path,
    *,
    inits: tuple[str, ...] = ("1981-11-01", "1982-11-01"),
    longitude: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5),
    leads: tuple[int, ...] = (1, 2),
    members: tuple[int, ...] = (0, 1),
    units: str = "K",
    values: np.ndarray | None = None,
) -> Path:
    """Write a minimal monthly_mean-style hindcast file."""
    latitudes = (3.5, 2.5, 1.5)
    shape = (
        len(members),
        len(inits),
        len(leads),
        len(latitudes),
        len(longitude),
    )
    if values is None:
        values = np.arange(np.prod(shape), dtype="float32").reshape(shape)

    dataset = xr.Dataset(
        {
            "t2m": (
                (
                    "number",
                    "forecast_reference_time",
                    "forecastMonth",
                    "latitude",
                    "longitude",
                ),
                values,
                {"units": units, "long_name": "2 metre temperature"},
            )
        },
        coords={
            "number": np.array(members, dtype="int64"),
            "forecast_reference_time": np.array(inits, dtype="datetime64[ns]"),
            "forecastMonth": np.array(leads, dtype="int64"),
            "latitude": np.array(latitudes, dtype="float64"),
            "longitude": np.array(longitude, dtype="float64"),
        },
        attrs={"Conventions": "CF-1.7", "institution": "test"},
    )
    dataset.to_netcdf(path)
    dataset.close()
    return Path(path)
