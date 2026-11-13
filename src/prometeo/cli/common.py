"""Shared helpers for the ProMeteo command-line commands.

The CLI is intentionally thin: it parses arguments, loads configuration and
delegates to the service layers.  Scientific work belongs in reader/canonical/
cpt, never in this module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from prometeo.catalog.models import ModelSpec
from prometeo.catalog.repository import CatalogRepository
from prometeo.diagnostics.findings import DiagnosticReport
from prometeo.reader.reader import ReadRequest
from prometeo.shared.config import AppConfig
from prometeo.shared.errors import CatalogError


def split_csv(value: str | None) -> tuple[str, ...] | None:
    """Split a comma-separated option into stripped, non-empty values."""
    if value is None:
        return None

    # Drop empty items so "t2m,,prate" behaves like "t2m,prate".
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or None


def split_int_csv(value: str | None) -> tuple[int, ...] | None:
    """Split a comma-separated option into integers."""
    items = split_csv(value)
    if items is None:
        return None

    try:
        return tuple(int(item) for item in items)
    except ValueError as exc:
        raise CatalogError(
            f"Se esperaban números enteros separados por coma: {value!r}",
            hint="Ejemplo: --members 0,1,2.",
        ) from exc


def resolve_model(
    repository: CatalogRepository,
    model_id: str | None,
    attributes: Mapping[str, object] | None = None,
) -> ModelSpec:
    """Resolve a model by id or, when omitted, from NetCDF attributes."""
    # The repository decides whether an explicit id, attributes or the
    # single-model default applies.
    return repository.resolve_model(model_id, attributes)


def catalog_variables(
    model: ModelSpec, available: Iterable[str]
) -> tuple[str, ...]:
    """Return catalog variables present in the file, preserving catalog order."""
    available_set = set(available)

    # Iterate over the catalog mapping (YAML order), not the file order, so the
    # output is deterministic.
    return tuple(name for name in model.variables if name in available_set)


def build_read_request(
    files: Iterable[str | Path],
    variables: Iterable[str],
    *,
    leads: tuple[int, ...] | None = None,
    init_start: str | None = None,
    init_end: str | None = None,
    area: tuple[float, float, float, float] | None = None,
    members: tuple[int, ...] | None = None,
) -> ReadRequest:
    """Create a reader request using the common CLI option names."""
    # ReadRequest is immutable and stores files as Path objects.
    return ReadRequest(
        files=tuple(Path(file) for file in files),
        variables=tuple(variables),
        leads=leads,
        init_start=init_start,
        init_end=init_end,
        area=area,
        members=members,
    )


def print_json(payload: Any) -> None:
    """Print a JSON document with stable, human-readable formatting."""
    # sort_keys makes output deterministic and diff-friendly.
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    print()


def format_model(model: ModelSpec) -> dict[str, object]:
    """Return the JSON-friendly representation used by model commands."""
    # This shape is shared by ``list-models --json`` and
    # ``describe-model --json``.
    return {
        "id": model.id,
        "centre": model.centre,
        "system": model.system,
        "dataset": model.dataset,
        "products": {
            "forecast": model.product_forecast,
            "hindcast": model.product_hindcast,
        },
        "variables": {
            key: {
                "cds": spec.cds,
                "netcdf": spec.netcdf,
                "cpt_field": spec.cpt_field,
                "units_in": spec.units_in,
                "units_out": spec.units_out,
            }
            for key, spec in model.variables.items()
        },
        "leads": list(model.leads),
        "hindcast_years": list(model.hindcast_years),
        "forecast_years": list(model.forecast_years),
        "members_hindcast": model.members_hindcast,
        "members_forecast": model.members_forecast,
        "grid": {
            "nlat": model.grid.nlat,
            "nlon": model.grid.nlon,
            "lat_order": model.grid.lat_order,
            "lon_convention": model.grid.lon_convention,
        },
        "release": {
            "day": model.release_day,
            "time": model.release_time,
        },
        "time_convention": model.time_convention,
        "source": model.source,
    }


def print_report(report: DiagnosticReport, *, as_json: bool = False) -> None:
    """Print a diagnostic report as JSON or one line per finding."""
    if as_json:
        # DiagnosticReport.to_dict() owns the stable JSON schema, including
        # counts and the process exit code.
        print_json(report.to_dict())
        return

    if not report.findings:
        print("Sin hallazgos.")
        return

    # Human-readable output: one finding per line, with its corrective action.
    for finding in report.findings:
        severity = getattr(finding.severity, "value", str(finding.severity))
        print(f"{severity} [{finding.check_id}] {finding.location}: {finding.message}")
        if finding.action:
            print(f"  acción: {finding.action}")

    print(
        "Resumen: "
        f"{len(report.errors())} error(es), "
        f"{len(report.warnings())} advertencia(s), "
        f"{len(report.info())} informativo(s)."
    )


def error(message: str) -> None:
    """Write a user-facing error to stderr."""
    # Errors always go to stderr, keeping stdout clean for JSON results.
    print(message, file=sys.stderr)


def load_repository(config: AppConfig) -> CatalogRepository:
    """Load the catalog described by the application configuration."""
    # The repository validates the YAML and raises CatalogError on problems.
    return CatalogRepository.from_path(config.catalog_path)
