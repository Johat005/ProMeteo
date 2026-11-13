"""CDS request construction, cache helpers and download orchestration.

Everything here runs offline.  ``FakeClient`` stands in for the CDS API and
writes a tiny file, which lets us test the full service flow without network
access or credentials.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from prometeo.catalog.repository import CatalogRepository
from prometeo.download.cache import (
    cache_is_valid,
    cache_key,
    cache_paths,
    read_manifest,
)
from prometeo.download.request import (
    COLOMBIA_AREA,
    GLOBAL_AREA,
    area_name,
    build_request,
    parse_area,
    parse_leads,
    to_cds_payload,
)
from prometeo.download.service import DownloadPlan, DownloadResult, DownloadService
from prometeo.shared.errors import CacheError, CatalogError, DownloadError
from prometeo.shared.timeutils import YearMonth

# Small payload with the classic NetCDF magic so the download layer's content
# validation is exercised offline.
NETCDF_CONTENT = b"CDF\x01fake-netcdf-content"

# The real catalog is used so the tests also validate the model entry.
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "config" / "models.yaml"

# Fixed reference month: tests must never depend on the real clock.
MONTH = YearMonth(2026, 11)


class FakeClient:
    """Test double that writes a small file instead of contacting CDS."""

    def __init__(self) -> None:
        # Record every call so tests can assert cache hits and payloads.
        self.calls: list[tuple[str, dict[str, object], Path]] = []

    def retrieve(
        self, dataset: str, payload: dict[str, object], target: Path
    ) -> Path:
        """Write fake NetCDF bytes to the requested target path."""
        self.calls.append((dataset, dict(payload), Path(target)))
        Path(target).write_bytes(NETCDF_CONTENT)
        return Path(target)


class ZipClient:
    """Test double that returns a ZIP like CDS may do for split NetCDF output."""

    def __init__(self, members: tuple[str, ...] = ("data.nc",)) -> None:
        self.members = members
        self.calls: list[tuple[str, dict[str, object], Path]] = []

    def retrieve(
        self, dataset: str, payload: dict[str, object], target: Path
    ) -> Path:
        self.calls.append((dataset, dict(payload), Path(target)))
        with zipfile.ZipFile(target, "w") as archive:
            for name in self.members:
                archive.writestr(name, NETCDF_CONTENT)
        return Path(target)


class InvalidPayloadClient:
    """Test double that returns a non-NetCDF payload (for example an HTML page)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], Path]] = []

    def retrieve(
        self, dataset: str, payload: dict[str, object], target: Path
    ) -> Path:
        self.calls.append((dataset, dict(payload), Path(target)))
        Path(target).write_bytes(b"<html>not a netcdf file</html>")
        return Path(target)


class AreaTests(unittest.TestCase):
    """Verify area parsing and naming."""

    def test_named_areas(self) -> None:
        """Global and Colombia presets keep their documented bounds."""
        self.assertEqual(parse_area("global"), GLOBAL_AREA)
        self.assertEqual(parse_area("colombia"), COLOMBIA_AREA)

    def test_custom_area(self) -> None:
        """Custom areas are parsed in N,W,S,E order."""
        self.assertEqual(parse_area("13,-79,-5,-66"), (13.0, -79.0, -5.0, -66.0))

        # Known bounds collapse to the readable preset name.
        self.assertEqual(area_name((13.0, -79.0, -5.0, -66.0)), "colombia")

        # Unknown bounds keep their raw signed values for cache filenames.
        self.assertEqual(area_name((10.0, -75.0, -5.0, -66.0)), "10,-75,-5,-66")

    def test_custom_area_names_keep_full_precision(self) -> None:
        """Distinct float areas must not collapse to the same filename."""
        first = area_name((89.123456, -179.123456, -89.123456, 179.123456))
        second = area_name((89.123456, -179.123457, -89.123456, 179.123456))
        self.assertNotEqual(first, second)

    def test_invalid_area_is_rejected(self) -> None:
        """Wrong arity and inverted bounds are rejected."""
        with self.assertRaises(CatalogError):
            parse_area("13,-79,-5")
        with self.assertRaises(CatalogError):
            parse_area("20,10,-20,-10")


class LeadTests(unittest.TestCase):
    """Verify lead parsing."""

    def test_range_and_list(self) -> None:
        """Ranges and comma lists produce sorted, unique tuples."""
        self.assertEqual(parse_leads("1-6"), (1, 2, 3, 4, 5, 6))
        self.assertEqual(parse_leads("1,3,5"), (1, 3, 5))
        self.assertEqual(parse_leads("3,1-2"), (1, 2, 3))

    def test_out_of_range_is_rejected(self) -> None:
        """Only leads 1–6 are valid in Sprint 1."""
        with self.assertRaises(CatalogError):
            parse_leads("0-6")
        with self.assertRaises(CatalogError):
            parse_leads("7")


class RequestTests(unittest.TestCase):
    """Verify request construction and CDS payloads."""

    @classmethod
    def setUpClass(cls) -> None:
        # Load the real model once for all request tests.
        cls.model = CatalogRepository.from_path(CATALOG).get_model("ecmwf-seas5")

    def _request(self, target: str = "forecast", variables=None):
        """Build a request with the standard leads and global area."""
        return build_request(
            self.model,
            variables,
            (1, 2, 3, 4, 5, 6),
            GLOBAL_AREA,
            MONTH,
            target=target,
            output_dir=Path("/tmp/prometeo-test"),
        )

    def test_hindcast_request(self) -> None:
        """Hindcast always spans 1981–2016, all months, monthly_mean."""
        request = self._request("hindcast")
        self.assertEqual(request.years, (1981, 2016))
        self.assertEqual(request.months, tuple(range(1, 13)))
        self.assertEqual(request.product_type, "monthly_mean")
        self.assertEqual(len(request.variables), 5)

    def test_forecast_request_uses_reference_month(self) -> None:
        """Forecast only asks for the current reference year and month."""
        request = self._request("forecast")
        self.assertEqual(request.years, (2026, 2026))
        self.assertEqual(request.months, (11,))
        self.assertEqual(request.product_type, "ensemble_mean")

    def test_variable_subset(self) -> None:
        """A single variable is supported and maps to its CDS name."""
        request = self._request("forecast", variables="t2m")
        self.assertEqual(request.variables, ("t2m",))
        self.assertEqual(request.cds_variables, ("2m_temperature",))
        self.assertEqual(request.variable, "t2m")

    def test_unknown_variable_is_rejected(self) -> None:
        """Unknown variables fail against the catalog."""
        with self.assertRaises(CatalogError):
            self._request("forecast", variables="rh2m")

    def test_cds_payload(self) -> None:
        """The hindcast payload contains 36 years, 12 months and six leads."""
        payload = to_cds_payload(self._request("hindcast"))
        self.assertEqual(payload["originating_centre"], ["ecmwf"])
        self.assertEqual(payload["system"], ["51"])
        self.assertEqual(payload["product_type"], ["monthly_mean"])
        self.assertEqual(len(payload["year"]), 36)
        self.assertEqual(len(payload["month"]), 12)
        self.assertEqual(payload["leadtime_month"], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(payload["data_format"], "netcdf")
        self.assertNotIn("format", payload)

        # Global is the CDS default, so the area key is omitted.
        self.assertNotIn("area", payload)

    def test_custom_area_is_sent_to_cds(self) -> None:
        """Non-global areas are explicit in the CDS payload."""
        request = build_request(
            self.model,
            "t2m",
            (1,),
            (10.0, -75.0, -5.0, -66.0),
            MONTH,
            target="forecast",
            output_dir=Path("/tmp/prometeo-test"),
        )
        self.assertEqual(to_cds_payload(request)["area"], [10.0, -75.0, -5.0, -66.0])


class CacheTests(unittest.TestCase):
    """Verify cache identity, paths and validity."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.model = CatalogRepository.from_path(CATALOG).get_model("ecmwf-seas5")

    def _request(self, target: str, month: YearMonth = MONTH):
        """Build the standard packed request for a given target/month."""
        return build_request(
            self.model,
            None,
            (1, 2, 3, 4, 5, 6),
            GLOBAL_AREA,
            month,
            target=target,
            output_dir=Path("/tmp/cache"),
        )

    def test_hindcast_path_is_stable_across_months(self) -> None:
        """Hindcast files must be reused regardless of the reference month."""
        first = cache_paths(self._request("hindcast", YearMonth(2026, 11)), Path("/tmp/c"))
        second = cache_paths(self._request("hindcast", YearMonth(2026, 12)), Path("/tmp/c"))
        self.assertEqual(first, second)
        self.assertEqual(
            cache_key(self._request("hindcast", YearMonth(2026, 11))),
            cache_key(self._request("hindcast", YearMonth(2026, 12))),
        )

    def test_forecast_path_changes_with_month(self) -> None:
        """Forecast files are monthly and must not collide across months."""
        first = cache_paths(self._request("forecast", YearMonth(2026, 11)), Path("/tmp/c"))
        second = cache_paths(self._request("forecast", YearMonth(2026, 12)), Path("/tmp/c"))
        self.assertNotEqual(first.netcdf, second.netcdf)
        self.assertNotEqual(
            cache_key(self._request("forecast", YearMonth(2026, 11))),
            cache_key(self._request("forecast", YearMonth(2026, 12))),
        )

    def test_missing_cache_is_invalid(self) -> None:
        """A missing NetCDF is reported as invalid, never as a cache hit."""
        request = self._request("forecast")
        paths = cache_paths(request, Path("/tmp/does-not-exist"))
        validation = cache_is_valid(paths, request)
        self.assertFalse(validation.valid)
        self.assertIn("no existe", validation.reason)


class ServiceTests(unittest.TestCase):
    """Verify the full offline service flow with a fake CDS client."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = CatalogRepository.from_path(CATALOG)

    def _service(self, root: Path, client: FakeClient) -> DownloadService:
        """Create a service that always returns the supplied fake client."""
        return DownloadService(self.repository, root, client_factory=lambda: client)

    def test_downloads_both_products_and_reuses_cache(self) -> None:
        """First run downloads hindcast + forecast; second run is a cache hit."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            # Before downloading, both plans must report a cache miss.
            plans = [service.plan(self._build("hindcast")), service.plan(self._build("forecast"))]
            self.assertTrue(all(not plan.cache_hit for plan in plans))

            # One service call downloads both products.
            results = service.download_products(MONTH)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(isinstance(item, DownloadResult) for item in results))
            self.assertTrue(all(not item.cached for item in results))
            self.assertEqual(len(client.calls), 2)

            # The first call is the hindcast request.
            hindcast_payload = client.calls[0][1]
            self.assertEqual(hindcast_payload["product_type"], ["monthly_mean"])
            self.assertEqual(len(hindcast_payload["year"]), 36)
            self.assertEqual(len(hindcast_payload["month"]), 12)

            # The second call is the forecast request.
            forecast_payload = client.calls[1][1]
            self.assertEqual(forecast_payload["product_type"], ["ensemble_mean"])
            self.assertEqual(forecast_payload["year"], ["2026"])
            self.assertEqual(forecast_payload["month"], ["11"])

            # A second run must not call the network at all.
            cached = service.download_products(MONTH)
            self.assertTrue(all(item.cached for item in cached))  # type: ignore[union-attr]
            self.assertEqual(len(client.calls), 2)

            # The sidecar manifest must exist and contain provenance.
            manifest = read_manifest(Path(results[0].path).with_suffix(".nc.json"))  # type: ignore[union-attr]
            self.assertIn("checksum", manifest)
            self.assertEqual(manifest["request"]["target"], "hindcast")

    def test_separate_flag_writes_one_file_per_variable(self) -> None:
        """Separate mode produces 2 products × 5 variables = 10 requests."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            results = service.download_products(MONTH, separate=True)
            self.assertEqual(len(results), 10)
            self.assertEqual(len(client.calls), 10)

            # Every filename must mention its variable.
            self.assertTrue(
                all(
                    "t2m" in str(item.path)
                    or "tprate" in str(item.path)
                    or "u10" in str(item.path)
                    or "v10" in str(item.path)
                    or "msnsrf" in str(item.path)
                    for item in results
                )
            )  # type: ignore[union-attr]

    def test_dry_run_does_not_touch_network(self) -> None:
        """Dry-run returns plans and payloads without calling the client."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            plans = service.download_products(MONTH, dry_run=True)
            self.assertEqual(len(plans), 2)
            self.assertTrue(all(isinstance(plan, DownloadPlan) for plan in plans))
            self.assertEqual(len(client.calls), 0)

            # The CDS payload must be available for inspection.
            self.assertTrue(all(plan.payload for plan in plans))  # type: ignore[union-attr]

    def test_force_redownloads(self) -> None:
        """force=True bypasses a valid cache entry."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            service.download_products(MONTH)
            self.assertEqual(len(client.calls), 2)

            service.download_products(MONTH, force=True)
            self.assertEqual(len(client.calls), 4)

    def test_corrupt_cache_is_redownloaded(self) -> None:
        """A size mismatch invalidates the entry and triggers a new download."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            results = service.download_products(MONTH)
            self.assertEqual(len(client.calls), 2)

            # Truncate the hindcast file; the manifest still records the old size.
            first_path = Path(results[0].path)  # type: ignore[arg-type]
            first_path.write_bytes(b"truncated")

            # Only the damaged hindcast is redownloaded; the forecast is reused.
            service.download_products(MONTH)
            self.assertEqual(len(client.calls), 3)

    def test_offline_returns_cached_or_fails(self) -> None:
        """Offline mode reuses a valid entry or raises without network access."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)
            request = self._build("forecast")

            # Nothing cached yet: offline must fail.
            with self.assertRaises(CacheError):
                service.offline(request)

            # After a download, offline returns the cached result.
            service.download_products(MONTH)
            result = service.offline(request)
            self.assertTrue(result.cached)

    def test_variable_subset(self) -> None:
        """A single-variable request sends only that CDS variable."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            results = service.download_products(MONTH, variables="t2m")
            self.assertEqual(len(results), 2)
            for _, payload, _ in client.calls:
                self.assertEqual(payload["variable"], ["2m_temperature"])

    def test_dry_run_does_not_create_cache_root(self) -> None:
        """Dry-run must not create directories or touch the network."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cache"
            client = FakeClient()
            service = DownloadService(
                self.repository, root, client_factory=lambda: client
            )

            plans = service.download_products(MONTH, dry_run=True)
            self.assertEqual(len(plans), 2)
            self.assertFalse(root.exists())
            self.assertEqual(len(client.calls), 0)

    def test_single_member_zip_is_unwrapped(self) -> None:
        """A one-file NetCDF ZIP response is cached as the NetCDF itself."""
        with tempfile.TemporaryDirectory() as tmp:
            client = ZipClient(("data.nc",))
            service = DownloadService(
                self.repository, Path(tmp), client_factory=lambda: client
            )

            results = service.download_products(MONTH, variables="t2m")
            self.assertEqual(len(results), 2)
            for result in results:
                self.assertTrue(
                    Path(result.path).read_bytes().startswith(b"CDF\x01")  # type: ignore[arg-type]
                )
            self.assertEqual(len(client.calls), 2)

    def test_multi_member_zip_is_rejected(self) -> None:
        """Split NetCDF ZIPs must not be published as a single .nc file."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = ZipClient(("one.nc", "two.nc"))
            service = DownloadService(
                self.repository, root, client_factory=lambda: client
            )
            request = self._build("forecast")

            with self.assertRaises(DownloadError):
                service.download(request)

            paths = cache_paths(request, root)
            self.assertFalse(paths.netcdf.exists())
            self.assertFalse(paths.partial.exists())
            self.assertEqual(len(client.calls), 1)

    def test_non_netcdf_payload_is_rejected(self) -> None:
        """HTML or GRIB payloads must not be cached as NetCDF."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = InvalidPayloadClient()
            service = DownloadService(
                self.repository, root, client_factory=lambda: client
            )
            request = self._build("forecast")

            with self.assertRaises(DownloadError):
                service.download(request)

            paths = cache_paths(request, root)
            self.assertFalse(paths.netcdf.exists())
            self.assertFalse(paths.partial.exists())

    def test_per_product_leads(self) -> None:
        """Hindcast and forecast can use different lead windows."""
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            service = self._service(Path(tmp), client)

            results = service.download_products(
                MONTH,
                variables="t2m",
                hindcast_leads=(1, 2),
                forecast_leads=(6,),
            )
            self.assertEqual(len(results), 2)
            self.assertEqual(
                client.calls[0][1]["leadtime_month"], ["1", "2"]
            )
            self.assertEqual(client.calls[1][1]["leadtime_month"], ["6"])

    def _build(self, target: str):
        """Build the packed request used by plan() assertions."""
        model = self.repository.get_model("ecmwf-seas5")
        return build_request(
            model,
            None,
            (1, 2, 3, 4, 5, 6),
            GLOBAL_AREA,
            MONTH,
            target=target,
            output_dir=Path("/tmp/cache"),
        )


if __name__ == "__main__":
    unittest.main()
