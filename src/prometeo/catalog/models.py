"""Typed catalog entities shared by the other modules.

The catalog is configuration, not code.  These dataclasses give the rest of
the application a typed, validated view over ``config/models.yaml`` so that
modules never need to parse YAML or hardcode model details.
"""

from __future__ import annotations

from dataclasses import dataclass

from prometeo.shared.errors import CatalogError


@dataclass(frozen=True)
class VariableSpec:
    """One canonical variable and its CDS/CPT counterparts.

    Keeping all names and units together avoids duplicating mappings in the
    download, canonical and CPT layers.
    """

    key: str          # Canonical name used inside ProMeteo, e.g. "tprate".
    cds: str          # Name sent to the CDS API, e.g. "total_precipitation".
    netcdf: str       # Name inside the downloaded NetCDF file.
    cpt_field: str    # Name written to the CPT header.
    units_in: str     # Units expected from CDS.
    units_out: str    # Units required by CPT.


@dataclass(frozen=True)
class GridSpec:
    """Expected source grid metadata. Actual grids are always read from the file."""

    nlat: int
    nlon: int
    # Descriptive metadata only: the converter reads the real grid from the file
    # and never silently flips or reorders it without an explicit rule.
    lat_order: str = "descending"
    lon_convention: str = "0-360"


@dataclass(frozen=True)
class ModelSpec:
    """A complete model entry from the catalog."""

    id: str
    centre: str
    system: str
    dataset: str
    product_forecast: str
    product_hindcast: str
    variables: dict[str, VariableSpec]
    leads: tuple[int, ...]
    hindcast_years: tuple[int, int]
    forecast_years: tuple[int, int]
    members_hindcast: int
    members_forecast: int
    grid: GridSpec
    release_day: int
    release_time: str
    time_convention: str
    source: str = ""

    def variable(self, name: str) -> VariableSpec:
        """Return a variable or raise a clear catalog error."""
        try:
            return self.variables[name]
        except KeyError:
            # Listing the valid options turns a typo into a self-service fix.
            available = ", ".join(sorted(self.variables)) or "(ninguna)"
            raise CatalogError(
                f"Variable desconocida '{name}' para el modelo '{self.id}'.",
                hint=f"Variables disponibles: {available}.",
            ) from None

    def supports_lead(self, lead: int) -> bool:
        """Return whether the lead is available."""
        return lead in self.leads

    def product_type(self, target: str) -> str:
        """Return the forecast or hindcast product type."""
        if target == "forecast":
            return self.product_forecast
        if target == "hindcast":
            return self.product_hindcast
        # Reject anything other than the two known targets.
        raise CatalogError(
            f"Tipo de producto desconocido: {target!r}",
            hint="Valores válidos: 'forecast' o 'hindcast'.",
        )

    def year_range(self, target: str) -> tuple[int, int]:
        """Return the forecast or hindcast year range."""
        if target == "forecast":
            return self.forecast_years
        if target == "hindcast":
            return self.hindcast_years
        # Same validation as product_type(), kept explicit for clarity.
        raise CatalogError(
            f"Tipo de producto desconocido: {target!r}",
            hint="Valores válidos: 'forecast' o 'hindcast'.",
        )


@dataclass(frozen=True)
class Catalog:
    """Versioned collection of model definitions."""

    version: int
    updated_at: str
    models: dict[str, ModelSpec]

    def get(self, model_id: str) -> ModelSpec:
        """Return a model or raise CatalogError."""
        try:
            return self.models[model_id]
        except KeyError:
            # Show the available models so the user can correct --model.
            available = ", ".join(sorted(self.models)) or "(ninguno)"
            raise CatalogError(
                f"Modelo desconocido: '{model_id}'.",
                hint=f"Modelos disponibles: {available}.",
            ) from None

    def resolve(self, attributes: dict[str, object]) -> ModelSpec:
        """Resolve a model from NetCDF global attributes.

        Download uses an explicit model id; inspect/validate may instead infer
        the model from the file's global attributes.
        """
        # Different C3S products use slightly different attribute names.
        centre = attributes.get("originating_centre") or attributes.get(
            "centre"
        ) or attributes.get("institution")
        system = attributes.get("system")
        dataset = attributes.get("dataset") or attributes.get("dataset_name")

        # Collect every model compatible with the supplied attributes.
        candidates: list[ModelSpec] = []
        for model in self.models.values():
            if centre is not None and str(model.centre).lower() != str(centre).lower():
                continue
            if system is not None and str(model.system) != str(system):
                continue
            if dataset is not None and str(model.dataset) != str(dataset):
                continue
            candidates.append(model)

        # Exactly one match is the only unambiguous answer.
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise CatalogError(
                "No se pudo resolver el modelo a partir de los atributos del archivo.",
                hint=(
                    "Indique --model explícitamente o revise los atributos "
                    "originating_centre, system y dataset."
                ),
            )

        # Multiple matches require the user to choose explicitly.
        available = ", ".join(sorted(model.id for model in candidates))
        raise CatalogError(
            "Varios modelos coinciden con los atributos del archivo.",
            hint=f"Candidatos: {available}. Indique --model explícitamente.",
        )
