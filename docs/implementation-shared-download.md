# Implementation notes — ProMeteo Sprint 1

**Scope:** `shared/`, `catalog/`, `download/`, `reader/`, `canonical/`, `cpt/`,
`diagnostics/` and the CLI.
**Status:** implemented and covered by offline tests. The live CDS path has been
exercised with real ECMWF forecast/hindcast requests, including a 6.79 GB global
`t2m` hindcast and its CPT conversion. Full five-variable global scale remains a
known limitation.
**Test command:** `python3 -m unittest discover -s tests -t . -v` → 187 tests passing.
On Windows use `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`
(see `docs/windows-guide.md`).

This document explains **what each file does** and **why it was built that way**.
It is intentionally more detailed than the public README.

---

## 1. Where these files sit

```text
cli ──► download ──► catalog ──► shared
                 └──────────────► shared
```

- `shared/` is the kernel: errors, configuration, logging, atomic I/O, time arithmetic.
  It contains no science, no NetCDF and no CDS-specific logic.
- `catalog/` is the single source of truth for models, variables, units, years,
  products and grid metadata. Adding a model must be a YAML change, not a code change.
- `download/` builds CDS requests, checks the cache, retrieves files and writes
  sidecar manifests. It does not read NetCDF (that is `reader/`'s job).

The implementation follows the project rule that a layer only imports downward.

---

## 2. File map

| File | Purpose |
| :-- | :-- |
| `prometeo` | Root launcher: `python3 prometeo ...` (Linux/macOS) or `py prometeo ...` (Windows) without installing or `PYTHONPATH` |
| `setup.sh` | One-time Linux/macOS setup: create `.venv`, install dependencies, verify |
| `docs/windows-guide.md` | Native Windows setup, credentials, paths and tests |
| `PROMETEO_AI_GUIDE.md` | Self-contained command guide for AI assistants |
| `config/models.yaml` | Real `ecmwf-seas5` catalog entry |
| `src/prometeo/shared/errors.py` | Exception hierarchy, exit codes, safe error formatting |
| `src/prometeo/shared/config.py` | `AppConfig`, YAML config loading, CLI override merge, validation |
| `src/prometeo/shared/io.py` | Atomic writes, SHA-256, directory creation, safe filenames |
| `src/prometeo/shared/logging.py` | Logger setup, JSON logs, run id, secret redaction |
| `src/prometeo/shared/timeutils.py` | `YearMonth`, month arithmetic, reference month, `forecastMonth` mapping |
| `src/prometeo/catalog/models.py` | Typed catalog entities and lookups |
| `src/prometeo/catalog/repository.py` | YAML parsing/validation and `CatalogRepository` |
| `src/prometeo/reader/reader.py` | Lazy NetCDF opening, layout discovery, concatenation, raw selection and inspection |
| `src/prometeo/canonical/dataset.py` | Canonical dataset wrapper, variable lookup and JSON summary |
| `src/prometeo/canonical/build.py` | Ensemble mean, units, longitude normalization, target periods, X/Z split |
| `src/prometeo/cpt/mapping.py` | Canonical-to-CPT field names, units and transformations |
| `src/prometeo/cpt/writer.py` | Deterministic CPT rendering, streaming and atomic write |
| `src/prometeo/cpt/validator.py` | Structural CPT+ v10 validation |
| `src/prometeo/diagnostics/findings.py` | Severity, findings and diagnostic report |
| `src/prometeo/diagnostics/checks.py` | File and canonical checks with sampled data quality |
| `src/prometeo/diagnostics/engine.py` | Check execution and conversion gate |
| `src/prometeo/download/request.py` | `DownloadRequest`, area/lead parsing, CDS payload |
| `src/prometeo/download/cache.py` | Cache identity, paths, validity, manifests |
| `src/prometeo/download/client.py` | Credentials, `configure` writer, `cdsapi` wrapper, retries, constraint query |
| `src/prometeo/download/service.py` | Orchestration, cache reuse, dry-run, two-product download |
| `src/prometeo/cli/common.py` | CLI helpers: CSV splitting, model formatting, JSON/report printing |
| `src/prometeo/cli/main.py` | Argument parser, subcommands (`configure` included), context loading and dispatch |
| `tests/__init__.py` | Makes `src/` importable for `unittest` discovery |
| `tests/test_launcher.py` | Subprocess tests for `python3 prometeo ...` |
| `tests/test_setup_script.py` | Syntax/help/option tests for `setup.sh` (skipped on Windows without `bash`) |
| `tests/test_configure.py` | Tests for `configure`, nested `~/.cdsapirc` and POSIX `0600` permissions |
| `tests/test_shared.py` | Tests for errors, config, I/O, logging, time |
| `tests/test_catalog.py` | Tests for the real catalog and YAML validation |
| `tests/test_download.py` | Tests for requests, cache and service orchestration |
| `tests/test_cli.py` | Tests for catalog commands, download dry-run, inspect and validate |
| `tests/test_reader.py` | Tests for layout discovery, concatenation, selection and inspection |
| `tests/test_canonical.py` | Tests for ensemble mean, units, target periods, validation and partitions |
| `tests/test_cpt.py` | Tests for CPT mappings, rendering and structural validation |
| `tests/test_diagnostics.py` | Tests for findings, checks, engine and conversion gate |
| `tests/test_convert.py` | End-to-end convert tests: partitions, leads, force and auto-download |
| `tests/netcdf_fixtures.py` | Synthetic forecast/hindcast NetCDF fixtures |

---

## 3. `config/models.yaml`

### What

The Sprint 1 model catalog. It now contains one complete model entry:

- Model id: `ecmwf-seas5`
- Centre: `ecmwf`
- System: `51`
- Dataset: `seasonal-monthly-single-levels`
- Products: hindcast `monthly_mean`, forecast `ensemble_mean`
- Variables: `t2m`, `tprate`, `u10`, `v10`, `msnsrf`
- Leads: `1–6`
- Hindcast years: `1981–2016`
- Forecast years: `2017–2026` (catalog snapshot)
- Members: 25 hindcast / 51 forecast
- Grid: 180 × 360, latitude descending, longitude `0–360`
- Release: day 6 at `12:00Z`
- Time convention: `c3s_forecastmonth_1_is_init`
- Source note for traceability

Each variable records both sides of the mapping:

```yaml
tprate:
  cds: total_precipitation      # name sent to CDS
  netcdf: tprate                # name in the NetCDF file
  cpt_field: prate              # name written to CPT
  units_in: m s-1               # source units
  units_out: mm/month           # CPT units
```

### Why

- The architecture requires configuration before code: adding a model or changing a
  system must not require editing the converter.
- Keeping the CDS name, NetCDF name, CPT name and units in one place prevents the
  mapping from being duplicated across `download`, `canonical` and `cpt`.
- The hindcast/forecast product types differ for ECMWF, so both are explicit rather
  than inferred.
- `forecast_years` is a snapshot. It should be updated when the operational period
  advances; the download request itself uses the current reference year.

---

## 4. `src/prometeo/shared/errors.py`

### What

- `ProMeteoError` is the base for all expected, user-facing failures. It stores
  `message` and an optional `hint`.
- Specialized subclasses: `ConfigurationError`, `CatalogError`, `DownloadError`,
  `CacheError`, `ReaderError`, `DiagnosticError`, `CanonicalError`, `CPTError`.
- `exit_code_for(error)` maps:
  - `DiagnosticError` → `2` (validation failure)
  - every other error → `1` (operational/conversion failure)
- `format_error(error)` renders `message` plus `Sugerencia: <hint>` and redacts
  credential-like text.
- `_redact()` masks:
  - `key=...`, `token=...`, `password=...`, `authorization=...`
  - URL userinfo such as `https://user:secret@host`.

### Why

- The CLI must return stable exit codes: `0` success, `1` operational error,
  `2` validation error.
- Users should never see a traceback for expected failures; they should see a cause
  and a suggested action.
- CDS credentials must never leak into logs, manifests or error messages.

---

## 5. `src/prometeo/shared/config.py`

### What

- `AppConfig` holds runtime settings:
  `timezone`, `catalog_path`, `cache_dir`, `output_dir`, `log_level`, `log_file`,
  `json_output`, `force`.
  `cache_dir` defaults to the **current working directory**; a YAML config or the
  download `--dest` flag can override it.
- `_repo_root()` resolves the repository root:
  1. `PROMETEO_ROOT` if set;
  2. the first parent containing `pyproject.toml` or `config/models.yaml`;
  3. the source-layout fallback (`parents[3]`).
- `default_config_path()` returns:
  1. `PROMETEO_CONFIG` if set;
  2. `$XDG_CONFIG_HOME/prometeo/config.yaml`;
  3. `~/.config/prometeo/config.yaml`.
- `load_config(path=None)`:
  - missing default config → defaults;
  - missing explicit path → actionable `ConfigurationError`;
  - reads YAML, rejects unknown keys, resolves relative paths **relative to the
    config file**, then validates.
- `merge_cli_overrides(config, overrides)` applies only non-`None` values and rejects
  unknown keys.
- `validate_config()` checks:
  - timezone is a valid IANA zone;
  - log level is one of `DEBUG/INFO/WARNING/ERROR/CRITICAL`;
  - `catalog_path` exists and is a file;
  - `cache_dir` and `output_dir` are directories or their parent is writable;
  - the log file’s parent is writable.

### Why

- Defaults must work from a checkout without any configuration file, while still
  allowing a user-level config for IDEAM machines.
- Resolving relative paths from the config file makes a config portable when the
  repository is moved.
- Rejecting unknown keys catches typos early instead of silently ignoring a setting.
- Validation happens before any network or filesystem work, so failures are fast and
  actionable.

---

## 6. `src/prometeo/shared/io.py`

### What

- `ensure_directory(path)` creates parents, verifies the result is a directory and
  returns the resolved path.
- `atomic_output_path(path)`:
  - creates a sibling temp file `.<name>.<random>.tmp`;
  - yields it to the caller;
  - `fsync`s it;
  - publishes with `os.replace` (atomic on the same filesystem);
  - deletes the temp file if the block raises.
- `atomic_write_text()` and `atomic_write_bytes()` are thin wrappers.
- `sha256_file()` reads in 1 MiB chunks and returns lowercase hex.
- `checksum_matches()` compares case-insensitively and returns `False` for an empty
  or missing file.
- `safe_filename_component()`:
  - replaces `<>:"/\|?*` and control characters;
  - collapses whitespace to `_`;
  - strips trailing dots/spaces;
  - prefixes Windows reserved names (`CON`, `NUL`, `COM1`, …) with `_`;
  - limits the component to 120 characters.

### Why

- A partial `.cpt` or corrupted cache file must never be published. Writing to a
  sibling and renaming atomically guarantees that.
- Checksums are used by the cache manifest and, later, by provenance in canonical
  datasets.
- Output names include model/system/region/variable data; sanitizing them makes the
  tool safe on both Linux and Windows, including the IDEAM machines.
- `atomic_output_path` is generic; the CPT writer and manifest writer can reuse it.

---

## 7. `src/prometeo/shared/logging.py`

### What

- `new_run_id()` returns a short 8-character id for one CLI invocation.
- `configure_logging(level, log_file, json_mode)`:
  - configures the `prometeo` logger;
  - sends console output to **stderr**;
  - optionally writes to a file;
  - replaces existing handlers to avoid duplicates;
  - uses JSON Lines when `json_mode=True`.
- `get_logger(name)` namespaces loggers under `prometeo.*`.
- `redact_secrets(values)` recursively:
  - masks keys whose name contains `key`, `token`, `secret`, `password`,
    `authorization`, `credential` or `cdsapi`;
  - masks URL userinfo in string values.

### Why

- Reports and JSON results must remain clean on stdout; logs go to stderr.
- JSON logs are useful for diagnostics and later structured reporting.
- Credentials can appear in request mappings, so redaction must be available before
  logging or serializing anything from CDS.

---

## 8. `src/prometeo/shared/timeutils.py`

### What

- `YearMonth` is an immutable, orderable `(year, month)` value:
  - year ≥ 1900;
  - month 1–12;
  - `str()` → `YYYY-MM`.
- `parse_year_month()` is strict about `YYYY-MM` and raises `ConfigurationError`.
- `add_months()` handles negative offsets and year boundaries.
- `month_length()` is leap-year aware.
- `current_reference_month(timezone, override)`:
  - returns the current month in `America/Bogota` by default;
  - keeps `override` for tests; the CLI does **not** expose it (reference month is
    always the current month).
- `derive_target_month(init_time, forecast_month, convention)`:
  - supports `c3s_forecastmonth_1_is_init`;
  - computes `target = init + (forecastMonth - 1)`;
  - rejects unknown conventions.
- `_as_year_month()` accepts `YearMonth`, `YYYY-MM`, `datetime`/`date`, `cftime`
  objects and `numpy.datetime64` (via its string form), without importing numpy.

### Why

- The `forecastMonth → target month` rule is central to CPT: `forecastMonth=1` is the
  initialization month, not the following month.
- The timezone matters because the reference month is “the current month” for IDEAM,
  which is in `America/Bogota`.
- Keeping the override parameter but not exposing it preserves reproducibility in
  tests without adding an operational flag that could silently shift the window.
- Duck typing avoids making `shared/` depend on numpy/cftime.

---

## 9. `src/prometeo/catalog/models.py`

### What

- `VariableSpec`: canonical key, CDS name, NetCDF name, CPT field, input/output units.
- `GridSpec`: expected `nlat`, `nlon`, latitude order, longitude convention.
- `ModelSpec`: complete model definition and helpers:
  - `variable(name)` with an actionable error listing available variables;
  - `supports_lead(lead)`;
  - `product_type(target)` for `forecast`/`hindcast`;
  - `year_range(target)`.
- `Catalog`: version, `updated_at`, models mapping; `get()` and `resolve(attributes)`.
- `resolve()` matches NetCDF attributes (`originating_centre`, `centre`,
  `institution`, `system`, `dataset`) and raises with candidates if ambiguous.

### Why

- Typed entities prevent downstream code from re-parsing YAML and spreading catalog
  knowledge across modules.
- Errors list valid options, which matters for a CLI used by non-developers.
- Attribute-based resolution supports the `inspect`/`validate` flow where the
  model can be inferred from the file instead of `--model`.

---

## 10. `src/prometeo/catalog/repository.py`

### What

- Private parsing helpers:
  - `_mapping`, `_required`, `_as_str`, `_as_int`;
  - `_range` for `[start, end]` years;
  - `_leads` validates leads are within 1–6;
  - `_variables` parses all variable specs;
  - `_product_types` accepts `product_type: {forecast, hindcast}` or the older
    `product_forecast`/`product_hindcast` fields;
  - `_model` builds one `ModelSpec`, including the `release` mapping.
- `load_catalog(path)` reads YAML and wraps file/YAML errors in `CatalogError`.
- `parse_catalog_mapping(data, source=...)` validates the top-level structure.
- `CatalogRepository`:
  - `from_path()`;
  - `list_models()` sorted by id;
  - `get_model()`;
  - `resolve_model(model_id, attributes)`;
  - `get_variable()`;
  - `supported_leads()`.

### Why

- Download needs a validated `ModelSpec` before it can build a request.
- Centralizing YAML validation means a malformed catalog fails once, with a clear
  message, instead of producing mysterious request errors later.
- Supporting both product-type layouts makes the catalog tolerant of the earlier
  schema while the project settles.

---

## 10b. `src/prometeo/reader/reader.py`

### What

- `DatasetLayout` records the discovered names for latitude, longitude,
  initialization time, lead, member and optional valid time.
- `_find_name()` searches dimensions, coordinates and scalar coordinates, first
  by the documented aliases and then case-insensitively.
- `discover_layout()` requires latitude, longitude, init and lead, and detects
  optional `number`/`member` and `valid_time`.
- `open_netcdf()` opens a file lazily with CF time decoding and raises a clear
  `ReaderError` when the file is missing or unreadable.
- `read_netcdf()`:
  - opens one or more files;
  - requires the same layout in every file;
  - concatenates them along initialization time and rejects duplicate init times;
  - applies variable, lead, member, init-range and area selections;
  - never converts units or reorders coordinates;
  - keeps source datasets open until `RawDataset.close()`.
- `_select_area()` implements CDS `N,W,S,E` selection with value-based masks, so
  it works for ascending or descending latitude and for both `-180..180` and
  `0..360` longitude conventions (conversion is used only for selection).
- `RawDataset` exposes `variable()`, `summary()` and a deterministic `close()`.
- `inspect_netcdf()` returns the same JSON-friendly summary for one file:
  dimensions, coordinates, variables, units, members, leads, init range,
  latitude/longitude ranges/order/convention and global attributes.
- `_jsonable()` normalizes numpy scalars, datetime64, cftime-like objects and
  paths so the summary can be serialized directly.

### Why

- The reader is the boundary between “what CDS gave us” and the normalized
  canonical object. Keeping it raw makes diagnostics meaningful and lets other
  exporters reuse the same data.
- Real C3S files use `forecast_reference_time`/`forecastMonth` and sometimes a
  scalar `number` coordinate (ensemble_mean) or a full `number` dimension
  (monthly_mean hindcast). Discovery must tolerate both.
- Regional downloads may return longitudes in `-180..180` even when the catalog
  says `0..360`. Selection handles the actual file convention; normalization
  stays in `canonical/`.
- Lazy opening avoids loading multi-GB cubes, and explicit `close()` avoids
  Windows file-handle leaks.
- The `inspect` summary is intentionally lazy: it reads dimensions/coordinates
  and metadata, not the full data cube.

---

## 10c. `src/prometeo/canonical/`

### What

`canonical/dataset.py`:

- `CanonicalDataset` wraps one `xarray.Dataset` with
  `target_period × latitude × longitude`, `init_time` and `lead` coordinates
  along `target_period`, and NaN for missing values.
- `variables` returns the canonical data variables in construction order.
- `variable(name)` returns one variable or raises with the available list.
- `summary()` returns dims, variables, units, target-period range, init range,
  lead list, lat/lon summaries and provenance attrs.
- `copy()` is a shallow copy: selections are independent, but multi-GB arrays are
  not loaded into memory.

`canonical/build.py`:

1. Selects the requested canonical variables and maps them to their NetCDF names.
2. Renames core dimensions/coordinates to `init_time`, `lead`, `latitude` and
   `longitude`.
3. Filters hindcast initializations by calendar month (default: reference month;
   `--hindcast-init-month` can be `current`, `1..12` or `all`).
4. Casts to float64 and averages the ensemble over `number` with `skipna=True`;
   scalar `number` coordinates from `ensemble_mean` are simply dropped.
5. Converts source units to canonical physical units:
   - `t2m` → K (accepts K or °C);
   - `tprate` → m s-1 (accepts m/s, mm/s, mm/day);
   - `u10`/`v10` → m s-1 (accepts m/s, km/h, knots);
   - `msnsrf` → W m-2 (accepts W m-2 or kW m-2).
6. Normalizes longitude to `-180..180` ascending (`>180` becomes `value - 360`),
   reordering the data with the coordinate. Latitude is normalized to
   descending (north → south) so X and Z always share the same row order.
7. Stacks `init_time × lead` into `target_period` and derives each label with
   `derive_target_month` (`cpt:T = init + (forecastMonth - 1)`).
8. Records provenance: model, centre, system, product type, reference month,
   hindcast init month, source files, ensemble size, tool version.
9. Provides `partition_canonical()` (X before cutoff, Z from cutoff) and
   `select_partition()` (`both`, `x`, `z`).
10. `validate_canonical()` enforces canonical dims/order, unique `YYYY-MM`
    periods, init/lead alignment, bounded grids (latitude descending,
    longitude ascending) and declared units.

### Why

- The canonical object is the contract that lets CPT and diagnostics work without
  knowing CDS or NetCDF.
- The hindcast has 36 years × 12 init months; using all of them produces
  duplicate `cpt:T` blocks. Filtering to the reference month gives 36 years ×
  6 leads = 216 unique target periods and keeps X comparable to the operational
  Z. `all` remains available for experiments but will fail validation if
  duplicate periods appear.
- Averaging in float64 and keeping physical units in canonical (K, m s-1,
  W m-2) follows NFR-07 and FR-CN-11. CPT remains the only layer that applies
  °C, mm/month and `-999`.
- Longitude normalization happens exactly once, here, so downstream code never
  has to guess the file convention.
- X/Z partitioning is explicit and recorded in attrs for diagnostics/CPT.

### Known limitation

The current implementation does not use dask, so a full global hindcast ensemble
mean can be memory-heavy. Region-sized and per-variable runs are comfortable;
global `t2m` completed with about 3.9 GiB max RSS, but a five-variable global run
may need chunking or per-slice processing.

---

## 10d. `src/prometeo/cpt/`

### What

`cpt/mapping.py`:

- `CPT_FIELDS` is the single canonical → CPT mapping table.
- `resolve_field()` rejects unknown variables with the available list.
- `temperature_to_celsius()` converts K → °C.
- `precipitation_to_mm_per_month()` multiplies m/s by the target month’s days,
  `86400` seconds/day and `1000` mm/m.
- `replace_missing()` turns NaN into `-999`.
- `to_cpt_values()` returns `(values, cpt_units, cpt_field_name)`.

`cpt/writer.py`:

- `render_cpt()` renders the namespace, `cpt:nfields=1`, the mandatory
  `cpt:T <date> <date> ...` date-list line, and one block per target period.
- Each block header contains `cpt:field`, `cpt:S`, `cpt:T`, `cpt:nrow`,
  `cpt:ncol`, `cpt:row=Y`, `cpt:col=X`, `cpt:units` and `cpt:missing=-999`.
- The longitude line is repeated per block; each row is
  `latitude + ncol values`.
- Coordinates use 2 decimals, values use 4 decimals, missing uses exactly
  `-999`; output is deterministic.
- `write_cpt()` streams to a temporary sibling, validates it with
  `validate_cpt()`, and only then publishes it atomically.

`cpt/validator.py`:

- Checks the exact namespace, `cpt:nfields=1`, the mandatory `cpt:T` date-list
  line, required tags, unique `cpt:T`, `cpt:missing=-999`, non-empty units,
  positive `nrow`/`ncol`, the longitude line width, every data-row width,
  numeric/finite values and truncation.
- Verifies that the date-list count and order match the data blocks.
- `CPTValidationReport.valid` and `to_dict()` provide machine-readable results.
- `validate_cpt(path)` validates a file from disk.

### Why

- Canonical units are physical (K, m s-1, W m-2); CPT needs °C, mm/month and
  `-999`. Keeping that conversion in one table prevents drift across modules.
- Streaming the file avoids building a huge string for a global hindcast.
- Validating the temporary file before the atomic rename guarantees that an
  invalid CPT is never published and an existing good file is not destroyed.
- Deterministic formatting makes golden-file tests and reproducibility possible.

### Known limitation

The CPT field names for wind and radiation (`u10`, `v10`, `msnsrf`) remain
unverified against CPT+ v10; the formal acceptance test is still pending.

---

## 11. `src/prometeo/download/request.py`

### What

- `DownloadRequest` is an immutable description of **one CDS request**:
  model, centre, system, dataset, canonical variables, CDS variables, product type,
  leads, area, reference month, target (`hindcast`/`forecast`), years, months,
  output directory, `force`, `dry_run`.
- `variable` / `cds_variable` properties return the single value when the request is
  per-variable.
- `parse_area(value)`:
  - `global` → `(90, -180, -90, 180)`;
  - `colombia` → `(13, -79, -5, -66)`;
  - otherwise four comma-separated values in CDS order `N,W,S,E`;
  - validates latitude/longitude bounds and `N > S`, `W < E`.
- `area_name(area)` returns `global`, `colombia`, or the raw signed values
  (e.g. `10,-75,-5,-66`). The cache filename keeps those raw values.
- `parse_leads(value)`:
  - accepts `1-6` or `1,3,5` (and mixtures such as `3,1-2`);
  - deduplicates and sorts;
  - rejects values outside 1–6.
- `build_request()`:
  - normalizes variables (`None` → all catalog variables, sorted);
  - validates variables, leads and area;
  - for **hindcast**: uses the catalog’s hindcast year range and all 12 months;
  - for **forecast**: uses the reference month’s year and month only;
  - pulls the product type from the catalog.
- `to_cds_payload()` produces the CDS API mapping:
  - `originating_centre`, `system`, `variable`, `product_type`, `year`, `month`,
    `leadtime_month`, `data_format: netcdf`;
  - omits `area` when the request is global (CDS default is global).
- `describe_request()` produces the human-readable summary used by dry-run.

### Why

- One request object per product type keeps the CDS payload pure and testable.
- Hindcast and forecast have different temporal semantics and product types, so they
  must not be conflated into a single request.
- All five variables are downloaded by default so the cache is reusable regardless of
  which variable is converted later.
- A single-variable request remains possible for smoke tests and advanced use.
- Omitting the area for global requests keeps the payload minimal; explicit regions
  are encoded for reproducibility.

---

## 12. `src/prometeo/download/cache.py`

### What

- `CachePaths` groups the three paths for one entry:
  - `netcdf`: final file;
  - `manifest`: sidecar `.nc.json`;
  - `partial`: `.nc.part` used during download.
- `CacheValidation` carries `valid`, `reason`, `checksum` and `raise_if_invalid()`.
- `_identity(request)` defines the stable cache identity. It includes model, centre,
  system, dataset, variables, CDS variables, product type, leads, area, target, years
  and months. **It includes `reference_month` only for forecast.**
- `cache_key(request)` is a SHA-256 of canonical JSON of that identity.
- Filename stem:
  `target_product_period_months_leads_area_variables`
  For example:
  ```text
  hindcast_monthly_mean_1981-2016_allmonths_leads1-6_global_msnsrf-t2m-tprate-u10-v10.nc
  forecast_ensemble_mean_2026-11_month11_leads1-6_global_msnsrf-t2m-tprate-u10-v10.nc
  ```
  Each variable in `--separate` mode gets its own file.
- The root of that layout is the download destination:
  `--dest` / `--destination` if supplied, otherwise the configured
  `cache_dir`, otherwise the current working directory.  A default run from
  `/srv/prometeo` therefore writes:
  ```text
  /srv/prometeo/ecmwf-seas5/...
  ```
  A server normally sets an explicit destination such as
  `/var/lib/prometeo/cache`.
- `cache_is_valid(paths, request, verify_checksum=False)` checks:
  1. NetCDF exists;
  2. manifest exists and parses;
  3. manifest `cache_key` matches the request;
  4. manifest `size` equals the actual file size;
  5. optionally, the checksum.
- `build_manifest()` writes:
  `cache_key`, request identity, filename, size, SHA-256 checksum, `created_at`,
  `tool_version`.
- `write_manifest()` uses atomic text writing.
- `read_manifest()` wraps missing/invalid JSON in `CacheError`.

### Why

- **Hindcast is downloaded once, ever.** Excluding the reference month from its key
  means running the tool in December reuses the January hindcast file.
- **Forecast is monthly.** Including the reference month means each month gets a new
  file and older forecasts remain available.
- Download must not open NetCDF files (that is the reader layer). A sidecar manifest
  provides enough identity and integrity metadata without violating that boundary.
- Size checking catches truncated files cheaply. Full checksum verification is
  available but off by default because hashing multi-GB files on every cache hit is
  expensive.
- A missing manifest is treated as invalid; manually copied files are re-downloaded
  rather than trusted blindly.

---

## 13. `src/prometeo/download/client.py`

### What

- `_parse_cdsapirc()` reads `url:` and `key:` lines from `~/.cdsapirc`.
- `load_credentials()` takes `CDSAPI_URL` / `CDSAPI_KEY` first, then the file.
- `write_credentials(url, key, path=None)` validates the URL/key and writes
  `~/.cdsapirc` atomically with permissions `0600`; it is used by the
  `configure` CLI command and never logs the key.
- `CDSClient`:
  - requires `cdsapi` and credentials (or an existing `~/.cdsapirc`);
  - constructs `cdsapi.Client` with `quiet=True`, `progress=False`, a bounded timeout
    and `retry_max=1` / `sleep_max=8`;
  - `retrieve(dataset, payload, target)`:
    - deletes any stale target;
    - retries up to `max_retries` with exponential backoff (capped at 8 s);
    - requires a non-empty output file;
    - wraps failures in `DownloadError`;
  - `check_constraints(dataset, inputs)` posts to the CDS constraints endpoint with
    `urllib` and returns the parsed JSON.

### Why

- All network access is isolated here; the rest of `download/` is testable offline.
- `cdsapi`’s default retry policy is 500 attempts with 120 s sleeps. That is
  unacceptable for an interactive CLI, so the wrapper bounds retries and implements
  its own smaller backoff.
- Credentials are read from the standard CDS locations and are never logged.
- `check_constraints` exists for the future `catalog check` command and for
  diagnosing rejected requests.

**Important:** constructing `cdsapi.Client` can contact the CDS catalogue endpoint.
`DownloadService` therefore creates the client **lazily**, only on a cache miss. A
dry-run or cache hit never constructs it.

---

## 14. `src/prometeo/download/service.py`

### What

- `DownloadPlan`: request, target path, `cache_hit`, human message, CDS payload.
- `DownloadResult`: path, `cached`, checksum, bytes written.
- `DownloadService`:
  - creates the cache root lazily, so plan/dry-run do not touch the filesystem;
  - validates the client factory is callable;
  - `plan(request)` checks the cache without network;
  - `dry_run(request)` returns the plan plus a description of the request;
  - `offline(request)` reuses the cache or raises (`--offline` is no longer a CLI
    flag, but the method remains available);
  - `download(request)`:
    1. returns the dry-run plan if requested;
    2. reuses a valid cache entry unless `force`;
    3. retrieves to `.part`;
    4. unwraps a single-member NetCDF ZIP and rejects GRIB/HTML or a
       multi-member ZIP before caching it;
    5. atomically replaces the final `.nc`;
    6. builds and atomically writes the `.json` manifest;
    7. returns a `DownloadResult`;
  - `download_products(reference_month, ...)`:
    - resolves `ecmwf-seas5`;
    - uses **all five catalog variables** by default;
    - produces one request per selected product (`hindcast` then `forecast` by
      default);
    - accepts `targets`/`--only` to restrict the run for smoke tests;
    - with `separate=True`, produces one request per variable per product;
    - returns a list of results or plans.
- `run_download(request, service)` is the convenience wrapper used by callers that
  already built a request.

### Why

- The operational default is “download both products”. Putting that in one
  service method prevents callers from accidentally downloading only the
  forecast, while an explicit `targets`/`--only` override keeps smoke tests and
  low-resource machines practical.
- Packed mode (2 files) is the default and the simplest cache layout. `separate=True`
  supports per-variable cache entries when smaller files or independent reuse matter.
- Cache-first behavior makes repeated runs cheap: the hindcast is effectively
  permanent, and the forecast is reused within its month.
- Atomic `.part → .nc` publishing plus the manifest keeps the cache self-describing
  and avoids partial files.
- CDS may split a large NetCDF request into several files and return a ZIP. A
  one-member ZIP is transparently unwrapped; a multi-member ZIP fails with an
  actionable error instead of being cached under a `.nc` name.
- Content validation catches the case where CDS returns GRIB or an HTML error
  page despite requesting NetCDF, which is cheap insurance around the payload
  format contract.
- The service has no CLI dependency, so the `download` command and the
  auto-download path in `convert` share exactly the same orchestration.

---

## 14b. CLI — `src/prometeo/cli/common.py` and `src/prometeo/cli/main.py`

### What

- `cli/common.py` contains helpers shared by the commands:
  - `split_csv()` / `split_int_csv()` for comma-separated flags;
  - `resolve_model()`, `catalog_variables()` and `build_read_request()` as
    helpers for read-selection requests;
  - `print_json()` with stable key ordering;
  - `format_model()` for the JSON shape used by `list-models`/`describe-model`;
  - `print_report()` for diagnostic output built from the diagnostics value
    objects;
  - `error()` for stderr messages;
  - `load_repository()` to load the catalog from the configured path.
- `cli/main.py` contains the actual command layer:
  - `add_common_options()` adds `--config`, `--catalog`, `--output-dir`,
    `--log-level`, `--log-file`, `--json` and `--force`;
  - `build_parser()` registers `configure`, `download`, `list-models`,
    `describe-model`, `inspect`, `validate` and `convert`;
  - `_load_context()` loads the config, merges CLI overrides, validates it,
    configures logging and loads the catalog;
  - `cmd_configure()`, `cmd_list_models()`, `cmd_describe_model()`,
    `cmd_download()`, `cmd_inspect()`, `cmd_validate()` and `cmd_convert()`
    implement the commands;
  - `main()` parses arguments, dispatches to the handler and maps errors to
    exit codes.
- `configure` prompts for the CDS URL/key and writes `~/.cdsapirc` with `0600`
  via `write_credentials()`; it does not load the catalog or download data.
- `download` accepts:
  - `--model` (default `ecmwf-seas5`);
  - `--dest` / `--destination` / `--cache-dir` (download directory; defaults to
    the current working directory);
  - `--region` (`global` by default, `colombia`, or raw `N,W,S,E`);
  - `--leads` (`1-6` by default);
  - `--hindcast-leads` / `--forecast-leads` (per-product overrides, default to
    `--leads`);
  - `--variables` (default: all five; a comma-separated subset is useful for
    smoke tests);
  - `--only` (`both` by default, or `hindcast`/`forecast` for a smaller run);
  - `--separate` (one file per variable and product);
  - `--dry-run` (print the CDS payload and destination without network);
  - `--timeout` and `--retries` (bound a network request);
  - the common `--force` and `--json`.
- The default run ensures both products and all five catalog variables. The
  `--only`, `--variables`, `--leads` and `--region` flags deliberately reduce
  the scope for smoke tests and low-resource machines.
- `inspect` accepts one or more files plus `--variables`, `--leads`,
  `--init-start`, `--init-end`, `--region`, `--members` and `--json`.
- `validate` accepts the same selections plus `--model`; it prints findings and
  returns `0`/`2` according to blocking errors.
- `convert` accepts `--model`, `--hindcast-file`/`--forecast-file` (or
  auto-download), `--dest`, `--region`, `--variables`, `--leads`,
  `--hindcast-leads`, `--forecast-leads`, `--hindcast-init-month`,
  `--partition both|x|z|single`, `--output-dir`, `--timeout`, `--retries`,
  `--force` and `--json`.

### Why

- The CLI must remain thin: parsing, configuration and dispatch only. Scientific
  work stays in the reader/canonical/CPT layers.
- A parent parser gives every subcommand the same options, so flags work after
  the subcommand (`prometeo download --json ...`) instead of only before it.
- Every command has an explicit handler; unknown commands still receive an
  argparse error rather than a traceback.
- JSON results go to stdout, logs go to stderr, and errors use the shared exit
  code mapping (`0` ok, `1` operational, `2` validation).
- `--dry-run` is the safest way to validate credentials, payload shape and cache
  paths before starting a multi-GB download.

### Example usage

```bash
# Without installing the package (launcher in the repository root):
python3 prometeo configure        # once: write ~/.cdsapirc with 600 permissions
python3 prometeo list-models
python3 prometeo download --dry-run

# Minimal live test: one product, one variable, one lead, small region
python3 prometeo download \
  --only forecast --variables t2m --leads 1 --region 10,-75,0,-70

# With the project virtualenv activated:
source .venv/bin/activate
prometeo download --dry-run --json
```

---

## 14c. `src/prometeo/diagnostics/`

### What

`diagnostics/findings.py`:

- `Severity` (`ERROR`, `WARNING`, `INFO`) and immutable `Finding`.
- `Finding.to_dict()` and `is_blocking()`.
- `DiagnosticReport` with `add`, `extend`, `errors`, `warnings`, `info`,
  `has_errors`, `exit_code()` and `to_dict()`.
- `error()`, `warning()` and `info()` constructors.

`diagnostics/checks.py`:

- File checks:
  - layout/coordinates present;
  - grid bounds, monotonicity and duplicates;
  - requested variables present and with the catalog units;
  - requested leads present;
  - unique initialization times;
  - ensemble member count;
  - `valid_time` vs `forecastMonth` consistency when available;
  - NaN fraction and constant-field checks.
- Canonical checks:
  - run `validate_canonical()`;
  - data-quality checks on canonical variables.
- Large-file data quality is sampled (up to 12 init/lead slices and 4 members)
  so a global hindcast does not load into memory.
- Severity policy:
  - missing variables/leads, wrong units, non-monotonic grids, duplicate
    init/periods and canonical contract violations → `ERROR`;
  - 100 % NaN → `ERROR`; >50 % NaN or constant field → `WARNING`;
  - regional grid size, ensemble count and `valid_time` absence → `INFO`.

`diagnostics/engine.py`:

- `DiagnosticEngine.run()` executes check callables and collects findings.
- Expected and unexpected check failures become `INTERNAL` `ERROR` findings, never
  tracebacks.
- `validate_file()` / `validate_canonical()` attach files and metadata.
- `can_convert()` = `force or no errors`.
- `assert_can_convert()` raises `DiagnosticError` with the first actionable
  failure.
- Convenience wrappers `validate_file()` and `validate_canonical()`.

### CLI `validate`

```bash
python3 prometeo validate FILE [FILE ...] \
  --model ecmwf-seas5 \
  --variables t2m,tprate \
  --leads 1-6 \
  --init-start 1981-01 --init-end 2016-12 \
  --region 10,-75,0,-70 \
  --members 0,1,2 \
  --json
```

- Reads explicit files only; never downloads.
- Returns `0` when there are no blocking errors and `2` when there are.
- An unreadable file becomes a `FILE-READ` finding and exits `2`, not a traceback.
- `--force` affects conversion (`can_convert`), but `validate` still reports the
  real validation exit code.

### Why

- Diagnostics is the conversion gate: an `ERROR` must block CPT writing unless
  `--force`.
- Findings carry a check id, location, message and corrective action so users can
  fix inputs without reading code.
- Sampling keeps validation usable on large files while keeping memory bounded.
- Keeping the engine independent from the CLI allows `convert` to reuse the same
  gate before writing CPT.

---

## 14d. `convert` orchestration

### What

`cmd_convert()` in `cli/main.py` implements the end-to-end conversion:

1. Resolve the model and the current reference month.
2. Ensure inputs:
   - use `--hindcast-file` / `--forecast-file` when provided;
   - otherwise auto-download only the needed products, requested variables and
     per-product leads.
3. Read the raw NetCDF slices.
4. Build canonical X (hindcast) and/or Z (forecast).
5. Run file and canonical diagnostics for every needed product.
6. Add a `LEADS-MISMATCH` warning when X and Z lead sets differ.
7. Apply the diagnostic gate:
   - blocking errors without `--force`: print the report, exit `2`;
   - blocking errors with `--force`: continue, write outputs, exit `0` and mark
     the result as forced.
8. Write CPT files atomically and validate them.
9. Print a summary or JSON result.

Default output directory: current working directory (`AppConfig.output_dir`
defaults to `Path.cwd()`).

### Partitions and naming

- `--partition both` (default): two files per variable, X and Z.
- `--partition x`: only hindcast.
- `--partition z`: only forecast.
- `--partition single`: one combined X+Z file.

Names encode model, variable, partition, period, leads, area label and grid, e.g.:

```text
ecmwf-seas5_t2m_x_1981-2016_leads1-6_global_180x360.cpt
ecmwf-seas5_t2m_z_2026-11_leads1-6_global_180x360.cpt
```

When inputs are local and no `--region` is supplied, the area label is inferred
from the canonical grid instead of being labeled `global`.

### Leads

- `--leads` is the shared default (`1-6`).
- `--hindcast-leads` and `--forecast-leads` override one product independently.
- Mismatched X/Z lead sets are allowed with a warning, because they may still be
  useful but are less directly comparable for CPT calibration.

### Tests

`tests/test_convert.py` covers:

- X+Z default output;
- X-only, Z-only and single combined files;
- per-product lead overrides and the mismatch warning;
- `--hindcast-init-month` (fixed month);
- diagnostic errors without force (exit `2`, no files) and with force (exit `0`,
  forced note);
- the auto-download path with an injected fake download service.

---

## 15. `tests/__init__.py`

### What

Adds `<repo>/src` to `sys.path` so the suite can run without installing the package:

```bash
python3 -m unittest discover -s tests -t .
```

### Why

`pyproject.toml` sets `pythonpath = ["src"]` for pytest, but pytest is not installed
in this environment. The `unittest` discovery path needs the same convenience, and
using `-t .` makes the tests a package so `tests/__init__.py` is executed.

---

## 16. `tests/test_shared.py`

### What

Covers:

- exit codes (`DiagnosticError → 2`, others → 1);
- error formatting with hints and credential redaction;
- `YearMonth` parsing/formatting, invalid input, month arithmetic, leap years;
- reference month with override and timezone;
- `forecastMonth → target month` for `datetime`, `numpy.datetime64` and NumPy
  integer leads;
- unknown conventions and invalid forecast months;
- atomic text/bytes writes, checksum matching, cleanup after a failed write;
- safe filenames, directory creation;
- config loading with relative paths, missing explicit files, unknown keys,
  null required paths, quoted booleans, CLI override merge, invalid timezone,
  `PROMETEO_CONFIG`;
- secret redaction and run ids.

### Why

`shared/` is the base of every other layer. These tests protect the contracts that
the rest of the project assumes: stable error codes, no partial files, correct month
arithmetic and no credential leakage.

---

## 17. `tests/test_catalog.py`

### What

- Loads the real `config/models.yaml`.
- Verifies centre, system, dataset, leads, hindcast years, grid and product types.
- Verifies all five variables and the CPT mappings (`tprate → prate [mm/month]`,
  `msnsrf → W m-2`).
- Checks actionable errors for unknown models/variables.
- Checks lead membership and attribute resolution.
- Verifies that unknown model/variable keys and a missing `product_type` fail with
  an actionable error.
- Feeds a deliberately invalid catalog mapping to ensure out-of-range leads are
  rejected.

### Why

The catalog is configuration, so a typo can silently produce wrong requests. These
tests pin the Sprint 1 model entry and exercise the parser’s validation.

---

## 18. `tests/test_download.py`

### What

- Area parsing: named areas, custom `N,W,S,E`, full-precision filenames,
  invalid bounds.
- Lead parsing: ranges, lists, mixtures, out-of-range values.
- Request construction:
  - hindcast = 1981–2016, 12 months, `monthly_mean`;
  - forecast = reference year/month, `ensemble_mean`;
  - variable subsets and unknown variables.
- CDS payload: centre, system, product type, 36 years, 12 months, leads,
  `data_format`, global area omitted, custom area included.
- Cache paths:
  - hindcast path/key stable across reference months;
  - forecast path/key changes with the reference month.
- Missing cache is invalid.
- Service, using a `FakeClient` that writes a small file instead of contacting CDS:
  - first run downloads both products and writes manifests;
  - second run is a cache hit;
  - `separate=True` produces 10 files/requests;
  - `dry_run=True` produces plans and makes zero client calls;
  - `force=True` redownloads;
  - a truncated cache file is detected by size and redownloaded;
  - `offline()` returns the cached result or raises;
  - a single-variable download only sends that variable to CDS;
  - dry-run does not create the cache directory;
  - a one-member ZIP is unwrapped to the NetCDF file;
  - a multi-member ZIP and a non-NetCDF payload are rejected.

### Why

These tests prove the operational behavior without network access: two products,
correct temporal windows, cache reuse, atomic publishing and `dry-run`. The fake
client also documents the exact `retrieve(dataset, payload, target)` seam that the
real `CDSClient` implements.

---

## 18b. `tests/test_cli.py`

### What

- Captures stdout/stderr and runs the real `main()` function.
- Verifies `list-models --json` returns the `ecmwf-seas5` entry.
- Verifies `describe-model --json` includes products, years and variables.
- Verifies an unknown model returns exit code `1` with a clear message.
- Verifies `download --dry-run`:
  - returns two products and the expected CDS payloads;
  - returns ten plans with `--separate`;
  - sends a custom `--region` as an explicit CDS area;
  - parses `--leads 1,3,5`;
  - limits the run with `--only forecast|hindcast`;
  - limits variables with `--variables t2m` / `t2m,tprate`;
  - rejects unknown variables and invalid regions before any download;
  - produces readable human output without `--json`.
- Verifies `inspect` on a synthetic file: JSON shape, variable/lead/area
  selections and human-readable output.
- Verifies `validate` on valid files, units mismatches (exit `2`) and unreadable
  files (`FILE-READ`, exit `2`).

Live checks were also run manually against CDS: a small regional forecast, a
regional all-five-variable hindcast, and a global `t2m` hindcast. The global
`t2m` download produced a 6.79 GB NetCDF and a valid manifest; its conversion
produced a 216-block X CPT and a 6-block Z CPT with zero diagnostic errors.
`tests/test_configure.py`, `tests/test_launcher.py` and
`tests/test_setup_script.py` cover the credential helper, launcher and setup
script. The setup-script tests are skipped on Windows when `bash` is absent, and
the credential permission assertion is POSIX-only; see `docs/windows-guide.md`.

### Why

The CLI is the user-facing surface, so the tests protect the exact flags,
JSON shapes and exit codes that the download smoke test depends on. They are
fully offline because `--dry-run` never constructs the CDS client.

---

## 18c. `tests/test_reader.py` and `tests/netcdf_fixtures.py`

### What

- `netcdf_fixtures.py` creates two synthetic NetCDF layouts:
  - forecast: scalar `number` coordinate, no member dimension;
  - hindcast: `number` as a real dimension with several members.
- `test_reader.py` verifies:
  - layout discovery and alias tolerance;
  - hindcast selection by variable, lead, init range and member;
  - area selection with `0..360` and `-180..180` longitudes;
  - ascending/descending latitude behavior;
  - multi-file concatenation and duplicate-init rejection;
  - actionable errors for missing variables;
  - JSON serialization of the inspection summary.

### Why

The real C3S files have exactly these two shapes, and regional downloads change
the longitude convention. Synthetic fixtures make these cases deterministic and
keep the suite offline while still matching the real layout.

---

## 18d. `tests/test_canonical.py`

### What

- Forecast build: target periods, lead/init coordinates, units and dropped
  scalar member.
- Longitude `0..360` normalization and ascending order.
- Hindcast build: default reference-month filtering, explicit month, missing
  month and `all` (duplicate-period detection).
- Ensemble mean compared against an explicit raw-data mean.
- Unit conversions for °C, mm/day and km/h, plus rejection of unknown units.
- X/Z partitioning and `both`/`x`/`z` selection.
- Canonical validation: valid dataset, duplicate periods, non-monotonic
  longitude and missing lead coordinate.
- JSON summary and shallow-copy independence.

### Why

The canonical layer is where scientific meaning is assigned: ensemble mean,
units, target periods, longitude convention and partition. These tests pin those
decisions and prevent accidental changes from silently altering CPT calibration.

---

## 18e. `tests/test_cpt.py`

### What

- Mapping: field lookup, unknown variable, K → °C, m/s → mm/month with leap
  February, NaN → `-999`, return tuple.
- Formatting: month normalization, 4-decimal values, exact `-999`, rejection of
  infinite values.
- Rendering: namespace, `nfields=1`, the mandatory `cpt:T` date list, block
  count, `cpt:T`/`cpt:S`, dimensions, units, missing sentinel, determinism,
  explicit variable selection when the canonical object has several.
- Writing: atomic file creation, validation of the output, byte-for-byte
  equality with `render_cpt`.
- Validator: wrong namespace, wrong `nfields`, missing or mismatched date list,
  missing tags, wrong row width, non-numeric values, truncation, duplicate
  periods and missing file.

### Why

CPT is the final artifact the client opens. These tests protect the format,
units, missing values and atomicity, and they ensure the validator can
independently catch a corrupted document.

The real downloaded forecast and hindcast were also converted manually:
1 block (forecast) and 36 blocks (hindcast), both structurally valid.

---

## 18f. `tests/test_diagnostics.py`

### What

- Findings/report: constructors, blocking severity, counts and exit codes, JSON
  serialization.
- File checks: valid forecast, missing variable, units mismatch, missing lead,
  non-monotonic longitude, all-NaN field, constant field and ensemble count.
- Canonical checks: valid canonical, duplicate periods and missing units.
- Engine: finding collection, exception-to-ERROR conversion, `force` gate and
  `assert_can_convert`.
- CLI `validate`: valid file → 0, units error → 2, unreadable file → 2 with
  `FILE-READ`, human output.

### Why

Diagnostics is the gate between raw data and CPT. These tests protect the
severity policy, the no-traceback guarantee, the exit codes and the sampled
quality checks.

The real forecast and hindcast were validated manually:
- valid forecast/hindcast → exit 0 with only INFO findings;
- a copy with `t2m` units changed to `degC` → exit 2 with `FILE-UNITS`;
- a missing file → exit 2 with `FILE-READ`.

---

## 19. Cross-cutting decisions

| Decision | Reason |
| :-- | :-- |
| `DiagnosticError` → exit 2, others → 1 | Matches the CLI requirements (`0` ok, `1` conversion, `2` validation) |
| User-facing messages in Spanish | Project house rule; identifiers/docstrings remain English |
| Config is optional, user-level by default | Works from a checkout and on IDEAM machines without admin rights |
| Relative config paths resolve from the config file | Makes configurations portable |
| Atomic writes everywhere | No partial `.cpt`/manifest files, even on Ctrl-C or disk errors |
| Hindcast cache excludes reference month | Download once, reuse forever |
| Forecast cache includes reference month | One file per operational month |
| Cache requires a sidecar manifest | Download layer does not read NetCDF but still gets integrity metadata |
| Checksum verification off by default | Avoid hashing multi-GB files on every cache hit |
| `data_format: netcdf` in the CDS payload | Current CDS Retrieve API uses `data_format`; `format` is rejected with HTTP 422 |
| Global area omits `area` | CDS defaults to global; keeps payload minimal |
| All five variables downloaded by default | The cache is reusable for any later conversion |
| `--offline` removed, `--dry-run` kept | User decision: dry-run is useful; offline cache reuse is already the default |
| `retry_max=1` in `cdsapi` + bounded outer retries | Avoids cdsapi’s 500×120 s default retry loop |

---

## 20. Validated scope, limitations and next steps

### Validated

1. **CLI and vertical slice.** `configure`, `download`, `list-models`,
   `describe-model`, `inspect`, `validate` and `convert` are wired, including
   `convert` auto-download.
2. **Real ECMWF data.** Regional forecast/hindcast downloads and conversions
   succeeded for all five variables with one lead. A global `t2m` hindcast
   downloaded as a single 6.79 GB NetCDF and converted to 216 X blocks and 6 Z
   blocks with zero diagnostic errors.
3. **CPT+.** The regional `t2m` X file was opened successfully in CPT+.

### Known constraints

- `forecast_years` is a snapshot (`2017–2026`); it needs periodic catalog updates.
- The full global five-variable hindcast has not been run end-to-end. Global
  `t2m` alone was 6.79 GB compressed; the five-variable request is several times
  larger and may hit CDS limits or return a multi-member ZIP. The service rejects
  multi-member ZIPs with an actionable error, so the request must be divided
  (for example with `separate=True` or by year/month) until it fits.
- Hindcast downloads request all 12 init months even when conversion later keeps
  only the reference month. This is correct but inefficient.
- There is no `--reference-month`; operational commands use the current month.
- `convert` auto-download does not reuse a larger packed NetCDF for a smaller
  region; pass `--hindcast-file`/`--forecast-file` to subset an existing file.
- `convert` accepts one local file per product, not multiple chunks.
- Cache validation requires the manifest; manually copied NetCDF files are treated
  as invalid and re-downloaded.
- No disk-space pre-check, no parallel downloads, no resume of interrupted `.part`
  files.
- The `offline()` service method exists but no CLI flag exposes it.
- `download_products()` and the CLI accept a `variables` subset and an `--only`
  filter for smoke tests. The default remains both products and all five catalog
  variables; these flags are explicit reductions, not the operational default.
- Canonical ensemble averaging is eager and does not use dask. Global `t2m`
  completed with about 3.9 GiB max RSS; a five-variable global run may need
  chunking.
- Data-quality diagnostics sample large files (up to 12 init/lead slices and 4
  members) instead of scanning a whole global cube.
- CPT+ GUI has practical memory/grid limits: the global 109 MB X CPT is too large
  for it, while regional CPT files work.
- Wind/radiation CPT field names (`u10`, `v10`, `msnsrf`) are not formally
  validated in CPT+.

### Recommended next implementation order

1. Validate the five-variable global conversion on a larger machine.
2. Validate the CPT+ v10 acceptance of wind/radiation field names.
3. Add per-slice/chunked processing and multi-member ZIP support.
4. Add `--reference-month` and explicit reuse of packed files for subsets.
5. Add an opt-in live integration test and `ruff`/`mypy` in CI.
6. Add diagnostics checks for coverage gaps and suspicious physical values.
7. Add Windows/IDEAM portability tests.
