"""Minimal Sprint 1 file and canonical checks.

The checks are read-only: they inspect structure, metadata and sampled data
quality, and return findings.  They never modify the input.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import numpy as np
import xarray as xr

from prometeo.canonical.build import validate_canonical as validate_canonical_contract
from prometeo.canonical.dataset import CanonicalDataset
from prometeo.catalog.models import ModelSpec
from prometeo.diagnostics.findings import Finding, error, info, warning
from prometeo.reader.reader import RawDataset, ReadRequest
from prometeo.shared.errors import CanonicalError, CatalogError
from prometeo.shared.timeutils import YearMonth, derive_target_month

_GLOBAL_AREA = (90.0, -180.0, -90.0, 180.0)

# Data-quality checks sample large files instead of loading a whole global cube.
_MAX_FULL_VALUES = 2_000_000
_MAX_SLICES = 12
_MAX_MEMBER_SAMPLES = 4

# Normalized unit aliases accepted by the checks.
_UNIT_ALIASES = {
    "k": {"k", "kelvin", "kelvins"},
    "m s-1": {"m s-1", "m/s", "m s^-1", "ms-1", "m s**-1"},
    "w m-2": {"w m-2", "w/m2", "w m^-2", "w m**-2"},
}


def _normalize_unit(value: object) -> str:
    """Collapse a units string for comparison."""
    return " ".join(str(value).strip().lower().replace("**", "^").split())


def _units_match(actual: object, expected: str) -> bool:
    """Return True when the actual units match the catalog units or an alias."""
    if actual is None:
        return False
    actual_text = _normalize_unit(actual)
    expected_text = _normalize_unit(expected)
    return actual_text == expected_text or actual_text in _UNIT_ALIASES.get(
        expected_text, set()
    )


def _coordinate_array(dataset: xr.Dataset, name: str) -> np.ndarray:
    return np.asarray(dataset[name].values)


def _check_layout(raw: RawDataset) -> list[Finding]:
    """Check that the discovered layout names exist in the dataset."""
    findings: list[Finding] = []
    layout = raw.layout
    for attribute, label in (
        ("latitude", "latitud"),
        ("longitude", "longitud"),
        ("init_time", "inicialización"),
        ("lead", "plazo"),
    ):
        name = getattr(layout, attribute)
        if not name or name not in raw.data.variables:
            findings.append(
                error(
                    "FILE-LAYOUT",
                    "file",
                    f"Falta la coordenada/dimensión de {label}.",
                    f"Revise el layout del NetCDF; se esperaba {label}.",
                )
            )
    return findings


def _check_grid(
    raw: RawDataset, model: ModelSpec, request: ReadRequest
) -> list[Finding]:
    """Check bounds, monotonicity, duplicates and grid metadata."""
    findings: list[Finding] = []
    layout = raw.layout

    if layout.latitude not in raw.data.variables:
        return findings
    if layout.longitude not in raw.data.variables:
        return findings

    latitude = _coordinate_array(raw.data, layout.latitude)
    longitude = _coordinate_array(raw.data, layout.longitude)

    if latitude.ndim != 1 or longitude.ndim != 1:
        findings.append(
            error(
                "GRID-DIM",
                "grid",
                "Latitud y longitud deben ser coordenadas 1-D.",
                "Use una rejilla regular en Sprint 1.",
            )
        )
        return findings

    if np.any(latitude < -90.0) or np.any(latitude > 90.0):
        findings.append(
            error(
                "GRID-LAT",
                "grid",
                "La latitud está fuera del rango -90..90.",
                "Revise la rejilla del archivo.",
            )
        )
    if np.any(longitude < -180.0) or np.any(longitude > 360.0):
        findings.append(
            error(
                "GRID-LON",
                "grid",
                "La longitud está fuera del rango -180..360.",
                "Revise la rejilla del archivo.",
            )
        )

    if latitude.size > 1:
        diffs = np.diff(latitude)
        if not (np.all(diffs > 0) or np.all(diffs < 0)):
            findings.append(
                error(
                    "GRID-LAT-ORDER",
                    "grid",
                    "La latitud no es monótona.",
                    "Ordene la rejilla o revise el archivo.",
                )
            )
    if longitude.size > 1:
        diffs = np.diff(longitude)
        if not (np.all(diffs > 0) or np.all(diffs < 0)):
            findings.append(
                error(
                    "GRID-LON-ORDER",
                    "grid",
                    "La longitud no es monótona.",
                    "Ordene la rejilla o revise el archivo.",
                )
            )

    if len(set(latitude.tolist())) != latitude.size:
        findings.append(
            error(
                "GRID-LAT-DUP",
                "grid",
                "La latitud contiene duplicados.",
                "Revise la rejilla del archivo.",
            )
        )
    if len(set(longitude.tolist())) != longitude.size:
        findings.append(
            error(
                "GRID-LON-DUP",
                "grid",
                "La longitud contiene duplicados.",
                "Revise la rejilla del archivo.",
            )
        )

    # Grid size is informational: regional subsets are legitimate.
    is_global = request.area is None or tuple(request.area) == _GLOBAL_AREA
    if is_global and (latitude.size, longitude.size) != (
        model.grid.nlat,
        model.grid.nlon,
    ):
        findings.append(
            info(
                "GRID-SIZE",
                "grid",
                (
                    f"La rejilla es {latitude.size}x{longitude.size}; "
                    f"el catálogo espera {model.grid.nlat}x{model.grid.nlon}."
                ),
                "Confirme si es una región o una actualización del catálogo.",
                actual_nlat=int(latitude.size),
                actual_nlon=int(longitude.size),
                catalog_nlat=model.grid.nlat,
                catalog_nlon=model.grid.nlon,
            )
        )
    elif not is_global:
        findings.append(
            info(
                "GRID-SUBSET",
                "grid",
                f"Rejilla regional de {latitude.size}x{longitude.size}.",
                "Ninguna acción requerida.",
            )
        )

    convention = (
        "0-360"
        if float(np.nanmin(longitude)) >= 0 and float(np.nanmax(longitude)) > 180
        else "-180-180"
    )
    findings.append(
        info(
            "GRID-CONVENTION",
            "grid",
            f"Longitud en convención {convention}.",
            "La normalización a -180..180 ocurre en canonical/.",
            convention=convention,
        )
    )
    return findings


def _check_variables(
    raw: RawDataset, model: ModelSpec, request: ReadRequest
) -> tuple[tuple[str, ...], list[Finding]]:
    """Check requested variables, their dimensions and their units."""
    findings: list[Finding] = []

    if request.variables:
        names = tuple(request.variables)
    else:
        # Without an explicit selection, validate the catalog variables present.
        names = tuple(
            name
            for name, spec in model.variables.items()
            if spec.netcdf in raw.data.data_vars
        )

    if not names:
        findings.append(
            error(
                "FILE-VARS",
                "file",
                "No se encontraron variables del catálogo en el archivo.",
                "Revise el archivo o indique --variables.",
            )
        )
        return names, findings

    known_netcdf = {spec.netcdf for spec in model.variables.values()}
    for name in names:
        try:
            spec = model.variable(name)
        except CatalogError as exc:
            findings.append(
                error(
                    "FILE-VAR-NAME",
                    name,
                    str(exc),
                    exc.hint or "Use una variable del catálogo.",
                )
            )
            continue

        if spec.netcdf not in raw.data.data_vars:
            findings.append(
                error(
                    "FILE-VAR-MISSING",
                    name,
                    f"Falta la variable '{name}' ({spec.netcdf}) en el archivo.",
                    "Descargue la variable o revise --variables.",
                )
            )
            continue

        variable = raw.data[spec.netcdf]
        if (
            raw.layout.latitude not in variable.dims
            or raw.layout.longitude not in variable.dims
        ):
            findings.append(
                error(
                    "FILE-VAR-DIMS",
                    name,
                    f"La variable '{name}' no incluye latitud y longitud.",
                    "Revise la estructura del archivo NetCDF.",
                )
            )

        actual_units = variable.attrs.get("units")
        if not _units_match(actual_units, spec.units_in):
            findings.append(
                error(
                    "FILE-UNITS",
                    name,
                    (
                        f"Unidades de '{name}' no coinciden: "
                        f"archivo={actual_units!r}, catálogo={spec.units_in!r}."
                    ),
                    "Revise la variable descargada o actualice el catálogo.",
                    actual=actual_units,
                    expected=spec.units_in,
                )
            )

    extra = sorted(set(raw.data.data_vars) - known_netcdf)
    if extra:
        findings.append(
            info(
                "FILE-VAR-EXTRA",
                "file",
                f"Variables fuera del catálogo: {', '.join(extra)}.",
                "Se ignorarán durante la conversión canónica.",
            )
        )

    return names, findings


def _check_leads(
    raw: RawDataset, model: ModelSpec, request: ReadRequest
) -> list[Finding]:
    """Check that requested leads are present."""
    findings: list[Finding] = []
    layout = raw.layout

    if layout.lead not in raw.data.variables:
        findings.append(
            error(
                "FILE-LEAD",
                "file",
                "Falta la coordenada de plazos (forecastMonth).",
                "Revise el archivo NetCDF.",
            )
        )
        return findings

    present = sorted(
        {int(value) for value in _coordinate_array(raw.data, layout.lead).reshape(-1)}
    )
    expected = (
        tuple(request.leads) if request.leads is not None else tuple(model.leads)
    )
    missing = sorted(set(expected) - set(present))
    if missing:
        findings.append(
            error(
                "FILE-LEAD-MISSING",
                "file",
                f"Faltan plazos solicitados: {missing}.",
                f"Plazos presentes: {present}. Descargue los plazos faltantes.",
                missing=missing,
                present=present,
            )
        )

    extra = sorted(set(present) - set(expected))
    if extra:
        severity = info if request.leads is not None else warning
        findings.append(
            severity(
                "FILE-LEAD-EXTRA",
                "file",
                f"Plazos presentes no solicitados: {extra}.",
                "Verifique la selección de plazos.",
                extra=extra,
            )
        )
    return findings


def _check_init(raw: RawDataset) -> list[Finding]:
    """Check that initialization times are present and unique."""
    findings: list[Finding] = []
    layout = raw.layout
    if layout.init_time not in raw.data.variables:
        return findings

    values = _coordinate_array(raw.data, layout.init_time).reshape(-1)
    if values.size == 0:
        findings.append(
            error(
                "FILE-INIT-EMPTY",
                "file",
                "No hay tiempos de inicialización.",
                "Revise el archivo NetCDF.",
            )
        )
        return findings

    index = raw.data[layout.init_time].to_index()
    duplicated = index[index.duplicated()]
    if len(duplicated):
        findings.append(
            error(
                "FILE-INIT-DUP",
                "file",
                f"Hay inicializaciones duplicadas: {list(duplicated[:5])}.",
                "Elimine archivos solapados o duplicados.",
            )
        )
    return findings


def _check_member(raw: RawDataset, model: ModelSpec) -> list[Finding]:
    """Report/validate the ensemble member count."""
    findings: list[Finding] = []
    layout = raw.layout
    if not layout.member or layout.member not in raw.data.variables:
        findings.append(
            info(
                "FILE-MEMBER-NONE",
                "file",
                "El archivo no declara una dimensión de ensamble.",
                "Puede ser un producto ensemble_mean ya promediado.",
            )
        )
        return findings

    values = _coordinate_array(raw.data, layout.member).reshape(-1)
    count = int(values.size)
    if count == 0:
        findings.append(
            error(
                "FILE-MEMBER-EMPTY",
                "file",
                "La dimensión de ensamble está vacía.",
                "Revise el archivo descargado.",
            )
        )
        return findings

    expected = {model.members_hindcast, model.members_forecast, 1}
    if count not in expected:
        findings.append(
            warning(
                "FILE-MEMBER-COUNT",
                "file",
                f"El ensamble tiene {count} miembros; el catálogo espera {sorted(expected)}.",
                "Confirme el producto o actualice el catálogo.",
                members=count,
            )
        )
    else:
        findings.append(
            info(
                "FILE-MEMBER",
                "file",
                f"Ensamble con {count} miembros.",
                "Ninguna acción requerida.",
                members=count,
            )
        )
    return findings


def _check_valid_time(raw: RawDataset, model: ModelSpec) -> list[Finding]:
    """Compare valid_time with init + forecastMonth when available."""
    findings: list[Finding] = []
    layout = raw.layout

    if not layout.valid_time or layout.valid_time not in raw.data.variables:
        findings.append(
            info(
                "FILE-VALID-TIME",
                "file",
                "No hay valid_time; se usará forecastMonth para derivar cpt:T.",
                "Ninguna acción requerida.",
            )
        )
        return findings

    try:
        valid = raw.data[layout.valid_time]
        if layout.init_time in valid.dims and layout.lead in valid.dims:
            init_value = raw.data[layout.init_time].values[0]
            for lead_index in range(min(3, valid.sizes[layout.lead])):
                lead = int(raw.data[layout.lead].values[lead_index])
                expected = derive_target_month(
                    init_value, lead, model.time_convention
                )
                actual = valid.isel(
                    {layout.init_time: 0, layout.lead: lead_index}
                ).values
                actual_ym = _year_month_from_value(actual)
                if actual_ym != expected:
                    findings.append(
                        warning(
                            "FILE-VALID-TIME-MISMATCH",
                            "file",
                            (
                                f"valid_time={actual_ym} no coincide con "
                                f"init+lead={expected} para el plazo {lead}."
                            ),
                            "Revise la convención temporal del archivo/catálogo.",
                        )
                    )
                    break
    except Exception as exc:
        findings.append(
            info(
                "FILE-VALID-TIME-SKIP",
                "file",
                f"No se pudo comparar valid_time: {exc}",
                "Ninguna acción requerida.",
            )
        )
    return findings


def _year_month_from_value(value: object) -> YearMonth:
    """Convert a datetime/cftime/string value to YearMonth."""
    year = getattr(value, "year", None)
    month = getattr(value, "month", None)
    if year is not None and month is not None:
        return YearMonth(int(year), int(month))
    text = str(value)
    return YearMonth(int(text[:4]), int(text[5:7]))


def _sample_combinations(
    dataset: xr.Dataset, dims: Sequence[str], max_slices: int
) -> list[dict[str, int]]:
    """Return evenly spaced index combinations for sampling."""
    indices_per_dim = []
    for dim in dims:
        size = int(dataset.sizes.get(dim, 0))
        if size == 0:
            return []
        count = min(max_slices, size)
        indices_per_dim.append(
            np.unique(np.linspace(0, size - 1, count, dtype=int)).tolist()
        )

    combinations = [
        dict(zip(dims, values)) for values in itertools.product(*indices_per_dim)
    ]
    if len(combinations) > max_slices:
        step = max(1, len(combinations) // max_slices)
        combinations = combinations[::step][:max_slices]
    return combinations


def _data_quality_findings(
    dataset: xr.Dataset,
    variables: Sequence[str],
    *,
    dims: Sequence[str],
    location_prefix: str,
    member_dim: str | None = None,
) -> list[Finding]:
    """Check NaN fraction and constant fields, sampling large datasets."""
    findings: list[Finding] = []

    for name in variables:
        if name not in dataset.data_vars:
            continue
        variable = dataset[name]
        sampled = variable.size > _MAX_FULL_VALUES

        if not sampled:
            values = np.asarray(variable.values)
            nan_count, total, minimum, maximum = _quality_stats(values)
        else:
            nan_count = 0
            total = 0
            minimum = np.inf
            maximum = -np.inf
            for selection in _sample_combinations(dataset, dims, _MAX_SLICES):
                # Also sample members when the selected slice is still large.
                if member_dim and member_dim in variable.dims:
                    member_size = int(variable.sizes[member_dim])
                    member_indices = np.unique(
                        np.linspace(
                            0,
                            member_size - 1,
                            min(_MAX_MEMBER_SAMPLES, member_size),
                            dtype=int,
                        )
                    ).tolist()
                    selection = {**selection, member_dim: member_indices}
                values = np.asarray(variable.isel(selection).values)
                slice_nan, slice_total, slice_min, slice_max = _quality_stats(values)
                nan_count += slice_nan
                total += slice_total
                if slice_total:
                    minimum = min(minimum, slice_min)
                    maximum = max(maximum, slice_max)

        if total == 0:
            continue

        fraction = nan_count / total
        location = f"{location_prefix}:{name}"
        if fraction >= 0.999999:
            findings.append(
                error(
                    "DATA-NAN",
                    location,
                    f"El campo '{name}' está prácticamente vacío ({fraction:.1%} NaN).",
                    "Revise la descarga o el área solicitada.",
                )
            )
        elif fraction > 0.5:
            findings.append(
                warning(
                    "DATA-NAN",
                    location,
                    f"El campo '{name}' tiene {fraction:.1%} de valores faltantes.",
                    "Confirme si la cobertura es suficiente.",
                    fraction=float(fraction),
                )
            )

        if minimum == maximum and total > 1:
            findings.append(
                warning(
                    "DATA-CONST",
                    location,
                    f"El campo '{name}' parece constante (valor={minimum}).",
                    "Revise la variable o la selección de datos.",
                )
            )

        if sampled:
            findings.append(
                info(
                    "DATA-SAMPLE",
                    location,
                    f"Calidad de '{name}' evaluada sobre una muestra de {total} valores.",
                    "Use un área/plazo menor si necesita revisar todo el campo.",
                    sampled_values=int(total),
                )
            )
    return findings


def _quality_stats(values: np.ndarray) -> tuple[int, int, float, float]:
    """Return (nan_count, total, min, max) for one array."""
    total = int(values.size)
    if total == 0:
        return 0, 0, np.inf, -np.inf

    if np.issubdtype(values.dtype, np.floating):
        nan_count = int(np.isnan(values).sum())
        if nan_count == total:
            return nan_count, total, np.nan, np.nan
        minimum = float(np.nanmin(values))
        maximum = float(np.nanmax(values))
        return nan_count, total, minimum, maximum

    return 0, total, float(np.min(values)), float(np.max(values))


def check_file(
    raw: RawDataset,
    model: ModelSpec,
    request: ReadRequest | None = None,
) -> list[Finding]:
    """Check the structure and contents of a raw NetCDF dataset."""
    request = request or ReadRequest(
        files=raw.source_files, variables=()
    )

    findings: list[Finding] = []
    findings.extend(_check_layout(raw))
    findings.extend(_check_grid(raw, model, request))
    names, variable_findings = _check_variables(raw, model, request)
    findings.extend(variable_findings)
    findings.extend(_check_leads(raw, model, request))
    findings.extend(_check_init(raw))
    findings.extend(_check_member(raw, model))
    findings.extend(_check_valid_time(raw, model))

    if names:
        dims = [
            dim
            for dim in (raw.layout.init_time, raw.layout.lead)
            if dim in raw.data.dims
        ]
        member_dim = (
            raw.layout.member
            if raw.layout.member and raw.layout.member in raw.data.dims
            else None
        )
        findings.extend(
            _data_quality_findings(
                raw.data,
                names,
                dims=dims,
                location_prefix="file",
                member_dim=member_dim,
            )
        )
    return findings


def check_canonical(canonical: CanonicalDataset) -> list[Finding]:
    """Check the canonical invariants required before CPT writing."""
    findings: list[Finding] = []
    try:
        validate_canonical_contract(canonical)
    except CanonicalError as exc:
        findings.append(
            error(
                "CANON-CONTRACT",
                "canonical",
                str(exc),
                exc.hint or "Revise la construcción canónica.",
            )
        )
    else:
        findings.append(
            info(
                "CANON-OK",
                "canonical",
                "El objeto canónico cumple el contrato mínimo.",
                "Ninguna acción requerida.",
            )
        )

    findings.extend(
        _data_quality_findings(
            canonical.data,
            tuple(canonical.variables),
            dims=("target_period",),
            location_prefix="canonical",
        )
    )
    return findings


def run_file_checks(
    raw: RawDataset, model: ModelSpec, request: ReadRequest | None = None
) -> list[Finding]:
    """Run the file checks and return their findings."""
    return check_file(raw, model, request)


def run_canonical_checks(canonical: CanonicalDataset) -> list[Finding]:
    """Run the canonical checks and return their findings."""
    return check_canonical(canonical)
