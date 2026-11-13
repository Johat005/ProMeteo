"""Application configuration loading and CLI override merging.

The configuration object is intentionally small: it only contains values that
every layer may need (timezone, catalog path, working directories, logging and
output preferences).  Scientific rules belong in the catalog or in the
canonical layer, never here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from prometeo.shared.errors import ConfigurationError

# Accepted values for AppConfig.log_level.  Keeping this list here lets us fail
# before configuring the logging subsystem with an invalid level.
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Fields that contain filesystem paths and therefore need relative-path
# resolution when they come from a YAML file.
_PATH_FIELDS = {"catalog_path", "cache_dir", "output_dir", "log_file"}

# Boolean fields must be real booleans: a YAML string such as "false" would be
# truthy and silently produce the opposite behavior.
_BOOL_FIELDS = {"json_output", "force"}


def _repo_root() -> Path:
    """Return the repository root from ``src/prometeo/shared/config.py``.

    Resolution order:
    1. PROMETEO_ROOT, useful for packaged builds or tests;
    2. the first parent directory containing pyproject.toml or
       config/models.yaml;
    3. the source-tree fallback (parents[3]).
    """
    # An explicit environment override wins over any heuristic.
    override = os.environ.get("PROMETEO_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    # Walk upwards from this file until a repository marker is found.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() or (parent / "config" / "models.yaml").is_file():
            return parent

    # Editable/source installs: src/prometeo/shared/config.py -> parents[3].
    return here.parents[3] if len(here.parents) > 3 else here.parent


@dataclass
class AppConfig:
    """Runtime settings shared by all layers."""

    # Timezone used to decide "the current reference month".
    timezone: str = "America/Bogota"

    # Catalog, cache and output locations.  The default factories are evaluated
    # lazily so importing the module does not touch the filesystem.  The download
    # destination defaults to the current working directory (where the command is
    # executed) unless a config file or --dest overrides it.
    catalog_path: Path = field(
        default_factory=lambda: _repo_root() / "config" / "models.yaml"
    )
    cache_dir: Path = field(default_factory=Path.cwd)
    output_dir: Path = field(default_factory=Path.cwd)

    # Logging and reporting preferences.
    log_level: str = "INFO"
    log_file: Path | None = None
    json_output: bool = False

    # Set by --force: ignore blocking diagnostics and redownload when needed.
    force: bool = False


# All dataclass field names, used to reject unknown YAML keys and CLI overrides.
_APP_CONFIG_FIELDS = set(AppConfig.__dataclass_fields__)


def default_config_path() -> Path:
    """Return the optional user configuration file path.

    PROMETEO_CONFIG takes precedence, then the XDG config directory
    (``~/.config`` by default).  The file is optional: load_config() returns
    defaults when it is absent.
    """
    override = os.environ.get("PROMETEO_CONFIG")
    if override:
        return Path(override).expanduser()

    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "prometeo" / "config.yaml"


def _as_path(value: object, *, base: Path, field_name: str) -> Path:
    """Convert a YAML value to a Path, resolving relatives against ``base``."""
    if not isinstance(value, (str, os.PathLike)):
        raise ConfigurationError(
            f"El campo '{field_name}' debe ser una ruta.",
            hint="Use una cadena o una ruta del sistema de archivos.",
        )

    path = Path(value).expanduser()
    if not path.is_absolute():
        # Relative paths are interpreted from the config file's directory, not
        # from the process working directory.
        path = base / path
    return path


def _ensure_path(value: object, field_name: str) -> Path:
    """Validate a path-like value that must be present."""
    if value is None:
        raise ConfigurationError(
            f"El campo '{field_name}' no puede estar vacío.",
            hint="Defina una ruta válida en la configuración.",
        )
    if not isinstance(value, (str, os.PathLike)):
        raise ConfigurationError(
            f"El campo '{field_name}' debe ser una ruta.",
            hint="Use una cadena o una ruta del sistema de archivos.",
        )
    return Path(value).expanduser()


def load_config(path: Path | None = None) -> AppConfig:
    """Load a YAML config file, or return defaults when it is absent.

    A missing *default* config is normal and yields defaults.  A missing
    *explicit* path is a user mistake and raises an actionable error.
    Unknown keys are rejected to catch typos early.
    """
    # Decide which file to read: explicit argument or the user-level default.
    config_path = Path(path).expanduser() if path is not None else default_config_path()

    if not config_path.is_file():
        if path is not None:
            raise ConfigurationError(
                f"No se encontró el archivo de configuración: {config_path}",
                hint="Cree el archivo o ejecute sin --config para usar los valores por defecto.",
            )
        # No user config is perfectly valid; use built-in defaults.
        return AppConfig()

    try:
        # empty YAML files are treated as empty mappings.
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigurationError(
            f"No se pudo leer el archivo de configuración: {config_path}",
            hint=str(exc),
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"El archivo de configuración no es YAML válido: {config_path}",
            hint=str(exc),
        ) from exc

    if not isinstance(raw, Mapping):
        raise ConfigurationError(
            f"El archivo de configuración debe contener un mapa YAML: {config_path}",
        )

    # Reject misspelled fields rather than silently ignoring them.
    unknown = set(raw) - _APP_CONFIG_FIELDS
    if unknown:
        raise ConfigurationError(
            f"Claves de configuración desconocidas: {', '.join(sorted(unknown))}",
            hint=f"Claves válidas: {', '.join(sorted(_APP_CONFIG_FIELDS))}.",
        )

    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _PATH_FIELDS:
            # log_file is optional, but catalog/cache/output paths are not.
            if value is None and key == "log_file":
                values[key] = None
            else:
                values[key] = _as_path(value, base=config_path.parent, field_name=key)
        else:
            # Scalar fields are used as-is; validate_config() checks them.
            values[key] = value

    # Build the config, then validate it before returning.
    config = replace(AppConfig(), **values)
    validate_config(config)
    return config


def merge_cli_overrides(
    config: AppConfig, overrides: Mapping[str, Any]
) -> AppConfig:
    """Apply explicit CLI values, ignoring ``None`` entries.

    CLI parsing produces ``None`` for flags that were not supplied; those must
    not overwrite values coming from the configuration file.
    """
    # Reject unknown option names, mirroring load_config().
    unknown = set(overrides) - _APP_CONFIG_FIELDS
    if unknown:
        raise ConfigurationError(
            f"Opciones desconocidas: {', '.join(sorted(unknown))}",
            hint=f"Opciones válidas: {', '.join(sorted(_APP_CONFIG_FIELDS))}.",
        )

    # Keep only values the user explicitly provided.
    values = {key: value for key, value in overrides.items() if value is not None}
    return replace(config, **values)


def validate_config(config: AppConfig) -> None:
    """Validate timezone, log level and required paths.

    This function never creates directories; it only checks that the paths are
    usable.  Creation happens later, when a layer actually writes data.
    """
    # Scalar types are validated before they reach ZoneInfo or logging.
    if not isinstance(config.timezone, str):
        raise ConfigurationError("El campo 'timezone' debe ser una cadena.")
    if not isinstance(config.log_level, str):
        raise ConfigurationError("El campo 'log_level' debe ser una cadena.")
    for name in sorted(_BOOL_FIELDS):
        if not isinstance(getattr(config, name), bool):
            raise ConfigurationError(
                f"El campo '{name}' debe ser booleano (true/false).",
            )

    # The timezone must be resolvable by zoneinfo.
    try:
        ZoneInfo(config.timezone)
    except Exception as exc:  # zoneinfo raises several exception types.
        raise ConfigurationError(
            f"Zona horaria inválida: {config.timezone}",
            hint="Use un identificador IANA, por ejemplo 'America/Bogota'.",
        ) from exc

    # The log level must be one of the names accepted by the logging module.
    level = config.log_level.upper()
    if level not in _LOG_LEVELS:
        raise ConfigurationError(
            f"Nivel de registro inválido: {config.log_level}",
            hint=f"Niveles válidos: {', '.join(sorted(_LOG_LEVELS))}.",
        )

    # The catalog must exist; it is the single source of model metadata.
    catalog_path = _ensure_path(config.catalog_path, "catalog_path")
    if not catalog_path.is_file():
        raise ConfigurationError(
            f"No se encontró el catálogo de modelos: {catalog_path}",
            hint="Verifique la ruta en la configuración o cree config/models.yaml.",
        )

    # Cache and output directories must be directories (or creatable via their
    # parent).  We check the nearest existing path for writability.
    for name in ("cache_dir", "output_dir"):
        path = _ensure_path(getattr(config, name), name)
        if path.exists() and not path.is_dir():
            raise ConfigurationError(f"'{name}' debe ser un directorio: {path}")
        parent = path if path.exists() else path.parent
        if not os.access(parent, os.W_OK):
            raise ConfigurationError(
                f"El directorio no es escribible: {parent}",
                hint=f"Revise los permisos de '{name}'.",
            )

    # A log file needs a writable parent directory.
    if config.log_file is not None:
        log_parent = _ensure_path(config.log_file, "log_file").parent
        if not os.access(log_parent, os.W_OK):
            raise ConfigurationError(
                f"El directorio del registro no es escribible: {log_parent}",
                hint="Revise la ruta de --log-file.",
            )
