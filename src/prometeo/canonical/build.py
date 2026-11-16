"""Build the canonical dataset from raw C3S NetCDF data.

The canonical layer is the contract between modules.  It:

* averages the ensemble explicitly;
* normalizes longitudes to ``-180..180`` ascending;
* derives one ``target_period`` per init/lead pair;
* converts source units to physical canonical units (K, m s-1, W m-2);
* records provenance;
* can split the data into hindcast (X) and forecast (Z) partitions.

No file I/O or CPT formatting happens here.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import xarray as xr

from prometeo import __version__
from prometeo.canonical.dataset import CanonicalDataset
from prometeo.catalog.models import ModelSpec, VariableSpec
from prometeo.reader.reader import RawDataset, ReadRequest
from prometeo.shared.errors import CanonicalError
from prometeo.shared.timeutils import YearMonth, derive_target_month, parse_year_month

# Canonical physical units required by Sprint 1 (FR-CN-11).
_CANONICAL_UNITS = {
    "t2m": "K",
    "tprate": "m s-1",
    "u10": "m s-1",
    "v10": "m s-1",
    "msnsrf": "W m-2",
}

# Established unit aliases seen in NetCDF/GRIB metadata.
_T2M_KELVIN = {"k", "kelvin", "kelvins", "degrees kelvin"}
_T2M_CELSIUS = {"c", "degc", "deg c", "°c", "celsius", "degrees celsius"}
_RATE_MPS = {
    "m s-1",
    "m/s",
    "m s^-1",
    "ms-1",
    "m s**-1",
    "metres per second",
    "meters per second",
}
_RATE_MMPS = {"mm s-1", "mm/s", "mm s^-1", "mm s**-1"}
_RATE_MMDAY = {"mm day-1", "mm/day", "mm d-1", "mm d^-1", "mm/day"}
_WIND_KMH = {"km h-1", "km/h", "km h^-1", "km h**-1"}
_WIND_KNOTS = {"kt", "knot", "knots"}
_RADIATION_WM2 = {"w m-2", "w/m2", "w m^-2", "w m**-2", "w/m^2"}
_RADIATION_KWM2 = {"kw m-2", "kw/m2", "kw m^-2", "kw m**-2"}

_TARGET_PATTERN = re.compile(r"^\d{4}-\d{2}$")


def _normalize_unit_text(source_units: str) -> str:
    """Normalize a units string for alias comparison."""
    return " ".join(str(source_units).strip().lower().replace("**", "^").split())


def _unit_transform(variable: str, source_units: str) -> tuple[float, float, str]:
    """Return (factor, offset, canonical_units) for one variable."""
    if variable not in _CANONICAL_UNITS:
        raise CanonicalError(
            f"Variable sin unidades canónicas definidas: '{variable}'.",
            hint=f"Variables soportadas: {', '.join(sorted(_CANONICAL_UNITS))}.",
        )

    unit = _normalize_unit_text(source_units)
    canonical = _CANONICAL_UNITS[variable]

    if variable == "t2m":
        if unit in _T2M_KELVIN:
            return 1.0, 0.0, canonical
        if unit in _T2M_CELSIUS:
            # Celsius to Kelvin.
            return 1.0, 273.15, canonical

    elif variable == "tprate":
        if unit in _RATE_MPS:
            return 1.0, 0.0, canonical
        if unit in _RATE_MMPS:
            # mm/s -> m/s.
            return 1.0 / 1000.0, 0.0, canonical
        if unit in _RATE_MMDAY:
            # mm/day -> m/s.
            return 1.0 / (1000.0 * 86400.0), 0.0, canonical

    elif variable in {"u10", "v10"}:
        if unit in _RATE_MPS:
            return 1.0, 0.0, canonical
        if unit in _WIND_KMH:
            return 1.0 / 3.6, 0.0, canonical
        if unit in _WIND_KNOTS:
            return 0.5144444444, 0.0, canonical

    elif variable == "msnsrf":
        if unit in _RADIATION_WM2:
            return 1.0, 0.0, canonical
        if unit in _RADIATION_KWM2:
            return 1000.0, 0.0, canonical

    raise CanonicalError(
        f"Unidades no soportadas para '{variable}': {source_units!r}.",
        hint=f"Unidades canónicas esperadas: {canonical}.",
    )


def _convert_units(
    variable: str, values: np.ndarray | xr.DataArray, source_units: str
) -> tuple[np.ndarray | xr.DataArray, str]:
    """Convert source units to canonical physical units.

    Works for both numpy arrays (tests) and xarray DataArrays (build pipeline);
    arithmetic stays lazy for DataArrays.
    """
    factor, offset, canonical_units = _unit_transform(variable, source_units)
    converted = values * factor
    if offset:
        converted = converted + offset
    return converted, canonical_units


def _normalize_longitudes(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Convert 0..360 to -180..180 and return values plus sort order.

    Values greater than 180 are shifted by -360 (e.g. 350 -> -10).  The result
    is sorted ascending for the canonical contract, and the returned order can
    be used to reorder the data dimension consistently.
    """
    array = np.asarray(values, dtype=float)
    normalized = np.where(array > 180.0, array - 360.0, array)
    normalized = np.where(normalized < -180.0, normalized + 360.0, normalized)
    order = np.argsort(normalized, kind="stable")
    return normalized[order], order


def _select_canonical_variables(
    raw: RawDataset, model: ModelSpec, request: ReadRequest
) -> tuple[tuple[str, ...], tuple[VariableSpec, ...]]:
    """Choose the canonical variables requested and validate their presence."""
    if request.variables:
        names = tuple(request.variables)
    else:
        # Default: every catalog variable present in the raw file.
        names = tuple(
            name
            for name, spec in model.variables.items()
            if spec.netcdf in raw.data.data_vars
        )

    if not names:
        raise CanonicalError(
            "No hay variables canónicas para construir.",
            hint="Indique --variables o revise el archivo de entrada.",
        )

    specs: list[VariableSpec] = []
    missing: list[str] = []
    for name in names:
        spec = model.variable(name)
        specs.append(spec)
        if spec.netcdf not in raw.data.data_vars:
            missing.append(f"{name} ({spec.netcdf})")

    if missing:
        available = ", ".join(sorted(raw.data.data_vars)) or "(ninguna)"
        raise CanonicalError(
            f"Faltan variables en el archivo: {', '.join(missing)}.",
            hint=f"Variables disponibles: {available}.",
        )
    return names, tuple(specs)


def _month_of(value: object) -> int:
    """Return the calendar month of a datetime64/cftime/datetime value."""
    month = getattr(value, "month", None)
    if month is not None:
        return int(month)

    # numpy.datetime64 string form starts with YYYY-MM.
    text = str(value)
    try:
        return int(text[5:7])
    except ValueError as exc:
        raise CanonicalError(
            f"No se pudo interpretar la fecha de inicialización: {value!r}."
        ) from exc


def _resolve_hindcast_init_month(
    target: str,
    reference_month: YearMonth | None,
    hindcast_init_month: str | int | None,
) -> int | None:
    """Resolve which hindcast initialization month to keep."""
    if target != "hindcast":
        return None

    if hindcast_init_month is None or str(hindcast_init_month).lower() == "current":
        if reference_month is None:
            raise CanonicalError(
                "Se requiere el mes de referencia para filtrar el hindcast.",
                hint="Use --hindcast-init-month con un mes explícito o 'all'.",
            )
        return reference_month.month

    if str(hindcast_init_month).lower() == "all":
        # Explicit opt-out.  This may produce duplicate target periods; the
        # diagnostic gate will reject the result before CPT writing.
        return None

    try:
        month = int(hindcast_init_month)
    except (TypeError, ValueError) as exc:
        raise CanonicalError(
            f"Mes de inicialización del hindcast inválido: {hindcast_init_month!r}.",
            hint="Use 'current', 'all' o un mes entre 1 y 12.",
        ) from exc

    if not 1 <= month <= 12:
        raise CanonicalError(
            f"Mes de inicialización del hindcast fuera de rango: {month}.",
            hint="Use un mes entre 1 y 12.",
        )
    return month


def _filter_init_month(
    dataset: xr.Dataset, month: int
) -> xr.Dataset:
    """Keep only initializations whose calendar month matches ``month``."""
    values = np.asarray(dataset["init_time"].values).reshape(-1)
    mask = np.array([_month_of(value) == month for value in values], dtype=bool)

    if not mask.any():
        available = sorted({_month_of(value) for value in values})
        raise CanonicalError(
            f"No hay inicializaciones del mes {month} en el hindcast.",
            hint=f"Meses disponibles: {available}.",
        )
    return dataset.isel(init_time=np.where(mask)[0])


def _drop_extra_coordinates(dataset: xr.Dataset) -> xr.Dataset:
    """Keep only the coordinates required by the canonical contract."""
    keep = {"init_time", "lead", "latitude", "longitude"}
    extra = [name for name in dataset.coords if name not in keep]
    if extra:
        dataset = dataset.drop_vars(extra)
    return dataset


def build_canonical(
    raw: RawDataset,
    model: ModelSpec,
    request: ReadRequest,
    *,
    target: str = "forecast",
    reference_month: YearMonth | None = None,
    hindcast_init_month: str | int | None = "current",
) -> CanonicalDataset:
    """Normalize all requested variables to the canonical contract.

    ``target`` is ``forecast`` or ``hindcast``.  For hindcast, the default is to
    keep only initializations whose calendar month matches ``reference_month``;
    this avoids duplicate target periods and aligns X with the operational Z.
    """
    if target not in {"forecast", "hindcast"}:
        raise CanonicalError(
            f"Tipo de producto desconocido: {target!r}.",
            hint="Valores válidos: 'forecast' o 'hindcast'.",
        )

    # 1. Resolve requested variables and their source names.
    names, specs = _select_canonical_variables(raw, model, request)
    source_names = [spec.netcdf for spec in specs]

    # 2. Keep only the requested data variables, but preserve all coordinates.
    data = raw.data[source_names]

    # 3. Rename NetCDF variable names to canonical names where they differ.
    rename_vars = {
        spec.netcdf: name
        for name, spec in zip(names, specs)
        if spec.netcdf != name
    }
    if rename_vars:
        data = data.rename(rename_vars)

    # 4. Rename core dimensions/coordinates to their canonical names.
    layout = raw.layout
    rename_dims: dict[str, str] = {}
    for source, canonical in (
        (layout.init_time, "init_time"),
        (layout.lead, "lead"),
        (layout.latitude, "latitude"),
        (layout.longitude, "longitude"),
    ):
        if source != canonical:
            rename_dims[source] = canonical
    if rename_dims:
        data = data.rename(rename_dims)

    # 5. Hindcast month filtering happens before stacking.
    init_month = _resolve_hindcast_init_month(
        target, reference_month, hindcast_init_month
    )
    if init_month is not None:
        data = _filter_init_month(data, init_month)

    # 6. Ensemble handling: average a member dimension, drop a scalar member.
    #    Cast first so the average is accumulated in float64 (NFR-07).
    data = data.astype("float64")
    member_count = 1
    if layout.member:
        if layout.member in data.dims:
            member_count = int(data.sizes[layout.member])
            data = data.mean(dim=layout.member, skipna=True)
        elif layout.member in data.variables:
            # ensemble_mean: scalar 'number' coordinate, no averaging needed.
            member_count = 1
            data = data.drop_vars(layout.member)

    # 7. Convert every variable to its canonical physical units.
    for name, spec in zip(names, specs):
        variable = data[name]
        source_units = variable.attrs.get("units") or spec.units_in
        converted, canonical_units = _convert_units(
            name, variable, str(source_units)
        )
        converted.attrs = dict(variable.attrs)
        converted.attrs["units"] = canonical_units
        converted.attrs["canonical_variable"] = name
        data[name] = converted

    # 8. Normalize longitude to -180..180 ascending and latitude to
    #    descending (north -> south).  Both X and Z must use the same row order
    #    because CPT+ compares their latitude coordinates.
    lon_values = np.asarray(data["longitude"].values)
    normalized_lon, order = _normalize_longitudes(lon_values)
    data = data.isel(longitude=order)
    data = data.assign_coords(longitude=normalized_lon)

    lat_values = np.asarray(data["latitude"].values, dtype=float)
    if lat_values.size > 1 and not np.all(np.diff(lat_values) < 0):
        lat_order = np.argsort(lat_values, kind="stable")[::-1]
        data = data.isel(latitude=lat_order)

    # 9. Keep only canonical coordinates.
    data = _drop_extra_coordinates(data)

    # 10. Stack init_time x lead into the canonical target_period dimension.
    stacked = data.stack(target_period=("init_time", "lead"))
    index = stacked.indexes["target_period"]
    init_labels = index.get_level_values("init_time")
    lead_labels = index.get_level_values("lead")

    target_labels = [
        str(
            derive_target_month(
                init_value,
                int(lead_value),
                model.time_convention,
            )
        )
        for init_value, lead_value in zip(init_labels, lead_labels)
    ]

    # Replace the MultiIndex with plain canonical coordinates.
    stacked = stacked.drop_vars(["target_period", "init_time", "lead"])
    stacked = stacked.assign_coords(
        target_period=("target_period", target_labels),
        init_time=("target_period", init_labels),
        lead=("target_period", lead_labels),
    )
    stacked = stacked.transpose("target_period", "latitude", "longitude")

    # 11. Provenance attributes.
    init_months = sorted({_month_of(value) for value in init_labels})
    stacked.attrs = {
        "model": model.id,
        "centre": model.centre,
        "system": model.system,
        "product_type": model.product_type(target),
        "time_convention": model.time_convention,
        "reference_month": str(reference_month) if reference_month else "",
        "hindcast_init_month": (
            str(init_month)
            if target == "hindcast" and init_month is not None
            else ("all" if target == "hindcast" else "")
        ),
        "init_months": ",".join(str(month) for month in init_months),
        "source_files": ";".join(str(path) for path in raw.source_files),
        "variables": ",".join(names),
        "ensemble_members": member_count,
        "longitude_convention": "-180-180",
        "schema_version": "1",
        "tool_version": __version__,
    }

    return CanonicalDataset(stacked, schema_version="1")


def validate_canonical(canonical: CanonicalDataset) -> None:
    """Validate the minimum canonical invariants for the MVP."""
    data = canonical.data
    if not isinstance(data, xr.Dataset):
        raise CanonicalError("El objeto canónico no contiene un xarray.Dataset.")

    required_dims = {"target_period", "latitude", "longitude"}
    missing_dims = required_dims - set(data.dims)
    if missing_dims:
        raise CanonicalError(
            f"Faltan dimensiones canónicas: {', '.join(sorted(missing_dims))}.",
            hint=f"Dimensiones encontradas: {', '.join(sorted(data.dims))}.",
        )

    if not data.data_vars:
        raise CanonicalError("El objeto canónico no contiene variables.")

    # Every variable must use the canonical dimension order.
    for name, variable in data.data_vars.items():
        if tuple(variable.dims) != ("target_period", "latitude", "longitude"):
            raise CanonicalError(
                f"La variable '{name}' tiene dimensiones no canónicas: {variable.dims}.",
                hint="Se esperaba (target_period, latitude, longitude).",
            )
        if "units" not in variable.attrs:
            raise CanonicalError(
                f"La variable '{name}' no declara unidades canónicas."
            )

    # target_period: non-empty, unique, YYYY-MM.
    periods = np.asarray(data["target_period"].values).astype(str).reshape(-1)
    if periods.size == 0:
        raise CanonicalError("El objeto canónico no contiene periodos objetivo.")
    if len(set(periods.tolist())) != len(periods):
        duplicates = sorted({value for value in periods if list(periods).count(value) > 1})
        raise CanonicalError(
            "Hay periodos objetivo duplicados.",
            hint=f"Periodos repetidos: {duplicates[:5]}.",
        )
    for value in periods:
        if not _TARGET_PATTERN.match(value):
            raise CanonicalError(
                f"Periodo objetivo inválido: {value!r}.",
                hint="Use el formato YYYY-MM.",
            )
        parse_year_month(value)

    # Provenance coordinates align with target_period.
    for coordinate in ("init_time", "lead"):
        if coordinate not in data.coords:
            raise CanonicalError(f"Falta la coordenada canónica '{coordinate}'.")
        if data[coordinate].dims != ("target_period",):
            raise CanonicalError(
                f"La coordenada '{coordinate}' debe estar definida sobre target_period."
            )
        if data[coordinate].size != periods.size:
            raise CanonicalError(
                f"La coordenada '{coordinate}' no coincide con target_period."
            )

    # Latitude: 1-D, bounded, descending (north -> south), unique.
    latitude = np.asarray(data["latitude"].values, dtype=float).reshape(-1)
    if latitude.size == 0:
        raise CanonicalError("La latitud canónica está vacía.")
    if np.any(latitude < -90.0) or np.any(latitude > 90.0):
        raise CanonicalError("La latitud canónica está fuera de -90..90.")
    if latitude.size > 1 and not np.all(np.diff(latitude) < 0):
        raise CanonicalError(
            "La latitud canónica debe ser descendente (norte a sur)."
        )
    if len(set(latitude.tolist())) != latitude.size:
        raise CanonicalError("La latitud canónica contiene duplicados.")

    # Longitude: 1-D, bounded, ascending, unique.
    longitude = np.asarray(data["longitude"].values, dtype=float).reshape(-1)
    if longitude.size == 0:
        raise CanonicalError("La longitud canónica está vacía.")
    if np.any(longitude < -180.0) or np.any(longitude > 180.0):
        raise CanonicalError("La longitud canónica está fuera de -180..180.")
    if longitude.size > 1 and not np.all(np.diff(longitude) > 0):
        raise CanonicalError("La longitud canónica debe ser ascendente.")
    if len(set(longitude.tolist())) != longitude.size:
        raise CanonicalError("La longitud canónica contiene duplicados.")


def combine_canonical(
    first: CanonicalDataset, second: CanonicalDataset
) -> CanonicalDataset:
    """Combine two canonical datasets into one with unique target periods."""
    if set(first.variables) != set(second.variables):
        raise CanonicalError(
            "Los objetos canónicos no contienen el mismo conjunto de variables.",
            hint=(
                f"Primero: {sorted(first.variables)}; "
                f"segundo: {sorted(second.variables)}."
            ),
        )

    first_periods = list(
        np.asarray(first.data["target_period"].values).astype(str)
    )
    second_periods = list(
        np.asarray(second.data["target_period"].values).astype(str)
    )
    all_periods = first_periods + second_periods
    if len(set(all_periods)) != len(all_periods):
        raise CanonicalError(
            "Los objetos canónicos tienen periodos objetivo duplicados.",
            hint="No combine hindcast y forecast con el mismo cpt:T.",
        )

    try:
        combined = xr.concat(
            [first.data, second.data],
            dim="target_period",
            data_vars="minimal",
            coords="minimal",
            compat="override",
            join="outer",
            combine_attrs="override",
        )
    except Exception as exc:
        raise CanonicalError(
            "No se pudieron combinar los objetos canónicos.",
            hint=str(exc),
        ) from exc

    combined = combined.transpose("target_period", "latitude", "longitude")
    combined.attrs = dict(first.data.attrs)
    combined.attrs["partition"] = "xz"

    result = CanonicalDataset(combined, first.schema_version)
    validate_canonical(result)
    return result


def partition_canonical(
    canonical: CanonicalDataset, cutoff: YearMonth
) -> tuple[CanonicalDataset, CanonicalDataset]:
    """Split target periods into hindcast (before cutoff) and forecast."""
    periods = np.asarray(canonical.data["target_period"].values).astype(str)
    cutoff_text = str(cutoff)

    hindcast_mask = periods < cutoff_text
    forecast_mask = ~hindcast_mask

    hindcast_data = canonical.data.isel(
        target_period=np.where(hindcast_mask)[0]
    )
    forecast_data = canonical.data.isel(
        target_period=np.where(forecast_mask)[0]
    )

    # Provenance makes the partition explicit for later diagnostics/CPT.
    hindcast_data = hindcast_data.copy(deep=False)
    forecast_data = forecast_data.copy(deep=False)
    hindcast_data.attrs = dict(canonical.data.attrs)
    forecast_data.attrs = dict(canonical.data.attrs)
    hindcast_data.attrs["partition"] = "x"
    forecast_data.attrs["partition"] = "z"

    return (
        CanonicalDataset(hindcast_data, canonical.schema_version),
        CanonicalDataset(forecast_data, canonical.schema_version),
    )


def select_partition(
    canonical: CanonicalDataset, name: str, cutoff: YearMonth
) -> CanonicalDataset:
    """Select ``both``, ``x`` (hindcast) or ``z`` (forecast)."""
    normalized = str(name).strip().lower()
    if normalized in {"both", "all"}:
        return canonical.copy()
    if normalized in {"x", "hindcast"}:
        return partition_canonical(canonical, cutoff)[0]
    if normalized in {"z", "forecast"}:
        return partition_canonical(canonical, cutoff)[1]

    raise CanonicalError(
        f"Partición desconocida: {name!r}.",
        hint="Valores válidos: 'both', 'x' (hindcast) o 'z' (forecast).",
    )
