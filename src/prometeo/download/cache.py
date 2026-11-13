"""Cache keys, cache paths and sidecar manifests.

The cache is deliberately simple:

* the hindcast file is stable across reference months and is reused forever;
* the forecast file is keyed by the reference month;
* every NetCDF has a ``.nc.json`` sidecar with the request identity, size and
  checksum.

The download layer never opens NetCDF files; the sidecar is what lets it verify
an entry without violating the layer boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prometeo import __version__
from prometeo.download.request import DownloadRequest, area_name
from prometeo.shared.errors import CacheError
from prometeo.shared.io import (
    atomic_write_text,
    checksum_matches,
    safe_filename_component,
    sha256_file,
)


@dataclass(frozen=True)
class CachePaths:
    """Paths for one cached NetCDF, its manifest and its partial download."""

    netcdf: Path    # Final, atomically published file.
    manifest: Path  # Sidecar JSON describing the request and file.
    partial: Path   # Temporary ".part" file used while downloading.


@dataclass(frozen=True)
class CacheValidation:
    """Result of checking whether a cache entry is reusable."""

    valid: bool
    reason: str
    checksum: str | None = None

    def raise_if_invalid(self) -> None:
        """Raise CacheError when the entry is not reusable."""
        if not self.valid:
            raise CacheError(
                f"Entrada de caché no reutilizable: {self.reason}",
                hint="Vuelva a descargar sin --offline o use --force.",
            )


def _identity(request: DownloadRequest) -> dict[str, object]:
    """Return the stable identity fields used by the cache key."""
    identity: dict[str, object] = {
        "model_id": request.model_id,
        "centre": request.centre,
        "system": request.system,
        "dataset": request.dataset,
        "variables": list(request.variables),
        "cds_variables": list(request.cds_variables),
        "product_type": request.product_type,
        "leads": list(request.leads),
        "area": [float(value) for value in request.area],
        "target": request.target,
        "years": [request.years[0], request.years[1]],
        "months": list(request.months),
    }

    # The hindcast request is identical every month, so including the reference
    # month would force a pointless re-download each month.  The forecast is
    # monthly, so it must include the reference month.
    if request.target == "forecast":
        identity["reference_month"] = str(request.reference_month)
    return identity


def cache_key(request: DownloadRequest) -> str:
    """Build the stable cache key for a request."""
    # Canonical JSON (sorted keys, no spaces) makes the key independent of dict
    # insertion order and therefore stable across runs and Python versions.
    encoded = json.dumps(
        _identity(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _period_label(request: DownloadRequest) -> str:
    """Return the year or year-range component of the cache filename."""
    if request.target == "forecast":
        # Forecast files are monthly, so the period is the reference month.
        return str(request.reference_month)
    if request.years[0] == request.years[1]:
        return str(request.years[0])
    return f"{request.years[0]}-{request.years[1]}"


def _months_label(request: DownloadRequest) -> str:
    """Return a short label for the selected months."""
    # Hindcast uses all twelve months; avoid a long 01-02-...-12 suffix.
    if request.months == tuple(range(1, 13)):
        return "allmonths"
    return "month" + "-".join(f"{month:02d}" for month in request.months)


def _leads_label(request: DownloadRequest) -> str:
    """Return ``leads1-6`` for a contiguous range or a joined list otherwise."""
    leads = list(request.leads)
    if leads == list(range(leads[0], leads[-1] + 1)):
        return f"leads{leads[0]}-{leads[-1]}"
    return "leads" + "-".join(str(lead) for lead in leads)


def _file_stem(request: DownloadRequest) -> str:
    """Build the descriptive filename stem for one cache entry."""
    # Variable names are part of the name so packed and separate modes cannot
    # collide, and so a per-variable file is self-describing.
    variables = "-".join(request.variables)
    return (
        f"{request.target}_{request.product_type}_{_period_label(request)}_"
        f"{_months_label(request)}_{_leads_label(request)}_"
        f"{area_name(request.area)}_{variables}"
    )


def cache_paths(request: DownloadRequest, root: Path) -> CachePaths:
    """Return the per-request cache paths from section 3.4."""
    # Organize by model to keep future multi-model caches tidy.
    directory = Path(root) / safe_filename_component(request.model_id)

    # Sanitize the whole stem so region values, variable names and model ids are
    # safe on both Linux and Windows.
    filename = f"{safe_filename_component(_file_stem(request))}.nc"
    netcdf = directory / filename

    # The manifest and partial file are siblings, which keeps os.replace atomic.
    return CachePaths(
        netcdf=netcdf,
        manifest=netcdf.with_suffix(netcdf.suffix + ".json"),
        partial=netcdf.with_suffix(netcdf.suffix + ".part"),
    )


def cache_is_valid(
    paths: CachePaths, request: DownloadRequest, *, verify_checksum: bool = False
) -> CacheValidation:
    """Check the cache contract for a request.

    The cheap checks (existence, manifest key, size) run every time.  The full
    checksum is optional because hashing a multi-GB hindcast on every cache hit
    would make reuse almost as expensive as a download.
    """
    # 1. The NetCDF itself must exist.
    if not paths.netcdf.is_file():
        return CacheValidation(False, "el archivo NetCDF no existe")

    # 2. A file without its sidecar is not trusted.
    if not paths.manifest.is_file():
        return CacheValidation(False, "el manifiesto lateral no existe")

    # 3. The manifest must be readable JSON.
    try:
        manifest = read_manifest(paths.manifest)
    except CacheError as exc:
        return CacheValidation(False, f"manifiesto inválido: {exc}")

    # 4. The manifest must describe this exact request.
    if manifest.get("cache_key") != cache_key(request):
        return CacheValidation(False, "la clave del manifiesto no coincide")

    # 5. A size mismatch catches truncated or replaced files cheaply.
    expected_size = manifest.get("size")
    actual_size = paths.netcdf.stat().st_size
    if not isinstance(expected_size, int) or expected_size != actual_size:
        return CacheValidation(
            False,
            f"tamaño inesperado ({actual_size} bytes, se esperaban {expected_size})",
        )

    # 6. Optional deep verification.
    checksum = manifest.get("checksum")
    if verify_checksum and isinstance(checksum, str):
        if not checksum_matches(paths.netcdf, checksum):
            return CacheValidation(False, "el checksum no coincide")

    return CacheValidation(
        True,
        "entrada de caché válida",
        checksum if isinstance(checksum, str) else None,
    )


def build_manifest(paths: CachePaths, request: DownloadRequest) -> dict[str, Any]:
    """Build the sidecar manifest for a completed download."""
    try:
        # The file must already be published at this point.
        size = paths.netcdf.stat().st_size
        checksum = sha256_file(paths.netcdf)
    except OSError as exc:
        raise CacheError(
            f"No se pudo inspeccionar el archivo descargado: {paths.netcdf}",
            hint=str(exc),
        ) from exc

    # Everything needed to decide later whether the file can be reused.
    return {
        "cache_key": cache_key(request),
        "request": _identity(request),
        "netcdf": paths.netcdf.name,
        "size": size,
        "checksum": checksum,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": __version__,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    """Write a manifest atomically."""
    # sort_keys + indent make manifests diff-friendly and deterministic.
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return atomic_write_text(path, text)


def read_manifest(path: Path) -> dict[str, Any]:
    """Read a manifest from disk."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CacheError(f"No se encontró el manifiesto: {path}") from exc
    except OSError as exc:
        raise CacheError(
            f"No se pudo leer el manifiesto: {path}",
            hint=str(exc),
        ) from exc

    # Invalid JSON means the sidecar cannot be trusted.
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CacheError(
            f"El manifiesto no es JSON válido: {path}",
            hint=str(exc),
        ) from exc

    # The manifest must be a JSON object, not a list or scalar.
    if not isinstance(data, dict):
        raise CacheError(f"El manifiesto debe contener un objeto JSON: {path}")
    return data
