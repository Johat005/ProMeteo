"""Build immutable CDS download requests from catalog entries.

A ``DownloadRequest`` represents **one CDS request**.  Sprint 1 always creates
two of them per run: one hindcast request and one forecast request.  Keeping the
request immutable makes dry-runs, cache keys and tests deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from prometeo.catalog.models import ModelSpec
from prometeo.shared.errors import CatalogError
from prometeo.shared.timeutils import YearMonth

# CDS area order is N, W, S, E.
GLOBAL_AREA = (90.0, -180.0, -90.0, 180.0)
COLOMBIA_AREA = (13.0, -79.0, -5.0, -66.0)

# Sprint 1 always requests the six monthly leads.
DEFAULT_LEADS = (1, 2, 3, 4, 5, 6)
_MAX_LEAD = 6


@dataclass(frozen=True)
class DownloadRequest:
    """One CDS request: one product type, one lead window and one area."""

    model_id: str
    centre: str
    system: str
    dataset: str
    variables: tuple[str, ...]        # Canonical names used inside ProMeteo.
    cds_variables: tuple[str, ...]    # Names sent to the CDS API.
    product_type: str                 # "monthly_mean" or "ensemble_mean".
    leads: tuple[int, ...]
    area: tuple[float, float, float, float]
    reference_month: YearMonth
    target: str                       # "hindcast" or "forecast".
    years: tuple[int, int]
    months: tuple[int, ...]
    output_dir: Path
    force: bool = False
    dry_run: bool = False

    @property
    def variable(self) -> str | None:
        """Return the single canonical variable, when the request is per-variable."""
        # Packed requests contain several variables and therefore have no
        # single "variable" value.
        return self.variables[0] if len(self.variables) == 1 else None

    @property
    def cds_variable(self) -> str | None:
        """Return the single CDS variable, when the request is per-variable."""
        return self.cds_variables[0] if len(self.cds_variables) == 1 else None


def _validate_area(area: tuple[float, float, float, float]) -> None:
    """Validate N, W, S, E bounds and ordering."""
    if len(area) != 4:
        raise CatalogError(
            "El área debe tener cuatro valores en orden N,W,S,E.",
            hint="Ejemplo: 13,-79,-5,-66.",
        )

    # Convert once so both ints and floats are accepted.
    north, west, south, east = (float(value) for value in area)

    # Physical bounds.
    if not (-90.0 <= north <= 90.0 and -90.0 <= south <= 90.0):
        raise CatalogError("Las latitudes deben estar entre -90 y 90.")
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise CatalogError("Las longitudes deben estar entre -180 y 180.")

    # CDS expects north > south and west < east.
    if north <= south:
        raise CatalogError("El límite norte debe ser mayor que el límite sur.")
    if west >= east:
        raise CatalogError("El límite oeste debe ser menor que el límite este.")


def parse_area(value: str) -> tuple[float, float, float, float]:
    """Parse global, colombia or N,W,S,E into CDS order."""
    text = value.strip().lower()

    # Named presets keep common CLI usage short.
    if text == "global":
        return GLOBAL_AREA
    if text == "colombia":
        return COLOMBIA_AREA

    # Custom areas must contain exactly four comma-separated numbers.
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise CatalogError(
            f"Área inválida: {value!r}",
            hint="Use 'global', 'colombia' o cuatro valores N,W,S,E, por ejemplo 13,-79,-5,-66.",
        )

    try:
        area = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise CatalogError(
            f"Área inválida: {value!r}",
            hint="Los cuatro valores deben ser números.",
        ) from exc

    # Reuse the same bounds/order checks as build_request().
    _validate_area(area)  # type: ignore[arg-type]
    return area  # type: ignore[return-value]


def _format_coordinate(value: float) -> str:
    """Format a coordinate without losing precision.

    ``repr()`` is the shortest string that round-trips to the same float, so
    distinct areas never collapse to the same cache filename.  Integral values
    are rendered without the trailing ``.0`` for readability.
    """
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


def area_name(area: tuple[float, float, float, float]) -> str:
    """Return global, colombia or the raw N,W,S,E values."""
    # Named presets produce short, readable filenames.
    if tuple(area) == GLOBAL_AREA:
        return "global"
    if tuple(area) == COLOMBIA_AREA:
        return "colombia"

    # Custom areas keep the raw signed values, e.g. "10,-75,-5,-66".
    return ",".join(_format_coordinate(float(value)) for value in area)


def _parse_lead_token(token: str) -> list[int]:
    """Expand one "N" or "A-B" lead token into a list of integers."""
    text = token.strip()
    if "-" in text:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError(text)

        start, end = int(parts[0]), int(parts[1])
        if start > end:
            raise ValueError(text)
        return list(range(start, end + 1))

    return [int(text)]


def parse_leads(value: str) -> tuple[int, ...]:
    """Parse ``1-6`` or ``1,2,3`` and enforce the 1..6 window."""
    text = value.strip()
    if not text:
        raise CatalogError("Debe indicar al menos un plazo.")

    # A comma-separated list may mix ranges and single values.
    leads: list[int] = []
    try:
        for token in text.split(","):
            leads.extend(_parse_lead_token(token))
    except ValueError as exc:
        raise CatalogError(
            f"Plazos inválidos: {value!r}",
            hint="Use '1-6' o una lista como '1,3,5'.",
        ) from exc

    # Reject out-of-range values before building a CDS request.
    invalid = sorted({lead for lead in leads if not 1 <= lead <= _MAX_LEAD})
    if invalid:
        raise CatalogError(
            f"Plazos fuera de rango: {invalid}.",
            hint=f"Los plazos válidos están entre 1 y {_MAX_LEAD}.",
        )

    # Sorted, deduplicated output makes cache keys deterministic.
    return tuple(sorted(set(leads)))


def _normalize_variables(
    model: ModelSpec, variables: str | Sequence[str] | None
) -> tuple[str, ...]:
    """Normalize a variable argument and validate it against the catalog."""
    if variables is None:
        # Default: every catalog variable, sorted for deterministic payloads.
        names = tuple(sorted(model.variables))
    elif isinstance(variables, str):
        # A single variable is convenient for smoke tests and CLI usage.
        names = (variables,)
    else:
        # Preserve the caller's order but remove duplicates.
        names = tuple(dict.fromkeys(str(name) for name in variables))

    if not names:
        raise CatalogError("Debe indicar al menos una variable.")

    # Fail fast if any name is unknown, with the catalog's helpful message.
    for name in names:
        model.variable(name)
    return names


def _normalize_leads(model: ModelSpec, leads: Sequence[int]) -> tuple[int, ...]:
    """Deduplicate, sort and validate leads against the model."""
    normalized = tuple(sorted(set(int(lead) for lead in leads)))
    if not normalized:
        raise CatalogError("Debe indicar al menos un plazo.")

    # A lead may be globally valid (1–6) but unsupported by this model.
    unsupported = [lead for lead in normalized if not model.supports_lead(lead)]
    if unsupported:
        raise CatalogError(
            f"Plazos no disponibles para el modelo '{model.id}': {unsupported}.",
            hint=f"Plazos válidos: {', '.join(str(lead) for lead in model.leads)}.",
        )
    return normalized


def build_request(
    model: ModelSpec,
    variables: str | Sequence[str] | None,
    leads: Sequence[int],
    area: tuple[float, float, float, float],
    reference_month: YearMonth,
    *,
    target: str = "forecast",
    output_dir: Path,
    force: bool = False,
    dry_run: bool = False,
) -> DownloadRequest:
    """Create a request after validating variables, leads, area and years."""
    # Only the two product targets are meaningful.
    if target not in {"forecast", "hindcast"}:
        raise CatalogError(
            f"Tipo de producto desconocido: {target!r}",
            hint="Valores válidos: 'forecast' o 'hindcast'.",
        )

    # Normalize and validate every variable/lead/area before constructing the
    # immutable request.
    names = _normalize_variables(model, variables)
    specs = tuple(model.variable(name) for name in names)
    normalized_leads = _normalize_leads(model, leads)
    _validate_area(area)

    if target == "hindcast":
        # Hindcast is a fixed historical reforecast: all years, all months.
        years = model.hindcast_years
        months = tuple(range(1, 13))
    else:
        # Forecast is operational: only the current reference year and month.
        years = (reference_month.year, reference_month.year)
        months = (reference_month.month,)

    return DownloadRequest(
        model_id=model.id,
        centre=model.centre,
        system=model.system,
        dataset=model.dataset,
        variables=names,
        cds_variables=tuple(spec.cds for spec in specs),
        product_type=model.product_type(target),
        leads=normalized_leads,
        area=tuple(float(value) for value in area),  # type: ignore[arg-type]
        reference_month=reference_month,
        target=target,
        years=years,
        months=months,
        output_dir=Path(output_dir),
        force=force,
        dry_run=dry_run,
    )


def to_cds_payload(request: DownloadRequest) -> dict[str, object]:
    """Return the CDS API payload for the request."""
    # CDS expects lists of strings for most selectors.
    payload: dict[str, object] = {
        "originating_centre": [request.centre],
        "system": [request.system],
        "variable": list(request.cds_variables),
        "product_type": [request.product_type],
        "year": [str(year) for year in range(request.years[0], request.years[1] + 1)],
        "month": [f"{month:02d}" for month in request.months],
        "leadtime_month": [str(lead) for lead in request.leads],
        # The current CDS Retrieve API uses "data_format" (the old "format"
        # key is rejected with HTTP 422 "invalid param 'format'").
        "data_format": "netcdf",
    }

    # CDS defaults to the global area when "area" is omitted.
    if tuple(request.area) != GLOBAL_AREA:
        payload["area"] = list(request.area)
    return payload


def describe_request(request: DownloadRequest) -> str:
    """Return a short human-readable request description."""
    variables = ", ".join(request.variables)

    # Collapse single-year ranges to one number.
    years = (
        str(request.years[0])
        if request.years[0] == request.years[1]
        else f"{request.years[0]}-{request.years[1]}"
    )

    # "todos" is clearer than listing the twelve months.
    months = (
        "todos"
        if request.months == tuple(range(1, 13))
        else ", ".join(f"{month:02d}" for month in request.months)
    )
    leads = ", ".join(str(lead) for lead in request.leads)

    # This string is used by --dry-run and by the download log.
    return (
        f"{request.target} {request.product_type}: variables [{variables}]; "
        f"años {years}; meses {months}; plazos {leads}; "
        f"área {area_name(request.area)}"
    )
