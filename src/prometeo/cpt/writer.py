"""Render canonical data as CPT+ v10+ gridded ASCII.

Sprint 1 writes one meteorological field per file.  Each target period becomes
one CPT block.  The writer streams blocks to a temporary sibling file, validates
the result, and only then publishes it atomically.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from prometeo.canonical.dataset import CanonicalDataset
from prometeo.cpt.mapping import CPTField, replace_missing, resolve_field, to_cpt_values
from prometeo.cpt.validator import NAMESPACE, validate_cpt
from prometeo.shared.errors import CPTError
from prometeo.shared.io import atomic_output_path
from prometeo.shared.timeutils import YearMonth, parse_year_month

MISSING = -999


def _format_month(value: object) -> str:
    """Format the month in the CPT format (YYYY-MM)."""
    # Canonical init_time/target_period may be datetime64, YearMonth or str.
    if isinstance(value, YearMonth):
        return str(value)

    text = str(value)
    if "/" in text:
        # Seasonal periods are out of Sprint 1 scope, but preserving them makes
        # the helper future-proof.
        return text

    try:
        return str(parse_year_month(text[:7]))
    except Exception as exc:
        raise CPTError(
            f"No se pudo formatear el periodo CPT: {value!r}.",
            hint="Use el formato YYYY-MM.",
        ) from exc


def _format_number(value: float) -> str:
    """Format numbers and write missing values as -999."""
    number = float(value)
    if math.isnan(number):
        return str(MISSING)
    if number == MISSING:
        # Write the missing sentinel exactly as -999, not -999.0000.
        return str(MISSING)
    if not math.isfinite(number):
        raise CPTError(
            f"Valor no finito en la salida CPT: {number!r}.",
            hint="Reemplace infinitos por NaN o por un valor válido.",
        )
    # Fixed precision keeps output deterministic across platforms.
    return f"{number:.4f}"


def _format_coordinate(value: float) -> str:
    """Format a latitude/longitude with two decimals."""
    return f"{float(value):.2f}"


def _resolve_variable(
    canonical: CanonicalDataset, variable: str | None
) -> tuple[str, CPTField]:
    """Resolve the variable to write and its CPT mapping."""
    if variable is None:
        names = canonical.variables
        if len(names) != 1:
            available = ", ".join(sorted(names)) or "(ninguna)"
            raise CPTError(
                "El archivo CPT debe contener un solo campo.",
                hint=f"Indique una variable. Disponibles: {available}.",
            )
        variable = names[0]

    # Raises CanonicalError if it does not exist; convert to a clear CPT error.
    try:
        canonical.variable(variable)
    except Exception as exc:
        raise CPTError(
            f"Variable canónica desconocida para CPT: '{variable}'.",
            hint=str(exc),
        ) from exc

    return variable, resolve_field(variable)


def _render_lines(
    canonical: CanonicalDataset, variable: str | None = None
) -> Iterator[str]:
    """Yield the CPT document line by line, one block per target period."""
    variable, field = _resolve_variable(canonical, variable)
    data = canonical.variable(variable)

    expected_dims = ("target_period", "latitude", "longitude")
    if tuple(data.dims) != expected_dims:
        raise CPTError(
            f"La variable '{variable}' no tiene dimensiones canónicas: {data.dims}.",
            hint=f"Se esperaba {expected_dims}.",
        )

    # Coordinate values are small and safe to materialize.
    periods = np.asarray(data["target_period"].values).astype(str)
    init_times = np.asarray(data["init_time"].values)
    latitudes = np.asarray(data["latitude"].values, dtype=float)
    longitudes = np.asarray(data["longitude"].values, dtype=float)

    nrow = int(latitudes.size)
    ncol = int(longitudes.size)
    if nrow == 0 or ncol == 0:
        raise CPTError("La rejilla CPT está vacía.")

    # Header.
    yield NAMESPACE
    yield "cpt:nfields=1"

    # CPT v10 requires a third header line listing every target period.  CPT+
    # uses it to build the file's time axis; without it, it tries to interpret
    # the first block header as dates and crashes in its season-labeling code.
    if periods.size == 0:
        raise CPTError("El CPT no contiene periodos objetivo.")
    yield "cpt:T " + " ".join(_format_month(period) for period in periods)

    # The longitude line is identical in every block.
    longitude_line = "  " + " ".join(
        _format_coordinate(value) for value in longitudes
    )

    for index, period in enumerate(periods):
        # Load one target-period field at a time, not the whole cube.
        block = data.isel(target_period=index).values
        cpt_values, units, cpt_name = to_cpt_values(variable, block, str(period))
        cpt_values = replace_missing(cpt_values)

        yield (
            f"cpt:field={cpt_name}, cpt:S={_format_month(init_times[index])}, "
            f"cpt:T={_format_month(period)}, cpt:nrow={nrow}, cpt:ncol={ncol}, "
            f"cpt:row=Y, cpt:col=X, cpt:units={units}, cpt:missing={MISSING}"
        )
        yield longitude_line

        for row_index in range(nrow):
            row = cpt_values[row_index]
            values = " ".join(_format_number(value) for value in row)
            yield f"{_format_coordinate(latitudes[row_index])} {values}"


def render_cpt(canonical: CanonicalDataset, variable: str | None = None) -> str:
    """Render one field and one block per target period.

    Sprint 1 writes a single meteorological field per file; multi-field output
    is deliberately left for Sprint 3.  This function builds the whole string
    in memory and is intended for tests/small outputs.  ``write_cpt`` streams.
    """
    return "\n".join(_render_lines(canonical, variable)) + "\n"


def write_cpt(
    canonical: CanonicalDataset, target: Path, variable: str | None = None
) -> Path:
    """Write and structurally validate a CPT file atomically."""
    destination = Path(target)

    # Write to a temporary sibling first.  The atomic context manager publishes
    # it only if validation succeeds.
    with atomic_output_path(destination) as temporary:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            for line in _render_lines(canonical, variable):
                handle.write(line)
                handle.write("\n")

        report = validate_cpt(temporary)
        if not report.valid:
            details = "; ".join(
                f"{issue.code}: {issue.message}" for issue in report.issues[:3]
            )
            raise CPTError(
                "El CPT generado no pasó la validación estructural.",
                hint=details,
            )

    return destination
