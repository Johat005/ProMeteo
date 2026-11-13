"""Load the YAML catalog and answer model/variable queries.

This module is the only place that knows the shape of ``config/models.yaml``.
It converts raw YAML into typed dataclasses and validates values early, so the
rest of the application can trust the catalog object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from prometeo.catalog.models import Catalog, GridSpec, ModelSpec, VariableSpec
from prometeo.shared.errors import CatalogError

# Accepted fields for each catalog mapping.  Rejecting unknown keys turns a
# typo in the YAML into an actionable error instead of a silently ignored
# setting, matching the strict behavior of the application config.
_CATALOG_FIELDS = {"version", "updated_at", "models"}
_MODEL_FIELDS = {
    "centre",
    "system",
    "dataset",
    "product_type",
    "product_forecast",
    "product_hindcast",
    "variables",
    "leads",
    "hindcast_years",
    "forecast_years",
    "members_hindcast",
    "members_forecast",
    "grid",
    "release",
    "release_day",
    "release_time",
    "time_convention",
    "source",
}
_GRID_FIELDS = {"nlat", "nlon", "lat_order", "lon_convention"}
_RELEASE_FIELDS = {"day", "time"}
_VARIABLE_FIELDS = {"cds", "netcdf", "cpt_field", "units_in", "units_out"}
_PRODUCT_TYPE_FIELDS = {"forecast", "hindcast"}


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: set[str], context: str
) -> None:
    """Raise when a mapping contains a key that is not part of the schema."""
    unknown = {str(key) for key in value} - allowed
    if unknown:
        raise CatalogError(
            f"Campos desconocidos en {context}: {', '.join(sorted(unknown))}.",
            hint=f"Campos válidos: {', '.join(sorted(allowed))}.",
        )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    """Require a YAML value to be a mapping and return it typed."""
    if not isinstance(value, Mapping):
        raise CatalogError(f"El campo '{field}' debe ser un mapa YAML.")
    return value


def _required(data: Mapping[str, Any], key: str, context: str) -> Any:
    """Return a required key or raise an error naming the missing field."""
    if key not in data:
        raise CatalogError(
            f"Falta el campo obligatorio '{key}' en {context}.",
        )
    return data[key]


def _as_str(value: object, field: str) -> str:
    """Convert a scalar to str, rejecting None."""
    if value is None:
        raise CatalogError(f"El campo '{field}' no puede estar vacío.")
    return str(value)


def _as_int(value: object, field: str) -> int:
    """Convert a scalar to int with a catalog-specific error message."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CatalogError(f"El campo '{field}' debe ser un entero.") from exc


def _range(value: object, field: str) -> tuple[int, int]:
    """Parse and validate a ``[start, end]`` year pair."""
    # Strings are sequences too, so explicitly reject them.
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CatalogError(f"El campo '{field}' debe ser una lista [inicio, fin].")

    # A year range must have exactly two elements.
    if len(value) != 2:
        raise CatalogError(f"El campo '{field}' debe tener exactamente dos años.")

    start = _as_int(value[0], field)
    end = _as_int(value[1], field)

    # Reversed ranges almost always indicate a YAML mistake.
    if start > end:
        raise CatalogError(f"El campo '{field}' tiene un rango invertido: {start} > {end}.")
    return start, end


def _leads(value: object, field: str) -> tuple[int, ...]:
    """Validate the lead list and return sorted unique values."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CatalogError(f"El campo '{field}' debe ser una lista de plazos.")

    # Deduplicate and sort so every downstream request is deterministic.
    leads = tuple(sorted({_as_int(item, field) for item in value}))
    if not leads:
        raise CatalogError(f"El campo '{field}' no puede estar vacío.")

    # Sprint 1 supports the six monthly leads exposed by CDS.
    invalid = [lead for lead in leads if not 1 <= lead <= 6]
    if invalid:
        raise CatalogError(
            f"Plazos inválidos en '{field}': {invalid}.",
            hint="Los plazos deben estar entre 1 y 6.",
        )
    return leads


def _variables(value: object, context: str) -> dict[str, VariableSpec]:
    """Parse the variables mapping into ``VariableSpec`` objects."""
    raw = _mapping(value, f"{context}.variables")
    variables: dict[str, VariableSpec] = {}

    # Every variable must provide all six mapping fields; partial entries are
    # rejected instead of producing half-populated specs.
    for key, item in raw.items():
        spec = _mapping(item, f"{context}.variables.{key}")
        _reject_unknown_keys(
            spec, _VARIABLE_FIELDS, f"{context}.variables.{key}"
        )
        variables[str(key)] = VariableSpec(
            key=str(key),
            cds=_as_str(_required(spec, "cds", f"{context}.variables.{key}"), "cds"),
            netcdf=_as_str(
                _required(spec, "netcdf", f"{context}.variables.{key}"), "netcdf"
            ),
            cpt_field=_as_str(
                _required(spec, "cpt_field", f"{context}.variables.{key}"),
                "cpt_field",
            ),
            units_in=_as_str(
                _required(spec, "units_in", f"{context}.variables.{key}"), "units_in"
            ),
            units_out=_as_str(
                _required(spec, "units_out", f"{context}.variables.{key}"), "units_out"
            ),
        )

    # A model without variables cannot produce any output.
    if not variables:
        raise CatalogError(f"El modelo {context} no define variables.")
    return variables


def _product_types(entry: Mapping[str, Any], context: str) -> tuple[str, str]:
    """Return (forecast, hindcast) product types from either schema form."""
    raw = entry.get("product_type")

    # Preferred schema: product_type: {forecast: ..., hindcast: ...}.
    if isinstance(raw, Mapping):
        _reject_unknown_keys(
            raw, _PRODUCT_TYPE_FIELDS, f"{context}.product_type"
        )
        forecast = _as_str(
            _required(raw, "forecast", f"{context}.product_type"),
            "product_type.forecast",
        )
        hindcast = _as_str(
            _required(raw, "hindcast", f"{context}.product_type"),
            "product_type.hindcast",
        )
        return forecast, hindcast

    # A single string means the same product type for both targets.
    if isinstance(raw, str):
        return raw, raw

    if raw is not None:
        raise CatalogError(
            f"El campo '{context}.product_type' debe ser un mapa o una cadena."
        )

    # Backward-compatible schema: product_forecast / product_hindcast.
    if "product_forecast" not in entry or "product_hindcast" not in entry:
        raise CatalogError(
            f"Falta el campo obligatorio 'product_type' en {context}.",
            hint="Defina product_type: {forecast: ..., hindcast: ...}.",
        )
    forecast = _as_str(
        _required(entry, "product_forecast", context), "product_forecast"
    )
    hindcast = _as_str(
        _required(entry, "product_hindcast", context), "product_hindcast"
    )
    return forecast, hindcast


def _model(model_id: str, value: object) -> ModelSpec:
    """Build one ``ModelSpec`` from its YAML mapping."""
    context = f"models.{model_id}"
    entry = _mapping(value, context)
    _reject_unknown_keys(entry, _MODEL_FIELDS, context)

    # Grid metadata is descriptive; the real grid is always read from the file.
    grid_raw = _mapping(entry.get("grid", {}), f"{context}.grid")
    _reject_unknown_keys(grid_raw, _GRID_FIELDS, f"{context}.grid")
    grid = GridSpec(
        nlat=_as_int(_required(grid_raw, "nlat", f"{context}.grid"), "grid.nlat"),
        nlon=_as_int(_required(grid_raw, "nlon", f"{context}.grid"), "grid.nlon"),
        lat_order=_as_str(grid_raw.get("lat_order", "descending"), "grid.lat_order"),
        lon_convention=_as_str(
            grid_raw.get("lon_convention", "0-360"), "grid.lon_convention"
        ),
    )

    # Release metadata is used for availability messages and diagnostics.
    # Both nested (release: {day, time}) and flat forms are accepted.
    release_raw = _mapping(entry.get("release", {}), f"{context}.release")
    _reject_unknown_keys(release_raw, _RELEASE_FIELDS, f"{context}.release")
    release_day = _as_int(
        release_raw.get("day", entry.get("release_day", 6)), "release.day"
    )
    release_time = _as_str(
        release_raw.get("time", entry.get("release_time", "12:00Z")), "release.time"
    )

    product_forecast, product_hindcast = _product_types(entry, context)

    # Assemble the typed model.  Required fields are validated as they are read.
    return ModelSpec(
        id=model_id,
        centre=_as_str(_required(entry, "centre", context), "centre"),
        system=_as_str(_required(entry, "system", context), "system"),
        dataset=_as_str(_required(entry, "dataset", context), "dataset"),
        product_forecast=product_forecast,
        product_hindcast=product_hindcast,
        variables=_variables(_required(entry, "variables", context), context),
        leads=_leads(_required(entry, "leads", context), "leads"),
        hindcast_years=_range(
            _required(entry, "hindcast_years", context), "hindcast_years"
        ),
        forecast_years=_range(
            _required(entry, "forecast_years", context), "forecast_years"
        ),
        members_hindcast=_as_int(
            _required(entry, "members_hindcast", context), "members_hindcast"
        ),
        members_forecast=_as_int(
            _required(entry, "members_forecast", context), "members_forecast"
        ),
        grid=grid,
        release_day=release_day,
        release_time=release_time,
        time_convention=_as_str(
            _required(entry, "time_convention", context), "time_convention"
        ),
        source=_as_str(entry.get("source", ""), "source"),
    )


def load_catalog(path: Path) -> Catalog:
    """Read and parse the versioned YAML catalog."""
    catalog_path = Path(path)

    # Read the file first; missing or unreadable catalogs are configuration
    # errors, not YAML errors.
    try:
        text = catalog_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CatalogError(
            f"No se encontró el catálogo de modelos: {catalog_path}",
            hint="Verifique la ruta de config/models.yaml.",
        ) from exc
    except OSError as exc:
        raise CatalogError(
            f"No se pudo leer el catálogo de modelos: {catalog_path}",
            hint=str(exc),
        ) from exc

    # Parse YAML separately so syntax errors get a clear message.
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise CatalogError(
            f"El catálogo no es YAML válido: {catalog_path}",
            hint=str(exc),
        ) from exc

    # Delegate the structural validation to the pure mapping parser.
    return parse_catalog_mapping(raw, source=str(catalog_path))


def parse_catalog_mapping(
    data: Mapping[str, object], *, source: str = "<catalog>"
) -> Catalog:
    """Convert a raw YAML mapping into typed catalog entities."""
    if not isinstance(data, Mapping):
        raise CatalogError(f"El catálogo {source} debe contener un mapa YAML.")

    _reject_unknown_keys(data, _CATALOG_FIELDS, source)

    # Top-level metadata is versioned for traceability.
    version = _as_int(data.get("version", 0), "version")
    updated_at = _as_str(data.get("updated_at", ""), "updated_at")
    models_raw = data.get("models") or {}

    if not isinstance(models_raw, Mapping):
        raise CatalogError(f"El campo 'models' de {source} debe ser un mapa.")

    # Build every model entry.  A failure names the offending model through the
    # context string used by _model().
    models = {
        str(model_id): _model(str(model_id), value)
        for model_id, value in models_raw.items()
    }
    return Catalog(version=version, updated_at=updated_at, models=models)


@dataclass
class CatalogRepository:
    """Read-only facade over the catalog."""

    catalog: Catalog

    @classmethod
    def from_path(cls, path: Path) -> "CatalogRepository":
        """Load the repository from a YAML file."""
        return cls(load_catalog(path))

    def list_models(self) -> list[ModelSpec]:
        """Return the models sorted by identifier."""
        return [self.catalog.get(model_id) for model_id in sorted(self.catalog.models)]

    def get_model(self, model_id: str) -> ModelSpec:
        """Return one model by identifier."""
        return self.catalog.get(model_id)

    def resolve_model(
        self, model_id: str | None, attributes: Mapping[str, object] | None = None
    ) -> ModelSpec:
        """Resolve by explicit identifier or file attributes."""
        # An explicit --model always wins.
        if model_id:
            return self.get_model(model_id)

        # Otherwise try the NetCDF global attributes.
        if attributes:
            return self.catalog.resolve(dict(attributes))

        # A single-model catalog is unambiguous even without attributes.
        if len(self.catalog.models) == 1:
            return next(iter(self.catalog.models.values()))

        # Multiple models and no hint: the user must choose.
        raise CatalogError(
            "No se indicó un modelo y el catálogo contiene varios.",
            hint="Use --model para seleccionar uno.",
        )

    def get_variable(self, model_id: str, variable: str) -> VariableSpec:
        """Return one variable specification."""
        return self.get_model(model_id).variable(variable)

    def supported_leads(self, model_id: str) -> tuple[int, ...]:
        """Return the supported forecast leads."""
        return self.get_model(model_id).leads
