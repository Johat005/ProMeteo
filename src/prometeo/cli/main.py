"""Argument parsing and command dispatch for ``python -m prometeo``.

The CLI is the orchestration layer (M7).  It must not contain scientific logic
or data I/O: it loads configuration, builds a request and delegates to the
service modules.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path
from typing import Any

from prometeo import __version__
from prometeo.canonical.build import build_canonical, combine_canonical
from prometeo.canonical.dataset import CanonicalDataset
from prometeo.cli.common import (
    error,
    format_model,
    load_repository,
    print_json,
    print_report,
    resolve_model,
    split_csv,
    split_int_csv,
)
from prometeo.cpt.writer import write_cpt
from prometeo.diagnostics.engine import DiagnosticEngine, validate_file
from prometeo.diagnostics.findings import DiagnosticReport
from prometeo.diagnostics.findings import error as finding_error
from prometeo.diagnostics.findings import warning as finding_warning
from prometeo.download.client import (
    CDSClient,
    DEFAULT_CDS_URL,
    write_credentials,
)
from prometeo.download.request import (
    GLOBAL_AREA,
    area_name,
    parse_area,
    parse_leads,
)
from prometeo.download.service import DownloadPlan, DownloadResult, DownloadService
from prometeo.reader.reader import ReadRequest, read_netcdf
from prometeo.shared.config import (
    AppConfig,
    load_config,
    merge_cli_overrides,
    validate_config,
)
from prometeo.shared.errors import (
    ConfigurationError,
    ProMeteoError,
    ReaderError,
    exit_code_for,
    format_error,
)
from prometeo.shared.io import ensure_directory, safe_filename_component
from prometeo.shared.logging import configure_logging
from prometeo.shared.timeutils import current_reference_month


def add_common_options(parser: argparse.ArgumentParser) -> None:
    """Add options accepted by every command."""
    # Configuration sources.  Missing values come from the user config file.
    parser.add_argument("--config", type=Path, help="Archivo YAML de configuración.")
    parser.add_argument("--catalog", type=Path, help="Ruta alternativa del catálogo.")
    parser.add_argument("--output-dir", type=Path, help="Directorio de salida.")

    # Logging and output preferences.
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Nivel de registro (por defecto INFO).",
    )
    parser.add_argument("--log-file", type=Path, help="Archivo de registro.")
    parser.add_argument("--json", action="store_true", help="Salida JSON.")

    # Global switch used by download (redownload) and later by diagnostics.
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora la caché válida y los diagnósticos bloqueantes.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser and register every subcommand."""
    parser = argparse.ArgumentParser(
        prog="prometeo",
        description="ProMeteo: conversor de NetCDF C3S a CPT+ v10+.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    # Every subcommand gets the same common options through a parent parser.
    common = argparse.ArgumentParser(add_help=False)
    add_common_options(common)

    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="comando"
    )

    # --- download ---------------------------------------------------------
    download = subparsers.add_parser(
        "download",
        parents=[common],
        help="Descarga el hindcast y el forecast desde CDS.",
        description=(
            "Asegura en la caché el hindcast (1981-2016, todos los meses, "
            "monthly_mean) y el forecast (mes actual, ensemble_mean), "
            "con los plazos 1-6."
        ),
    )
    download.add_argument(
        "--model",
        default="ecmwf-seas5",
        help="Identificador del modelo en el catálogo (por defecto ecmwf-seas5).",
    )
    download.add_argument(
        "--region",
        default=None,
        help="Área: 'global' (por defecto), 'colombia' o N,W,S,E.",
    )
    download.add_argument(
        "--dest",
        "--destination",
        "--cache-dir",
        dest="dest",
        type=Path,
        default=None,
        help=(
            "Directorio de destino de la descarga. Por defecto, el directorio "
            "actual donde se ejecuta el comando."
        ),
    )
    download.add_argument(
        "--leads",
        default="1-6",
        help="Plazos por defecto para ambos productos, por ejemplo 1-6 o 1,3,5.",
    )
    download.add_argument(
        "--hindcast-leads",
        default=None,
        help="Plazos solo para el hindcast; por defecto usa --leads.",
    )
    download.add_argument(
        "--forecast-leads",
        default=None,
        help="Plazos solo para el forecast; por defecto usa --leads.",
    )
    download.add_argument(
        "--variables",
        default=None,
        help=(
            "Subconjunto de variables separadas por coma (por defecto las cinco). "
            "Ejemplo: t2m o t2m,tprate."
        ),
    )
    download.add_argument(
        "--only",
        choices=["both", "hindcast", "forecast"],
        default="both",
        help="Limita la descarga a uno de los dos productos (por defecto ambos).",
    )
    download.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout de red por petición CDS, en segundos (por defecto 120).",
    )
    download.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Reintentos ante fallos transitorios (por defecto 3).",
    )
    download.add_argument(
        "--separate",
        action="store_true",
        help="Escribe un archivo por variable y producto en lugar de dos archivos.",
    )
    download.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra la petición CDS y el destino sin acceder a la red.",
    )
    download.set_defaults(func=cmd_download)

    # --- catalog inspection ----------------------------------------------
    list_models = subparsers.add_parser(
        "list-models",
        parents=[common],
        help="Lista los modelos del catálogo.",
    )
    list_models.set_defaults(func=cmd_list_models)

    describe_model = subparsers.add_parser(
        "describe-model",
        parents=[common],
        help="Describe un modelo del catálogo.",
    )
    describe_model.add_argument("model_id", help="Identificador del modelo.")
    describe_model.set_defaults(func=cmd_describe_model)

    # --- configure --------------------------------------------------------
    configure = subparsers.add_parser(
        "configure",
        help="Configura las credenciales de CDS en ~/.cdsapirc.",
        description=(
            "Solicita la URL y la API key de CDS y escribe ~/.cdsapirc con "
            "permisos 600. No descarga datos."
        ),
    )
    configure.set_defaults(func=cmd_configure)

    # --- inspect ----------------------------------------------------------
    inspect = subparsers.add_parser(
        "inspect",
        parents=[common],
        help="Inspecciona uno o más archivos NetCDF.",
        description=(
            "Muestra dimensiones, coordenadas, variables, unidades, miembros, "
            "plazos y rango de inicialización. No descarga archivos."
        ),
    )
    inspect.add_argument("files", nargs="+", help="Archivos NetCDF de entrada.")
    inspect.add_argument(
        "--variables",
        default=None,
        help="Variables separadas por coma (por defecto todas las del archivo).",
    )
    inspect.add_argument(
        "--leads",
        default=None,
        help="Plazos mensuales, por ejemplo 1-6 o 1,3,5.",
    )
    inspect.add_argument(
        "--init-start",
        default=None,
        help="Primer mes de inicialización, formato YYYY-MM.",
    )
    inspect.add_argument(
        "--init-end",
        default=None,
        help="Último mes de inicialización, formato YYYY-MM.",
    )
    inspect.add_argument(
        "--region",
        default=None,
        help="Área: 'global', 'colombia' o N,W,S,E.",
    )
    inspect.add_argument(
        "--members",
        default=None,
        help="Miembros separados por coma, por ejemplo 0,1,2.",
    )
    inspect.set_defaults(func=cmd_inspect)

    # --- validate ---------------------------------------------------------
    validate = subparsers.add_parser(
        "validate",
        parents=[common],
        help="Valida uno o más archivos NetCDF.",
        description=(
            "Ejecuta los chequeos de estructura, unidades, plazos, rejilla y "
            "calidad de datos. No descarga archivos."
        ),
    )
    validate.add_argument("files", nargs="+", help="Archivos NetCDF de entrada.")
    validate.add_argument(
        "--model",
        default="ecmwf-seas5",
        help="Modelo del catálogo (por defecto ecmwf-seas5).",
    )
    validate.add_argument(
        "--variables",
        default=None,
        help="Variables separadas por coma (por defecto las presentes en el archivo).",
    )
    validate.add_argument(
        "--leads",
        default=None,
        help="Plazos mensuales, por ejemplo 1-6 o 1,3,5.",
    )
    validate.add_argument(
        "--init-start",
        default=None,
        help="Primer mes de inicialización, formato YYYY-MM.",
    )
    validate.add_argument(
        "--init-end",
        default=None,
        help="Último mes de inicialización, formato YYYY-MM.",
    )
    validate.add_argument(
        "--region",
        default=None,
        help="Área: 'global', 'colombia' o N,W,S,E.",
    )
    validate.add_argument(
        "--members",
        default=None,
        help="Miembros separados por coma, por ejemplo 0,1,2.",
    )
    validate.set_defaults(func=cmd_validate)

    # --- convert ----------------------------------------------------------
    convert = subparsers.add_parser(
        "convert",
        parents=[common],
        help="Convierte hindcast/forecast a CPT+ v10+.",
        description=(
            "Construye el objeto canónico X/Z, ejecuta el diagnóstico y escribe "
            "uno o varios archivos CPT. Por defecto descarga los insumos si "
            "faltan y escribe dos archivos por variable (X y Z)."
        ),
    )
    convert.add_argument(
        "--model",
        default="ecmwf-seas5",
        help="Modelo del catálogo (por defecto ecmwf-seas5).",
    )
    convert.add_argument(
        "--hindcast-file",
        type=Path,
        default=None,
        help="NetCDF de hindcast; si se omite, se descarga.",
    )
    convert.add_argument(
        "--forecast-file",
        type=Path,
        default=None,
        help="NetCDF de forecast; si se omite, se descarga.",
    )
    convert.add_argument(
        "--dest",
        "--destination",
        "--cache-dir",
        dest="dest",
        type=Path,
        default=None,
        help="Directorio de caché/descarga; por defecto el directorio actual.",
    )
    convert.add_argument(
        "--region",
        default=None,
        help="Área: 'global' (por defecto), 'colombia' o N,W,S,E.",
    )
    convert.add_argument(
        "--variables",
        default=None,
        help="Variables separadas por coma (por defecto todas las presentes).",
    )
    convert.add_argument(
        "--leads",
        default="1-6",
        help="Plazos por defecto para ambos productos.",
    )
    convert.add_argument(
        "--hindcast-leads",
        default=None,
        help="Plazos solo para el hindcast; por defecto usa --leads.",
    )
    convert.add_argument(
        "--forecast-leads",
        default=None,
        help="Plazos solo para el forecast; por defecto usa --leads.",
    )
    convert.add_argument(
        "--hindcast-init-month",
        default="current",
        help="Mes de inicialización del hindcast: current, 1..12 o all.",
    )
    convert.add_argument(
        "--partition",
        choices=["both", "x", "z", "single"],
        default="both",
        help=(
            "both = archivos X y Z separados (por defecto); x = solo hindcast; "
            "z = solo forecast; single = un archivo combinado."
        ),
    )
    convert.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout de red por petición CDS, en segundos (por defecto 120).",
    )
    convert.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Reintentos ante fallos transitorios (por defecto 3).",
    )
    convert.set_defaults(func=cmd_convert)

    return parser


def _load_context(args: argparse.Namespace) -> tuple[AppConfig, Any]:
    """Load configuration, apply CLI overrides and configure logging."""
    # Start from the user config (or defaults if it is absent).
    config = load_config(args.config)

    # CLI values override the file only when explicitly provided.  ``None``
    # means "not supplied", so merge_cli_overrides ignores it.
    overrides = {
        "catalog_path": args.catalog,
        "output_dir": args.output_dir,
        "log_level": args.log_level,
        "log_file": args.log_file,
        "json_output": True if args.json else None,
        "force": True if args.force else None,
    }
    config = merge_cli_overrides(config, overrides)

    # Validate before touching the network or filesystem.
    validate_config(config)

    # Console logs go to stderr; --json affects both logs and result output.
    configure_logging(
        level=config.log_level,
        log_file=config.log_file,
        json_mode=config.json_output,
    )

    # Load the catalog once per command.
    repository = load_repository(config)
    return config, repository


def _print_model(model: Any) -> None:
    """Print one model in the human-readable format used by model commands."""
    print(f"Modelo: {model.id}")
    print(f"  centro/sistema: {model.centre} / {model.system}")
    print(f"  dataset: {model.dataset}")
    print(
        "  productos: "
        f"forecast={model.product_forecast}, hindcast={model.product_hindcast}"
    )
    print(f"  plazos: {', '.join(str(lead) for lead in model.leads)}")
    print(
        "  años: "
        f"hindcast={model.hindcast_years[0]}-{model.hindcast_years[1]}, "
        f"forecast={model.forecast_years[0]}-{model.forecast_years[1]}"
    )
    print(
        "  miembros: "
        f"hindcast={model.members_hindcast}, forecast={model.members_forecast}"
    )
    print(
        "  rejilla: "
        f"{model.grid.nlat}x{model.grid.nlon} "
        f"(lat={model.grid.lat_order}, lon={model.grid.lon_convention})"
    )
    print(f"  convención temporal: {model.time_convention}")
    if model.source:
        print(f"  fuente: {model.source}")

    print("  variables:")
    for key, spec in model.variables.items():
        print(
            f"    {key}: cds={spec.cds}, netcdf={spec.netcdf}, "
            f"cpt={spec.cpt_field}, {spec.units_in} -> {spec.units_out}"
        )


def cmd_list_models(args: argparse.Namespace) -> int:
    """Implement the ``list-models`` command."""
    config, repository = _load_context(args)
    models = repository.list_models()

    if config.json_output:
        print_json([format_model(model) for model in models])
        return 0

    if not models:
        print("El catálogo no contiene modelos.")
        return 0

    # Human-readable table: one compact block per model.
    for model in models:
        print(
            f"{model.id}: {model.centre}/{model.system} "
            f"({model.dataset}) — variables: {', '.join(sorted(model.variables))}"
        )
    return 0


def cmd_describe_model(args: argparse.Namespace) -> int:
    """Implement the ``describe-model`` command."""
    config, repository = _load_context(args)
    model = repository.get_model(args.model_id)

    if config.json_output:
        print_json(format_model(model))
        return 0

    _print_model(model)
    return 0


def cmd_configure(_args: argparse.Namespace) -> int:
    """Prompt for CDS credentials and write ``~/.cdsapirc`` safely."""
    # This command is intentionally independent of the catalog and of the
    # application config: a new user must be able to configure credentials
    # before anything else works.

    # Interactive input is required; avoid hanging when run from cron/systemd.
    if sys.stdin is None or not sys.stdin.isatty():
        raise ConfigurationError(
            "El comando 'configure' necesita una terminal interactiva.",
            hint=(
                "Ejecútelo desde una terminal, cree ~/.cdsapirc manualmente o "
                "defina CDSAPI_URL y CDSAPI_KEY."
            ),
        )

    target = Path.home() / ".cdsapirc"
    try:
        if target.exists():
            answer = input(
                f"Ya existe {target}. ¿Sobrescribir? [s/N]: "
            ).strip().lower()
            if answer not in {"s", "si", "sí", "y", "yes"}:
                print("No se modificó nada.")
                return 0

        url = input(f"URL de CDS [{DEFAULT_CDS_URL}]: ").strip() or DEFAULT_CDS_URL
        key = getpass.getpass("API key de CDS (no se mostrará): ").strip()
    except EOFError as exc:
        raise ConfigurationError(
            "No se pudieron leer las credenciales de CDS.",
            hint="Ejecute el comando en una terminal interactiva.",
        ) from exc

    path = write_credentials(url, key, path=target)
    print(f"Credenciales guardadas en {path} (permisos 600).")
    print("Pruebe: python prometeo list-models")
    print("En Linux/macOS use: python3 prometeo list-models")
    return 0


def _print_inspection(summary: dict[str, object]) -> None:
    """Print an inspection summary in a compact human-readable form."""
    files = summary.get("source_files") or []
    print("Archivos:")
    for path in files:
        print(f"  {path}")

    dimensions = summary.get("dimensions") or {}
    print("Dimensiones:")
    for name, size in sorted(dimensions.items()):
        print(f"  {name}: {size}")

    print("Coordenadas:")
    for name in summary.get("coordinates") or []:
        print(f"  {name}")

    variables = summary.get("variables") or {}
    print("Variables:")
    for name, info in sorted(variables.items()):
        if not isinstance(info, dict):
            continue
        units = info.get("units") or ""
        print(
            f"  {name}: dims={info.get('dims')} shape={info.get('shape')}"
            + (f" [{units}]" if units else "")
        )

    members = summary.get("members")
    if isinstance(members, list):
        preview = members[:5]
        suffix = " ..." if len(members) > 5 else ""
        print(f"Miembros: {len(members)} ({preview}{suffix})")

    leads = summary.get("leads")
    if isinstance(leads, list):
        print(f"Plazos: {leads}")

    init = summary.get("init_time")
    if isinstance(init, dict):
        print(
            "Inicializaciones: "
            f"{init.get('count')} ({init.get('first')} .. {init.get('last')})"
        )

    latitude = summary.get("latitude")
    if isinstance(latitude, dict):
        print(
            "Latitud: "
            f"{latitude.get('count')} valores, {latitude.get('min')}..{latitude.get('max')}, "
            f"orden {latitude.get('order', 'n/a')}"
        )

    longitude = summary.get("longitude")
    if isinstance(longitude, dict):
        print(
            "Longitud: "
            f"{longitude.get('count')} valores, {longitude.get('min')}..{longitude.get('max')}, "
            f"orden {longitude.get('order', 'n/a')}, convención {longitude.get('convention')}"
        )

    layout = summary.get("layout")
    if isinstance(layout, dict):
        print("Layout:")
        for name, value in layout.items():
            print(f"  {name}: {value}")


def cmd_inspect(args: argparse.Namespace) -> int:
    """Implement the ``inspect`` command."""
    # inspect uses explicit files; it never downloads anything.
    config, _repository = _load_context(args)

    variables = split_csv(args.variables) or ()
    leads = parse_leads(args.leads) if args.leads else None
    area = parse_area(args.region) if args.region else None
    members = split_int_csv(args.members)

    request = ReadRequest(
        files=tuple(Path(path) for path in args.files),
        variables=tuple(variables),
        leads=leads,
        init_start=args.init_start,
        init_end=args.init_end,
        area=area,
        members=members,
    )

    raw = read_netcdf(request)
    try:
        summary = raw.summary()
    finally:
        # Always release NetCDF handles, even if summary generation fails.
        raw.close()

    if config.json_output:
        print_json(summary)
        return 0

    _print_inspection(summary)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Implement the ``validate`` command."""
    config, repository = _load_context(args)
    model = resolve_model(repository, args.model)

    variables = split_csv(args.variables) or ()
    leads = parse_leads(args.leads) if args.leads else None
    area = parse_area(args.region) if args.region else None
    members = split_int_csv(args.members)

    request = ReadRequest(
        files=tuple(Path(path) for path in args.files),
        variables=tuple(variables),
        leads=leads,
        init_start=args.init_start,
        init_end=args.init_end,
        area=area,
        members=members,
    )

    # A file that cannot even be read is still a validation failure (exit 2),
    # not an unexpected crash.
    try:
        raw = read_netcdf(request)
    except ReaderError as exc:
        report = DiagnosticReport(files=[str(path) for path in request.files])
        report.add(
            finding_error(
                "FILE-READ",
                "input",
                str(exc),
                exc.hint or "Revise la ruta y el formato del archivo.",
            )
        )
        print_report(report, as_json=config.json_output)
        return report.exit_code()

    try:
        report = validate_file(raw, model, request, force=config.force)
    finally:
        raw.close()

    print_report(report, as_json=config.json_output)
    # validate reports the validation result; --force only affects convert.
    return report.exit_code()


def cmd_download(args: argparse.Namespace) -> int:
    """Implement the ``download`` command."""
    config, repository = _load_context(args)

    # Reference month is always the current month in the configured timezone.
    reference_month = current_reference_month(config.timezone)

    # Global area is the default; --region may select a preset or custom bounds.
    area = parse_area(args.region) if args.region else GLOBAL_AREA
    shared_leads = parse_leads(args.leads)
    hindcast_leads = parse_leads(args.hindcast_leads) if args.hindcast_leads else None
    forecast_leads = parse_leads(args.forecast_leads) if args.forecast_leads else None

    # Optional resource-safety limits.  The defaults preserve the operational
    # behavior: both products and all five catalog variables.
    variables = split_csv(args.variables)
    targets = (
        ("hindcast", "forecast") if args.only == "both" else (args.only,)
    )

    # Download destination: --dest wins, then the configured cache_dir, then
    # the current working directory (the AppConfig default).
    destination = args.dest if args.dest is not None else config.cache_dir

    # The client factory is lazy: dry-run and cache hits never construct it.
    service = DownloadService(
        repository,
        destination,
        client_factory=lambda: CDSClient(
            timeout=args.timeout,
            max_retries=args.retries,
        ),
    )
    results = service.download_products(
        reference_month,
        model_id=args.model,
        variables=variables,
        separate=args.separate,
        area=area,
        leads=shared_leads,
        hindcast_leads=hindcast_leads,
        forecast_leads=forecast_leads,
        targets=targets,
        force=config.force,
        dry_run=args.dry_run,
    )

    if config.json_output:
        print_json(
            {
                "reference_month": str(reference_month),
                "destination": str(destination),
                "region": area_name(area),
                "leads": list(shared_leads),
                "hindcast_leads": list(hindcast_leads or shared_leads),
                "forecast_leads": list(forecast_leads or shared_leads),
                "variables": list(variables) if variables else "all",
                "only": args.only,
                "separate": args.separate,
                "dry_run": args.dry_run,
                "results": [result.to_dict() for result in results],
            }
        )
        return 0

    # Human-readable output.
    print(f"Mes de referencia: {reference_month}")
    print(f"Destino: {destination}")
    print(f"Área: {area_name(area)}")
    print(
        "Plazos: "
        f"hindcast={list(hindcast_leads or shared_leads)}; "
        f"forecast={list(forecast_leads or shared_leads)}"
    )
    print(f"Productos: {args.only}; variables: {', '.join(variables) if variables else 'todas'}")
    for result in results:
        if isinstance(result, DownloadPlan):
            # Dry-run: show what would be sent and where it would be stored.
            print(f"[dry-run] {result.message}")
            print(f"  destino: {result.target}")
            print(
                "  petición: "
                + json.dumps(result.payload, ensure_ascii=False, sort_keys=True)
            )
        elif isinstance(result, DownloadResult):
            status = "cache" if result.cached else "descargado"
            print(f"{status}: {result.path} ({result.bytes_written} bytes)")
    return 0


def _leads_label(canonical) -> str:
    """Return ``leads1-6`` or a joined list for a canonical dataset."""
    leads = sorted({int(value) for value in canonical.data["lead"].values})
    if leads == list(range(leads[0], leads[-1] + 1)):
        return f"leads{leads[0]}-{leads[-1]}"
    return "leads" + "-".join(str(lead) for lead in leads)


def _grid_label(canonical) -> str:
    """Return the canonical grid size as ``NxM``."""
    return (
        f"{int(canonical.data.sizes['latitude'])}x"
        f"{int(canonical.data.sizes['longitude'])}"
    )


def _canonical_area_label(canonical: Any) -> str:
    """Infer an N,W,S,E label from the actual canonical grid."""
    latitude = canonical.data["latitude"].values
    longitude = canonical.data["longitude"].values
    north = float(latitude.max())
    south = float(latitude.min())
    west = float(longitude.min())
    east = float(longitude.max())
    return area_name((north, west, south, east))


def _convert_output_name(
    model: Any,
    variable: str,
    partition: str,
    canonical: Any,
    reference_month: Any,
    area_label: str,
) -> str:
    """Build a descriptive, filesystem-safe CPT filename."""
    if partition == "x":
        period = f"{model.hindcast_years[0]}-{model.hindcast_years[1]}"
        partition_label = "x"
    elif partition == "z":
        period = str(reference_month)
        partition_label = "z"
    else:
        period = f"{model.hindcast_years[0]}-{model.hindcast_years[1]}_{reference_month}"
        partition_label = "xz"

    stem = (
        f"{model.id}_{variable}_{partition_label}_{period}_"
        f"{_leads_label(canonical)}_{area_label}_{_grid_label(canonical)}"
    )
    return safe_filename_component(stem) + ".cpt"


def _output_info(path: Path, variable: str, partition: str, canonical: Any) -> dict[str, object]:
    """Return the summary entry for one written CPT file."""
    return {
        "path": str(path),
        "variable": variable,
        "partition": partition,
        "blocks": int(canonical.data.sizes["target_period"]),
        "bytes": int(path.stat().st_size),
    }


def _convert_variables(
    requested: tuple[str, ...] | None,
    canonicals: dict[str, Any],
    needed: tuple[str, ...],
) -> tuple[str, ...]:
    """Choose the variables to write, validating requested names."""
    if requested:
        missing = [
            variable
            for variable in requested
            if any(variable not in canonicals[target].variables for target in needed)
        ]
        if missing:
            raise ProMeteoError(
                f"Variables solicitadas ausentes en el canónico: {missing}.",
                hint="Revise --variables o los archivos de entrada.",
            )
        return tuple(requested)

    if len(needed) == 1:
        return tuple(canonicals[needed[0]].variables)

    hindcast_variables = tuple(canonicals["hindcast"].variables)
    forecast_variables = set(canonicals["forecast"].variables)
    common = tuple(
        variable for variable in hindcast_variables if variable in forecast_variables
    )
    if not common:
        raise ProMeteoError(
            "No hay variables comunes entre hindcast y forecast.",
            hint="Revise los archivos de entrada o use --variables.",
        )
    return common


def _resolve_convert_inputs(
    args: argparse.Namespace,
    config: AppConfig,
    repository: Any,
    reference_month: Any,
    needed: tuple[str, ...],
    variables: tuple[str, ...] | None,
    shared_leads: tuple[int, ...],
    hindcast_leads: tuple[int, ...],
    forecast_leads: tuple[int, ...],
    area: tuple[float, float, float, float],
) -> dict[str, Path]:
    """Return the NetCDF path for each needed product."""
    local = args.hindcast_file is not None or args.forecast_file is not None
    if local:
        files: dict[str, Path] = {}
        if "hindcast" in needed:
            if args.hindcast_file is None:
                raise ProMeteoError(
                    "Falta --hindcast-file.",
                    hint="Indique ambos archivos locales o ninguno para auto-descargar.",
                )
            files["hindcast"] = Path(args.hindcast_file)
        if "forecast" in needed:
            if args.forecast_file is None:
                raise ProMeteoError(
                    "Falta --forecast-file.",
                    hint="Indique ambos archivos locales o ninguno para auto-descargar.",
                )
            files["forecast"] = Path(args.forecast_file)
        return files

    destination = args.dest if args.dest is not None else config.cache_dir
    service = DownloadService(
        repository,
        destination,
        client_factory=lambda: CDSClient(
            timeout=args.timeout,
            max_retries=args.retries,
        ),
    )
    results = service.download_products(
        reference_month,
        model_id=args.model,
        variables=variables,
        separate=False,
        area=area,
        leads=shared_leads,
        hindcast_leads=hindcast_leads,
        forecast_leads=forecast_leads,
        targets=needed,
        force=False,
        dry_run=False,
    )
    if len(results) != len(needed):
        raise ProMeteoError(
            "No se resolvieron todos los productos necesarios.",
            hint="Revise la descarga y el caché.",
        )

    files = {}
    for target, result in zip(needed, results):
        if not isinstance(result, DownloadResult):
            raise ProMeteoError(
                f"No se pudo descargar el producto {target}.",
                hint="Revise la conexión y las credenciales de CDS.",
            )
        files[target] = result.path
    return files


def cmd_convert(args: argparse.Namespace) -> int:
    """Implement the ``convert`` command."""
    config, repository = _load_context(args)
    model = resolve_model(repository, args.model)
    reference_month = current_reference_month(config.timezone)
    area = parse_area(args.region) if args.region else GLOBAL_AREA

    shared_leads = parse_leads(args.leads)
    hindcast_leads = (
        parse_leads(args.hindcast_leads) if args.hindcast_leads else shared_leads
    )
    forecast_leads = (
        parse_leads(args.forecast_leads) if args.forecast_leads else shared_leads
    )
    variables = split_csv(args.variables)

    needed: list[str] = []
    if args.partition in {"both", "single", "x"}:
        needed.append("hindcast")
    if args.partition in {"both", "single", "z"}:
        needed.append("forecast")
    needed_tuple = tuple(needed)

    start = time.perf_counter()
    files = _resolve_convert_inputs(
        args,
        config,
        repository,
        reference_month,
        needed_tuple,
        variables,
        shared_leads,
        hindcast_leads,
        forecast_leads,
        area,
    )

    raws: dict[str, Any] = {}
    requests: dict[str, ReadRequest] = {}
    canonicals: dict[str, Any] = {}
    engine = DiagnosticEngine(force=config.force)
    report = DiagnosticReport()

    try:
        # Read and build one canonical object per needed product.
        for target in needed_tuple:
            if target not in files:
                raise ProMeteoError(f"Falta el archivo de {target}.")
            request = ReadRequest(
                files=(files[target],),
                variables=tuple(variables) if variables else (),
                leads=hindcast_leads if target == "hindcast" else forecast_leads,
                area=area,
            )
            requests[target] = request
            raw = read_netcdf(request)
            raws[target] = raw
            canonical = build_canonical(
                raw,
                model,
                request,
                target=target,
                reference_month=reference_month,
                hindcast_init_month=args.hindcast_init_month,
            )
            canonicals[target] = canonical

            file_report = engine.validate_file(raw, model, request)
            report.extend(file_report.findings)
            report.files.extend(file_report.files)

            canonical_report = engine.validate_canonical(canonical)
            report.extend(canonical_report.findings)

        # Warn when X and Z do not share the same lead window.
        if (
            "hindcast" in needed_tuple
            and "forecast" in needed_tuple
            and set(hindcast_leads) != set(forecast_leads)
        ):
            report.add(
                finding_warning(
                    "LEADS-MISMATCH",
                    "convert",
                    (
                        f"Plazos hindcast {list(hindcast_leads)} distintos de "
                        f"forecast {list(forecast_leads)}."
                    ),
                    "Confirme que la calibración X/Z es comparable.",
                )
            )

        # Warn when the variable sets differ between products.
        if "hindcast" in needed_tuple and "forecast" in needed_tuple:
            hindcast_vars = set(canonicals["hindcast"].variables)
            forecast_vars = set(canonicals["forecast"].variables)
            if hindcast_vars != forecast_vars:
                report.add(
                    finding_warning(
                        "VARIABLES-MISMATCH",
                        "convert",
                        (
                            "El hindcast y el forecast no tienen las mismas "
                            f"variables: X={sorted(hindcast_vars)}, "
                            f"Z={sorted(forecast_vars)}."
                        ),
                        "Se escribirán solo las variables comunes.",
                    )
                )

        report.metadata = {
            "kind": "convert",
            "model": model.id,
            "reference_month": str(reference_month),
            "partition": args.partition,
        }

        try:
            variables_to_write = _convert_variables(
                variables, canonicals, needed_tuple
            )
        except ProMeteoError as exc:
            report.add(
                finding_error(
                    "CONVERT-VARS",
                    "convert",
                    str(exc),
                    exc.hint or "Revise --variables y los archivos de entrada.",
                )
            )
            print_report(report, as_json=config.json_output)
            return report.exit_code()

        # Diagnostic gate.
        if not engine.can_convert(report):
            print_report(report, as_json=config.json_output)
            return report.exit_code()

        forced = bool(report.has_errors())
        forced_errors = len(report.errors())
        output_dir = ensure_directory(config.output_dir)
        outputs: list[dict[str, object]] = []

        for variable in variables_to_write:
            if args.partition in {"both", "x"}:
                x_canonical = canonicals["hindcast"]
                area_label = (
                    area_name(area)
                    if args.region
                    else _canonical_area_label(x_canonical)
                )
                path = output_dir / _convert_output_name(
                    model, variable, "x", x_canonical, reference_month, area_label
                )
                write_cpt(x_canonical, path, variable)
                outputs.append(_output_info(path, variable, "x", x_canonical))

            if args.partition in {"both", "z"}:
                z_canonical = canonicals["forecast"]
                area_label = (
                    area_name(area)
                    if args.region
                    else _canonical_area_label(z_canonical)
                )
                path = output_dir / _convert_output_name(
                    model, variable, "z", z_canonical, reference_month, area_label
                )
                write_cpt(z_canonical, path, variable)
                outputs.append(_output_info(path, variable, "z", z_canonical))

            if args.partition == "single":
                # Select only the variables being written so a local input with
                # extra variables can still be combined safely.
                x_subset = CanonicalDataset(
                    canonicals["hindcast"].data[list(variables_to_write)]
                )
                z_subset = CanonicalDataset(
                    canonicals["forecast"].data[list(variables_to_write)]
                )
                combined = combine_canonical(x_subset, z_subset)
                area_label = (
                    area_name(area)
                    if args.region
                    else _canonical_area_label(combined)
                )
                path = output_dir / _convert_output_name(
                    model, variable, "single", combined, reference_month, area_label
                )
                write_cpt(combined, path, variable)
                outputs.append(_output_info(path, variable, "xz", combined))

    finally:
        # Canonical arrays may still be lazy over these files.
        for raw in raws.values():
            raw.close()

    elapsed = time.perf_counter() - start

    if config.json_output:
        print_json(
            {
                "model": model.id,
                "reference_month": str(reference_month),
                "partition": args.partition,
                "hindcast_leads": list(hindcast_leads),
                "forecast_leads": list(forecast_leads),
                "variables": list(variables_to_write),
                "forced": forced,
                "forced_errors": forced_errors,
                "diagnostics": report.to_dict(),
                "outputs": outputs,
                "elapsed_seconds": round(elapsed, 2),
            }
        )
        return 0

    print(f"Modelo: {model.id}")
    print(f"Mes de referencia: {reference_month}")
    print(
        "Plazos: "
        f"hindcast={list(hindcast_leads)}; forecast={list(forecast_leads)}"
    )
    print(f"Partición: {args.partition}")
    if forced:
        print(
            f"Forzado: se ignoraron {forced_errors} error(es) de diagnóstico "
            "debido a --force."
        )
    print(
        "Diagnóstico: "
        f"{len(report.errors())} error(es), "
        f"{len(report.warnings())} advertencia(s)."
    )
    for output in outputs:
        print(
            f"CPT: {output['path']} "
            f"({output['blocks']} bloques, {output['bytes']} bytes)"
        )
    print(f"Duración: {elapsed:.1f} s")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run one command and map errors to exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        # Every handler returns a process exit code.
        return int(args.func(args))
    except ProMeteoError as exc:
        # Expected, user-facing failures: no traceback, actionable message.
        error(format_error(exc))
        return exit_code_for(exc)
    except KeyboardInterrupt:
        error("Operación cancelada por el usuario.")
        return 1
    except Exception as exc:  # pragma: no cover - defensive catch-all.
        # Never expose a traceback to the user.  Unexpected errors still return
        # a non-zero exit code and a short message.
        error(f"Error inesperado: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
