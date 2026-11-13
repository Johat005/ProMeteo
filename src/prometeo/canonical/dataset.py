"""Canonical dataset contract: dims, coordinates, variables and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from prometeo.shared.errors import CanonicalError


def _jsonable(value: Any) -> Any:
    """Convert numpy/cftime/path values into JSON-serializable objects."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # datetime64 must be handled before the generic numpy branch: .item() would
    # produce an opaque nanosecond integer.
    if isinstance(value, np.datetime64):
        return str(value.astype("datetime64[D]"))
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _coordinate_summary(values: np.ndarray) -> dict[str, object]:
    """Describe one 1-D coordinate: size, range and order."""
    flat = np.asarray(values).reshape(-1)
    payload: dict[str, object] = {
        "count": int(flat.size),
        "min": _jsonable(np.nanmin(flat)) if flat.size else None,
        "max": _jsonable(np.nanmax(flat)) if flat.size else None,
    }
    if flat.size > 1:
        differences = np.diff(flat.astype(float))
        if np.all(differences > 0):
            payload["order"] = "ascending"
        elif np.all(differences < 0):
            payload["order"] = "descending"
        else:
            payload["order"] = "mixed"
    return payload


@dataclass
class CanonicalDataset:
    """Thin wrapper around the canonical xarray dataset.

    Expected dimensions: ``target_period``, ``latitude``, ``longitude``.
    ``init_time`` and ``lead`` are coordinates along ``target_period``.
    Missing values are NaN; CPT is responsible for writing -999.
    """

    data: xr.Dataset
    schema_version: str = "1"

    @property
    def variables(self) -> tuple[str, ...]:
        """Return the canonical variable names."""
        # Preserve the order used when the dataset was built.
        return tuple(self.data.data_vars)

    def variable(self, name: str) -> xr.DataArray:
        """Return one canonical variable or raise CanonicalError."""
        if name in self.data.data_vars:
            return self.data[name]

        available = ", ".join(sorted(self.data.data_vars)) or "(ninguna)"
        raise CanonicalError(
            f"Variable canónica desconocida: '{name}'.",
            hint=f"Variables disponibles: {available}.",
        )

    def summary(self) -> dict[str, Any]:
        """Return a JSON-friendly summary of the dataset."""
        variables: dict[str, object] = {}
        for name, variable in self.data.data_vars.items():
            variables[name] = {
                "dims": list(variable.dims),
                "shape": list(variable.shape),
                "dtype": str(variable.dtype),
                "units": _jsonable(variable.attrs.get("units")),
                "long_name": _jsonable(variable.attrs.get("long_name")),
            }

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "dimensions": {name: int(size) for name, size in self.data.sizes.items()},
            "variables": variables,
            "coordinates": list(self.data.coords),
            "attrs": {key: _jsonable(value) for key, value in self.data.attrs.items()},
        }

        # The target period is the canonical time axis.
        if "target_period" in self.data.coords:
            periods = np.asarray(self.data["target_period"].values).astype(str)
            payload["target_period"] = {
                "count": int(periods.size),
                "first": str(periods[0]) if periods.size else None,
                "last": str(periods[-1]) if periods.size else None,
            }

        # Provenance coordinates along target_period.
        if "init_time" in self.data.coords:
            init_values = np.asarray(self.data["init_time"].values).reshape(-1)
            payload["init_time"] = {
                "count": int(init_values.size),
                "first": _jsonable(init_values[0]) if init_values.size else None,
                "last": _jsonable(init_values[-1]) if init_values.size else None,
            }
        if "lead" in self.data.coords:
            leads = np.asarray(self.data["lead"].values).reshape(-1)
            payload["leads"] = sorted({int(value) for value in leads})

        # Grid description.
        if "latitude" in self.data.coords:
            payload["latitude"] = _coordinate_summary(
                self.data["latitude"].values
            )
        if "longitude" in self.data.coords:
            payload["longitude"] = _coordinate_summary(
                self.data["longitude"].values
            )

        return payload

    def copy(self) -> "CanonicalDataset":
        """Return an independent copy of the canonical dataset."""
        # Shallow copy: selections/attrs are independent, but variable arrays are
        # shared until modified.  This avoids loading a multi-GB cube into RAM.
        return CanonicalDataset(self.data.copy(deep=False), self.schema_version)
