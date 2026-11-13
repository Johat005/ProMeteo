"""Download orchestration: cache reuse, dry-run and retrieval.

The service is the only place that combines request building, cache checks and
the CDS client.  The future CLI calls ``download_products()`` for the normal
two-product workflow, and the conversion flow can call the same method for
automatic downloads.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from prometeo.catalog.repository import CatalogRepository
from prometeo.download.cache import (
    build_manifest,
    cache_is_valid,
    cache_paths,
    write_manifest,
)
from prometeo.download.client import CDSClient
from prometeo.download.request import (
    DEFAULT_LEADS,
    GLOBAL_AREA,
    DownloadRequest,
    area_name,
    build_request,
    describe_request,
    to_cds_payload,
)
from prometeo.shared.errors import CacheError, CatalogError, DownloadError
from prometeo.shared.io import ensure_directory
from prometeo.shared.logging import get_logger
from prometeo.shared.timeutils import YearMonth

_LOGGER = get_logger("download")

# NetCDF starts either with the classic "CDF" magic or with the HDF5
# signature used by NetCDF-4.  These checks catch GRIB or HTML payloads before
# they are published into the cache with a .nc name.
_NETCDF_MAGIC = (b"CDF", b"\x89HDF\r\n\x1a\n")


def _normalize_downloaded_file(path: Path) -> None:
    """Validate a CDS payload and unwrap a single-member ZIP response.

    CDS may return a ZIP when the NetCDF conversion splits a large request into
    several files.  The cache stores one NetCDF per request, so a single member
    is extracted transparently; multiple members raise an actionable error
    instead of caching a ZIP with a ``.nc`` extension.
    """
    if zipfile.is_zipfile(path):
        temporary_name: str | None = None
        try:
            with zipfile.ZipFile(path) as archive:
                members = [
                    info
                    for info in archive.infolist()
                    if not info.is_dir() and info.filename.lower().endswith(".nc")
                ]
                if len(members) != 1:
                    raise DownloadError(
                        f"CDS devolvió un ZIP con {len(members)} archivo(s) NetCDF.",
                        hint=(
                            "El caché admite un NetCDF por petición. Divida la "
                            "petición por variable, año o mes (por ejemplo, con "
                            "separate=True) y vuelva a intentarlo."
                        ),
                    )

                # Extract before replacing the archive so Windows can close the
                # ZIP handle before the atomic rename.
                with archive.open(members[0]) as source:
                    descriptor, temporary_name = tempfile.mkstemp(
                        prefix=f".{path.name}.",
                        suffix=".tmp",
                        dir=path.parent,
                    )
                    with os.fdopen(descriptor, "wb") as destination:
                        shutil.copyfileobj(source, destination)
            if temporary_name is None:  # pragma: no cover - defensive
                raise DownloadError("No se pudo extraer el NetCDF del ZIP.")
            os.replace(temporary_name, path)
        except zipfile.BadZipFile as exc:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            raise DownloadError(
                f"CDS devolvió un ZIP corrupto: {path.name}",
                hint="Vuelva a intentarlo o reduzca el tamaño de la petición.",
            ) from exc
        except BaseException:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            raise

    with path.open("rb") as handle:
        header = handle.read(8)

    if header.startswith(_NETCDF_MAGIC):
        return
    if header.startswith(b"GRIB"):
        raise DownloadError(
            "CDS devolvió datos GRIB en lugar de NetCDF.",
            hint="Verifique que la petición use data_format='netcdf'.",
        )
    raise DownloadError(
        f"El archivo descargado no parece un NetCDF válido: {path.name}",
        hint="Revise la respuesta de CDS o vuelva a intentarlo.",
    )


@dataclass
class DownloadPlan:
    """Resolved download plan without touching the network."""

    request: DownloadRequest
    target: Path
    cache_hit: bool
    message: str
    payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        # Used by --dry-run and future --json output.
        return {
            "request": describe_request(self.request),
            "target": str(self.target),
            "cache_hit": self.cache_hit,
            "message": self.message,
            "payload": self.payload,
        }


@dataclass
class DownloadResult:
    """Result of a completed or reused download."""

    path: Path
    cached: bool
    checksum: str | None = None
    bytes_written: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation."""
        return {
            "path": str(self.path),
            "cached": self.cached,
            "checksum": self.checksum,
            "bytes_written": self.bytes_written,
        }


class DownloadService:
    """Coordinates cache checks and the CDS client."""

    def __init__(
        self,
        repository: CatalogRepository,
        cache_root: Path,
        client_factory: Callable[[], CDSClient] = CDSClient,
    ) -> None:
        self.repository = repository

        # Do not create the cache directory here: plan()/dry_run() must not
        # have filesystem side effects.  download() creates it lazily.
        self.cache_root = Path(cache_root).expanduser().resolve()

        # A factory allows tests to inject a fake client and avoids constructing
        # cdsapi.Client (which may contact the network) until it is needed.
        if not callable(client_factory):
            raise CacheError("El constructor del cliente CDS no es invocable.")
        self.client_factory = client_factory

    def plan(self, request: DownloadRequest) -> DownloadPlan:
        """Resolve the destination and cache status."""
        # Compute the expected paths and inspect the sidecar without network.
        paths = cache_paths(request, self.cache_root)
        validation = cache_is_valid(paths, request)

        if validation.valid:
            message = f"cache hit: {paths.netcdf}"
        else:
            # The reason explains why a redownload is necessary.
            message = f"descarga requerida: {validation.reason}"

        return DownloadPlan(
            request=request,
            target=paths.netcdf,
            cache_hit=validation.valid,
            message=message,
            payload=to_cds_payload(request),
        )

    def dry_run(self, request: DownloadRequest) -> DownloadPlan:
        """Return the plan without network access."""
        plan = self.plan(request)

        # Add the human-readable request description to the plan message.
        plan.message = (
            f"{describe_request(request)}; destino {plan.target}; {plan.message}"
        )
        return plan

    def offline(self, request: DownloadRequest) -> DownloadResult:
        """Reuse the cache or fail without touching the network.

        The CLI no longer exposes --offline, but keeping this method makes the
        service useful for scripts that must never access the network.
        """
        paths = cache_paths(request, self.cache_root)

        # Offline mode verifies the checksum as well as the cheap checks.
        validation = cache_is_valid(paths, request, verify_checksum=True)
        validation.raise_if_invalid()

        return DownloadResult(
            path=paths.netcdf,
            cached=True,
            checksum=validation.checksum,
        )

    def download(self, request: DownloadRequest) -> DownloadResult | DownloadPlan:
        """Download one request or return a plan in dry-run mode."""
        # Dry-run never constructs a client and never touches the network.
        if request.dry_run:
            return self.dry_run(request)

        paths = cache_paths(request, self.cache_root)
        validation = cache_is_valid(paths, request)

        # Reuse a valid entry unless the caller explicitly forces a refresh.
        if validation.valid and not request.force:
            _LOGGER.info("Cache hit: %s", paths.netcdf)
            return DownloadResult(
                path=paths.netcdf,
                cached=True,
                checksum=validation.checksum,
            )

        # Make sure the model directory exists before writing the partial file.
        ensure_directory(paths.netcdf.parent)
        _LOGGER.info("Descargando %s", describe_request(request))

        # Constructing the client is lazy: only cache misses reach this point.
        client = self.client_factory()
        try:
            # The client writes to the ".part" file, never to the final path.
            client.retrieve(request.dataset, to_cds_payload(request), paths.partial)

            # A zero-byte or missing partial file is not a successful download.
            if not paths.partial.is_file() or paths.partial.stat().st_size == 0:
                raise CacheError(
                    "La descarga no produjo un archivo parcial válido.",
                    hint="Revise la conexión y vuelva a intentarlo.",
                )

            # Unwrap a ZIP response and reject GRIB/HTML payloads before they
            # are published into the cache as if they were NetCDF.
            _normalize_downloaded_file(paths.partial)

            # Atomic publish: the final .nc appears only after a complete file.
            os.replace(paths.partial, paths.netcdf)
        except BaseException:
            # Never leave a partial file behind after a failure.
            paths.partial.unlink(missing_ok=True)
            raise

        # Build and publish the sidecar only after the NetCDF is in place.
        manifest = build_manifest(paths, request)
        write_manifest(paths.manifest, manifest)

        # Reuse the manifest values so the file is not hashed twice.
        checksum = manifest.get("checksum")
        size = manifest.get("size")
        return DownloadResult(
            path=paths.netcdf,
            cached=False,
            checksum=checksum if isinstance(checksum, str) else None,
            bytes_written=size if isinstance(size, int) else 0,
        )

    def download_products(
        self,
        reference_month: YearMonth,
        *,
        model_id: str = "ecmwf-seas5",
        variables: str | Sequence[str] | None = None,
        separate: bool = False,
        area: tuple[float, float, float, float] = GLOBAL_AREA,
        leads: Sequence[int] = DEFAULT_LEADS,
        hindcast_leads: Sequence[int] | None = None,
        forecast_leads: Sequence[int] | None = None,
        targets: Sequence[str] | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> list[DownloadResult | DownloadPlan]:
        """Ensure the selected products for one reference month.

        By default each product is stored in one file with all requested
        variables. With ``separate=True`` one file per variable and product is
        produced instead. ``targets`` defaults to both ``hindcast`` and
        ``forecast``; callers may restrict it for smoke tests or resource-safe
        runs. ``hindcast_leads``/``forecast_leads`` override the shared
        ``leads`` value for one product.
        """
        # The Sprint 1 catalog contains a single model, but resolving it through
        # the repository keeps the service configuration-driven.
        model = self.repository.get_model(model_id)

        # Default to all five catalog variables so the cache is reusable for any
        # later conversion.  The conversion layer can still select a subset.
        if variables is None:
            names: tuple[str, ...] = tuple(sorted(model.variables))
        elif isinstance(variables, str):
            names = (variables,)
        else:
            names = tuple(dict.fromkeys(str(name) for name in variables))

        # Default remains "both products"; an explicit subset limits the run
        # (useful for smoke tests and low-resource machines).
        selected_targets = (
            tuple(targets) if targets is not None else ("hindcast", "forecast")
        )
        if not selected_targets:
            raise CatalogError(
                "Debe indicar al menos un producto para descargar.",
                hint="Valores válidos: hindcast, forecast o ambos.",
            )
        invalid_targets = [
            target
            for target in selected_targets
            if target not in {"hindcast", "forecast"}
        ]
        if invalid_targets:
            raise CatalogError(
                f"Productos desconocidos: {invalid_targets}.",
                hint="Valores válidos: hindcast, forecast o ambos.",
            )

        # Packed mode: one group with every variable.
        # Separate mode: one group per variable.
        groups = (
            [(name,) for name in names]
            if separate
            else [tuple(names)]
        )

        results: list[DownloadResult | DownloadPlan] = []

        # Preserve the requested order; by default hindcast first because it is
        # the long-lived cache entry.
        shared_leads = tuple(leads)
        leads_by_target = {
            "hindcast": (
                tuple(hindcast_leads)
                if hindcast_leads is not None
                else shared_leads
            ),
            "forecast": (
                tuple(forecast_leads)
                if forecast_leads is not None
                else shared_leads
            ),
        }

        for target in selected_targets:
            for group in groups:
                request = build_request(
                    model,
                    group,
                    leads_by_target[target],
                    area,
                    reference_month,
                    target=target,
                    output_dir=self.cache_root,
                    force=force,
                    dry_run=dry_run,
                )
                results.append(self.download(request))
        return results


def run_download(
    request: DownloadRequest, service: DownloadService
) -> DownloadResult | DownloadPlan:
    """Convenience wrapper used by the CLI."""
    # Keeps the CLI free of service-construction details.
    return service.download(request)


__all__ = [
    "DownloadPlan",
    "DownloadResult",
    "DownloadService",
    "area_name",
    "run_download",
]
