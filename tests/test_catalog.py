"""YAML catalog loading and typed queries.

The catalog is configuration, so these tests pin the Sprint 1 model entry and
exercise the parser's validation.  A typo here would silently produce wrong CDS
requests, which is why the checks are explicit.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from prometeo.catalog.repository import CatalogRepository, parse_catalog_mapping
from prometeo.shared.errors import CatalogError

# Repository root, then the real catalog file used by the application.
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "models.yaml"


class CatalogTests(unittest.TestCase):
    """Verify the real catalog and the YAML parser."""

    @classmethod
    def setUpClass(cls) -> None:
        # Load once per test class; the catalog is read-only.
        cls.repository = CatalogRepository.from_path(CATALOG)

    @staticmethod
    def _minimal_catalog() -> dict[str, Any]:
        """Return a small valid catalog mapping for parser tests."""
        return {
            "version": 1,
            "models": {
                "x": {
                    "centre": "x",
                    "system": "1",
                    "dataset": "d",
                    "product_type": {"forecast": "f", "hindcast": "h"},
                    "variables": {
                        "t2m": {
                            "cds": "2m_temperature",
                            "netcdf": "t2m",
                            "cpt_field": "t2m",
                            "units_in": "K",
                            "units_out": "C",
                        }
                    },
                    "leads": [1],
                    "hindcast_years": [1981, 2016],
                    "forecast_years": [2017, 2026],
                    "members_hindcast": 25,
                    "members_forecast": 51,
                    "grid": {"nlat": 180, "nlon": 360},
                    "time_convention": "c3s_forecastmonth_1_is_init",
                }
            },
        }

    def test_seas5_is_loaded(self) -> None:
        """The Sprint 1 model must match the C3S/requirements values."""
        model = self.repository.get_model("ecmwf-seas5")
        self.assertEqual(model.centre, "ecmwf")
        self.assertEqual(model.system, "51")
        self.assertEqual(model.dataset, "seasonal-monthly-single-levels")
        self.assertEqual(model.leads, (1, 2, 3, 4, 5, 6))
        self.assertEqual(model.hindcast_years, (1981, 2016))
        self.assertEqual(model.grid.nlat, 180)
        self.assertEqual(model.grid.nlon, 360)
        self.assertEqual(model.product_type("hindcast"), "monthly_mean")
        self.assertEqual(model.product_type("forecast"), "ensemble_mean")

    def test_variables_map_to_cpt(self) -> None:
        """Each variable must keep its CDS/NetCDF/CPT mapping and units."""
        model = self.repository.get_model("ecmwf-seas5")
        self.assertEqual(model.variable("t2m").cpt_field, "t2m")
        self.assertEqual(model.variable("tprate").cpt_field, "prate")
        self.assertEqual(model.variable("tprate").units_out, "mm/month")
        self.assertEqual(model.variable("msnsrf").units_out, "W m-2")

        # Sprint 1 requires all five catalog variables.
        self.assertEqual(len(model.variables), 5)

    def test_unknown_model_is_actionable(self) -> None:
        """Unknown model errors must list the available models."""
        with self.assertRaises(CatalogError) as caught:
            self.repository.get_model("does-not-exist")
        self.assertIn("ecmwf-seas5", caught.exception.hint or "")

    def test_unknown_variable_lists_options(self) -> None:
        """Unknown variable errors must list the available variables."""
        model = self.repository.get_model("ecmwf-seas5")
        with self.assertRaises(CatalogError) as caught:
            model.variable("rh2m")
        self.assertIn("t2m", caught.exception.hint or "")

    def test_lead_membership(self) -> None:
        """Only leads 1–6 are supported in Sprint 1."""
        model = self.repository.get_model("ecmwf-seas5")
        self.assertTrue(model.supports_lead(6))
        self.assertFalse(model.supports_lead(7))

    def test_resolve_from_attributes(self) -> None:
        """A file's global attributes can identify the model."""
        model = self.repository.catalog.resolve(
            {"originating_centre": "ecmwf", "system": "51"}
        )
        self.assertEqual(model.id, "ecmwf-seas5")

    def test_resolve_unknown_attributes_is_actionable(self) -> None:
        """No matching attributes is an error, not a silent fallback."""
        with self.assertRaises(CatalogError):
            self.repository.catalog.resolve({"originating_centre": "noaa"})

    def test_unknown_top_level_key_is_rejected(self) -> None:
        """A typo at the catalog root must fail instead of being ignored."""
        data = self._minimal_catalog()
        data["typo"] = True
        with self.assertRaises(CatalogError) as caught:
            parse_catalog_mapping(data)
        self.assertIn("typo", caught.exception.message)

    def test_unknown_model_key_is_rejected(self) -> None:
        """A typo in a model field must fail instead of being ignored."""
        data = self._minimal_catalog()
        data["models"]["x"]["soruce"] = "typo"
        with self.assertRaises(CatalogError) as caught:
            parse_catalog_mapping(data)
        self.assertIn("soruce", caught.exception.message)

    def test_unknown_variable_key_is_rejected(self) -> None:
        """A typo in a variable field must fail instead of being ignored."""
        data = self._minimal_catalog()
        data["models"]["x"]["variables"]["t2m"]["unit_in"] = "K"
        with self.assertRaises(CatalogError) as caught:
            parse_catalog_mapping(data)
        self.assertIn("unit_in", caught.exception.message)

    def test_missing_product_type_is_actionable(self) -> None:
        """The error must name product_type, not the legacy flat field."""
        data = self._minimal_catalog()
        del data["models"]["x"]["product_type"]
        with self.assertRaises(CatalogError) as caught:
            parse_catalog_mapping(data)
        self.assertIn("product_type", caught.exception.message)

    def test_invalid_lead_range_is_rejected(self) -> None:
        """The parser rejects leads outside 1–6 during catalog loading."""
        with self.assertRaises(CatalogError):
            parse_catalog_mapping(
                {
                    "version": 1,
                    "models": {
                        "broken": {
                            "centre": "x",
                            "system": "1",
                            "dataset": "d",
                            "product_type": {"forecast": "a", "hindcast": "b"},
                            "variables": {
                                "t2m": {
                                    "cds": "c",
                                    "netcdf": "n",
                                    "cpt_field": "c",
                                    "units_in": "K",
                                    "units_out": "C",
                                }
                            },
                            # Deliberately invalid: 0 and 7 are outside 1–6.
                            "leads": [0, 7],
                            "hindcast_years": [1981, 2016],
                            "forecast_years": [2017, 2026],
                            "members_hindcast": 25,
                            "members_forecast": 51,
                            "grid": {"nlat": 180, "nlon": 360},
                            "time_convention": "c3s_forecastmonth_1_is_init",
                        }
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
