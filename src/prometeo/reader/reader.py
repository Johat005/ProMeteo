"""Open NetCDF files, discover the C3S layout and return raw slices.

The reader is deliberately conservative:

* it opens files lazily with xarray;
* it discovers dimension/coordinate names tolerantly;
* it selects variables, leads, init range, members and area;
* it does **not** convert units, normalize longitudes or reorder coordinates.

Normalization belongs to ``canonical/``.  Keeping the reader raw makes it easy
to diagnose exactly what came from CDS and to reuse it for other exporters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import xarray as xr

from prometeo.shared.errors import ReaderError
from prometeo.shared.timeutils import YearMonth, add_months, parse_year_month

# Tolerant aliases for the names used by different C3S products.
LATITUDE_NAMES = ("latitude", "lat", "nav_lat")
LONGITUDE_NAMES = ("longitude", "lon", "nav_lon")
INIT_NAMES = ("forecast_reference_time", "init_time")
LEAD_NAMES = ("forecastMonth", "forecast_month", "leadtime_month", "lead")
MEMBER_NAMES = ("number", "member", "members")
VALID_TIME_NAMES = ("valid_time",)

# Global CDS area in N,W,S,E order.  Selecting it is a no-op.
_GLOBAL_AREA = (90.0, -180.0, -90.0, 180.0)


@dataclass(frozen=True)
class DatasetLayout:
    """Names of the dimensions and coordinates found in one or more files."""

    latitude: str
    longitude: str
    init_time: str
    lead: str
    member: str | None = None
    valid_time: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        # Keep all fields explicit so inspect output is stable.
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "init_time": self.init_time,
            "lead": self.lead,
            "member": self.member,
            "valid_time": self.valid_time,
        }


@dataclass
class RawDataset:
    """Raw xarray data plus the names needed to interpret it."""

    data: xr.Dataset
    layout: DatasetLayout
    source_files: tuple[Path, ...]
    # Keep the originally opened datasets alive: xr.concat() results may still
    # read lazily from them until close() is called.
    _sources: tuple[xr.Dataset, ...] = field(default_factory=tuple, repr=False)

    def variable(self, name: str) -> xr.DataArray:
        """Return one raw variable or raise ReaderError."""
        if name in self.data.data_vars:
            return self.data[name]

        available = ", ".join(sorted(self.data.data_vars)) or "(ninguna)"
        raise ReaderError(
            f"Variable desconocida en el archivo: '{name}'.",
            hint=f"Variables disponibles: {available}.",
        )

    def close(self) -> None:
        """Close the underlying xarray dataset."""
        # Close the combined view first, then every source file.  Datasets are
        # compared by identity to avoid closing the same handle twice.
        closed: set[int] = set()
        try:
            self.data.close()
            closed.add(id(self.data))
        finally:
            for source in self._sources:
                if id(source) in closed:
                    continue
                try:
                    source.close()
                finally:
                    closed.add(id(source))

    def summary(self) -> dict[str, object]:
        """Return a JSON-friendly summary of the raw dataset."""
        payload = _dataset_summary(self.data, self.layout)
        payload["source_files"] = [str(path) for path in self.source_files]
        return payload


@dataclass(frozen=True)
class ReadRequest:
    """Selection requested by inspect, validate or convert."""

    files: tuple[Path, ...]
    variables: tuple[str, ...]
    leads: tuple[int, ...] | None = None
    init_start: str | None = None
    init_end: str | None = None
    area: tuple[float, float, float, float] | None = None
    members: tuple[int, ...] | None = None


def _find_name(
    dataset: xr.Dataset,
    aliases: Sequence[str],
    *,
    required: bool = True,
) -> str | None:
    """Find the first matching dimension or coordinate name."""
    # dataset.variables includes dimensions, coordinates and data variables, so
    # scalar coordinates such as the forecast 'number' are also found.
    available = set(dataset.variables) | set(dataset.dims)

    for alias in aliases:
        if alias in available:
            return alias

    # Case-insensitive fallback for files with slightly different casing.
    lowered = {name.lower(): name for name in available}
    for alias in aliases:
        if alias.lower() in lowered:
            return lowered[alias.lower()]

    if required:
        expected = ", ".join(aliases)
        found = ", ".join(sorted(available)) or "(ninguno)"
        raise ReaderError(
            f"No se encontró ninguna dimensión/coordenada esperada: {expected}.",
            hint=f"Nombres encontrados en el archivo: {found}.",
        )
    return None


def discover_layout(dataset: xr.Dataset) -> DatasetLayout:
    """Find latitude, longitude, initialization and lead names tolerantly."""
    # Latitude/longitude/init/lead are mandatory in Sprint 1.  Member and
    # valid_time are optional because ensemble_mean files may only have a
    # scalar 'number' coordinate and some samples have no valid_time.
    return DatasetLayout(
        latitude=_find_name(dataset, LATITUDE_NAMES) or "",
        longitude=_find_name(dataset, LONGITUDE_NAMES) or "",
        init_time=_find_name(dataset, INIT_NAMES) or "",
        lead=_find_name(dataset, LEAD_NAMES) or "",
        member=_find_name(dataset, MEMBER_NAMES, required=False),
        valid_time=_find_name(dataset, VALID_TIME_NAMES, required=False),
    )


def open_netcdf(path: Path) -> xr.Dataset:
    """Open one NetCDF file lazily with CF time decoding."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ReaderError(
            f"No se encontró el archivo NetCDF: {file_path}",
            hint="Verifique la ruta del archivo.",
        )

    try:
        # decode_times=True lets xarray turn CF time values into datetime64 or
        # cftime objects.  The dataset stays lazy: no data is loaded here.
        return xr.open_dataset(file_path, decode_times=True, decode_coords="all")
    except Exception as exc:
        raise ReaderError(
            f"No se pudo abrir el archivo NetCDF: {file_path}",
            hint=str(exc),
        ) from exc


def _layout_key(layout: DatasetLayout) -> tuple[object, ...]:
    """Return the layout fields that must match across concatenated files."""
    return (
        layout.latitude,
        layout.longitude,
        layout.init_time,
        layout.lead,
        layout.member,
        layout.valid_time,
    )


def _member_values(dataset: xr.Dataset, layout: DatasetLayout) -> tuple[int, ...]:
    """Return the member identifiers in one file, including scalar ones."""
    if not layout.member or layout.member not in dataset.variables:
        return ()
    values = np.asarray(dataset[layout.member].values).reshape(-1)
    return tuple(int(value) for value in values)


def _concat_datasets(
    datasets: list[xr.Dataset], layout: DatasetLayout
) -> xr.Dataset:
    """Concatenate files along the initialization dimension."""
    if len(datasets) == 1:
        return datasets[0]

    # The same ensemble members must be present in every file.
    member_sets = {_member_values(dataset, layout) for dataset in datasets}
    if len(member_sets) > 1:
        raise ReaderError(
            "Los archivos contienen conjuntos de miembros diferentes.",
            hint="Verifique que todos los archivos correspondan al mismo producto.",
        )

    try:
        combined = xr.concat(
            datasets,
            dim=layout.init_time,
            data_vars="minimal",
            coords="minimal",
            compat="override",
            join="outer",
            combine_attrs="override",
        )
    except Exception as exc:
        raise ReaderError(
            "No se pudieron concatenar los archivos NetCDF.",
            hint=str(exc),
        ) from exc

    # Duplicate initialization times usually mean the user passed the same file
    # twice or overlapping downloads.
    if layout.init_time in combined.coords:
        index = combined[layout.init_time].to_index()
        duplicated = index[index.duplicated()]
        if len(duplicated):
            values = ", ".join(str(value) for value in duplicated[:5])
            raise ReaderError(
                "Hay tiempos de inicialización duplicados.",
                hint=f"Valores duplicados: {values}.",
            )
    return combined


def _select_init_range(
    dataset: xr.Dataset,
    layout: DatasetLayout,
    init_start: str | None,
    init_end: str | None,
) -> xr.Dataset:
    """Select an inclusive YYYY-MM initialization range without sorting."""
    if not init_start and not init_end:
        return dataset

    start = parse_year_month(init_start) if init_start else None
    end = parse_year_month(init_end) if init_end else None
    if start and end and start > end:
        raise ReaderError(
            f"Rango de inicialización invertido: {start} > {end}.",
            hint="Use --init-start anterior o igual a --init-end.",
        )

    values = np.asarray(dataset[layout.init_time].values).reshape(-1)
    mask = np.ones(len(values), dtype=bool)

    for index, value in enumerate(values):
        key = _year_month_of(value)
        if start is not None and key < start:
            mask[index] = False
        if end is not None and key > end:
            mask[index] = False

    if not mask.any():
        raise ReaderError(
            "El rango de inicialización no contiene ningún dato.",
            hint="Revise --init-start y --init-end.",
        )
    return dataset.isel({layout.init_time: np.where(mask)[0]})


def _year_month_of(value: object) -> YearMonth:
    """Convert a datetime64/cftime/datetime value to YearMonth."""
    year = getattr(value, "year", None)
    month = getattr(value, "month", None)
    if year is not None and month is not None:
        return YearMonth(int(year), int(month))

    # numpy.datetime64 has no attributes; its string form starts with YYYY-MM.
    text = str(value)
    return parse_year_month(text[:7])


def _select_leads(
    dataset: xr.Dataset, layout: DatasetLayout, leads: Sequence[int] | None
) -> xr.Dataset:
    """Select the requested forecast leads."""
    if leads is None:
        return dataset

    if layout.lead not in dataset.variables:
        raise ReaderError(
            f"El archivo no contiene la coordenada de plazos '{layout.lead}'."
        )

    lead_values = np.asarray(dataset[layout.lead].values).reshape(-1)
    if len(lead_values) == 1:
        # Scalar lead: keep the file only if the requested lead matches.
        if int(lead_values[0]) not in set(leads):
            raise ReaderError(
                f"El archivo contiene el plazo {int(lead_values[0])}, "
                f"pero se solicitaron {sorted(set(leads))}."
            )
        return dataset

    missing = sorted(set(leads) - {int(value) for value in lead_values})
    if missing:
        raise ReaderError(
            f"Faltan plazos solicitados en el archivo: {missing}.",
            hint=f"Plazos presentes: {[int(value) for value in lead_values]}.",
        )
    return dataset.sel({layout.lead: list(leads)})


def _select_members(
    dataset: xr.Dataset, layout: DatasetLayout, members: Sequence[int] | None
) -> xr.Dataset:
    """Select ensemble members when the file has a member dimension."""
    if members is None or not layout.member or layout.member not in dataset.variables:
        return dataset

    if layout.member in dataset.dims:
        available = {int(value) for value in dataset[layout.member].values}
        missing = sorted(set(members) - available)
        if missing:
            raise ReaderError(
                f"Faltan miembros solicitados: {missing}.",
                hint=f"Miembros disponibles: {sorted(available)}.",
            )
        return dataset.sel({layout.member: list(members)})

    # Scalar member (ensemble_mean): it only matches if the requested set
    # contains that single member.
    scalar = int(np.asarray(dataset[layout.member].values).reshape(-1)[0])
    if scalar not in set(members):
        raise ReaderError(
            f"El archivo contiene el miembro {scalar}, "
            f"pero se solicitaron {sorted(set(members))}."
        )
    return dataset


def _area_indices(
    values: np.ndarray, lower: float, upper: float
) -> np.ndarray:
    """Return indices of a 1-D coordinate inside [lower, upper]."""
    mask = (values >= lower) & (values <= upper)
    return np.where(mask)[0]


def _select_area(
    dataset: xr.Dataset,
    area: tuple[float, float, float, float],
    layout: DatasetLayout,
) -> xr.Dataset:
    """Select a CDS-order N,W,S,E area without reordering coordinates."""
    if tuple(float(value) for value in area) == _GLOBAL_AREA:
        return dataset

    latitude = dataset[layout.latitude]
    longitude = dataset[layout.longitude]
    if latitude.ndim != 1 or longitude.ndim != 1:
        raise ReaderError(
            "La selección de área requiere coordenadas 1-D de latitud/longitud.",
            hint="Las rejillas curvilíneas no están soportadas en Sprint 1.",
        )

    north, west, south, east = (float(value) for value in area)
    lat_values = np.asarray(latitude.values)
    lon_values = np.asarray(longitude.values)

    # Latitude masking works for ascending and descending grids because it is
    # based on values, not positions.
    lat_indices = _area_indices(lat_values, south, north)
    if len(lat_indices) == 0:
        raise ReaderError(
            "El área solicitada no contiene puntos de latitud.",
            hint=(
                f"Latitudes disponibles: {float(np.nanmin(lat_values))}..{float(np.nanmax(lat_values))}."
            ),
        )

    # Longitude may be 0..360 or -180..180.  Convert the requested bounds to the
    # file's convention only for selection; the data itself is not modified.
    is_0_360 = float(np.nanmin(lon_values)) >= 0 and float(np.nanmax(lon_values)) > 180
    if is_0_360:
        west_bound = west % 360
        east_bound = east % 360
        if west_bound <= east_bound:
            lon_mask = (lon_values >= west_bound) & (lon_values <= east_bound)
        else:
            # Region crosses the 0/360 seam.
            lon_mask = (lon_values >= west_bound) | (lon_values <= east_bound)
        lon_indices = np.where(lon_mask)[0]
    else:
        lon_indices = _area_indices(lon_values, west, east)

    if len(lon_indices) == 0:
        raise ReaderError(
            "El área solicitada no contiene puntos de longitud.",
            hint=(
                f"Longitudes disponibles: {float(np.nanmin(lon_values))}..{float(np.nanmax(lon_values))}."
            ),
        )

    # isel preserves the original coordinate order.
    return dataset.isel(
        {
            layout.latitude: lat_indices,
            layout.longitude: lon_indices,
        }
    )


def _select_variables(
    dataset: xr.Dataset, variables: Sequence[str]
) -> xr.Dataset:
    """Keep only the requested data variables, validating their names."""
    if not variables:
        return dataset

    available = set(dataset.data_vars)
    missing = [name for name in variables if name not in available]
    if missing:
        raise ReaderError(
            f"Faltan variables solicitadas en el archivo: {missing}.",
            hint=f"Variables disponibles: {', '.join(sorted(available)) or '(ninguna)'}.",
        )
    return dataset[list(variables)]


def read_netcdf(request: ReadRequest) -> RawDataset:
    """Open, concatenate and select the requested raw slice."""
    if not request.files:
        raise ReaderError("No se indicó ningún archivo NetCDF.")

    # Validate all paths before opening anything.
    for path in request.files:
        if not Path(path).is_file():
            raise ReaderError(
                f"No se encontró el archivo NetCDF: {path}",
                hint="Verifique la ruta del archivo.",
            )

    datasets: list[xr.Dataset] = []
    try:
        # Open every file lazily and require the same layout in all of them.
        for path in request.files:
            datasets.append(open_netcdf(Path(path)))

        layouts = [discover_layout(dataset) for dataset in datasets]
        first_layout = layouts[0]
        for path, layout in zip(request.files, layouts):
            if _layout_key(layout) != _layout_key(first_layout):
                raise ReaderError(
                    f"El archivo {path} tiene una estructura incompatible.",
                    hint=(
                        "Todos los archivos deben usar los mismos nombres de "
                        "dimensiones y coordenadas."
                    ),
                )

        # Concatenate by initialization time, then apply the raw selections.
        combined = _concat_datasets(datasets, first_layout)
        combined = _select_variables(combined, request.variables)
        combined = _select_leads(combined, first_layout, request.leads)
        combined = _select_members(combined, first_layout, request.members)
        combined = _select_init_range(
            combined, first_layout, request.init_start, request.init_end
        )
        if request.area is not None:
            combined = _select_area(combined, request.area, first_layout)

        return RawDataset(
            data=combined,
            layout=first_layout,
            source_files=tuple(Path(path) for path in request.files),
            _sources=tuple(datasets),
        )
    except BaseException:
        # Never leak open file handles if selection fails halfway.
        for dataset in datasets:
            try:
                dataset.close()
            except Exception:
                pass
        raise


def _jsonable(value: Any) -> Any:
    """Convert numpy/cftime/path values into JSON-serializable objects."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # datetime64 must be handled before the generic numpy branch: .item() would
    # turn it into an opaque nanosecond integer.
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
    # datetime64, cftime objects and anything else become readable strings.
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


def _dataset_summary(
    dataset: xr.Dataset, layout: DatasetLayout
) -> dict[str, object]:
    """Build the JSON-friendly summary shared by inspect and RawDataset."""
    # Data variables only; coordinates are reported separately.
    variables: dict[str, object] = {}
    for name, variable in dataset.data_vars.items():
        variables[name] = {
            "dims": list(variable.dims),
            "shape": list(variable.shape),
            "dtype": str(variable.dtype),
            "units": _jsonable(variable.attrs.get("units")),
            "long_name": _jsonable(variable.attrs.get("long_name")),
        }

    # Coordinates are cheap to inspect; no data loading is required.
    coordinates = {
        name: _coordinate_summary(dataset[name].values)
        for name in dataset.coords
        if dataset[name].ndim == 1
    }

    payload: dict[str, object] = {
        "dimensions": {name: int(size) for name, size in dataset.sizes.items()},
        "coordinates": list(dataset.coords),
        "variables": variables,
        "layout": layout.to_dict(),
        "attrs": {key: _jsonable(value) for key, value in dataset.attrs.items()},
    }

    # Member identifiers: scalar (ensemble_mean) or a full dimension (hindcast).
    if layout.member and layout.member in dataset.variables:
        members = np.asarray(dataset[layout.member].values).reshape(-1)
        payload["members"] = [int(value) for value in members]
        payload["member_is_dimension"] = layout.member in dataset.dims

    # Forecast leads, when present.
    if layout.lead and layout.lead in dataset.variables:
        leads = np.asarray(dataset[layout.lead].values).reshape(-1)
        payload["leads"] = [int(value) for value in leads]

    # Initialization range.
    if layout.init_time in dataset.variables:
        init_values = np.asarray(dataset[layout.init_time].values).reshape(-1)
        payload["init_time"] = {
            "count": int(init_values.size),
            "first": _jsonable(init_values[0]) if init_values.size else None,
            "last": _jsonable(init_values[-1]) if init_values.size else None,
        }

    # Grid ranges and orientation.
    if layout.latitude in dataset.variables:
        payload["latitude"] = _coordinate_summary(dataset[layout.latitude].values)
    if layout.longitude in dataset.variables:
        lon = np.asarray(dataset[layout.longitude].values)
        lon_summary = _coordinate_summary(lon)
        lon_summary["convention"] = (
            "0-360"
            if float(np.nanmin(lon)) >= 0 and float(np.nanmax(lon)) > 180
            else "-180-180"
        )
        payload["longitude"] = lon_summary

    return payload


def inspect_netcdf(path: Path) -> dict[str, object]:
    """Return a JSON-friendly summary used by the inspect command."""
    dataset = open_netcdf(path)
    try:
        layout = discover_layout(dataset)
        payload = _dataset_summary(dataset, layout)
        payload["source_files"] = [str(Path(path))]
        return payload
    finally:
        # inspect must never leak an open NetCDF handle.
        dataset.close()
