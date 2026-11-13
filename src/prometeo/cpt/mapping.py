"""The single canonical-to-CPT variable and unit mapping table.

CPT+ v10 expects different units from the canonical layer:

* temperature in °C (canonical is K);
* precipitation in mm/month (canonical is m s-1);
* winds in m s-1;
* radiation in W m-2.

All of those transformations live here so no other module needs to know CPT
unit rules.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from prometeo.shared.errors import CPTError
from prometeo.shared.timeutils import month_length, parse_year_month


@dataclass(frozen=True)
class CPTField:
    """One canonical variable mapped to its CPT field and units."""

    canonical: str
    cpt_name: str
    units: str
    transform: str


CPT_FIELDS = {
    "t2m": CPTField("t2m", "t2m", "C", "celsius"),
    "tprate": CPTField("tprate", "prate", "mm/month", "mm_month"),
    "u10": CPTField("u10", "u10", "m s-1", "identity"),
    "v10": CPTField("v10", "v10", "m s-1", "identity"),
    "msnsrf": CPTField("msnsrf", "msnsrf", "W m-2", "identity"),
}


def resolve_field(variable: str) -> CPTField:
    """Return the CPT mapping or reject an unknown variable."""
    try:
        return CPT_FIELDS[variable]
    except KeyError:
        available = ", ".join(sorted(CPT_FIELDS))
        raise CPTError(
            f"Variable sin mapeo CPT: '{variable}'.",
            hint=f"Variables disponibles: {available}.",
        ) from None


def temperature_to_celsius(values: np.ndarray) -> np.ndarray:
    """Convert Kelvin values to degrees Celsius."""
    # Cast to float64 so integer or float32 inputs do not lose precision.
    return np.asarray(values, dtype="float64") - 273.15


def precipitation_to_mm_per_month(
    values: np.ndarray, target_period: str
) -> np.ndarray:
    """Convert m/s values to mm/month for the target month.

    ``1 m/s`` during one day is ``86400 m``; each metre is ``1000 mm``.
    Multiplying by the number of days in the target month gives mm/month.
    """
    period = parse_year_month(target_period)
    days = month_length(period)
    factor = days * 86400.0 * 1000.0
    return np.asarray(values, dtype="float64") * factor


def replace_missing(values: np.ndarray, missing: float = -999.0) -> np.ndarray:
    """Replace NaN values with the CPT missing value."""
    array = np.asarray(values, dtype="float64")
    # np.isnan works element-wise; infinities are preserved and later rejected
    # by the structural validator rather than silently converted.
    return np.where(np.isnan(array), float(missing), array)


def to_cpt_values(
    variable: str, values: np.ndarray, target_period: str
) -> tuple[np.ndarray, str, str]:
    """Convert canonical values to CPT values and units.

    Returns ``(values, cpt_units, cpt_field_name)``.
    """
    field = resolve_field(variable)

    if field.transform == "celsius":
        converted = temperature_to_celsius(values)
    elif field.transform == "mm_month":
        converted = precipitation_to_mm_per_month(values, target_period)
    elif field.transform == "identity":
        converted = np.asarray(values, dtype="float64")
    else:  # pragma: no cover - defensive: table and code must stay in sync.
        raise CPTError(
            f"Transformación CPT desconocida: {field.transform!r}.",
            hint="Revise prometeo/cpt/mapping.py.",
        )

    return converted, field.units, field.cpt_name
