"""Thin CDS API client wrapper. All network access lives here.

Keeping every network call in this module has two benefits:

* the rest of ``download/`` can be tested offline with a fake client;
* retries, credential handling and error normalization live in one place.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping

# cdsapi is an optional runtime dependency from the point of view of tests and
# of commands that only read the cache.  Importing the module must not fail when
# the package is missing.
try:
    import cdsapi
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    cdsapi = None  # type: ignore[assignment]

from prometeo.shared.errors import ConfigurationError, DownloadError
from prometeo.shared.io import ensure_directory

# Default CDS endpoint used when neither the environment nor ~/.cdsapirc sets one.
DEFAULT_CDS_URL = "https://cds.climate.copernicus.eu/api"
_DEFAULT_URL = DEFAULT_CDS_URL


def write_credentials(url: str, key: str, *, path: Path | None = None) -> Path:
    """Validate and write CDS credentials to ``~/.cdsapirc``.

    The file is written atomically with mode ``0600`` so the API key is never
    world-readable, even briefly.  ``path`` is primarily a test seam; normal
    callers should leave it as ``None``.
    """
    target = (
        Path(path).expanduser()
        if path is not None
        else Path.home() / ".cdsapirc"
    )
    normalized_url = str(url).strip()
    normalized_key = str(key).strip()

    if not normalized_url:
        raise ConfigurationError(
            "La URL de CDS no puede estar vacía.",
            hint=f"Use la URL por defecto: {DEFAULT_CDS_URL}",
        )
    if not normalized_url.startswith(("http://", "https://")):
        raise ConfigurationError(
            "La URL de CDS debe empezar por http:// o https://.",
            hint=f"Ejemplo: {DEFAULT_CDS_URL}",
        )
    if not normalized_key:
        raise ConfigurationError("La API key de CDS no puede estar vacía.")
    if ":" not in normalized_key:
        raise ConfigurationError(
            "La API key de CDS debe tener el formato UID:CLAVE.",
            hint=(
                "Copie la clave desde la página de CDS: "
                "https://cds.climate.copernicus.eu/how-to-api"
            ),
        )

    # Write a sibling temporary file first so an interrupted write cannot leave
    # a truncated credential file.  The temporary file is created with 0600.
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    payload = f"url: {normalized_url}\nkey: {normalized_key}\n"

    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(temporary, target)
        # os.replace preserves the temporary file's 0600 mode on POSIX, but be
        # explicit for filesystems that do not.
        os.chmod(target, 0o600)
    finally:
        # Never leave a temporary credential file behind.
        temporary.unlink(missing_ok=True)

    return target


def _parse_cdsapirc(path: Path) -> tuple[str | None, str | None]:
    """Read ``url:`` and ``key:`` from a .cdsapirc file."""
    url: str | None = None
    key: str | None = None

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        # A missing or unreadable file simply means "no file credentials".
        return None, None

    for line in lines:
        stripped = line.strip()

        # Skip blank lines and comments.
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue

        # Split only on the first colon; the key itself contains a colon.
        name, value = stripped.split(":", 1)
        name = name.strip().lower()
        value = value.strip()
        if name == "url":
            url = value
        elif name == "key":
            key = value
    return url, key


def load_credentials() -> tuple[str | None, str | None]:
    """Read CDS credentials from the environment or ``~/.cdsapirc``."""
    # Environment variables take precedence over the config file.
    url = os.environ.get("CDSAPI_URL")
    key = os.environ.get("CDSAPI_KEY")

    file_url, file_key = _parse_cdsapirc(Path.home() / ".cdsapirc")
    return url or file_url, key or file_key


def _resolve_progress(progress: bool | None) -> bool:
    """Resolve whether CDS progress output should be enabled.

    Precedence: explicit argument, ``PROMETEO_PROGRESS``, terminal detection.
    """
    if progress is not None:
        return bool(progress)

    value = os.environ.get("PROMETEO_PROGRESS")
    if value is not None:
        text = value.strip().lower()
        if text in {"0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True

    try:
        return bool(sys.stderr.isatty())
    except Exception:
        return False


class CDSClient:
    """Small wrapper around ``cdsapi.Client`` with a stable test seam."""

    def __init__(
        self,
        *,
        url: str | None = None,
        key: str | None = None,
        timeout: int = 120,
        max_retries: int = 3,
        progress: bool | None = None,
    ) -> None:
        """Check that cdsapi is installed and build the client."""
        # Fail with an actionable message instead of an ImportError traceback.
        if cdsapi is None:
            raise ConfigurationError(
                "La dependencia 'cdsapi' no está instalada.",
                hint="Instale las dependencias del proyecto: pip install cdsapi.",
            )

        # Explicit arguments win; otherwise use env/file credentials.
        env_url, env_key = load_credentials()
        resolved_url = url or env_url
        resolved_key = key or env_key

        # CDS also accepts ~/.cdsapirc directly, so allow that case.
        if not resolved_key and not (Path.home() / ".cdsapirc").is_file():
            raise ConfigurationError(
                "No se encontraron credenciales de CDS.",
                hint=(
                    "Defina CDSAPI_KEY/CDSAPI_URL o cree ~/.cdsapirc con las "
                    "claves url y key."
                ),
            )

        self.url = resolved_url or _DEFAULT_URL
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.progress = _resolve_progress(progress)
        # The callback sets this event when CDS starts downloading the result,
        # which stops the "still waiting" ticker.
        self._download_started = threading.Event()
        self._progress_lock = threading.Lock()
        self._wait_line_active = False
        self._wait_line_width = 0
        try:
            self._interactive = bool(sys.stderr.isatty())
        except Exception:
            self._interactive = False

        # Bound cdsapi's own retry loop.  The service performs its own retries;
        # cdsapi's default is 500 attempts with 120-second sleeps, which is far
        # too long for an interactive CLI.
        options: dict[str, object] = {
            "quiet": not self.progress,
            "progress": self.progress,
            "timeout": timeout,
            "retry_max": 1,
            "sleep_max": 8,
        }
        if self.progress:
            # cdsapi only enables its tqdm bar when quiet=False; these
            # callbacks replace its logger output with concise status lines.
            options.update(
                {
                    "info_callback": self._on_cds_info,
                    "warning_callback": self._on_cds_warning,
                    "error_callback": self._on_cds_error,
                    "debug_callback": self._on_cds_debug,
                }
            )
        if resolved_url:
            options["url"] = resolved_url
        if resolved_key:
            options["key"] = resolved_key

        try:
            # Note: constructing cdsapi.Client may contact the catalogue
            # endpoint.  DownloadService creates it lazily, only on cache misses.
            self._client = cdsapi.Client(**options)  # type: ignore[union-attr]
        except Exception as exc:
            raise ConfigurationError(
                "No se pudo inicializar el cliente de CDS.",
                hint=str(exc),
            ) from exc

    @staticmethod
    def _format_cds_message(message: object, args: tuple[object, ...]) -> str:
        """Format a cdsapi log message with its %-style arguments."""
        text = str(message)
        if args:
            try:
                text = text % args
            except Exception:
                text = " ".join(str(part) for part in (message, *args))
        return text

    def _on_cds_info(self, message: object, *args: object) -> None:
        """Print concise CDS status lines and detect the download start."""
        text = self._format_cds_message(message, args)
        lowered = text.lower()
        if "request is complete" in lowered or "downloading" in lowered:
            self._download_started.set()
        self._write_status(f"CDS: {text}")

    def _on_cds_warning(self, message: object, *args: object) -> None:
        text = self._format_cds_message(message, args)
        self._write_status(f"CDS aviso: {text}")

    def _on_cds_error(self, message: object, *args: object) -> None:
        text = self._format_cds_message(message, args)
        self._write_status(f"CDS error: {text}")

    def _on_cds_debug(self, message: object, *args: object) -> None:
        # Debug output would interleave with the progress bar; keep it quiet.
        return

    def _clear_wait_locked(self) -> None:
        """Clear the in-place wait line. Caller must hold the lock."""
        if self._wait_line_active and self._interactive:
            sys.stderr.write("\r" + " " * self._wait_line_width + "\r")
            sys.stderr.flush()
        self._wait_line_active = False

    def _clear_wait(self) -> None:
        """Clear the in-place wait line, if any."""
        with self._progress_lock:
            self._clear_wait_locked()

    def _write_status(self, text: str) -> None:
        """Write one status line, replacing the wait line if it is active."""
        with self._progress_lock:
            self._clear_wait_locked()
            print(text, file=sys.stderr, flush=True)

    def _write_wait(self, elapsed: int) -> None:
        """Update the wait line in place."""
        text = f"CDS: esperando respuesta de CDS... {elapsed} s"
        with self._progress_lock:
            if not self._interactive or self._download_started.is_set():
                return
            padding = " " * max(0, self._wait_line_width - len(text))
            sys.stderr.write("\r" + text + padding)
            sys.stderr.flush()
            self._wait_line_width = len(text)
            self._wait_line_active = True

    def _wait_ticker(
        self,
        started: threading.Event,
        stop: threading.Event,
        interval: float = 5.0,
    ) -> None:
        """Print elapsed queue time until CDS starts downloading."""
        # Without a terminal, carriage returns are noise; leave one message.
        if not self._interactive:
            self._write_status("CDS: esperando respuesta de CDS...")
            return

        start = time.monotonic()
        while not stop.is_set():
            if started.wait(interval):
                return
            if started.is_set() or stop.is_set():
                return
            self._write_wait(int(time.monotonic() - start))

    def retrieve(
        self, dataset: str, payload: Mapping[str, object], target: Path
    ) -> Path:
        """Retrieve one CDS request to ``target``."""
        destination = Path(target)
        ensure_directory(destination.parent)

        operation_start = time.monotonic()
        last_error: Exception | None = None

        # Retry transient failures with a small exponential backoff.
        for attempt in range(self.max_retries):
            # Remove any stale partial result before retrying.
            destination.unlink(missing_ok=True)

            # A fresh event per attempt so the wait ticker follows this request.
            started = threading.Event()
            stop = threading.Event()
            self._download_started = started
            ticker: threading.Thread | None = None
            if self.progress:
                ticker = threading.Thread(
                    target=self._wait_ticker,
                    args=(started, stop),
                    daemon=True,
                )
                ticker.start()

            try:
                # cdsapi writes the result directly to the target path.
                self._client.retrieve(dataset, dict(payload), str(destination))

                # A successful call that produced no file is still a failure.
                if not destination.is_file() or destination.stat().st_size == 0:
                    raise DownloadError(
                        "CDS no generó un archivo de salida válido.",
                        hint="Revise la petición o vuelva a intentarlo.",
                    )
                if self.progress:
                    elapsed = time.monotonic() - operation_start
                    self._write_status(f"CDS: listo en {elapsed:.1f} s")
                return destination
            except DownloadError:
                # Our own validation errors are not transient; do not retry.
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    # 1 s, 2 s, 4 s, capped at 8 s.
                    time.sleep(min(2**attempt, 8))
            finally:
                stop.set()
                started.set()
                if ticker is not None:
                    ticker.join(timeout=1.0)
                self._clear_wait()

        raise DownloadError(
            f"No se pudo descargar el dataset '{dataset}' desde CDS.",
            hint=str(last_error) if last_error is not None else None,
        ) from last_error

    def check_constraints(
        self, dataset: str, inputs: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Query CDS constraints for a request.

        Used by the future ``catalog check`` command to validate centres,
        systems, variables, products, years and leads without downloading data.
        """
        url = (
            f"{self.url.rstrip('/')}/retrieve/v1/processes/{dataset}/constraints"
        )

        # Build a standard JSON POST request with the typed inputs.
        request = urllib.request.Request(
            url,
            data=json.dumps({"inputs": dict(inputs)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            raise DownloadError(
                f"No se pudieron consultar las restricciones de CDS para '{dataset}'.",
                hint=str(exc),
            ) from exc

        # The endpoint must return a JSON object.
        if not isinstance(data, Mapping):
            raise DownloadError(
                "La respuesta de restricciones de CDS no es un objeto JSON."
            )
        return data
